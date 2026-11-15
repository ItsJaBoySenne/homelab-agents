# Homelab Agents

[![tests](https://github.com/your-org/homelab-agents/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/your-org/homelab-agents/actions/workflows/tests.yml)

Self-hosted AI agents for your homelab. You chat with agents, give them multi-step projects and recurring
checks, and let them deploy apps and work on Home Assistant. Every action on a server goes through the SSH gateway,
which checks each agent's permission level on every call. The models never see SSH keys or API tokens.

The dashboard is served at `/ui/` of the agent router, typically behind a reverse proxy with single sign-on
(for example Traefik + Authelia at `https://agents.example.com/ui/`). The examples below assume the platform runs on
a VM at `192.168.1.10`; replace that with your own address.

The dashboard works on phones and can be installed as an app (PWA). On Android/Chrome use **Install app** in the
browser menu. On iPhone use Safari's **Share → Add to Home Screen**. Installing needs the HTTPS address above. The
installed app keeps only the page itself offline; data and agent runs always come live from the server.

**Notifications:** tap the 🔔 in the header, on each device you want them on, to get a push notification when a
project task finishes, is blocked or waits for an approval, when a plan is ready, and when a project completes or
pauses on its limits. Tapping a notification opens that project. On an iPhone this only works in the installed app
(iOS 16.4 or later). A notification holds the project and task title, never task results.

## Components

| Directory | What it is |
|---|---|
| [`agent-platform-starter/`](agent-platform-starter/README.md) | Agent router (FastAPI), dashboard, Postgres and queue worker |
| [`ssh-gateway/`](ssh-gateway/README.md) | The only holder of SSH keys and service tokens: runs checks, commands, file writes, deploys and Home Assistant calls, within the policy |
| [`ssh-fleet/`](ssh-fleet/README.md) | Scripts that prepare a VM for the gateway (service user `svc-hla-agent`, restricted key) |

```
browser ── Traefik/Authelia ── agent-router :8088 ──┬── Postgres (tasks, conversations, projects, knowledge)
                                                    ├── TypeSafe (risk check) · OpenRouter (models)
                                                    └── hla-tool-net ── ssh-gateway-api :8099 ──┬── SSH to the servers
                                                                                                   ├── Home Assistant API
                                                                                                   └── Infisical (keys, tokens)
```

## What it does

- **Needs you** on Overview lists everything waiting for you, with a direct action or a link to it: commands
  and risky requests to approve, plans to review, tasks waiting to start or stopped, paused projects. The count
  shows on the Overview tab and in the browser tab title.
- **Conversations** with agents. Replies stream live with their tool steps. Risky requests wait for **Approve
  read-only**, **Approve & run** or **Reject**, and a command that needs approval appears as a card in the chat.
  A reply that runs out of tool calls continues automatically. Search matches titles and message text; the ⋯
  menu renames, archives or deletes a conversation (its tasks stay in Task history).
- **Projects** for bigger work. A planner splits the goal into tasks for different agents, you approve the plan,
  and tasks run in their own conversations. They run in parallel where possible, hand off to each other, and have
  review rounds and gates for anything that deploys. They stop only when they need something from you.
  A completed project keeps going: **Add features or changes** plans a new round on top of the finished tasks
  (same folder, rules and notes), with its own budget.
- **Schedules** post a fixed prompt into a conversation every hour, 6 h, 12 h, day or week.
- **Knowledge**: notes you write, notes agents save (decisions, paths, conventions; never secret values) and facts
  the agents verified, each marked fresh, stale or unverified. Projects have their own knowledge: a compact note per
  finished task is kept automatically. Every run gets the notes and facts most relevant to its request (the project's
  first), and long conversations keep a rolling summary of older turns.
- **Audit** lists everything the SSH gateway ran, wrote, deployed or refused, with filters and a link to the
  conversation it came from; secret values never appear. Stale diagnostic facts can be re-checked weekly.
- **Costs**: spend per day, kind of work, model, project and agent, the costliest runs and budget use; a project warns
  at 80% of its budget.
- **Models**: the model registry. Search OpenRouter's catalog (price, context, tool support), add, enable or disable
  models, pick the default and test a model with one tiny call. Cheaper models can be set per kind of work (planning,
  reviews, continuations, summaries). `AGENT_ALLOWED_MODELS` / `OPENROUTER_WORKER_MODEL` only seed it and are the fallback.
- **Pull requests** (opt-in per project; new projects merge directly): project tasks push a branch and open a PR on
  GitHub (repositories you allow on the Policy page; the token stays in the gateway) instead of merging into main; open
  PRs of finished projects wait in **Needs you**. The sandbox needs a way to push branches, and a write credential
  there lets agents push any branch, so protect `main` with branch protection first (see the starter README).
- **Connections**: credentials you enter once (encrypted in the gateway, never shown again), the GitHub token with a
  connection test and a repository picker, and **HTTP tools**: simple API calls you define that agents make through
  the gateway, which adds the credential, refuses private addresses unless allowed and audits every call.
- **Deployments** of Git/Compose apps. Each commit is tested on the sandbox first, then deployed and verified,
  and rolled back automatically if it fails.
- **Home Assistant** through the gateway (token from Infisical `/HomeAssistant`). Every dashboard save is backed
  up first.
- **Secrets** the agents create and use without seeing them: the gateway generates passwords in the "Agent
  Generated" Infisical project, agents write `{{secret:NAME}}` where a value belongs, and output shows
  `[secret:NAME]` instead of the value.
- **MCP servers**: agents use the tools of remote MCP servers (Streamable HTTP) through the gateway, which holds their
  tokens, checks a level per server (`mcp:<name>`, off until raised) and per tool, and audits each call; the **MCP**
  page registers servers and sets tool levels. stdio servers run behind a small proxy on a host.
- **Websites**: every website on the website VM (Docker on `public-website`) with its public URL from Traefik, its
  environment and a live status (HTTP and certificate checked through Traefik); a production site that goes down sends a
  push notification and waits in **Needs you**. Read-only, through the gateway; Traefik's config never leaves it.
- **Domains & routes** (on the Websites view): give a site a public address (subdomain of your domains, staging LAN-only
  with a password by default) and the gateway writes it to its one Traefik file (`hla-managed.yml`), checks it through
  the Traefik API and rolls back if Traefik refuses; hand-written Traefik files are never touched. Agents only request a
  route; you approve it in **Needs you**. Projects can name their staging and production address.
- **Proxmox** (Infrastructure view, optional): your nodes through the gateway, which holds the API tokens and checks TLS against
  each node's CA. Power, snapshots and **Create VM** (Debian 13 cloud image, spec previewed and validated live), with
  the new VM onboarded automatically (gateway-held SSH key, host key recorded) as a server at level 0. Agents get a
  `proxmox` tool per node level (`proxmox:<node>`, off by default); creating a VM or a read-only monitoring token waits
  for your approval of the exact spec, and destroying is limited to VMs the platform created, approved every time.
- **Sandbox** (optional; a VM on an isolated VLAN, `SANDBOX_HOST`): a free shell for building and testing, and it can be
  reset to its `clean` snapshot from the Policy page.

## Permissions

Each agent and each server (and Home Assistant) has a level on the **Policy** page. An agent acts at the
**lower** of the two levels.

| Level | Allows |
|---|---|
| 0 | Nothing |
| 1 | Read-only diagnostics (Home Assistant: read states and dashboards; secrets: list and use) |
| 2 | Install allowlisted packages |
| 3 | Restart allowlisted services and containers |
| 4 | Compose deploys and registered app deploys (Home Assistant: call services, save dashboards; secrets: create) |
| 5 | Shell commands you approve, once each |
| 6 | Free shell without approval (meant for the sandbox; secrets: replace existing values) |

Proxmox nodes (`proxmox:proxmox`, `proxmox:pve2`) start at 0: 1 inventory, 4 power and snapshots, 5 create a VM or a
read-only token with your approval, 6 rollback, create without approval, and destroy VMs the platform created (always
with your approval).

**Unhinged mode** (1 h, 8 h, 24 h or until switched off) lets level-5 agents act without approval on every server
above level 0.

## Deploy and update

Secrets live on the host only: `agent-platform-starter/.env`, `ssh-gateway/.env.gateway` and the `*.pem` CA files
(none are in this repository; see [`ssh-gateway/certs/README.md`](ssh-gateway/certs/README.md)).
The `.example` files list every setting.

The first time only, create the network the two stacks share:

```bash
sudo docker network create hla-tool-net
```

Deploy or update. Do the gateway first when both changed:

```bash
cd homelab-agents/ssh-gateway && sudo ./dc up -d --build ssh-gateway-api
cd ../agent-platform-starter && sudo ./dc up -d --build --force-recreate agent-router
curl -s http://192.168.1.10:8088/health        # {"status":"ok","database":"connected"}
```

The platform database is dumped every night into `agent-platform-starter/backups/` by the `backup` service
(14 daily and 8 weekly dumps, each verified). The first deploy after it was added needs
`cd agent-platform-starter && sudo ./dc up -d backup` once; copying dumps off the VM and restoring
are in [the platform README](agent-platform-starter/README.md#backups).

`./dc` is `docker compose` with the right files for that directory. Database and gateway schema changes are
applied on startup and only add tables, columns and indexes, so older code keeps running against a newer database.

## Roll back

Releases are commits on `main`. To run an earlier version of one or both parts:

```bash
cd homelab-agents
git checkout <commit-or-tag> -- agent-platform-starter ssh-gateway
cd ssh-gateway && sudo ./dc up -d --build ssh-gateway-api
cd ../agent-platform-starter && sudo ./dc up -d --build --force-recreate agent-router
```

To go back to the latest version afterwards, run `git checkout HEAD -- agent-platform-starter ssh-gateway`, then
the same two deploy commands.

## License

[MIT](LICENSE).

## Tests

No Docker or root needed. See [`agent-platform-starter/tests/README.md`](agent-platform-starter/tests/README.md).

- `tests/local-env.sh <dir>` builds a throwaway Postgres, a Python venv and headless Chromium.
- `tests/test_thread_approval.py`: the backend suite.
- `tests/e2e/`: browser end-to-end tests and a preview server with mocked model providers.
- `tests/perf/`: load and query benchmarks.
- `ssh-gateway/tests/test_*.py`: gateway suites (policy, files, deploys, Home Assistant, maintenance). These run
  with SSH and Infisical stubbed.

## Continuous integration

[`.github/workflows/tests.yml`](.github/workflows/tests.yml) runs on every push and pull request to `main` (and by
hand from the Actions tab), with no secrets: the suites mock the model providers, Infisical and SSH.

- **gateway**: every `ssh-gateway/tests/test_*.py` on Python 3.12 with paramiko.
- **backend**: `tests/test_thread_approval.py` against a Postgres 17 service container.
- **e2e**: the preview server on that Postgres, then `tests/e2e/e2e.js` in Playwright Chromium. On failure the
  screenshots, the preview server log and the test output are attached to the run as `e2e-output`.

A newer push to the same branch cancels the run still going.
