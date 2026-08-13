"""Agent CRUD (FR-3) and routing rules (FR-3.3, FR-4.2).

Registering with AgentOS (FR-3.2) happens via agentos/service.py's
sync_agents() after every create/update/delete commit — see that module's
docstring for how "registration" works without an HTTP AgentOS API.
Routing rules are auto-generated via dispatch/rule_generation.py's LLM
call (FR-3.3, US-017) on create, and regenerated on update when the
description or instructions change (FR-3.4).

Agents are also the first entity type wired into P2P sync (FR-9.1's thin
first slice — see sync/apply.py's module docstring): every create/update
bumps this node's vector-clock component and publishes the new state to
peers. Publishing is best-effort — a peer being unreachable, or the sync
engine not running at all, must never fail the request (FR-9.5).

Every create/update/rollback that sets instructions/model also snapshots
an AgentVersion row (#104) — see record_agent_version and the
/{agent_id}/versions endpoints below for the history/rollback surface.
"""

import json
from typing import cast

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from rivulets.agentos import sync_agents
from rivulets.agentos.agent_lifecycle import (
    generate_and_store_routing_rules,
    publish_agent_change,
    record_agent_version,
    register_agent_with_agentos,
    set_agent_teams,
    set_agent_tools,
)
from rivulets.agentos.tool_scopes import TOOL_SCOPES
from rivulets.api.deps import CurrentWorkspaceId, DbSession, OwnerGrant
from rivulets.db.models import (
    Agent,
    AgentPeerPreference,
    AgentRoutingRule,
    AgentRun,
    AgentToolScope,
    AgentVersion,
)
from rivulets.sync.publish import publish_current_state

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentCreate(BaseModel):
    name: str = Field(min_length=2, max_length=64)
    description: str = Field(min_length=10, max_length=500)
    instructions: str
    model: str  # "provider:model_name"
    # Ordered "provider:model_name" strings (#103): tried in turn if
    # `model`'s call fails with a retryable-looking error.
    fallback_models: list[str] = Field(default_factory=list)
    # #107: a raw JSON Schema object constraining this agent's reply. None
    # (the default) means free-form text -- the only behavior that existed
    # before this field did.
    output_schema: dict[str, object] | None = None
    tool_ids: list[str] = Field(default_factory=list)
    team_ids: list[str] = Field(default_factory=list)


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    instructions: str | None = None
    model: str | None = None
    fallback_models: list[str] | None = None
    # #107: same "None means don't touch, {} clears it" convention as
    # fallback_models' "None means don't touch, [] clears it" above --
    # there's otherwise no way to distinguish "not sent" from "explicitly
    # cleared" on an already-nullable field.
    output_schema: dict[str, object] | None = None
    tool_ids: list[str] | None = None
    team_ids: list[str] | None = None
    # #100: one-time approval to run this agent's sensitive tools (if any
    # are assigned) unattended -- see Agent.approved_for_unattended_tools.
    approved_for_unattended_tools: bool | None = None


class AgentOut(BaseModel):
    id: str
    name: str
    description: str
    instructions: str
    model: str
    fallback_models: list[str] = Field(default_factory=list)
    output_schema: dict[str, object] | None = None
    approved_for_unattended_tools: bool
    agentos_agent_id: str | None

    model_config = {"from_attributes": True}

    @field_validator("fallback_models", mode="before")
    @classmethod
    def _parse_fallback_models(cls, value: object) -> list[str]:
        # Agent.fallback_models is stored as a JSON string (same
        # convention as AgentRoutingRule.pattern) -- unpack it into the
        # list the API actually exposes.
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except ValueError:
                return []
            if not isinstance(parsed, list):
                return []
            return [item for item in cast(list[object], parsed) if isinstance(item, str)]
        if isinstance(value, list):
            return [item for item in cast(list[object], value) if isinstance(item, str)]
        return []

    @field_validator("output_schema", mode="before")
    @classmethod
    def _parse_output_schema(cls, value: object) -> dict[str, object] | None:
        # Agent.output_schema is stored as a JSON string (same convention
        # as fallback_models above) -- unpack it into the dict the API
        # actually exposes.
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except ValueError:
                return None
            return cast(dict[str, object], parsed) if isinstance(parsed, dict) else None
        if isinstance(value, dict):
            return cast(dict[str, object], value)
        return None


