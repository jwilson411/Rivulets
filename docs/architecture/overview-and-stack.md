# Rivulets — Architecture Overview & Technology Stack

## Architecture Overview

Rivulets is a **local-first, P2P-synchronized, single-binary application** consisting of three tightly integrated layers:

```
┌─────────────────────────────────────────────────────────┐
│                    Web UI (SvelteKit)                     │
│  localhost:8484 — Slack-like chat interface               │
│  SSE streaming, drag-and-drop, responsive                 │
└──────────────────────┬──────────────────────────────────┘
                       │ HTTP + SSE (localhost only)
┌──────────────────────▼──────────────────────────────────┐
│               Rivulets Application Server               │
│  FastAPI / Starlette (Python 3.11+)                       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐   │
│  │ Dispatch  │ │  Rivulet  │ │   Tool   │ │   Sync   │   │
│  │  Engine   │ │  Manager │ │ Registry │ │  Engine  │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐   │
│  │ Loop      │ │  Agent   │ │ Channel  │ │   Auth   │   │
│  │ Guard     │ │   CRUD   │ │   CRUD   │ │  Layer   │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘   │
└──────────────────────┬──────────────────────────────────┘
                       │ Internal API calls (localhost)
┌──────────────────────▼──────────────────────────────────┐
│                   AgentOS (Agno)                          │
│  FastAPI app — agent runtime, sessions, streaming, MCP    │
│  POST /agents/{id}/runs  •  SSE streaming  •  MCP mount  │
│  Session management  •  Tracing  •  RBAC (security_key)   │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│                 Local SQLite Database                     │
│  Workspace state, rivulets, messages, agent configs        │
│  File metadata, sync state (vector clocks)               │
└─────────────────────────────────────────────────────────┘

                        ═══ P2P Mesh ═══

   Node A ◄── libp2p + Tailscale/WireGuard ──► Node B
   Node A ◄── libp2p + Tailscale/WireGuard ──► Node C
   (gossipsub for structured data, rsync-delta for files)
```

### Architectural Drivers (What Shaped These Decisions)

1. **Local-first, zero cloud dependency.** Everything runs on the user's machine. No server to manage, no SaaS to trust. The web UI talks to a local API.
2. **AgentOS is the runtime, not something we build.** We don't reimplement agent execution, session management, streaming, or MCP. AgentOS does that. Rivulets is the UX and orchestration layer on top.
3. **Offline resilience with eventual consistency.** Nodes must work fully offline and reconcile when reconnected. This rules out any architecture where a central server is the source of truth.
4. **Dispatcher is the critical path.** Every message hits the dispatcher. It must be fast (deterministic path <50ms) and reliable (graceful degradation when LLM is unreachable).

### Component Boundaries

| Component | Responsibility | Communicates With |
|---|---|---|
| **Web UI** | Slack-like chat, agent/tool/channel management, streaming display | App Server via REST + SSE |
| **App Server** | Dispatch engine, rivulet management, tool registry, sync coordination, config export/import | AgentOS (API), SQLite (DB), Peer Nodes (libp2p) |
| **AgentOS** | Agent execution, session state, streaming, MCP, tracing | LLM providers (API), App Server (internal API) |
| **SQLite DB** | Persistent state for all workspace entities | App Server only (no direct AgentOS access) |
| **Sync Engine** | P2P state replication, file transfer, conflict resolution | Peer Sync Engines (libp2p), SQLite (to read/write sync state) |

### Why Not Microservices?

This is a single-user local application. Splitting into microservices adds deployment complexity (orchestration, service discovery, inter-service auth) with zero benefit. The App Server + AgentOS + SQLite trio runs as one process group. If Rivulets ever becomes a multi-user cloud offering, the Dispatch Engine and Sync Engine are the first candidates to extract into separate services — but that's not the product we're building today.

---

## Technology Stack

### Core Runtime

