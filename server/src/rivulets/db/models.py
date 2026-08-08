"""ORM models for all workspace entities.

Every synced entity carries a `vector_clock` column used by the P2P sync
engine for last-write-wins conflict resolution (FR-9.6). Tables noted as
"not synced" in the data model (provider_config, rivulet_guard_state,
sync_state) intentionally omit or ignore that column's sync semantics.
"""

from sqlalchemy import ForeignKey, Index, UniqueConstraint, text
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
    agentos_agent_id: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(default=utcnow_iso)
    vector_clock: Mapped[int] = mapped_column(default=0)

    routing_rules: Mapped[list["AgentRoutingRule"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )


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

    `source` leaves room for #31 (dispatcher hit-rate tracking) to record
    dispatcher-side LLM calls (classification, rule generation, fallback
    routing) in this same table without a schema change — those all record
    `"dispatcher_call"` instead of the default `"agent_run"`.
    """

    __tablename__ = "agent_run"
    __table_args__ = (Index("idx_agent_run_agent", "agent_id", "created_at"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agent.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(default="agent_run")  # 'agent_run' | 'dispatcher_call'
    model: Mapped[str]  # 'provider:model_name' — the concrete model that actually ran
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


class Tool(Base):
    __tablename__ = "tool"

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    name: Mapped[str]
    description: Mapped[str]
    tool_type: Mapped[str]  # 'builtin' | 'custom' | 'mcp'
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
    __tablename__ = "mcp_server"

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    name: Mapped[str]
    url: Mapped[str]
    connected: Mapped[bool] = mapped_column(default=False)
    last_connected_at: Mapped[str | None] = mapped_column(default=None)
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
    workflow definition is shared workspace content, not per-node state."""

    __tablename__ = "workflow"
    __table_args__ = (UniqueConstraint("name", name="idx_workflow_name"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    name: Mapped[str]
    description: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(default=utcnow_iso)
    vector_clock: Mapped[int] = mapped_column(default=0)

    nodes: Mapped[list["WorkflowNode"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan"
    )
    connections: Mapped[list["WorkflowConnection"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan"
    )


class WorkflowNode(Base):
    """One step in a workflow (#24): either an existing `Agent` (node_type
    'agent', `agent_id` set) or a built-in utility node ('summarize' |
    'transform' | 'conditional' | 'merge', workflows/nodes.py). `agent_id`
    uses ondelete='SET NULL' rather than CASCADE or the FK-less "looser
    association" pattern (Channel.team_id) -- deleting an agent that a
    workflow references shouldn't be blocked by that reference (unlike
    TeamAgent, which really is meaningless without its agent), but the
    node itself should survive as a now-misconfigured step an owner can
    fix, not silently disappear along with its parent workflow. Ordering/
    flow between nodes lives entirely in `WorkflowConnection`, not on this
    row, so branching later doesn't need a schema change here (only a
    change to how many outbound connections the engine follows)."""

    __tablename__ = "workflow_node"

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"))
    name: Mapped[str]
    node_type: Mapped[str]  # 'agent' | 'summarize' | 'transform' | 'conditional' | 'merge'
    agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent.id", ondelete="SET NULL"), default=None
    )
    config_json: Mapped[str | None] = mapped_column(default=None)  # JSON, node-type-specific
    retry_max_attempts: Mapped[int] = mapped_column(default=0)
    retry_backoff_seconds: Mapped[int] = mapped_column(default=5)
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(default=utcnow_iso)
    vector_clock: Mapped[int] = mapped_column(default=0)

    workflow: Mapped["Workflow"] = relationship(back_populates="nodes")


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
    function, and re-running the *definition* is all sync needs to carry."""

    __tablename__ = "workflow_run"
    __table_args__ = (Index("idx_workflow_run_workflow", "workflow_id", "started_at"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"))
    rivulet_id: Mapped[str] = mapped_column(ForeignKey("rivulet.id", ondelete="CASCADE"))
    triggered_by: Mapped[str]  # 'human' | 'agent'
    triggered_by_id: Mapped[str | None] = mapped_column(default=None)  # human_id or agent_id
    input_content: Mapped[str]
    # 'running' | 'completed' | 'failed' | 'awaiting_human'
    status: Mapped[str] = mapped_column(default="running")
    current_node_id: Mapped[str | None] = mapped_column(default=None)
    error_message: Mapped[str | None] = mapped_column(default=None)
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
    status: Mapped[str] = mapped_column(default="running")  # running|completed|failed|skipped
    input_content: Mapped[str]
    output_content: Mapped[str | None] = mapped_column(default=None)
    error_message: Mapped[str | None] = mapped_column(default=None)
    started_at: Mapped[str] = mapped_column(default=utcnow_iso)
    completed_at: Mapped[str | None] = mapped_column(default=None)


class Rivulet(Base):
    __tablename__ = "rivulet"
    __table_args__ = (Index("idx_rivulet_channel", "channel_id", "created_at"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channel.id", ondelete="CASCADE"))
    title: Mapped[str | None] = mapped_column(default=None)
    agentos_session_id: Mapped[str | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(default="active")  # active | paused | closed
    created_by: Mapped[str]  # 'human' or agent_id
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
    itself — purely local bookkeeping."""

    __tablename__ = "sync_pending_outbound"

    entity_type: Mapped[str] = mapped_column(primary_key=True)
    entity_id: Mapped[str] = mapped_column(primary_key=True)
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
