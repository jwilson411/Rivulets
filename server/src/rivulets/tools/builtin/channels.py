"""Channel management tools (#189): create/update/archive/unarchive/
reorder/list built-in tools mirroring api/channels.py's HTTP surface, so
an agent holding the "channels:manage" scope (#188, agentos/
tool_scopes.py) can act on channels the same way a human can through the
API -- one of the more common "act on my behalf" delegations (#125's
tracking issue).

Same deliberately side-effect-free shape as schedules.py/run_workflow.py:
none of these functions touch the DB directly (a shared `@tool` callable
has no request-scoped session -- see agentos/tool_resolution.py's
_BUILTIN_REGISTRY, the same Function instances are handed to every agent
regardless of who's asking). The real work happens in dispatch/
service.py's _handle_create_channel_trigger / _handle_update_channel_trigger
/ _handle_archive_channel_trigger / _handle_unarchive_channel_trigger /
_handle_reorder_channels_trigger / _handle_list_channels_trigger, which
inspect the completed run's tool calls the same way it already does for
handoff/run_workflow/schedule_workflow.

Unlike schedule_workflow, these are not create-then-approve: #188's scope
grant *is* the human approval boundary here (a workspace owner has to
explicitly grant channels:manage before any of the mutating tools even
resolve for an agent -- see tool_resolution.py's resolve_agent_tools), so
once granted, calls take effect immediately, the same as a human's own API
call would. list_channels is read-only and deliberately left unscoped,
mirroring read_file/list_files.
"""

from agno.tools import tool


@tool
def create_channel(name: str, description: str = "") -> str:
    """Create a new channel. `name` must be 3-80 characters and not
    already in use by another (non-archived) channel. `description` is
    optional. Use this when the conversation calls for setting up a new
    place to talk about something, not for renaming or reusing an
    existing channel."""
    return f"Requested creation of channel {name!r}."


@tool
def update_channel(channel: str, name: str | None = None, description: str | None = None) -> str:
    """Rename and/or change the description of an existing channel.
    `channel` is the channel's id or its current name. Only the fields
    you provide are changed; omit `name` or `description` to leave it
    as-is."""
    return f"Requested update to channel {channel!r}."


@tool
def archive_channel(channel: str) -> str:
    """Archive an existing channel (soft delete -- recoverable via
    unarchive_channel, not destroyed). `channel` is the channel's id or
    its current name."""
    return f"Requested archiving of channel {channel!r}."


@tool
def unarchive_channel(channel: str) -> str:
    """Restore a previously archived channel. `channel` is the channel's
    id or the name it had before archiving."""
    return f"Requested unarchiving of channel {channel!r}."


@tool
def reorder_channels(order: list[str]) -> str:
    """Set the display order of channels. `order` is the full list of
    channel ids or names in the desired order -- every channel you want
    to keep, including ones that didn't move, must be included, not just
    the ones that did."""
    return f"Requested reordering of {len(order)} channels."


@tool
def list_channels() -> str:
    """List the workspace's channels, including which are archived, so
    you can find a channel's id/name before acting on it with the other
    channel tools."""
    return "Looking up the workspace's channels."