| Layer | Choice | Version | Rationale |
|---|---|---|---|
| **Language** | Python | 3.11+ | Required by Agno SDK. Single language for app server + tool ecosystem. |
| **App Framework** | FastAPI | 0.115+ | AgentOS is FastAPI. Using the same framework means shared middleware patterns, no impedance mismatch. Native async, SSE, WebSocket support. |
| **Agent Runtime** | Agno AgentOS | >=0.3,<1.0 | Per FR-1.7 — all agent execution goes through AgentOS. Provides agent runs, sessions, streaming, MCP, tracing out of the box. |
| **ASGI Server** | Uvicorn | 0.34+ | Standard for FastAPI. Handles both the App Server and AgentOS on different ports or path prefixes. |

### Data

| Layer | Choice | Rationale |
|---|---|---|
| **Primary DB** | SQLite (via SQLAlchemy + aiosqlite) | Single-file, zero-config, survives reboots. WAL mode + fsync per NFR-2.3. More than sufficient for single-user scale (NFR-4). Embedded — no separate database process. |
| **File Storage** | Local filesystem (~/.rivulets/files/) | Files are content-addressed (SHA-256 hash as filename). Metadata in SQLite. Simple, portable, syncable. |
| **Vector Clocks** | JSON column in SQLite | Per-entity clocks for conflict resolution (FR-9.6). No need for a dedicated CRDT library — last-write-wins with clock comparison is sufficient for single-user multi-machine. |

### Frontend

| Layer | Choice | Version | Rationale |
|---|---|---|---|
| **Framework** | SvelteKit | 2.x (Svelte 5 runes) | Justin's preference over React. Compile-time optimization = smaller bundles. Built-in SSR for fast initial load. First-class streaming support via SvelteKit's `stream()` for agent SSE. |
| **Styling** | Tailwind CSS | 4.x | Utility-first, fast to iterate, small production bundles with purging. Matches the "Slack-like" aesthetic with minimal custom CSS. |
| **Real-time** | Server-Sent Events (SSE) | — | AgentOS already streams via SSE. SvelteKit's `EventSource` integration is straightforward. No WebSocket upgrade complexity needed for unidirectional streaming. |
| **State** | Svelte stores (writable/derived) | — | Lightweight. Sufficient for a single-page chat app — no Redux/Zustand needed. |

### P2P Sync

| Layer | Choice | Rationale |
|---|---|---|
| **Transport** | libp2p (noise handshake + gossipsub) | Proven P2P framework. mDNS for LAN discovery. Noise protocol for encrypted transport (workspace key as PSK). gossipsub for pub/sub state change events. |
| **NAT Traversal** | Tailscale / WireGuard | Per OQ-8 decision. Handles all NAT scenarios. Doubles as an additional encryption layer. Same-LAN sync works without it. |
| **File Transfer** | Custom rsync-style delta over libp2p stream | Content-hash comparison to skip unchanged files. Chunked transfer for large files. Simpler than bundling bitswap. |

### Tool Ecosystem

| Layer | Choice | Rationale |
|---|---|---|
| **Tool SDK** | Agno SDK (`agno.tools`) | Tools are Agno-native. Built-in tools, custom tools, and MCP-discovered tools all register through the same Agno tool interface. |
| **Sandboxing** | firejail (Linux), sandbox-exec (macOS), job objects (Windows) | Platform-native sandboxing for the Code Execution tool (NFR-3.5). firejail is the primary target (Linux is the primary dev platform). |

### Development & Operations

| Layer | Choice | Rationale |
|---|---|---|
| **Package Management** | uv (pip-compatible, fast resolver) | Faster than pip, better dependency resolution. Single `uv sync` to install everything. |
| **Packaging** | Single binary via PyInstaller or Nuitka | Users shouldn't need Python installed. Single binary bundles Python runtime + all deps + SvelteKit build output. |
| **Version Control** | Git + GitHub | Standard. Monorepo: `rivulets/` with `server/`, `ui/`, `tools/` directories. |
| **CI/CD** | GitHub Actions | Test on push, build binaries for Linux/macOS/Windows on release tags. |
| **Testing** | pytest (server), Vitest (UI), Playwright (E2E) | Standard tooling. pytest with pytest-asyncio for async FastAPI tests. Playwright for browser-based acceptance tests (maps to AC-001 through AC-031). |
