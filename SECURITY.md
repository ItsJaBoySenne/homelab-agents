# Security policy

Homelab Agents lets AI models act on your infrastructure, so we take security reports seriously.

## Reporting a vulnerability

**Please do not open a public issue.** Report privately instead:

- GitHub: **Security → Report a vulnerability** on this repository (a private security advisory).

Include what you found, how to reproduce it, the version (commit) and the impact you expect. We aim to acknowledge
reports within a week and to agree on a disclosure date with you; we credit reporters unless you prefer otherwise.

## In scope

- Ways for an agent, a model or content it reads (prompt injection) to get around levels, approvals or the policy.
- Ways for credentials or secret values to reach the router, a model, logs, the audit view or the browser.
- Authentication or authorization bypasses in the dashboard, the API or the gateway.
- Weaknesses in `setup.sh`, the compose files or the `ssh-fleet` scripts that leave an install less safe than
  documented.

Out of scope: problems that need an attacker who already controls the host, the Docker daemon, the gateway's secret
store or a level-6 agent you granted; denial of service by the dashboard's own authenticated user; issues in
third-party services (model providers, Proxmox, Home Assistant, ...) themselves.

## Supported versions

Only the latest commit on `main` gets fixes. Keep your install up to date (`git pull && docker compose up -d --build`).

See [docs/security.md](docs/security.md) for the security model and hardening recommendations.
