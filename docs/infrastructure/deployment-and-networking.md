# Rivulets — Deployment Architecture & Packaging

> **Note:** Rivulets is a local-first desktop application with zero cloud infrastructure. This document covers binary packaging, distribution, installation, and local runtime architecture — not server deployment.

---

## Deployment Architecture

Rivulets runs entirely on the user's machine. There is no server-side component to deploy. The "deployment" is a single binary that bundles everything.

```
┌──────────────────────────────────────────────────────┐
│              Rivulets Binary (single file)          │
│                                                       │
│  ┌─────────────────┐  ┌────────────────────────────┐ │
│  │  Python 3.11     │  │  SvelteKit Build Output    │ │
│  │  Runtime         │  │  (static HTML/JS/CSS)      │ │
│  └─────────────────┘  └────────────────────────────┘ │
│  ┌─────────────────┐  ┌────────────────────────────┐ │
│  │  All pip deps    │  │  Built-in Tool Library     │ │
│  │  (agno, fastapi, │  │  (web_search, filesystem,  │ │
│  │   sqlalchemy,    │  │   code_exec, http, db)     │ │
│  │   libp2p, etc.)  │  │                            │ │
│  └─────────────────┘  └────────────────────────────┘ │
└──────────────────────────────────────────────────────┘
                           │
              User runs:   $ ./rivulets
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                  ▼
   ┌──────────┐    ┌──────────────┐    ┌──────────┐
   │ App Srv  │    │ AgentOS      │    │ libp2p   │
   │ :8484    │───▶│ :7777 (int)  │    │ daemon   │
   └──────────┘    └──────────────┘    └──────────┘
         │
         ▼
   ┌──────────┐    ┌──────────────┐
   │ SQLite   │    │ File Store   │
   │ WAL mode │    │ ~/.rivulets│
   └──────────┘    └──────────────┘
```

### Process Architecture

The binary spawns a **process supervisor** that manages three child processes:

| Process | Role | Restart Policy |
|---|---|---|
| **App Server** (FastAPI) | Serves UI, dispatcher, CRUD API, tool registry | Always restart (crash loop detection after 5 in 60s) |
| **AgentOS** (Agno FastAPI) | Agent execution, sessions, streaming, MCP | Always restart (same policy) |
| **Sync Engine** (libp2p) | P2P state sync, file transfer, peer discovery | Always restart (tolerates network errors) |

The supervisor is a lightweight async process manager. It handles:
- Process lifecycle (start, monitor, graceful shutdown)
- Crash recovery with backoff (1s, 2s, 4s, 8s, max 30s)
- Signal forwarding (SIGTERM → graceful shutdown → SIGKILL after 10s)
- Health checks on App Server (`GET /api/v1/health`)
- Port conflict detection and resolution

### Startup Sequence

1. Binary starts → supervisor spawns all three processes.
2. App Server starts → runs DB migrations → binds to `127.0.0.1:8484`.
3. AgentOS starts → loads agent configs → binds to `127.0.0.1:7777`.
4. Sync Engine starts → begins mDNS discovery for LAN peers.
5. Health check passes → binary prints `Rivulets ready: http://localhost:8484`.
6. User opens browser.

### Shutdown Sequence

1. SIGTERM received (Ctrl+C or OS shutdown).
2. Supervisor sends SIGTERM to Sync Engine (stop announcing, flush pending messages).
3. Supervisor sends SIGTERM to App Server (stop accepting new requests, drain in-flight).
4. Supervisor sends SIGTERM to AgentOS (cancel running agent runs, flush sessions).
5. Wait 10s. If any process still alive → SIGKILL.
6. Exit.

---

## Binary Packaging Strategy

### Toolchain Decision: PyInstaller (primary), Nuitka (fallback)

**Why PyInstaller:**
- Mature, well-documented, large community.
- Works on all three target platforms.
- `--onefile` mode produces a single executable.
- Handles binary dependencies (.so/.dylib/.dll) automatically.
- Fast build times (<2 min on CI).

**When to fall back to Nuitka:**
- If PyInstaller binary exceeds 200MB.
- If platform-specific issues arise (macOS code signing, Windows antivirus false positives).
- Nuitka compiles Python to C → smaller output, better performance, harder to reverse-engineer.

### Build Matrix

