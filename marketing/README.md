# Marketing docs — Rivulets website

This folder is the source-of-truth brief for building the Rivulets marketing
website. It was compiled by reading the actual codebase and docs (not
invented), so treat it as ground truth over any prior assumptions about what
Rivulets is or does.

**Audience for this folder:** an agent (or human) building the marketing
site, starting cold with no prior context on the product.

## Reading order

1. **[00-product-brief.md](00-product-brief.md)** — what Rivulets is, who
   it's for, the core pitch, and the differentiators. Start here.
2. **[01-features.md](01-features.md)** — the full feature set, grouped and
   written in marketing language, with the underlying mechanism noted so
   claims stay accurate.
3. **[02-messaging-and-voice.md](02-messaging-and-voice.md)** — value props,
   taglines, terminology glossary, tone guidelines, and claims to avoid.
4. **[03-site-structure.md](03-site-structure.md)** — recommended page/section
   architecture, nav, and what content goes where.
5. **[04-brand-and-visual-identity.md](04-brand-and-visual-identity.md)** —
   the product's existing visual language ("process inks on paper"), colors,
   type, motion, logo — so the marketing site feels like the same product,
   not a generic SaaS wrapper around it.
6. **[05-claude-design-prompt.md](05-claude-design-prompt.md)** — a
   ready-to-paste prompt for Claude Design (or an equivalent layout/design
   generation pass) that synthesizes everything above into one brief.

## Ground rules for whoever writes the site

- **Rivulets has no cloud product.** There is no sign-up, no hosted
  dashboard, no "start free trial." Every CTA on the site routes to
  installing the software or reading the docs on GitHub — never to an
  account flow, because none exists.
- **It is not "open source" in the OSI sense.** It's licensed under the
  Business Source License 1.1 (source-available, free for effectively all
  use, converts to Apache 2.0 after 4 years per release). Say
  "source-available," not "open source." See
  [02-messaging-and-voice.md](02-messaging-and-voice.md) for exact framing.
- **Don't invent features.** Everything in [01-features.md](01-features.md)
  is traceable to code, docs, or shipped PRs at the time this was written
  (2026-08-12). If the site needs a feature claim not covered here, check
  the codebase before writing copy for it.
