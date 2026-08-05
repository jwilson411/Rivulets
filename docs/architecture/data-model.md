# Rivulets — Data Model

All entities are stored in SQLite. Tables use UUIDv7 primary keys for time-sortable, globally unique IDs (critical for P2P sync — no ID collisions across nodes).

---

## Entity Relationship Diagram

```
Workspace
  ├── ProviderConfig (1:N) — LLM provider keys (NOT synced)
  ├── WorkspaceSettings (1:1)
  ├── Channel (1:N)
  │     ├── Thread (1:N)
  │     │     ├── Message (1:N)
  │     │     ├── ThreadGuardState (1:1)
  │     │     └── ThreadSummary (1:N)
  │     └── Team (N:1, via channel.team_id)
  ├── Team (1:N)
  │     └── TeamAgent (N:N join)
  ├── Agent (1:N)
  │     ├── AgentRoutingRule (1:N)
  │     └── AgentTool (N:N join)
  ├── Tool (1:N)
  │     └── ToolVersion (1:N) — for code history
  ├── MCPServer (1:N)
  ├── File (1:N)
  └── SyncState (1:1 per peer) — vector clock tracking
```

---

## Core Tables

### workspace
The root entity. Exactly one row per installation.
```sql
CREATE TABLE workspace (
    id          TEXT PRIMARY KEY,  -- UUIDv7
    name        TEXT NOT NULL DEFAULT 'My Workspace',
    key_hash    TEXT NOT NULL,      -- bcrypt hash of workspace key for local verification
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    vector_clock INTEGER NOT NULL DEFAULT 0
);
```

### provider_config
LLM provider credentials. **NOT synced between nodes** (FR-1.5). Stored in the system keychain where available, with SQLite as fallback for platforms without a keychain API.
```sql
CREATE TABLE provider_config (
    id          TEXT PRIMARY KEY,
    provider    TEXT NOT NULL,      -- 'openai', 'anthropic', 'deepseek', 'openai_compatible'
    label       TEXT NOT NULL,      -- user-friendly name
    api_key_ref TEXT NOT NULL,      -- reference to keychain entry, NOT the raw key
    base_url    TEXT,               -- override for custom endpoints
    is_default  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    synced      INTEGER NOT NULL DEFAULT 0  -- always 0 for provider_config
);
```

### workspace_settings
Key-value settings. Configurable via UI. Synced between nodes.
```sql
CREATE TABLE workspace_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,      -- JSON-encoded value
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    vector_clock INTEGER NOT NULL DEFAULT 0
);
-- Known keys:
-- dispatcher.model_override, dispatcher.fallback_enabled,
-- guard.turn_limit (default: 10), guard.cycle_window (default: 8),
-- guard.cycle_threshold (default: 3), guard.timeout_minutes (default: 30),
-- thread.summarization_enabled, thread.context_threshold_pct (default: 80),
-- thread.recent_messages_kept (default: 20),
-- sync.eager_files_lan (default: true), sync.eager_files_wan (default: false),
-- ui.port (default: 8484)
```

---

### channel
A Slack-like channel. Channels belong to the workspace.
```sql
CREATE TABLE channel (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,      -- unique within workspace
    description TEXT,
    team_id     TEXT,                -- FK to team, nullable (no team assigned)
    position    INTEGER NOT NULL DEFAULT 0,  -- display order
    archived    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    vector_clock INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX idx_channel_name ON channel(name) WHERE archived = 0;
```

### team
A named group of agents. Assigned to channels (one team per channel).
```sql
CREATE TABLE team (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    vector_clock INTEGER NOT NULL DEFAULT 0
);
```

### team_agent
Join table: which agents are on which teams.
```sql
CREATE TABLE team_agent (
    team_id     TEXT NOT NULL REFERENCES team(id) ON DELETE CASCADE,
    agent_id    TEXT NOT NULL REFERENCES agent(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (team_id, agent_id)
);
```

---

