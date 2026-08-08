"""Agent-authored and agent-managed workflow schedules (#93), on top of
#92's WorkflowSchedule/scheduler.py mechanism. "I ask an agent to do this
on a regular basis. Set a reminder, etc." -- today an agent can only kick
off a workflow immediately (run_workflow.py); these three tools give it
create/list/cancel access to the timer-based path too.

Same deliberately side-effect-free shape as handoff.py and
run_workflow.py: none of these functions touch the DB directly (a shared
`@tool` callable has no request-scoped session or caller identity to work
with -- see agentos/tool_resolution.py's _BUILTIN_REGISTRY, the same
Function instances are handed to every agent regardless of who's asking).
The real work -- resolving the calling agent and rivulet, validating the
schedule, writing the row, posting a visible confirmation -- happens in
dispatch/service.py's _handle_schedule_workflow_trigger /
_handle_list_schedules_trigger / _handle_cancel_schedule_trigger, which
inspect the completed run's tool calls the same way it already does for
handoff and run_workflow.
"""

from agno.tools import tool


@tool
def schedule_workflow(
    workflow_name: str,
    input_content: str = "",
    cron_expression: str | None = None,
    fire_at: str | None = None,
    name: str | None = None,
) -> str:
    """Schedule a saved, published workflow to run automatically later,
    instead of running it right now. Provide exactly one of:
    - `cron_expression`: a standard 5-field UTC cron string, for a
      recurring schedule (e.g. "0 9 * * 1-5" for every weekday at 9am UTC).
    - `fire_at`: an ISO 8601 UTC timestamp (e.g. "2026-08-08T21:30:00Z"),
      for a single one-time reminder.
    Resolve a relative ask like "every morning at 8" or "in 20 minutes"
    into one of these two concrete forms yourself before calling this
    tool -- it does not parse natural language. `name` is an optional
    short label (e.g. "daily digest") to make the schedule easy to find
    later via list_schedules/cancel_schedule. New schedules you create
    start disabled and won't actually fire until a human approves them."""
    when = cron_expression or fire_at or "the given time"
    return f"Requested a schedule for workflow {workflow_name!r} ({when}), pending human approval."


@tool
def list_schedules() -> str:
    """List the workflow schedules you (this agent) have created,
    including whether each is still pending human approval, disabled, or
    actively running, and when it will next fire."""
    return "Looking up your scheduled workflows."


@tool
def cancel_schedule(schedule: str) -> str:
    """Cancel one of your own previously-created workflow schedules.
    `schedule` can be the schedule's id or the `name` it was given when
    created. Only schedules you created yourself can be cancelled this
    way."""
    return f"Requested cancellation of schedule {schedule!r}."
