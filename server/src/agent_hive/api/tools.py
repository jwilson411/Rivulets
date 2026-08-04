"""Tool CRUD (FR-8.2 through FR-8.4).

Simple-mode codegen (send a prompt to an LLM, review, approve) and the
advanced-mode editor handoff both need integrations this scaffold doesn't
wire up yet (an LLM client, and OS-specific "open in default editor"
logic) — marked TODO. Listing/registering/versioning tool rows is real.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from agent_hive.api.deps import CurrentWorkspaceId, DbSession
from agent_hive.config import get_settings
from agent_hive.db.models import Tool, ToolVersion

router = APIRouter(prefix="/tools", tags=["tools"])


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

    model_config = {"from_attributes": True}


class ToolVersionOut(BaseModel):
    version: int
    source_code: str
    created_at: str

    model_config = {"from_attributes": True}


async def _get_or_404(db: DbSession, tool_id: str) -> Tool:
    tool = await db.get(Tool, tool_id)
    if tool is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tool not found")
    return tool


@router.get("", response_model=list[ToolOut])
async def list_tools(db: DbSession, _: CurrentWorkspaceId) -> list[Tool]:
    result = await db.execute(select(Tool))
    return list(result.scalars().all())


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
    return tool


@router.get("/{tool_id}", response_model=ToolOut)
async def get_tool(tool_id: str, db: DbSession, _: CurrentWorkspaceId) -> Tool:
    return await _get_or_404(db, tool_id)


@router.patch("/{tool_id}", response_model=ToolOut)
async def update_tool(tool_id: str, body: ToolUpdate, db: DbSession, _: CurrentWorkspaceId) -> Tool:
    tool = await _get_or_404(db, tool_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(tool, field, value)
    tool.vector_clock += 1
    await db.commit()
    await db.refresh(tool)
    return tool


@router.delete("/{tool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tool(tool_id: str, db: DbSession, _: CurrentWorkspaceId) -> None:
    tool = await _get_or_404(db, tool_id)
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
