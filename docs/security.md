# Security

Rivulets runs entirely on your machine, but the web UI is still a network service and the workspace still syncs data over the network to other machines you own. This document covers the security model: what's protected, what isn't, and the threat model behind the design.

Rivulets' security model assumes an attacker may have network access but **not** local machine access. If an attacker already has local access, they own the filesystem and OS keychain — there's no defense against a root-level attacker on the same machine.

## Defense in depth

```
Layer 1: Localhost Binding
  └─ The web UI and App Server bind to 127.0.0.1 only.
     No external network interface — an attacker must be on the same machine.

Layer 2: Workspace Key Authentication
  └─ JWT-based session auth. The workspace key (a BIP-39 mnemonic) is required
     for initial login and remains the only real credential — the JWT is
     signed with an HKDF-derived key, never the raw workspace key. A logged-in
     session additionally claims a display identity (which human is posting)
     via its `human_id` claim, but that claim carries no independent
     authentication weight: it's a per-session label on top of the one
     workspace-level credential above, not a separate per-user login.

Layer 2b: Invite-Based Access
  └─ A second human can join without ever seeing the workspace mnemonic.
     The owner issues a scoped, revocable bearer secret (shown once);
     redeeming it mints a session-claim JWT the same shape as Layer 2's,
     but marked with an "invite" grant instead of "owner" — that grant is
     checked server-side on sensitive routes (provider credentials,
     backups, sync control, settings, further invites), which an
     invite-redeemed session can never reach. Invites are never P2P mesh
     credentials: redeeming one talks to the inviting node over plain
     HTTP/JWT, never touching Layer 3's libp2p pre-shared key, so an
     invited human's device never gains mesh membership. Redemption only
     works while the inviting node already has an unlocked session, since
     that's the only place the JWT signing key exists.

Layer 3: P2P Encryption (libp2p noise)
  └─ All sync traffic is encrypted with the workspace key as a pre-shared key.
     Per-message nonces provide forward secrecy via the noise handshake.

Layer 4: Tailscale/WireGuard (cross-network sync only)
  └─ An additional, independent layer of network encryption for sync traffic
     that leaves the local network.

Layer 5: Credential Isolation
  └─ LLM provider keys live in the OS keychain, never in the database.
     Provider keys are excluded from sync payloads entirely. When no OS
     keychain backend is available (headless/Docker installs, #118), keys
     fall back to a local encrypted-SQLite store, still excluded from the
     database and sync, but encrypted with a key derived from the
     workspace mnemonic instead of the OS keychain — the UI discloses
     this so the tradeoff (below) is informed, not silent.

Layer 6: Sandboxed Code Execution
  └─ The code execution tool runs inside firejail (Linux) or sandbox-exec
     (macOS), restricted to the workspace directory with network access
     denied by default. Unavailable on Windows and, by default, in the
     published Docker image (see below) — the tool reports itself
     unavailable and refuses to run rather than executing unsandboxed.
```

## Key derivation

```
BIP-39 Mnemonic (12 words)
  └─ PBKDF2 (2048 rounds) → BIP-39 Seed (512 bits)
       ├─ HKDF (info="workspace-key") → Workspace Key (256 bits)
       │    ├─ bcrypt → stored hash (for local login verification)
       │    ├─ HKDF (info="jwt-signing") → JWT Signing Key (HS256)
       │    ├─ HKDF (info="p2p-psk") → libp2p pre-shared key
       │    └─ HKDF (info="credential-store") → provider-key fallback
       │         encryption key (#118, used only without an OS keychain)
       └─ (optional BIP-39 passphrase applied before seed derivation)
```

All derived keys are computed at login time and held in memory only — they're never written to disk. The bcrypt hash of the workspace key is the only persistent derivative.

## Runtime hardening

