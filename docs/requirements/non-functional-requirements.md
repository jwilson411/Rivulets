# Agent Hive — Non-Functional Requirements

All NFRs include a measurable target. Vague requirements ("fast," "secure") are rejected in favor of testable thresholds.

---

## NFR-1: Performance

### NFR-1.1 Dispatcher Latency
- Deterministic rule matching for up to 50 agents: **<50ms p95**
- LLM fallback dispatcher (including API round-trip): **<3s p95**
- Combined pipeline (deterministic + fallback when needed): **<3.5s p95**

### NFR-1.2 Agent Response Streaming
- Time to first token (TTFT) in the UI after dispatch decision: **<2s p50, <5s p95**
- Streaming visual updates: at least **10 updates per second** perceived by the user

### NFR-1.3 UI Responsiveness
- Channel switching (loading thread list): **<200ms**
- Thread opening (loading message history): **<500ms for threads with <100 messages**
- Agent creation form submission: **<2s** (including routing rule generation)

### NFR-1.4 Sync Performance
- Initial full sync between two nodes on same LAN: **<30s for 10K messages, 50 agents**
- Incremental sync delta (post-connection): **<2s after a change on either node**
- File sync for a 10MB file: **<5s on gigabit LAN**

---

## NFR-2: Reliability & Availability

### NFR-2.1 Local Availability
- The local Agent Hive instance (AgentOS + web UI) MUST be available as long as the host machine is running.
- AgentOS crashes MUST be automatically restarted by the process supervisor.

### NFR-2.2 Offline Resilience
- A node with zero network connectivity MUST be 100% functional for all local operations (agent runs, channel management, thread viewing).
- No operation may fail or hang because peer nodes are unreachable. Sync is background-only.

### NFR-2.3 Data Durability
- Thread messages and agent configurations MUST survive AgentOS restarts, machine reboots, and application crashes without data loss.
- Database writes MUST use write-ahead logging (WAL) with fsync on commit.

### NFR-2.4 Graceful Degradation
- If the LLM dispatcher API is unreachable (rate limited, network error), the system MUST fall back to deterministic routing only and surface a warning in the UI.
- If an agent's model provider is unreachable, that agent MUST be marked "unavailable" with a visible indicator. Other agents continue functioning.

---

## NFR-3: Security

### NFR-3.1 Workspace Key Protection
- The workspace key MUST never be transmitted in plaintext over any network.
- The workspace key MUST be stored with filesystem permissions restricting read to the owning user only (0600 on Unix).

### NFR-3.2 Peer-to-Peer Encryption
- All sync traffic between nodes MUST be encrypted using the workspace key as a pre-shared key. Minimum: AES-256-GCM with per-message nonces.
- Node authentication MUST use the workspace key — only nodes possessing the key can join the mesh.

### NFR-3.3 LLM Key Isolation
- LLM provider API keys MUST be stored in a local credential store (system keychain on macOS, libsecret on Linux, Credential Manager on Windows).
- API keys MUST never appear in logs, error messages, sync payloads, or debug output.

### NFR-3.4 Web UI Security
- The web UI MUST bind to localhost only (127.0.0.1), not 0.0.0.0, by default.
- The UI MUST require the workspace key (or a derived session token) on first connect.
- CSRF protection MUST be enabled on all state-changing endpoints.

### NFR-3.5 Code Execution Sandboxing
- The code execution tool (FR-8.1) MUST run Python in an isolated environment: container, firejail, or equivalent. File system access MUST be restricted to a workspace-bounded directory.
- Network access from code execution MUST be configurable (default: deny outbound, allow with user approval).

---

## NFR-4: Scalability

### NFR-4.1 Agent Scale
- A single workspace MUST support at least **100 agents** without degradation in dispatcher performance.
- A single channel team MUST support at least **25 agents**.

### NFR-4.2 Message Volume
- The system MUST handle **1,000 messages per day** across all channels without performance degradation.

### NFR-4.3 Thread Depth
- Threads MUST support at least **500 messages** with full history browsing. Context summarization triggers automatically (FR-5.4).

### NFR-4.4 Node Count
- The P2P mesh MUST support at least **10 peer nodes** in a single workspace.

### NFR-4.5 File Storage
- Per-node file storage MUST scale to at least **10GB** of attachments without performance degradation.

---

## NFR-5: Usability

### NFR-5.1 First-Run Experience
- A new user MUST be able to go from running the install command to sending their first message in a channel in under **5 minutes**.

### NFR-5.2 Agent Creation Time
- Creating a basic agent (name, description, instructions, model selection) MUST take under **60 seconds** from start to first available use.

### NFR-5.3 Discoverability
- All primary actions (create channel, create agent, create tool, settings) MUST be reachable within **2 clicks** from the main view.

### NFR-5.4 Error Messaging
- Error messages MUST describe what went wrong in plain language and suggest a corrective action. Raw stack traces MUST never be shown to the user in the UI.

---

## NFR-6: Platform Compatibility

### NFR-6.1 Operating Systems
- The system MUST run on: **Linux** (x86_64, aarch64), **macOS** (Apple Silicon, Intel), **Windows** (x86_64).

### NFR-6.2 Browser Support
- The web UI MUST function correctly in: **Chrome 120+**, **Firefox 120+**, **Safari 17+**, **Edge 120+**.

### NFR-6.3 Python Runtime
- The system MUST support **Python 3.11 and 3.12**. Python 3.10 support is desirable but not required.

---

## NFR-7: Observability

### NFR-7.1 Logging
- The system MUST log all agent invocations, dispatcher decisions, sync operations, and errors to structured log files.
- Logs MUST include timestamps, correlation IDs (thread ID, agent ID), and severity levels.

### NFR-7.2 Cost Tracking
- Per-agent token consumption and estimated cost MUST be tracked and viewable in the UI.
- Workspace-level cost aggregates MUST be available (daily, weekly, monthly).

### NFR-7.3 AgentOS Tracing
- AgentOS tracing MUST be enabled and traces accessible via the AgentOS Control Plane or direct API query.

---

## NFR-8: Maintainability

### NFR-8.1 Configuration as Code
- All workspace configuration (agents, teams, channels, tools) MUST be serializable to a version-controllable format (YAML).
- The system MUST support exporting and importing workspace configurations.

### NFR-8.2 Upgrade Path
- Agent Hive version upgrades MUST preserve all user data (agents, threads, settings) without manual migration.
- Breaking changes MUST be documented with migration scripts when unavoidable.

### NFR-8.3 AgentOS Version Compatibility
- The system MUST declare a minimum and maximum compatible AgentOS/Agno version. Upgrading AgentOS within the compatible range MUST not break Agent Hive.
