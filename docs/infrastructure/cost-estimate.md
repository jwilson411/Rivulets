# Agent Hive — Cost Estimate & Infrastructure as Code

---

## Project Infrastructure Costs

Agent Hive has **zero cloud hosting costs** — it's a desktop application. The costs below are for the open-source project infrastructure: source control, CI/CD, distribution, and web presence.

### Monthly Infrastructure Costs

| Service | Tier | Monthly Cost | Notes |
|---|---|---|---|
| **GitHub** (source + CI) | Free / Team | $0 – $4 | Free for public repos. Team plan ($4/user) if private repos needed. Included CI minutes: 3,000 min/month (private) or unlimited (public). |
| **GitHub Actions runners** | Included + self-hosted | $0 | Linux/macOS/Windows runners included. macOS is the expensive one (10x minute multiplier). Release builds use ~60 macOS min/month. Within free tier. |
| **GitHub Releases** (binary hosting) | Free | $0 | No bandwidth limits. Each binary ~40MB compressed × 5 platforms = 200MB/release. |
| **Domain** (`agent-hive.dev`) | Namecheap / Cloudflare | ~$1.25 | ~$15/year. Standard .dev TLD pricing. |
| **Website hosting** | GitHub Pages / Cloudflare Pages | $0 | Static site. Free tier is more than sufficient. |
| **Install script hosting** | Same as website | $0 | `get.agent-hive.dev` → static file on Pages. |
| **Total** | | **$0 – $5.25/month** | |

### One-Time / Annual Costs

| Item | Cost | Notes |
|---|---|---|
| **Apple Developer Program** | $99/year | Required for macOS code signing + notarization (P1+). Not needed for P0 (users right-click → Open). |
| **Windows EV Code Signing Certificate** | $250-500/year | Required for Windows SmartScreen trust (P2). Not needed for P0-P1 (users click "More info" → "Run anyway"). |
| **Domain registration** | ~$15/year | First year included in monthly above. |
| **Total** | **$114 – $614/year** | None required for P0. |

### User-Facing Costs (Not Paid by the Project)

Users pay their own LLM provider costs directly. Agent Hive does not proxy or resell API access.

**Typical user cost estimates:**

| Usage Profile | Monthly LLM Cost (Estimated) |
|---|---|
| **Light** (10 messages/day, Haiku/mini dispatcher, cheap agents) | ~$5-15/month |
| **Moderate** (50 messages/day, GPT-4o agents, moderate tool use) | ~$30-80/month |
| **Heavy** (200 messages/day, Claude Opus agents, heavy tool use, multi-agent threads) | ~$150-400/month |
| **Dispatcher only** (Haiku/mini, fires on ~20% of messages) | ~$0.50-2/month at any usage level |

These are rough estimates. Actual costs depend on provider, model, context length, and tool usage. Agent Hive's built-in cost tracking gives users per-agent and workspace-level visibility.

---

## Infrastructure as Code

Since Agent Hive has no cloud infrastructure, traditional IaC (Terraform, Pulumi) is not applicable. The "infrastructure" that IS managed as code:

### GitHub Repository Configuration

Managed via `.github/` directory in the repo:

```
.github/
├── workflows/
│   ├── ci.yml              # Lint, test, type-check on push/PR
│   ├── release.yml          # Build + publish on tag
│   └── dependabot.yml       # Dependency update config
├── dependabot.yml           # Python + npm dependency scanning
├── CODEOWNERS               # PR review assignments
└── ISSUE_TEMPLATE/
    ├── bug_report.md
    └── feature_request.md
```

### Build Configuration (as Code)

```
pyproject.toml               # Python project config, deps, tool settings
packaging/
├── linux.spec               # PyInstaller spec for Linux
├── macos.spec               # PyInstaller spec for macOS
└── windows.spec             # PyInstaller spec for Windows
scripts/
├── install.sh               # curl | sh install script
└── build-all.sh             # Local multi-platform build script
```

### Website (as Code)

The marketing/landing page site is a SvelteKit static build (separate from the app UI):
- Repo: `agent-hive/website/`
- Host: Cloudflare Pages (free) with automatic deploy on push to `main`.
- Custom domain: `agent-hive.dev`.
- Subdomain `get.agent-hive.dev` serves the install script.

### DNS Configuration

```
agent-hive.dev        → Cloudflare Pages (website)
get.agent-hive.dev    → Cloudflare Pages (install script redirect)
docs.agent-hive.dev   → Cloudflare Pages (docs site — could be same as website)
```

DNS managed via Cloudflare (free tier). Proxied through Cloudflare for DDoS protection and CDN.

### Supply Chain Attestation

SLSA Level 2 provenance generated in CI for every release:
```yaml
# In release.yml
- uses: slsa-framework/slsa-github-generator/.github/workflows/builder_go_slsa3.yml@v2
  with:
    provenance: true
```

This produces a signed attestation verifying that the binary was built from a specific commit on the official repo — users can verify they're running the real Agent Hive, not a tampered binary.

---

## Open Source Operations

### Issue Triage

Labels:
- `bug` / `enhancement` / `question` / `documentation`
- `good first issue` — accessible to new contributors
- `P0` / `P1` / `P2` — priority mapping to user story priorities
- `needs-repro` — bug needs reproduction steps

### Contribution Workflow

1. Contributor forks → creates feature branch → opens PR to `develop`.
2. CI runs: lint, type-check, unit tests, integration test.
3. At least one maintainer review required.
4. CLA bot if BSL with contribution license agreement (TBD — may not need CLA until a managed offering exists).
5. Squash merge to `develop`.

### Release Cadence

- **Patch releases** (`v0.1.x`): bug fixes, as needed. Can ship same day.
- **Minor releases** (`v0.x.0`): new features. Target: every 2-4 weeks during active development.
- **Major releases** (`v1.0.0`): first stable release. When P0 acceptance criteria are all met + 30 days without critical bugs.

### Versioning

Semantic versioning (`MAJOR.MINOR.PATCH`):
- MAJOR: breaking changes (config format, API, DB schema requiring manual migration).
- MINOR: new features, backward-compatible.
- PATCH: bug fixes, backward-compatible.

Pre-1.0: anything goes. Breaking changes allowed in minor versions. Documented in changelog.

### Changelog

Auto-generated from conventional commits (`feat:`, `fix:`, `chore:`, `docs:`) using `git-cliff` in the release workflow. Keep a changelog format.