class AgentRunOut(BaseModel):
    id: str
    model: str
    # Set only when a fallback chain (#103) served this run instead of
    # the model that was actually asked for.
    requested_model: str | None
    tier: str | None
    status: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float | None
    created_at: str

    model_config = {"from_attributes": True}


class RoutingRuleOut(BaseModel):
    id: str
    rule_type: str
    pattern: str
    priority: int

    model_config = {"from_attributes": True}


class RoutingRuleIn(BaseModel):
    rule_type: str
    pattern: str
    priority: int = 0


class RoutingRulesUpdate(BaseModel):
    rules: list[RoutingRuleIn]


class PeerPreferenceOut(BaseModel):
    capability_tag: str | None


class PeerPreferenceIn(BaseModel):
    capability_tag: str | None = None


class AgentToolScopesOut(BaseModel):
    scopes: list[str]


class AgentToolScopesIn(BaseModel):
    scopes: list[str]


class AgentVersionOut(BaseModel):
    version: int
    instructions: str
    model: str
    created_at: str

    model_config = {"from_attributes": True}


async def _get_or_404(db: DbSession, agent_id: str) -> Agent:
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
    return agent


@router.get("", response_model=list[AgentOut])
async def list_agents(db: DbSession, _: CurrentWorkspaceId) -> list[Agent]:
    result = await db.execute(select(Agent))
    return list(result.scalars().all())


async def _check_name_available(db: DbSession, name: str) -> None:
    """Mirrors api/workflows.py's create/update pre-check -- a lookup
    first so the common case (no collision) fails fast with a real 409
    instead of the raw IntegrityError Agent.name's UniqueConstraint would
    otherwise raise past the flush() below (#250)."""
    existing = await db.scalar(select(Agent).where(Agent.name == name))
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"An agent named {name!r} already exists")


@router.post("", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
async def create_agent(body: AgentCreate, db: DbSession, _: CurrentWorkspaceId) -> Agent:
    await _check_name_available(db, body.name)
    agent = Agent(
        name=body.name,
        description=body.description,
        instructions=body.instructions,
        model=body.model,
        fallback_models=json.dumps(body.fallback_models) if body.fallback_models else None,
        output_schema=json.dumps(body.output_schema) if body.output_schema else None,
    )
    db.add(agent)
    try:
        await db.flush()  # populate agent.id before using it in join rows
    except IntegrityError as exc:
        # The pre-check above closes the common case; this closes the race
        # window between it and the flush (two concurrent creates with the
        # same name) -- same treatment sync/apply.py gives a losing
        # IntegrityError on commit.
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"An agent named {body.name!r} already exists"
        ) from exc

    await set_agent_tools(db, agent.id, body.tool_ids)
    await set_agent_teams(db, agent.id, body.team_ids)
    await record_agent_version(db, agent)
    await db.commit()

    await generate_and_store_routing_rules(db, agent)
    await register_agent_with_agentos(db, agent)
    await publish_agent_change(db, agent)
    return agent


@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(agent_id: str, db: DbSession, _: CurrentWorkspaceId) -> Agent:
    return await _get_or_404(db, agent_id)


@router.patch("/{agent_id}", response_model=AgentOut)
async def update_agent(
    agent_id: str, body: AgentUpdate, db: DbSession, _: CurrentWorkspaceId
) -> Agent:
    agent = await _get_or_404(db, agent_id)
    if body.name is not None and body.name != agent.name:
        await _check_name_available(db, body.name)
    rule_regen_fields = {"description", "instructions"}
    updates = body.model_dump(
        exclude_unset=True, exclude={"tool_ids", "team_ids", "fallback_models", "output_schema"}
    )
    needs_rule_regen = rule_regen_fields & updates.keys()
    old_instructions, old_model = agent.instructions, agent.model

    for field, value in updates.items():
        setattr(agent, field, value)
    if body.fallback_models is not None:
        agent.fallback_models = json.dumps(body.fallback_models) if body.fallback_models else None
    if body.output_schema is not None:
        agent.output_schema = json.dumps(body.output_schema) if body.output_schema else None
    if body.tool_ids is not None:
        await set_agent_tools(db, agent_id, body.tool_ids)
    if body.team_ids is not None:
        await set_agent_teams(db, agent_id, body.team_ids)

    if agent.instructions != old_instructions or agent.model != old_model:
        await record_agent_version(db, agent)

    agent.vector_clock += 1
    try:
        await db.commit()
    except IntegrityError as exc:
        # Same race-window close as create_agent's flush() above -- two
        # concurrent renames to the same name both pass the pre-check.
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"An agent named {body.name!r} already exists"
        ) from exc

    if needs_rule_regen:
        await db.execute(delete(AgentRoutingRule).where(AgentRoutingRule.agent_id == agent_id))
        await generate_and_store_routing_rules(db, agent)

    await register_agent_with_agentos(db, agent)
    await publish_agent_change(db, agent)
    return agent


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(agent_id: str, db: DbSession, _: CurrentWorkspaceId) -> None:
    agent = await _get_or_404(db, agent_id)
    await db.delete(agent)
    await db.commit()
    # Rebuilds AgentOS's registry from the remaining rows — the deleted
    # agent simply won't be in it anymore (FR-3.2's "unregister").
    await sync_agents(db)


