# Agent Hive — Compute Resources & Storage Strategy

> **Note:** Agent Hive runs on the user's hardware. This document defines minimum requirements, resource management, storage layout, and data lifecycle — not cloud provisioning.

---

## Minimum System Requirements

| Resource | Minimum | Recommended | Notes |
|---|---|---|---|
| **CPU** | 2 cores, x86_64 or ARM64 | 4+ cores | Agent runs are I/O bound (API calls), not CPU bound. Dispatcher is lightweight. |
| **RAM** | 4 GB free | 8 GB free | Python runtime + AgentOS + App Server: ~500MB idle. Peaks during agent runs (model context in memory). |
| **Disk** | 1 GB free (application) + 10 GB free (data) | 20 GB free | Binary ~85MB. DB grows with messages + file attachments. 10K messages ~= 50MB. |
| **Network** | Broadband internet | Same | Required for LLM API calls + P2P sync. Offline operation supported for local features only. |
| **OS** | Linux 5.15+, macOS 14+, Windows 11 | Same | Kernel support for firejail (Linux) or equivalent sandboxing. |

---

## Runtime Resource Management

### Memory Budget per Component

| Component | Idle | Under Load (1 agent run) | Under Load (5 concurrent runs) |
|---|---|---|---|
| App Server | ~80 MB | ~120 MB | ~200 MB |
| AgentOS | ~150 MB | ~300 MB | ~600 MB |
| Sync Engine | ~30 MB | ~50 MB | ~80 MB |
| SQLite cache | ~10 MB | ~30 MB | ~50 MB |
| **Total** | **~270 MB** | **~500 MB** | **~930 MB** |

Worst case (5 concurrent agent streams on a machine with 4GB free RAM) leaves ~3GB for the OS and browser. This is within the minimum requirement.

### CPU Usage

- Dispatcher: single-threaded regex/keyword matching — negligible.
- Agent runs: I/O bound (waiting on LLM API). CPU usage is minimal during agent execution.
- Summarization: brief CPU spike when generating thread summaries (cheap model API call, not local inference).
- Sync: CPU usage proportional to sync volume. Typically <5% CPU during incremental sync, 20-30% during initial full sync.

### Concurrency Model

The App Server uses Python `asyncio` with FastAPI's async handlers:
- **I/O-bound operations** (LLM calls, AgentOS API calls, DB queries): `async/await` — no thread pool contention.
- **CPU-bound operations** (regex matching in dispatcher, file hashing): run in a `ProcessPoolExecutor` (one process per CPU core, capped at 4) to avoid blocking the event loop.
- **Agent runs**: one async task per agent invocation. Up to 10 concurrent agent runs per node (configurable).

---

## Storage Strategy

### Database: SQLite

**Location:** `~/.agent-hive/agent-hive.db`

**Configuration:**
```
PRAGMA journal_mode=WAL;          -- concurrent reads
PRAGMA synchronous=NORMAL;        -- safe with WAL, faster than FULL
PRAGMA busy_timeout=5000;         -- 5s timeout on lock contention
PRAGMA cache_size=-64000;         -- 64MB page cache (in KB, negative = KB)
PRAGMA foreign_keys=ON;           -- enforce FK constraints
PRAGMA mmap_size=268435456;       -- 256MB memory-mapped I/O
```

**Growth projections:**
| Data | Storage per unit | At 1K msgs/day for 1 year |
|---|---|---|
| Thread metadata | ~200 bytes/thread | ~5 MB (365 threads) |
| Messages | ~2 KB/message (avg) | ~730 MB (365K messages) |
| Agent configs | ~5 KB/agent | ~500 KB (100 agents) |
| Routing rules | ~2 KB/agent | ~200 KB |
| Tool versions | ~10 KB/version | ~10 MB (100 tools, 10 versions each) |
| File metadata | ~500 bytes/file | ~50 MB (100K files) |
| Sync state | ~1 KB/peer | ~10 KB (10 peers) |
| **Total** | | **~800 MB/year** |

**Compaction strategy:**
- Auto-VACUUM on startup if DB is idle.
- `PRAGMA auto_vacuum=INCREMENTAL` — free pages are reclaimed gradually, not all at once.
- `PRAGMA optimize` run daily (via scheduler or on shutdown).
- Manual "Compact Database" button in settings for power users.

