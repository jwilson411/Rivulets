# Agent Hive — Security Hardening & Disaster Recovery

---

## Security Hardening

### Application-Level Hardening

#### Dependency Scanning
- **Dependabot** enabled on the repo: daily scans for Python (pip) and JavaScript (npm) vulnerabilities.
- **pip-audit** run in CI on every push: fails the build on critical/high CVEs.
- **npm audit** run in CI: fails on critical vulnerabilities. High vulnerabilities flagged as warnings.
- Pinned dependencies in `requirements.in` / `pyproject.toml` with hash checking (`--require-hashes` in pip-compile).

#### Binary Hardening
- PyInstaller builds with `--strip` to remove debug symbols.
- Nuitka builds (if used) compile to C, providing obfuscation by default.
- Linux binary: compiled on oldest supported glibc (Ubuntu 24.04) for maximum compatibility.
- macOS binary: code-signed with an Apple Developer ID (P1+). Notarized for Gatekeeper bypass (P2+).
- Windows binary: signed with an EV code signing certificate (P2+). Without signing, SmartScreen will flag it.

#### Static Analysis
- **ruff** with security rules enabled: `S` (bandit-compatible), `F` (pyflakes), `E/W` (pycodestyle).
- **pyright** in strict mode: catches type errors that could mask logic bugs.
- **svelte-check**: catches Svelte template errors.
- No `eval()`, `exec()`, or `__import__()` in application code (enforced by ruff S102, S301-S308).

#### Supply Chain Security
- SLSA Level 2 build provenance: GitHub Actions generates attestations for all release binaries.
- SBOM (Software Bill of Materials) generated for each release via `syft` in CI.
- All GitHub Actions pinned to commit SHA, not tags/branches.
- No third-party GitHub Actions from untrusted sources.

### Runtime Hardening

#### Process Isolation
- App Server, AgentOS, and Sync Engine run as separate OS processes (per deployment architecture).
- Agent code execution runs in `firejail` (Linux) with:
  - `--net=none` (no network, configurable)
  - `--private=~/.agent-hive/sandbox` (isolated filesystem, bind-mounted workspace dir)
  - `--caps.drop=all` (no Linux capabilities)
  - `--seccomp=firejail-default` (restricted syscall filter)
  - `--noprofile` (no Firefox profiles, reduces attack surface)
- macOS sandbox: `sandbox-exec` with a custom profile restricting file access and network.
- Windows: Job objects with restricted tokens + AppContainer isolation.

#### File Permissions
```
~/.agent-hive/
├── agent-hive.db         0600  (owner read/write only)
├── agent-hive.db-wal     0600
├── agent-hive.db-shm     0600
├── files/                0700  (owner full access)
├── tools/                0700
├── logs/                 0700
├── config.yaml           0600  (may contain derived keys)
├── backups/              0700
└── sandbox/              0700  (firejail private directory)
```

All directories and files created with `os.umask(0o077)` — group and other have zero permissions by default.

#### Memory Hardening
- Workspace key derived keys held in memory only, never swapped to disk (where `mlock` is available).
- Python `gc` disabled for key material (use `bytes` not `str`, zero out after use).
- JWT tokens stored in browser memory (JS variable), not `localStorage` or `sessionStorage`.
- Browser CSP headers: `Content-Security-Policy: default-src 'self'; script-src 'self'; connect-src 'self' http://localhost:8484`.

#### Network Hardening
- App Server binds to `127.0.0.1` only (NFR-3.4). Confirmed at startup — refuses to start if bound to `0.0.0.0`.
- CORS disabled (same-origin only — UI and API are on the same origin).
- CSRF protection via FastAPI's CSRF middleware.
- Rate limiting on login endpoint: 5 attempts per minute per IP (mitigates brute force).
- AgentOS internal port (7777) only accessible from localhost, enforced by AgentOS config.

### Key Management Hardening

- BIP-39 mnemonic entered only in the browser — never logged, never stored in plaintext.
- Derived keys computed in the App Server on first request, held in memory for session lifetime.
- `workspace.key_hash` (bcrypt, cost=12) is the ONLY persistent derivative of the workspace key.
- Provider API keys stored in OS keychain:
  - **Linux:** `libsecret` (Secret Service API — used by GNOME Keyring, KDE Wallet)
  - **macOS:** Keychain Services
  - **Windows:** Credential Manager
- Fallback: if keychain is unavailable, encrypt keys with a key derived from the workspace key and store in SQLite. This is less secure (decryptable by anyone with the workspace key) but ensures functionality.

---

## Disaster Recovery

