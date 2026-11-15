# Homelab Agents: agent router and dashboard

The agent router (FastAPI), the dashboard, the queue worker, Postgres with nightly backups and the optional Kokoro voice
service. **To install, use `../setup.sh`** (see the [top-level README](../README.md)); this page documents how the
router works inside. Examples use `192.168.1.10` for the machine running it and `https://agents.example.com/ui/` for
an optional reverse proxy address; replace them with your own.

## How a request runs

1. A task (Overview, Queue, API) or a conversation message creates a row in `tasks`.
2. Optionally (`HLA_RISK_REVIEW=true`, off by default) **TypeSafe** scores it as expert and risky. Risk ≥ 0.40
   pauses it as `human_approval_required`; conversations score only the new message plus the previous exchange. Off,
   nothing is held for risk and no score is recorded; agent levels, command approvals and unhinged mode are unchanged.
3. The **OpenRouter** worker model answers, with knowledge notes, memory and Proxmox inventory as context
   (`memory/knowledge.py`: ranked by full-text search, plus pg_trgm when installed; project notes first). Agents
   with tools also get `knowledge_search` and `knowledge_save`. Conversation history is sent by size
   (`HLA_HISTORY_CHARS`); older turns are folded into a per-conversation summary.
   Replies stream to the browser over SSE (`/ui/api/tasks/{id}/events`).
4. Agents get tools that call `../ssh-gateway`, limited to what the **Policy** page allows them per server
   (diagnostics, verified installs and restarts, Compose deploys, approved commands; see the gateway README).
   The gateway enforces the same policy on every call. Each tool call is recorded in `tasks.progress`
   (never its command or output). In unhinged mode, level-5 agents skip approvals and the risk hold.
   **MCP tools** (`agent_tool/runtime.py`): an agent with an `mcp:<name>` service above level 0 also gets the MCP tools
   the gateway allows it (`GET /v1/mcp/tools`, cached 60 s), named `mcp__<server>__<tool>` (sanitised, at most 64
   characters, at most 64 tools) with the server's input schema reduced to JSON-schema basics. Calls go to
   `POST /v1/mcp/call` with the task header and count toward the tool budget; results reach the model wrapped as
   untrusted data, and steps show as "MCP · server · tool" without their arguments. Without the gateway the run goes on
   without MCP tools; read-only approvals and restricted maintenance runs never get them.
5. **Sandbox** (optional): a disposable VM (`SANDBOX_HOST`, ideally on a separate Proxmox host and an isolated VLAN) is
   at level 6, a free shell. The Policy page resets it to its `clean` snapshot (`sandbox.py`).

**Audit** shows the SSH gateway's audit log (`audit_view.py`, `GET /ui/api/audit`): filters for agent, server, action,
text and dates, 50 rows per page, details with a link to the conversation or task a row came from. The router reads
the gateway with `POLICY_ADMIN_TOKEN` when it is set, otherwise `TOOL_GATEWAY_TOKEN`.

**Fact re-check** (`fact_recheck.py`): facts from a gateway diagnostic expire (stale). With `HLA_FACT_RECHECK=weekly`
the router re-runs, once a week, the same read-only diagnostic (level 1) for up to 20 stale facts, as the agent that
recorded them (the gateway's policy still decides), oldest first. The fact is refreshed (a change keeps the previous
value) or stays stale with the reason; each fact shows when it was re-checked and how. **Re-check now** on the
Knowledge page runs the same pass. Changes, deploys, commands and Proxmox facts are never re-run: they are history or
come from elsewhere. Runs are logged in `fact_recheck_runs`.

Paused requests are approved or rejected in the conversation or the task drawer: **Approve read-only**
(diagnostics only), **Approve & run** or **Reject**. Approvals run once, expire after
`HLA_APPROVAL_TTL_HOURS`, record the approver (Authelia user when `HLA_TRUSTED_PROXY_IPS` is set)
and are logged in `task_approval_events`. Conversation runs can be stopped, and one follow-up can be queued.

## Projects: progress check and pull requests

- **No progress, no endless continuations** (`projects.py`, HLA_SPIN_V1). Before a checkpointed task continues, the
  orchestrator compares its newest replies: how similar consecutive checkpoints are (difflib), whether new commits,
  branches, paths or URLs appear, and whether the tool steps repeat (each step in `tasks.progress` carries a short hash
  of its arguments). After `SPIN_CONTINUATIONS` (2) continuations without progress, automatic continuations stop and
  the task gets one final wrap-up turn ("stop re-verifying, do what is left or say what blocks you"). If that turn only
  checkpoints again, the task stops as failed with the reason and the project pauses (push + inbox). The status line
  is read leniently (markdown, quotes, emoji around the word; complete/completed/finished count as done; status lines
  inside the HANDOFF are ignored); an unfinished status such as `TASK STATUS: in progress`, partial or wip counts as a
  checkpoint. Failed runs (provider errors) do not count as turns without progress. A manual Retry or Send back starts over.
