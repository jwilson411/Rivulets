"""Workspace invite management tools (#193): create/list/revoke built-in
tools mirroring the owner-gated subset of api/invites.py's HTTP surface
(POST/GET/DELETE -- POST /invites/accept is deliberately out of scope,
it's how an uninvited human becomes a session in the first place, not a
management action an already-authenticated agent would ever need), so an
agent holding the "invites:manage" scope (#188, agentos/tool_scopes.py)
can invite a second human to the workspace, see outstanding invites, or
revoke one on a user's behalf -- the other half of #193, alongside
tools/builtin/settings.py.

Same deliberately side-effect-free shape as settings.py/channels.py/
agents_teams.py/mcp_servers.py/workflows.py: none of these functions
touch the DB directly -- see agentos/tool_resolution.py's
_BUILTIN_REGISTRY docstring for why a shared `@tool` callable can't have
a request-scoped session. The real work happens in dispatch/service.py's
_handle_create_invite_trigger/_handle_list_invites_trigger/
_handle_revoke_invite_trigger, which inspect the completed run's tool
calls the same way it already does for create_channel/create_workflow/
register_mcp_server.

list_invites carries a required_scope too, unlike every prior sub-issue's
read-only list tool (list_channels, list_agents, list_mcp_servers,
list_workflows) -- api/invites.py's GET route is OwnerGrant-gated same as
its POST/DELETE, and an outstanding invite list is itself sensitive (it
reveals who's been invited and how many uses each link has left). See
tool_scopes.py's BUILTIN_TOOL_SCOPES comment for the full reasoning.

create_invite's tool-call result never carries the raw invite secret --
unlike api/invites.py's create_invite response (shown once, directly to
the owner's own browser), a tool call's return value is read back by the
calling *model*, then likely echoed into a chat message any channel
member can see. Handing the secret to the model would mean it travels
through the model provider's own logging/inference pipeline and lands in
plain sight in the rivulet, defeating the "shown once, to the owner"
property invites are built around (api/invites.py's module docstring).

The confirmation posted into the rivulet (dispatch/service.py's
_handle_create_invite_trigger) is real work that bypasses the model, the
same split every other mutating built-in tool in this library already
follows -- but #241 found that message carrying the raw secret too: a
persisted Message is a synced entity (sync/engine.py's FR-9.6 gossipsub
sync covers messages same as agents/channels/teams), so embedding the
secret there replicated it to every peer and left it sitting in this
rivulet's history for a model to read back on a future turn, undoing the
whole point of keeping it out of the tool result. The confirmation
message now carries only the invite id/display hint/expiry; the one-shot
url instead rides the in-process SSE `system_alert` payload
(streaming.py's publish() -- in-memory, per-process, never persisted or
gossiped), the one channel with the same "nowhere but this one delivery"
shape the tool result already had.
"""

from agno.tools import tool


@tool
def create_invite(
    display_name_hint: str | None = None, max_uses: int = 1, expires_in_hours: int = 168
) -> str:
    """Create an invite link a second human can use to join this
    workspace, without handing over the workspace's own recovery phrase.
    `display_name_hint` is an optional name to suggest to the invited
    human (they can pick their own instead). `max_uses` caps how many
    times the link can be redeemed (default 1, one person). `expires_in_hours`
    caps how long the link stays valid (default 168, one week). The link
    itself is never shown here or in chat -- it's pushed once, live, to
    whoever has this rivulet open at the moment it's created, so let the
    human know to watch for it right now."""
    return "Requested creation of a workspace invite."


@tool
def list_invites() -> str:
    """List the workspace's outstanding and past invites -- who they were
    meant for (if a display name hint was given), how many times each has
    been used out of its max uses, when each expires, and whether it's
    been revoked. Invite secrets themselves are never shown here (they're
    revealed only once, at creation)."""
    return "Looking up the workspace's invites."


@tool
def revoke_invite(invite_id: str) -> str:
    """Permanently revoke an invite so its link can no longer be used to
    join the workspace, even if it hasn't expired or reached its max
    uses yet. `invite_id` is the invite's id (from list_invites). Has no
    effect on a human who already redeemed the invite -- they keep
    whatever session they already have."""
    return f"Requested revocation of invite {invite_id!r}."