| Platform | Arch | Build Host | Output | Test Target |
|---|---|---|---|---|
| Linux | x86_64 | ubuntu-24.04 | `rivulets-linux-amd64` | Ubuntu 24.04, Fedora 40 |
| Linux | aarch64 | ubuntu-24.04-arm | `rivulets-linux-arm64` | Raspberry Pi 5 (Ubuntu) |
| macOS | x86_64 | macos-14 | `rivulets-darwin-amd64` | macOS 14 (Intel) |
| macOS | arm64 | macos-14 | `rivulets-darwin-arm64` | macOS 14 (Apple Silicon) |
| Windows | x86_64 | windows-2025 | `rivulets-windows-amd64.exe` | Windows 11 |

### Binary Contents

```
rivulets (single executable)
├── Python 3.11.x stdlib (stripped)
├── Site-packages:
│   ├── agno (AgentOS SDK)
│   ├── fastapi + starlette + uvicorn
│   ├── sqlalchemy + aiosqlite
│   ├── libp2p Python bindings
│   ├── cryptography (for key derivation, noise handshake)
│   ├── pyyaml (config import/export)
│   └── ... (transitive deps)
├── SvelteKit build output (static/)
├── Built-in tool library (tools/ directory bundled as Python package data)
├── Migration scripts (alembic/versions/)
└── Entry point: rivulets.main:main
```

### Size Budget

| Component | Estimated Size |
|---|---|
| Python runtime (stripped) | ~25 MB |
| All pip dependencies | ~45 MB |
| SvelteKit static build | ~2 MB |
| Built-in tools + migrations | ~1 MB |
| PyInstaller overhead | ~10 MB |
| **Total** | **~83 MB uncompressed, ~40 MB compressed (.tar.gz)** |

Acceptance threshold: <150 MB uncompressed, <80 MB compressed.

---

## Distribution Channels

### Primary: GitHub Releases
- Tagged releases trigger the full build matrix.
- Each release produces 5 binaries + SHA-256 checksums.
- Release notes auto-generated from merged PRs.

### Secondary: Package Managers (P1+)
- **Linux:** AUR (Arch), PPA (Ubuntu), COPR (Fedora)
- **macOS:** Homebrew cask
- **Windows:** Winget, Chocolatey

### Install Script (curl | sh pattern)
```bash
curl -fsSL https://get.rivulets.dev | sh
```
- Detects OS and arch.
- Downloads the correct binary from GitHub Releases.
- Verifies SHA-256 checksum.
- Places binary in `/usr/local/bin` (or `~/.local/bin`).
- Runs first-time setup wizard.

---

## Installation Directory Layout

```
~/.rivulets/
├── rivulets.db          # SQLite database (WAL mode)
├── rivulets.db-wal      # WAL file
├── rivulets.db-shm      # Shared memory file
├── files/                 # File attachments (content-addressed)
│   ├── ab/
│   │   └── ab3f9c...     # SHA-256 prefix → full hash filename
│   └── ...
├── tools/                 # Custom tool Python files
│   └── my_custom_tool.py
├── logs/
│   ├── app.log            # App Server logs
│   ├── agentos.log        # AgentOS logs
│   └── sync.log           # Sync Engine logs
├── config.yaml            # AgentOS configuration (generated, not user-edited)
└── backups/               # Automatic SQLite backups
    └── rivulets-2026-08-04T12:00:00Z.db
```

---

## Local Networking

### Port Allocation

| Port | Service | Binding | Purpose |
|---|---|---|---|
| 8484 | App Server | 127.0.0.1 | Web UI + REST API |
| 7777 | AgentOS | 127.0.0.1 | Agent execution API (internal) |
| 0 (dynamic) | Sync Engine | 0.0.0.0 + Tailscale interface | P2P communication |
| 0 (dynamic) | mDNS | 224.0.0.251:5353 | LAN peer discovery |

Ports 8484 and 7777 are configurable via workspace settings. The sync engine binds to a random port and announces it via mDNS + gossipsub.

### Port Conflict Handling
- On startup, check if configured ports are available.
- If 8484 is taken: try 8485, 8486, ... up to 8494. Log warning. Use first available.
- If 7777 is taken: try 7778, 7779, ... up to 7787. Internal only, no user impact.
- User can override in settings at any time.