- **Merge policy** per project (`merge_policy`, set on the new-project form, editable on the project page while no task
  runs): `direct` (the default) has tasks merge into main themselves. `pr` is opt-in: tasks push their branch and
  open a pull request with the `github` tool and hand off `PR: <url>`; reviewers check the PR (pr_status, the diff)
  and may comment; nobody merges but you. **PR mode needs setup**: the GitHub token in the gateway, the repository on
  the allowlist (Policy page → GitHub) and a way for the sandbox to push branches. The sandbox's deploy key is
  read-only and the gateway has no push operation yet, so without more setup the push step fails and the task ends
  blocked. A write credential on the sandbox (deploy key with write access, token) can be read and used by the agents
  to push *any* branch, main included, so protect `main` with GitHub branch protection (require a PR) before adding one.
  PRs named in handoffs are kept in `project_prs`, shown on the task card (`PR #12 · open`) and, once all tasks of the
  project are done, as "PR ready to merge" in the inbox. Their state is refreshed through the gateway at most once a
  minute (admin token, read-only; failures keep the stored state). Allow repositories on the Policy page (GitHub).

## Layout

| Path | Purpose |
|---|---|
| `app.py` | FastAPI app: tasks, TypeSafe/OpenRouter routing, dashboard API, schema setup on startup |
| `threads/` | Conversations: background runs, queue, stop, approval cards |
| `approvals.py` | Approver identity, expiry, approval audit |
| `inbox.py` | "Needs you" inbox (`/ui/api/inbox`) and conversation rename, archive and delete |
| `audit_view.py` | Audit page: the gateway's audit log with agent names and conversation links (`/ui/api/audit`) |
| `fact_recheck.py` | Weekly / on-demand re-check of stale diagnostic facts (`/ui/api/facts/recheck`) |
| `costs.py` | Costs page API (`/ui/api/costs`): spend per day, project, agent, model and kind; top runs; budget use; the 80% budget warning |
| `voice.py` | Optional server speech for the Chief's car mode (`/ui/api/voice`): config, transcribe (raw audio, sniffed, 25 MB), speak (MP3) through an OpenAI-compatible service |
| `mcp_admin.py` | MCP page API (`/ui/api/mcp`): the gateway's MCP registry, refresh, service level and per-tool levels (writes need an approver) |
| `proxmox_admin.py` | Infrastructure view API (`/ui/api/proxmox`): both Proxmox nodes from the gateway, power/snapshot/destroy, Create VM (background job), read-only token, levels (admin token, approver for changes) |
| `public_routes.py` | Domains & routes API (`/ui/api/routes`): proxies the gateway's managed Traefik routes (admin token, approver for writes), agents' route requests in "Needs you" |
| `websites.py` | Websites view API (`/ui/api/websites`): the gateway's website inventory with owner overrides, the status monitor (push + inbox for production sites that go down) |
| `models.py` | Model per kind of work (`models.choose`): planning, reviews, continuations, summaries, per-project overrides |
| `model_registry.py` | Model registry (`/ui/api/models`): allowed models, default model, OpenRouter and xAI catalogs, test call |
| `providers.py` | Model providers: OpenRouter, or xAI for `xai/<model>` (key, request body, cost, error messages); subscription engines for `claude-sub/` and `chatgpt-sub/` |
| `engines.py`, `engine_mcp_relay.py`, `subscriptions.py` | Subscription engines: the Claude Code / Codex CLIs as providers, the tool relay (MCP server the CLI starts), sign-in and the Subscriptions page API (`/ui/api/subscriptions`) |
| `chief.py` | The Chief (`/ui/api/chief`): one agent and conversation with the platform_* tools (state, changes, projects, runs, conversations, logs; confirmed actions), voice reply style |
| `runs.py` | Registry of background runs (single process) for Stop |
| `sandbox.py` | Sandbox reset through the pve2 Proxmox API |
| `agent_tool/` | Tool loop and tool definitions for the SSH agent |
| `streaming/`, `memory/`, `brain/`, `tool_context/` | SSE, memory capture, knowledge tools and secret writer, Proxmox context |
| `queue_worker.py` | Runs queued jobs one at a time through the API |
| `ui/` | Dashboard (no build step) |
| `tests/` | Backend suite, browser E2E, preview server; see `tests/README.md` |
| `scripts/` | Database backups: `pg-backup.sh` (run by the `backup` service), `backup-now.sh`, `restore-backup.sh` |

## MCP servers

The **MCP** page lists the MCP servers registered in the gateway (see the gateway README for transport, levels and
SSRF rules): status (tools loaded, last refresh, error), **Add server** / **Edit** (name, URL, auth type, a credential
picked by name from the gateway's credential store, timeout, **Allow private network addresses**, plain http,
enabled), **Refresh tools**, **Remove**, every tool with its description and hints, and the level it needs with a
select to override it (or disable it). Tools a server adds after its first refresh show **new — review** and stay off
until you press **Enable**; changed descriptions or hints are flagged. A URL with a token in it can be kept in the
credential store (**URL is stored as a credential**). The server's own level (`mcp:<name>`, 0 = off) is set on the card and is also
listed on the Policy page. Everything goes through `mcp_admin.py` to the gateway with `POLICY_ADMIN_TOKEN`; writes need
the UI header and an approver. Credentials are not entered here (Connections page).

