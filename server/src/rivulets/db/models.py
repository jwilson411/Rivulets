"""ORM models for all workspace entities.

Every synced entity carries a `vector_clock` column used by the P2P sync
engine for last-write-wins conflict resolution (FR-9.6). Tables noted as
"not synced" in the data model (provider_config, rivulet_guard_state,
sync_state) intentionally omit or ignore that column's sync semantics.
"""

from sqlalchemy import CheckConstraint, ForeignKey, Index, LargeBinary, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from rivulets.db.base import Base, utcnow_iso, uuid7


class Workspace(Base):
    __tablename__ = "workspace"

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    name: Mapped[str] = mapped_column(default="My Workspace")
    key_hash: Mapped[str]
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(default=utcnow_iso)
    vector_clock: Mapped[int] = mapped_column(default=0)


class Human(Base):
    """A per-human display identity within the single shared workspace
    (#14). This is a lightweight session claim, not a credential — the
    workspace mnemonic remains the only thing that's actually
    authenticated (see api/auth.py's POST /auth/identity). display_name is
    intentionally not unique: two offline nodes each bootstrapping the
    same name shouldn't force a sync conflict (sync/apply.py's
    SyncConflict); id is the real identity."""

    __tablename__ = "human"

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    display_name: Mapped[str]
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(default=utcnow_iso)
    vector_clock: Mapped[int] = mapped_column(default=0)


class Invite(Base):
    """A scoped, revocable credential for a second human to join the
    workspace (#15) -- deliberately NOT the mnemonic, and never synced
    (excluded by omission from sync/apply.py's spec table, same FR-9.2
    treatment as ProviderConfig.api_key_ref: only secret_hash is stored,
    never the raw secret). Redemption (api/invites.py's accept_invite)
    only works against the specific node whose HTTP port receives the
    accept request while that node's SessionKeyStore is populated -- an
    invite doesn't grant P2P mesh membership, just a scoped HTTP session
    on whichever node issued it (a deliberately lighter-weight design than
    handing an invited human's device a P2P pre-shared key)."""

    __tablename__ = "invite"

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    secret_hash: Mapped[str]
    display_name_hint: Mapped[str | None] = mapped_column(default=None)
    max_uses: Mapped[int] = mapped_column(default=1)
    use_count: Mapped[int] = mapped_column(default=0)
    expires_at: Mapped[str]
    revoked: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)


class ProviderConfig(Base):
    """LLM provider credentials. NOT synced between nodes (FR-1.5)."""

    __tablename__ = "provider_config"

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    provider: Mapped[str]  # 'openai', 'anthropic', 'deepseek', 'openai_compatible'
    label: Mapped[str]
    api_key_ref: Mapped[str]  # reference to keychain entry, NOT the raw key
    base_url: Mapped[str | None] = mapped_column(default=None)
    is_default: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
    synced: Mapped[bool] = mapped_column(default=False)


class WorkspaceSetting(Base):
    __tablename__ = "workspace_settings"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str]  # JSON-encoded
    updated_at: Mapped[str] = mapped_column(default=utcnow_iso)
    vector_clock: Mapped[int] = mapped_column(default=0)


class Channel(Base):
    __tablename__ = "channel"
    __table_args__ = (
        Index("idx_channel_name", "name", unique=True, sqlite_where=text("archived = 0")),
    )

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    name: Mapped[str]
    description: Mapped[str | None] = mapped_column(default=None)
    team_id: Mapped[str | None] = mapped_column(ForeignKey("team.id"), default=None)
    position: Mapped[int] = mapped_column(default=0)
    archived: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(default=utcnow_iso)
    vector_clock: Mapped[int] = mapped_column(default=0)

    rivulets: Mapped[list["Rivulet"]] = relationship(back_populates="channel")


class Team(Base):
    __tablename__ = "team"

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    name: Mapped[str]
    description: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(default=utcnow_iso)
    vector_clock: Mapped[int] = mapped_column(default=0)


