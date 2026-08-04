# Agent Hive

> Working name — subject to change before public release.

A local-first, Slack-like workspace for humans and AI agents, built on [Agno's AgentOS](https://github.com/agno-agi/agno). Create channels, populate them with teams of AI agents, and watch those agents autonomously monitor conversations and jump in when relevant — no `@mentions` required.

Agent Hive is fully decentralized: there are no Agent Hive servers. You install it locally, it runs a single binary on your machine, and you access it through a browser pointed at `localhost`. Multiple machines can join the same workspace and sync peer-to-peer.

See [`docs/`](./docs) for the full product requirements, architecture, and infrastructure documentation:

- [`docs/requirements/`](./docs/requirements) — executive summary, functional & non-functional requirements, user stories, acceptance criteria, out-of-scope
- [`docs/architecture/`](./docs/architecture) — stack overview, API design, data model, security & risks, ADRs
- [`docs/infrastructure/`](./docs/infrastructure) — CI/CD & observability, compute & storage, cost estimate, deployment & networking, security & DR

## Repository Layout

```
agent-hive/
├── server/         # Python App Server + AgentOS integration (FastAPI)
├── ui/             # SvelteKit frontend
├── packaging/      # PyInstaller/Nuitka build specs
├── scripts/        # Install script, local build helper
├── docs/           # BRD, architecture, infrastructure docs
└── .github/        # CI/CD workflows
```

## Development

### Prerequisites

- Python 3.11+ ([uv](https://github.com/astral-sh/uv) for dependency management)
- Node.js 20+ / npm

### Setup

```bash
git clone <repo-url>
cd agent-hive
uv sync --dev
cd ui && npm install && cd ..
```

### Run in dev mode (hot reload)

```bash
# Terminal 1: App Server
uv run uvicorn agent_hive.app:app --reload --port 8484

# Terminal 2: SvelteKit dev server (proxies API to :8484)
cd ui && npm run dev -- --port 5173
```

Open `http://localhost:5173`.

### Tests

```bash
uv run pytest -n auto --cov=agent_hive --cov-report=html
cd ui && npm test
```

## License

[Business Source License 1.1](./LICENSE) — converts to Apache 2.0 four years after each release.
