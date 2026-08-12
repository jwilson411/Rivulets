# Claude Design prompt — Rivulets marketing site

Copy everything in the fenced block below into Claude Design as the brief
for generating the layout/visual design. It's self-contained (doesn't
require the other files in this folder to make sense), but it's derived
from them — if you change positioning/features/brand direction upstream,
update this prompt to match.

---

```
Design a marketing website for Rivulets, a local-first, Slack-like chat
workspace where teams of AI agents work alongside humans in channels —
autonomously monitoring conversations and jumping in when relevant, not
just responding to @mentions or webhooks. There is no cloud version: it
installs as a single process on the user's own machine, with no account,
no server, and no data leaving that machine by default (optional
peer-to-peer sync lets someone extend the same workspace to their own
other machines, encrypted, with no central relay).

AUDIENCE
Technical builders and small teams evaluating a dev tool: comfortable with
Docker, API keys, self-hosting, Slack-shaped mental models. Skeptical of
generic AI-marketing hype; trusts specificity and architecture detail over
adjectives. Not a consumer audience — don't dumb down technical concepts,
but do explain product-specific vocabulary (see terms below) on first use.

CORE MESSAGE / HERO
Headline direction: agents that participate in a channel on their own
initiative — not a chatbot you have to ask, and not a cloud service you
have to trust with your data. Something in the spirit of "A workspace
where your AI agents actually work — together, and with you" or "Slack for
humans and AI agent teams. No cloud required." — write 3-5 headline
options in this direction rather than committing to one.
Sub-headline should land two claims fast: (1) teams of AI agents
autonomously participate in chat channels, handing off to each other when
useful, and (2) it all runs on your own machine — no server, no account.

VISUAL IDENTITY — MUST MATCH THE EXISTING PRODUCT, NOT GENERIC SAAS
The product has an established, deliberate design language internally
called "process inks on paper" — carry it into the site rather than
designing a separate marketing aesthetic:
- Background: warm off-white "paper" (not pure white), warm near-black
  "ink" as the dark background — think matte paper stock, not screen-white.
- Text/ink color: near-black on light backgrounds, near-white on dark —
  never pure #000/#fff.
- Primary accent / interactive color: a teal-cyan (~#0088b0 family).
- Secondary accent, used specifically to mark "something dynamic/active is
  happening" (e.g. a hover state on a feature about agent handoffs or
  live routing): a magenta/pink (~#d6006c family).
- Tertiary accent, decorative only, never for buttons/links/chrome: a
  warm gold/yellow (~#edbb00).
- These three accent colors are meant to evoke CMY printing inks — cyan,
  magenta, yellow — laid down on paper, with the near-black "ink" text
  color standing in for the black printing plate. Lean into that print
  metaphor visually (subtle paper texture, ink-like color transitions)
  without becoming kitschy or literally skeuomorphic.
- Typography: a serif typeface throughout — headlines AND body copy, not
  just headlines. This is a deliberate departure from sans-serif-only dev
  tool sites and should read as confident/editorial, not old-fashioned.
  (The product uses "Source Serif 4"; use that or a very close match.)
- Corners/radii: small and sharp (1-4px range) — not the rounded-2xl
  soft-SaaS-card look. Corners should read closer to print/paper than to
  "soft app UI."
- Shadows: soft, low-contrast, warm-tinted — subtle depth, never glossy or
  neumorphic.
- Buttons/cards: flat, minimal, ink-on-paper — avoid gradients, glassmorphism,
  glowing borders, or neon accents.
- Explicitly avoid: blue/purple SaaS gradient hero backgrounds, glowing
  orbs, abstract neural-network line art, particle fields, big pill-shaped
  buttons, generic "AI product" iconography. This should not look like a
  generic AI startup landing page.
- Support both light and dark mode with distinct, intentional palettes for
  each (not one palette mechanically inverted).
- A logo mark exists: a dimensional, illustrated ribbon forming the letter
  "R," rendered as a twisting teal-to-blue gradient with a subtle carved
  texture — more illustrative/dimensional than the rest of the flat
  ink-on-paper palette. Treat it as a fixed asset (nav mark / favicon,
  maybe echoed once as a hero accent) rather than something to flatten to
  match the rest of the UI, and don't extend its glossy-ribbon style into
  buttons or cards elsewhere on the page.
- If a signature motion/illustration detail is wanted (e.g. under the hero,
  or animating a "message -> dispatch -> agent -> handoff" diagram), the
  on-brand direction is a flowing ink/liquid line animation — not
  fade-in-on-scroll or generic micro-interactions.

PAGE STRUCTURE (single primary page)
1. Nav: wordmark, anchor links (Features, Docs, GitHub), a persistent
   "Install" or "Get started" CTA. No login/pricing nav items — none exist.
2. Hero: headline + sub-headline, primary CTA ("Get started" -> install
   section), secondary CTA ("View on GitHub"). Hero visual should be a
   product screenshot mockup of the chat UI showing an agent responding
   without an explicit @mention, ideally with a second agent visibly
   handing off mid-thread — that single image communicates the core
   differentiator faster than copy can.
3. "How it works," 3-4 short numbered beats: create agents with their own
   instructions/model/tools -> group into teams, assign a team to a
   channel -> post a message, the dispatcher routes it and agents hand off
   to each other as needed, every branch becomes its own thread ("a
   rivulet") -> (optional 4th beat) sync the same workspace to another
   machine with a 12-word key, no server involved.
4. Differentiator cards (3-4): Autonomous dispatch (agents act on
   relevance, not just @mentions) / Real multi-agent teams with handoffs /
   Local-first, no cloud, nothing leaves your machine / Peer-to-peer sync
   across your own machines, no central server.
5. Feature-depth sections, grouped (not one giant undifferentiated grid):
   Chat & agents (autonomous dispatch, handoffs, threaded "rivulets",
   image/vision support, structured JSON output) / Automation (node-based
   workflows with a visual drag-and-drop canvas, nested workflows, run
   visualization, triggers via slash-command/agent/webhook/schedule) /
   Tools & extensibility (MCP servers, custom Python tools, per-tool
   permission scoping, built-in tools like sandboxed code execution and
   web search) / Governance & safety (human approval queue, spend budgets,
   tool-call audit log, agent version history with rollback, eval suites
   for regression testing, usage dashboards).
6. Local-first / security section with real detail, not a vague trust
   badge: localhost-only network binding by default, a 12-word recovery
   phrase as the one root credential (explicitly like a crypto seed
   phrase - no password reset if lost), provider API keys stored in the
   OS keychain and excluded from sync, sandboxed code execution, encrypted
   peer-to-peer sync traffic.
7. A short "built on a real agent runtime" credibility beat: built on
   Agno's AgentOS underneath (not a reimplemented agent runtime), plus a
   one-line tech stack callout (Python/FastAPI, SvelteKit, SQLite,
   libp2p) for the technically diligent reader.
8. Install section: tabs or stacked blocks for Quick install (one-line
   curl script), Docker/Docker Compose, and Build from source, each with a
   copy-pastable command block. Include a short note that first run asks
   for a 12-word workspace recovery phrase.
9. Footer: "Source-available under the Business Source License 1.1 - free
   for effectively any use, including production, except offering
   Rivulets as a hosted/managed service to third parties" with a link to
   the license, plus links to GitHub, docs, and the security policy. No
   newsletter signup, no customer logos, no pricing - none of that exists
   for this product and the page shouldn't imply it does.

TONE
Precise and architecture-first rather than hype-driven. Confident about
"no cloud" as a deliberate advantage, not an apology. Every claim should
sound like it's describing a specific mechanism, not a marketing
superlative. A little literary/editorial flourish in the hero copy is
on-brand (the product's own naming is a nature metaphor - channels are
streams, threads are "rivulets" that split off and rejoin) but feature-level
copy should stay plain, specific, and skimmable.

CONSTRAINTS
- No pricing page, no login/account UI, no "request a demo" CTA - none of
  those exist for this product.
- Every CTA routes to either the install section or the GitHub repo.
- Fully responsive; must look correct at mobile widths too, particularly
  the hero screenshot and the install command blocks.
- Support light and dark mode.
```

---

## Notes for whoever runs this

- If Claude Design wants reference images, provide `ui/static/logo.png`
  (the wordmark) and, ideally, a real screenshot of the channel UI (e.g.
  `.github/assets/readme-hero.png`) so the generated hero mock is
  grounded in the actual product rather than an imagined chat UI.
- The prompt intentionally asks for headline *options*, not a single
  locked headline — resolve that choice against
  [02-messaging-and-voice.md](02-messaging-and-voice.md) once you see
  what the design pass generates, rather than pre-committing.
- If Claude Design's output leans generic-SaaS despite the brief (rounded
  cards, sans-serif, gradient hero), that's a signal to push back
  explicitly in a follow-up turn citing the "process inks on paper"
  section — it's a strong enough departure from default AI-site output
  that it may need reinforcing once you see a first draft.
