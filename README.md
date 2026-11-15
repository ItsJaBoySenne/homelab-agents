# Homelab Agents

[![tests](https://github.com/your-org/homelab-agents/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/your-org/homelab-agents/actions/workflows/tests.yml)

**Self-hosted AI agents for your homelab.** Chat with agents, hand them multi-step projects and recurring checks, and
let them look after your servers, deploy apps, manage Proxmox VMs and tune Home Assistant: with a permission level
per agent and per server, approvals for anything risky, and a full audit log.

The agents never see a password, SSH key or API token. Every action goes through the **gateway**, a separate
container that holds all credentials, checks each call against your policy, runs it and returns only the result.

## Features

- **Conversations** with agents that stream their replies and tool steps live. Risky requests wait for *Approve
  read-only*, *Approve & run* or *Reject*.
- **Projects** for bigger goals: a planner splits the work into tasks for different agents, you approve the plan,
  tasks run in parallel with reviews and approval gates, and stop only when they need you.
- **Needs you**: one inbox for everything waiting on you (approvals, plans, blocked tasks), with push notifications
  on your phone. The dashboard installs as an app (PWA).
- **Levels and approvals**: every agent and every server gets a level from 0 (nothing) to 6 (free shell); an agent
  acts at the lower of the two. Shell commands can require your approval, once each.
- **The Chief**: a voice assistant for the car or the kitchen. Ask what is going on, get a spoken briefing, start or
  approve work hands-free. Browser speech by default, optional server speech (Kokoro TTS, Whisper-compatible STT).
- **Knowledge**: notes you and the agents write, and facts they verified (fresh or stale, re-checked weekly).
- **Integrations**: SSH servers, Proxmox VE (inventory, power, snapshots, new VMs), TP-Link Omada, Home Assistant,
  GitHub pull requests, remote **MCP** servers, your own HTTP tools, Git/Compose deployments with automatic rollback,
  websites and reverse-proxy routes.
- **Models**: OpenRouter (hundreds of models), xAI directly, or your own **Claude Pro/Max or ChatGPT Plus/Pro
  subscription** through the official Claude Code / Codex CLIs. Costs per day, project, agent and model.
- **Secrets agents use without seeing them**: agents write `{{secret:NAME}}`, the gateway fills it in, and output
  shows `[secret:NAME]` instead of the value.

## Screenshots

> _Screenshots coming soon: Overview with "Needs you", a conversation with an approval card, a project plan, the
> Policy page and the Chief's car mode._ `agent-platform-starter/tests/e2e/shots.js` renders a set against the preview
> server if you want to look before installing.

## Quick start

On a Linux machine (VM or bare metal) with Docker:

```bash
git clone https://github.com/your-org/homelab-agents.git && cd homelab-agents
./setup.sh          # a few questions; generates every password and key; builds and starts the stack
# open the URL it prints (default http://127.0.0.1:8088/ui/) and log in with the password it shows once
```

`setup.sh` asks where the dashboard should listen (this machine only, or your LAN), which model provider to use
(OpenRouter key, xAI key, or a ChatGPT/Claude subscription you sign in to later), whether to enable server voice, and
optionally which servers the agents may reach. It is safe to re-run: it keeps every existing value and backs up each
settings file before changing it. `./setup.sh --check` validates the configuration without changing anything, and
`./setup.sh --help` lists the non-interactive options (`--yes`, `--provider`, `--listen`, `--server`, ...).

Then, in the dashboard:

1. **Settings › Models**: check the default model, or sign in to a subscription under *Subscriptions*.
2. **Agents**: create an agent, or talk to the Chief.
3. **Policy**: raise the level of the servers and agents you want to use (everything starts at the safe end).

To add a server later, re-run `./setup.sh` (it adds the server to `config/inventory.yaml`, creates its SSH key and
prints the one-liner that installs the public key on it), or edit `config/inventory.yaml` yourself and run
`docker compose up -d`.

## Requirements

- A Linux host with **Docker Engine** and the **Compose v2 plugin** (`docker compose` 2.24 or newer).
- **2 vCPU and 4 GB RAM** minimum; add about 2 GB for Kokoro voice. About 10 GB of disk for the images, plus the
  database and its nightly backups.
- `openssl` or `python3` (for `setup.sh`), and `ssh-keygen` if setup should create server keys.
- A model provider: an [OpenRouter](https://openrouter.ai) or [xAI](https://console.x.ai) API key, or a Claude /
  ChatGPT subscription.
- Not required: a domain, a reverse proxy, SSO or Infisical. See [optional add-ons](#optional-add-ons).

## How it fits together

```
browser / phone ── (optional reverse proxy + SSO) ── agent-router :8088 ──┬── Postgres (+ nightly backups)
                                                                          ├── model APIs / subscription CLIs
                                                                          ├── kokoro (optional voice)
                                                                          └── hla-tool-net ── gateway :8099 ──┬── SSH to your servers
                                                                                                              ├── Proxmox, Omada, Home Assistant
                                                                                                              ├── GitHub, MCP servers, HTTP tools
                                                                                                              └── secret store (built-in or Infisical)
```

| Path | What it is |
|---|---|
| [`agent-platform-starter/`](agent-platform-starter/README.md) | Agent router (FastAPI), dashboard, queue worker, Postgres, backups, voice |
| [`ssh-gateway/`](ssh-gateway/README.md) | The gateway: the only holder of keys and tokens; policy, audit and every tool call |
| [`ssh-fleet/`](ssh-fleet/README.md) | Scripts that prepare a server for the gateway (locked service user, restricted key) |
| `config/` | `inventory.yaml`: your servers and services (created by `setup.sh`) |
| `compose.yaml`, `deploy/` | The one-command stack ([how it is put together](docs/configuration.md#compose-layout)) |

## Configuration

Four files, all created by `setup.sh`, none committed to git:

| File | What goes in it |
|---|---|
| `agent-platform-starter/.env` | Dashboard and database passwords, model keys, listen address, voice, feature switches |
| `ssh-gateway/.env.gateway` | Gateway tokens (shared with the router), secret store backend and key, integration settings |
| `config/inventory.yaml` | Servers the agents may reach (name, host, user, port, description, sandbox) and services (Proxmox, Omada, Home Assistant, GitHub) |
| `.env` | Compose-level switches only, e.g. `COMPOSE_PROFILES=voice` |

The settings you are most likely to touch:

| Setting | File | Default | Meaning |
|---|---|---|---|
| `HLA_LISTEN_ADDRESS` | router `.env` | `127.0.0.1` | Address the dashboard listens on (port 8088). A LAN IP to reach it from other devices. |
| `OPENROUTER_API_KEY`, `XAI_API_KEY` | router `.env` | empty | Model provider keys. Subscriptions need none. |
| `OPENROUTER_WORKER_MODEL` | router `.env` | set by setup | Seeds the default model; after that the Models page decides. |
| `HLA_SECRETS_BACKEND` | `.env.gateway` | `local` | `local`: built-in encrypted store (key `HLA_SECRETS_KEY`). `infisical`: your Infisical. |
| `HLA_VOICE_LANGUAGE` | router `.env` | `en-US` | Default language of the Chief's car mode (en-US, en-GB, nl-BE, nl-NL). |
| `HLA_RISK_REVIEW` | router `.env` | `false` | Optional TypeSafe risk scoring that holds risky requests for approval. |
| `BACKUP_TIME`, `BACKUP_KEEP_DAYS` | router `.env` | `03:30`, `14` | Nightly database backup time and retention. |

Full reference, inventory format and split installs (the gateway on another machine):
[docs/configuration.md](docs/configuration.md). The `.example` files document every setting inline.

## Security model

- **Agents never hold credentials.** SSH keys, API tokens and generated passwords stay in the gateway. The router
  and the models only see results, with secret values masked.
- **Two levels decide every action**: the agent's and the server's (0-6); the lower one wins. Anything above that is
  refused or waits for your approval of that exact command. *Unhinged mode* (time-limited, off by default) is the only
  way around approvals.
- **Narrow keys**: each server gets its own Ed25519 key that only works from the gateway's address (`from=` in
  `authorized_keys`), on a locked service account without sudo unless you grant it.
- **Hardened containers**: read-only root file systems, no Linux capabilities, `no-new-privileges`; the gateway has no
  published port.
- **Everything is audited**: every command, file write, deploy, API call and refusal, linked to the conversation it
  came from.
- The dashboard listens on localhost by default. Put HTTPS and single sign-on in front before exposing it beyond
  your LAN.

Details and hardening tips: [docs/security.md](docs/security.md). Reporting a vulnerability: [SECURITY.md](SECURITY.md).

## Optional add-ons

| Add-on | What you get | How |
|---|---|---|
| **HTTPS + single sign-on** | Access from anywhere, phone install and push notifications, named approvers | Any reverse proxy (Traefik, Caddy, nginx) plus an SSO (Authelia, Authentik, ...): [docs/security.md](docs/security.md#https-and-single-sign-on) |
| **Infisical** | Secrets in your existing Infisical instead of the built-in store | `HLA_SECRETS_BACKEND=infisical`: [docs/configuration.md](docs/configuration.md#secret-store) |
| **Server voice** | Kokoro text-to-speech (and any Whisper-compatible speech-to-text) for the Chief | `./setup.sh` → voice yes, or `COMPOSE_PROFILES=voice`: [docs/integrations.md](docs/integrations.md#voice-the-chief) |
| **Integrations** | Proxmox, Omada, Home Assistant, GitHub, MCP, deployments, websites | [docs/integrations.md](docs/integrations.md) |

## Updating

```bash
cd homelab-agents
git pull
./setup.sh --check              # reports anything new that needs a value
docker compose up -d --build
```

Schema changes are applied on startup and only add tables, columns and indexes, so an older version keeps running
against a newer database. To roll back: `git checkout <tag-or-commit>` and the same `up` command.

## Backups

The `backup` service dumps the database every night (03:30 UTC by default) into `agent-platform-starter/backups/`,
verifies each dump, and keeps 14 daily and 8 weekly ones. One now: `sudo agent-platform-starter/scripts/backup-now.sh`.
Restore (into a copy by default): `sudo agent-platform-starter/scripts/restore-backup.sh <dump>`.

**Copy backups off the machine regularly**, together with the four configuration files above and the secret store
(the `homelab-agents_gateway_secrets` volume plus `HLA_SECRETS_KEY`, or your Infisical). Details:
[agent-platform-starter/README.md](agent-platform-starter/README.md#backups).

## Uninstall

```bash
cd homelab-agents
docker compose --profile voice --profile tools down      # remove the containers, keep the data volumes
docker compose --profile voice --profile tools down -v   # ...and delete the volumes: database, secrets, sign-ins
```

Then delete the folder (it holds your settings, inventory and backups), the server keys in `~/.ssh/hla-fleet/` if
setup created them, and the service account on each server (`sudo userdel -r svc-hla-agent`).

## FAQ and troubleshooting

**`setup.sh` says the Docker daemon is not reachable.** Start it (`sudo systemctl start docker`) or add yourself to the
`docker` group (`sudo usermod -aG docker $USER`, then log in again). Your answers are saved; run `./setup.sh` again.

**The dashboard does not load.** `docker compose ps` (is `agent-router` healthy?) and
`docker compose logs --tail 80 agent-router`. `curl http://127.0.0.1:8088/health` should answer
`{"status":"ok","database":"connected"}`; use your LAN address instead of 127.0.0.1 if you chose one.

**I lost the dashboard password.** `./setup.sh --reset-password` makes a new one and shows it once; it restarts the
stack for you.

**Agents answer "Missing server setting: OPENROUTER_WORKER_MODEL".** There is no default model yet: pick one under
Settings › Models (after adding an API key or signing in to a subscription).

**An agent cannot reach my server.** Check the server's level on the Policy page, that the public key is installed
([ssh-fleet/README.md](ssh-fleet/README.md)) and the gateway log: `docker compose logs --tail 50 ssh-gateway-api`.
The address the server sees the connection from must match the `from=` address in its `authorized_keys`.

**Can I use it without any servers?** Yes. Conversations, projects, knowledge and the Chief work without SSH targets;
add servers when you want the agents to act on them.

**Can the gateway run on another machine?** Yes: [docs/configuration.md](docs/configuration.md#split-installs).

**Port 8088 is taken.** Change the published port in `agent-platform-starter/compose.yaml` (`...:8088:8080`).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The suites need Python, a throwaway Postgres (one `docker run` line) and, for the
browser tests, Playwright. CI runs everything on every push: [`.github/workflows/tests.yml`](.github/workflows/tests.yml).

## License

[MIT](LICENSE).
