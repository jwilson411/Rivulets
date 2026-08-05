# Rivulets — Security Architecture & Risks

---

## Security Architecture

### Defense in Depth

Rivulets' security model assumes the attacker may have network access but NOT local machine access. If the attacker has local machine access, they already own the filesystem and keychain — there is no defense against a root-level attacker on the same machine.

```
Layer 1: Localhost Binding
  └─ Web UI and App Server bind to 127.0.0.1 only (NFR-3.4).
     No external network interface. Attacker must be on the same machine.

Layer 2: Workspace Key Authentication
  └─ JWT-based session auth. Workspace key (BIP-39 mnemonic) required for initial login.
     JWT signed with HKDF-derived key (not the raw workspace key).

Layer 3: AgentOS Security Key
  └─ AgentOS configured with auth_mode: security_key.
     App Server authenticates to AgentOS using workspace-key-derived secret.
     AgentOS API not exposed to the network (internal port, or same process).

Layer 4: P2P Encryption (libp2p noise)
  └─ All sync traffic encrypted with workspace key as PSK.
     AES-256-GCM. Per-message nonces. Forward secrecy via noise handshake.

Layer 5: Tailscale/WireGuard (WAN sync only)
  └─ Additional network-layer encryption for cross-network traffic.
     Independent of workspace key encryption — defense in depth.

Layer 6: Credential Isolation
  └─ LLM provider keys stored in OS keychain, never in DB.
     Provider keys excluded from sync payloads.
     Credential references (not values) stored in SQLite.

Layer 7: Sandboxed Code Execution
  └─ Code execution tool runs in firejail (Linux) or equivalent.
     Filesystem access restricted to workspace directory.
     Network access denied by default.
```

### Key Derivation Hierarchy

```
BIP-39 Mnemonic (12 words)
  └─ PBKDF2 (2048 rounds) → BIP-39 Seed (512 bits)
       ├─ HKDF (info="workspace-key") → Workspace Key (256 bits)
       │    ├─ bcrypt → stored hash (for local login verification)
       │    ├─ HKDF (info="agentos-auth") → AgentOS Security Key
       │    ├─ HKDF (info="jwt-signing") → JWT Signing Key (HS256)
       │    └─ HKDF (info="p2p-psk") → libp2p PSK
       └─ (Optional BIP-39 passphrase applied before seed derivation)
```

All derived keys are computed at login time and held in memory only. They are never written to disk. The workspace key hash (bcrypt) is the only persistent derivative.

### Threat Model Summary

| Threat | Mitigation | Residual Risk |
|---|---|---|
| Attacker on same LAN intercepts sync traffic | libp2p noise encryption with workspace key PSK | Attacker with workspace key can decrypt. Key must be kept secret. |
| Attacker accesses machine physically | OS filesystem permissions (0600 on key material). OS keychain for provider keys. | Root-level attacker can read memory or keychain. Out of scope. |
| Malicious tool code reads user files | firejail sandbox restricts to workspace directory | firejail escape vulnerability. Pin version, monitor CVEs. |
| XSS in web UI reads JWT | JWT in memory (not localStorage). CSP headers. Svelte's compile-time XSS protection. | DOM-based XSS via dependency vulnerability. Regular npm audit. |
| Workspace key lost | BIP-39 mnemonic + passphrase model provides human-writable recovery. No server-side recovery. | User loses both mnemonic and passphrase = permanent workspace loss. |
| MITM on LLM provider API calls | HTTPS enforced. App Server → Provider APIs use TLS. | Provider API key compromise via TLS vulnerability. Standard risk for all API consumers. |
| CSRF on state-changing endpoints | FastAPI CSRF middleware. SameSite=Strict cookies. | None if CSRF protection is correctly configured. |
| Agent infinite loop costs money | Guardrails: turn limit, cycle detection, timeout. All configurable. | Agents could still consume tokens within limits before being paused. Acceptable risk. |
| Sync replay attack | libp2p noise handshake provides forward secrecy. Per-message nonces prevent replay. | None if noise protocol is correctly implemented. |

---

## Risks & Mitigations

### R-1: AgentOS API Instability
**Risk:** Agno ships a breaking API change that requires significant Rivulets rework.
**Likelihood:** Medium (Agno is pre-1.0).
**Impact:** High (core runtime broken).
**Mitigation:**
- Pin `agno>=0.3,<1.0` with exact version in lockfile.
- CI smoke test: create an agent, run it, verify streaming works — on every PR.
- Monitor Agno changelog and release notes.
- Contingency: if Agno becomes unmaintained, we can fork or wrap the AgentOS API surface. The App Server's abstraction layer (ADR-001) limits blast radius.

### R-2: Single Binary Size Bloat
**Risk:** The bundled Python runtime + all deps produces an unacceptably large binary (>200MB).
**Likelihood:** Medium.
**Impact:** Low (user downloads once; disk space is cheap).
**Mitigation:**
- Use PyInstaller's `--exclude-module` to strip unused stdlib components.
- SvelteKit build is already small (~50KB gzipped for the JS bundle).
- If binary exceeds 150MB, investigate Nuitka (compiles to C, smaller output) or `uv`'s embedded Python.
- Acceptance threshold: <150MB compressed for Linux, <200MB for macOS/Windows.

