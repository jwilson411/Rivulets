# Rivulets — API Design

Two API surfaces: the **App Server API** (what the SvelteKit UI calls) and the **AgentOS API** (what the App Server calls internally). The UI never talks to AgentOS directly — the App Server mediates all communication.

---

## App Server API (localhost:8484/api/v1)

All endpoints require the workspace session token in the `Authorization` header, obtained via the auth endpoint.

### Authentication

```
POST /api/v1/auth/login
  Body: { "key": "<workspace-key-or-mnemonic>", "passphrase": "<optional-bip39-passphrase>" }
  Response: { "token": "<jwt>", "expires_at": "..." }
  Notes: Validates key against stored bcrypt hash. Returns JWT valid for 24h.
         JWT is signed with a derived key (not the workspace key directly).

POST /api/v1/auth/logout
  Header: Authorization: Bearer <token>
  Response: 204 No Content
```

### Channels

```
GET    /api/v1/channels                              — list all (active + archived)
POST   /api/v1/channels                              — create
         Body: { "name": "...", "description": "..." }
GET    /api/v1/channels/{channel_id}                 — get one
PATCH  /api/v1/channels/{channel_id}                 — update name/description/team
         Body: { "name"?: "...", "description"?: "...", "team_id"?: "..." }
DELETE /api/v1/channels/{channel_id}                 — archive (soft delete)
POST   /api/v1/channels/{channel_id}/unarchive       — restore
PATCH  /api/v1/channels/reorder                       — reorder
         Body: { "order": ["id1", "id2", ...] }
```

### Teams

```
GET    /api/v1/teams                                 — list all
POST   /api/v1/teams                                 — create
         Body: { "name": "...", "description": "..." }
GET    /api/v1/teams/{team_id}                       — get one with agent list
PATCH  /api/v1/teams/{team_id}                       — update
         Body: { "name"?: "...", "description"?: "...", "agent_ids"?: [...] }
DELETE /api/v1/teams/{team_id}                       — delete
```

### Agents

```
GET    /api/v1/agents                                — list all
POST   /api/v1/agents                                — create
         Body: {
           "name": "...", "description": "...", "instructions": "...",
           "model": "provider:model_name", "tool_ids": [...], "team_ids": [...]
         }
         Side effects: registers agent in AgentOS, generates routing rules via LLM
GET    /api/v1/agents/{agent_id}                     — get one with tools, teams, rules
PATCH  /api/v1/agents/{agent_id}                     — update (re-registers in AgentOS)
DELETE /api/v1/agents/{agent_id}                     — delete (removes from AgentOS)
GET    /api/v1/agents/{agent_id}/runs                — run history from AgentOS
         Query: ?limit=20&offset=0
GET    /api/v1/agents/{agent_id}/routing-rules       — get routing rules
PATCH  /api/v1/agents/{agent_id}/routing-rules       — manually edit rules
         Body: { "rules": [...] }
```

### Threads & Messages

```
GET    /api/v1/channels/{channel_id}/threads         — list threads (paginated)
         Query: ?limit=20&before=<cursor>&status=active
POST   /api/v1/channels/{channel_id}/threads         — post a new message (creates thread)
         Body: { "content": "...", "files"?: ["file_id", ...] }
         Side effects: triggers dispatcher, invokes matching agents
GET    /api/v1/threads/{thread_id}                   — get thread metadata
GET    /api/v1/threads/{thread_id}/messages          — get messages
         Query: ?limit=50&before=<cursor>
POST   /api/v1/threads/{thread_id}/messages          — post a message in existing thread
         Body: { "content": "...", "files"?: [...] }
         Side effects: resets guard counters, triggers dispatcher if human message
POST   /api/v1/threads/{thread_id}/resume            — resume paused thread
DELETE /api/v1/threads/{thread_id}                   — close thread

GET    /api/v1/threads/{thread_id}/stream            — SSE endpoint for live messages
         Event types: agent_token, agent_message, agent_tool_call, system_alert, handoff
         Notes: Client connects and receives all new messages in this thread as SSE events.
                Agent responses stream token-by-token as 'agent_token' events,
                with a final 'agent_message' event containing the complete message.
```

### Tools

```
GET    /api/v1/tools                                 — list all tools
POST   /api/v1/tools                                 — create custom tool
         Body: { "name": "...", "description": "..." }
         Simple mode: { "mode": "simple", "prompt": "A tool that..." }
         Advanced mode: { "mode": "advanced" } → returns tool path, user edits in editor
GET    /api/v1/tools/{tool_id}                       — get tool with current code
PATCH  /api/v1/tools/{tool_id}                       — update tool
DELETE /api/v1/tools/{tool_id}                       — delete custom tool
GET    /api/v1/tools/{tool_id}/versions              — version history
POST   /api/v1/tools/{tool_id}/versions/{v}/rollback — rollback to version
POST   /api/v1/tools/{tool_id}/open-editor           — open tool file in system editor
         Response: { "path": "/abs/path/to/tool.py" }
```

### MCP Servers