To use an MCP server: store its token as a credential, add the server, check its tools and levels (tools need 4 by
default, destructive ones 6; set read-only tools to 1 yourself, the server's hints are not trusted), then raise
`mcp:<name>` to the level you want and give the agent at least that level.

## Infrastructure: Proxmox

The **Infrastructure** view shows both Proxmox nodes as the SSH gateway sees them (`proxmox_admin.py` → gateway
`/v1/proxmox`, admin token; the node tokens stay in the gateway): per node the PVE version, CPU and memory, next free
VMID, what the node token may do (Read, Power, Snapshots, Rollback, Create VM, Download image, Destroy, Audit token; a red
badge names the missing privileges), the agents' level for that node (`proxmox:<node>`, 0 = off), and every VM with
status, vCPU/RAM/disk, IP and tags (`managed` for VMs created here, `protected` for the platform's own VMs). Buttons:
Start, Shutdown, Reboot, Stop, Snapshot…, Rollback…, Destroy… (only VMs created here; type the name to confirm), Onboard
(retry after a failed onboarding), **+ Create VM** and **Read-only token…**. Every change asks in an in-page dialog and
needs an approver.

**Create VM**: VMID (next free on both nodes, 100-108 and 204 reserved), name, cores, memory, disk, storage and bridge
(from the node), IP (suggested from the VMID inside `PROXMOX_ALLOWED_NETS`), gateway and DNS (detected from existing VMs).
**Preview** asks the gateway to validate it live (collisions of VMID, name, IP and MAC on both nodes, an address that
answers) and shows the exact spec; **Create VM** runs it as a background job (image download, boot, onboarding take
minutes; the job line shows progress). The VM becomes a server at level 0 under **Servers created here**, with its
host key fingerprint and a level select.

Agents use the `proxmox` tool at their level per node: 1 inventory, 4 power and snapshots, 5 create a VM or the
read-only token after you approve the exact request, 6 rollback and destroy (VMs created here only, always approved).
Such a request appears in the conversation as a **Proxmox request** card with the full spec (and in "Needs you");
**Approve & run once** grants exactly that text in the gateway and sends the stored request again as the agent; the agent
then gets the result and may only read Proxmox to check it. Details, required PVE token privileges and `pveum` commands:
the gateway README, section **Proxmox VE**.

## Costs and model routing

The **Costs** page shows the OpenRouter spend of the last 7, 30 or 90 days (UTC days): totals, a daily bar chart, and
breakdowns per kind of work (conversations, project tasks, planning, conversation summaries, one-shot tasks), model,
project and agent, the ten most expensive runs and every project's budget use. A running project logs a warning and
sends a push notification once when it passes 80% of its budget; changing the budget (Limits…, a new round) re-arms it.

"Models per kind of work" (on the Models page; Costs links to it) picks the model for planning, review tasks, continuations of project tasks
(checkpoints and automatic continuations) and conversation summaries, plus per-project overrides for planning, tasks and
reviews. Defaults come from `HLA_MODEL_PLAN`, `HLA_MODEL_REVIEW`, `HLA_MODEL_CONTINUE` and `HLA_SUMMARY_MODEL`;
empty means the agent's own model. Only models enabled in the model registry are used (a stored or env value outside it
is skipped; `HLA_SUMMARY_MODEL` from the environment is trusted as before). The model a run used is its task's model.
Summary costs are recorded separately on their run (`tasks.summary_cost_usd`) from this version on.

## Connections: credentials, GitHub and HTTP tools

The **Connections** page (`connections.py`, all through the SSH gateway with `POLICY_ADMIN_TOKEN`; changes need an
allowed approver, like policy edits):

- **Credentials**: name, kind, description, fingerprint (an HMAC keyed from `GATEWAY_CREDENTIAL_KEY`) and who changed it when. A value is typed once into a password
  field, sent to the gateway's write-only store (`GATEWAY_CREDENTIAL_KEY` in `../ssh-gateway/.env.gateway`) and never
  shown or returned again; *Replace* keeps the name. Without `POLICY_ADMIN_TOKEN` or the key the page says what to set.
- **GitHub**: whether the token works, where it comes from (credential store, `.env.gateway` or Infisical) and its login
  and scopes; *Test connection*; a field that stores `GITHUB_TOKEN` in the credential store (it takes precedence); the
  `github` service level; and a picker of the repositories the token reaches: checking one adds it to the allowlist
  (`github.repos`, the same list as Policy → GitHub), with an *allow merges* switch per repository.
- **HTTP tools**: API calls the owner defines (method, https base URL, path with `{placeholders}`, typed parameters,
  a credential from the list, level, limits). Agents whose level on the `http_tools` service reaches a tool's level get
  it as a function `http_<name>` (results reach the model labelled as untrusted data); the gateway fills in the credential, refuses private addresses unless the tool allows
  them, caps the response and audits every call. *Test* runs one call with sample parameters. See the gateway README.

Endpoints: `GET /ui/api/connections`, `GET|PUT|DELETE /ui/api/credentials[/NAME]`, `GET /ui/api/github/status`,
`GET /ui/api/github/repos`, `GET|PUT|DELETE /ui/api/http-tools[/name]`, `POST /ui/api/http-tools/{name}/test`.

## Model registry

The **Models** page manages which models agents may use (`model_registry.py`, table `models_registry`). A model is its
OpenRouter id (`vendor/model`, e.g. `anthropic/claude-sonnet-4.5`), or `xai/<model>` for Grok called directly at xAI.

**Providers (HLA_XAI_V1, `providers.py`).** Every chat completion (agents, conversations, planner, review, summaries,
the Test button) picks its provider from the model id: `xai/<model>` goes to `https://api.x.ai/v1` with `XAI_API_KEY`,
everything else (including OpenRouter's own `x-ai/...` ids) to OpenRouter with `OPENROUTER_API_KEY`. OpenRouter-only
request fields (`reasoning`, `provider`, `transforms`, ...) are dropped for xAI. xAI's cost is taken from
`usage.cost_in_usd_ticks` (1 tick = 1e-10 USD) when present, else from the registry's per-1M prices, and stored like
OpenRouter's, so budgets, limits and the Costs page (with a Provider column) include it.

To use your xAI credits: back up `.env` (`cp .env ~/backups/agent-router.env.$(date +%F)`), add `XAI_API_KEY=xai-...`,
recreate agent-router, then on the Models page choose **xAI (direct)** under *Add a model* (it lists
`GET /v1/language-models` with prices, cached for an hour) or add `xai/grok-4` by hand, and press **Test**.
Without the key the xAI list says so, and runs on `xai/...` models fail with "XAI_API_KEY is not set".

- **Registry**: label, id, context length, price in/out per 1M tokens, tool support, enabled, notes and what uses it
  (agents, routing settings, project overrides, the default). Only enabled models can be picked for agents, conversation
  model overrides and routing, and runs check it too (an agent on a disabled model cannot run). Disabling a model that
  is in use warns who uses it; removing one is refused while an agent, routing setting, project override or the default
  uses it, and the default model cannot be disabled. At least one model stays enabled.
- **Default model**: used by runs without an agent or override, and when routing falls back (planner, summaries).
- **Add a model**: search OpenRouter's public model list (`GET /ui/api/models/catalog?q=`, fetched without the API key and
  cached for an hour; on failure the page says so and an older copy is used when there is one), or add an id by hand.
  Models without tool calls are flagged: agents with server tools need them. **Refresh prices** re-reads prices, context
  and tool support of every registry model.
- **Test** sends one tiny completion (at most 16 tokens) and shows latency and reply; it costs a fraction of a cent and
  is not recorded as a task.

Adding, enabling/disabling, removing and changing the default need an approver (like policy edits: with
`HLA_APPROVER_USERS`/`HLA_APPROVER_GROUPS` set, a verified Authelia identity on that list); labels, notes and
tests need only the dashboard session.

The environment seeds and backs it up: on startup an empty registry is filled from `AGENT_ALLOWED_MODELS` and
`OPENROUTER_WORKER_MODEL`, so an existing setup keeps working; from then on the registry is the source of truth. While
the registry is empty, `AGENT_ALLOWED_MODELS` is the allowlist again. Without a default chosen on the page,
`OPENROUTER_WORKER_MODEL` is the default (while it is enabled), else the first enabled model.

API: `GET /ui/api/models` (`models`: enabled ids for the pickers, `registry` rows with `used_by`, `default`
{model, source: dashboard|env|registry}, `labels`, `env`, `approver`), `POST /ui/api/models` {id, label?, notes?,
enabled?}, `PATCH /ui/api/models/{id}` {label?, notes?, enabled?}, `DELETE /ui/api/models/{id}`,
`PUT /ui/api/models/default` {model | null}, `POST /ui/api/models/{id}/test`, `POST /ui/api/models/sync`,
`GET /ui/api/models/catalog?q=&refresh=&limit=&source=openrouter|xai|subscription`.

## Subscription engines

Agents can run on your own **Claude (Pro/Max)** or **ChatGPT (Plus/Pro)** plan instead of per-token API keys
(HLA_ENGINES_V1, `engines.py`). The platform never touches the plan's OAuth tokens: the vendors' official CLIs,
**Claude Code** (`@anthropic-ai/claude-code` 2.1.325) and **Codex CLI** (`@openai/codex` 0.169.0), are installed in the
agent-router image and do all the talking to Anthropic / OpenAI, signed in with your own account.

**Sign in** on the Subscriptions page (needs an approver, like policy edits):
- *Claude*: **Sign in** starts `claude auth login --claudeai` in the router. Open the link, sign in with your Claude
  account, copy the code the page shows and paste it back. The account e-mail and plan (from `claude auth status`) appear.
- *ChatGPT*: **Sign in** starts `codex login --device-auth`. Open the link, sign in with your ChatGPT account and enter
  the one-time code shown; the page turns to signed in by itself (the code expires after 15 minutes).
- **Test** sends one tiny prompt through the engine (no tools). **Sign out** runs the CLI's logout and removes its
  credential file. The sign-ins live in the `engine_auth` volume (`/data/engines/claude`, `/data/engines/codex`,
  chmod 700) and survive restarts and image updates.

**Models.** `claude-sub/<model>` (`sonnet`, `opus`, `haiku`, `fable`, or any name `claude --model` accepts) and
`chatgpt-sub/<model>` (`gpt-6.1-sol`, `gpt-6-astra`, `gpt-6.1-terra`, `gpt-6-sol`, `gpt-6-luna`, `gpt-5.5`); change the
lists with `HLA_CLAUDE_SUB_MODELS` / `HLA_CHATGPT_SUB_MODELS`. While an engine is signed in, its models are in every
model picker (agents, conversations, routing, the default) without a registry row; add one on the Models page (*Add a
model* -> Subscription) to give it a label or to switch it off. They have no per-token price: runs record cost 0 with
`usage.billing: "subscription"`, the Costs page shows them as *subscription* (and counts them in
`totals.subscription_runs`), and agent/project budgets are not used up by them. Images are not passed to engines.

**What runs where.** For a run on a subscription model the router starts the CLI as a child process (one per run), with:
- **every built-in tool off**: Claude Code with `--tools ""` (plus the built-ins denied by name), `--strict-mcp-config`,
  `--permission-mode dontAsk --permission-prompts none`; Codex with `-s read-only`, `approval_policy="never"`,
  `web_search="disabled"` and its shell (`shell_tool`, `unified_exec`), apps, plugins, browser/computer use, image tools,
  sub-agents, hooks and memories disabled. Codex's newer models write their tool calls as JavaScript run by Codex's
  code-mode host, which can only call the enabled tools.
- **one MCP server, the tool relay** (`engine_mcp_relay.py`), offering exactly the tools the platform built for this
  run. When the model calls one, the relay sends it over the run's private unix socket (random token per run) to the
  router, where it runs through the same loop as any other model's tool call (`agent_tool/runtime.py`): levels,
  approvals, the gateway's audit log, progress steps, facts and the tool budget. The result goes back to the CLI. Tools
  never run inside the CLI.
- a **scrubbed environment**: `PATH`, `HOME` (the engine's credential directory), `LANG`, `TMPDIR` in the run's
  directory, `SSL_CERT_*`/`TZ` and the CLI's own non-secret switches. The router's secrets (gateway and admin tokens,
  provider keys, `DATABASE_URL`, the dashboard password...) never reach it. The working directory is an empty per-run
  directory under `/data/engines/runs`, deleted afterwards. CLI output is never logged; errors show one redacted line.
- **stop and timeouts**: cancelling a run kills the CLI's process group; an engine silent for `HLA_ENGINE_TIMEOUT`
  seconds (default 600) fails the run. The platform's tool budget applies: when it is used up the CLI is stopped and the
  report is written without tools, as for other models.

**Plan limits.** The plan's own usage limits apply (Claude's 5-hour/weekly limits, ChatGPT's Codex limits). When one is
reached the run fails with "plan usage limit reached" (no retries; tool work already done is kept as a checkpoint) and
runs on that engine keep failing until the limit resets; switch the agent to an API model meanwhile. Heavy automated use
(schedules, projects) uses the same limits as your own interactive use of Claude / ChatGPT.

API: `GET /ui/api/subscriptions` -> `{engines: [{id: claude|chatgpt, label, installed, version, signed_in, account,
plan, models: [{id, label}], login: {state: idle|starting|waiting|done|error, url, user_code, needs_code, message},
last_error}]}`; `POST /ui/api/subscriptions/{id}/login`, `.../login/code` {code}, `.../login/cancel` (each returns the
`login` object), `POST .../logout` (the engine object), `POST .../test` -> `{ok, ms, reply, error}`.
Tests: `tests/test_engines.py` with fake CLIs in `tests/fake_engines` (no network, no real accounts).

## Websites

The **Websites** view lists every website on the website VM (`public-website`) from the gateway's inventory (see the
gateway README for what is read where; Traefik's config and passwords never leave the gateway): per site its public
URL(s) as links (new tab), environment (production / staging / test; a guess is marked "guessed"), status (Up, Up ·
protected, Degraded, Down) with the reason, HTTP code and response time, certificate days left, containers with state,
health and image, uptime and when it was checked. A summary counts the statuses per environment; filters, a search and
**Show hidden** narrow the list; **Details** shows the Traefik routes (rule, entry points, TLS, middlewares by name and
type, backends), LAN URLs, the compose folder and notes; **Edit** sets the environment, a display name, hidden (not
counted, no alerts) and notes. The page says where the routes came from (Traefik API, config files, Docker labels) and
what could not be read. **Refresh** asks the gateway for a fresh read (needs `POLICY_ADMIN_TOKEN`; within 15 s of the last
read it shows that read). `#websites` opens it; Overview has a one-line tile ("Websites: 4 up · 1 down") from the last check.

`websites.py`: `GET /ui/api/websites` (gateway read with `POLICY_ADMIN_TOKEN`, else `TOOL_GATEWAY_TOKEN`, plus overrides
and status history), `GET /ui/api/websites/summary` (from the stored statuses, no gateway call), `POST
/ui/api/websites/refresh`, `PUT /ui/api/websites/{id}` `{environment: production|staging|test|null, display_name, hidden,
notes}` (UI write). Tables `website_overrides` and `website_status` (id, status, since, last_checked, reason, ...).
Environment: the `hla.environment` container label wins, then the override, then the guess.

**Monitor**: every `HLA_WEBSITE_CHECK_MINUTES` (default 5, `0` = off; first pass 90 s after startup) the inventory is
read and the status per site stored. When a **production** site goes down, or comes back, it is logged and a push
notification is queued once per change (opens `/ui/#websites`); production sites that are down are a "Needs you" item
(kind `website`) until they are up again or no longer production / hidden. A failed read changes nothing; the view and
the tile say "couldn't check" with the reason and show the last known statuses.

**Agent tool** `websites` (`agent_tool/runtime.py`): read-only, optional `environment` and `search`, offered when the
agent has level ≥ 1 on both `public-website` and `traefik`; it calls `GET /v1/websites` with the task header and shows as
the step "Websites" (without the search text).

### Domains & routes

The Websites view has a **Domains & routes** panel (`public_routes.py`, HLA_ROUTES_V1) for the routes the SSH gateway
manages on Traefik (gateway README, "Managed routes"): one file on the traefik host, checked through the Traefik API,
rolled back when Traefik refuses. Hand-written Traefik routes are listed read-only and never changed; a new route may not
reuse their host names or router names (the gateway refuses with the router's name).

- **Domains…** edits your domains (suggestions come from the hosts of the existing routers).
- **+ New route**: subdomain + domain dropdown with a live preview of the address, environment (production → public,
  staging → LAN only + password; you can pick any protection), the backend as a container/port of `public-website` from
  the inventory (or another port), an optional path prefix. The DNS check runs as you type and says what record to add
  (`shop-staging A 203.0.113.10`); saving with missing DNS is allowed and warned (no certificate until DNS exists).
- The list shows each route's address, environment, protection (with the login user and the Infisical secret name of the
  password: read it in Infisical, Agent Generated project), backend and the container behind it, DNS state, Traefik
  router state and the certificate from the inventory. **Edit** and **Delete** (with a confirmation).
- **Requested by agents**: pending requests with **Approve** (creates the route through the gateway) and **Reject**; the
  same requests are "Needs you" items (kind `route_request`), with the project of the task that asked.

Every write needs the UI header and an approver (`approvals.require_approver`) and goes to the gateway with
`POLICY_ADMIN_TOKEN`; without it the panel says so. `GET /ui/api/routes`, `GET /ui/api/routes/domains` (cheap, for the
project form), `PUT /ui/api/routes/domains`, `POST /ui/api/routes/check-dns`, `PUT|DELETE /ui/api/routes/{name}`,
`POST /ui/api/routes/requests/{id}/approve|reject`. The inbox reads pending requests from the gateway at most every 20 s.

**Projects** have optional **Staging address** and **Production address** (domain dropdown + subdomain on the new-project
form; **Addresses…** on the project page; columns `staging_domain`, `production_domain`). They reach the planner and every
task prompt as a rule ("staging must be served at https://…; production at https://…; request each route with the
request_route tool when the container runs"). The project page shows them with **Create route**, which opens the route
form prefilled (you pick the container/port) until a managed route serves that address.

**Agent tool** `request_route` (`agent_tool/runtime.py`): host, backend_port, environment, optional protection, reason;
offered with level ≥ 4 on `traefik`; calls `POST /v1/routes/request` (a pending request only, with the task header) and
tells the model that the owner approves it. The step shows as "Route request · <host>", "waiting for the owner".

## Attachments: photos, files and documents

Attach files to a conversation message (📎, paste a screenshot, or drop files on the conversation), to a task on
Overview ("Attach files"), or to a project (reference files on the new-project form, or **Files** on the project page).
Images show as thumbnails, other files as chips with Preview (text, PDF, DOCX, XLSX) and Download. **Files** in a
conversation's header lists everything sent there and every file the agent made.

- **Storage**: `attachments` table plus the bytes once per sha256 on the `agent_attachments` volume
  (`/data/attachments`). Files picked in a message box are uploaded at once and wait (staged) until the message is
  sent; unsent ones are removed after a day. Deleting a conversation or project removes its files (the bytes on the next
  restart).
- **Types and safety**: the type comes from the content, not the name. Only PNG, JPEG, GIF and WebP display inline;
  HTML, SVG and everything else download as files (`Content-Disposition: attachment`, `nosniff`, a sandbox CSP).
  Limits: `HLA_ATTACHMENT_MAX_MB` (20) per file, `HLA_ATTACHMENTS_PER_MESSAGE` (10).
- **What the model gets**: images as image parts when the model accepts images (catalog metadata from OpenRouter/xAI,
  stored on Models → Sync; `HLA_VISION_MODELS` / `HLA_NO_VISION_MODELS` override), downscaled to 1568 px. Other
  models get a note instead, and the message box warns before sending. Documents go into the prompt as extracted text
  within `HLA_ATTACHMENT_PROMPT_CHARS`, marked untrusted, with lines that look like secret values and private keys
  masked. Project reference files reach the planner and every task.
- **Agent tools** (agents with server tools): `attachment_list` / `attachment_read` (page through any file here),
  `attachment_create` (a downloadable report, CSV, config…), and on free-shell servers `attachment_to_server` /
  `attachment_from_server`. Those two copy **text files only** (up to 512 KiB from a server): they use the gateway's
  text-only `write_file` / `read_file` path, so the gateway still checks the free shell and audits every copy.
  Agents without server tools see attached text and images in the prompt but cannot create files.

## The Chief (voice assistant backend)

The **Chief** is one agent with its own conversation ("Chief" in Conversations), made for talking to the platform,
e.g. by voice in the car: updates on everything and planning projects. `GET /ui/api/chief` creates both on first use
(once, also when called concurrently; again if either was deleted) and returns `thread_id`, `agent_id`, `model` and
`since` (the previous voice message, for "what's new since last time"). `PATCH /ui/api/chief {"model": ...}` sets its
model (an enabled model of the registry); its instructions and daily budget (`HLA_CHIEF_DAILY_BUDGET`, default $2
at creation) are edited like any agent's.

- **Platform tools** (`platform_*`, only for the agent with `agents.platform_tools`, i.e. the Chief): overview (projects,
  runs of the last 24 h, Needs you, cost today/this month, websites down, schedules due), changes since a time, projects
  and one project, runs and one run (steps, result, cost), conversations (search, last messages), logs (project log,
  approvals, runs, websites, costs, the gateway's audit log). Results are compact JSON, shortened to fit, and every text
  is masked like attachment text (agents never see secrets).
- **Actions**: create a project (planning starts), approve a project's plan, pause/resume/cancel a
  project, post a message into another conversation (it runs there with that agent's permissions, risk review and
  approvals; this is how the Chief gets server logs). Each needs `confirmed=true` **and** the owner's latest message
  must say yes (not no/wait) right after a Chief reply; otherwise the tool returns `needs_confirmation`.
- **Plan approval** (`platform_project_approve`, HLA_CHIEF_APPROVE_V1): only a project awaiting approval, only when
  the Chief's reply the owner said yes to named that project, and only when that yes was sent from an approver login
  (`approvals.approver_status` when the message was posted, stored as `agent_thread_messages.approver`; with
  `HLA_APPROVER_USERS`/`GROUPS` set that means a listed Authelia user). Logged as "<approver> (via Chief)". The Chief
  approves nothing else (commands, gates, routes, risk holds), never reads secret values and never changes
  permissions or unhinged mode. A Chief still on an earlier default instruction text gets the current one.
- **Voice**: `POST /ui/api/threads/{id}/messages` takes `"voice": true` (any conversation). The message is stored with
  `voice` (shown in the message list) and the run gets the spoken style: short sentences, the answer first, no
  markdown/ids/URLs read aloud, rounded numbers, "want details?" instead of long lists. Automatic continuations of a
  spoken request keep the style. Speech to text and text to speech are the dashboard's job.

## Voice: the Chief's car mode

**Chief** in the nav (on a phone under **More**) opens a full-screen voice view; `https://agents.example.com/ui/#chief`
opens it directly, and an installed app offers it as a shortcut (long-press the icon). Add that link to the home screen
for one tap in the car. The screen stays on while it is open (Wake Lock).

- **🎙 Tap to talk**, speak, and pause: after 1.5 s of silence what you said is sent. Tap again to send at once.
  While he speaks, a tap interrupts him and listens.
- **∞ Hands-free**: listen → send after a pause → speak the reply → listen again, until a minute without speech.
- **☰ Briefing**: asks what changed since your last visit, what needs you and what is running.
- **■ Stop** silences him and stops listening. **✕** leaves car mode.
- **⚙ Settings**: the Chief's model, language (English (US) by default, `HLA_VOICE_LANGUAGE`; remembered per device), how to listen
  and speak (this browser, or the server's speech service), voice and speed.
- Every turn is a normal message in the Chief's conversation (`/ui/api/chief`, sent with `voice: true`), so it is also
  in **Conversations** with its costs. The reply is spoken sentence by sentence while it streams in; Markdown, code and
  links are left out of the speech. Nothing is approved by voice: when something waits for you, **⚑ Open Needs you**
  appears.
- **Steering-wheel / headset button**: play/pause toggles listening while car mode is open, if the phone hands the
  button to the browser. Android does that mostly while the page plays audio (e.g. server speech); iOS rarely. Tap the
  screen when it does not react.

**Android (Chrome)**: speech recognition and speech work in the browser out of the box. Chrome's recognition sends the
audio to Google's speech service; pick **Server** under ⚙ to keep your voice on your own speech service.
**iPhone (Safari / home-screen app)**: speech works; Safari's recognition is less reliable, especially in the
home-screen app, so configure server recognition and pick **Server**. The first tap unlocks sound (iOS only plays sound
started by a tap).

**Speed (time to the first spoken words)**: pick a fast, non-reasoning model for the Chief (car mode ⚙ → Chief's model):
a reasoning model spends seconds thinking on every round. A spoken turn is built to need one model round: its prompt
carries a compact, masked platform snapshot (overview plus changes since your previous spoken message, at most
`HLA_CHIEF_SNAPSHOT_CHARS`, default 2500 characters) so updates and briefings are answered without tool calls; the
history is short (the last `HLA_VOICE_HISTORY_MESSAGES`, default 16, messages and at most
`HLA_VOICE_HISTORY_CHARS`, default 6000 characters, plus the summary); platform tool results are smaller (8 rows, 4000
characters); the risk review runs while the prompt is prepared (it still decides before anything reaches the model or a
tool); and the conversation summary is folded after the reply instead of before it. Car mode says "One moment" when
nothing arrived within 1.2 s, starts speaking at the first clause, and with server speech merges short sentences and
fetches up to three clips ahead, so they play back to back. Every run's **Timings** (Tasks → a task) show where the time
went: risk review, knowledge, snapshot, prompt, each model round with its first token, tools, first words, total.

**Server speech (optional)**: any OpenAI-compatible service (`/audio/transcriptions`, `/audio/speech`), set in `.env`
(`VOICE_STT_*`, `VOICE_TTS_*`, see `.env.example`); keys stay on the server. `GET /ui/api/voice/config` says what is on;
`POST /ui/api/voice/transcribe` takes the raw recording (webm, ogg, mp4/m4a, wav or mp3, checked by content, max
25 MB); `POST /ui/api/voice/speak` returns MP3.

**Kokoro TTS (in compose.yaml):** the `kokoro` service (Kokoro-82M via Kokoro-FastAPI, CPU, no published port, 2 CPUs /
2 GB) speaks the Chief's replies with US/UK English voices (no Dutch: keep car mode on English when you use it) once
its speak engine is set to **Server** (⚙). It runs only with the compose profile `voice` (`../setup.sh --voice` sets
`COMPOSE_PROFILES=voice` in `../.env`). `.env`: `VOICE_TTS_BASE_URL=http://kokoro:8880/v1`, `VOICE_TTS_MODEL=kokoro`,
`VOICE_TTS_VOICE=af_heart`, `VOICE_TTS_VOICES=af_heart,af_bella,af_nicole,am_michael,am_adam` (more: `bf_*`/`bm_*`
British). Start it with `docker compose --profile voice up -d`; the first request after a start loads the model (a few
seconds).

Another self-hosted option on this VM, for speech recognition as well (CPU is enough for the small model):

```yaml
# compose.yaml, next to agent-router
  speaches:
    image: ghcr.io/speaches-ai/speaches:latest-cpu
    restart: unless-stopped
    volumes: [speaches-models:/home/ubuntu/.cache/huggingface/hub]
# .env: VOICE_STT_BASE_URL=http://speaches:8000/v1  VOICE_STT_MODEL=Systran/faster-whisper-small
#       VOICE_TTS_BASE_URL=http://speaches:8000/v1  VOICE_TTS_MODEL=speaches-ai/Kokoro-82M-v1.0-ONNX  VOICE_TTS_VOICE=af_heart
# (add speaches-models under volumes:, and download the models once as the speaches README describes)
```

Or OpenAI: `VOICE_STT_BASE_URL=https://api.openai.com/v1`, `VOICE_STT_API_KEY=…`, `VOICE_STT_MODEL=whisper-1`, and the
same for TTS with `tts-1`.

## Configure and deploy

Settings live in `.env` (`.env.example` lists all of them; `../setup.sh` creates it and generates the secrets). Optional
CA files (`proxmox-ca.pem`, `pve2-ca.pem`, `infisical-ca.pem`) sit next to it and are not in git. The whole stack runs
as one Compose project from the repository root (`../compose.yaml`, see
[docs/configuration.md](../docs/configuration.md#compose-layout)); `./dc` here runs `docker compose` against it.

```bash
cd homelab-agents
docker compose up -d --build                          # deploy the current code
curl -s http://127.0.0.1:8088/health                  # {"status":"ok","database":"connected"}
docker compose logs --tail 60 agent-router            # if it does not come up
```

Schema changes are applied on startup and only add tables, columns and indexes. New volumes in `compose.yaml` (e.g.
`agent_attachments`) are created by `up`; the image creates `/data/attachments` owned by the app user, so a new volume
starts writable. Port 8088 listens on `HLA_LISTEN_ADDRESS` (default 127.0.0.1; a LAN IP when a reverse proxy runs
elsewhere). Approver identity headers from an SSO proxy are trusted only from `HLA_TRUSTED_PROXY_IPS`
([docs/security.md](../docs/security.md#https-and-single-sign-on)).

## Backups

The `backup` service (plain `postgres:17-alpine`, no build) runs `scripts/pg-backup.sh`: every night at
`BACKUP_TIME` (default 03:30, `BACKUP_TZ` default UTC) it makes a `pg_dump -Fc` of the platform database,
checks it with `pg_restore --list` and only then stores it as `backups/agent_platform-YYYYmmdd-HHMM.dump`
(next to `compose.yaml`, owned by root, mode 600, not in git). It keeps the newest dump of each of the last
`BACKUP_KEEP_DAYS` (14) days that have one, plus the newest of each of the last `BACKUP_KEEP_WEEKS` (8) weeks, and
deletes older ones. `backups/status.json` holds the last attempt and success, the file, its size, the error (or
`null`) and the next run; the container shows as unhealthy while the last attempt failed. The dumps contain every
conversation, task and note: treat them like the database itself.

First deploy (Docker creates `backups/` as root; if you make it yourself, use `sudo mkdir -m 700 backups`):

```bash
cd homelab-agents/agent-platform-starter
sudo ./dc up -d backup
sudo ./dc logs --tail 20 backup          # "nightly at 03:30 UTC ..." and the next run
sudo scripts/backup-now.sh               # one backup now, to check it works
sudo ls -l backups/ && sudo cat backups/status.json
```

`backup-now.sh` names its dump `...-manual.dump`; manual dumps are never rotated out, delete them yourself.
`./dc up -d --build --force-recreate agent-router` does not touch the backup service.

**Copy off the VM** (do this regularly; a dump on the same disk does not survive losing the VM):

```bash
# on the platform VM (replace admin with your login)
sudo install -m 600 -o admin backups/agent_platform-20261108-0330.dump ~/backups/
# from your machine
scp admin@192.168.1.10:backups/agent_platform-20261108-0330.dump .
```

**Restore.** The dump must be in `backups/` (copy it back there with `sudo install -m 600 FILE backups/`).
By default it goes into a **new** database next to the live one, which stays untouched:

```bash
sudo scripts/restore-backup.sh agent_platform-20261108-0330.dump      # -> database agent_platform_restore_20261108_0330
sudo ./dc exec database psql -U agents -d agent_platform_restore_20261108_0330   # look around
sudo ./dc exec database dropdb -U agents agent_platform_restore_20261108_0330     # remove the copy
```

`--replace` makes the dump the live database: it restores into a new database first, asks you to type the live
database name, stops `agent-router` and `queue-worker`, swaps the names in one transaction (the old database is
kept as `agent_platform_before_restore_<time>`), and starts both again. Check `/health`, then drop the old one.

```bash
sudo scripts/restore-backup.sh agent_platform-20261108-0330.dump --replace
```
