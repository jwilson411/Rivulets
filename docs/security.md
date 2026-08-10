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
     denied by default.
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
- **Auth:** sessions use a bearer JWT in the `Authorization` header, held in browser memory (not `localStorage`/`sessionStorage`) — never an ambient cookie, so there's no CSRF-style attack surface to defend against.
- **File permissions:** everything under `~/.rivulets/` (the database, keys, config, logs) is created with a restrictive umask so it's readable only by the owning user.
- **Sandboxed code execution:** the Code Execution tool runs under `firejail --private=<dir> --private-tmp --private-dev --caps.drop=all --seccomp`, with `--net=none` unless network access is explicitly allowed. On macOS, `sandbox-exec` provides the equivalent restriction. If neither is available on the host, the tool refuses to run rather than executing unsandboxed.
- **Outbound request filtering:** the built-in `http_request` tool blocks requests to loopback, private, link-local, and other reserved IP ranges — including on redirect hops — since an agent's outbound requests can be driven by synced or otherwise untrusted content. This closes off SSRF against the node's own localhost services and LAN.

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
| An invited human's device is later compromised | Invite-grant sessions are owner-gated out of provider credentials, backups, sync control, and further invites; they never gain P2P mesh membership, so there's nothing to exfiltrate beyond that session's own JWT | An attacker with that JWT can act as that human within the un-gated surface (channels, messages, agents) until the token expires; the owner can't remotely invalidate a single already-issued session token before then. |
| A peer falsely self-reports a high capability score to win coordinator election (#101) | None beyond the existing peer trust boundary — every peer in a workspace already shares the same trust level (Layer 3's PSK gates *mesh membership*, not per-peer trust within the mesh), and the coordinator role only owns specific workspace-singleton actions, never a broader privilege over other peers' data or requests | A peer that's already inside the mesh could always publish false `node_capabilities`/other synced state; a false coordinator claim is the same category of self-reported, unverified broadcast, not a new capability an attacker gains. `POST /sync/coordinator/reclaim` (the human override) is owner-gated, same as `/sync/connect`/`/sync/disconnect`. |

## Reporting a vulnerability

Please report security issues through [GitHub's private security advisory form](https://github.com/jwilson411/Rivulets/security/advisories/new) rather than a public issue.