@router.get("/{agent_id}/runs", response_model=list[AgentRunOut])
async def get_agent_runs(agent_id: str, db: DbSession, _: CurrentWorkspaceId) -> list[AgentRun]:
    """Run history (FR-3.5): most recent 100 runs, newest first. Rows come
    from dispatch/service.py's `_record_agent_run`, added alongside #28's
    workspace-level usage dashboard — not AgentOS's own SqliteDb, which
    isn't queried by this app (see agentos/service.py's module docstring)."""
    await _get_or_404(db, agent_id)
    result = await db.execute(
        select(AgentRun)
        .where(AgentRun.agent_id == agent_id)
        .order_by(AgentRun.created_at.desc())
        .limit(100)
    )
    return list(result.scalars().all())


@router.get("/{agent_id}/versions", response_model=list[AgentVersionOut])
async def list_agent_versions(
    agent_id: str, db: DbSession, _: CurrentWorkspaceId
) -> list[AgentVersion]:
    """Instructions/model history (#104): newest first, so a regression
    can be diffed against what the agent used to say."""
    await _get_or_404(db, agent_id)
    result = await db.execute(
        select(AgentVersion)
        .where(AgentVersion.agent_id == agent_id)
        .order_by(AgentVersion.version.desc())
    )
    return list(result.scalars().all())


@router.post("/{agent_id}/versions/{version}/rollback", response_model=AgentOut)
async def rollback_agent_version(
    agent_id: str, version: int, db: DbSession, _: CurrentWorkspaceId
) -> Agent:
    """Reverts instructions/model to a prior version (#104) and records the
    rollback itself as a new version -- same "what's now current becomes
    the next version" treatment as rollback_tool_version, so a rollback is
    itself diffable/revertible rather than silently overwriting history.
    Regenerates routing rules when instructions changed, matching a normal
    instructions edit's needs_rule_regen handling above (FR-3.4) --
    otherwise a reverted agent could keep routing rules generated for
    instructions it no longer has."""
    agent = await _get_or_404(db, agent_id)
    target = await db.scalar(
        select(AgentVersion).where(
            AgentVersion.agent_id == agent_id, AgentVersion.version == version
        )
    )
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Version not found")

    instructions_changed = agent.instructions != target.instructions
    agent.instructions = target.instructions
    agent.model = target.model
    await record_agent_version(db, agent)
    agent.vector_clock += 1
    await db.commit()

    if instructions_changed:
        await db.execute(delete(AgentRoutingRule).where(AgentRoutingRule.agent_id == agent_id))
        await generate_and_store_routing_rules(db, agent)

    await db.refresh(agent)
    await register_agent_with_agentos(db, agent)
    await publish_agent_change(db, agent)
    return agent


@router.get("/{agent_id}/routing-rules", response_model=list[RoutingRuleOut])
async def get_routing_rules(
    agent_id: str, db: DbSession, _: CurrentWorkspaceId
) -> list[AgentRoutingRule]:
    await _get_or_404(db, agent_id)
    result = await db.execute(
        select(AgentRoutingRule)
        .where(AgentRoutingRule.agent_id == agent_id)
        .order_by(AgentRoutingRule.priority.desc())
    )
    return list(result.scalars().all())


