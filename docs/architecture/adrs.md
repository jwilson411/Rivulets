# Rivulets — Architecture Decision Records

---

## ADR-001: AgentOS as Runtime (Not a Custom Agent Engine)

**Decision:** Rivulets uses Agno's AgentOS as its agent execution runtime. We do not build a custom agent runner, session manager, or streaming infrastructure.

**Rationale:**
- AgentOS already provides: agent runs (POST `/agents/{id}/runs`), session management, SSE streaming, MCP server mounting, tracing, RBAC, scheduler, and background execution.
- Building these from scratch would be 6-12 months of work for a team of 2-3 engineers. AgentOS gives us all of it for free.
- The Agno SDK's `Agent` class maps cleanly to our "create agent" UX — name, model, tools, instructions.
- AgentOS's built-in Slack/Telegram interfaces prove the pattern works.

**Alternatives considered:**
- **Custom agent engine:** Rejected. Massive scope. We'd be building AgentOS, not Rivulets.
- **LangGraph/CrewAI as runtime:** Rejected. These are agent frameworks, not runtimes. They don't provide session management, streaming APIs, MCP mounting, or RBAC out of the box. AgentOS can run LangGraph agents via its Multi-Framework BETA if we ever need it.
- **Direct LLM API calls from App Server:** Rejected. We'd have to build session persistence, streaming, tool execution, and MCP from scratch.

**Consequences:**
- **Gain:** 80%+ of the agent infrastructure is free. We focus on UX, dispatch, threading, and sync.
- **Trade-off:** Tight coupling to Agno's release cycle. Breaking changes in AgentOS require us to update. Mitigated by pinning a compatible version range (ADR-008).
- **Risk:** Agno is a relatively young project. If it's abandoned, we'd need to fork or replace it. Likelihood: low (active development, growing community). Impact: high.

---

## ADR-002: Single Binary, Local-First Deployment

**Decision:** Rivulets ships as a single binary (Python + deps + UI bundled) that runs entirely on the user's machine. The web UI connects to localhost. No cloud infrastructure.

**Rationale:**
- The product vision is explicitly decentralized (OOS-1). No servers, no SaaS.
- Single binary eliminates the "install Python, create a venv, pip install, configure..." onboarding friction. The 5-minute first-run target (NFR-5.1) is only achievable with a single download-and-run artifact.
- Localhost binding (127.0.0.1) satisfies the security requirement (NFR-3.4) — no network exposure by default.

**Alternatives considered:**
- **Docker container:** Rejected. Adds Docker as a prerequisite. Non-technical users don't have Docker. Single binary is simpler.
- **pip-installable package:** Rejected. Requires Python knowledge. Fails the 5-minute first-run target.
- **Electron desktop app:** Rejected. Adds ~200MB to binary size, Chromium security surface, and Electron update complexity. A localhost web app achieves the same UX with zero Electron overhead.

**Consequences:**
- **Gain:** Zero-infrastructure deployment. Works on air-gapped machines. No Docker, no Python, no npm.
- **Trade-off:** Cross-platform binary builds (Linux, macOS, Windows) add CI complexity. PyInstaller/Nuitka have platform-specific quirks that require per-platform testing.
- **Risk:** Binary size may be large (Python runtime + all deps). Estimated: 80-120MB compressed. Acceptable for a desktop tool in 2026.

---

## ADR-003: SQLite as the Only Database

**Decision:** SQLite (via SQLAlchemy with aiosqlite for async access) is the sole database. No PostgreSQL, no external DB process.

**Rationale:**
- Single-user, local application. SQLite handles the NFR-4 scale targets (100 agents, 1K messages/day, 10K messages in sync) with room to spare.
- Zero-configuration — no database to install, configure, or secure. Matches the "download and run" philosophy.
- WAL mode provides concurrent reads without blocking. Write volume is low (a few messages per minute, not thousands per second).
- SQLite is the most tested database in the world (used in every phone, browser, and OS). Reliability is proven.

