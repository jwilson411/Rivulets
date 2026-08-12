# Feature inventory

Organized for a marketing site: each group maps roughly to a feature-grid
card or a section. Each entry has a short marketing-ready line plus a
"how it actually works" note so copy stays accurate and specific rather than
generic AI-marketing fluff.

## 1. Chat-native multi-agent workspace

- **Channels & teams** — Organize agents into teams, assign a team to a
  channel, and every message in that channel is visible to the whole team.
  *(Mechanism: a channel has one assigned team; all agents on that team see
  channel messages.)*
- **Autonomous dispatch** — Agents respond based on relevance, not because
  someone remembered to tag them. Relevance is configured per agent as
  keyword/regex rules, semantic matching, or "always respond."
  *(Mechanism: the channel dispatcher first tries fast, deterministic rules
  generated at agent-creation time; only if nothing matches does it escalate
  to a lightweight LLM router — so routing is fast by default and smart when
  it needs to be.)*
- **Agent handoffs** — An agent can hand a conversation to a teammate
  mid-thread, with context carried over. The handoff itself shows up as a
  distinct visual event in the thread, not just another message.
- **Loop guards** — Turn-count caps and cycle detection stop two agents from
  volleying a conversation back and forth forever.
- **Threaded rivulets** — Every conversation that branches off a channel is
  its own persistent thread with full history and context management —
  Rivulets calls these "rivulets."
- **`@mention` still works** — Autonomous dispatch is the default mode, but
  explicit mentions are fully supported for when you want to address a
  specific agent directly.
- **File attachments & vision** — Attach files to any message; agents can
  read and act on them. Attached images are shown to agents directly (as
  images), not just described in text.
- **Structured output** — Agents can be configured to return JSON-schema-
  constrained output, for when a response needs to be machine-parseable
  downstream (e.g. feeding a workflow node).

## 2. Automation: workflows

- **Saved, reusable automations** — A workflow is a named, node-based
  automation a channel (or agent) can trigger. Nodes include: run an agent,
  apply a deterministic string transform, run an ad hoc summarization call,
  branch on a condition, or merge multiple inputs.
- **Visual canvas** — A drag-and-drop node editor (built on Svelte Flow):
  place nodes from a palette, draw and delete connections, author
  conditional/branching edges, build loops with a visible visit cap, use
  merge nodes for multi-input joins, and create an agent inline from the
  canvas without leaving the builder.
- **Nested workflows** — A workflow can invoke another workflow as a node,
  so complex automations can be composed from smaller, reusable ones.
- **Run visualization** — Watch a workflow run overlaid directly on its
  canvas as it executes, node by node.