- **Network:** the App Server refuses to start bound to anything other than `127.0.0.1` unless explicitly overridden. CORS is disabled — the UI and API share an origin. A `Content-Security-Policy` header restricts script and connect sources to `'self'`.
- **Auth:** sessions use a bearer JWT in the `Authorization` header, held in browser memory (not `localStorage`/`sessionStorage`) — never an ambient cookie, so there's no CSRF-style attack surface to defend against. The one deliberate exception (#350): an invite-redeemed browser persists an *invite resume token* in `localStorage` — not the JWT itself, but a per-redemption secret exchanged at `POST /invites/resume` for a fresh invite-grant session. Without it, a refresh or sign-out would permanently lock out an invited human, who has no mnemonic and can't re-redeem a spent single-use invite. The token is scoped (it can only ever mint `grant="invite"` sessions, never owner access), bcrypt-hashed at rest server-side, expires after 30 days idle, and dies when the owner revokes the invite it came from — and the invited browser already persists a comparable secret regardless, since the original invite URL sits in its history.
- **File permissions:** everything under `~/.rivulets/` (the database, keys, config, logs) is created with a restrictive umask (`0o077`, set once at process start in `main.py`) so it's readable only by the owning user. Directory and database file modes are also re-tightened to `0o700`/`0o600` on every startup (`Settings.ensure_workspace_dirs`), so an install predating this hardening gets fixed in place rather than only newly created ones.
- **Sandboxed code execution:** the Code Execution tool runs under `firejail --private=<dir> --private-tmp --private-dev --caps.drop=all --seccomp`, with `--net=none` unless network access is explicitly allowed. On macOS, `sandbox-exec` provides the equivalent restriction. If neither is available on the host, the tool refuses to run rather than executing unsandboxed. This includes Windows (no sandbox backend implemented yet) and the published Docker image (see "Code execution under Docker" below).
- **Outbound request filtering:** the built-in `http_request` tool blocks requests to loopback, private, link-local, and other reserved IP ranges — including on redirect hops — since an agent's outbound requests can be driven by synced or otherwise untrusted content. This closes off SSRF against the node's own localhost services and LAN.
- **API docs disabled:** `/docs`, `/redoc`, and `/openapi.json` are turned off (`docs_url=None` etc. in `app.py`). Every other route already sits behind the workspace JWT; this just removes unauthenticated surface with no product cost, since the API has no external integrators to document for.

### Code execution under Docker

The published Docker image does **not** install firejail, so the Code Execution tool reports itself unavailable under a stock `docker compose up` (it fails closed with `SandboxUnavailableError` rather than running agent-submitted code unsandboxed — same behavior as an unpatched Windows install).

This is deliberate, not an oversight: firejail needs to create its own mount/user namespaces, which needs `CAP_SYS_ADMIN` — a capability outside Docker's default capability bounding set. Installing the firejail binary into the image without also granting that capability would leave it present but non-functional. Adding `CAP_SYS_ADMIN` to every container by default, to support one opt-in tool, would weaken this image's baseline hardening for every install to benefit the minority that use Code Execution under Docker.

If you need Code Execution under Docker, you can opt in and accept that tradeoff yourself:

1. Extend this repo's `Dockerfile` with `RUN apt-get update && apt-get install -y --no-install-recommends firejail && rm -rf /var/lib/apt/lists/*` in the runtime stage.
2. Run the container with the extra capability and a permissive AppArmor profile, e.g. in `docker-compose.yml`:
   ```yaml
   cap_add:
     - SYS_ADMIN
   security_opt:
     - apparmor:unconfined
   ```

Only do this if you understand and accept that it grants the container a capability capable of far more than firejail alone (mount manipulation, namespace creation) — it's a real reduction in the container's isolation from the host, not a free unlock.

## Threat model summary

