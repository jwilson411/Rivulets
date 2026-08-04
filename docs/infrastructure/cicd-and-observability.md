# Agent Hive — CI/CD Pipeline & Observability

---

## CI/CD Pipeline

### Repository Structure

```
agent-hive/                      # GitHub repo: github.com/justin/agent-hive (TBD)
├── server/                      # Python App Server + AgentOS integration
│   ├── agent_hive/
│   │   ├── main.py              # Entry point + process supervisor
│   │   ├── app.py               # FastAPI app factory
│   │   ├── api/                 # REST API routes
│   │   ├── dispatch/            # Dispatcher engine
│   │   ├── sync/                # P2P sync engine
│   │   ├── tools/               # Built-in tool library
│   │   └── db/                  # SQLAlchemy models + Alembic migrations
│   ├── tests/
│   └── pyproject.toml
├── ui/                          # SvelteKit frontend
│   ├── src/
│   ├── static/
│   └── package.json
├── packaging/                   # PyInstaller/Nuitka configs
│   ├── linux.spec
│   ├── macos.spec
│   └── windows.spec
├── scripts/                     # CI helper scripts
│   ├── install.sh               # curl | sh install script
│   └── build-all.sh             # Local build helper
├── docs/                        # BA docs, architecture, infrastructure
├── LICENSE                      # BSL 1.1
└── README.md
```

### Branch Strategy

```
main          — stable, protected. Only merges via PR.
├─ develop    — integration branch. PRs target this.
│   ├─ feat/*   — feature branches
│   ├─ fix/*    — bug fix branches
│   └─ chore/*  — docs, deps, CI changes
└─ release/*  — release preparation branches (version bump, changelog)
```

**Protection rules on `main`:**
- Require 1 approving review.
- Require status checks to pass (lint, test, type-check).
- Require linear history (no merge commits — squash merge only).
- No direct pushes.

### CI Pipeline (GitHub Actions)

#### On Every Push to `develop` and PRs:

```
┌──────────┐    ┌───────────┐    ┌────────────┐    ┌────────────┐
│  Lint    │    │  Type     │    │  Unit      │    │  UI Tests  │
│          │    │  Check    │    │  Tests     │    │            │
│ ruff     │    │ pyright   │    │ pytest     │    │ vitest     │
│ prettier │    │ svelte-   │    │ + coverage │    │ + svelte   │
│          │    │ check     │    │            │    │ testing-lib│
└────┬─────┘    └─────┬─────┘    └─────┬──────┘    └─────┬──────┘
     │                │               │                  │
     └────────────────┴───────────────┴──────────────────┘
                              │
                    All must pass (parallel)
                              │
                    ┌─────────▼─────────┐
                    │  Build Check      │
                    │  (does it build?) │
                    │  pyinstaller --    │
                    │  dry-run          │
                    └─────────┬─────────┘
                              │
                    ┌─────────▼─────────┐
                    │  Integration Test │
                    │  (start app,      │
                    │   create agent,   │
                    │   post message,   │
                    │   verify response)│
                    └───────────────────┘
```

**Tools & Versions:**
| Check | Tool | Config |
|---|---|---|
| Python lint | ruff | `pyproject.toml` — strict rules, 100 char line length |
| Python format | ruff format | Same config |
| Type check | pyright | `pyrightconfig.json` — strict mode |
| UI lint | prettier | `.prettierrc` — Svelte plugin |
| UI type check | svelte-check | `svelte.config.js` |
| Python tests | pytest + pytest-asyncio + pytest-cov | 85% coverage minimum |
| UI tests | vitest + @testing-library/svelte | Component + store tests |
| E2E tests | Playwright | Runs against built binary on Ubuntu |

#### On Release Tag (`v*`):

```
┌─────────────────────────────────────────────────────┐
│  Tag push: v0.1.0                                      │
└──────────────────────┬────────────────────────────────┘
                       │
       ┌───────────────┼───────────────┐
       ▼               ▼               ▼
┌────────────┐  ┌────────────┐  ┌────────────┐
│ Build      │  │ Build      │  │ Build      │
│ Linux      │  │ macOS      │  │ Windows    │
│ (x86_64 +  │  │ (x86_64 +  │  │ (x86_64)   │
│  arm64)    │  │  arm64)    │  │            │
└─────┬──────┘  └─────┬──────┘  └─────┬──────┘
      │               │               │
      ▼               ▼               ▼
┌────────────┐  ┌────────────┐  ┌────────────┐
│ Smoke      │  │ Smoke      │  │ Smoke      │
│ Test       │  │ Test       │  │ Test       │
│ (start     │  │ (start     │  │ (start     │
│  binary,   │  │  binary,   │  │  binary,   │
│  health    │  │  health    │  │  health    │
│  check,    │  │  check,    │  │  check,    │
│  create    │  │  create    │  │  create    │
│  agent)    │  │  agent)    │  │  agent)    │
└─────┬──────┘  └─────┬──────┘  └─────┬──────┘
      │               │               │
      └───────────────┼───────────────┘
                      │
              ┌───────▼───────┐
              │  Create       │
              │  GitHub       │
              │  Release      │
              │  + upload     │
              │  all binaries │
              │  + checksums  │
              └───────┬───────┘
                      │
              ┌───────▼───────┐
              │  Update       │
              │  install.sh   │
              │  with new     │
              │  version      │
              └───────────────┘
```

### Smoke Test Script