class TeamAgent(Base):
    """Join table: which agents are on which teams."""

    __tablename__ = "team_agent"

    team_id: Mapped[str] = mapped_column(
        ForeignKey("team.id", ondelete="CASCADE"), primary_key=True
    )
    agent_id: Mapped[str] = mapped_column(
        ForeignKey("agent.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(default=0)


class Agent(Base):
    __tablename__ = "agent"
    __table_args__ = (UniqueConstraint("name", name="idx_agent_name"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    name: Mapped[str]
    description: Mapped[str]  # used by dispatcher, 10-500 chars
    instructions: Mapped[str]  # system prompt
    model: Mapped[str]  # 'provider:model_name'
    # Ordered JSON array of 'provider:model_name' strings (#103): tried in
    # order if `model`'s call fails with a retryable-looking error (rate
    # limit, 5xx, timeout). None/empty means no fallback configured, the
    # original single-model behavior. JSON-in-TEXT, same convention as
    # AgentRoutingRule.pattern below -- a normalized child table would be
    # schema churn for no benefit over reusing that pattern for a small
    # ordered list nothing ever queries by element.
    fallback_models: Mapped[str | None] = mapped_column(default=None)
    # #100: one-time human approval that this agent may use its assigned
    # sensitive tools (execute_python/http_request/write_file/
    # query_workspace_db -- see tool_resolution.py's SENSITIVE_BUILTIN_TOOL_
    # NAMES) when invoked *unattended* -- a schedule fire (#92) or a
    # remediation run (#94), where nothing resembling a human is watching
    # the tool call happen live. Doesn't affect ordinary chat/slash-command
    # use of the same tools at all (those already happen with a human
    # plausibly present, same as before this flag existed) -- only gates
    # the specific unattended paths workflows/engine.py's `unattended`
    # threading identifies. False by default: an agent with a sensitive
    # tool assigned is unattended-safe only once a human explicitly opts
    # it in (agents/+page.svelte), not the moment it's created.
    approved_for_unattended_tools: Mapped[bool] = mapped_column(default=False)
    # #107: JSON Schema object (JSON-in-TEXT, same convention as
    # fallback_models above) constraining this agent's reply. None (the
    # default) means free-form text, exactly as every agent behaved before
    # this existed. When set, agentos/service.py's run_agent requests
    # structured output from the model instead of streaming free text --
    # see that module for the streaming/parsing tradeoffs this implies.
    output_schema: Mapped[str | None] = mapped_column(default=None)
    agentos_agent_id: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(default=utcnow_iso)
    vector_clock: Mapped[int] = mapped_column(default=0)

    routing_rules: Mapped[list["AgentRoutingRule"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )


class AgentVersion(Base):
    """History of Agent.instructions/model changes (#104), so an edit that
    turns out worse can be diffed against and reverted -- until this
    existed, editing an agent overwrote instructions/model in place with
    no way to see what it used to say or revert to. Snapshotted by
    api/agents.py's create_agent/update_agent/rollback_agent_version
    whenever either field is set, mirroring ToolVersion: a side effect of
    editing the parent row, not an independently-replicated record (no
    vector_clock here). Deliberately narrow scope, per #104: only
    instructions/model, not routing rules or tool assignments (those can
    be just as behavior-altering, but versioning them is a separate,
    unscoped follow-up). Only records edits made on this node -- an
    instructions/model change that arrives via P2P sync updates the
    Agent row (see sync/apply.py's AGENT_SPEC) but doesn't add a version
    row here; the smaller "history to look at and revert" core the issue
    asked for, not full draft/published gating or cross-peer history
    replication."""

    __tablename__ = "agent_version"
    __table_args__ = (Index("idx_agent_version", "agent_id", "version"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agent.id", ondelete="CASCADE"))
    version: Mapped[int]
    instructions: Mapped[str]
    model: Mapped[str]
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)


class AgentRoutingRule(Base):
    __tablename__ = "agent_routing_rule"

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agent.id", ondelete="CASCADE"))
    rule_type: Mapped[str]  # 'keyword' | 'regex' | 'semantic' | 'always' | 'mention_only'
    pattern: Mapped[str]  # JSON: keywords array, regex string, or trigger phrases
    priority: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)

    agent: Mapped["Agent"] = relationship(back_populates="routing_rules")


class AgentPeerPreference(Base):
    """Issue #10: free-form capability tag this agent should preferentially
    run on (e.g. "gpu"). One row per agent (v1 pins agents, not teams).
    Synced across the workspace, unlike per-node status fields — the node
    that ends up dispatching for a given rivulet isn't necessarily the
    node where this preference was set. See sync/apply.py's
    AGENT_PEER_PREFERENCE_SPEC and dispatch/service.py's
    _resolve_remote_peer for how it's used."""

    __tablename__ = "agent_peer_preference"

    agent_id: Mapped[str] = mapped_column(
        ForeignKey("agent.id", ondelete="CASCADE"), primary_key=True
    )
    capability_tag: Mapped[str]
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(default=utcnow_iso)
    vector_clock: Mapped[int] = mapped_column(default=0)


class AgentRun(Base):
    """One agent invocation's token/cost/status accounting (FR-3.5), and the
    source data for the workspace-level usage dashboard (#28). Not synced —
    local telemetry, not user content; a fresh peer doesn't need another
    node's run history to function.

    `source` records dispatcher-side LLM calls (#246: llm_fallback.py's
    routing decision, complexity_classifier.py's tier classification) in
    this same table as `"dispatcher_call"` instead of the default
    `"agent_run"`, so that spend is no longer invisible to the usage
    dashboard and budget caps (#31's dispatcher hit-rate tracking can use
    the same rows later).

    `agent_id` is nullable for exactly one case: llm_fallback.py's routing
    call runs before any agent has been matched, evaluating a whole team's
    roster at once, so there's no single agent to attribute it to. Every
    other AgentRun (including complexity_classifier.py's dispatcher_call
    rows, which classify a specific agent's next reply) keeps a real
    agent_id. A null-agent_id row only shows up in workspace-scope budget
    caps (dispatch/budgets.py's compute_spend) and workspace usage totals
    (api/usage.py) — agent/team-scope caps can't attribute it to anyone
    more specific, which is an accepted v1 gap the same as BudgetCap's
    documented no-cross-peer-aggregation limitation.
    """

    __tablename__ = "agent_run"
    __table_args__ = (Index("idx_agent_run_agent", "agent_id", "created_at"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent.id", ondelete="CASCADE"), default=None
    )
    source: Mapped[str] = mapped_column(default="agent_run")  # 'agent_run' | 'dispatcher_call'
    model: Mapped[str]  # 'provider:model_name' — the concrete model that actually ran
    # Set only when a fallback chain (#103) served this run instead of the
    # originally-requested model -- `model` above then holds the model
    # that actually answered, and this holds the one that was asked for
    # first but failed. None on every run that didn't fall back, so a
    # normal run's accounting looks exactly like it did before #103.
    requested_model: Mapped[str | None] = mapped_column(default=None)
    tier: Mapped[str | None] = mapped_column(default=None)  # 'cheap'|'capable'|None (fixed model)
    status: Mapped[str]  # 'completed' | 'error'
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    total_tokens: Mapped[int] = mapped_column(default=0)
    # None when `model` isn't in pricing.py's static table — an unpriced
    # model's tokens still count toward totals, its cost just can't be
    # estimated (see api/usage.py's `cost_incomplete` flag).
    cost_usd: Mapped[float | None] = mapped_column(default=None)
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)


class ToolCallLog(Base):
    """One row per tool call an agent actually made (#100) -- until this
    table existed, `agentos/tool_resolution.py` resolved an agent's tools
    with no record of what was actually invoked, with what arguments, or
    whether it succeeded, beyond a `logger.warning` for a tool that failed
    to *resolve* (not one that ran and errored). Populated by
    `agentos/tool_audit.py`'s `log_tool_calls`, called from the same
    `record_agent_run` choke point every `run_agent` caller already goes
    through (agentos/accounting.py's docstring) -- so this covers ordinary
    chat tool use and workflow agent-node tool use uniformly, not just the
    unattended paths `sensitive`/gating below cares about.

    `sensitive` is denormalized off `Tool.sensitive` *at call time* rather
    than joined live -- a tool's sensitivity tag changing later shouldn't
    rewrite history for calls made under the old tag. `agent_run_id` is
    nullable because a tool call is logged the same way regardless of
    whether the run that made it went on to complete or error (AgentRun's
    own `status` already carries the run-level outcome); this row's own
    `status` is the tool call's outcome specifically, which can differ (a
    tool can fail without the overall run failing, if the agent recovers).

    `arguments_json`/`result_summary` are truncated (see tool_audit.py) --
    this is an audit trail for "what ran, with roughly what, and did it
    work", not a byte-exact replay log; a tool that returns megabytes of
    data (a large file read) shouldn't make this table unbounded.

    Not synced, same reasoning as AgentRun: local execution telemetry tied
    to whichever node actually ran the tool, not shared workspace content."""

    __tablename__ = "tool_call_log"
    __table_args__ = (Index("idx_tool_call_log_agent", "agent_id", "created_at"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agent.id", ondelete="CASCADE"))
    agent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_run.id", ondelete="CASCADE"), default=None
    )
    tool_name: Mapped[str]
    sensitive: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str]  # 'success' | 'error'
    arguments_json: Mapped[str | None] = mapped_column(default=None)  # truncated JSON
    result_summary: Mapped[str | None] = mapped_column(default=None)  # truncated
    duration_ms: Mapped[int | None] = mapped_column(default=None)
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)


class DispatchDecision(Base):
    """One row per dispatch/engine.py `dispatch()` call's outcome — the raw
    data behind R-4's dispatcher hit-rate metric and fallback-rate cost
    warning (#31). Deliberately separate from AgentRun: most decisions
    (mention/deterministic matches, and LLM-fallback calls that matched zero
    agents) carry no token spend, so folding this into AgentRun's token/cost
    accounting would misrepresent both tables. Not synced — local telemetry,
    not user content, same reasoning as AgentRun.
    """

    __tablename__ = "dispatch_decision"
    __table_args__ = (Index("idx_dispatch_decision_created", "created_at"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    method: Mapped[str]  # 'mention' | 'deterministic' | 'llm' | 'none'
    # True iff the LLM fallback callable was actually invoked (stage 3 ran),
    # regardless of whether it matched an agent — that's the event that
    # costs money, which `method == 'none'` alone can't distinguish (it also
    # covers "no fallback configured at all").
    llm_invoked: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)


class AgentTool(Base):
    """Join table: which tools are assigned to which agents."""

    __tablename__ = "agent_tool"

    agent_id: Mapped[str] = mapped_column(
        ForeignKey("agent.id", ondelete="CASCADE"), primary_key=True
    )
    tool_id: Mapped[str] = mapped_column(
        ForeignKey("tool.id", ondelete="CASCADE"), primary_key=True
    )


class AgentToolScope(Base):
    """Per-agent grant of a capability scope (#188) -- independent of
    which tools are assigned via AgentTool above. A tool whose
    Tool.required_scope is set only resolves for an agent that holds a
    matching row here; see tool_resolution.py's resolve_agent_tools.

    Granted by a workspace owner only (api/agents.py's
    set_agent_tool_scopes, gated by OwnerGrant) -- an agent shouldn't be
    able to expand its own reach, and neither should an invited session,
    so this is a separate owner-only surface rather than folded into
    agent_tool's existing update path (open to any valid session, same as
    the rest of api/agents.py).

    Not P2P-synced, same as AgentTool itself (sync/apply.py's AGENT_SPEC
    doesn't cover either join table) -- local-node authorization state,
    not shared workspace content."""

    __tablename__ = "agent_tool_scope"

    agent_id: Mapped[str] = mapped_column(
        ForeignKey("agent.id", ondelete="CASCADE"), primary_key=True
    )
    scope: Mapped[str] = mapped_column(primary_key=True)
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)


class Tool(Base):
    __tablename__ = "tool"

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    name: Mapped[str]
    description: Mapped[str]
    tool_type: Mapped[str]  # 'builtin' | 'custom' | 'mcp'
    # #100: real blast radius (arbitrary code exec, arbitrary outbound
    # HTTP, local filesystem writes, DB access) -- gates unattended use
    # via Agent.approved_for_unattended_tools; doesn't restrict attended
    # (chat/slash-command) use at all. Seeded True for the four builtin
    # tools tool_resolution.py's SENSITIVE_BUILTIN_TOOL_NAMES names;
    # False for every other builtin tool and, for now, every custom/mcp
    # tool -- v1 scope is the fixed builtin set called out in #100, not a
    # UI for marking an arbitrary custom/mcp tool sensitive yet.
    sensitive: Mapped[bool] = mapped_column(default=False)
    # #188: capability scope (agentos/tool_scopes.py's TOOL_SCOPES) an
    # agent must be granted, via AgentToolScope below, before this tool
    # resolves for it -- see tool_resolution.py's resolve_agent_tools.
    # None (every tool today) means no scope is required, the same
    # unconditional "assigned -> usable" behavior that existed before
    # #188. Independent of `sensitive` above: that axis gates unattended
    # use of an already-invocable tool; this one gates invocability at
    # all, attended or not.
    required_scope: Mapped[str | None] = mapped_column(default=None)
    source_path: Mapped[str | None] = mapped_column(default=None)
    mcp_server_id: Mapped[str | None] = mapped_column(ForeignKey("mcp_server.id"), default=None)
    mcp_tool_name: Mapped[str | None] = mapped_column(default=None)
    mcp_input_schema_json: Mapped[str | None] = mapped_column(default=None)  # JSON
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(default=utcnow_iso)
    vector_clock: Mapped[int] = mapped_column(default=0)


class ToolVersion(Base):
    __tablename__ = "tool_version"
    __table_args__ = (Index("idx_tool_version", "tool_id", "version"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    tool_id: Mapped[str] = mapped_column(ForeignKey("tool.id", ondelete="CASCADE"))
    version: Mapped[int]
    source_code: Mapped[str]
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)


class MCPServer(Base):
    """`header_names_json` (#124) is a JSON list of configured HTTP header
    *names* only (e.g. `["Authorization"]`) -- the header values are
    secrets and live in the OS keychain (security/credentials.py) under
    `agentos.mcp.mcp_header_ref(id)`, same as ProviderConfig.api_key_ref
    never puts a raw key in this database. Per-node like `connected`/
    `last_connected_at` (module docstring on api/mcp_servers.py) -- not
    in MCP_SERVER_SPEC's synced fields, so each node configures its own
    auth for a given server rather than a secret propagating over sync.

    `transport` (#187): "streamable-http" (the original, still the
    default) or "stdio" -- a locally-spawned subprocess Rivulets itself
    execs, per the `mcp` SDK's stdio transport. `url`/`header_names_json`
    apply only to the former; `command`/`args_json`/`env_names_json` only
    to the latter -- api/mcp_servers.py's MCPServerCreate validates the
    pairing so a row never carries a mix. `env_names_json` mirrors
    `header_names_json` exactly: names only here, values in the keychain
    under `agentos.mcp.mcp_env_ref(id)` -- same "never a raw secret in
    this database" guarantee, just for a subprocess's environment instead
    of HTTP headers.

    `command`/`args_json` are synced (MCP_SERVER_SPEC), same as `url`:
    they describe *what to run*, not a secret, and each node spawns its
    own local subprocess from that description the same way each node
    makes its own outbound connection to a synced `url` -- neither one
    propagates a live connection or process across nodes, just the
    config to start one. `env_names_json` stays unsynced like
    `header_names_json`, for the same reason."""

    __tablename__ = "mcp_server"

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    name: Mapped[str]
    transport: Mapped[str] = mapped_column(default="streamable-http")
    url: Mapped[str | None] = mapped_column(default=None)
    command: Mapped[str | None] = mapped_column(default=None)
    args_json: Mapped[str | None] = mapped_column(default=None)
    env_names_json: Mapped[str | None] = mapped_column(default=None)
    connected: Mapped[bool] = mapped_column(default=False)
    last_connected_at: Mapped[str | None] = mapped_column(default=None)
    header_names_json: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
    vector_clock: Mapped[int] = mapped_column(default=0)


class Workflow(Base):
    """A saved, reusable, node-based definition of how work flows between
    agents and built-in utility nodes (#24). `name` doubles as the
    triggering slash command (`/​{name} <input>` in a channel, api/
    rivulets.py's slash-command interceptor) -- kept as one field rather
    than a separate `slash_command` column since the issue's proposal
    never distinguishes them and a second, possibly-diverging name would
    just be a sync hazard for no benefit. Synced like Agent/Team: a
    workflow definition is shared workspace content, not per-node state.

    `published` (#84): a new workflow starts unpublished, and
    workflows/trigger.py's find_workflow_by_name -- the lookup behind both
    the slash-command trigger and the run_workflow tool -- only matches
    published ones, so a workflow still being built in the canvas (#80)
    can't be accidentally triggered by a stray `/{name}` message. This is
    deliberately a single boolean gate on the *live* nodes/connections,
    not a second, independent copy of the graph: publishing doesn't
    snapshot anything, and editing an already-published workflow still
    takes effect immediately for the next trigger, same as before this
    column existed. (What *does* get frozen at trigger time is a specific
    WorkflowRun's own execution -- see WorkflowRun.graph_snapshot_json --
    which is what actually protects an in-flight run from a concurrent
    edit; `published` only gates *starting* a new one.) api/workflows.py's
    publish endpoint refuses if the workflow has no entry connection, the
    same "can this even run" check the engine itself makes at trigger
    time (workflows/engine.py's "Workflow has no entry point" failure) --
    published is meant to mean "ready", not just "flagged".

    Not required for a node_type='workflow' step (#85) to *reference* this
    workflow as a nested child at save time -- that's a structural choice
    made by whoever built the parent, not an external trigger a stray
    message could hit by accident. Actually *running* that nested step
    does require it, though (#249): `workflows/engine.py`'s
    `_execute_workflow_node` re-checks `published` immediately before
    invoking the child, the same gate `find_workflow_by_name` applies to
    every other trigger path. A synced peer that's only received this
    workflow's nodes/connections (not yet this flag -- see sync/apply.py's
    `WORKFLOW_SPEC`, which now syncs `published` for exactly this reason)
    therefore can't nest-run a graph its own copy still considers a draft,
    even though the node/connection rows are already fully present.

    `on_failure_workflow_id` (#94 layer 2): an optional remediation
    workflow, invoked automatically (workflows/engine.py's
    `_maybe_trigger_remediation`) whenever a run of *this* workflow
    finishes `failed`, with the failing run's input/error as its input --
    the same "one workflow invoking another" plumbing #85's
    node_type='workflow' step already established
    (`_execute_workflow_node`), just triggered by a run's finalize
    instead of a graph node. Unlike `child_workflow_id`, a self-reference
    here is left unrejected rather than validated against at save time:
    "on failure, retry this same workflow once" is a legitimate
    remediation shape, not a mistake, and it's safe without that
    validation because `WorkflowRun.triggered_by == 'remediation'` never
    triggers further remediation (see that method's docstring) -- a
    structural, depth-1 cap that doesn't need the reference itself
    forbidden. Not required to be `published` for the same reason
    `child_workflow_id` isn't: a deliberate structural reference, not
    something a stray message could hit by accident.

    `on_call_agent_id` (#94 layer 3): an optional agent to `@mention`
    automatically (workflows/engine.py's `_maybe_notify_on_call_agent`)
    whenever a run of this workflow fails -- independently configurable
    alongside `on_failure_workflow_id`, not a replacement for it: a fixed
    remediation workflow and an agent narrating/investigating aren't
    really in tension, so both fire if both are set. When null, falls
    back to the workspace-wide 'workflows.default_on_call_agent_id'
    setting (api/settings.py) -- this field is only the per-workflow
    override, not the sole place an on-call agent can be configured.
    ondelete='SET NULL' -- same "survives as a now-misconfigured
    reference rather than disappearing" treatment `agent_id` and
    `child_workflow_id` already get elsewhere in this file."""

    __tablename__ = "workflow"
    __table_args__ = (UniqueConstraint("name", name="idx_workflow_name"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    name: Mapped[str]
    description: Mapped[str | None] = mapped_column(default=None)
    published: Mapped[bool] = mapped_column(default=False)
    on_failure_workflow_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow.id", ondelete="SET NULL"), default=None
    )
    on_call_agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent.id", ondelete="SET NULL"), default=None
    )
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(default=utcnow_iso)
    vector_clock: Mapped[int] = mapped_column(default=0)

    nodes: Mapped[list["WorkflowNode"]] = relationship(
        back_populates="workflow",
        cascade="all, delete-orphan",
        # WorkflowNode has two FKs to workflow.id (workflow_id and, since
        # #85, child_workflow_id) -- without this, SQLAlchemy can't infer
        # which one defines "this workflow's own nodes".
        foreign_keys="WorkflowNode.workflow_id",
    )
    connections: Mapped[list["WorkflowConnection"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan"
    )


class WorkflowNode(Base):
    """One step in a workflow (#24): either an existing `Agent` (node_type
    'agent', `agent_id` set) or a built-in utility node ('summarize' |
    'transform' | 'conditional' | 'merge' | 'human_input' |'workflow',
    workflows/nodes.py). `agent_id` uses ondelete='SET NULL' rather than
    CASCADE or the FK-less "looser association" pattern (Channel.team_id)
    -- deleting an agent that a workflow references shouldn't be blocked
    by that reference (unlike TeamAgent, which really is meaningless
    without its agent), but the node itself should survive as a
    now-misconfigured step an owner can fix, not silently disappear along
    with its parent workflow. Ordering/flow between nodes lives entirely
    in `WorkflowConnection`, not on this row, so branching later doesn't
    need a schema change here (only a change to how many outbound
    connections the engine follows).

    `child_workflow_id` (#85): the workflow node_type='workflow' invokes
    as a step -- same ondelete='SET NULL' reasoning as `agent_id`, a
    dedicated FK column rather than an id buried in `config_json` so it
    gets the same referential-integrity and "survives a deletion as a
    now-misconfigured step" treatment `agent_id` already has, not a
    second, inconsistent way of referencing something. Cycle prevention
    (workflow A embeds B embeds A) isn't enforced by the schema -- it's a
    runtime check in workflows/engine.py's `_execute_workflow_node`
    against the chain of workflow ids currently executing, the same
    "structural, but checked live rather than validated at save time"
    tradeoff Workflow's own docstring already made for the entry-point/
    outbound-connection invariants."""

    __tablename__ = "workflow_node"

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"))
    name: Mapped[str]
    # 'agent' | 'summarize' | 'transform' | 'conditional' | 'merge' | 'human_input' | 'workflow'
    node_type: Mapped[str]
    agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent.id", ondelete="SET NULL"), default=None
    )
    child_workflow_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow.id", ondelete="SET NULL"), default=None
    )
    config_json: Mapped[str | None] = mapped_column(default=None)  # JSON, node-type-specific
    retry_max_attempts: Mapped[int] = mapped_column(default=0)
    retry_backoff_seconds: Mapped[int] = mapped_column(default=5)
    # Canvas coordinates (#194). Nullable rather than defaulting to (0, 0)
    # so "never positioned" (every node saved before the canvas existed,
    # or created without an explicit position) stays distinguishable from
    # "deliberately placed at the origin" -- the API layer's auto-layout
    # fallback (workflows/layout.py) only fills in nodes where this is
    # still None, so it never clobbers a real 0.0 a user actually dragged
    # to. Two typed floats rather than a layout_json blob per the
    # fallback_models comment above: fixed two-field shape, not variable,
    # so JSON-in-TEXT would just be schema churn without the benefit that
    # pattern buys for genuinely variable-shape data.
    position_x: Mapped[float | None] = mapped_column(default=None)
    position_y: Mapped[float | None] = mapped_column(default=None)
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(default=utcnow_iso)
    vector_clock: Mapped[int] = mapped_column(default=0)

    workflow: Mapped["Workflow"] = relationship(back_populates="nodes", foreign_keys=[workflow_id])


class WorkflowConnection(Base):
    """An edge in a workflow's node graph (#24): `from_node_id` NULL marks
    the workflow's entry point (the first node run); otherwise it's the
    node whose output feeds `to_node_id`'s input. The MVP engine
    (workflows/engine.py) executes a single linear chain and the API layer
    (api/workflows.py) enforces at most one outbound connection per
    from_node_id (including the NULL entry point) to keep that true --
    but the table itself places no such limit, so a future branching
    engine can allow multiple outbound edges (picked via `condition_json`)
    without a schema migration, just a relaxed API validation rule and a
    smarter engine walk."""

    __tablename__ = "workflow_connection"

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"))
    from_node_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_node.id", ondelete="CASCADE"), default=None
    )
    to_node_id: Mapped[str] = mapped_column(ForeignKey("workflow_node.id", ondelete="CASCADE"))
    condition_json: Mapped[str | None] = mapped_column(default=None)  # reserved for branching
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
    vector_clock: Mapped[int] = mapped_column(default=0)

    workflow: Mapped["Workflow"] = relationship(back_populates="connections")


class WorkflowRun(Base):
    """One end-to-end execution of a Workflow (#24), triggered by a slash
    command or the `run_workflow` tool. Not synced -- like AgentRun/
    DispatchDecision, this is local execution telemetry/state tied to
    whichever node happened to run it, not shared workspace content; a
    fresh peer doesn't need another node's workflow-run history to
    function, and re-running the *definition* is all sync needs to carry.

    status='awaiting_human' (#83): a 'human_input' node paused this run --
    `current_node_id` is that paused node, and the run resumes
    (workflows/engine.py's `resume_workflow`) from the next human message
    posted to `rivulet_id` (api/rivulets.py's post_message, via
    workflows/trigger.py's find_awaiting_workflow_run), which becomes that
    node's output. Rivulet.status is set to 'paused' alongside this, the
    same surfacing dispatch/guards.py's loop-guard pause already uses.

    `graph_snapshot_json` (#84): the workflow's nodes/connections, as they
    existed the moment this run started, serialized so a *later* resume
    doesn't re-read the live (possibly since-edited) WorkflowNode/
    WorkflowConnection rows -- without this, a run paused on a
    'human_input' node and resumed after the workflow was edited in the
    builder meanwhile would silently execute a different graph than the
    one that paused it. A run that never crosses a pause boundary doesn't
    strictly need this (workflows/engine.py already loads the graph once
    per run_workflow call and holds it in memory for that call's
    duration), but every run gets one uniformly rather than only runs that
    turn out to pause -- simpler than a null column meaning "never
    snapshotted" that engine.py would need to branch on.

    triggered_by='workflow' (#85): this run is a nested invocation from a
    node_type='workflow' step in *another* WorkflowRun, whose id is
    `triggered_by_id` (a WorkflowRun id here, unlike the human_id/agent_id
    triggered_by_id already holds for 'human'/'agent'). `final_output`
    (#85) is what that parent node's own output becomes: the workflow
    engine has never persisted a single "this run's result" anywhere --
    each node's output only ever existed as a transient local value passed
    to the next one -- so nesting needed *something* queryable a parent
    can read back once its child run finishes. Set only when a run
    actually completes (workflows/engine.py's `_finalize_run`); stays
    None for a failed or still-paused run. Run history intentionally
    stays flat and per-workflow rather than a cross-workflow tree (see
    engine.py's module docstring) -- a nested child's own runs only show
    up under *its* workflow's run history, not folded into the parent's;
    `triggered_by`/`triggered_by_id` are what let a human trace one back
    to the other.

    triggered_by='schedule' (#92): this run was started by
    workflows/scheduler.py's poll loop firing a WorkflowSchedule, not a
    human or agent action -- `triggered_by_id` is that schedule's id, the
    same "id of the thing that caused this" convention every other
    triggered_by value already follows.

    triggered_by='remediation' (#94 layer 2): this run was started
    automatically by `workflows/engine.py`'s `_maybe_trigger_remediation`
    because a *different* WorkflowRun -- `triggered_by_id` here -- just
    finished `failed` and its workflow had `Workflow.on_failure_workflow_id`
    set. The one triggered_by value that also feeds back into triggering
    logic itself: a run whose own triggered_by is already 'remediation'
    is never eligible to trigger further remediation, the depth-1 cap
    that keeps a remediation workflow referencing (directly or via a
    cycle) the workflow it's remediating from ping-ponging forever.

    triggered_by='webhook' (#99): this run was started by an external
    system's HMAC-signed POST to api/webhooks.py's trigger endpoint,
    verified against a WorkflowWebhook -- `triggered_by_id` is that
    webhook's id, same convention as 'schedule'. Unattended for the same
    reason 'schedule' is: nothing resembling a human is watching an
    inbound HTTP call happen live.

    `unattended` (#100): True iff nothing resembling a human was watching
    this run happen live -- derived once at creation from `triggered_by`
    ('schedule'/'remediation'/'webhook' are unattended; everything else --
    'human', 'agent' (a live chat's own run_workflow tool call), 'workflow'
    (a nested run, which inherits its *parent* run's unattended-ness
    rather than being derived from the literal string 'workflow'), 'eval'
    -- is not) by `run_workflow`'s own default-derivation logic, unless a
    nested invocation passes it through explicitly. Persisted (not just
    held in the in-memory `_RunContext`) so `resume_workflow` can restore
    it after a pause/resume boundary, which starts a fresh `_RunContext`.
    Read by `workflows/nodes.py`'s `execute_agent_node` to gate an 'agent'
    node whose assigned agent has an unapproved sensitive tool -- see
    `Agent.approved_for_unattended_tools`.

    `visit_counts_json`/`total_steps` (#249): the same "persisted so a
    pause/resume boundary doesn't lose it" treatment `unattended` already
    gets, for workflows/engine.py's loop guard (`MAX_NODE_VISITS_PER_RUN`/
    `MAX_TOTAL_STEPS_PER_RUN`). Before this, `resume_workflow` rebuilt a
    fresh `_RunContext` on every resume, so a looping graph that paused
    once per iteration (a 'human_input' node inside the loop) reset its
    guard counters at every pause and could run past either cap without
    ever tripping it. `_pause_for_human_input` writes the in-flight
    `_RunContext`'s counters here (alongside `current_node_id`/`status`)
    every time a run pauses, and `resume_workflow` seeds its new
    `_RunContext` from these columns instead of starting at zero. A run
    that never pauses never touches these -- the loop guard's usual
    in-memory-only path (module docstring's "Loops" section) is unchanged
    for the common case."""

    __tablename__ = "workflow_run"
    __table_args__ = (Index("idx_workflow_run_workflow", "workflow_id", "started_at"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"))
    rivulet_id: Mapped[str] = mapped_column(ForeignKey("rivulet.id", ondelete="CASCADE"))
    triggered_by: Mapped[str]  # 'human' | 'agent' | 'workflow' | 'schedule' | 'webhook'
    # human_id / agent_id / (for 'workflow') parent WorkflowRun's id /
    # (for 'schedule') the firing WorkflowSchedule's id / (for 'webhook')
    # the firing WorkflowWebhook's id
    triggered_by_id: Mapped[str | None] = mapped_column(default=None)
    unattended: Mapped[bool] = mapped_column(default=False)
    input_content: Mapped[str]
    # 'running' | 'completed' | 'failed' | 'awaiting_human'
    status: Mapped[str] = mapped_column(default="running")
    current_node_id: Mapped[str | None] = mapped_column(default=None)
    error_message: Mapped[str | None] = mapped_column(default=None)
    final_output: Mapped[str | None] = mapped_column(default=None)
    graph_snapshot_json: Mapped[str] = mapped_column(default="{}")
    # #249: loop-guard state, persisted across a pause/resume boundary --
    # see this class's own docstring.
    visit_counts_json: Mapped[str] = mapped_column(default="{}")
    total_steps: Mapped[int] = mapped_column(default=0)
    started_at: Mapped[str] = mapped_column(default=utcnow_iso)
    completed_at: Mapped[str | None] = mapped_column(default=None)


class WorkflowNodeRun(Base):
    """One node's execution within a WorkflowRun (#24), including retries
    (WorkflowNode.retry_max_attempts) -- one row per attempt, not one row
    updated in place, so a node's retry history stays inspectable after
    the fact. Not synced, same reasoning as WorkflowRun."""

    __tablename__ = "workflow_node_run"
    __table_args__ = (Index("idx_workflow_node_run_run", "workflow_run_id", "started_at"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    workflow_run_id: Mapped[str] = mapped_column(ForeignKey("workflow_run.id", ondelete="CASCADE"))
    node_id: Mapped[str] = mapped_column(ForeignKey("workflow_node.id", ondelete="CASCADE"))
    attempt: Mapped[int] = mapped_column(default=1)
    # running|completed|failed|skipped|awaiting_human (#83: a paused
    # 'human_input' node -- updated to completed with the human's reply
    # as output_content once resume_workflow runs, not a new row)
    status: Mapped[str] = mapped_column(default="running")
    input_content: Mapped[str]
    output_content: Mapped[str | None] = mapped_column(default=None)
    error_message: Mapped[str | None] = mapped_column(default=None)
    started_at: Mapped[str] = mapped_column(default=utcnow_iso)
    completed_at: Mapped[str | None] = mapped_column(default=None)


class WorkflowSchedule(Base):
    """Cron-based automatic triggering of a published workflow (#92) --
    fully local/unsynced, unlike Workflow/WorkflowNode/WorkflowConnection:
    each peer configures its own firing schedule independently (no
    vector_clock, absent from sync/apply.py's _DISPATCH, same treatment
    as WorkflowRun/WorkflowNodeRun above). Nothing here syncs, so there's
    no cross-peer duplicate-fire risk to guard against.

    `cron_expression` is a raw 5-field cron string interpreted in UTC,
    same as every other timestamp in this schema (db/base.py's
    utcnow_iso), not the host machine's local timezone.

    `next_fire_at` is precomputed (workflows/scheduler.py's
    compute_next_fire_at) rather than derived from cron_expression on
    every poll tick, keeping the due-schedule query a plain indexed
    `next_fire_at <= now` scan. It is *always* recomputed from wall-clock
    now (never from the prior slot), which is what makes "skip missed
    fires, never backfill" fall out automatically rather than being
    logic the poll loop has to get right.

    `consecutive_failures` mirrors engine.py's MAX_NODE_VISITS_PER_RUN /
    MAX_TOTAL_STEPS_PER_RUN runaway-execution protection, but at the
    schedule level: workflows/scheduler.py resets it to 0 on any
    non-failed fire and clears `enabled` once it hits
    MAX_CONSECUTIVE_FAILURES, so a permanently broken workflow can't
    retry forever every poll interval.

    `run_once` (#93) distinguishes a single fire-at-a-specific-time
    reminder from a recurring cron schedule -- `cron_expression` is
    nullable specifically to accommodate this: a one-off schedule has
    `next_fire_at` set directly (from the agent- or human-resolved
    timestamp) and no cron expression to recompute from, so
    workflows/scheduler.py's _fire disables it after firing instead of
    calling compute_next_fire_at.

    `created_by` (#93) is 'human' for anything made through the builder
    UI/REST API, or an agent's id for a schedule an agent created via the
    schedule_workflow tool (tools/builtin/schedules.py) -- same
    'human'-or-id convention Rivulet.created_by already uses below. It's
    the ownership boundary list_schedules/cancel_schedule enforce: an
    agent can only see and cancel schedules it created itself, never
    another agent's or a human's. Agent-created schedules also always
    start with `enabled=False` regardless of what the tool call asked for
    (dispatch/service.py's _handle_schedule_workflow_trigger forces it) --
    #84's draft/published gate is the direct precedent for "an agent's
    unilateral creation doesn't take effect until a human approves it",
    reused here via the `enabled` flag rather than inventing a parallel
    approval mechanism."""

    __tablename__ = "workflow_schedule"
    __table_args__ = (
        Index("idx_workflow_schedule_workflow", "workflow_id"),
        Index("idx_workflow_schedule_due", "enabled", "next_fire_at"),
    )

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"))
    channel_id: Mapped[str] = mapped_column(ForeignKey("channel.id", ondelete="CASCADE"))
    cron_expression: Mapped[str | None] = mapped_column(default=None)
    run_once: Mapped[bool] = mapped_column(default=False)
    input_content: Mapped[str] = mapped_column(default="")
    enabled: Mapped[bool] = mapped_column(default=True)
    next_fire_at: Mapped[str]
    last_fired_at: Mapped[str | None] = mapped_column(default=None)
    consecutive_failures: Mapped[int] = mapped_column(default=0)
    name: Mapped[str | None] = mapped_column(default=None)
    created_by: Mapped[str] = mapped_column(default="human")  # 'human' or agent_id
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(default=utcnow_iso)


class WorkflowWebhook(Base):
    """Inbound HTTP trigger for a published workflow (#99): an external
    system POSTs to api/webhooks.py's trigger endpoint, HMAC-signed with
    this row's secret (security/webhook_signing.py), and that endpoint
    fires the workflow via workflows/webhook.py.

    Deliberately unsynced, same treatment and reasoning as
    WorkflowSchedule above (no vector_clock, absent from
    sync/apply.py's _DISPATCH): the URL this row's id appears in is only
    ever reachable through whichever specific peer's HTTP port a human
    has exposed to the external sender in the first place, so no other
    peer could ever receive this webhook's POST -- there's nothing for a
    synced copy of this row to do.

    `secret_nonce`/`secret_ciphertext` hold the HMAC signing secret
    encrypted with a workspace-key-derived key (security/keys.py's
    derive_webhook_secret_key + security/webhook_secret_store.py),
    recoverable rather than bcrypt-hashed like Invite.secret_hash --
    verifying an inbound signature means recomputing the HMAC with the
    actual secret, not just an equality check. Like an invite's secret,
    the plaintext is returned to the human exactly once, at creation
    (api/workflows.py's create_webhook response), and never again.

    `input_template` reuses the exact "{input}" substitution convention a
    `transform` node's config already establishes (workflows/nodes.py's
    execute_transform_node) rather than inventing a second mechanism --
    None/empty passes the raw request body through as input_content
    unchanged; set, it's plain str.replace (not str.format), so a JSON
    payload's own brace characters can't break template rendering."""

    __tablename__ = "workflow_webhook"
    __table_args__ = (Index("idx_workflow_webhook_workflow", "workflow_id"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"))
    channel_id: Mapped[str] = mapped_column(ForeignKey("channel.id", ondelete="CASCADE"))
    secret_nonce: Mapped[bytes] = mapped_column(LargeBinary)
    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    input_template: Mapped[str | None] = mapped_column(default=None)
    enabled: Mapped[bool] = mapped_column(default=True)
    last_triggered_at: Mapped[str | None] = mapped_column(default=None)
    name: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(default=utcnow_iso)


class EvalSuite(Base):
    """A named regression-test suite (#95) attached to exactly one Agent or
    Workflow -- `ck_eval_suite_single_subject` enforces exactly one of
    `agent_id`/`workflow_id` is set (re-checked at the API layer too, since
    a CHECK violation is a worse error message than a 400). Synced like
    Agent/Workflow: a suite's *definition* (this row and its EvalCase
    children) is shared workspace content -- EvalRun/EvalCaseResult
    (execution history) are local-only instead, exactly the same
    Workflow/WorkflowRun split.

    Both subject FKs use ondelete='CASCADE', unlike WorkflowNode.agent_id's
    SET NULL: a suite testing a subject that no longer exists has nothing
    left to test, so it's deleted along with its subject rather than kept
    around as a dangling, now-meaningless reference."""

    __tablename__ = "eval_suite"
    __table_args__ = (
        CheckConstraint(
            "(agent_id IS NOT NULL) != (workflow_id IS NOT NULL)",
            name="ck_eval_suite_single_subject",
        ),
    )

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    name: Mapped[str]
    description: Mapped[str | None] = mapped_column(default=None)
    agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent.id", ondelete="CASCADE"), default=None
    )
    workflow_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow.id", ondelete="CASCADE"), default=None
    )
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(default=utcnow_iso)
    vector_clock: Mapped[int] = mapped_column(default=0)

    cases: Mapped[list["EvalCase"]] = relationship(
        back_populates="suite", cascade="all, delete-orphan"
    )


class EvalCase(Base):
    """One test case in an EvalSuite (#95): an input plus a judge_type-
    specific way to score the subject's output. Synced with its parent
    suite.

    Field usage by `judge_type` ('exact' | 'substring' | 'llm_judge' |
    'structural') -- enforced at the API layer, not the schema, same
    "structural over validated" tradeoff Workflow's entry-point invariant
    already makes:
      - 'exact' / 'substring': `expected_output` required, the rest null.
      - 'llm_judge': `rubric` required, the rest null.
      - 'structural' (agent-attached suites only -- api/evals.py rejects
        this judge_type when the parent suite is workflow-attached, since
        only an agent run's RunOutput.tools carries tool-call data;
        WorkflowRun has no equivalent): `expected_tool_name` required;
        `expected_tool_args_json` optional (null means "just check the
        tool was called at all, ignore its arguments")."""

    __tablename__ = "eval_case"

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    suite_id: Mapped[str] = mapped_column(ForeignKey("eval_suite.id", ondelete="CASCADE"))
    name: Mapped[str]
    input_content: Mapped[str]
    judge_type: Mapped[str]  # 'exact' | 'substring' | 'llm_judge' | 'structural'
    expected_output: Mapped[str | None] = mapped_column(default=None)
    rubric: Mapped[str | None] = mapped_column(default=None)
    expected_tool_name: Mapped[str | None] = mapped_column(default=None)
    expected_tool_args_json: Mapped[str | None] = mapped_column(default=None)  # JSON dict
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(default=utcnow_iso)
    vector_clock: Mapped[int] = mapped_column(default=0)

    suite: Mapped["EvalSuite"] = relationship(back_populates="cases")


class EvalRun(Base):
    """One execution of every case in an EvalSuite (#95), single pass -- v1
    has no repeat-and-average scoring for judge flakiness (see
    evals/judge.py). Not synced -- local telemetry, same reasoning as
    WorkflowRun: a fresh peer doesn't need another node's eval history to
    function, and re-running the suite *definition* is all sync needs to
    carry.

    `pass_count`/`fail_count`/`error_count` are denormalized off this run's
    EvalCaseResult rows, the same convenience WorkflowRun.final_output
    provides over re-deriving a summary from WorkflowNodeRun every time the
    UI wants to show one."""

    __tablename__ = "eval_run"
    __table_args__ = (Index("idx_eval_run_suite", "suite_id", "started_at"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    suite_id: Mapped[str] = mapped_column(ForeignKey("eval_suite.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(default="running")  # 'running' | 'completed'
    triggered_by: Mapped[str] = mapped_column(default="human")  # v1 is on-demand-only
    triggered_by_id: Mapped[str | None] = mapped_column(default=None)  # Human.id who clicked Run
    case_count: Mapped[int] = mapped_column(default=0)
    pass_count: Mapped[int] = mapped_column(default=0)
    fail_count: Mapped[int] = mapped_column(default=0)
    error_count: Mapped[int] = mapped_column(default=0)
    started_at: Mapped[str] = mapped_column(default=utcnow_iso)
    completed_at: Mapped[str | None] = mapped_column(default=None)


class EvalCaseResult(Base):
    """One EvalCase's outcome within an EvalRun (#95). Not synced, same
    reasoning as EvalRun/WorkflowNodeRun.

    `score` is a float in [0.0, 1.0], set only for judge_type='llm_judge'
    results -- always null for the three boolean-match types ('exact',
    'substring', 'structural'), where `status` alone is complete and a
    score would just be a fake-precise 0.0/1.0 standing in for a plain
    pass/fail.

    `actual_tool_calls_json` is populated only for judge_type='structural'
    results (possibly an empty JSON list -- see evals/judge.py's
    judge_structural for why an empty list is `status='error'`, not an
    automatic 'failed': some providers don't report tool calls even on a
    genuinely successful completion)."""

    __tablename__ = "eval_case_result"
    __table_args__ = (Index("idx_eval_case_result_run", "run_id", "started_at"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    run_id: Mapped[str] = mapped_column(ForeignKey("eval_run.id", ondelete="CASCADE"))
    case_id: Mapped[str] = mapped_column(ForeignKey("eval_case.id", ondelete="CASCADE"))
    status: Mapped[str]  # 'passed' | 'failed' | 'error'
    score: Mapped[float | None] = mapped_column(default=None)
    actual_output: Mapped[str | None] = mapped_column(default=None)
    actual_tool_calls_json: Mapped[str | None] = mapped_column(default=None)
    judge_reasoning: Mapped[str | None] = mapped_column(default=None)  # llm_judge only
    error_message: Mapped[str | None] = mapped_column(default=None)
    started_at: Mapped[str] = mapped_column(default=utcnow_iso)
    completed_at: Mapped[str | None] = mapped_column(default=None)


class BudgetCap(Base):
    """Spend budget definition (#97): a $ limit per day/week/month at
    agent, team, or workspace scope, with a configurable action on breach.
    Synced like Team/Agent/WorkspaceSetting -- the *definition* is agreed
    workspace policy, visible to every peer. *Enforcement* against it is
    local-only per peer (dispatch/budgets.py), computed from that peer's
    own AgentRun spend -- no cross-peer aggregation (that needs a
    singleton consumer of #101's coordinator election, which is not
    wired yet -- the election primitive itself shipped). Same explicit
    v1 limitation WorkflowSchedule (#92) documents for its own
    local-only firing. #246's launch-readiness scan re-raised this same
    gap (multiple online peers can each independently pass the same cap,
    letting workspace-wide spend reach 2x the configured limit); accepted
    as a documented v1 limitation rather than fixed there, same reasoning
    as above -- the fix is consuming the coordinator election, not
    something specific to budget enforcement.

    `ck_budget_cap_scope` mirrors EvalSuite's ck_eval_suite_single_subject:
    exactly one of agent_id/team_id is set for their respective scope_type,
    both null for 'workspace'. Both FKs use ondelete='CASCADE' for the same
    reason EvalSuite's do -- a cap on a deleted agent/team has nothing left
    to cap.

    A workflow's 'agent' node now also writes an AgentRun row (#96's
    agentos/accounting.py, shared with dispatch/service.py's _invoke_agent),
    so workflow-node agent spend counts toward these caps too -- unlike the
    gap that existed before #96 landed, there's no longer a blind spot
    here."""

    __tablename__ = "budget_cap"
    __table_args__ = (
        CheckConstraint(
            "(scope_type = 'agent' AND agent_id IS NOT NULL AND team_id IS NULL) OR "
            "(scope_type = 'team' AND team_id IS NOT NULL AND agent_id IS NULL) OR "
            "(scope_type = 'workspace' AND agent_id IS NULL AND team_id IS NULL)",
            name="ck_budget_cap_scope",
        ),
        CheckConstraint("limit_usd > 0", name="ck_budget_cap_limit_positive"),
        CheckConstraint("period IN ('day', 'week', 'month')", name="ck_budget_cap_period"),
        CheckConstraint("action IN ('alert', 'hard_stop')", name="ck_budget_cap_action"),
        Index("idx_budget_cap_agent", "agent_id"),
        Index("idx_budget_cap_team", "team_id"),
    )

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    scope_type: Mapped[str]  # 'agent' | 'team' | 'workspace'
    agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent.id", ondelete="CASCADE"), default=None
    )
    team_id: Mapped[str | None] = mapped_column(
        ForeignKey("team.id", ondelete="CASCADE"), default=None
    )
    period: Mapped[str]  # 'day' | 'week' | 'month'
    limit_usd: Mapped[float]
    action: Mapped[str]  # 'alert' | 'hard_stop'
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(default=utcnow_iso)
    vector_clock: Mapped[int] = mapped_column(default=0)


class RunTrace(Base):
    """The root of one causal chain of execution (#96): a human message, a
    slash command, or a scheduled workflow fire. Everything that chain goes
    on to do -- dispatch decisions, agent runs, workflow runs and their
    nodes, however deeply nested or re-dispatched -- is recorded as a
    `RunSpan` under this same `id`, so a human can view one end-to-end
    timeline instead of piecing it together from AgentRun/DispatchDecision/
    WorkflowRun/WorkflowNodeRun's separate, previously-unlinked tables (see
    those models' own docstrings -- this is deliberately a thin linking
    layer over them, not a replacement).

    Not synced -- local telemetry tied to whichever node actually did the
    work, same reasoning as AgentRun/DispatchDecision/WorkflowRun: a fresh
    peer doesn't need another node's trace history to function. Also
    deliberately local-only in a second sense (#101's "who coordinates
    workspace-singleton state" question doesn't apply here): a trace never
    spans two peers even when a run does (a remote-dispatched agent call,
    sync/agent_dispatch.py) -- the remote peer's own share of that work
    shows up only in *its* trace history, invisible from here. Tracing that
    across peers is explicitly out of scope for v1 (see issue #96's own
    "leaning toward local-only" open question).

    `label` is a short, human-readable summary of what triggered this trace
    (the leading slice of the triggering message's content, or the
    workflow name for a schedule fire) -- set once at creation so the
    trace-list UI has something to show without joining back to Message/
    WorkflowSchedule for every row.

    `span_count`/`total_cost_usd`/`total_tokens` are denormalized off this
    trace's own RunSpan rows at `finish_trace` time, the same convenience
    EvalRun.pass_count/fail_count/error_count provides over re-deriving a
    summary from EvalCaseResult every time the UI wants to show one.

    A trace left `status='running'` with no completed_at is either still
    genuinely in flight, or -- v1's one known gap -- a WorkflowRun that
    paused for human input and was later resumed (workflows/engine.py's
    `resume_workflow` docstring): tracing intentionally doesn't span a
    pause/resume boundary, so a resumed run's remaining node executions
    aren't added as spans, and this trace is left open rather than closed
    prematurely. Retention (tracing.py's prune loop) sweeps these up by
    `started_at` age regardless of status, so a stuck-open trace doesn't
    accumulate forever."""

    __tablename__ = "run_trace"
    __table_args__ = (Index("idx_run_trace_started", "started_at"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    trigger_type: Mapped[str]  # 'message' | 'schedule' | 'webhook'
    label: Mapped[str]
    rivulet_id: Mapped[str | None] = mapped_column(
        ForeignKey("rivulet.id", ondelete="SET NULL"), default=None
    )
    channel_id: Mapped[str | None] = mapped_column(
        ForeignKey("channel.id", ondelete="SET NULL"), default=None
    )
    status: Mapped[str] = mapped_column(default="running")  # 'running' | 'completed' | 'error'
    span_count: Mapped[int] = mapped_column(default=0)
    total_cost_usd: Mapped[float | None] = mapped_column(default=None)
    total_tokens: Mapped[int] = mapped_column(default=0)
    started_at: Mapped[str] = mapped_column(default=utcnow_iso)
    completed_at: Mapped[str | None] = mapped_column(default=None)


class RunSpan(Base):
    """One step within a RunTrace (#96): either an existing AgentRun,
    DispatchDecision, WorkflowRun, or WorkflowNodeRun row (`entity_id`
    points at it; `span_type` says which table) given start/end timestamps
    and a place in the parent/child tree via `parent_span_id`. This table
    doesn't replace any of those four -- it's created alongside them, at
    the same call sites that already record them, purely to link what was
    previously only correlatable by `created_at` proximity (see those
    models' own docstrings, and issue #96's "zero structural link between
    a parent and child execution" framing).

    `parent_span_id` NULL marks a trace's root span (the first dispatch
    decision or workflow run a trace's trigger produced); everything else
    nests under the span that caused it -- an agent_run span's own
    recursive re-dispatch or handoff nests its dispatch_decision/agent_run
    spans under that agent_run span, a workflow_run's node executions nest
    under that workflow_run span, and a 'workflow'-type node's nested child
    run nests under that node's own workflow_node_run span.

    `model`/`cost_usd`/`total_tokens` are denormalized off the underlying
    AgentRun row for agent_run spans only (null for every other span_type)
    -- convenience for rendering a trace's cost/token breakdown, and for
    `finish_trace` aggregating RunTrace.total_cost_usd/total_tokens,
    without a join back to AgentRun for every span.

    `duration_ms` is computed once at `finish_span` time from
    started_at/completed_at -- which, like every other timestamp in this
    schema (db/base.py's utcnow_iso), only has one-second resolution, so
    this is accurate to the nearest second, not truly millisecond-precise.
    That's an acceptable v1 tradeoff (most spans of interest -- an LLM
    call, a workflow node -- run for multiple seconds) rather than adding
    a separate sub-second timing channel just for this one field.

    Not synced, same reasoning as RunTrace."""

    __tablename__ = "run_span"
    __table_args__ = (
        Index("idx_run_span_trace", "trace_id", "started_at"),
        Index("idx_run_span_parent", "parent_span_id"),
    )

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    trace_id: Mapped[str] = mapped_column(ForeignKey("run_trace.id", ondelete="CASCADE"))
    parent_span_id: Mapped[str | None] = mapped_column(
        ForeignKey("run_span.id", ondelete="CASCADE"), default=None
    )
    # 'dispatch_decision' | 'agent_run' | 'workflow_run' | 'workflow_node_run'
    span_type: Mapped[str]
    entity_id: Mapped[str | None] = mapped_column(default=None)
    name: Mapped[str]
    status: Mapped[str] = mapped_column(default="running")  # 'running' | 'completed' | 'error'
    model: Mapped[str | None] = mapped_column(default=None)
    cost_usd: Mapped[float | None] = mapped_column(default=None)
    total_tokens: Mapped[int | None] = mapped_column(default=None)
    started_at: Mapped[str] = mapped_column(default=utcnow_iso)
    completed_at: Mapped[str | None] = mapped_column(default=None)
    duration_ms: Mapped[int | None] = mapped_column(default=None)


class Rivulet(Base):
    __tablename__ = "rivulet"
    __table_args__ = (Index("idx_rivulet_channel", "channel_id", "created_at"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channel.id", ondelete="CASCADE"))
    title: Mapped[str | None] = mapped_column(default=None)
    agentos_session_id: Mapped[str | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(default="active")  # active | paused | closed
    created_by: Mapped[str]  # 'human' or agent_id or workflow_schedule_id
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(default=utcnow_iso)
    vector_clock: Mapped[int] = mapped_column(default=0)

    channel: Mapped["Channel"] = relationship(back_populates="rivulets")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="rivulet", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "message"
    __table_args__ = (Index("idx_message_rivulet", "rivulet_id", "created_at"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    rivulet_id: Mapped[str] = mapped_column(ForeignKey("rivulet.id", ondelete="CASCADE"))
    sender_type: Mapped[str]  # 'human' | 'agent' | 'system'
    sender_id: Mapped[str | None] = mapped_column(
        default=None
    )  # agent ID (sender_type='agent') or human ID (sender_type='human'); None for system messages
    sender_name: Mapped[str]
    content: Mapped[str]  # markdown
    content_type: Mapped[str] = mapped_column(default="text")  # text|handoff|system_alert
    metadata_json: Mapped[str | None] = mapped_column(default=None)  # JSON
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
    vector_clock: Mapped[int] = mapped_column(default=0)

    rivulet: Mapped["Rivulet"] = relationship(back_populates="messages")


class RivuletGuardState(Base):
    """Loop prevention state. Not synced — tracked locally per node."""

    __tablename__ = "rivulet_guard_state"

    rivulet_id: Mapped[str] = mapped_column(
        ForeignKey("rivulet.id", ondelete="CASCADE"), primary_key=True
    )
    agent_exchange_count: Mapped[int] = mapped_column(default=0)
    recent_interactions: Mapped[str | None] = mapped_column(default=None)  # JSON
    agent_active_since: Mapped[str | None] = mapped_column(default=None)
    paused: Mapped[bool] = mapped_column(default=False)
    paused_at: Mapped[str | None] = mapped_column(default=None)
    pause_reason: Mapped[str | None] = mapped_column(default=None)


class BudgetCapState(Base):
    """Local-only enforcement bookkeeping for one BudgetCap (#97) -- not
    synced, same treatment as RivuletGuardState: each peer tracks its own
    alert/override state against its own locally-computed spend (see
    BudgetCap's docstring on why enforcement itself is local-only).

    `period_start` is the calendar-aligned window boundary
    (dispatch/budgets.py's `_period_start`) this row's alert/override
    state was last evaluated against -- both `alerted_at` and
    `override_active` are meaningless once a fresh check computes a
    *different* period_start than what's stored here (the window rolled
    over), which is what makes a hard-stop override auto-expire at the
    next period without any cleanup job: dispatch/budgets.py's check
    function simply ignores a stale override rather than clearing it."""

    __tablename__ = "budget_cap_state"

    cap_id: Mapped[str] = mapped_column(
        ForeignKey("budget_cap.id", ondelete="CASCADE"), primary_key=True
    )
    period_start: Mapped[str | None] = mapped_column(default=None)
    alerted_at: Mapped[str | None] = mapped_column(default=None)  # dedup for action='alert'
    override_active: Mapped[bool] = mapped_column(default=False)
    # Must equal the freshly computed period_start to count as still valid.
    override_period_start: Mapped[str | None] = mapped_column(default=None)
    override_by: Mapped[str | None] = mapped_column(default=None)  # Human.id
    override_at: Mapped[str | None] = mapped_column(default=None)


class PendingApproval(Base):
    """Unified "needs a human's OK first" queue (#102), replacing three
    independently-invented gates that each reused a different pre-existing
    field for the same underlying primitive:

      - 'schedule' (#93): an agent-created WorkflowSchedule starts
        `enabled=False` (see that model's docstring) -- approving here sets
        it True, same recompute-next_fire_at-from-now logic
        api/workflows.py's update_schedule already applies on a manual
        re-enable.
      - 'budget' (#97): a tripped hard_stop BudgetCap needs the same
        BudgetCapState.override_active write api/budgets.py's
        POST /budgets/{id}/override already performs -- approving here
        does exactly that, for the remainder of the current period only.
      - 'tool_guardrail' (#100): an unattended run hit
        agentos/tool_audit.py's ensure_unattended_tools_allowed with the
        agent not yet approved -- approving here sets
        Agent.approved_for_unattended_tools, the same flag the agent's own
        settings toggle already writes. Unlike the other two sources, the
        run this gate blocked has already failed by the time this row
        exists (tool_audit.py's docstring: a hard pre-flight refusal, not
        a pause-and-wait) -- approving unblocks the *next* attempt, it
        doesn't resume the one that failed.

    `ck_pending_approval_source` enforces exactly one of
    schedule_id/budget_cap_id/agent_id is set, matching source_type --
    same discriminator+CHECK shape as BudgetCap's own ck_budget_cap_scope.

    Deliberately NOT synced, unlike the issue's own "presumably
    workspace-shared" framing -- two of the three things a row can point
    at (WorkflowSchedule, BudgetCapState) are themselves local-only per
    peer (see their docstrings), so a synced approval row would dangle a
    foreign key on every peer that isn't the one that created it. Same
    local-only treatment as WorkflowSchedule/BudgetCapState/ToolCallLog
    themselves: this is an enforcement-time event tied to whichever node's
    gate actually tripped, not shared workspace policy -- only the
    *outcome* of approving it (Agent.approved_for_unattended_tools, a
    BudgetCap's definition) is workspace policy, and those already sync
    through their own synced rows.

    `title`/`detail` are captured at creation time rather than derived
    live from the source row when listing -- so the inbox still reads
    sensibly for a 'tool_guardrail' row whose triggering run has already
    failed and scrolled out of view, and so a later edit to the source
    (e.g. the schedule's cron_expression) doesn't retroactively change
    what a still-open approval says it's asking for.

    `resolved_by`/`resolved_at` are set once, on the terminal transition
    into 'approved' or 'rejected' -- a row never moves out of a terminal
    status, so unlike BudgetCapState's override bookkeeping there's
    nothing here that auto-expires or needs to be re-checked against a
    rolling period."""

    __tablename__ = "pending_approval"
    __table_args__ = (
        CheckConstraint(
            "(source_type = 'schedule' AND schedule_id IS NOT NULL "
            "AND budget_cap_id IS NULL AND agent_id IS NULL) OR "
            "(source_type = 'budget' AND budget_cap_id IS NOT NULL "
            "AND schedule_id IS NULL AND agent_id IS NULL) OR "
            "(source_type = 'tool_guardrail' AND agent_id IS NOT NULL "
            "AND schedule_id IS NULL AND budget_cap_id IS NULL)",
            name="ck_pending_approval_source",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')", name="ck_pending_approval_status"
        ),
        Index("idx_pending_approval_status", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    source_type: Mapped[str]  # 'schedule' | 'budget' | 'tool_guardrail'
    schedule_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_schedule.id", ondelete="CASCADE"), default=None
    )
    budget_cap_id: Mapped[str | None] = mapped_column(
        ForeignKey("budget_cap.id", ondelete="CASCADE"), default=None
    )
    agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent.id", ondelete="CASCADE"), default=None
    )
    title: Mapped[str]
    detail: Mapped[str]
    status: Mapped[str] = mapped_column(default="pending")  # 'pending' | 'approved' | 'rejected'
    resolved_by: Mapped[str | None] = mapped_column(default=None)  # Human.id
    resolved_at: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)


class RivuletSummary(Base):
    __tablename__ = "rivulet_summary"
    __table_args__ = (Index("idx_summary_rivulet", "rivulet_id", "level"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    rivulet_id: Mapped[str] = mapped_column(ForeignKey("rivulet.id", ondelete="CASCADE"))
    level: Mapped[int]  # 1 = chunk summary, 2 = meta-summary
    summary: Mapped[str]
    message_range_start: Mapped[str]
    message_range_end: Mapped[str]
    token_count: Mapped[int | None] = mapped_column(default=None)
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)


class File(Base):
    __tablename__ = "file"
    __table_args__ = (Index("idx_file_hash", "content_hash"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    content_hash: Mapped[str]  # SHA-256 hex
    filename: Mapped[str]
    mime_type: Mapped[str]
    size_bytes: Mapped[int]
    message_id: Mapped[str | None] = mapped_column(default=None)
    local_path: Mapped[str]
    # JSON list of node_ids known to have this content -- populated by
    # sync/apply.py's _remember_known_source whenever a remote file
    # change is applied, so a lazily-deferred (issue #123) fetch can
    # still be completed later, on demand.
    synced_to_nodes: Mapped[str | None] = mapped_column(default=None)  # JSON
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
    vector_clock: Mapped[int] = mapped_column(default=0)


class SyncState(Base):
    """Per-peer sync progress. Not synced itself."""

    __tablename__ = "sync_state"

    peer_node_id: Mapped[str] = mapped_column(primary_key=True)
    last_seen_at: Mapped[str | None] = mapped_column(default=None)
    last_sync_at: Mapped[str | None] = mapped_column(default=None)
    last_seq_num: Mapped[int] = mapped_column(default=0)
    pending_changes: Mapped[int] = mapped_column(default=0)


class VectorClockTracker(Base):
    __tablename__ = "vector_clock_tracker"

    entity_type: Mapped[str] = mapped_column(primary_key=True)
    entity_id: Mapped[str] = mapped_column(primary_key=True)
    node_id: Mapped[str] = mapped_column(primary_key=True)
    clock: Mapped[int] = mapped_column(default=0)


class SyncConflict(Base):
    """A concurrent edit detected via vector-clock comparison (FR-9.6) —
    neither the local nor the incoming remote version of an entity is a
    causal descendant of the other, so it isn't safe to last-write-wins
    resolve automatically. Not synced itself: each node judges conflicts
    against its own local state, and a conflict on one node isn't
    necessarily a conflict from another node's point of view."""

    __tablename__ = "sync_conflict"

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    entity_type: Mapped[str]
    entity_id: Mapped[str]
    local_snapshot: Mapped[str]  # JSON
    remote_snapshot: Mapped[str]  # JSON
    remote_node_id: Mapped[str]
    detected_at: Mapped[str] = mapped_column(default=utcnow_iso)
    resolved: Mapped[bool] = mapped_column(default=False)


class SyncPendingOutbound(Base):
    """An entity whose most recent publish attempt either couldn't be made
    (sync engine wasn't running) or failed outright — retried the next
    time the engine starts (FR-9.5: "when connectivity resumes, pending
    changes sync automatically"). Only entity_type/entity_id are stored,
    not the payload itself: by the time a retry runs, the entity may have
    changed again, so the retry always re-reads current state from its own
    table rather than replaying a payload that could be stale. Not synced
    itself — purely local bookkeeping.

    `deleted` (#238) distinguishes a queued live-state publish (re-read the
    entity and publish its current fields — publish_current_state) from a
    queued tombstone (the entity is already gone locally, so there's
    nothing to re-read — publish_tombstone). A row that started as a
    live-state publish gets promoted to `deleted=True` in place if the
    entity is deleted before the original publish ever succeeds, rather
    than the delete adding a second row for the same (entity_type,
    entity_id) — see sync/publish.py's `_record_pending_outbound`."""

    __tablename__ = "sync_pending_outbound"

    entity_type: Mapped[str] = mapped_column(primary_key=True)
    entity_id: Mapped[str] = mapped_column(primary_key=True)
    deleted: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)


class SyncPendingInbound(Base):
    """An incoming sync message that couldn't be applied because it
    references an entity that hasn't synced to this node yet
    (Rivulet.channel_id, Message.rivulet_id — the FK-ordering hazard
    documented in sync/apply.py's module docstring). The full message is
    stored here (unlike SyncPendingOutbound, which only stores an entity
    reference and re-reads current state) because there's nothing local
    to re-read: this is someone else's change, and the only copy of it is
    what arrived. Retried after every subsequent successful apply, on the
    chance the missing dependency just arrived too. Not synced itself."""

    __tablename__ = "sync_pending_inbound"

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    entity_type: Mapped[str]
    entity_id: Mapped[str]
    vector_clock_json: Mapped[str]  # JSON: dict[str, int]
    origin_node_id: Mapped[str]
    payload_json: Mapped[str]  # JSON: dict[str, Any]
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)


class KnowledgeBase(Base):
    """A named collection of ingested documents an agent or team can
    search mid-conversation via the search_knowledge_base builtin tool
    (#98). Synced like Team/BudgetCap -- the *definition* (name,
    description, scope) is agreed workspace policy, visible to every
    peer. Ingested documents and their chunks/embeddings
    (KnowledgeBaseDocument/KnowledgeBaseChunk below) are NOT synced --
    same FR-13.3 "definition syncs, derived local state doesn't"
    precedent AgentRun/WorkflowRun/DispatchDecision already established,
    and the same reason File bytes never travel in the gossipsub payload
    either: embeddings are large, regenerable, and tied to whichever peer
    actually did the ingestion. A peer that receives a KnowledgeBase's
    definition but hasn't ingested any documents locally yet just has an
    empty knowledge base until a human there uploads/ingests documents on
    that node -- an explicit v1 limitation, not an oversight (see #98's
    own "sync" open question).

    `ck_knowledge_base_scope` mirrors BudgetCap's ck_budget_cap_scope --
    exactly one of agent_id/team_id is set. Unlike BudgetCap there's no
    'workspace' scope option in v1: a knowledge base always belongs to a
    specific agent or team."""

    __tablename__ = "knowledge_base"
    __table_args__ = (
        CheckConstraint(
            "(scope_type = 'agent' AND agent_id IS NOT NULL AND team_id IS NULL) OR "
            "(scope_type = 'team' AND team_id IS NOT NULL AND agent_id IS NULL)",
            name="ck_knowledge_base_scope",
        ),
        Index("idx_knowledge_base_agent", "agent_id"),
        Index("idx_knowledge_base_team", "team_id"),
    )

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    name: Mapped[str]
    description: Mapped[str | None] = mapped_column(default=None)
    scope_type: Mapped[str]  # 'agent' | 'team'
    agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent.id", ondelete="CASCADE"), default=None
    )
    team_id: Mapped[str | None] = mapped_column(
        ForeignKey("team.id", ondelete="CASCADE"), default=None
    )
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(default=utcnow_iso)
    vector_clock: Mapped[int] = mapped_column(default=0)


class KnowledgeBaseDocument(Base):
    """One ingested source file (#98) within a KnowledgeBase -- v1 scope
    is single-file ingestion per document, reusing the existing File
    entity/upload endpoint (api/files.py) rather than a second
    content-storage path. Not synced (see KnowledgeBase's docstring):
    document rows are per-peer, derived-from-ingestion state, same
    treatment as AgentRun."""

    __tablename__ = "knowledge_base_document"
    __table_args__ = (Index("idx_kb_document_kb", "knowledge_base_id"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_base.id", ondelete="CASCADE")
    )
    file_id: Mapped[str] = mapped_column(ForeignKey("file.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(default="pending")  # 'pending'|'ingested'|'failed'
    error_message: Mapped[str | None] = mapped_column(default=None)
    chunk_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)


class KnowledgeBaseChunk(Base):
    """One embedded chunk of a KnowledgeBaseDocument's text content (#98).
    v1 stores the embedding vector as JSON-encoded floats in a regular
    column and does brute-force cosine similarity in Python
    (tools/builtin/knowledge_base.py) rather than a dedicated
    vector-search extension like sqlite-vec -- simple, correct, and fast
    enough at the modest scale a v1, single-file-per-document knowledge
    base actually reaches, avoiding a new C-extension dependency's
    platform-specific binary distribution risk for a first pass. Worth
    revisiting once real usage shows brute force doesn't scale. Not
    synced, same reasoning as KnowledgeBaseDocument."""

    __tablename__ = "knowledge_base_chunk"
    __table_args__ = (Index("idx_kb_chunk_kb", "knowledge_base_id"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_base.id", ondelete="CASCADE")
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_base_document.id", ondelete="CASCADE")
    )
    chunk_index: Mapped[int]
    content: Mapped[str]
    embedding_json: Mapped[str]  # JSON: list[float]
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
