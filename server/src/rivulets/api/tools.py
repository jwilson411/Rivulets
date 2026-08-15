"""Tool CRUD (FR-8.2 through FR-8.4).

Simple-mode codegen (send a prompt to an LLM, review, approve) still
needs an LLM client wired up — marked TODO. The advanced-mode editor
handoff is real on both ends now: open_tool_editor hands the UI a path
to open in the OS's default editor (FR-8.4), and save_tool_version
(below) is what a "Save" action in that flow calls — until it existed, a
tool created via POST /tools had no way to ever get real code onto disk
short of something writing to source_path directly outside the API, so
every custom tool a user "wrote" was actually silently unusable
(tool_resolution.py's _load_custom_tool skips a tool whose source file
doesn't define a matching function).

Custom tools are also synced (FR-9.1's "tool code") via
sync/apply.py's apply_remote_tool_change — see _publish_tool_change
below. `mcp`/`builtin` tool rows aren't synced (per-node caches, matching
MCPServer's own tools list). Publishing always re-reads the file at
tool.source_path rather than trusting the latest ToolVersion row: rollback
below writes straight to disk without recording a new version, so the DB's
"latest version" and what's actually on disk can disagree — the file is
the source of truth for what this node currently has.
"""

from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from rivulets.agentos import sync_agents
from rivulets.agentos.tool_scopes import TOOL_SCOPES
from rivulets.api.deps import CurrentWorkspaceId, DbSession, OwnerGrant
from rivulets.config import get_settings
from rivulets.db.base import uuid7
from rivulets.db.models import Tool, ToolVersion
from rivulets.sync.publish import publish_current_state, publish_tombstone
from rivulets.tools.builtin import code_exec
from rivulets.validation import TOOL_NAME_RE

router = APIRouter(prefix="/tools", tags=["tools"])

# Per-builtin-tool availability checks (NFR-2.4's "unavailable" pattern,
# same one used for unreachable model providers): a builtin tool can be
# implemented but still non-functional on *this* machine — e.g.
# execute_python needs a sandbox backend that isn't installed. Checked at
# read time, not cached, since e.g. installing firejail shouldn't require
# an app restart to be reflected here.
_BUILTIN_AVAILABILITY: dict[str, Callable[[], bool]] = {
    "execute_python": code_exec.is_available,
}


def _is_available(tool: Tool) -> bool:
    if tool.tool_type != "builtin":
        return True
    check = _BUILTIN_AVAILABILITY.get(tool.name)
    return check() if check is not None else True


async def _publish_tool_change(db: DbSession, tool: Tool) -> None:
    await publish_current_state(db, "tool", tool.id)


# body.name must also be a valid Python identifier -- it's the name of the
# function _load_custom_tool (agentos/tool_resolution.py) looks up inside
# the tool's source file. source_path itself is keyed off the Tool's id,
# not this name (#289), so a name no longer needs to double as a safe path
# segment -- but sync/apply.py's apply_remote_tool_change still reuses this
# same regex to validate an incoming peer's name before trusting it.
_TOOL_NAME_RE = TOOL_NAME_RE


class ToolCreate(BaseModel):
    name: str
    description: str
    mode: str = "advanced"  # "simple" | "advanced"
    prompt: str | None = None  # required when mode == "simple"

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not _TOOL_NAME_RE.match(value):
            raise ValueError(
                "Tool name must be a valid Python identifier "
                "(letters, digits, underscores; can't start with a digit)"
            )
        return value


class ToolUpdate(BaseModel):
    name: str | None = None
    description: str | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str | None) -> str | None:
        if value is not None and not _TOOL_NAME_RE.match(value):
            raise ValueError(
                "Tool name must be a valid Python identifier "
                "(letters, digits, underscores; can't start with a digit)"
            )
        return value


class ToolOut(BaseModel):
    id: str
    name: str
    description: str
    tool_type: str
    source_path: str | None
    # #100: real blast radius (code exec, outbound HTTP, filesystem
    # writes, DB access) -- read-only here, not in ToolCreate/ToolUpdate;
    # v1 only marks the fixed builtin set (tool_resolution.py's
    # SENSITIVE_BUILTIN_TOOL_NAMES), no UI to mark a custom/mcp tool
    # sensitive yet.
    sensitive: bool = False
    # #188: the capability scope (if any) an agent must be granted before
    # this tool actually resolves for it -- read-only here, set only by
    # seed_builtin_tools via BUILTIN_TOOL_SCOPES (no custom/mcp tool
    # declares one yet). None means no scope is required, same as every
    # tool before #188.
    required_scope: str | None = None
    available: bool = True

    model_config = {"from_attributes": True}

    @classmethod
    def from_tool(cls, tool: Tool) -> "ToolOut":
        return cls.model_validate(tool).model_copy(update={"available": _is_available(tool)})


class ToolVersionOut(BaseModel):
    version: int
    source_code: str
    created_at: str

    model_config = {"from_attributes": True}