Each platform build runs this smoke test:
```bash
# 1. Start the binary in background
./agent-hive &
PID=$!
sleep 5

# 2. Health check
curl -s http://localhost:8484/api/v1/health | grep '"status":"ok"'

# 3. Create an agent via API
curl -s -X POST http://localhost:8484/api/v1/agents \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"smoke-test","description":"Smoke test agent","instructions":"You are a test agent. Respond with OK.","model":"test:mock"}'

# 4. Verify agent exists
curl -s http://localhost:8484/api/v1/agents | grep 'smoke-test'

# 5. Cleanup
kill $PID
```

---

## Observability

### Logging Architecture

All three processes (App Server, AgentOS, Sync Engine) write JSON Lines logs to `~/.agent-hive/logs/`.

**Log levels:**
| Level | Usage |
|---|---|
| ERROR | Crash, data corruption, unrecoverable state. Pages the user (UI notification). |
| WARN | Graceful degradation, retryable failures, approaching limits. |
| INFO | Normal operations: agent created, dispatch decision, sync completed. |
| DEBUG | Detailed state: routing rule match details, sync message contents. Off by default. |

**Structured log fields (all logs):**
```json
{
  "ts": "ISO timestamp",
  "level": "ERROR|WARN|INFO|DEBUG",
  "component": "dispatcher|agentos|sync|api",
  "correlation_id": "thread_id or request_id",
  "agent_id": "if applicable",
  "msg": "Human-readable message",
  "extra": {}  // component-specific data
}
```

### Key Metrics (Emitted as Log Events, Aggregatable)

| Metric | Source | Description |
|---|---|---|
| `dispatch.decision` | Dispatcher | deterministic/llm/none, latency, agent matched |
| `dispatch.fallback_rate` | Dispatcher | % of messages using LLM fallback (rolling 100) |
| `agent.run.started` | App Server | Agent invoked, thread ID, model |
| `agent.run.completed` | App Server | Run ID, tokens used, cost, duration, status |
| `agent.run.failed` | App Server | Run ID, error type, error message |
| `thread.guard.paused` | Guard | Thread ID, reason (turn_limit/cycle/timeout) |
| `sync.peer.connected` | Sync Engine | Peer ID, address |
| `sync.peer.disconnected` | Sync Engine | Peer ID, reason |
| `sync.state.applied` | Sync Engine | Entity type, entity ID, source peer |
| `sync.conflict` | Sync Engine | Entity type, entity ID, local clock, remote clock |
| `storage.db.size` | App Server | DB file size in bytes (logged hourly) |
| `storage.files.size` | App Server | File store size in bytes (logged hourly) |

### UI-Accessible Metrics

The Settings > Usage dashboard shows:
- **Today:** messages sent, agents invoked, tokens used, estimated cost.
- **This week/month:** same aggregates.
- **Per agent:** runs, tokens, cost, success rate.
- **Dispatcher hit rate:** deterministic vs. LLM fallback %.

### Alerting (In-App, Not PagerDuty)

Since this is a desktop app, "alerting" means in-UI notifications, not external paging:

| Condition | Alert |
|---|---|
| Agent run fails | Toast notification: "Code Reviewer encountered an error. View details." |
| LLM provider unreachable | Sidebar warning icon on affected agents |
| Dispatcher provider unreachable | Banner: "Agent routing is degraded. Deterministic rules only." |
| DB approaching size limit | Toast: "Database is getting large. Consider compacting." |
| File store approaching limit | Toast: "File storage is 80% full. Manage files in Settings." |
| Peer disconnected unexpectedly | Toast: "Lost connection to home-desktop. Changes will sync when reconnected." |
| Conflict detected | Banner: "A conflict was detected. Review in Sync settings." |
| New version available | Banner: "Agent Hive v0.2.0 is available. Download now." |

### Tracing

AgentOS provides built-in tracing (enabled via `tracing=True` in AgentOS config). Traces are accessible via:
- **AgentOS Control Plane** (if user connects one — out of scope for us).
- **Direct API:** `GET /agentos/traces/{run_id}` — App Server proxies this to the UI.
- **Trace viewer UI:** A "View Trace" button on each run in the agent run history.

### Health Dashboard

The `/api/v1/health` endpoint (see Compute & Storage doc) powers a simple health dashboard in Settings > System:

```
System Health
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
App Server    ● Running (1h 23m)
AgentOS       ● Connected
Sync Engine   ● Connected (2 peers)
Database      42.3 MB (OK)
File Store    230 MB / 10 GB (2%)
Logs          8.2 MB (OK)
Memory        312 MB (OK)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Development Environment

### Local Dev Setup (One Command)

```bash
git clone https://github.com/justin/agent-hive
cd agent-hive
uv sync --dev
cd ui && npm install && cd ..
```

### Dev Server (Hot Reload)

```bash
# Terminal 1: App Server (hot reload via uvicorn --reload)
uv run uvicorn agent_hive.app:app --reload --port 8484

# Terminal 2: SvelteKit dev server (hot reload, proxies API to :8484)
cd ui && npm run dev -- --port 5173
```

Open `http://localhost:5173` for the UI with hot module replacement. API calls proxy to the App Server.

### Running Tests

```bash
# All Python tests
uv run pytest -n auto --cov=agent_hive --cov-report=html

# Specific test file
uv run pytest tests/test_dispatcher.py -v

# UI tests
cd ui && npm test

# E2E tests (requires built binary or dev servers running)
cd ui && npx playwright test
```
