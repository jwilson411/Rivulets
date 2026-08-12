# Recommended site structure

A single-page (or single-page + a few sub-pages) marketing site is the right
scope — this is a dev-tool install page with strong storytelling, not a
multi-product marketing site. Recommended structure below; adjust section
order to taste but keep the hero → problem/how it works → feature depth →
trust/security → install flow arc, since that mirrors how a skeptical
technical reader actually evaluates a local-first tool.

## Primary page (home)

1. **Nav** — Logo/wordmark, links to: Features (anchor), Docs (→ GitHub
   `docs/`), GitHub repo, Install (anchor or button). Keep it short; no
   mega-menu, no "Pricing" (nothing to price), no "Login" (no accounts).

2. **Hero**
   - Tagline (see 02-messaging-and-voice.md) + one supporting sentence.
   - Primary CTA: install command / "Get started" → install section.
   - Secondary CTA: "View on GitHub."
   - Visual: a real (or realistic mock) screenshot of the channel UI —
     sidebar, an active thread with a visible agent handoff or dispatch
     moment, composer. This product's core differentiator (autonomous
     participation) is hard to describe abstractly but easy to *see* — the
     hero visual should show an agent replying without a visible
     `@mention`, ideally with a second agent handing off mid-thread, since
     that's the single clearest illustration of what makes this different
     from "Slack + a bot."

3. **"How it works" — three-ish beat sequence**
   Short, scannable, mechanism-first (not feature-list):
   1. Create agents with their own instructions, model, and tools.
   2. Group them into teams, assign a team to a channel.
   3. Post a message — the dispatcher decides who responds, agents hand off
      to each other when relevant, and every thread becomes its own
      rivulet.
   Optionally a 4th beat: "Sync the same workspace to another machine with
   nothing but a 12-word key — no server involved."

4. **Core differentiator callouts** (3–4 cards, matches
   00-product-brief.md's differentiator list)
   - Autonomous dispatch
   - Real multi-agent teams + handoffs
   - Local-first / no cloud
   - P2P sync across your machines

5. **Feature depth section(s)**
   Group by the categories in 01-features.md, likely as tabs or stacked
   sections rather than one giant grid, since there's real depth here:
   - Chat & agents (dispatch, handoffs, rivulets, vision, structured output)
   - Automation (workflows, visual canvas, triggers)
   - Tools & extensibility (MCP, custom tools, built-ins)
   - Governance & safety (approvals, audit log, budgets, evals, versioning)
   Each section: 1 short paragraph + 2–4 bullet specifics + (ideally) a
   small screenshot or diagram of that surface.

6. **Local-first / security section**
   This deserves real estate, not just a footer badge — it's a top value
   prop for the target audience. Cover, at a level a technical skimmer can
   verify: localhost-only binding, workspace recovery phrase model, OS
   keychain for provider keys, sandboxed code execution, P2P encryption.
   Link out to `docs/security.md` for the reader who wants the full threat
   model — don't try to reproduce that whole document as marketing copy.

7. **"Built on a real agent runtime" / architecture credibility beat**
   Short section or aside: Agno's AgentOS underneath, FastAPI/SvelteKit/
   SQLite/libp2p stack, single-binary packaging. This is for the reader
   doing technical diligence before installing something that touches their
   API keys — a short, honest "here's what's actually running" beat builds
   more trust than more adjectives would.

8. **Install section**
   - Tabs or stacked blocks per install path: Quick install (curl script),
     Docker/Compose, Build from source, Windows (manual download note).
   - Copy-pastable commands, matching README.md exactly (don't
     re-derive/paraphrase install commands — copy them verbatim from
     `README.md` so the site never drifts out of sync with reality).
   - A note on the first-run flow (workspace recovery phrase) since it's
     an unusual first-run UX worth setting expectations for before someone
     installs.

9. **License / footer**
   - "Source-available under BUSL 1.1" with the one-line carve-out (free
     for any use except offering it as a hosted/managed service), link to
     LICENSE.
   - Links: GitHub repo, docs, security policy (private advisory form),
     issue tracker / contributing guide.
   - No newsletter signup, no social proof logos section — nothing in the
     codebase supports either; don't fabricate them.

## Optional secondary pages (only if scope allows)

- **/docs** — could simply deep-link to the GitHub `docs/` folder rather
  than duplicating content on the marketing site; duplicating
  `architecture.md`/`security.md` as marketing pages risks drift. If a
  docs page is built, treat it as a landing/index that links out, not a
  copy.
- **/changelog or /releases** — could link straight to GitHub Releases
  instead of maintaining a separate page.

Given the "no cloud, install-and-go" nature of the product, resist scope
creep into a full marketing site with blog/pricing/customers pages — those
don't have real content to back them right now, and empty/placeholder
sections undercut the credibility this audience is evaluating on.

## Navigation & CTA rules

- Every CTA is either "Install" (→ install section) or "View on GitHub" (→
  repo). There is no third kind of CTA available (no demo request, no
  contact sales, no trial).
- Keep the install command visible/sticky if feasible (e.g. in the nav or a
  persistent corner) — for this audience, "how do I run this" is the
  single most important piece of information on the page.