@router.patch("/{agent_id}/routing-rules", response_model=list[RoutingRuleOut])
async def update_routing_rules(
    agent_id: str, body: RoutingRulesUpdate, db: DbSession, _: CurrentWorkspaceId
) -> list[AgentRoutingRule]:
    await _get_or_404(db, agent_id)
    await db.execute(delete(AgentRoutingRule).where(AgentRoutingRule.agent_id == agent_id))
    rows = [
        AgentRoutingRule(
            agent_id=agent_id,
            rule_type=r.rule_type,
            pattern=r.pattern,
            priority=r.priority,
        )
        for r in body.rules
    ]
    db.add_all(rows)
    await db.commit()
    for row in rows:
        await db.refresh(row)
    return rows


@router.get("/{agent_id}/peer-preference", response_model=PeerPreferenceOut)
async def get_peer_preference(
    agent_id: str, db: DbSession, _: CurrentWorkspaceId
) -> PeerPreferenceOut:
    """Issue #10: which capability tag (if any) this agent should
    preferentially run on. See dispatch/service.py's _resolve_remote_peer
    for how this is consumed at dispatch time."""
    await _get_or_404(db, agent_id)
    pref = await db.get(AgentPeerPreference, agent_id)
    return PeerPreferenceOut(capability_tag=pref.capability_tag if pref else None)


@router.put("/{agent_id}/peer-preference", response_model=PeerPreferenceOut)
async def set_peer_preference(
    agent_id: str, body: PeerPreferenceIn, db: DbSession, _: CurrentWorkspaceId
) -> PeerPreferenceOut:
    """capability_tag=None clears the preference. Clearing is local-only
    (not propagated to peers) -- same as Agent/Team deletion elsewhere in
    this API, sync's generic path has no delete-propagation mechanism."""
    await _get_or_404(db, agent_id)
    pref = await db.get(AgentPeerPreference, agent_id)
    if body.capability_tag is None:
        if pref is not None:
            await db.delete(pref)
            await db.commit()
        return PeerPreferenceOut(capability_tag=None)
    if pref is None:
        pref = AgentPeerPreference(agent_id=agent_id, capability_tag=body.capability_tag)
        db.add(pref)
    else:
        pref.capability_tag = body.capability_tag
        pref.vector_clock += 1
    await db.commit()
    await publish_current_state(db, "agent_peer_preference", agent_id)
    return PeerPreferenceOut(capability_tag=pref.capability_tag)


@router.get("/{agent_id}/tool-scopes", response_model=AgentToolScopesOut)
async def get_agent_tool_scopes(
    agent_id: str, db: DbSession, _: CurrentWorkspaceId
) -> AgentToolScopesOut:
    """Capability scopes (#188) currently granted to this agent -- bounds
    which of its assigned tools with a Tool.required_scope actually
    resolve at run time. See tool_resolution.py's resolve_agent_tools."""
    await _get_or_404(db, agent_id)
    result = await db.execute(
        select(AgentToolScope.scope).where(AgentToolScope.agent_id == agent_id)
    )
    return AgentToolScopesOut(scopes=sorted(result.scalars().all()))


@router.put("/{agent_id}/tool-scopes", response_model=AgentToolScopesOut)
async def set_agent_tool_scopes(
    agent_id: str, body: AgentToolScopesIn, db: DbSession, _: CurrentWorkspaceId, __: OwnerGrant
) -> AgentToolScopesOut:
    """Owner-only (#188's design decision): an agent shouldn't be able to
    expand its own reach, and neither should an invited session, so this
    is gated separately from the rest of this router. Replaces the full
    granted-scope set, same delete+recreate shape as set_agent_tools/set_agent_teams
    above. Unknown scope names are rejected rather than silently stored --
    a typo'd scope would grant nothing (no tool's required_scope would
    ever match it) while looking like it worked. Re-syncs AgentOS
    afterward so the change actually takes effect -- tool resolution
    happens at agent-build time (agentos/service.py's _build_agno_agent),
    not per run."""
    agent = await _get_or_404(db, agent_id)
    unknown = sorted(set(body.scopes) - TOOL_SCOPES)
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown scope(s): {', '.join(unknown)}")
    await db.execute(delete(AgentToolScope).where(AgentToolScope.agent_id == agent_id))
    granted = set(body.scopes)
    for scope in granted:
        db.add(AgentToolScope(agent_id=agent_id, scope=scope))
    await db.commit()
    await register_agent_with_agentos(db, agent)
    return AgentToolScopesOut(scopes=sorted(granted))
