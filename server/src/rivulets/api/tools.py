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
from pydantic import BaseModel
from sqlalchemy import select

from rivulets.api.deps import CurrentWorkspaceId, DbSession
from rivulets.config import get_settings
from rivulets.db.models import Tool, ToolVersion
from rivulets.sync.publish import publish_current_state
from rivulets.tools.builtin import code_exec

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


class ToolCreate(BaseModel):
    name: str
    description: str
    mode: str = "advanced"  # "simple" | "advanced"
    prompt: str | None = None  # required when mode == "simple"


class ToolUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class ToolOut(BaseModel):
    id: str
    name: str
    description: str
    tool_type: str
    source_path: str | None
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


@router.post("", response_model=ToolOut, status_code=status.HTTP_201_CREATED)
async def create_tool(body: ToolCreate, db: DbSession, _: CurrentWorkspaceId) -> Tool:
    if body.mode == "simple":
        # TODO(FR-8.3): send body.prompt to an LLM, generate Agno tool code,
        # return it for review before creating the Tool/ToolVersion rows.
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED, "Simple-mode tool codegen not yet wired up"
        )

    source_path = str(get_settings().tools_dir / f"{body.name}.py")
    tool = Tool(
        name=body.name,
        description=body.description,
        tool_type="custom",
        source_path=source_path,
    )
    db.add(tool)
    await db.flush()
    db.add(ToolVersion(tool_id=tool.id, version=1, source_code=""))
    await db.commit()
    await db.refresh(tool)
    await _publish_tool_change(db, tool)
    return tool


@router.get("/{tool_id}", response_model=ToolOut)
async def get_tool(tool_id: str, db: DbSession, _: CurrentWorkspaceId) -> ToolOut:
    return ToolOut.from_tool(await _get_or_404(db, tool_id))


@router.patch("/{tool_id}", response_model=ToolOut)
async def update_tool(tool_id: str, body: ToolUpdate, db: DbSession, _: CurrentWorkspaceId) -> Tool:
    tool = await _get_or_404(db, tool_id)
    if tool.tool_type == "builtin":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Builtin tools cannot be modified")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(tool, field, value)
    tool.vector_clock += 1
    await db.commit()
    await db.refresh(tool)
    await _publish_tool_change(db, tool)
    return tool


@router.delete("/{tool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tool(tool_id: str, db: DbSession, _: CurrentWorkspaceId) -> None:
    tool = await _get_or_404(db, tool_id)
    if tool.tool_type == "builtin":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Builtin tools cannot be deleted")
    await db.delete(tool)
    await db.commit()


@router.get("/{tool_id}/versions", response_model=list[ToolVersionOut])
async def list_tool_versions(
    tool_id: str, db: DbSession, _: CurrentWorkspaceId
) -> list[ToolVersion]:
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
    tool_id: str, body: ToolVersionCreate, db: DbSession, _: CurrentWorkspaceId
) -> ToolVersion:
    """The "Save" side of the editor handoff (FR-8.4) — writes new source
    to the tool's file and records it as the next version. Rejects
    syntactically invalid Python outright (compile-only, not executed —
    running arbitrary just-submitted code as part of a save request would
    be its own risk) rather than silently accepting code that would just
    make the tool unresolvable at agent-build time."""
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
    await _publish_tool_change(db, tool)
    return version_row


@router.post("/{tool_id}/versions/{version}/rollback", response_model=ToolOut)
async def rollback_tool_version(
    tool_id: str, version: int, db: DbSession, _: CurrentWorkspaceId
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
    await _publish_tool_change(db, tool)
    return tool


@router.post("/{tool_id}/open-editor")
async def open_tool_editor(tool_id: str, db: DbSession, _: CurrentWorkspaceId) -> dict[str, str]:
    """Returns the tool's file path for the UI to hand to the OS "open with
    default editor" call (FR-8.4). Detecting/launching the editor itself is
    a UI-side concern, not this endpoint's."""
    tool = await _get_or_404(db, tool_id)
    if not tool.source_path:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Tool has no source file")
    return {"path": tool.source_path}
