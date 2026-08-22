"""Workspace config as YAML (NFR-8.1, #519): export/import that walks
agents, teams, channels, custom tools, and workspace settings — not just
the `workspace_settings` table api/settings.py serves.

The export is a portable *workspace definition* (config-as-code), not a
node backup (that's api/backups.py / #243), so everything node-local is
deliberately absent: `ui.port` / `tools.working_directory` settings,
Channel.working_directory, provider API keys, MCP header/env values (all
keychain-held anyway), and `mcp`/`builtin` Tool rows (per-node
caches/seeds — agents reference tools by (type, name) instead of by id
so assignments survive the id differences between installs). Custom tool
source code IS included: it's the tool's definition, and P2P sync
already treats it as workspace content (sync/publish.py's
_build_tool_payload gossips the same source to every PSK-holding peer).

Import runs in two phases: validate everything against the live DB first
(unknown setting keys, name collisions/ambiguity, unresolvable
references, invalid tool source) and raise ImportValidationError with
every problem at once — nothing is written unless the whole file passes.
The apply phase then upserts in FK order (teams → tools → agents →
channels → settings), matching each entity by id first and by its
natural unique name second, so re-importing an export converges instead
of duplicating, and importing into a *different* workspace merges onto
same-named entities (e.g. the seeded Assistant) rather than 409ing.

Tombstone respect (#238): an entity in the file whose id has no live row
but does have sync history — VectorClockTracker rows (kept forever,
precisely so long-offline messages can't resurrect deletes) or a queued
offline-delete marker (SyncPendingOutbound.deleted) — was deleted, and
import skips it (reported in ImportSummary.skipped) instead of
recreating a row every peer would rightly treat as new divergence.
Every applied create/update publishes through the same
publish_current_state path ordinary API edits use, so an import is just
a batch of normal, vector-clock-bumped local edits as far as the mesh
is concerned.

Apply is not one atomic transaction — it reuses the same commit-then-
publish helpers the entity routes use (some commit internally). The
up-front validation phase is what keeps a half-applied import from
being a practical failure mode.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.agentos.agent_lifecycle import (
    publish_agent_change,
    publish_agent_teams_change,
    publish_agent_tool_scopes_change,
    publish_agent_tools_change,
    record_agent_version,
    record_registration_flags,
    replace_routing_rules,
    set_agent_teams,
    set_agent_tool_scopes,
    set_agent_tools,
)
from rivulets.agentos.service import sync_agents
from rivulets.agentos.tool_scopes import TOOL_SCOPES
from rivulets.config import get_settings
from rivulets.db.base import uuid7
from rivulets.db.models import (
    Agent,
    AgentRoutingRule,
    AgentTool,
    AgentToolScope,
    Channel,
    SyncPendingOutbound,
    Team,
    TeamAgent,
    Tool,
    ToolVersion,
    VectorClockTracker,
)
from rivulets.db.models import (
    WorkspaceSetting as WorkspaceSettingRow,
)
from rivulets.sync.publish import publish_current_state
from rivulets.validation import TOOL_NAME_RE

EXPORT_VERSION = 1


# ---------------------------------------------------------------------------
# File schema. Field constraints mirror the entity routes' own create
# models (api/agents.py's AgentCreate, api/channels.py's name-length
# check, api/tools.py's TOOL_NAME_RE) so a file that validates here would
# also have been accepted entity-by-entity over the ordinary API.


class ExportedTeam(BaseModel):
    id: str | None = None
    name: str = Field(min_length=1)
    description: str | None = None


class ExportedTool(BaseModel):
    """Custom tools only — see module docstring for why builtin/mcp rows
    aren't part of the workspace definition."""

    id: str | None = None
    name: str
    description: str
    source_code: str = ""


class ExportedToolRef(BaseModel):
    """An agent's tool assignment, by (type, name) rather than id: builtin
    ids are seeded per-install and mcp ids are per-node discovery caches,
    so only the name is portable. Custom names resolve against this same
    file's `tools` (or an already-present custom tool)."""

    type: str  # 'builtin' | 'custom' | 'mcp'
    name: str


class ExportedRoutingRule(BaseModel):
    rule_type: str
    pattern: str
    priority: int = 0


