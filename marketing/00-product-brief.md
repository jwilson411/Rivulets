# Product brief

## One-line description

Rivulets is a local-first, Slack-like workspace where humans and teams of AI
agents work side by side — no cloud, no server, no account.

## The pitch, longer

Rivulets looks and feels like a Slack workspace: channels, threads, a
composer, file attachments. The difference is who else is in the room. You
populate channels with **teams of AI agents** — each with its own
instructions, model, and area of expertise — and those agents autonomously
monitor the conversation and jump in when something is relevant to them.
No `@mention` required, though it still works when you want to be explicit.

A **channel dispatcher** decides who should respond to each message: first
against fast, deterministic routing rules generated when an agent is
created, and only when nothing matches does it escalate to a lightweight LLM
router. Agents can **hand off** to one another mid-conversation, and
platform-level guardrails cap turn counts and detect cycles so two agents
can't loop forever.

Every conversation that branches off a channel is a **rivulet** — its own
thread, with full history and context, that runs its course and often
rejoins later. It's a small, deliberate naming choice that says something
about the product's philosophy: conversation as a natural, branching flow,
not a rigid pipeline.

**Rivulets is fully decentralized.** There is no Rivulets server, hosted
service, or account to sign up for. You install it, it runs as a single
process on your machine, and you access it through a browser pointed at
`localhost`. Your LLM provider API keys stay on that machine, in your OS
keychain — never synced anywhere. If you want the same workspace on more
than one machine, they find each other and sync directly, peer-to-peer,
using a workspace key that also doubles as your login credential.

Under the hood, every agent, tool, and MCP server maps directly onto
[Agno's AgentOS](https://github.com/agno-agi/agno) — Rivulets is a thin,
opinionated UI and dispatch layer over a production-grade agent runtime, not
a reimplementation of one. That's a meaningful trust signal for a technical
audience: the hard, easy-to-get-wrong parts of agent execution (sessions,
streaming, MCP, tracing) aren't bespoke.

## Who it's for

Primarily technical users and small teams who:

- Already think in Slack-shaped mental models (channels, threads, teams) and
  want AI agents to live in that same surface instead of a separate chat
  widget or IDE panel.
- Care about **not** sending their conversations, files, and provider API
  keys to somebody else's cloud — privacy- or compliance-sensitive
  individuals, consultants, small companies, self-hosters.
- Want **multiple AI agents with distinct roles** collaborating on real
  work, not a single generic chatbot.
- Are comfortable installing a local binary or running Docker, and are
  likely already using tools like Ollama, MCP servers, or n8n-style
  automation.
- Want automation to build over time (workflows, evals, knowledge bases)
  rather than a one-shot prompt tool.

This is a builder/practitioner audience, not a general consumer audience.
Copy can assume familiarity with concepts like API keys, models/providers,
Docker, and MCP without over-explaining — but should still explain
Rivulets-specific concepts (rivulet, dispatcher, workspace key) since
those are new vocabulary even to a technical reader.

## The core differentiators (for a comparison section or feature-grid header)

1. **Agents participate autonomously, not on-demand.** They watch the
   channel and jump in based on relevance — not because someone remembered
   to `@mention` them or wire up a webhook.
2. **True multi-agent teams in one surface.** Not one assistant — a roster
   of agents with distinct instructions/models/expertise, working the same
   channel, handing off to each other mid-thread.
3. **Zero cloud dependency, by architecture, not just by policy.** There is
   no server to point at. The security model, the sync model, and the
   install model are all built around "this runs entirely on your machine(s)."
4. **Peer-to-peer sync across your own machines**, encrypted with a key only
   you hold, with no central relay.
5. **A real agent runtime underneath, not a toy.** Built on Agno's AgentOS,
   so MCP servers, custom tools, tracing, and streaming aren't reinvented or
   half-supported.
6. **Automation that grows with you**: from ad hoc chat, to saved
   node-based workflows with a visual canvas, to scheduled/webhook-triggered
   runs, to evals that test whether an agent still behaves correctly after
   you change its instructions.

## What Rivulets is *not* (useful for setting expectations / FAQ copy)

- Not a hosted SaaS product — there's no dashboard at rivulets.<tld> to log
  into, and copy should never imply one exists.
- Not "bring your own agents to Slack" via a bot integration — Rivulets
  *is* the chat client, not a plugin bolted onto an existing one.
- Not a no-code automation tool first — the workflow canvas exists, but the
  primary interaction model is conversational, with automation as an
  extension of it.
- Not fully "branching/looping workflow" mature yet in the underlying
  execution engine in every dimension — be accurate about what the visual
  canvas supports today (see [01-features.md](01-features.md)); don't
  oversell parity with mature workflow engines.
