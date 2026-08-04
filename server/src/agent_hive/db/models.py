"""ORM models mirroring docs/architecture/data-model.md.

Every synced entity carries a `vector_clock` column used by the P2P sync
engine for last-write-wins conflict resolution (FR-9.6). Tables noted as
"not synced" in the data model (provider_config, thread_guard_state,
sync_state) intentionally omit or ignore that column's sync semantics.
"""

from sqlalchemy import ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agent_hive.db.base import Base, utcnow_iso, uuid7


class Workspace(Base):
    __tablename__ = "workspace"

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    name: Mapped[str] = mapped_column(default="My Workspace")
    key_hash: Mapped[str]
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(default=utcnow_iso)
    vector_clock: Mapped[int] = mapped_column(default=0)


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

    threads: Mapped[list["Thread"]] = relationship(back_populates="channel")


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


class Thread(Base):
    __tablename__ = "thread"
    __table_args__ = (Index("idx_thread_channel", "channel_id", "created_at"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channel.id", ondelete="CASCADE"))
    title: Mapped[str | None] = mapped_column(default=None)
    agentos_session_id: Mapped[str | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(default="active")  # active | paused | closed
    created_by: Mapped[str]  # 'human' or agent_id
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(default=utcnow_iso)
    vector_clock: Mapped[int] = mapped_column(default=0)

    channel: Mapped["Channel"] = relationship(back_populates="threads")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="thread", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "message"
    __table_args__ = (Index("idx_message_thread", "thread_id", "created_at"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    thread_id: Mapped[str] = mapped_column(ForeignKey("thread.id", ondelete="CASCADE"))
    sender_type: Mapped[str]  # 'human' | 'agent' | 'system'
    sender_id: Mapped[str | None] = mapped_column(default=None)  # agent ID if applicable
    sender_name: Mapped[str]
    content: Mapped[str]  # markdown
    content_type: Mapped[str] = mapped_column(default="text")  # text|handoff|system_alert
    metadata_json: Mapped[str | None] = mapped_column(default=None)  # JSON
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
    vector_clock: Mapped[int] = mapped_column(default=0)

    thread: Mapped["Thread"] = relationship(back_populates="messages")


class ThreadGuardState(Base):
    """Loop prevention state. Not synced — tracked locally per node."""

    __tablename__ = "thread_guard_state"

    thread_id: Mapped[str] = mapped_column(
        ForeignKey("thread.id", ondelete="CASCADE"), primary_key=True
    )
    agent_exchange_count: Mapped[int] = mapped_column(default=0)
    recent_interactions: Mapped[str | None] = mapped_column(default=None)  # JSON
    agent_active_since: Mapped[str | None] = mapped_column(default=None)
    paused: Mapped[bool] = mapped_column(default=False)
    paused_at: Mapped[str | None] = mapped_column(default=None)
    pause_reason: Mapped[str | None] = mapped_column(default=None)


class ThreadSummary(Base):
    __tablename__ = "thread_summary"
    __table_args__ = (Index("idx_summary_thread", "thread_id", "level"),)

    id: Mapped[str] = mapped_column(primary_key=True, default=uuid7)
    thread_id: Mapped[str] = mapped_column(ForeignKey("thread.id", ondelete="CASCADE"))
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