class ExportedAgent(BaseModel):
    id: str | None = None
    name: str = Field(min_length=2, max_length=64)
    description: str = Field(min_length=10, max_length=500)
    instructions: str
    model: str
    fallback_models: list[str] = Field(default_factory=list)
    output_schema: dict[str, Any] | None = None
    approved_for_unattended_tools: bool = False
    tools: list[ExportedToolRef] = Field(default_factory=list[ExportedToolRef])
    teams: list[str] = Field(default_factory=list)  # team ids as used in this file
    tool_scopes: list[str] = Field(default_factory=list)
    # None means "leave whatever rules the matched agent already has" —
    # import never invokes the LLM rule generator (a config import must
    # not depend on a live model provider), so rules only change when the
    # file carries them.
    routing_rules: list[ExportedRoutingRule] | None = None


class ExportedChannel(BaseModel):
    id: str | None = None
    name: str = Field(min_length=3, max_length=80)
    description: str | None = None
    team_id: str | None = None  # a team id as used in this file
    position: int = 0
    archived: bool = False


class WorkspaceExport(BaseModel):
    version: int = EXPORT_VERSION
    settings: dict[str, Any] = Field(default_factory=dict)
    teams: list[ExportedTeam] = Field(default_factory=list[ExportedTeam])
    tools: list[ExportedTool] = Field(default_factory=list[ExportedTool])
    agents: list[ExportedAgent] = Field(default_factory=list[ExportedAgent])
    channels: list[ExportedChannel] = Field(default_factory=list[ExportedChannel])


# ---------------------------------------------------------------------------
# Export


async def build_export(db: AsyncSession, settings_values: dict[str, object]) -> str:
    """Serializes the workspace definition to YAML. `settings_values` is
    the already-merged, already-filtered settings dict (api/settings.py
    owns the key catalog and which keys are node-local)."""
    teams = list((await db.scalars(select(Team).order_by(Team.name))).all())
    custom_tools = list(
        (await db.scalars(select(Tool).where(Tool.tool_type == "custom").order_by(Tool.name))).all()
    )
    agents = list((await db.scalars(select(Agent).order_by(Agent.name))).all())
    channels = list((await db.scalars(select(Channel).order_by(Channel.position))).all())

    tool_names_by_id = {
        row.id: (row.tool_type, row.name) for row in (await db.scalars(select(Tool))).all()
    }

    agent_dicts: list[dict[str, Any]] = []
    for agent in agents:
        assigned = (
            await db.scalars(select(AgentTool.tool_id).where(AgentTool.agent_id == agent.id))
        ).all()
        tool_refs = [
            {"type": tool_names_by_id[tool_id][0], "name": tool_names_by_id[tool_id][1]}
            for tool_id in assigned
            if tool_id in tool_names_by_id
        ]
        team_ids = (
            await db.scalars(select(TeamAgent.team_id).where(TeamAgent.agent_id == agent.id))
        ).all()
        scopes = (
            await db.scalars(
                select(AgentToolScope.scope).where(AgentToolScope.agent_id == agent.id)
            )
        ).all()
        rules = (
            await db.scalars(
                select(AgentRoutingRule)
                .where(AgentRoutingRule.agent_id == agent.id)
                .order_by(AgentRoutingRule.priority)
            )
        ).all()
        agent_dicts.append(
            {
                "id": agent.id,
                "name": agent.name,
                "description": agent.description,
                "instructions": agent.instructions,
                "model": agent.model,
                "fallback_models": json.loads(agent.fallback_models)
                if agent.fallback_models
                else [],
                "output_schema": json.loads(agent.output_schema) if agent.output_schema else None,
                "approved_for_unattended_tools": agent.approved_for_unattended_tools,
                "tools": sorted(tool_refs, key=lambda ref: (ref["type"], ref["name"])),
                "teams": sorted(team_ids),
                "tool_scopes": sorted(scopes),
                "routing_rules": [
                    {"rule_type": r.rule_type, "pattern": r.pattern, "priority": r.priority}
                    for r in rules
                ],
            }
        )

    tool_dicts: list[dict[str, Any]] = []
    for tool in custom_tools:
        source_code = ""
        if tool.source_path:
            try:
                source_code = Path(tool.source_path).read_text(encoding="utf-8")
            except OSError:
                pass  # no file yet (created but never saved) — metadata only
        tool_dicts.append(
            {
                "id": tool.id,
                "name": tool.name,
                "description": tool.description,
                "source_code": source_code,
            }
        )

    document = {
        "version": EXPORT_VERSION,
        "settings": settings_values,
        "teams": [{"id": t.id, "name": t.name, "description": t.description} for t in teams],
        "tools": tool_dicts,
        "agents": agent_dicts,
        "channels": [
            {
                "id": c.id,
                "name": c.name,
                "description": c.description,
                "team_id": c.team_id,
                "position": c.position,
                "archived": c.archived,
            }
            for c in channels
        ],
    }
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# Import


class ImportValidationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass
class ImportSummary:
    created: dict[str, int] = field(default_factory=dict[str, int])
    updated: dict[str, int] = field(default_factory=dict[str, int])
    skipped: list[str] = field(default_factory=list[str])
    warnings: list[str] = field(default_factory=list[str])

    def count(self, bucket: dict[str, int], entity_type: str) -> None:
        bucket[entity_type] = bucket.get(entity_type, 0) + 1


async def _is_tombstoned(db: AsyncSession, entity_type: str, entity_id: str) -> bool:
    """True if this (live-row-less) entity id has known sync history: a
    VectorClockTracker row (kept after deletes precisely so stale messages
    can't resurrect them — apply_remote_delete's docstring) or a queued
    offline-delete marker (publish_tombstone with the engine down). Either
    way the id was deleted, not never-seen, and import must not recreate
    it."""
    clock = await db.scalar(
        select(VectorClockTracker)
        .where(
            VectorClockTracker.entity_type == entity_type,
            VectorClockTracker.entity_id == entity_id,
        )
        .limit(1)
    )
    if clock is not None:
        return True
    pending = await db.get(SyncPendingOutbound, (entity_type, entity_id))
    return pending is not None and pending.deleted


@dataclass
class _Plan:
    """One entity's resolved import action, decided during validation."""

    item: Any
    existing_id: str | None = None  # update this row
    create_id: str | None = None  # create with this id


def _parse_document(text: str, errors: list[str]) -> WorkspaceExport | None:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        errors.append(f"Not valid YAML: {exc}")
        return None
    if not isinstance(raw, dict):
        errors.append("Expected a YAML mapping at the top level")
        return None
    try:
        document = WorkspaceExport.model_validate(raw)
    except ValidationError as exc:
        for err in exc.errors():
            location = ".".join(str(part) for part in err["loc"])
            errors.append(f"{location}: {err['msg']}")
        return None
    if document.version != EXPORT_VERSION:
        errors.append(f"Unsupported export version {document.version} (expected {EXPORT_VERSION})")
        return None
    return document


def _check_duplicate_names(items: list[Any], label: str, errors: list[str]) -> None:
    seen: set[str] = set()
    for item in items:
        if item.name in seen:
            errors.append(f"{label}: duplicate name {item.name!r} in file")
        seen.add(item.name)


async def _plan_entity(
    db: AsyncSession,
    entity_type: str,
    item: Any,
    match_by_name: Any,
    errors: list[str],
    summary: ImportSummary,
) -> _Plan | None:
    """Shared match-or-create resolution: id first (exact identity), then
    the entity's natural name (so re-imports and cross-workspace imports
    merge instead of colliding), else create — under the file's id when
    it's fresh, so ids stay stable across export/import cycles. Returns
    None for a tombstoned id (recorded in summary.skipped) or an
    ambiguous name match (recorded in errors)."""
    spec_model = {"team": Team, "tool": Tool, "agent": Agent, "channel": Channel}[entity_type]
    if item.id is not None:
        existing = await db.get(spec_model, item.id)
        if existing is not None:
            return _Plan(item=item, existing_id=item.id)
        if await _is_tombstoned(db, entity_type, item.id):
            summary.skipped.append(
                f"{entity_type} {item.name!r} ({item.id}): deleted here or on a peer, "
                "not resurrected"
            )
            return None
    matches = await match_by_name(item.name)
    if len(matches) > 1:
        errors.append(
            f"{entity_type} {item.name!r}: name matches {len(matches)} existing rows — "
            "ambiguous, cannot import"
        )
        return None
    if matches:
        return _Plan(item=item, existing_id=matches[0].id)
    return _Plan(item=item, create_id=item.id or uuid7())