**Alternatives considered:**
- **PostgreSQL:** Rejected. Requires installation and management. Vast overkill for single-user scale. Would add 15+ minutes to first-run experience.
- **DuckDB:** Considered for analytical queries on run history. Rejected as primary store — optimized for OLAP, not OLTP. SQLite's row-based storage is better for chat messages.
- **LiteFS/Cloud-synced SQLite:** Rejected. Adds a cloud dependency. Our sync is peer-to-peer, not cloud-replicated.

**Consequences:**
- **Gain:** Zero-config, zero-maintenance persistence. Backups are a file copy.
- **Trade-off:** No built-in vector search. If we later need semantic search over rivulet history, we'd add sqlite-vec extension or an embedded vector index.
- **Risk:** Database corruption from power loss. Mitigated by WAL + fsync on commit (NFR-2.3). SQLite's ACID guarantees are well-tested.

---

## ADR-004: SvelteKit with SSE for Real-Time Streaming

**Decision:** The web UI is built with SvelteKit (Svelte 5, runes mode) and receives agent responses via Server-Sent Events (SSE) from the App Server.

**Rationale:**
- Justin explicitly prefers Svelte over React (OQ-9). Svelte 5 runes provide fine-grained reactivity comparable to Solid.js.
- SSE is simpler than WebSockets for unidirectional streaming (server → UI). AgentOS already streams via SSE. No WebSocket upgrade handshake, no reconnection protocol to invent.
- SvelteKit's built-in `EventSource` handling in the browser, combined with Svelte stores, creates a clean reactive pipeline: SSE event → update store → UI re-renders.
- SvelteKit's adapter-static mode produces a fully static build that can be served by the FastAPI app (no separate Node.js server).

**Alternatives considered:**
- **React/Next.js:** Rejected per user preference.
- **Solid.js:** Strongly considered. Excellent reactivity model, smaller bundles. Rejected because SvelteKit's SSR + static adapter story is more mature for our use case (a localhost app that benefits from SSR for initial load).
- **HTMX:** Rejected per user preference. Also, SSE streaming with HTMX is clunky for token-by-token rendering.
- **WebSockets:** Rejected as primary streaming protocol. Adds bidirectional complexity we don't need (user messages go via REST POST). SSE is simpler and sufficient.

**Consequences:**
- **Gain:** Simple streaming architecture. Static build served from Python. Reactive UI with minimal boilerplate.
- **Trade-off:** SSE is HTTP/1.1 only (no HTTP/2 multiplexing). Not a problem on localhost. If we later need bidirectional streaming (e.g., collaborative typing indicators), we'd add WebSockets as a supplement.
- **Risk:** Browser SSE implementations cap connections per domain (usually 6). With multiple concurrent agent streams in different rivulets, we could hit this limit. Mitigation: multiplex multiple agent streams over a single SSE endpoint with event types, or raise to HTTP/2.

---

## ADR-005: Two-Stage Dispatcher (Deterministic → LLM Fallback)

**Decision:** The channel dispatcher uses a two-stage pipeline: (1) deterministic rule matching against all agents on the channel's team, then (2) if no rules match, an LLM-based fallback dispatcher evaluates the message against agent descriptions.

**Rationale:**
- Per FR-4.1. The two-stage design optimizes for the common case (deterministic match, <50ms) while handling edge cases (LLM fallback, <3s).
- Deterministic rules are generated once at agent creation time (via LLM) and stored as structured data. They cost nothing to evaluate at runtime — just in-memory keyword/regex matching.
- The LLM fallback only fires when no deterministic rules match. In a well-configured workspace, this should be rare (<20% of messages). Users can add explicit routing rules to reduce LLM dispatcher calls.
- Graceful degradation (NFR-2.4): if the LLM dispatcher provider is unreachable, the system falls back to deterministic-only and warns the user. No messages are lost.

**Alternatives considered:**
- **LLM-only dispatcher:** Rejected. $0.001-0.003 per dispatch × thousands of messages = significant cost. Also violates the <50ms latency target.
- **Deterministic-only:** Rejected. Can't handle semantic matches ("I'm worried my model is overfitting" → route to Data Scientist). Would require users to write perfect keyword rules.
- **Embedding-based semantic match:** Considered. Compute embeddings for the message, compare to agent description embeddings via cosine similarity. Rejected because it adds embedding model dependency and doesn't outperform LLM routing for ambiguous cases.