### File Attachments

**Location:** `~/.agent-hive/files/{hash[0:2]}/{full_hash}`

**Content addressing:**
- SHA-256 hash of file contents is the filename.
- First two characters of the hash prefix the directory (e.g., `ab/ab3f9c82a1...`).
- Prevents any single directory from having too many files.
- Deduplication: identical files (same hash) share the same storage — they're symlinked or hardlinked to the first instance.

**File lifecycle:**
1. User uploads file → content hash computed, file written to `files/`.
2. Metadata row created in `file` table (see data model).
3. On sync: metadata propagates via gossipsub. File contents transferred only if peer's hash differs.
4. On delete: file unlinked from `files/` only when no remaining `file` rows reference that hash.
5. Orphan cleanup: periodic sweep (weekly) removes files with no DB references.

**Size limits:**
| Limit | Value | Enforcement |
|---|---|---|
| Per-file max | 100 MB | Client-side validation before upload |
| Total file storage | Configurable, default 10 GB | Warning at 80%, reject uploads at 100% |
| Per-thread file count | Unlimited | — |

### Logs

**Location:** `~/.agent-hive/logs/`

| Log File | Rotation | Retention | Contents |
|---|---|---|---|
| `app.log` | Daily, 7 days | 30 days | App Server: dispatcher decisions, CRUD ops, errors |
| `agentos.log` | Daily, 7 days | 30 days | AgentOS: agent runs, session events, MCP connections |
| `sync.log` | Daily, 7 days | 30 days | Sync Engine: peer connections, state transfers, conflicts |

Log format: JSON Lines (one JSON object per line) for machine readability.
```json
{"ts": "2026-08-04T12:00:00Z", "level": "INFO", "component": "dispatcher", "thread_id": "...", "msg": "Deterministic match: agent=DBA, rule=keyword, matched=postgresql"}
```

### Backups

**Location:** `~/.agent-hive/backups/`

| Type | Frequency | Retention | Method |
|---|---|---|---|
| Automatic | Daily (on first start of day) | 7 days | `VACUUM INTO 'backups/agent-hive-{date}.db'` |
| Pre-upgrade | On version upgrade | 30 days | Full file copy of `.db` + `.db-wal` |
| Manual | User-triggered | User-managed | Export to user-chosen path |

Backups do NOT include file attachments (those are synced to peers and recoverable). Backups are NOT synced between nodes (each node manages its own).

### Export/Import

Per NFR-8.1, full workspace configuration can be exported/imported as YAML:
```yaml
# agent-hive-export.yaml
version: 1
exported_at: "2026-08-04T12:00:00Z"
workspace:
  name: "My Workspace"
  settings:
    guard.turn_limit: 10
    ...
agents:
  - name: "Code Reviewer"
    description: "..."
    instructions: "..."
    model: "deepseek:deepseek-chat"
    tools: ["web_search", "filesystem"]
    routing_rules: [...]
teams:
  - name: "Engineering"
    agents: ["Code Reviewer", "DBA"]
channels:
  - name: "engineering"
    team: "Engineering"
tools:
  - name: "my_tool"
    description: "..."
    source_code: "..."
```

Export excludes: messages, threads (data, not config), provider keys, sync state. Import creates entities if they don't exist, updates if they do (by name match).

---

## Local Resource Monitoring

The App Server exposes resource usage via the health endpoint:
```
GET /api/v1/health
{
  "status": "ok",
  "agentos": "connected",
  "resources": {
    "db_size_mb": 45.2,
    "file_store_size_mb": 230.5,
    "file_store_percent": 23.0,
    "log_size_mb": 12.1,
    "memory_mb": 310,
    "uptime_seconds": 86400
  },
  "peers": 2,
  "pending_sync_changes": 0
}
```

The UI displays a health indicator in the sidebar footer. Warnings appear when:
- DB exceeds 500MB (suggest compaction).
- File store exceeds 80% of limit.
- Any log file exceeds 100MB.
- Memory exceeds 1GB (suggest restart).