| Threat | Mitigation | Residual risk |
|---|---|---|
| Attacker on the same LAN intercepts sync traffic | libp2p noise encryption with the workspace key as PSK | An attacker who has the workspace key can decrypt — keep it secret. |
| Attacker gets physical access to the machine | OS filesystem permissions on key material; OS keychain for provider keys | A root-level attacker can read memory or the keychain directly — out of scope for any local application. |
| Workspace mnemonic is compromised, on an install with no OS keychain backend (#118) | The encrypted-SQLite provider-key fallback is only used when the OS keychain is unavailable; the UI discloses when it's active | Unlike the normal (keychain) case, the mnemonic now also decrypts provider API keys, not just workspace/sync access — an accepted tradeoff for keeping Docker/headless installs functional, not an oversight. Treat the mnemonic with the same care as your provider keys on those installs. |
| Malicious tool code reads user files | firejail/sandbox-exec restricts execution to the workspace directory | A sandbox escape vulnerability would defeat this — sandbox versions should be kept current. |
| XSS in the web UI reads the session token | JWT held in memory, not `localStorage`; CSP headers; Svelte's compile-time output escaping | A DOM-based XSS via a vulnerable dependency is still possible — keep dependencies current. |
| Workspace key is lost | BIP-39 mnemonic (+ optional passphrase) is human-writable and recoverable by the user | There is no server-side recovery. Losing both the mnemonic and passphrase means permanent loss of that workspace. |
| MITM on LLM provider API calls | HTTPS enforced for all provider API traffic | Standard risk shared by any API consumer; depends on TLS integrity. |
| An agent loop runs away and burns tokens | Turn limits, cycle detection, and timeouts, all configurable | An agent can still consume tokens up to the configured limit before being paused. |
| Sync replay attack | The noise handshake provides forward secrecy; per-message nonces prevent replay | None, assuming a correct noise protocol implementation. |
| An invite link is leaked or intercepted | Owner-configurable expiry and max-use count; owner can revoke at any time; the secret is bcrypt-hashed at rest, never stored or logged in plaintext | Between issuance and revocation, anyone with the link can join as an invite-grant session — treat an invite link with the same care as a one-time password. |
| An invited human's device is later compromised | Invite-grant sessions are owner-gated out of provider credentials, backups, sync control, and further invites; they never gain P2P mesh membership, so there's nothing to exfiltrate beyond that session's own JWT and the persisted invite resume token (#350) | An attacker with that JWT can act as that human within the un-gated surface (channels, messages, agents) until the token expires, and the resume token in `localStorage` lets them mint further invite-grant sessions until it idles out (30 days) — but revoking the invite kills the resume token on its next use, so the owner *does* have a remote kill switch for the persistent credential, unlike for an individual in-flight JWT. |
| A peer falsely self-reports a high capability score to win coordinator election (#101) | None beyond the existing peer trust boundary — every peer in a workspace already shares the same trust level (Layer 3's PSK gates *mesh membership*, not per-peer trust within the mesh), and the coordinator role only owns specific workspace-singleton actions, never a broader privilege over other peers' data or requests | A peer that's already inside the mesh could always publish false `node_capabilities`/other synced state; a false coordinator claim is the same category of self-reported, unverified broadcast, not a new capability an attacker gains. `POST /sync/coordinator/reclaim` (the human override) is owner-gated, same as `/sync/connect`/`/sync/disconnect`. |
| A node published beyond loopback (Docker, `8484:8484` or wider) is reachable before the owner's first login and an attacker races them to `POST /auth/login` with their own mnemonic (#247, #318) | `RIVULETS_REQUIRE_BOOTSTRAP_TOKEN` + `RIVULETS_BOOTSTRAP_TOKEN`: creating the workspace row while the former is set requires the request to carry a matching operator-set token. Deliberately not keyed on `app_server_host == "0.0.0.0"` directly — the Docker image always binds that internally, whether the host published it to loopback (default `docker-compose.yml`, no race possible) or the LAN, so that bind address alone can't tell the two apart (#318) | An operator who exposes the node beyond loopback without setting both vars can't complete first login at all (fails closed) until they set them — a deliberate inconvenience over the alternative of leaving the race open. The default loopback-only Docker publish needs neither var, matching a native install. |

Operational backups, restore, and login rate limits are documented in [`infrastructure/security-and-dr.md`](infrastructure/security-and-dr.md).

## Reporting a vulnerability

Please report security issues through [GitHub's private security advisory form](https://github.com/jwilson411/Rivulets/security/advisories/new) rather than a public issue. The repo-root [`SECURITY.md`](../SECURITY.md) is the GitHub-conventional pointer to that form.
