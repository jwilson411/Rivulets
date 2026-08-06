# Contributing to Rivulets

Thanks for taking a look. This is a young project, so process is deliberately light — but a few things keep it maintainable as it grows.

## Getting set up

See [README.md#development](./README.md#development) for the full dev environment setup (Python 3.11+/uv, Node 20+/npm) and the two-terminal hot-reload workflow.

## Before opening a PR

Run the same checks CI runs ([`.github/workflows/ci.yml`](./.github/workflows/ci.yml)):

```bash
# Server
cd server
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest -n auto --cov=rivulets

# UI
cd ui
npm run lint
npm run check
npm test
```

A few conventions worth knowing before you dive in:

- **Comments explain *why*, not *what*.** If a comment just restates the code, delete it. Comments earn their place by capturing a non-obvious constraint, a subtle invariant, or the reason behind a decision that isn't visible from reading the diff alone.
- **Strict type checking is enforced** (`pyright --strict` on the server, `svelte-check`/TypeScript on the UI). A few directories with untyped third-party dependencies get relaxed Unknown-type reporting — see the `[tool.pyright]` block in `server/pyproject.toml` for the current list and why.
- **`docs/` is living documentation, not a spec frozen in time.** If your change affects a functional requirement, an architecture decision, or the data model, update the relevant file under `docs/` in the same PR.

## Reporting bugs / requesting features

Use the issue templates under [`.github/ISSUE_TEMPLATE/`](./.github/ISSUE_TEMPLATE) — they ask for the context that's actually useful for a local-first app (OS, install method, workspace size) rather than a generic template.

For security vulnerabilities, please use [GitHub's private security advisory form](https://github.com/jwilson411/Rivulets/security/advisories/new) instead of a public issue.

## Code ownership

See [`.github/CODEOWNERS`](./.github/CODEOWNERS) for which areas of the codebase are auto-requested for review on a PR.

## License

By contributing, you agree that your contributions will be licensed under the project's [Business Source License 1.1](./LICENSE).