**Consequences:**
- **Gain:** Fast, cheap common path. Graceful degradation. Routing rules improve over time as users refine agent descriptions.
- **Trade-off:** The LLM dispatcher is an additional API call with its own cost and latency. Mitigated by it being the fallback, not the primary path.
- **Risk:** Poorly generated routing rules cause excessive LLM fallback. Mitigation: the rule generation prompt is tuned for high recall (catch what the agent should handle) with precision as secondary.

---

## ADR-006: libp2p + Tailscale for P2P Sync

**Decision:** Structured data (agents, channels, rivulets, settings) syncs via libp2p's gossipsub pub/sub protocol. Files sync via content-addressed delta transfer over libp2p streams. Cross-network connectivity uses Tailscale/WireGuard.

**Rationale:**
- libp2p provides: encrypted transport (noise handshake with workspace key as PSK), peer discovery (mDNS for LAN), pub/sub messaging (gossipsub for state change broadcasts), and stream multiplexing.
- Tailscale handles the hard problem (NAT traversal) so libp2p doesn't need to. Same-LAN sync works without Tailscale — mDNS discovery + direct TCP.
- The workspace key serves dual purpose: workspace identity + PSK for libp2p noise handshake. Nodes without the key can't join the mesh.
- gossipsub efficiently broadcasts state changes: Agent A on Node 1 updates an agent config → publish to topic `workspace/state` → all connected peers receive → apply to local DB.

**Alternatives considered:**
- **libp2p with built-in relay (no Tailscale):** Rejected per OQ-8 decision. Building and maintaining relay infrastructure adds operational complexity. Tailscale is battle-tested.
- **CRDT library (Automerge / Yjs):** Considered for conflict-free data types. Rejected because our conflict model is simple (single-user, last-write-wins with vector clocks, FR-9.6). Full CRDTs add complexity without proportional benefit.
- **Syncthing for everything:** Rejected. Syncthing is file sync, not structured data sync. We'd still need a state sync layer. Two sync systems is worse than one.

**Consequences:**
- **Gain:** Proven P2P stack. Encrypted transport by default. LAN and WAN modes with graceful transition.
- **Trade-off:** Tailscale is a dependency for cross-network sync. Users who don't need remote sync don't need Tailscale. libp2p adds ~10MB to binary size.
- **Risk:** gossipsub message ordering is not guaranteed. A state change could arrive out of order. Mitigation: vector clocks on every entity. If a message arrives with a clock older than current, it's discarded.

---

## ADR-007: Rivulet as the Unit of Agent Context

**Decision:** Each rivulet is an independent conversation context with its own AgentOS session ID. Agents invoked in a rivulet receive the full rivulet history (summarized when needed). The main channel feed shows only human messages + rivulet previews.

**Rationale:**
- Per FR-5.1 through FR-5.6. This models how Slack rivulets work — main channel is clean, rivulets contain the deep work.
- Tying each rivulet to a single AgentOS session ID means AgentOS handles session persistence automatically. We don't build session management.
- Hierarchical summarization (OQ-4) keeps agent context within token limits without losing semantic detail.
- Agents see each other's messages in the rivulet (FR-5.6) — this is critical for the "agents act like humans" design principle.

**Alternatives considered:**
- **Single session per channel:** Rejected. Mixes unrelated topics. Context grows unbounded. No natural "reset" point.
- **New session per agent invocation:** Rejected. Agents can't see each other's messages. Kills the multi-agent collaboration pattern.

**Consequences:**
- **Gain:** Clean information architecture. Natural context boundaries. AgentOS session management for free.
- **Trade-off:** Cross-rivulet context is not available to agents. An agent can't reference "what we discussed in the other rivulet." By design — rivulets are independent topics.
- **Risk:** Rivulets with hundreds of messages may hit token limits despite summarization. Mitigation: hierarchical summarization preserves key information. Users can start a new rivulet to reset context.