async def apply_import(
    db: AsyncSession,
    text: str,
    *,
    known_settings_keys: frozenset[str] | set[str],
    local_only_settings_keys: frozenset[str] | set[str],
) -> ImportSummary:
    """Validates the whole document (raising ImportValidationError with
    every problem found), then applies it. See module docstring."""
    errors: list[str] = []
    summary = ImportSummary()

    document = _parse_document(text, errors)
    if document is None:
        raise ImportValidationError(errors)

    # --- validate settings ---------------------------------------------
    settings_to_apply: dict[str, object] = {}
    for key, value in document.settings.items():
        if key in local_only_settings_keys:
            summary.warnings.append(f"setting {key!r} is node-local, not imported")
            continue
        if key not in known_settings_keys:
            errors.append(f"Unknown setting: {key}")
            continue
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            errors.append(f"setting {key!r}: value is not JSON-serializable")
            continue
        settings_to_apply[key] = value

    _check_duplicate_names(document.teams, "teams", errors)
    _check_duplicate_names(document.tools, "tools", errors)
    _check_duplicate_names(document.agents, "agents", errors)
    # Channel names are only unique among non-archived channels
    # (idx_channel_name), so an archived channel sharing a live one's name
    # is a legitimate exported state, not a duplicate.
    _check_duplicate_names([c for c in document.channels if not c.archived], "channels", errors)

    # --- resolve teams -------------------------------------------------
    async def _teams_named(name: str) -> list[Team]:
        # Team names carry no unique constraint, so a name match is only
        # trusted when it's unambiguous.
        return list((await db.scalars(select(Team).where(Team.name == name))).all())

    team_plans: list[_Plan] = []
    # file team id -> the id it resolves to locally; tombstone-skipped
    # teams get None so references to them can be dropped with a warning.
    team_id_map: dict[str, str | None] = {}
    for team in document.teams:
        plan = await _plan_entity(db, "team", team, _teams_named, errors, summary)
        if plan is None:
            if team.id is not None:
                team_id_map[team.id] = None
            continue
        team_plans.append(plan)
        resolved = plan.existing_id or plan.create_id
        assert resolved is not None
        if team.id is not None:
            team_id_map[team.id] = resolved
        team_id_map[resolved] = resolved

    async def _resolve_team_ref(ref: str, context: str) -> str | None:
        if ref in team_id_map:
            if team_id_map[ref] is None:
                summary.warnings.append(f"{context}: team {ref} was deleted, reference dropped")
            return team_id_map[ref]
        if await db.get(Team, ref) is not None:
            return ref
        errors.append(f"{context}: references unknown team {ref!r}")
        return None

    # --- resolve custom tools ------------------------------------------
    async def _custom_tools_named(name: str) -> list[Tool]:
        return list(
            (
                await db.scalars(select(Tool).where(Tool.tool_type == "custom", Tool.name == name))
            ).all()
        )

    tool_plans: list[_Plan] = []
    file_custom_tool_names: set[str] = set()
    for tool in document.tools:
        if not TOOL_NAME_RE.match(tool.name):
            errors.append(f"tool {tool.name!r}: name must be a valid Python identifier")
            continue
        if tool.source_code:
            try:
                compile(tool.source_code, f"<import:{tool.name}>", "exec")
            except SyntaxError as exc:
                errors.append(f"tool {tool.name!r}: invalid Python source: {exc}")
                continue
        plan = await _plan_entity(db, "tool", tool, _custom_tools_named, errors, summary)
        if plan is None:
            continue
        if plan.existing_id is not None:
            existing = await db.get(Tool, plan.existing_id)
            if existing is not None and existing.tool_type != "custom":
                errors.append(
                    f"tool {tool.name!r}: id {plan.existing_id} is a "
                    f"{existing.tool_type} tool, not a custom tool"
                )
                continue
        tool_plans.append(plan)
        file_custom_tool_names.add(tool.name)

    # --- resolve agents ------------------------------------------------
    async def _agents_named(name: str) -> list[Agent]:
        # Agent.name is globally unique (idx_agent_name), so 0 or 1 rows.
        return list((await db.scalars(select(Agent).where(Agent.name == name))).all())

    async def _resolve_tool_ref(ref: ExportedToolRef, agent_name: str) -> str | None:
        """Returns a tool id, or None for a (warned) dropped assignment.
        Marks a validation error only for a missing *custom* tool — the
        file is self-contained for those; builtin/mcp availability is
        environment-dependent, so a miss there degrades to a warning the
        same way sync degrades an unresolvable tool."""
        row = await db.scalar(
            select(Tool).where(Tool.tool_type == ref.type, Tool.name == ref.name).limit(1)
        )
        if row is not None:
            return row.id
        if ref.type == "custom" and ref.name in file_custom_tool_names:
            return None  # resolved post-creation, in the apply phase
        if ref.type == "custom":
            errors.append(
                f"agent {agent_name!r}: assigned custom tool {ref.name!r} is neither "
                "in this file nor already present"
            )
        else:
            summary.warnings.append(
                f"agent {agent_name!r}: {ref.type} tool {ref.name!r} not available "
                "here, assignment dropped"
            )
        return None

    agent_plans: list[_Plan] = []
    for agent in document.agents:
        for scope in agent.tool_scopes:
            if scope not in TOOL_SCOPES:
                errors.append(f"agent {agent.name!r}: unknown tool scope {scope!r}")
        for ref in agent.tools:
            if ref.type not in ("builtin", "custom", "mcp"):
                errors.append(
                    f"agent {agent.name!r}: unknown tool type {ref.type!r} for tool {ref.name!r}"
                )
            else:
                await _resolve_tool_ref(ref, agent.name)  # records errors/warnings
        for team_ref in agent.teams:
            await _resolve_team_ref(team_ref, f"agent {agent.name!r}")
        plan = await _plan_entity(db, "agent", agent, _agents_named, errors, summary)
        if plan is not None:
            agent_plans.append(plan)

    # --- resolve channels ----------------------------------------------
    async def _active_channels_named(name: str) -> list[Channel]:
        # Uniqueness (idx_channel_name) only covers non-archived channels,
        # so only those are merge targets — importing a channel whose name
        # matches an archived one creates a fresh channel, same as the
        # ordinary create route would allow.
        return list(
            (
                await db.scalars(
                    select(Channel).where(Channel.name == name, Channel.archived.is_(False))
                )
            ).all()
        )

    channel_plans: list[_Plan] = []
    for channel in document.channels:
        if channel.team_id is not None:
            await _resolve_team_ref(channel.team_id, f"channel {channel.name!r}")
        plan = await _plan_entity(db, "channel", channel, _active_channels_named, errors, summary)
        if plan is not None:
            channel_plans.append(plan)

    if errors:
        raise ImportValidationError(errors)

    # --- apply: teams --------------------------------------------------
    for plan in team_plans:
        team_item: ExportedTeam = plan.item
        if plan.existing_id is not None:
            team = await db.get(Team, plan.existing_id)
            assert team is not None  # validated above
            team.name = team_item.name
            team.description = team_item.description
            team.vector_clock += 1
            summary.count(summary.updated, "team")
        else:
            team = Team(id=plan.create_id, name=team_item.name, description=team_item.description)
            db.add(team)
            summary.count(summary.created, "team")
        await db.commit()
        await publish_current_state(db, "team", team.id)

    # --- apply: custom tools -------------------------------------------
    tools_changed = False
    for plan in tool_plans:
        tool_item: ExportedTool = plan.item
        if plan.existing_id is not None:
            tool = await db.get(Tool, plan.existing_id)
            assert tool is not None  # validated above
            tool.name = tool_item.name
            tool.description = tool_item.description
            summary.count(summary.updated, "tool")
        else:
            # Same id-keyed source_path scheme as api/tools.py's
            # create_tool (#289) — never keyed off the (mutable) name.
            tool = Tool(
                id=plan.create_id,
                name=tool_item.name,
                description=tool_item.description,
                tool_type="custom",
                source_path=str(get_settings().tools_dir / f"{plan.create_id}.py"),
            )
            db.add(tool)
            summary.count(summary.created, "tool")
        current_source = ""
        if plan.existing_id is not None and tool.source_path:
            try:
                current_source = Path(tool.source_path).read_text(encoding="utf-8")
            except OSError:
                current_source = ""
        if tool_item.source_code and tool_item.source_code != current_source:
            assert tool.source_path is not None  # every custom tool gets one
            latest = await db.scalar(
                select(ToolVersion.version)
                .where(ToolVersion.tool_id == tool.id)
                .order_by(ToolVersion.version.desc())
                .limit(1)
            )
            Path(tool.source_path).write_text(tool_item.source_code, encoding="utf-8")
            db.add(
                ToolVersion(
                    tool_id=tool.id,
                    version=(latest or 0) + 1,
                    source_code=tool_item.source_code,
                )
            )
            tools_changed = True
        tool.vector_clock += 1
        await db.commit()
        await publish_current_state(db, "tool", tool.id)

    # --- apply: agents -------------------------------------------------
    for plan in agent_plans:
        agent_item: ExportedAgent = plan.item
        fallback = json.dumps(agent_item.fallback_models) if agent_item.fallback_models else None
        schema = json.dumps(agent_item.output_schema) if agent_item.output_schema else None
        if plan.existing_id is not None:
            agent = await db.get(Agent, plan.existing_id)
            assert agent is not None  # validated above
            agent.name = agent_item.name
            agent.description = agent_item.description
            agent.instructions = agent_item.instructions
            agent.model = agent_item.model
            agent.fallback_models = fallback
            agent.output_schema = schema
            agent.approved_for_unattended_tools = agent_item.approved_for_unattended_tools
            agent.vector_clock += 1
            summary.count(summary.updated, "agent")
        else:
            agent = Agent(
                id=plan.create_id,
                name=agent_item.name,
                description=agent_item.description,
                instructions=agent_item.instructions,
                model=agent_item.model,
                fallback_models=fallback,
                output_schema=schema,
                approved_for_unattended_tools=agent_item.approved_for_unattended_tools,
            )
            db.add(agent)
            await db.flush()
            summary.count(summary.created, "agent")

        tool_ids: list[str] = []
        for ref in agent_item.tools:
            resolved_tool = await db.scalar(
                select(Tool).where(Tool.tool_type == ref.type, Tool.name == ref.name).limit(1)
            )
            if resolved_tool is not None:
                tool_ids.append(resolved_tool.id)
            # else: already warned during validation (builtin/mcp miss)
        team_ids = [
            resolved
            for team_ref in agent_item.teams
            if (resolved := team_id_map.get(team_ref, team_ref)) is not None
        ]
        old_tools, new_tools = await set_agent_tools(db, agent.id, tool_ids)
        old_teams, new_teams = await set_agent_teams(db, agent.id, team_ids)
        old_scopes, new_scopes = await set_agent_tool_scopes(db, agent.id, agent_item.tool_scopes)
        await record_agent_version(db, agent)
        await db.commit()
        await publish_agent_tools_change(db, agent.id, old_tools, new_tools)
        await publish_agent_teams_change(db, agent.id, old_teams, new_teams)
        await publish_agent_tool_scopes_change(db, agent.id, old_scopes, new_scopes)
        if agent_item.routing_rules is not None:
            await replace_routing_rules(
                db,
                agent.id,
                [(r.rule_type, r.pattern, r.priority) for r in agent_item.routing_rules],
            )
        await publish_agent_change(db, agent)

    # --- apply: channels -----------------------------------------------
    for plan in channel_plans:
        channel_item: ExportedChannel = plan.item
        resolved_team = (
            team_id_map.get(channel_item.team_id, channel_item.team_id)
            if channel_item.team_id is not None
            else None
        )
        if plan.existing_id is not None:
            channel = await db.get(Channel, plan.existing_id)
            assert channel is not None  # validated above
            channel.name = channel_item.name
            channel.description = channel_item.description
            channel.team_id = resolved_team
            channel.position = channel_item.position
            channel.archived = channel_item.archived
            channel.vector_clock += 1
            summary.count(summary.updated, "channel")
        else:
            channel = Channel(
                id=plan.create_id,
                name=channel_item.name,
                description=channel_item.description,
                team_id=resolved_team,
                position=channel_item.position,
                archived=channel_item.archived,
            )
            db.add(channel)
            summary.count(summary.created, "channel")
        await db.commit()
        await publish_current_state(db, "channel", channel.id)

    # --- apply: settings -----------------------------------------------
    for key, value in settings_to_apply.items():
        row = await db.get(WorkspaceSettingRow, key)
        if row is None:
            db.add(WorkspaceSettingRow(key=key, value=json.dumps(value)))
            summary.count(summary.created, "setting")
        else:
            row.value = json.dumps(value)
            row.vector_clock += 1
            summary.count(summary.updated, "setting")
    if settings_to_apply:
        await db.commit()
        for key in settings_to_apply:
            await publish_current_state(db, "workspace_setting", key)

    # One registry rebuild at the end instead of per-agent
    # register_agent_with_agentos calls: imported tool source and agent
    # definitions are loaded at agent *build* time (#362), and
    # record_registration_flags then rewrites every agentos_agent_id from
    # the live registry, same as login does.
    if agent_plans or tools_changed:
        await sync_agents(db)
        await record_registration_flags(db)

    return summary
