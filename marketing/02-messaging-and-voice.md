# Messaging, positioning & voice

## Positioning statement

For technical builders and small teams who want AI agents doing real,
ongoing work — not a single chatbot — Rivulets is a local-first, Slack-like
workspace where teams of AI agents autonomously participate in channels
alongside humans. Unlike cloud multi-agent platforms, Rivulets runs entirely
on your own machine(s), with no server, no account, and no data ever leaving
your control by default.

## Candidate taglines / heroes

Pick one voice and stay consistent; these lean different directions —
pragmatic vs. evocative — pick per the site's overall tone (see below).

- "A workspace where your AI agents actually work — together, and with you."
- "Slack for humans and AI agent teams. No cloud required."
- "Your agents. Your machine. Your data."
- "Multi-agent AI, without giving up your terminal — or your data."
- "The chat workspace where agents don't wait to be asked."

Supporting line (works under most of the above):
"Rivulets is a local-first workspace where teams of AI agents monitor your
channels and jump in when it matters — no `@mention` required, no cloud
account, no server to run."

## Value props, ranked

Use this order for a landing page's feature-grid or section order — it goes
from the most differentiated/emotionally resonant claim to the more
expected/table-stakes ones:

1. **Agents that participate, not just respond.** The autonomous-dispatch
   model is the single most differentiated thing about the product — most
   competitors require an explicit trigger (mention, webhook, slash
   command). Lead with this.
2. **Nothing leaves your machine.** Local-first architecture, not a privacy
   *policy* — API keys, conversations, and files never touch a Rivulets
   server, because there isn't one. This is a structural claim, not a
   promise, and should be written that way ("there is no server" beats "we
   don't store your data").
3. **Real multi-agent teams, not one assistant.** Distinct agents, distinct
   roles, handoffs between them, visible in one channel.
4. **Built on a real agent runtime.** Agno's AgentOS underneath signals
   engineering seriousness to a technical audience — this is a trust/depth
   point, good for a "how it's built" section or footer credibility line,
   not necessarily the hero.
5. **Automation that scales with you** — from ad hoc chat to saved
   workflows, schedules, webhooks, and evals.
6. **You're not locked in.** Source-available license, single binary,
   works with any provider you choose (including fully local models via
   Ollama) — no vendor lock-in on model choice or hosting.

## Terminology glossary (use precisely and consistently)

| Term | Definition | Copy notes |
|---|---|---|
| **Workspace** | The whole Rivulets instance/data set on one or more synced machines. | Not "account" or "org." |
| **Channel** | A Slack-like space with an assigned team of agents. | |
| **Rivulet** | A thread branching off a channel — Rivulets' name for a conversation thread. | This is the namesake term; worth a small callout somewhere on the site (e.g. an "about the name" aside), but don't force it into every sentence — it can read twee if overused. |
| **Team** | A group of agents assigned to a channel. | |
| **Agent** | A configured AI persona: instructions, model, tools, dispatch rules. | Not "bot." |
| **Dispatcher** | The routing engine that decides which agent(s) respond to a message. | |
| **Handoff** | An agent passing a conversation to a teammate mid-thread. | |
| **Workflow** | A saved, node-based automation, optionally slash-command-triggered. | Don't conflate with "channel" or "agent." |
| **MCP server** | An external Model Context Protocol server providing tools to agents. | Assume audience familiarity; don't over-explain MCP itself. |
| **Knowledge base** | A RAG data source an agent can be grounded in. | |
| **Workspace recovery phrase / workspace key** | The 12-word mnemonic that creates/unlocks a workspace and is the root credential for sync and (as fallback) key encryption. | Never call this a "password" — it's closer to a crypto-wallet seed phrase, and copy should treat it with that level of "write this down, there's no reset" seriousness. |
| **Invite** | A scoped, revocable link that lets a second human join without the recovery phrase. | |

## Tone & voice

- **Precise over hyped.** This product's differentiation is architectural
  and mechanical (how dispatch works, how sync works, what's sandboxed) —
  the copy earns more trust by being specific than by using superlatives.
  Prefer "agents route through deterministic rules first, then an LLM
  router" over "blazing-fast intelligent routing."
- **Confident about the local-first stance, not defensive about it.**
  Frame "no cloud" as an advantage and a deliberate architecture decision,
  not an apology for missing hosted convenience.
  - Do: "There is no Rivulets server. You install it, it runs on your
    machine, and that's the whole system."
  - Avoid: "We take your privacy seriously" (generic SaaS-privacy-policy
    voice; says nothing structural).
- **Technical audience, not explain-everything audience.** Fine to use
  "API key," "Docker," "MCP," "webhook," "SQLite" without a glossary aside.
  Do define Rivulets-specific terms (rivulet, dispatcher, workspace key) on
  first use per page.
- **Slightly literary is on-brand, not off-brand** — the product's own
  in-app design language leans into a print/ink metaphor and the name
  itself is a nature metaphor (streams, branching, rejoining). A *little*
  of that voice in hero copy is consistent with the product; don't let it
  take over feature-level copy, which should stay plain and specific.

## Claims to avoid / get right

- **Don't call it "open source."** It's the Business Source License 1.1:
  free for effectively any use, including production, *except* offering
  Rivulets (or a substantially similar product) as a hosted/managed service
  to third parties. It converts to Apache License 2.0 four years after each
  release. Say **"source-available"**, and if licensing gets its own
  section, state the hosted-service carve-out plainly rather than burying
  it — this audience will read the license.
- **Don't imply a hosted/cloud tier exists, now or "coming soon."** There is
  none in the codebase. No "sign up," no pricing page, no "start your free
  trial."
- **Don't overclaim the workflow engine.** It's node-based with a real
  visual canvas, but the execution engine is linear/MVP-stage underneath
  (see [01-features.md](01-features.md)) — say "visual, node-based
  automation," not "full branching/parallel workflow engine" or compare
  directly to mature workflow-automation products as if at parity.
  Wait to strengthen that claim's until the corresponding docs confirm.
- **Don't say "unlimited" or "no limits."** Turn caps, cycle detection, and
  spend budgets are explicit product features — the story is "safe
  autonomy with guardrails," not "no limits."
- **Windows/Intel Mac nuance:** native binaries currently cover
  Linux, Windows, and Apple Silicon macOS. Intel Mac users need Docker or
  a source build. If a platform-availability section/badge row exists on
  the site, get this right rather than saying "runs everywhere" flatly.
- **Security copy should mention the actual threat model**, not a vague
  "secure by design." The real model: protected against a network attacker
  without local machine access; not designed to resist an attacker who
  already has local/root access to the machine. If a security section goes
  deep, this nuance belongs there (see `docs/security.md`).