- **Multiple ways to trigger a run**: a `/slash-command` typed in a channel
  (a workflow's name doubles as its trigger), an agent calling a
  `run_workflow` tool mid-conversation, an incoming webhook, or a schedule.
- **Retries and visible step output** — Each node supports a configurable
  retry policy; each node execution posts a visible step indicator into the
  thread, followed by its actual output, so a workflow run reads like a
  transparent, auditable trail rather than a black box.
- **Copy caution:** the execution engine is linear today (branching in the
  data model is designed-for but not yet a general-purpose branching/
  parallel engine) — don't claim full parallel/branching workflow-engine
  parity with mature tools. Frame it as "node-based automation with a visual
  canvas," not "a complete replacement for n8n."

## 3. Tools & extensibility

- **MCP servers** — Connect any MCP server (stdio or remote transport,
  including auth headers) and its tools become available to your agents
  automatically.
- **Custom tools in Python** — Write your own tools; a "simple mode" lets
  you describe a tool by name/description and have an agent draft the code,
  or write it yourself in "advanced mode."
- **Per-tool permission/scope model** — Fine-grained control over which
  tools an agent can use and what they're allowed to touch.
- **Built-in tools out of the box**, including: code execution (sandboxed),
  web search, HTTP requests (with SSRF protections), file/filesystem access,
  database queries, knowledge-base lookup, workflow triggering, schedule
  management, and channel-management actions.

## 4. Knowledge & memory

- **Knowledge bases (RAG)** — Give agents a knowledge base to ground their
  answers in your own documents/data rather than model memory alone.

## 5. Governance, safety & trust

- **Unified approval queue** — A single place to approve/deny pending
  actions gated behind human review: scheduled runs, spend budgets, and
  sensitive-tool guardrails.
- **Tool-call audit log** — Every sensitive tool call an agent makes is
  logged, with guardrails specifically for unattended (no human watching)
  runs.
- **Spend budgets** — Cap agent/token spend and require approval before
  crossing a threshold.
- **Agent version history & rollback** — Every change to an agent's
  instructions or model is versioned; roll back to a prior version if a
  change made things worse.
- **Evals** — Build eval suites (test cases + expected results) against
  agents or workflows, run them, and see pass/fail scoring — a way to catch
  regressions before they reach a live channel.
- **Usage dashboard** — Token/cost usage broken down by agent and by model.

## 6. Local-first architecture & security

- **No cloud, no account, no server to manage.** Rivulets installs as a
  single process on your machine and serves both the API and the web UI at
  `localhost`.
- **Localhost-only by default.** The web UI and API bind to `127.0.0.1` —
  reachable only from the machine it runs on, unless you deliberately
  reconfigure it.
- **Workspace recovery phrase as the only credential.** A 12-word BIP-39
  mnemonic is both how you create/re-enter a workspace and the root of key
  derivation for session auth, sync encryption, and (as a fallback) provider
  credential encryption. There's no password-reset flow — you keep the
  phrase, you keep the workspace.
- **Provider keys never leave the machine.** LLM provider API keys live in
  the OS keychain (or an encrypted local store as a fallback on headless/
  Docker installs, clearly disclosed in the UI when active), and are
  excluded from sync and from the database.
- **Sandboxed code execution.** The code-execution tool runs inside
  `firejail` (Linux) or `sandbox-exec` (macOS), scoped to the workspace
  directory with network access denied by default; if no sandbox is
  available, the tool refuses to run rather than executing unsandboxed.
- **Session auth without ambient cookies.** JWT session tokens live in
  browser memory, not `localStorage`, closing off CSRF-style attack surface.
- **Multi-human workspaces via scoped invites.** A second person can join a
  workspace without ever seeing the recovery phrase — the owner issues a
  revocable, expiring, single-purpose invite link instead. Invite-based
  sessions are gated out of sensitive surfaces (provider credentials,
  backups, sync control, settings, issuing further invites) and never gain
  peer-to-peer mesh membership.

## 7. Peer-to-peer sync (multi-machine)

- **No central server, ever.** Multiple machines on the same workspace key
  sync channels, agents, teams, and files directly with each other over an
  encrypted mesh.
- **Works offline, reconciles later.** Nodes function fully offline and
  reconcile state when they reconnect (vector-clock-based conflict
  resolution).
- **LAN and cross-network.** mDNS discovery on the same network; Tailscale/
  WireGuard support for syncing across networks, adding another encryption
  layer on top.
- **Coordinator election for singleton work.** Some responsibilities must
  happen on exactly one peer at a time (e.g., a scheduled workflow firing
  once, not once per synced peer). Peers elect a coordinator automatically
  based on capability, with automatic failover and no single point of
  failure — a technical-credibility detail worth a line in a "how it works"
  or engineering-audience section, not necessarily hero copy.

## 8. Model & provider flexibility

- **Bring your own provider(s).** Configure hosted providers (OpenAI,
  Anthropic, etc.) or self-hosted/local models (e.g. Ollama) — the UI groups
  them as hosted vs. self-hosted so the distinction (and its cost/privacy
  trade-off) is explicit rather than buried.
- **Per-agent model choice.** Every agent picks its own model — cheap/fast
  models for simple routing or high-volume agents, stronger models for
  agents doing harder reasoning.

## 9. Installation & deployment

- **Single binary, no runtime required.** One download for macOS
  (Apple Silicon), Linux, or Windows — no Python or Node.js needed on the
  user's machine.
- **One-line install script** with SHA-256 checksum verification.
- **Docker / Docker Compose** for containerized environments, with the same
  loopback-only exposure posture as a native install by default.
- **Build from source** for contributors/customizers (Python 3.11+/uv,
  Node 20+/npm).

## Feature list NOT to use

Nothing found in the codebase suggests: multi-tenant/hosted mode, mobile
apps, a plugin marketplace, or a paid tier. Don't imply any of these exist.
