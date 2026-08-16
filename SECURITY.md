# Security Policy

## Reporting a vulnerability

Please report security vulnerabilities through [GitHub's private security advisory form](https://github.com/jwilson411/Rivulets/security/advisories/new) rather than a public issue. Private advisories give us a chance to fix the problem before it's disclosed.

Please include what you can: affected version or commit, steps to reproduce, and what an attacker gains.

## Scope

Rivulets runs entirely on the user's own machine with no cloud component, but the web UI is a network service and workspaces sync between machines. The threat model — what's protected, what isn't, and why — is documented in [`docs/security.md`](./docs/security.md). Operational backups, restore, and login rate limits are documented in [`docs/infrastructure/security-and-dr.md`](./docs/infrastructure/security-and-dr.md).

Reports about an attacker who already has local (root-level) access to the machine are out of scope — see the threat model's assumptions.

## Supported versions

Only the latest release receives security fixes.