class ToolVersionCreate(BaseModel):
    source_code: str


async def _get_or_404(db: DbSession, tool_id: str) -> Tool:
    tool = await db.get(Tool, tool_id)
    if tool is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tool not found")
    return tool


@router.get("", response_model=list[ToolOut])
async def list_tools(db: DbSession, _: CurrentWorkspaceId) -> list[ToolOut]:
    result = await db.execute(select(Tool))
    return [ToolOut.from_tool(t) for t in result.scalars().all()]


async def _check_custom_name_available(db: DbSession, name: str) -> None:
    """Mirrors api/agents.py's _check_name_available -- a lookup first so
    the common case fails fast with a real 409 instead of the raw
    IntegrityError idx_tool_custom_name would otherwise raise past the
    flush()/commit() below (#289)."""
    existing = await db.scalar(select(Tool).where(Tool.tool_type == "custom", Tool.name == name))
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"A custom tool named {name!r} already exists"
        )


@router.post("", response_model=ToolOut, status_code=status.HTTP_201_CREATED)
async def create_tool(
    body: ToolCreate, db: DbSession, _: CurrentWorkspaceId, _o: OwnerGrant
) -> Tool:
    if body.mode == "simple":
        # TODO(FR-8.3): send body.prompt to an LLM, generate Agno tool code,
        # return it for review before creating the Tool/ToolVersion rows.
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED, "Simple-mode tool codegen not yet wired up"
        )

    await _check_custom_name_available(db, body.name)

    # #289: source_path is keyed off the tool's own id, not its (mutable,
    # collidable-in-flight-with-a-peer's) name -- generated up front so it
    # can go straight into the Tool row's constructor rather than needing a
    # flush first just to learn the id.
    tool_id = uuid7()
    source_path = str(get_settings().tools_dir / f"{tool_id}.py")
    tool = Tool(
        id=tool_id,
        name=body.name,
        description=body.description,
        tool_type="custom",
        source_path=source_path,
    )
    db.add(tool)
    try:
        await db.flush()
    except IntegrityError as exc:
        # The pre-check above closes the common case; this closes the race
        # window between it and the flush (two concurrent creates, or a
        # concurrent sync apply, with the same name) -- same treatment
        # api/agents.py's create_agent gives its own name race.
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"A custom tool named {body.name!r} already exists"
        ) from exc
    db.add(ToolVersion(tool_id=tool.id, version=1, source_code=""))
    await db.commit()
    await db.refresh(tool)
    await _publish_tool_change(db, tool)
    return tool


@router.get("/scopes", response_model=list[str])
async def list_tool_scopes(_: CurrentWorkspaceId) -> list[str]:
    """The fixed catalog of capability scopes (#188, agentos/
    tool_scopes.py's TOOL_SCOPES) a workspace owner can grant to an agent
    via PUT /agents/{agent_id}/tool-scopes. Registered ahead of the
    /{tool_id} route below so "scopes" isn't swallowed as a tool ID."""
    return sorted(TOOL_SCOPES)


@router.get("/{tool_id}", response_model=ToolOut)
async def get_tool(tool_id: str, db: DbSession, _: CurrentWorkspaceId) -> ToolOut:
    return ToolOut.from_tool(await _get_or_404(db, tool_id))


@router.patch("/{tool_id}", response_model=ToolOut)
async def update_tool(
    tool_id: str, body: ToolUpdate, db: DbSession, _: CurrentWorkspaceId, _o: OwnerGrant
) -> Tool:
    tool = await _get_or_404(db, tool_id)
    if tool.tool_type == "builtin":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Builtin tools cannot be modified")
    if body.name is not None and body.name != tool.name and tool.tool_type == "custom":
        await _check_custom_name_available(db, body.name)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(tool, field, value)
    tool.vector_clock += 1
    try:
        await db.commit()
    except IntegrityError as exc:
        # Same race-window close as create_tool's flush() above -- two
        # concurrent renames (or a rename racing a sync apply) to the same
        # name both pass the pre-check.
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"A custom tool named {body.name!r} already exists"
        ) from exc
    await db.refresh(tool)
    await _publish_tool_change(db, tool)
    return tool