---

## ADR-008: Firejail for Code Execution Sandboxing

**Decision:** The Code Execution tool runs Python code in a firejail sandbox on Linux, with equivalent platform-native sandboxing on macOS and Windows. Network access is denied by default, configurable per-workspace.

**Rationale:**
- Per NFR-3.5. A tool that executes arbitrary Python is a remote code execution vector. Sandboxing is not optional.
- firejail is lightweight (~100KB), uses Linux namespaces + seccomp, and is widely available in package managers. It provides filesystem isolation (the tool only sees a workspace-bounded directory) and network namespace isolation.
- Docker/podman would also work but add a heavy dependency (container runtime). firejail is a single binary with no daemon.

**Alternatives considered:**
- **Docker/podman:** Rejected. Too heavy for a local app. Requires container runtime installation and daemon.
- **gVisor/nsjail:** Considered. More secure (user-space kernel), but more complex to configure. firejail is sufficient for our threat model (single-user machine, user's own code).
- **No sandboxing:** Rejected. Unacceptable risk. A malformed or malicious tool could read/write anywhere on the user's filesystem.

**Consequences:**
- **Gain:** Safe code execution. Configurable network access. Minimal dependency footprint.
- **Trade-off:** firejail is Linux-only. macOS and Windows need separate sandbox implementations (sandbox-exec and job objects, respectively). Cross-platform sandboxing is non-trivial.
- **Risk:** firejail escape vulnerabilities. Mitigation: pin firejail version, monitor CVEs, run with least privilege. The blast radius is limited to the workspace directory even on escape.

**Implementation update (2026-08-06):** Linux (firejail) and macOS (`sandbox-exec`) are wired up as decided above — see `tools/builtin/code_exec.py`. Windows is deliberately *not* implemented as "job objects": a job object alone only bounds process/resource usage, not filesystem or network access, so it can't actually satisfy NFR-3.5's deny-by-default network requirement or the filesystem-confinement guarantee on its own — that needs a restricted token plus Windows Filtering Platform firewall rules, which is unwritten, unscoped work. Until that lands, `execute_python` reports itself unavailable on Windows (surfaced via `GET /tools`' `available` field) rather than shipping a sandbox that doesn't enforce what it claims to.

---

## ADR-009: Hierarchical Summarization for Context Management

**Decision:** When a rivulet exceeds 80% of the target model's context window, older messages are chunked into groups of 20, each chunk is summarized by a cheap model, then the summaries are summarized into a single rivulet-context block. The 20 most recent messages always remain in full.

**Rationale:**
- Per OQ-4. Hierarchical summarization preserves more semantic detail than a flat "summarize everything" approach. Each chunk summary captures local context; the meta-summary connects themes across the rivulet.
- A cheap model (the workspace's dispatcher model — Haiku or GPT-4o-mini) handles the summarization to keep costs low.
- Summarization is triggered proactively at 80% (not 100%) to prevent a single message from overflowing context mid-agent-run.

**Alternatives considered:**
- **Sliding window only (last N messages):** Rejected. Older context is lost entirely. An agent invoked late in a long rivulet has no idea what was discussed earlier.
- **Flat summarization (one summary of everything older):** Rejected. Loses detail. A 200-message rivulet summarized in one pass produces vague output.
- **RAG over rivulet history:** Considered. Embed all messages, retrieve relevant ones on each agent invocation. Rejected because it adds embedding infrastructure and latency to every agent run. Summarization is a one-time cost per threshold crossing.

**Consequences:**
- **Gain:** Agents retain context from the entire rivulet history, not just recent messages. Summarization cost is amortized (recomputed only when new messages push past the threshold again).
- **Trade-off:** 2-3 additional LLM calls per summarization event. At ~$0.001 per call, this is negligible (<$0.01 per rivulet lifecycle).
- **Risk:** Summarization quality degrades with very long rivulets (500+ messages). The meta-summary may lose important details. Mitigation: humans can start a new rivulet. Agents should be instructed to be concise.