### Failure Scenarios & Recovery

| Scenario | Impact | Recovery | RPO | RTO |
|---|---|---|---|---|
| **DB corruption** (power loss, disk error) | All workspace data lost | Restore from latest automatic backup (`~/.agent-hive/backups/`) | 24 hours (last daily backup) | 5 minutes (copy backup → restart) |
| **File store corruption** | Agent can't access files in threads | Files re-synced from peers on next connection. Metadata preserved in sync log. | Variable (last peer sync) | Automatic (on peer reconnect) |
| **Binary won't start** (bad update) | Agent Hive unavailable | Download previous version from GitHub Releases. Restore pre-upgrade backup. | 0 (pre-upgrade backup taken automatically) | 10 minutes |
| **Workspace key lost** | Cannot authenticate to workspace | If mnemonic stored: re-enter. If not: **permanent data loss.** No recovery possible. | — | N/A |
| **Provider key revoked** | Agents using that provider fail | Update key in Settings > Providers. Agents resume on next run. | 0 (agents don't lose state) | 2 minutes |
| **Peer node fails** (hardware failure) | Sync unavailable from that node | Other peers continue. Data on failed node lost if no other peer had it synced. | Variable (depends on sync recency) | N/A (node must be replaced) |
| **firejail escape** (security vulnerability) | Sandboxed code accesses host filesystem | Update firejail. Audit affected workspace directory. | — | — |
| **LLM provider outage** | All agent runs fail | Graceful degradation: agents marked unavailable. User can switch providers. | 0 | Provider-dependent |

### Backup Strategy

#### Automatic Daily Backup
- **Trigger:** First App Server start of each calendar day.
- **Method:** `VACUUM INTO '~/.agent-hive/backups/agent-hive-{YYYY-MM-DD}.db'`
- **Retention:** Last 7 daily backups. Oldest deleted automatically.
- **Verification:** After backup, run `PRAGMA integrity_check` on the backup file. Log result. Alert user if check fails.

#### Pre-Upgrade Backup
- **Trigger:** App Server detects a version change (stored version < binary version).
- **Method:** `cp agent-hive.db agent-hive.db-wal agent-hive.db-shm → backups/pre-upgrade-v{old_version}/`
- **Retention:** Last 5 pre-upgrade backups.
- **Rollback:** If new version fails to start 3 times, prompt user to restore pre-upgrade backup.

#### Manual Backup
- **Trigger:** User clicks "Backup Now" in Settings > System.
- **Method:** `VACUUM INTO <user-chosen-path>` or full file copy.
- **Export option:** YAML config export (agents, channels, teams, tools — no messages).

### Restore Procedure

#### From Automatic Backup
1. Stop Agent Hive.
2. `cp ~/.agent-hive/backups/agent-hive-2026-08-04.db ~/.agent-hive/agent-hive.db`
3. Delete WAL/SHM files: `rm ~/.agent-hive/agent-hive.db-wal ~/.agent-hive/agent-hive.db-shm`
4. Start Agent Hive. Migration check runs. Sync engine pulls any messages created since backup from peers.

#### From Peer Sync (New Machine)
1. Install Agent Hive on new machine.
2. Enter workspace key (mnemonic).
3. App Server creates fresh DB, connects to peers, initiates full state sync.
4. All agents, channels, threads, messages, files replicated from peers.
5. Provider keys must be re-entered (they don't sync).

### Data Integrity Verification

- **SQLite integrity check**: Run `PRAGMA integrity_check` on startup. If it fails, alert user and offer to restore from backup.
- **WAL checkpoint**: Run `PRAGMA wal_checkpoint(TRUNCATE)` on clean shutdown. Ensures WAL is flushed to the main DB file.
- **File hash verification**: Periodic sweep (weekly) verifies file content hashes match metadata. Reports corruption to user.
- **Sync conflict detection**: Vector clock comparison catches divergent state. Surfaced in UI, not automatically resolved.

### Business Continuity (for the project itself)

Since Agent Hive is open source (BSL → Apache 2.0):
- **Source code:** GitHub is the source of truth. CI artifacts are reproducible.
- **Build infrastructure:** GitHub Actions. If unavailable, builds can run locally via `scripts/build-all.sh`.
- **Distribution:** GitHub Releases. If GitHub is unavailable, binaries can be distributed via direct download from any web server.
- **Website:** Static site (SvelteKit adapter-static) hosted on GitHub Pages or any static host.
- **Domain:** `agent-hive.dev` (TBD). DNS managed separately from hosting.
- **Community:** GitHub Issues + Discussions. No proprietary infrastructure dependency.
