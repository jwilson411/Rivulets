"""Slash-command triggering for workflows (#24): `/​{workflow.name} <input>`
in a channel runs that workflow instead of the normal dispatcher.

Kept separate from the execution engine (workflows/engine.py) and from
api/rivulets.py's HTTP handlers so both the human-typed path (api/
rivulets.py) and the agent-triggered path (tools/builtin/run_workflow.py,
"a human typing `@some-agent run this workflow` should let that agent
launch it") share the exact same command-parsing and workflow-lookup
logic rather than each reimplementing it slightly differently.

find_awaiting_workflow_run (#83) is a second, unrelated kind of
"triggering": not a fresh workflow start, but resuming one already
paused on a 'human_input' node. api/rivulets.py checks it first, ahead of
find_triggered_workflow -- if a rivulet is mid-pause, whatever a human
types next is unambiguously the reply (the issue's own "how a reply is
disambiguated" question), not a fresh slash command.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rivulets.db.models import Workflow, WorkflowRun


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


async def find_awaiting_workflow_run(db: AsyncSession, rivulet_id: str) -> WorkflowRun | None:
    """The most recent WorkflowRun paused on this rivulet, if any -- a
    rivulet realistically has at most one at a time (each workflow
    trigger runs in the rivulet the human typed it in), but `.desc()` +
    a single row is cheap insurance against that assumption ever being
    violated instead of `.one()` raising on an unexpected second row."""
    return await db.scalar(
        select(WorkflowRun)
        .where(WorkflowRun.rivulet_id == rivulet_id, WorkflowRun.status == "awaiting_human")
        .order_by(WorkflowRun.started_at.desc())
        .limit(1)
    )
