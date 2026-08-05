# Rivulets — Out of Scope & Open Questions

---

## Out of Scope (Explicitly)

The following are NOT in scope for the MVP (P0) or near-term (P1) releases.

### OOS-1: SaaS / Cloud-Hosted Version
Rivulets is decentralized by design. There will be no cloud-hosted SaaS offering where Rivulets Inc. runs servers. Users self-host on their own machines. A future cloud sync/hosting option is possible but not part of the current product vision.

### OOS-2: Mobile Native Apps
The P0-P1 scope includes a responsive web UI. Native iOS and Android apps are out of scope.

### OOS-3: Voice/Video Channels
Slack-like voice channels, video calls, and screen sharing are out of scope.

### OOS-4: Custom Agent Frameworks
Rivulets is built on AgentOS (Agno). Support for non-Agno agent frameworks is out of scope for P0-P1.

### OOS-5: Public Agent/Tool Marketplace
A marketplace where users share and discover agents, tools, and MCP servers is out of scope.

### OOS-6: Enterprise SSO / SAML / OIDC
Single sign-on integration for enterprise identity providers is out of scope.

### OOS-7: Usage-Based Billing / Metering
Rivulets is free and open source. Users pay their own LLM provider costs directly.

### OOS-8: AgentOS Control Plane Hosting
Rivulets will not host or resell Control Plane access.

### OOS-9: Real-Time Collaborative Editing
Google Docs-style simultaneous multi-user editing is out of scope.

### OOS-10: End-to-End Encrypted DMs
Per-message E2E encryption for direct messages is out of scope for P0-P1.

---

## Resolved Open Questions

### OQ-1: P2P Sync Protocol — RESOLVED
**Decision:** libp2p with the gossipsub pub/sub protocol for structured data sync. File sync via rsync-style delta transfer over the same transport. Cross-network connectivity handled by Tailscale/WireGuard (see OQ-8).
**Rationale:** libp2p provides encrypted transport, peer discovery (mDNS for LAN), and pub/sub messaging out of the box. It's used by IPFS and Ethereum — proven at scale. Tailscale handles the NAT traversal layer, so libp2p doesn't need its circuit relay component. The workspace key serves as the pre-shared key for libp2p's noise handshake. All sync traffic is doubly encrypted: Tailscale/WireGuard at the network layer + libp2p noise at the transport layer.

### OQ-2: Lightweight Dispatcher Model — RESOLVED
**Decision:** User-selectable with smart defaults based on their configured provider.
**Defaults:**
- If Anthropic is configured: Claude Haiku
- If OpenAI is configured: GPT-4o-mini
- If multiple: prefer the user's designated "default" provider
- User can override in workspace settings
**Additional providers will be added as supported.**
**Impact:** Settings UI needs a "Dispatcher Model" dropdown in workspace config.

### OQ-3: Routing Rule Storage Format — RESOLVED
**Decision:** JSON rule objects stored in the local database, with an in-memory keyword index for sub-millisecond matching.
**Structure per agent:**
```json
{
  "agent_id": "...",
  "rules": [
    {"type": "keyword", "pattern": ["database", "SQL", "schema"], "priority": 10},
    {"type": "regex", "pattern": "(?i)\\bpostgres\\b", "priority": 8},
    {"type": "semantic", "trigger": "database design help"},
    {"type": "always", "priority": 0},
    {"type": "mention_only", "priority": -1}
  ]
}
```
**Impact:** Simple JSON blobs in SQLite. In-memory index rebuilt on agent rule changes.

### OQ-4: Rivulet Summarization Strategy — RESOLVED
**Decision:** Hierarchical summarization.
**Approach:** When a rivulet exceeds the context limit, the system chunks the older messages into groups of 20, summarizes each chunk with a cheap model, then summarizes the summaries into a single rivulet-context block. This preserves more semantic detail than a flat "summarize everything older than N" approach. The most recent 20 messages always remain in full.
**Impact:** Two LLM calls per summarization event (cheap model, small payloads).

### OQ-5: File Sync Trade-offs — RESOLVED
**Decision:** Configurable with sensible defaults.
- **Default: eager on LAN, lazy on metered/WAN.** Detected automatically by network interface type.
- User can force eager or lazy globally or per-network in settings.
- Lazy sync: file metadata syncs immediately; file contents are fetched on first access by an agent on that node.
**Impact:** Settings UI needs file sync policy controls.

### OQ-6: AgentOS API Versioning — RESOLVED
**Decision:** Pin to a compatible version range. Upgrade on our schedule.
Rivulets declares `agno>=X.Y,<X+1.0` in its dependencies. Upgrades to new AgentOS versions are tested and released as Rivulets updates — not automatic. No CI against nightlies needed; we control the upgrade cadence.
**Impact:** Standard dependency pinning. No special infrastructure.

### OQ-7: Workspace Key Recovery — RESOLVED
**Decision:** BIP-39 mnemonic (12-word recovery phrase) + optional passphrase.
**Rationale:** Familiar pattern from crypto wallets. 12 words are easier to write down and verify than a 64-character hex string. The mnemonic encodes 128 bits of entropy. An optional user-supplied passphrase (BIP-39 passphrase) adds a 13th "word" for additional security — without it, the 12 words alone can't derive the key. This lets the user write the 12 words somewhere safe and keep the passphrase in their password manager. Losing both means losing the workspace permanently.
**Format:** The workspace key displayed to the user is the 12-word phrase, not a hex string. The hex key is derived internally.
**Impact:** Add BIP-39 library dependency. Setup wizard shows words + verification step.

### OQ-8: NAT Traversal — RESOLVED
**Decision:** Tailscale/WireGuard as the connectivity layer for cross-network peer sync.
**How it works:**
- Nodes on the same LAN discover each other via mDNS — zero config, no Tailscale needed.
- For nodes on different networks, users connect them via Tailscale (or any WireGuard-compatible mesh). Once on the same tailnet, nodes discover each other and sync.
- Rivulets detects Tailscale interfaces automatically and uses them for peer communication.
- Users who don't need cross-network sync don't need Tailscale at all.
**Rationale:** Tailscale/WireGuard is battle-tested, zero-config for users after initial setup, handles all NAT scenarios without us building traversal from scratch. It also provides an additional encryption layer on top of the workspace-key encryption.
**P1 delivers this.**
**Impact:** Tailscale (or equivalent WireGuard mesh) required for cross-network sync. Same-LAN sync works without it.

### OQ-9: UI Framework — RESOLVED
**Decision:** SvelteKit.
**Rationale:** Justin prefers Svelte or Solid. SvelteKit provides: compile-time optimization (smaller bundles), built-in SSR (useful for initial load), first-class streaming support (SSE for agent responses), and a simpler developer experience than React. Svelte 5 runes provide Solid-like reactivity. Solid.js is the fallback if Svelte proves insufficient.
**Impact:** Frontend team/structure designed around SvelteKit.

### OQ-10: Open Source License — RESOLVED
**Decision:** BSL (Business Source License) 1.1 → converts to Apache 2.0 after 4 years.
**Rationale:** BSL allows free use for non-production and small-scale use while reserving the right to monetize a cloud-hosted or managed offering. After 4 years, each release converts to Apache 2.0 (fully open source). This is the same model used by CockroachDB, Sentry, and HashiCorp (before their relicensing). It keeps the door open for a future "Rivulets Cloud" sync/hosting option without letting AWS or a competitor offer it first.
**Impact:** LICENSE file at repo root. Contribution requires CLA only if a managed offering is launched.