### agent
An AI agent. Maps 1:1 to an AgentOS agent. This is the core entity.
```sql
CREATE TABLE agent (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,      -- unique within workspace, 2-64 chars
    description TEXT NOT NULL,      -- used by dispatcher, 10-500 chars
    instructions TEXT NOT NULL,     -- system prompt, no char limit
    model       TEXT NOT NULL,      -- 'provider:model_name', e.g. 'deepseek:deepseek-chat'
    agentos_agent_id TEXT,          -- AgentOS internal ID (populated after AgentOS registration)
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    vector_clock INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX idx_agent_name ON agent(name);
```

### agent_routing_rule
Deterministic routing rules per agent. Generated by LLM at creation time. Evaluated in priority order.
```sql
CREATE TABLE agent_routing_rule (
    id          TEXT PRIMARY KEY,
    agent_id    TEXT NOT NULL REFERENCES agent(id) ON DELETE CASCADE,
    rule_type   TEXT NOT NULL,      -- 'keyword', 'regex', 'semantic', 'always', 'mention_only'
    pattern     TEXT NOT NULL,      -- JSON: keywords array, regex string, or trigger phrases
    priority    INTEGER NOT NULL DEFAULT 0,  -- higher = evaluated first
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX idx_routing_agent ON agent_routing_rule(agent_id);
```

### agent_tool
Join table: which tools are assigned to which agents.
```sql
CREATE TABLE agent_tool (
    agent_id    TEXT NOT NULL REFERENCES agent(id) ON DELETE CASCADE,
    tool_id     TEXT NOT NULL REFERENCES tool(id) ON DELETE CASCADE,
    PRIMARY KEY (agent_id, tool_id)
);
```

---

### tool
A tool (built-in or custom) that agents can use. Custom tools map to Python files.
```sql
CREATE TABLE tool (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL,
    tool_type   TEXT NOT NULL,      -- 'builtin', 'custom', 'mcp'
    source_path TEXT,               -- path to .py file (custom tools only)
    mcp_server_id TEXT,             -- FK to mcp_server (MCP tools only)
    mcp_tool_name TEXT,             -- original tool name from MCP server
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    vector_clock INTEGER NOT NULL DEFAULT 0
);
```

### tool_version
Version history for custom tools. Each save creates a new version for rollback.
```sql
CREATE TABLE tool_version (
    id          TEXT PRIMARY KEY,
    tool_id     TEXT NOT NULL REFERENCES tool(id) ON DELETE CASCADE,
    version     INTEGER NOT NULL,
    source_code TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX idx_tool_version ON tool_version(tool_id, version DESC);
```

### mcp_server
Registered external MCP servers.
```sql
CREATE TABLE mcp_server (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    url         TEXT NOT NULL,
    connected   INTEGER NOT NULL DEFAULT 0,
    last_connected_at TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    vector_clock INTEGER NOT NULL DEFAULT 0
);
```

---

### thread
A conversation thread inside a channel. Created when a human posts a message.
```sql
CREATE TABLE thread (
    id          TEXT PRIMARY KEY,
    channel_id  TEXT NOT NULL REFERENCES channel(id) ON DELETE CASCADE,
    title       TEXT,               -- auto-generated from first message
    agentos_session_id TEXT,         -- AgentOS session ID (populated on first agent run)
    status      TEXT NOT NULL DEFAULT 'active',  -- 'active', 'paused', 'closed'
    created_by  TEXT NOT NULL,      -- 'human' or agent_id
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    vector_clock INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_thread_channel ON thread(channel_id, created_at DESC);
```

### message
A single message in a thread (human or agent). The main channel's human message is the root of the thread and also appears here.
```sql
CREATE TABLE message (
    id          TEXT PRIMARY KEY,
    thread_id   TEXT NOT NULL REFERENCES thread(id) ON DELETE CASCADE,
    sender_type TEXT NOT NULL,      -- 'human', 'agent', 'system'
    sender_id   TEXT,               -- agent ID if sender_type = 'agent', null for human/system
    sender_name TEXT NOT NULL,      -- display name
    content     TEXT NOT NULL,      -- markdown
    content_type TEXT NOT NULL DEFAULT 'text',  -- 'text', 'handoff', 'system_alert'
    metadata    TEXT,               -- JSON: handoff details, file attachments, etc.
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    vector_clock INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_message_thread ON message(thread_id, created_at);
```

