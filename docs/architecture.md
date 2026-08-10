# Architecture

## Overview

Rivulets is a **local-first, peer-to-peer-synced, single-binary application** made of three tightly integrated layers:

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
│  Agent runtime — sessions, streaming, MCP, tracing         │
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

### What shaped this design

- **Local-first, zero cloud dependency.** Everything runs on your machine. There's no server to manage and no SaaS to trust — the web UI talks to a local API only.
- **AgentOS is the runtime, not something Rivulets reimplements.** Agent execution, session management, streaming, and MCP support all come from [Agno's AgentOS](https://github.com/agno-agi/agno). Rivulets is the UX and orchestration layer on top of it.
- **Offline resilience with eventual consistency.** Nodes work fully offline and reconcile when reconnected, which rules out any design where a central server is the source of truth.
- **The dispatcher is the critical path.** Every message hits it, so it has to be fast (a deterministic rule match resolves in milliseconds) and degrade gracefully if an LLM call is needed and unreachable.

### Component boundaries

| Component | Responsibility | Talks to |
|---|---|---|
| **Web UI** | Slack-like chat, agent/tool/channel management, streaming display | App Server via REST + SSE |
| **App Server** | Dispatch engine, rivulet management, tool registry, sync coordination, config export/import | AgentOS (in-process), SQLite, peer nodes (libp2p) |
| **AgentOS** | Agent execution, session state, streaming, MCP, tracing | LLM providers, App Server |
| **SQLite** | Persistent state for all workspace entities | App Server only |
| **Sync Engine** | P2P state replication, file transfer, conflict resolution | Peer sync engines (libp2p), SQLite |

### Coordinator election (#101)

Every peer runs fully autonomously — a human's message is dispatched and answered locally by whichever node received it, and sync exists to keep *data* eventually consistent across peers, not to make one node authoritative for *requests*. That doesn't change here. A handful of responsibilities are **workspace-singleton**, though: something that must happen on exactly one peer, not independently (and redundantly, or duplicately) on every peer — a cron-scheduled workflow firing once instead of N times if the schedule is ever synced across peers being the motivating case. Rather than each singleton feature inventing its own "who's responsible" answer, `sync/coordinator.py` + `sync/engine.py`'s election loop solve it once, centrally.

This is deliberately **not** a client-server promotion. A coordinator's only extra responsibility is owning specific workspace-singleton actions; every other read/write in the app still happens wherever it always did, on whichever peer received the request.

- **Capability scoring.** Each peer locally computes a coarse, stdlib-only score (CPU count, RAM, free disk, current load, and this session's own continuous uptime) and gossips it every heartbeat alongside its coordinator belief, over the same `_STATE_TOPIC` gossipsub channel entity sync already uses (a `coordinator_heartbeat` sentinel, handled the same way `node_capabilities` is — never touches `sync/apply.py`, no vector clock, latest-broadcast-wins).
- **Election.** Lightweight bully-algorithm style, not Raft/Paxos: the highest-scored currently-connected peer self-claims coordinator; every peer adopts a self-claim unconditionally if it carries a higher term, or (at an equal term, the simultaneous-election collision case) whichever candidate the shared `outranks()` tie-break total order picks — score first, peer ID string as the deterministic fallback. No quorum threshold: a single peer with no visible mesh becomes its own coordinator immediately.
- **Failover.** A coordinator's heartbeat going silent (missed heartbeats, or an immediate trigger on TCP disconnect) causes the remaining peers to re-elect among themselves — no special-casing the original coordinator.
- **Split-brain / term (epoch).** A strictly increasing term number resolves the case where a network partition lets two peers each believe they're coordinator: any peer that observes a higher term than its own adopts it and steps down, so the mesh converges to one coordinator once the partition heals, without ever needing every peer to agree in lockstep.
- **No automatic failback.** Once a new coordinator takes over, a returning higher-spec original peer does *not* automatically reclaim the role — a coordinator that flaps as the original blinks on and off is worse than a stable-but-suboptimal one. `POST /sync/coordinator/reclaim` is the explicit human-triggered override for when the spec difference actually matters enough to want it back.
- **Ephemeral, not synced.** Coordinator/term/score state lives only in `SyncEngine`'s memory, reset on every `stop()`/`start()` cycle — same treatment as `node_capabilities`, not a DB-backed `EntitySpec`. Consuming features (workflow schedules, trace retention, budget enforcement, etc.) check `GET /sync/coordinator`'s `is_self` before running their own singleton action; none of that consumption is wired up yet — this issue lands the coordination primitive itself.

### Why not microservices?

This is a single-workspace local application — even with multiple humans sharing that workspace, there's exactly one App Server/AgentOS/SQLite process group per node, not one per person. Splitting it into microservices would add deployment complexity — orchestration, service discovery, inter-service auth — for no benefit at this scale. The App Server, AgentOS, and SQLite run together as one process group.

Multiple humans can share a workspace: each browser session claims a display identity on top of the one real credential (the workspace recovery phrase), rather than getting a separate per-user login of its own. See [`security.md`](security.md) for how that claim fits into the auth model.

## Technology stack

### Core runtime

| Layer | Choice | Why |
|---|---|---|
| **Language** | Python 3.11+ | Required by the Agno SDK; one language for the app server and its tool ecosystem. |
| **App framework** | FastAPI | AgentOS is also FastAPI-based, so middleware patterns line up cleanly. Native async, SSE, and WebSocket support. |
| **Agent runtime** | Agno AgentOS | All agent execution goes through AgentOS — agent runs, sessions, streaming, MCP, and tracing come built in. |
| **ASGI server** | Uvicorn | Standard for FastAPI; serves both the App Server and AgentOS. |

### Data

| Layer | Choice | Why |
|---|---|---|
| **Primary DB** | SQLite (SQLAlchemy + aiosqlite) | Single-file, zero-config, and more than sufficient at single-user scale. WAL mode + fsync for durability. |
| **File storage** | Local filesystem (`~/.rivulets/files/`) | Files are content-addressed (SHA-256 as filename); metadata lives in SQLite. |
| **Vector clocks** | JSON column in SQLite | Per-entity clocks drive conflict resolution — last-write-wins with clock comparison is sufficient for single-user, multi-machine sync. |

### Frontend

| Layer | Choice | Why |
|---|---|---|
| **Framework** | SvelteKit (Svelte 5 runes) | Compile-time optimization keeps bundles small; built-in SSR for fast initial load; first-class streaming for agent SSE. |
| **Styling** | Tailwind CSS 4 | Utility-first, fast to iterate, small production bundles with purging. |
| **Real-time** | Server-Sent Events | AgentOS already streams via SSE; no WebSocket upgrade complexity needed for one-way streaming. |
| **State** | Svelte stores | Lightweight — no external state library needed for a single-page chat app. |

### Peer-to-peer sync

| Layer | Choice | Why |
|---|---|---|
| **Transport** | libp2p (noise handshake + gossipsub) | mDNS for LAN discovery; noise protocol for encrypted transport using the workspace key as a pre-shared key; gossipsub for pub/sub state-change events. |
| **NAT traversal** | Tailscale / WireGuard | Handles cross-network NAT scenarios and adds an additional encryption layer. Same-LAN sync works without it. |
| **File transfer** | Custom rsync-style delta over a libp2p stream | Content-hash comparison skips unchanged files; chunked transfer for large ones. |

### Tools

| Layer | Choice | Why |
|---|---|---|
| **Tool SDK** | Agno SDK (`agno.tools`) | Built-in tools, custom tools, and MCP-discovered tools all register through the same interface. |
| **Sandboxing** | firejail (Linux), sandbox-exec (macOS), job objects (Windows) | Platform-native sandboxing for the Code Execution tool. |

### Development & operations

| Layer | Choice | Why |
|---|---|---|
| **Package management** | uv | Fast, pip-compatible dependency resolution. |
| **Packaging** | PyInstaller | Bundles the Python runtime, dependencies, and the built SvelteKit UI into a single binary — no Python required on the user's machine. |
| **CI/CD** | GitHub Actions | Lint, type-check, and test on every push/PR; builds release binaries for Linux/macOS/Windows on tags. |
| **Testing** | pytest (server), Vitest (UI), Playwright (E2E) | Standard tooling for each layer. |
