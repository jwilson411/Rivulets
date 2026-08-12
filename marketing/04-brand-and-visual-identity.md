# Brand & visual identity

The product already has a deliberate, non-generic design language. The
marketing site should read as the same product, not a separate "marketing
skin" bolted on top — a technical audience will open the app five minutes
after visiting the site, and a mismatch (generic SaaS gradients on the site,
print/ink minimalism in the app) undercuts trust more than it might for a
consumer product. Everything below is pulled directly from
`ui/src/routes/layout.css` and the shipped logo, not invented.

## The existing design language: "process inks on paper"

That's the literal in-code description (`layout.css`, line ~20). The
concept: the product surface is styled like **paper**, and each agent is
assigned a **print ink** color in join order (cyan, magenta, yellow — the
CMY printing primaries), while the human user is the fixed **black ink
plate**. It's a subtle, coherent metaphor: agents are literally colored like
printing-press inks laid down on a page, and a "rivulet" thread is visually
a flowing line of ink.

Concretely:

- **Background:** warm off-white "paper" (`#f3f2f2` light / `#1b1a19`
  dark) — not pure white/black. Slightly warm, matte, papery.
- **Ink/text:** near-black (`#201e1d`) on light, near-white (`#eae7e7`) on
  dark — "ink," not pure `#000`/`#fff`.
- **Neutrals:** a warm gray ramp (50–950) used for surfaces, borders, and
  secondary text, all in the same warm-paper family (never cool/blue-gray).
- **Agent inks:**
  - **Cyan** (`#0088b0` family) — first agent ink, and doubles as the
    product's primary/interactive accent color (links, buttons, focus
    rings).
  - **Magenta** (`#d6006c` family) — second agent ink, and doubles as the
    dispatcher/handoff accent — i.e., the color that shows up specifically
    when a handoff or routing event is happening.
  - **Yellow** (`#edbb00` / `#8a6d00`) — third agent ink, explicitly
    reserved for print/illustrative treatment only — *not* used for chrome
    or UI copy/interactive elements. Treat it the same way on the marketing
    site: fine in an illustration, wrong as a button color.
- **Typography:** Source Serif 4 for essentially everything (`--font-serif`
  is the base body font, not just headlines) — a deliberate departure from
  the sans-serif-everywhere convention of most dev-tool marketing sites.
  This is a strong, ownable choice; the marketing site should use the same
  serif, at minimum for headlines, and ideally for body copy too, to feel
  continuous with the product rather than "here's our separate marketing
  font."
- **Radii:** small and sharp — 1px/2px/4px (`--radius-sm/md/lg`). Not the
  rounded-2xl SaaS-card look. Corners read closer to print/paper-adjacent
  than to "soft app UI."
- **Shadows:** soft, low-contrast, warm-tinted (shadow color mixes from the
  dark neutral, not pure black) — subtle depth, not glossy/neumorphic.
- **Spacing rhythm:** a 5px base unit with a 5·10·15·20·30·40 rhythm
  (replacing Tailwind's default 4px base) — described in code as a "print
  rhythm." Worth carrying into the site's spacing scale if the site is
  hand-built rather than templated, for visual continuity.
- **Motion:** a specific "ink flowing" animation (`spine-flowing`) —
  a dash-offset animation that reads as ink/liquid flowing along a line,
  used on rivulet "spine" connectors in the UI, respecting
  `prefers-reduced-motion`. If the marketing site wants a signature motion
  detail (e.g. an animated line under the hero, or in a "how it works"
  diagram tracing the message → dispatch → agent → handoff flow), this is
  the on-brand direction — flowing ink/stream, not generic
  fade-in-on-scroll.
- **Theming:** the product supports light/system/dark via a `data-theme`
  attribute plus a `prefers-color-scheme` fallback for "system." The site
  should support light and dark for the same reason, using the same token
  approach (explicit palettes for each, not one palette dimmed
  algorithmically).

## Logo

`ui/static/logo.png` is a dimensional, illustrated ribbon forming an "R,"
rendered as a twisting teal-to-blue gradient ribbon with a carved/etched
surface texture — closer to a crafted wordmark/icon than a flat vector
mark. It's noticeably more illustrative and dimensional than the flat,
minimal "process ink" palette used elsewhere in the product.

**Guidance for the site:** treat the logo as a fixed asset (nav mark,
favicon, maybe a hero accent) — don't try to flatten it to match the ink
palette, and don't try to extend its gradient ribbon style into the rest of
the site's UI chrome (buttons, cards, etc.), which should stay in the flat
process-ink language above. It's fine, and arguably good, for the mark to
be the one place on the page with dimensional shine — a small "ribbon of
water/ink" motif that echoes the rivulet/stream naming without turning the
whole site into a 3D-render aesthetic.

## Practical direction for whoever designs the site (or for the Claude
Design prompt)

- **Do:** warm paper background, near-black/near-white ink text, Source
  Serif 4 (or a very close serif) for headlines at minimum, sharp/small
  radii, cyan as the primary interactive color, magenta reserved for a
  specific "something dynamic is happening" accent (e.g., hovering a
  differentiator card about handoffs/dispatch), yellow used sparingly and
  only decoratively.
- **Don't:** default to a blue/purple SaaS gradient hero, big rounded
  cards/pill buttons, a sans-serif-only type system, or generic
  "AI product" iconography (glowing orbs, neural-net line art, abstract
  particle fields). Those would visually contradict the product it's
  advertising.
- **Diagram style, if the site includes a "how dispatch/handoff works"
  diagram:** favor a clean, editorial, almost technical-illustration style
  (think: a printed diagram in a well-designed manual) over a glossy
  isometric SaaS illustration — consistent with the print/ink metaphor and
  the serif typography.