### thread_guard_state
Loop prevention state per thread. Not synced — each node tracks independently based on local message processing.
```sql
CREATE TABLE thread_guard_state (
    thread_id           TEXT PRIMARY KEY REFERENCES thread(id) ON DELETE CASCADE,
    agent_exchange_count INTEGER NOT NULL DEFAULT 0,
    recent_interactions TEXT,       -- JSON array of last 8 [agent_id, agent_id] pairs
    agent_active_since  TEXT,       -- ISO timestamp of first agent msg without human msg
    paused              INTEGER NOT NULL DEFAULT 0,
    paused_at           TEXT,
    pause_reason        TEXT        -- 'turn_limit', 'cycle_detected', 'timeout', 'manual'
);
```

### thread_summary
Stored summaries for context management (hierarchical summarization).
```sql
CREATE TABLE thread_summary (
    id          TEXT PRIMARY KEY,
    thread_id   TEXT NOT NULL REFERENCES thread(id) ON DELETE CASCADE,
    level       INTEGER NOT NULL,   -- 1 = chunk summary, 2 = meta-summary
    summary     TEXT NOT NULL,
    message_range_start TEXT NOT NULL,  -- message_id of first summarized message
    message_range_end   TEXT NOT NULL,  -- message_id of last summarized message
    token_count INTEGER,            -- estimated tokens in the summary
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX idx_summary_thread ON thread_summary(thread_id, level);
```

---

### file
File metadata for attachments. Actual files stored at `~/.rivulets/files/{content_hash}`.
```sql
CREATE TABLE file (
    id          TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,     -- SHA-256 hex
    filename    TEXT NOT NULL,
    mime_type   TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL,
    message_id  TEXT,               -- FK to message (nullable — file may sync before message)
    local_path  TEXT NOT NULL,      -- absolute path on this node
    synced_to_nodes TEXT,           -- JSON array of node IDs that have a copy
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    vector_clock INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_file_hash ON file(content_hash);
```

---

### sync_state
Tracks sync progress with each peer node. Per-peer, per-node. Not synced itself.
```sql
CREATE TABLE sync_state (
    peer_node_id TEXT PRIMARY KEY,  -- libp2p peer ID
    last_seen_at TEXT,
    last_sync_at TEXT,
    last_seq_num INTEGER NOT NULL DEFAULT 0,  -- last gossipsub sequence number processed
    pending_changes INTEGER NOT NULL DEFAULT 0
);
```

### vector_clock_tracker
Per-entity vector clocks for conflict resolution. Each row tracks the clock for one entity on one node.
```sql
CREATE TABLE vector_clock_tracker (
    entity_type TEXT NOT NULL,      -- 'agent', 'channel', 'thread', 'message', 'tool', etc.
    entity_id   TEXT NOT NULL,
    node_id     TEXT NOT NULL,      -- which node's clock this is
    clock       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (entity_type, entity_id, node_id)
);
```

---

## Sync Strategy

1. **State change events** are published to libp2p gossipsub topic `workspace/state` with the changed entity's full JSON + vector clock.
2. **Receiving node** compares the incoming vector clock with its local clock for that entity. If incoming > local, apply the change. If incoming <= local, discard (local is newer or same).
3. **Files** are transferred over libp2p streams using content-hash comparison. The receiving node requests only files where its hash differs from the sender's.
4. **Initial sync** (new node joins): full state dump from any peer, processed in dependency order (workspace → providers → agents → tools → channels → teams → threads → messages).

---

## Migration Strategy

SQLite makes migrations straightforward:
- Schema version stored in `workspace_settings` as `schema.version`.
- On startup, check version against expected. Run migrations sequentially via SQLAlchemy Alembic.
- Breaking changes: documented migration scripts per NFR-8.2.
- AgentOS version compatibility tracked separately in `agentos.version` setting.