```
GET    /api/v1/mcp-servers                           — list registered MCP servers
POST   /api/v1/mcp-servers                           — register new
         Body: { "name": "...", "url": "..." }
         Side effects: connects to server, discovers tools, registers in AgentOS
GET    /api/v1/mcp-servers/{server_id}               — get with tool list
DELETE /api/v1/mcp-servers/{server_id}               — unregister
POST   /api/v1/mcp-servers/{server_id}/reconnect     — reconnect and refresh tools
```

### Files

```
POST   /api/v1/files/upload                          — upload file (multipart)
         Response: { "file_id": "...", "content_hash": "...", "filename": "...", ... }
GET    /api/v1/files/{file_id}                       — download file (or preview)
GET    /api/v1/files/{file_id}/info                  — metadata only
```

### Workspace Settings

```
GET    /api/v1/settings                              — all settings
PATCH  /api/v1/settings                              — update settings
         Body: { "guard.turn_limit": 15, ... }
POST   /api/v1/settings/export                       — export config as YAML
POST   /api/v1/settings/import                       — import config from YAML
```

### Sync

```
GET    /api/v1/sync/status                           — peer list + sync status
POST   /api/v1/sync/connect                          — manually connect to peer
         Body: { "address": "/ip4/192.168.1.5/tcp/..." }
POST   /api/v1/sync/disconnect                       — disconnect from peer
         Body: { "peer_id": "..." }
GET    /api/v1/sync/conflicts                        — list unresolved conflicts
POST   /api/v1/sync/conflicts/{entity_type}/{id}/resolve  — resolve conflict
         Body: { "keep": "local" | "remote" }
```

### Providers

```
GET    /api/v1/providers                             — list configured providers (no keys)
POST   /api/v1/providers                             — add provider
         Body: { "provider": "openai", "label": "...", "api_key": "...", "base_url"?: "..." }
PATCH  /api/v1/providers/{id}                        — update API key or settings
DELETE /api/v1/providers/{id}                        — remove provider
         Side effects: warns if agents are using this provider
```

### Health & Info

```
GET    /api/v1/health                                — { "status": "ok", "agentos": "connected|error" }
GET    /api/v1/info                                  — { "version": "0.1.0", "agentos_version": "..." }
```

---

## AgentOS API (Internal — App Server → AgentOS)

The App Server communicates with AgentOS on a separate internal port (default: 7777) or via Python SDK calls. These are the AgentOS endpoints we rely on:

```
POST   /agents/{agent_id}/runs                       — run an agent
         Body: {
           "message": "<user or system message>",
           "user_id": "human",
           "session_id": "<thread.agentos_session_id>",
           "stream": true
         }
         Response: SSE stream of RunEvent

GET    /agents                                       — list registered agents (for verification)
POST   /agents                                       — register an agent (on agent create/update)
DELETE /agents/{agent_id}                            — unregister an agent

GET    /sessions/{session_id}                        — get session history
GET    /sessions/{session_id}/runs                   — get runs for session

POST   /config/mcp                                   — configure MCP server
DELETE /config/mcp/{server_name}                     — remove MCP server configuration

GET    /info                                         — discover instance (auth mode, version, etc.)
GET    /config                                       — get full AgentOS configuration
```

The App Server wraps these calls. The UI never calls AgentOS directly — it only calls the App Server API.

---

## Authentication Flow (Web UI)

1. User opens `http://localhost:8484`.
2. If no valid JWT in localStorage, redirect to `/login`.
3. User enters 12-word mnemonic (workspace key) + optional passphrase.
4. App Server derives the key from the mnemonic (BIP-39 → seed → HKDF → workspace key).
5. App Server bcrypt-compares against stored `workspace.key_hash`. If match, issue JWT.
6. UI stores JWT in memory (not localStorage, for security). All subsequent requests include `Authorization: Bearer <jwt>`.
7. JWT expires after 24h. UI transparently re-authenticates using a refresh mechanism (re-derive from stored mnemonic in session storage — or prompt re-entry if the user cleared session).

---

## SSE Protocol (Thread Streaming)

The SSE endpoint at `GET /api/v1/threads/{thread_id}/stream` emits these event types:

```
event: agent_token
data: {"agent_id": "...", "agent_name": "...", "token": "Hello", "seq": 1}

event: agent_message
data: {"agent_id": "...", "message_id": "...", "content": "full message...", "seq": N}

event: agent_tool_call
data: {"agent_id": "...", "tool_name": "...", "args": {...}}

event: handoff
data: {"from_agent_id": "...", "to_agent_name": "...", "context": "..."}

event: system_alert
data: {"type": "guard_paused", "reason": "turn_limit", "message": "..."}

event: error
data: {"agent_id": "...", "error": "AgentOS run failed: ..."}

event: done
data: {"thread_id": "..."}
```

The UI uses `agent_token` events to build the streaming response display (character-by-character or chunk-by-chunk). `agent_message` is the final, complete message — the UI replaces the streamed preview with the formatted message. Tool calls render as expandable sections. Handoffs render as distinct message dividers.
