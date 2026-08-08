"""Slash-command triggering for workflows (#24): `/​{workflow.name} <input>`
in a channel runs that workflow instead of the normal dispatcher.

Kept separate from the execution engine (workflows/engine.py) and from
api/rivulets.py's HTTP handlers so both the human-typed path (api/
rivulets.py) and the agent-triggered path (tools/builtin/run_workflow.py,
"a human typing `@some-agent run this workflow` should let that agent
launch it") share the exact same command-parsing and workflow-lookup
logic rather than each reimplementing it slightly differently.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.models import Workflow


def parse_slash_command(content: str) -> tuple[str, str] | None:
    """Returns (command_name, remaining_input) if `content` starts with a
    `/command` token, else None. Only the *shape* is checked here — matching
    the name against an actual Workflow is find_triggered_workflow's job,
    so a message that merely looks like a slash command (e.g. "/etc/passwd"
    pasted into chat) but isn't a registered workflow name falls through to
    ordinary dispatch unchanged, not an error."""
    if not content.startswith("/"):
        return None
    command, _, rest = content[1:].partition(" ")
    if not command:
        return None
    return command.lower(), rest.strip()


async def find_workflow_by_name(db: AsyncSession, name: str) -> Workflow | None:
    return await db.scalar(select(Workflow).where(Workflow.name == name.lower()))


async def find_triggered_workflow(db: AsyncSession, content: str) -> tuple[Workflow, str] | None:
    """Returns (workflow, workflow_input) if `content` triggers a known
    workflow, else None."""
    parsed = parse_slash_command(content)
    if parsed is None:
        return None
    command_name, remaining = parsed
    workflow = await find_workflow_by_name(db, command_name)
    if workflow is None:
        return None
    return workflow, remaining