@router.delete("/{tool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tool(tool_id: str, db: DbSession, _: CurrentWorkspaceId, _o: OwnerGrant) -> None:
    tool = await _get_or_404(db, tool_id)
    if tool.tool_type == "builtin":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Builtin tools cannot be deleted")
    is_custom = tool.tool_type == "custom"
    # #362: captured before commit (the ORM expires attributes on
    # committed-deleted instances), unlinked only after the delete actually
    # commits -- the source file is where operators bake integration
    # secrets (see list_tool_versions), so it must not outlive the row.
    source_path = tool.source_path if is_custom else None
    await db.delete(tool)
    await db.commit()
    if source_path:
        Path(source_path).unlink(missing_ok=True)
    # #362: custom tools are loaded at agent *build* time, so an agent this
    # tool was assigned to keeps the deleted function in memory until its
    # next rebuild -- same reason set_agent_tools resyncs.
    await sync_agents(db)
    # #287: only 'custom' tools are synced (module docstring) -- an 'mcp'
    # tool row is a per-node discovery cache with an id no peer shares, so
    # tombstoning it would be a harmless no-op there but is skipped anyway
    # to keep this scoped to what's actually synced.
    if is_custom:
        await publish_tombstone(db, "tool", tool_id)


@router.get("/{tool_id}/versions", response_model=list[ToolVersionOut])
async def list_tool_versions(
    tool_id: str, db: DbSession, _: CurrentWorkspaceId, _o: OwnerGrant
) -> list[ToolVersion]:
    """Owner-gated (#321): ToolVersionOut carries source_code, and custom
    tools are exactly where operators put integration secrets (API keys,
    webhook URLs) that were deliberately kept off provider_config/the
    keychain. #285 stopped invite-grant from writing that source
    (save_tool_version above); this stops it from reading it back out
    over the same API."""
    await _get_or_404(db, tool_id)
    result = await db.execute(
        select(ToolVersion)
        .where(ToolVersion.tool_id == tool_id)
        .order_by(ToolVersion.version.desc())
    )
    return list(result.scalars().all())


@router.post(
    "/{tool_id}/versions", response_model=ToolVersionOut, status_code=status.HTTP_201_CREATED
)
async def save_tool_version(
    tool_id: str, body: ToolVersionCreate, db: DbSession, _: CurrentWorkspaceId, _o: OwnerGrant
) -> ToolVersion:
    """The "Save" side of the editor handoff (FR-8.4) — writes new source
    to the tool's file and records it as the next version. Rejects
    syntactically invalid Python outright (compile-only, not executed —
    running arbitrary just-submitted code as part of a save request would
    be its own risk) rather than silently accepting code that would just
    make the tool unresolvable at agent-build time.

    Owner-gated: this is the one place arbitrary Python becomes a custom
    tool's source, and _load_custom_tool (agentos/tool_resolution.py) execs
    that file directly in the app-server process, unsandboxed, the moment
    the tool is assigned to and run by an agent. An invite-grant session
    must never reach this -- same "sensitive surface" bucket as provider
    credentials/backups/sync/settings (api/deps.py's require_owner_grant)."""
    tool = await _get_or_404(db, tool_id)
    if tool.tool_type != "custom":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only custom tools have editable source")
    assert tool.source_path is not None  # invariant: every custom tool gets one on create

    try:
        compile(body.source_code, tool.source_path, "exec")
    except SyntaxError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid Python: {exc}") from exc

    latest_version = await db.scalar(
        select(ToolVersion.version)
        .where(ToolVersion.tool_id == tool_id)
        .order_by(ToolVersion.version.desc())
        .limit(1)
    )
    next_version = (latest_version or 0) + 1

    Path(tool.source_path).write_text(body.source_code, encoding="utf-8")
    version_row = ToolVersion(tool_id=tool_id, version=next_version, source_code=body.source_code)
    db.add(version_row)
    tool.vector_clock += 1
    await db.commit()
    await db.refresh(version_row)
    # #362: custom tool source is loaded at agent *build* time
    # (agentos/tool_resolution.py), so without a rebuild every agent this
    # tool is already assigned to keeps executing the previous version
    # from memory until some unrelated change happens to rebuild it.
    await sync_agents(db)
    await _publish_tool_change(db, tool)
    return version_row


@router.post("/{tool_id}/versions/{version}/rollback", response_model=ToolOut)
async def rollback_tool_version(
    tool_id: str, version: int, db: DbSession, _: CurrentWorkspaceId, _o: OwnerGrant
) -> Tool:
    tool = await _get_or_404(db, tool_id)
    target = await db.scalar(
        select(ToolVersion).where(ToolVersion.tool_id == tool_id, ToolVersion.version == version)
    )
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Version not found")
    if tool.source_path:
        Path(tool.source_path).write_text(target.source_code, encoding="utf-8")
    tool.vector_clock += 1
    await db.commit()
    await db.refresh(tool)
    # #362: same rebuild-after-source-change as save_tool_version above.
    await sync_agents(db)
    await _publish_tool_change(db, tool)
    return tool


@router.post("/{tool_id}/open-editor")
async def open_tool_editor(
    tool_id: str, db: DbSession, _: CurrentWorkspaceId, _o: OwnerGrant
) -> dict[str, str]:
    """Returns the tool's file path for the UI to hand to the OS "open with
    default editor" call (FR-8.4). Detecting/launching the editor itself is
    a UI-side concern, not this endpoint's."""
    tool = await _get_or_404(db, tool_id)
    if not tool.source_path:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tool has no source file")
    return {"path": tool.source_path}
