# Rivulets — Functional Requirements

All requirements are numbered, testable, and scoped to a specific feature area. References in brackets map to user stories (US-###).

---

## FR-1: Workspace & Installation

### FR-1.1 Installation Process
The system MUST provide an install script or binary that sets up the local Rivulets runtime including the AgentOS server, the web UI, and a local database. [US-001]

### FR-1.2 Workspace Key Generation
On first install, the system MUST generate a cryptographically secure workspace key (minimum 256-bit entropy) and present it to the user exactly once with a warning to store it securely. [US-001]

### FR-1.3 Workspace Key Import
The system MUST allow a user to import an existing workspace key during installation on additional machines, establishing that machine as a peer node in the mesh. [US-035]

### FR-1.4 LLM Provider Configuration
During installation, the system MUST prompt the user to configure at least one LLM provider by providing an API key and base URL. The system MUST support OpenAI, Anthropic, DeepSeek, and any OpenAI-compatible endpoint as minimum providers. [US-003]

### FR-1.5 Provider Key Isolation
LLM provider keys configured on one machine MUST NOT be included in any sync payload transmitted to peer nodes. Each machine's keys remain local. [US-036]

### FR-1.6 Web UI Binding
The system MUST serve a web-based chat UI on `localhost` at a configurable port (default: 8484). The UI MUST function in all modern browsers (Chrome, Firefox, Safari, Edge — last 2 major versions). [US-002]

### FR-1.7 AgentOS Integration
The local runtime MUST be a configured AgentOS (Agno) instance. All agent creation, tool registration, and MCP server mounting MUST map to AgentOS API calls, not custom implementations. [US-008, US-010]

---

## FR-2: Channel & Team Management

### FR-2.1 Channel CRUD
The system MUST allow users to create, rename, and delete channels. Each channel has a name (alphanumeric + hyphens/underscores, 3-80 chars) and an optional description. [US-004]

### FR-2.2 Team CRUD
The system MUST allow users to create, rename, and delete teams. Each team has a name and an ordered list of agent references. [US-005]

### FR-2.3 Channel-Team Assignment
The system MUST allow exactly one team to be assigned to a channel. Changing the team assignment MUST be possible at any time. [US-006]

### FR-2.4 Channel List Ordering
The system MUST persist channel display order and allow drag-to-reorder in the UI. [US-007]

### FR-2.5 Channel Archival
Archiving a channel MUST hide it from the default view, preserve all rivulet data, and allow unarchiving. Archived channels MUST NOT trigger agent dispatches. [US-007]

---

## FR-3: Agent Management

### FR-3.1 Agent Creation
The system MUST allow creating an agent with these fields:
- **name** (required, unique within workspace, 2-64 chars)
- **description** (required, 10-500 chars — used by the dispatcher for routing)
- **instructions** (required, system prompt — no character limit)
- **model** (required, selected from configured LLM providers)
- **tools** (optional, list of tool/MCP references)
- **teams** (optional, list of team assignments) [US-008, US-009, US-010, US-011]

### FR-3.2 Agent-to-AgentOS Mapping
Creating an agent in the UI MUST create a corresponding AgentOS agent via the AgentOS API, using the Agno SDK `Agent` class with all specified configuration parameters. [US-008]

### FR-3.3 Routing Rule Generation
On agent creation, the system MUST call a lightweight LLM with the agent's name, description, and instructions to generate deterministic routing rules. These rules MUST be stored as structured data (keywords, regex patterns, semantic triggers) associated with the agent. [US-017]

### FR-3.4 Agent Editing
The system MUST allow editing all agent fields post-creation. Editing instructions or description MUST trigger regeneration of routing rules. [US-012]

### FR-3.5 Agent Run History
The system MUST display per-agent run history including: run ID, timestamp, session ID, tokens consumed, model used, estimated cost, and status (completed/failed/cancelled). This data MUST come from AgentOS's built-in run tracking. [US-013]

---

## FR-4: Channel Dispatcher (Message Routing)

### FR-4.1 Dispatcher Pipeline
On every message posted to a channel's main feed, the system MUST run the dispatcher pipeline:
1. Match message against deterministic routing rules for all agents on the channel's team.
2. For any agent whose rules match, invoke that agent with the message context.
3. If NO agent's rules match, send the message + agent list (names + descriptions) to a lightweight LLM dispatcher.
4. The LLM dispatcher returns a list of agents to invoke (may be empty). [US-014, US-015, US-016]

### FR-4.2 Rule Types
Deterministic routing rules MUST support at minimum:
- **Keyword match:** Trigger if message contains any of [keywords]
- **Regex match:** Trigger if message matches [pattern]
- **Semantic trigger:** Trigger if message contains phrases like [examples]
- **Always:** Agent responds to every message (for omnipresent agents like orchestrators)
- **Never (explicit @mention only):** Agent only responds when @mentioned by name [US-015]

### FR-4.3 Dispatcher Performance
Deterministic rule matching for up to 50 agents MUST complete in under 50ms. The LLM fallback path MUST complete in under 3 seconds for 95th percentile. [US-015, US-016]

### FR-4.4 Empty Dispatch
If the dispatcher determines no agent should respond, the system MUST do nothing — no agent invocation, no error, no notification. The human's message simply sits in the channel. [US-016]

### FR-4.5 @Mention Override
Explicit @mentions of an agent by name MUST bypass all routing logic and directly invoke that agent, even if its routing rules would not match. [US-014]

---

## FR-5: Rivulets & Conversation Context

### FR-5.1 Automatic Rivulet Creation
When a human posts a message in the main channel, the system MUST create a rivulet container. The human message is the rivulet root. [US-018]

### FR-5.2 Agent Response Target
Agent responses to a message MUST be posted inside the rivulet, not the main channel feed. The main channel shows only human messages and a rivulet preview (last agent message + reply count). [US-019]

### FR-5.3 Rivulet Participation
Any agent on the channel's team that is invoked (by dispatcher or @mention) MUST receive the full rivulet context as conversation history. Agents MUST be able to post messages within the rivulet at any point during the rivulet's lifecycle. [US-020, US-023]

### FR-5.4 Context Window Management
The system MUST track token count for each rivulet. When total rivulet token count exceeds 80% of the target model's context window, the system MUST:
1. Generate a running summary of messages older than the most recent N (configurable, default N=20).
2. Replace summarized messages with the summary in the context sent to agents.
3. Preserve the full history in the database for human viewing. [US-021]

### FR-5.5 Internal Reasoning Suppression
Agent internal reasoning (chain-of-thought, thinking blocks, tool call details) MUST NOT be displayed to the human or to other agents. Only the agent's final output message is posted to the rivulet. Tool call results MUST be visible only if the agent explicitly includes them in its response. [US-022]

### FR-5.6 Agent-to-Agent Visibility
When an agent posts a message in a rivulet, that message MUST be visible to all other agents subsequently invoked in that rivulet. Agents read the rivulet the same way a human does — they see prior agent messages as part of the conversation history. [US-020]

---

## FR-6: Agent Handoff

### FR-6.1 Handoff Tool
The system MUST provide a built-in `handoff` tool available to all agents with the signature:
```
handoff(target_agent_name: str, context: str, urgency: str = "normal")
```
Calling handoff MUST post a visible message in the rivulet: "@AgentName has been handed off: [context]" and MUST invoke the target agent with the handoff context. [US-024]

### FR-6.2 Handoff Context Injection
The target agent MUST receive the handoff context as a system-level message indicating it was explicitly handed work by another agent, plus the full rivulet history. [US-024]

### FR-6.3 Handoff Visibility
The human MUST see a distinct visual indicator in the rivulet when a handoff occurs (e.g., a divider or badge), showing which agent handed off to which, with the context message. [US-025]

---

## FR-7: Loop Prevention & Guardrails

### FR-7.1 Turn Limit
Each rivulet MUST track an "agent exchange count" — incremented each time an agent posts a message in the rivulet without an intervening human message. When the count reaches the configurable limit (default: 10), the system MUST:
1. Post a system message in the rivulet: "Agent conversation has reached the turn limit. Waiting for human input."
2. Suppress all further agent invocations in the rivulet until a human posts. [US-026]

### FR-7.2 Cycle Detection
The system MUST maintain a sliding window of the last 8 agent-to-agent interactions in each rivulet. If the pattern repeats (same ordered pair of agents appearing 3+ times in the window), the system MUST:
1. Post a system message identifying the cycle.
2. Pause both agents in the rivulet until a human posts. [US-027]

### FR-7.3 Time-Based Pause
Each rivulet MUST track total agent-active time (time since first agent response in the rivulet without a human message). When this exceeds the configurable limit (default: 30 minutes), further agent invocations are paused. [US-028]

### FR-7.4 Guardrail Configuration
All loop prevention thresholds MUST be configurable in workspace settings: turn limit (1-100), cycle detection window size (4-20), time-based pause (5 min - 24 hours). [US-026, US-027, US-028]

### FR-7.5 Human Reactivation
A paused rivulet MUST display a clear "Resume" affordance. A human posting any message in the rivulet MUST automatically reset all loop counters and resume normal agent activity. [US-029]

---

## FR-8: Tool & MCP Management

### FR-8.1 Built-in Tool Library
The system MUST ship with a library of pre-built tools. Minimum required tools:
- **Web Search:** Search the web via configurable search API (Brave, Tavily, etc.)
- **File System:** Read/write/list files within a workspace-bounded directory
- **Code Execution:** Execute Python in a sandboxed environment
- **HTTP Request:** Make arbitrary HTTP requests
- **Database Query:** Query a local SQLite database via a workspace tool [US-030]

### FR-8.2 Tool Assignability
Any built-in or custom tool MUST be assignable to one or more agents. Assigning a tool MUST register it with the agent's AgentOS configuration. [US-010]

### FR-8.3 Simple Tool Creation
The system MUST provide a "Simple Mode" tool creator where the user describes the desired tool in natural language. The system MUST:
1. Send the description to an LLM with instructions to generate valid Agno SDK tool code.
2. Display the generated code for review.
3. On user approval, register the tool and make it available for agent assignment. [US-031]

### FR-8.4 Advanced Tool Creation
The system MUST provide a way to open a tool's Python file in the user's default editor (detected or configurable). The file MUST be a valid Agno SDK tool file at a known path on disk. Changes saved by the user MUST trigger tool re-registration. [US-032]

### FR-8.5 MCP Server Registration
The system MUST allow registering an external MCP server by providing a name and URL. The system MUST connect to the server, discover its tools, and make them available for agent assignment. This MUST map to AgentOS MCP configuration. [US-033]

---

## FR-9: Peer-to-Peer Sync

### FR-9.1 Sync Scope
The sync system MUST replicate across nodes: agent definitions, channel and team structures, rivulet history (messages and metadata), tool code, MCP server registrations, file attachments, and workspace settings. [US-035]

### FR-9.2 Sync Exclusion
LLM provider keys and API credentials MUST be excluded from all sync payloads. Each node manages its own credentials. [US-036]

### FR-9.3 Node Discovery
Nodes sharing the same workspace key MUST discover each other on the local network via mDNS/DNS-SD. Manual node address entry MUST also be supported for nodes on different networks. [US-035]

### FR-9.4 Workspace Key as Cipher
The workspace key MUST be used as the pre-shared key for encrypting all sync traffic between nodes. No plaintext workspace data may traverse the network. [US-035]

### FR-9.5 Offline Operation
A node MUST be fully functional when other nodes are unreachable: create agents, post messages, manage channels, use tools. When connectivity resumes, pending changes sync automatically. [US-037]

### FR-9.6 Conflict Resolution
When the same entity (agent, rivulet, setting) is modified on two nodes while disconnected, the system MUST apply last-write-wins based on vector clocks. Conflicting changes MUST be surfaced in the UI as a notification with the ability to inspect both versions. [US-038]

### FR-9.7 File Sync Strategy
Files attached to rivulets MUST be replicated to all peer nodes. The system MUST store a content hash with each file. On sync, only files with differing hashes are transferred. Each node maintains a complete copy for fault tolerance. [US-043]

---

## FR-10: File Handling

### FR-10.1 File Upload
The system MUST support uploading files (up to 100MB per file) into rivulets via drag-and-drop or file picker. Supported formats for preview: images (PNG, JPEG, GIF, WebP, SVG), code (any text file with syntax highlighting), PDFs, CSVs (rendered as tables). [US-041]

### FR-10.2 File Storage
Uploaded files MUST be stored locally on the node where they were uploaded. File metadata (path, hash, MIME type, upload timestamp) MUST be included in the rivulet message and synced to peers. [US-041]

### FR-10.3 Agent File Access
When a file is shared in a rivulet, agents invoked in that rivulet MUST be able to access the file through a workspace tool that reads from the local file store. The file reference in the rivulet message MUST resolve to the local path where the file (or its synced copy) resides. [US-042]

---

## FR-11: Invite System (P2 — Future)

### FR-11.1 Invite Generation
The workspace owner MUST be able to generate time-limited invite codes cryptographically signed with the workspace key. Invite codes are single-use by default with configurable max uses. [US-039]

### FR-11.2 Invite Acceptance
A user receiving an invite code MUST be able to enter it during installation to join an existing workspace. The system MUST validate the invite cryptographically against the workspace key. [US-040]

### FR-11.3 New Node Bootstrap
On joining, the new node MUST receive a full sync of the workspace state (agents, channels, rivulets, files) from any reachable peer. [US-040]

---

## FR-12: AgentOS API Surface Mapping

### FR-12.1 Agent Runs
Posting a message that routes to an agent MUST call `POST /agents/{agent_id}/runs` on the local AgentOS instance, passing the rivulet context as conversation history and a session ID keyed to the rivulet. [US-014, US-020]

### FR-12.2 Session Management
Each rivulet MUST have a persistent AgentOS session ID. All agent invocations within a rivulet reuse the same session ID for continuity. [US-020]

### FR-12.3 Streaming
Agent responses MUST stream to the UI via AgentOS SSE endpoints. The human MUST see agent responses appear token-by-token (or chunk-by-chunk) in real time. [US-022]

### FR-12.4 MCP Mounting
Registering an MCP server in the UI MUST configure it in the AgentOS MCP configuration, making its tools discoverable to agents. [US-033]

### FR-12.5 RBAC / Security Key
The AgentOS instance MUST be configured with `auth_mode: security_key` using the workspace key (or a derived key) as the shared secret. The web UI communicates with the local AgentOS API using this key. [US-001]
