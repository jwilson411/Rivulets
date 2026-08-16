# Security & Disaster Recovery

This is the operational half of the security story: backups, restore, and login rate limits. The threat model and defense-in-depth design live in [`../security.md`](../security.md); how to report a vulnerability is in the repo-root [`SECURITY.md`](../../SECURITY.md).

Everything here is node-local. Backups are recovery artifacts for **this** installation and are never synced to peers — another machine in the same workspace mesh keeps its own.

## Backups

### What a backup contains

A backup is a single tar archive in the workspace's `backups/` directory (`~/.rivulets/backups/` by default; the whole tree moves with `RIVULETS_WORKSPACE_DIR`). It bundles everything a restore needs to bring the workspace back to a consistent state:

| Member | What it is |
|---|---|
| `rivulets.db` | The main app database, snapshotted live via SQLite `VACUUM INTO` (safe against a running server — no raw file copy of a database mid-write). |
| `credentials.db` | The encrypted provider-key fallback store, when it exists (headless/Docker installs with no OS keychain, #118). Included so a restored `rivulets.db` never points at secrets that disagree with the fallback store. |
| `sync/` | This node's libp2p identity and capability tags. Small per-installation files, included so a workspace can be rebuilt on replacement hardware. |
| `tools/` | Custom tool source files (`{name}.py`), kept 1:1 with the `Tool` rows in `rivulets.db` (#290). |

Deliberately **not** included:

- **`files/` (user-uploaded attachments)** — can be arbitrarily large, which would make every automatic backup's cost scale with attachment volume instead of app-state size. Cover it with your regular filesystem backup practices; the UI's backup panel discloses this.
- **`agentos.db`** — AgentOS's own run/session history. On restore it is wiped and rebuilt from the restored `rivulets.db` rather than carried as potentially-disagreeing state.

### When backups are taken

| Kind | Filename prefix | Trigger | Retention |
|---|---|---|---|
| Daily | `rivulets-` | The first start of each day (UTC). The filename embeds the calendar date, so a second same-day start just overwrites that day's file. | 7 most recent |
| Pre-upgrade | `pre-upgrade-v{old}-` | The first time a new binary version starts against an existing workspace (a `.last_version` marker in `backups/` is compared on startup), taken **before** migrations run so it captures the true pre-migration state. | 5 most recent |
| Manual Backup | `manual-` | On demand: the UI's backup panel, or `POST /backups` (owner-only). | Kept until you delete the file |
| Pre-restore | `pre-restore-` | Automatically, immediately before any restore, so a restore is never a one-way door. | Kept until you delete the file |

There is no in-process scheduler: the daily and pre-upgrade triggers run at process startup. A node that stays up for a week takes its next daily backup on its next start — restart it (or take a Manual Backup) if you want a fresher snapshot.

### Integrity checks

Every SQLite file staged into an archive is verified with `PRAGMA integrity_check` as the backup is written. The policy on failure is to **alert user if check fails** and leave the bad file in place for inspection rather than silently deleting it: a manual backup returns an error to the caller, and a startup-time (daily/pre-upgrade) failure is logged without blocking startup — a backup failure must not become a new source of downtime.

## Restore

### From the UI / API

`POST /backups/{filename}/restore` (owner-only; the UI's restore panel drives it) restores in place, without a process restart:

1. A pre-restore safety snapshot of the current live state is taken first.
2. The archive's `rivulets.db` (and `credentials.db`, if present) are extracted and integrity-checked in a scratch directory **before** anything live is touched — a corrupt or truncated archive is rejected with the running workspace fully intact.
3. Each proven-good file is atomically swapped into place, stale `-wal`/`-shm` sidecars are removed, `sync/` and `tools/` are replaced from the archive, and `agentos.db` is wiped and rebuilt from the restored database.
4. The API requires the caller to echo the exact backup filename back as confirmation (`confirm_filename`), so a stray scripted retry can't confirm a different restore (#243).

One caveat: the sync engine reads this node's identity key once, at engine start, so a restored `sync/` identity only takes effect on the next process start. After restoring onto a running node, restart the process to be safe.

### Manually (dead node, replacement hardware)

The restore procedure is: stop, copy, delete WAL/SHM, start.

1. Stop the Rivulets process.
2. Extract the archive into the workspace directory (`~/.rivulets/` by default): `tar -xf backups/<name>.tar -C ~/.rivulets/`.
3. Delete any stale sidecars and derived state: `rivulets.db-wal`, `rivulets.db-shm`, `credentials.db-wal`, `credentials.db-shm`, and `agentos.db` (it is rebuilt from `rivulets.db` on startup).
4. Start Rivulets and log in with the workspace mnemonic.

Remember that `files/` attachments are not in the archive — restore those from your filesystem backups separately.

## Login rate limits

Rate limiting on login endpoint: 5 attempts per minute per IP (mitigates brute force). Every attempt counts toward the cap, not just failures — an attacker guessing mnemonics doesn't announce which guess will succeed, so gating only on failure would let a flood through right up to the one that works.

Each guessable-secret endpoint gets its own independent counter (exhausting one must not lock a legitimate user out of the others):

| Endpoint | Budget | Rationale |
|---|---|---|
| `POST /auth/login` | 5 attempts / minute / IP | Mnemonic brute-force mitigation. |
| Invite accept | 5 attempts / minute / IP | Invite-secret guessing, same reasoning (#15). |
| Invite resume | 30 attempts / minute / IP | The resume secret is 256 bits — unguessable at any rate — so this is flood protection for the bcrypt verify; invited browsers legitimately auto-resume on every page load (#350). |
| Webhook trigger | 30 attempts / minute / IP | The HMAC secret is likewise unguessable; this absorbs legitimately bursty senders such as CI (#99). |

The limiter is an in-memory sliding window inside the single App Server process, which is sufficient because the server binds to `127.0.0.1` only (NFR-3.4) — there is no multi-instance or load-balancer scenario needing shared state. Counters reset on process restart.