### R-3: P2P Sync Merge Conflicts
**Risk:** User makes conflicting changes on two offline nodes (e.g., edits the same agent's instructions differently on laptop and desktop).
**Likelihood:** Medium (single user, but multi-machine).
**Impact:** Low (last-write-wins with conflict visibility).
**Mitigation:**
- Vector clocks provide deterministic last-write-wins.
- Conflicting changes surfaced in UI as "This was also changed on another device. View diff."
- User manually resolves by accepting one version.
- True CRDT merge (automatic) is out of scope for P0-P1 but can be added later.

### R-4: Dispatcher LLM Cost Overruns
**Risk:** User's routing rules are poorly generated, causing excessive LLM fallback and unexpected API costs.
**Likelihood:** Medium (particularly for new users who haven't tuned agent descriptions).
**Impact:** Medium (user pays their own LLM costs — surprise bills erode trust).
**Mitigation:**
- Cost tracking dashboard shows dispatcher vs. agent token consumption separately.
- "Dispatcher hit rate" metric visible in settings (goal: >80% deterministic).
- Warning in UI if fallback rate exceeds 50%.
- Tutorial/onboarding emphasizes writing good agent descriptions for routing.
- User can add manual routing rules to reduce LLM fallback.

### R-5: SQLite Concurrency Under Streaming Load
**Risk:** Multiple concurrent SSE streams (agent responses) + UI reads (message loading) cause SQLite "database is locked" errors.
**Likelihood:** Low (WAL mode handles concurrent reads).
**Impact:** Medium (UI stutters or errors during heavy agent activity).
**Mitigation:**
- WAL mode enabled by default.
- Use aiosqlite for async access — no thread pool contention.
- Connection pooling: one write connection, multiple read connections.
- If contention is observed, increase WAL `busy_timeout` to 5000ms.
- Worst case: move to a dedicated write thread with a queue. But WAL mode should suffice.

### R-6: Hierarchical Summarization Quality Loss
**Risk:** Meta-summaries of very long threads lose critical context, causing agents to give incorrect or redundant responses.
**Likelihood:** Low (most threads won't exceed a few hundred messages).
**Impact:** Medium (bad agent responses waste tokens and user trust).
**Mitigation:**
- Always keep the last 20 messages in full (configurable).
- Summarization prompt instructs the model to preserve: decisions made, open questions, assigned tasks, key facts.
- User-facing: "Context was summarized. Start a new thread for a fresh conversation." prompt when opening old threads.
- Quality monitoring: log summarization events, periodically review for degradation.

### R-7: Build Complexity for Cross-Platform Binaries
**Risk:** PyInstaller/Nuitka produce working binaries on Linux but fail on macOS (code signing, notarization) or Windows (DLL resolution).
**Likelihood:** High (cross-platform Python packaging is notoriously difficult).
**Impact:** Medium (delays P1 release beyond Linux-only).
**Mitigation:**
- P0 ships Linux binary only. P1 adds macOS and Windows.
- Use GitHub Actions matrix builds (ubuntu-latest, macos-latest, windows-latest) from day one for CI.
- Nuitka as fallback if PyInstaller proves unreliable on a platform.
- For macOS: avoid notarization initially (users right-click → Open to bypass Gatekeeper). Add notarization in a later release.
- For Windows: test on clean VM, not dev machine with Python installed.

### R-8: Dependency on Tailscale for Cross-Network Sync
**Risk:** Users who need cross-network sync must install and configure Tailscale (or equivalent WireGuard mesh). This adds friction.
**Likelihood:** Certain (it's a dependency for WAN sync).
**Impact:** Medium (reduces the "it just works" experience for multi-machine users).
**Mitigation:**
- Same-LAN sync works without Tailscale (mDNS discovery).
- Setup wizard detects if Tailscale is installed and offers to install/configure it.
- Documentation: clear setup guide with screenshots. 5-minute Tailscale setup.
- Long-term (P2+): investigate native NAT traversal via libp2p AutoNAT + circuit relay as an alternative for users who don't want Tailscale.

### R-9: User Expectation Mismatch (Agents Aren't Human)
**Risk:** Despite the "agents act like humans" design, users expect agents to be perfect and get frustrated when they misunderstand context, loop, or give wrong answers.
**Likelihood:** Certain (LLM limitations are inherent).
**Impact:** Medium (user satisfaction).
**Mitigation:**
- Agent "status" indicators: thinking, executing tool, waiting for handoff.
- Thread pause mechanism gives the user control when agents go off-track.
- Error messages are plain-language, not stack traces.
- Onboarding sets expectations: "Agents are AI teammates — they're good at focused tasks but may need guidance. You're always in control."
- /feedback command in threads to rate agent responses (future P2).
