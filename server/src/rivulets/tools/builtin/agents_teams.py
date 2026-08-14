"""Agent & team management tools (#190): create/update/delete/routing-
rules/peer-preference/rollback for agents, and create/update/delete for
teams, mirroring api/agents.py's and api/teams.py's HTTP surface so an
agent holding the "agents_teams:manage" scope (#188, agentos/
tool_scopes.py) can act on other agents and teams the same way a human
can through the API -- another slice of #125's "act on my behalf"
tracking issue, alongside #189's channel tools.

Same deliberately side-effect-free shape as channels.py/schedules.py: none
of these functions touch the DB directly -- see agentos/tool_resolution.py's
_BUILTIN_REGISTRY docstring for why a shared `@tool` callable can't have a
request-scoped session. The real work happens in dispatch/service.py's
_handle_create_agent_trigger / _handle_update_agent_trigger / ... /
_handle_delete_team_trigger, which inspect the completed run's tool calls
the same way it already does for handoff/run_workflow/create_channel.

This is the highest-reach category of built-in tool so far (#190's issue
body flags it explicitly): update_agent's tool_ids lets an agent reassign
any agent's tool assignments, including its own or a sibling's. #188's
scope grant is still the human approval boundary -- none of these nine
mutating tools resolve for an agent without an explicit
"agents_teams:manage" grant.

Reassignment alone is NOT always safe to leave ungated, though (#310
corrected an earlier version of this docstring that claimed otherwise): a
builtin tool whose own Tool.required_scope is set only actually works for
whichever agent it ends up assigned to if *that* agent separately holds
the matching AgentToolScope grant, itself an owner-only surface
(api/agents.py's set_agent_tool_scopes, gated by OwnerGrant) with no
built-in tool anywhere in this library that can set it -- so assigning
one of those (or one of the four SENSITIVE_BUILTIN_TOOL_NAMES,
agentos/tool_resolution.py) is inert without a second, still-owner-only
step. Custom and MCP tools have no such second gate: a custom tool's
code runs unsandboxed in the App Server process the moment it's called,
and an MCP tool resolves with the owner's stored headers/env -- for
those, assignment *is* eligibility. dispatch/service.py's
_handle_update_agent_trigger therefore refuses to assign a custom/MCP/
required_scope/SENSITIVE_BUILTIN_TOOL_NAMES tool via this tool's tool_ids
outright (find_unauthorized_tool_assignment, reused from api/agents.py),
the same way it refuses to touch an agent that already holds an
AgentToolScope at all. list_agents/list_teams are read-only and
deliberately left unscoped, mirroring list_channels.
"""

from agno.tools import tool


@tool
def create_agent(
    name: str,
    description: str,
    instructions: str,
    model: str,
    team_ids: list[str] | None = None,
) -> str:
    """Create a new agent. `name` must be 2-64 characters and unique
    across the workspace. `description` (10-500 characters) is what the
    dispatcher uses to decide when to route messages to this agent, so it
    should describe what the agent is for, not just its name.
    `instructions` is the agent's system prompt. `model` is a
    "provider:model_name" string (e.g. "anthropic:claude-3-5-haiku-latest").
    `team_ids` optionally adds the new agent to one or more existing
    teams by id. Tool assignment isn't available here -- use update_agent
    once the agent exists."""
    return f"Requested creation of agent {name!r}."


@tool
def update_agent(
    agent: str,
    name: str | None = None,
    description: str | None = None,
    instructions: str | None = None,
    model: str | None = None,
    tool_ids: list[str] | None = None,
    team_ids: list[str] | None = None,
) -> str:
    """Update an existing agent. `agent` is the agent's id or its current
    name. Only the fields you provide are changed. `tool_ids`/`team_ids`,
    when provided, *replace* the agent's full set of assigned tools/teams
    -- pass every id you want kept, not just the ones that changed; omit
    either to leave it as-is, or pass an empty list to clear it. Assigning
    a tool via `tool_ids` doesn't by itself let the target agent use it if
    that tool requires a capability scope -- scope grants are a separate,
    owner-only step."""
    return f"Requested update to agent {agent!r}."


@tool
def delete_agent(agent: str) -> str:
    """Permanently delete an agent. `agent` is the agent's id or its
    current name. This cannot be undone -- the agent's version history,
    routing rules, and team memberships are all removed with it."""
    return f"Requested deletion of agent {agent!r}."


@tool
def update_agent_routing_rules(agent: str, rules: list[dict[str, object]]) -> str:
    """Replace an agent's dispatcher routing rules. `agent` is the
    agent's id or its current name. `rules` is the full replacement set
    -- each item is an object with `rule_type` (one of "keyword",
    "regex", "semantic", "always", "mention_only"), `pattern` (a JSON
    array of trigger phrases for "keyword"/"semantic", a regex string for
    "regex", or unused for "always"/"mention_only"), and an optional
    `priority` (higher wins when more than one rule matches, default 0).
    This replaces every existing rule for the agent, not just the ones
    you list."""
    return f"Requested routing-rule update for agent {agent!r} ({len(rules)} rule(s))."


@tool
def update_agent_peer_preference(agent: str, capability_tag: str | None = None) -> str:
    """Set or clear which capability tag (e.g. "gpu") an agent should
    preferentially run on when dispatched to a remote peer. `agent` is
    the agent's id or its current name. Pass `capability_tag=None` (the
    default) to clear any existing preference."""
    if capability_tag is None:
        return f"Requested clearing peer preference for agent {agent!r}."
    return f"Requested setting peer preference for agent {agent!r} to {capability_tag!r}."


@tool
def rollback_agent_version(agent: str, version: int) -> str:
    """Revert an agent's instructions and model to a prior version from
    its history. `agent` is the agent's id or its current name. `version`
    is the version number to revert to (see list_agents for an agent's
    current state; version history itself isn't exposed as a tool yet).
    The rollback itself is recorded as a new version, so it can be
    reverted too."""
    return f"Requested rollback of agent {agent!r} to version {version}."


@tool
def create_team(name: str, description: str = "") -> str:
    """Create a new team. Teams group agents together so they can be
    assigned as a unit to a channel. `description` is optional."""
    return f"Requested creation of team {name!r}."


@tool
def update_team(
    team: str,
    name: str | None = None,
    description: str | None = None,
    agent_ids: list[str] | None = None,
) -> str:
    """Update an existing team. `team` is the team's id or its current
    name. Only the fields you provide are changed. `agent_ids`, when
    provided, *replaces* the team's full membership in the given order --
    pass every agent id or name you want kept, not just the ones that
    changed; omit it to leave membership as-is, or pass an empty list to
    remove every member."""
    return f"Requested update to team {team!r}."


@tool
def delete_team(team: str) -> str:
    """Permanently delete a team. `team` is the team's id or its current
    name. Any channel assigned to this team stops having a dispatch
    target until reassigned; this does not delete the team's agents."""
    return f"Requested deletion of team {team!r}."


@tool
def list_agents() -> str:
    """List the workspace's agents, so you can find an agent's id/name
    before acting on it with the other agent tools."""
    return "Looking up the workspace's agents."


@tool
def list_teams() -> str:
    """List the workspace's teams and their members, so you can find a
    team's id/name before acting on it with the other team tools."""
    return "Looking up the workspace's teams."
