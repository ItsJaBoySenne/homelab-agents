# Homelab Agents SSH Gateway

The only component that holds SSH access to the fleet. It reads keys from its secret store (an encrypted local file
by default, or Infisical) per request, connects only to the servers in the inventory and enforces what may run where; the agent platform only asks. Two entry points
share the same code and audit database:

- **API** (`ssh-gateway-api`, used by the agent platform): `POST /v1/execute` on the private Docker network
  `hla-tool-net`, port 8099, bearer `TOOL_GATEWAY_TOKEN`. No published ports. Do not expose it through Traefik.
- **CLI** (`ssh-gateway`, for you): diagnostics, and granting one-time approvals for exact commands.

## Policy

Every request names its agent. The gateway allows it at the **lower** of the agent's and the server's level
(`gateway/policy.py`, stored in the gateway's SQLite, edited on the dashboard's Policy page):

| Level | Allows (cumulative) |
|---|---|
| 1 | `diagnostic`: `identity`, `system`, `nginx-status`, `docker-status`, `disk`, `failed-units`, `containers`, `shares`, and with a `target`: `path-permissions` (path), `container-detail` / `container-logs` (container) |
| 2 | `maintenance install_package`: curl, jq, git (htop and nginx need 3) |
| 3 | `maintenance restart_service` (nginx), `restart_container` (any container name) |
| 4 | `maintenance compose_up` for project folders listed on that server; `deploy` of registered apps (below) |
| 5 | `approved_command`: an exact command you approved once, in the conversation or the CLI (single use, expires) |
| 6 | free shell: any command without approval, 300 s timeout (meant for the sandbox) |

New installs start at dev 5, sandbox 6, every other server 1, and no agents (the dashboard seeds the pre-policy SSH agent
at 5). **Unhinged mode** (1/8/24 h or until switched off) lets level-5 agents run any command without approval
on servers at level ≥ 1; a server at level 0 stays out. Those executions are audited as `agent_unhinged`.
Reading policy needs `TOOL_GATEWAY_TOKEN`; changing it needs `POLICY_ADMIN_TOKEN`, so the tool token alone
cannot raise its own permissions. Levels ≥ 2 also need sudo for `svc-hla-agent` on that server.

**Approve in chat:** when a level-5 command needs approval, the agent's reply shows it as a card in the conversation.
Approve sends `POST /v1/approvals {server, command, actor}` with `POLICY_ADMIN_TOKEN` (one execution within 5 minutes,
audited as `chat_approval` with the approver), then runs it as the agent, so the agent's level still applies.

**Audit log** (`gateway/audit.py`): every command, diagnostic, maintenance step, file write or read, deploy, Home
Assistant call, secret creation or use and chat approval is a row in the `audit` table, with the agent, what was done
(`detail`: the command as the agent sent it, with `{{secret:NAME}}` references; a path; a diagnostic name; a deploy ref),
the exit code and, for agent runs, the dashboard task (sent as the `X-HLA-Task` header). Credential-looking text
(bearer tokens, `PASSWORD=…`, `--password …`, `user:pass@` in URLs, GitHub/AWS/JWT-style tokens, long random strings)
and every value the gateway knows (Agent Generated secrets, its own tokens, stored credentials) are masked before the
row is stored and again when it is read. Known values are found also JSON-, percent- or HTML-escaped, base64-encoded,
as dict keys and cut off at the end of a text (`gateway/masking.py`, the same helper for every reply; values shorter
than 6 characters are not masked). Rows from before this version have only the digest. `GET /v1/audit` reads it, newest first,
with either token: `?agent=<id>|none&server=&action=<name>|<prefix>*&q=<text>&since=&until=<unix s>&limit=<1..200>&before_id=`
returns `{rows, next_before_id}` (and `facets` with servers, actions and agent ids when `facets=1`). An approved
command row carries `approved_by`, taken from the dashboard grant for the same command. The dashboard shows it on the
**Audit** page.

**File tools** (`gateway/files.py`, free shell only: level 6 or unhinged mode, otherwise 403
`file_tools_need_free_shell`). Both run as `svc-hla-agent` without sudo, 60 s timeout, audited as `agent_write_file` /
`agent_read_file` with a digest of server, path and content SHA-256:

- `mode: write_file`, `{path, content, append?}`: creates parent folders and writes (or appends) up to 256 KiB of UTF-8 text.
  The content travels on the SSH channel's stdin, never in the command. Reply adds `path`, `bytes` (file size afterwards), `sha256`.
- `mode: read_file`, `{path, offset? = 0, length? = 12000}` (max 12000 bytes): reply adds `path`, `size`, `offset`, `content`
  (invalid UTF-8 replaced), `eof`; `stdout` is left empty. Missing or non-regular files give a non-zero `exit_code` and `stderr`.

Paths must be absolute, ≤ 255 characters, without control characters (400 `invalid_file_request` with `detail`).
`/v1/execute` accepts request bodies up to 600 000 bytes for this; other endpoints keep 16 KiB, and commands keep 2048 characters.

Maintenance is self-healing and verified (`gateway/autonomy.py`): an install skips packages that are already
present, waits up to 60 s for the dpkg lock, refreshes the apt index and retries **once** on stale-index
errors, and confirms the result with `dpkg-query`. A restart confirms `systemctl is-active` and returns the
last 20 journal lines if the unit is not running. Responses include
`outcome: {verified, version | service_state, retried_after_index_refresh, error_class}`.

## Inventory (servers and integrations)

Servers and integrations are configuration, not code: one file, `config/inventory.yaml` at the repository root
(start from `config/inventory.example.yaml`), mounted read-only into the gateway and the agent platform at
`/config/inventory.yaml` (`HLA_INVENTORY` overrides the path). The loader is `gateway/inventory.py`;
`agent-platform-starter/inventory.py` is an identical copy (a test keeps them equal).

```yaml
servers:
  - name: server1          # lowercase letters, digits, . _ - (also the Policy and audit name)
    host: 192.168.1.10     # IP or DNS name, pinned: the gateway connects only here
    user: svc-hla-agent    # default; any Unix user (ssh-fleet/ creates svc-hla-agent)
    port: 22               # default
    description: "Docker host"
    sandbox: false         # true for the one disposable sandbox VM (free shell, deploy staging, reset)
    # role: website | traefik      the public website server / the Traefik host (Websites, Routes)
    # default_level: 1             the policy level it starts with (sandbox 6, others 1)
    # host_fingerprint: "SHA256:…" pinned ed25519 host key (also spelled host_key_fingerprint; a list is allowed)
    # vmid: 110 / node: pve        its Proxmox VMID and node (never offered for new VMs; the sandbox reset uses them)
services:                  # omit a block to disable that integration
  proxmox: {url: https://192.168.1.5:8006, nodes: [pve], reserved_vmids: [100], verify_tls: true}
  omada: {url: https://192.168.1.2, site: Default, verify_tls: false}
  homeassistant: {url: http://192.168.1.20:8123}
  github: {}
public_ips: []             # the router's public IPs (DNS checks of routes); empty = unknown
```

Validation is strict: unknown keys, duplicate names, two sandboxes, bad addresses or URLs stop the gateway at start
with one line naming the file, the entry and the problem (`INVENTORY ERROR: …`). A missing file at the default path is
an empty inventory: the platform runs (chat, agents) without server tools; `servers: []` is the same. A folder where
the file should be (Docker creates one when the host file is missing) is reported as such.

SSH: each server's key is `servers/<name>/SSH_PRIVATE_KEY` in the secret store (`SSH_KEY_PASSPHRASE` optional), the
connection goes to `host:port` as `user`, and only an **ed25519 host key** listed for that server is accepted (no
trust-on-connect, no `StrictHostKeyChecking=no`). The pinned key comes from `host_fingerprint` in the inventory, or
else `servers/<name>/SSH_HOST_FINGERPRINT` in the secret store, which the owner records explicitly:

```bash
python -m gateway.cli secrets import-ssh-key server1 /path/to/key     # or /dev/stdin, or - (piped)
python -m gateway.cli secrets pin-host-key server1                     # trust on first use, done by you: prints the
# fingerprint it recorded; compare with `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the server
```

Without either, every call to that server fails with a message naming `pin-host-key`. Older Infisical folders may
still hold `SSH_HOST` / `SSH_USER`: when present they must equal the inventory. Servers are prepared with
`../ssh-fleet`. VMs the gateway creates on Proxmox are added at runtime (`servers_dynamic`, key in the credential
store, fingerprint recorded on first contact; see **Proxmox VE** below). `python -m gateway.cli servers` lists them all.

## Secret store

`gateway/secret_store.py` is the one interface every module uses for secrets. `HLA_SECRETS_BACKEND` chooses the backend:

- **`local`** (default): `${HLA_SECRETS_DIR:-/data/secrets}/secrets.enc`, on the named volume `gateway_secrets`
  (the image creates `/data/secrets` owned by uid 65532, mode 700). The whole file is one Fernet token (AES-128-CBC +
  HMAC-SHA256) keyed by `HLA_SECRETS_KEY`, 32 random bytes in base64:
  `python -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"`.
  Names and values are both inside the encryption. Writes hold an exclusive `flock` on `secrets.lock`, re-read the
  file, write a temporary file (mode 0600, fsync) and rename it over `secrets.enc` (then fsync the folder): readers see
  the old or the new file, concurrent writers never lose a change. A wrong key is a clear `wrong_key` error and the
  file is never overwritten then. **Losing the key loses the secrets**; back up the key and the volume separately.
- **`infisical`**: the previous setup, unchanged: `INFISICAL_URL`, `INFISICAL_CLIENT_ID/_SECRET`,
  `INFISICAL_PROJECT_ID`, `INFISICAL_ENV` for the platform folders; `INFISICAL_GENERATED_PROJECT_ID` (+ optional own
  identity `INFISICAL_GENERATED_CLIENT_ID/_SECRET`, `INFISICAL_GENERATED_ENV`, `INFISICAL_GENERATED_PATH`) for the
  generated secrets. The INFISICAL_* settings matter only with this backend.

Two namespaces, as before: **platform** (what you put in; agents can never read or write it) and **generated**
(agent secrets, `{{secret:NAME}}`). Platform names are `FOLDER/KEY`; the folders are the Infisical folders of the old
layout, so an Infisical project keeps working:

| Secret | Used for |
|---|---|
| `servers/<name>/SSH_PRIVATE_KEY`, `SSH_HOST_FINGERPRINT`, `SSH_KEY_PASSPHRASE` (optional) | each inventory server |
| `Proxmox/<node>/TOKEN_ID`, `Proxmox/<node>/TOKEN_SECRET` | each Proxmox node |
| `Omada/CLIENT_ID`, `Omada/CLIENT_SECRET`, `Omada/APPLICATION_NAME` (optional) | the Omada Open API app |
| `HomeAssistant/HA_TOKEN` | Home Assistant |
| `GitHub/GITHUB_TOKEN` | GitHub (after the Connections page and `GITHUB_TOKEN` in `.env.gateway`) |

A server or service can name another folder with `secret_path` in the inventory. Generated names are flat
(`A-Z 0-9 _`); the reserved prefixes (`SSH_`, `INFISICAL_`, `TOOL_GATEWAY_`, `POLICY_ADMIN_`, `AGENT_PLATFORM_`, `HA_`,
`HLA_SECRETS_`, `GATEWAY_CREDENTIAL_`) can't be created there, and `{{secret:…}}` only ever resolves generated names.

Owner CLI (values come from stdin or a file, never from the command line, and are never printed):

```bash
G='sudo ./dc exec -T ssh-gateway-api python -m gateway.cli'
$G secrets status                                  # backend, usable or not, how many names
$G secrets list [--generated]                      # names only
printf %s "$TOKEN" | $G secrets set GitHub/GITHUB_TOKEN        # typed without echo when stdin is a terminal
$G secrets set APP_PASSWORD --generated < file
$G secrets delete HomeAssistant/HA_TOKEN
$G secrets import-ssh-key server1 /dev/stdin < ~/.ssh/hla/server1   # [--fingerprint SHA256:…]
$G secrets pin-host-key server1
$G secrets export TRAEFIK_BASIC_SHOP --generated --out /dev/stdout > pw.txt   # never to a terminal; or --out FILE (new, 0600)
```

The dashboard shows only the backend name (`GET /v1/policy` and `GET /v1/credentials` carry
`secret_store: {backend, configured}`) and mentions Infisical only when it is the backend. The dashboard's credential
store (Connections page, `gateway/credentials.py`) uses `GATEWAY_CREDENTIAL_KEY`, or when that is empty a key derived
from `HLA_SECRETS_KEY`, so one generated key is enough.

## Deployments (Git/Compose, level 4)

An app is a git checkout with a compose file in a folder on its server, registered on the dashboard's
Policy page (name, server, folder, repo URL, optional health URL such as `http://127.0.0.1:8080/`, staging on/off).
`gateway/deploy.py` then:

1. **Staging** on the sandbox: clone/fetch into `/srv/staging/<app>`, resolve the branch/tag/commit to an exact
   commit, `docker compose up -d --build`, verify every container runs and the health URL answers.
2. **Target**: refuse if the checkout has local changes, record the current commit, check out the commit that passed
   staging, rebuild and verify the same way. On failure it checks out the previous commit, rebuilds and verifies
   again, and reports `rolled_back` or `rollback_failed`.

Agents with level 4 on the app's server get a `server_deploy` tool. From the dashboard, deploys and rollbacks need
an approver; a rollback skips staging and only targets a commit that ran on that app before. History:
`GET /v1/deployments`; each phase may take up to 300 s.

One-time setup per app (example: `site` in `/opt/site` on public-website). Each server gets its **own read-only
deploy key**, so no Git credential passes through the gateway or reaches an agent.

```bash
# On the target server: root does the git and docker work (svc-hla-agent runs `sudo -n git` / `sudo -n docker`).
sudo ssh-keygen -t ed25519 -N '' -C deploy-site-target -f /root/.ssh/deploy-site
sudo cat /root/.ssh/deploy-site.pub        # add on GitHub: repo → Settings → Deploy keys (read-only)
sudo git clone -c core.sshCommand='ssh -i /root/.ssh/deploy-site -o IdentitiesOnly=yes -o BatchMode=yes' \
  git@github.com:you/site.git /opt/site    # accept GitHub's host key once; put the app's .env next to compose.yaml
# On sandbox-vm, as svc-hla-agent (then retake the 'clean' snapshot so the key survives resets):
ssh-keygen -t ed25519 -N '' -C deploy-site-sandbox -f ~/.ssh/deploy-site && cat ~/.ssh/deploy-site.pub   # second deploy key
```

Staging uses `.env.example` as `.env` when the repo has one. Agents have a free shell on the sandbox, so they could
read the sandbox's (read-only) deploy key: they can read that repository's code, never write to it.

## Home Assistant (services)

`homeassistant` is a service, not an SSH server: agents call `POST /v1/homeassistant` and the gateway adds the token.
It exists when the inventory has `services.homeassistant.url`, and reads `HomeAssistant/HA_TOKEN` from the secret
store on every request (an `HA_URL` kept there, from older Infisical setups, must equal the inventory URL).
The token never leaves the gateway. Its level is set in Policy like a server's (seeded at 1):

- 1: read (entities, `GET /api/...`, read-only WebSocket commands such as `lovelace/config` and the registries)
- 4: also call services (`POST /api/services/<domain>/<service>`), save dashboards and restore backups. Every save
  stores the current dashboard config first (table `ha_backups`, 50 per dashboard).
- 6 or unhinged mode: any `/api/` request and any WebSocket command

Use a dedicated HA user's long-lived token. A non-admin user can't save dashboards.

## Agent secrets (services)

`secrets` is a service too: passwords the agents create and use without ever seeing them (`POST /v1/secrets`,
`gateway/agent_secrets.py`). They live in the **generated** namespace of the secret store (see **Secret store**): in
`secrets.enc` with the local backend, or in the separate Infisical project `INFISICAL_GENERATED_PROJECT_ID` ("Agent
Generated", identity with **read and write** there) with the Infisical backend. The platform namespace with the
servers' SSH keys can't be reached this way.

- **create** `{name, kind}`: the gateway generates the value (32 letters and digits, 48 for `token`, 64 hex for `hex`),
  stores it with a comment naming the agent, and returns only `{{secret:NAME}}`. An existing name is not overwritten.
- **use**: `{{secret:NAME}}` in a command or in `write_file` content is replaced by the value just before it runs.
  Approvals, the approval card and the audit log see the reference. Each use is audited (`agent_secret_use`).
- **redact**: every value in that project is replaced by `[secret:NAME]` in everything a POST returns (command
  output, file reads, deploy logs, Home Assistant replies), cached for 60 s.

Levels: 1 list names and use references (the command still needs its own server level), 4 create, 6 or unhinged
mode replace an existing value. Seeded at 1. Redaction stops accidental leaks into chats and logs. It can't stop an
agent with root that deliberately encodes a value before printing it, so keep this project to application passwords.

## GitHub pull requests (services)

`github` is a service as well (`POST /v1/github`, `gateway/github.py`): project agents push a branch and open a pull
request instead of merging into main themselves. It exists when the inventory has `services.github`. The token stays
in the gateway: the Connections page, `GITHUB_TOKEN` in `.env.gateway`, or `GitHub/GITHUB_TOKEN` in the secret store
(`INFISICAL_GITHUB_PATH` or the inventory's `secret_path` change the folder), read on every request. Without one, calls answer 503 `github_not_configured`. Use a fine-grained token limited to the repositories
you allow, with Contents and Pull requests read/write (Commit statuses/Checks read for the checks summary).

Only repositories on the allowlist (Policy page → GitHub, stored as `github.repos` in the policy) can be reached; the
list starts empty. Levels (seeded at 1):

- 1: `pr_status {repo, number}` (state, mergeable, a checks summary, latest review per reviewer), `list_prs {repo, state}`
- 4: `create_pr {repo, head, base, title, body, draft}` (an open PR for the same branch is returned instead of a
  duplicate) and `comment {repo, number, body}`
- 6 or unhinged mode: `merge {repo, number, method squash|merge}`; a repository with **merges allowed** on the
  Policy page also allows it from level 4

Changes and merges are audited (`agent_github_change`, `agent_github_merge`). The dashboard refreshes PR states with
the admin token, which may only read. GitHub's error messages are passed on with the token removed.

There is no push operation: agents push their branch from the sandbox, whose deploy key is read-only, so projects
default to direct merges and PR mode is opt-in. Future work: a gateway `push_branch` operation (push a `task/*` branch
from the sandbox repository with the gateway's token, never `main`), so no write credential has to live on the sandbox.

**Connections page** (admin token, read only): `POST /v1/github {actor, operation: "status"}` reports where the token
comes from (`store` = credential store, `env` = `.env.gateway`, `secret_store`), the login and name from `GET /user`, and the
scopes of a classic token (`X-OAuth-Scopes`; fine-grained tokens have none); `{operation: "list_repos"}` lists the
repositories the token reaches (`/user/repos`, 100 per page, at most 300, `truncated` beyond) with their allowlist and
merge state. Agents cannot call either (`invalid_operation`). The token is never returned.

## HTTP tools (services)

The owner defines simple HTTP API tools on the dashboard (Connections → HTTP tools); agents call them through
`POST /v1/http_tool {agent_id, tool, params}` (`gateway/http_tools.py`). Definitions: `GET /v1/http_tools[/NAME]`,
`PUT /v1/http_tools/NAME`, `DELETE /v1/http_tools/NAME`, all admin token. A tool is:

- `name` (snake_case, 2-40), `description` (shown to the model), `method` GET/POST/PUT/PATCH/DELETE
- `base_url`: `https://host[:port][/base]`, fixed per tool: the host never comes from the agent; no user info, query or `..`
- `path` with `{param}` placeholders (each value URL-encoded as one segment; empty, `.` and `..` refused)
- `params`: `{name, in: path|query|body, type: string|number|integer|boolean, required, description, enum}`; GET and
  DELETE take no body; unknown parameters are refused
- `headers` (static, no `Host`/`Content-Length`/…, no credentials: names such as `Authorization`, `Cookie` or anything
  containing key/token/secret/password/auth are refused and point to the credential picker) and
  `auth {credential, in: header|query, name, prefix}`:
  the credential is read from the credential store and filled in only inside the gateway (default
  `Authorization: Bearer <value>`)
- `level` (default 1 for GET, 4 for writes), `timeout` ≤ 30 s, `max_response` ≤ 256 KiB (longer responses are cut with a
  note), `approval_required`, `allow_private`, `enabled`

**Policy**: one service `http_tools` (seeded at level 1). An agent may call a tool when min(agent level, `http_tools`
level) reaches the tool's level; unhinged mode as on servers. `GET /v1/policy/effective` lists, under
`services.http_tools.tools`, the tools the agent may call (name, description, method, parameters, never the URL or the
credential), and the agent runtime turns each into its own function `http_<name>`. **`approval_required`** is the
simple, safe option: there is no per-call approval for HTTP tools, so such a tool is offered and allowed only at level 6
or in unhinged mode.

**SSRF** (`gateway/netcheck.py`, the same rules as MCP servers): the host is resolved on every call; if *any* address
is not global the call is refused (`address_refused`, audited): private, loopback, link-local incl. 169.254.169.254,
`fd00:ec2::/32` and 100.100.100.200, CGNAT, multicast, reserved, IPv6 site-local, documentation and benchmark ranges
(192.0.2/24, 198.51.100/24, 203.0.113/24, 198.18/15, 192.0.0/24, 2001:db8::/32) and the gateway's own addresses, also
when wrapped in IPv6 (IPv4-mapped/compatible, SIIT, NAT64 `64:ff9b::/96` and `64:ff9b:1::/48`, 6to4, Teredo). The
host names of this stack (`ssh-gateway-api`, `agent-router`, `database`, `queue-worker`, `localhost`, …) are refused
before any lookup. `allow_private: true` lets a tool reach RFC 1918 / ULA / CGNAT addresses (your own internal APIs)
but nothing else on that list. The connection is made to a checked address (no second DNS lookup, so rebinding cannot
swap it), in DNS order; the next address is tried only when a connection cannot be made (nothing sent yet), with TLS
verified against the hostname. Redirects are never followed: the reply gives the status and the target host.

**Replies and audit**: `{tool, http_status, ok, content_type, seconds, truncated, json | text, note}`; credential values
are replaced by `[credential:NAME]` (also encoded forms and a value cut off by `max_response`). The agent runtime hands
results to the model labelled as untrusted data (`untrusted_data`, `source`, `note`). Every call is audited (`agent_http_read`,
`agent_http_change`, `agent_http_denied`, `admin_http_test` for the dashboard's Test button, which uses the admin token,
skips the level and keeps every other check) with the tool, method, host and path and the *names* of query parameters,
never their values, plus the HTTP status. Saving and deleting a tool is audited too (`http_tool_saved`, `http_tool_deleted`).

## MCP servers (services)

Agents can use the tools of remote MCP servers (`gateway/mcp.py`, stdlib only). The owner registers a server on the
dashboard's **MCP** page; the gateway connects, holds its auth and checks every call. Each server is a service
`mcp:<name>` with its own level, which starts at **0 (off)** until you raise it (MCP page or Policy page).

- **Transport: Streamable HTTP only** (MCP 2025-03-26 and later). Each JSON-RPC message is a POST to the server's
  endpoint with `Accept: application/json, text/event-stream`; JSON and SSE answers are both read (notifications and
  progress events in a stream are skipped). `initialize` → `notifications/initialized` → `tools/list` (all cursor
  pages, up to 200 tools) → `tools/call`. The `Mcp-Session-Id` from `initialize` and the negotiated
  `MCP-Protocol-Version` go on every later request; a session is cached per server and initialized again on 404 or
  after 30 idle minutes. The legacy HTTP+SSE transport (2024-11-05) is not supported.
- **stdio servers** are not run by the gateway. Put one behind a small proxy on a host and register its URL, e.g.
  `npx -y supergateway --stdio "npx -y @modelcontextprotocol/server-filesystem /srv/share" --outputTransport streamableHttp --port 8000`
  (URL `http://<host>:8000/mcp`) or `mcp-proxy --transport streamablehttp --port 8000 -- <server command>`. Protect
  the proxy (a header token stored as a credential, or a firewall rule that only lets 192.168.10.202 in).
- **Auth**: none, a bearer token, or a custom header (`X-API-Key`, …). The value is always a credential name from the
  credential store (`gateway/credentials.py`, entered on the Connections page), read for each request and masked
  (`[credential:NAME]`) in every reply and audit row. Keys in the URL's query string are refused; a URL that itself
  carries a token (e.g. `/s/<token>/mcp`) is stored as a credential instead (`url_credential: NAME`, "URL is stored as
  a credential" on the MCP page), and token-looking path segments of a plain URL are shown as `[redacted]`.
- **Registry**: table `mcp_servers` (name, url, auth, enabled, timeout 5-120 s (default 60), allow_private,
  allow_insecure, description, the tool snapshot with name, description, input schema and annotations, the refresh time
  and last error). Changing the URL drops the old snapshot until the next refresh. Deleting a server removes its level
  and tool overrides.
- **Levels**: a tool needs 4, or 6 when the server marks it `destructiveHint`. `readOnlyHint` does not lower a level:
  the hints come from the server and are not trusted, so set read-only tools to 1 yourself. **New tools**: a tool that
  appears on a later refresh (not in the previous snapshot) is marked new and stays off until the owner sets an
  override for it (the MCP page's **Enable** button sets its default level); the first snapshot of a new server marks
  nothing, since the server itself starts at level 0. Changed descriptions, hints and schemas are flagged on the
  refresh (the view's `changed`, and the `mcp_refresh` audit row names new and changed tools). Per tool the owner can
  set another level or disable it
  (`mcp.tools` in the policy: `{"server": {"tool": {"level": 1..6} | {"disabled": true}}}`, edited through
  `PUT /v1/policy`). An agent may call a tool when min(agent level, `mcp:<name>` level) ≥ 1 and reaches the tool's
  level; unhinged mode works as on servers (level-5 agents reach every tool of a server above level 0).
- **SSRF** (`gateway/netcheck.py`, as for HTTP tools): loopback, link-local, metadata (169.254.0.0/16,
  `fd00:ec2::/32`, 100.100.100.200), multicast, reserved and documentation addresses, the gateway's own addresses and
  the host names of this stack are always refused, also when a name resolves to them, or through IPv4-mapped,
  NAT64, 6to4 or Teredo forms. Private ranges (10/8,
  172.16/12, 192.168/16, 100.64/10, fc00::/7) need **Allow private network addresses**; `http://` needs **Allow plain
  http** and only reaches private addresses. The name is resolved once per request, every address must pass, and the
  connection goes to a checked address in DNS order, the next one only when a connection cannot be made (TLS still
  checks the certificate against the name). Redirects are never followed. SSE lines of any length are read, within
  the 2 MiB answer cap.
  Note that "private" includes the Docker networks on this VM: only tick it for servers you trust.
- **Calls**: arguments up to 100 000 characters of JSON, answers up to 2 MiB, result text cut at 20 000 characters
  with a note. Text, resource text and structured content are passed on; images and audio are summarised.
  `isError: true` comes back as `is_error` (a tool error, not a gateway failure). Every call is audited:
  `agent_mcp_read` (level-1 tools) or `agent_mcp_call`, with the tool, the argument names, a 160-character masked
  preview, ok/isError, the task id, and `agent_mcp_denied` for refused calls. Registry changes and refreshes are
  audited as `mcp_server_saved`, `mcp_server_deleted`, `mcp_refresh`.

| Endpoint | Token | Body / query → reply |
|---|---|---|
| `GET /v1/mcp/tools?agent_id=` | tool | `{tools: [{server, tool, title, description, input_schema, annotations, level}]}`: what this agent may call now |
| `POST /v1/mcp/call` | tool | `{agent_id, server, tool, arguments}` → `{service, server, tool, is_error, content, truncated}` |
| `GET /v1/mcp/servers` | admin | `{servers: [...], credentials: [names], credentials_configured, levels}` (tools without schemas) |
| `GET /v1/mcp/servers/NAME` | admin | one server with input schemas |
| `PUT /v1/mcp/servers/NAME` | admin | `{url \| url_credential, auth: {type: none\|bearer\|header, credential, header}, enabled, timeout, allow_private, allow_insecure, description, actor}` |
| `DELETE /v1/mcp/servers/NAME` | admin | header `X-HLA-Actor` |
| `POST /v1/mcp/servers/NAME/refresh` | admin | `{actor}`: connect, list the tools, store the snapshot (or the error) |

Tool results are untrusted data: the agent runtime labels them as such for the model. A tool that the server marks
read-only can still do anything the server lets it do with your credential, so give MCP servers least-privilege tokens.

## Websites (read-only inventory)

`GET /v1/websites` (`gateway/websites.py`, stdlib only) lists every website on the website VM: each container on
`public-website` with its public URL(s) from Traefik, its environment and its live status. It runs three fixed, read-only
scripts; no agent input ever reaches a command:

| Server | What is read | How |
|---|---|---|
| `public-website` | every container, running and stopped: name, image, state, health, status text, start time, port bindings (configured and live), compose labels (`com.docker.compose.project`, `.service`, `.project.working_dir`, `.project.config_files`), `traefik.*` labels and the owner labels `hla.environment`, `hla.url`, `hla.name` | `docker ps -a --format '{{json .}}'` and `docker inspect --format <template>`; the template never asks for `.Config.Env` |
| `traefik` | routers, services and middlewares | `curl -fsS --max-time 5 $TRAEFIK_API_URL/api/http/{routers,services,middlewares}`; only if that fails, every `*.yml`, `*.yaml`, `*.toml` in `TRAEFIK_DYNAMIC_DIR` (file provider) |
| `traefik` | one request per public URL, through Traefik itself, plus the certificate's expiry | `curl -sk --max-time 8 --resolve <host>:443:127.0.0.1 https://<host>/` and `openssl s_client -servername <host> -connect 127.0.0.1:443`, at most 50 URLs, 10 at a time, in one SSH call |

**Nothing secret leaves the gateway.** The API answers and the config files contain basicAuth hashes, allow-list ranges
and other middleware settings. They are parsed inside the gateway (a small YAML-subset parser, `tomllib` for TOML) and
only this is kept per route: router name, rule, host names (from `Host(...)`, literal `HostRegexp`), path prefix, entry
points, TLS and the cert resolver's name, middleware **names and types** (`basicauth`, `ipallowlist`, ...; never their
values), the service name and its backend URLs. Labels are filtered the same way (other labels, middleware values and the
container environment are dropped). Host names are checked against a host-name pattern and go to the check script on
stdin, which checks them again and quotes them; `TRAEFIK_API_URL` and `TRAEFIK_DYNAMIC_DIR` are validated and quoted.

**Matching**: a route belongs to the container whose published port (on `192.168.30.11`, `0.0.0.0` or all addresses)
is the backend's port, when the backend host is `192.168.30.11` or `public-website` (more names: `WEBSITE_BACKEND_HOSTS`,
comma-separated), or to the container that declares it in `traefik.http.routers.*` labels. Containers are grouped by
compose project (or `hla.name`). A route to the VM without such a container is listed as "route without a running
container"; a container without a route has "no public URL" and shows its LAN URL `http://192.168.30.11:<port>`; routes
to other machines are only counted (`other_routes`).

**Environment**: the `hla.environment` label (production/staging/test) wins, then the owner's override (stored by the
dashboard), then a guess from the project, container and host names (`staging` → staging; `alpha`, `beta`, `test`,
`preview`, `dev` → test; otherwise production), marked `environment_source: guess`.

**Status** per URL: `up` (2xx/3xx), `protected` (401/403 on a route with an auth or allow-list middleware), `degraded`
(other 4xx, slower than 3 s, certificate valid for less than 14 days or not trusted), `down` (5xx, no answer, expired
certificate). Per site: the worst URL (one URL up beats one that only asks for a login), made worse by containers that
are unhealthy or starting (degraded) or not running (down, or degraded when other containers of the site run; a one-shot
container that exited with 0 is fine). Without a URL the container state decides.

```
GET /v1/websites[?agent_id=…&environment=…&search=…][&refresh=1]
→ {sites: [{id, name, project, working_dir, environment, environment_source, status, status_reason, public, unmatched,
            urls: [{url, router, entrypoints, tls, protected, http_status, seconds, cert_days, status, reason, error}],
            lan_urls, label_url, containers: [{name, image, state, health, status, started, exit_code, restarts, service, ports}],
            routes: [{name, provider, rule, hosts, entrypoints, tls, cert_resolver, middlewares: [{name, type}], protected, service, backends}]}],
   routes_source: api|files|labels|none, routes_error, routes_files, routes_file_errors, containers_error, checks_error,
   checks_truncated, other_routes, checked_at, traefik_api, dynamic_dir, cached, age}
```

Cached for 60 s. `refresh=1` reads everything again: admin token only, at most once per 15 s (429 with `retry_after`).
Every inventory run is audited as `websites_refresh` (server `websites`, with a summary). **Levels**: the platform reads
it with either token; an agent (`agent_id`, tool token) needs level ≥ 1 on **both** `public-website` and `traefik` (the
same level that already allows read-only diagnostics there, so no new service), gets a compact copy without route
internals, and its reads are audited (`agent_websites_read`, `agent_websites_denied`).

Settings in `.env.gateway`: `TRAEFIK_API_URL` (default `http://127.0.0.1:8080`, as seen **on the traefik host**),
`TRAEFIK_DYNAMIC_DIR` (default `/opt/traefik/dynamic`), optional `WEBSITE_BACKEND_HOSTS`.

**Permissions on the servers** (the scripts use `sudo -n` only where it works without a password, otherwise they run as
`svc-hla-agent` and say what could not be read):

```bash
# public-website: let svc-hla-agent read Docker. Simplest: the docker group (note: docker group = root-equivalent
# on that VM; the gateway only ever runs the fixed read-only script above with it).
sudo usermod -aG docker svc-hla-agent
# Narrower alternative: passwordless sudo for exactly the two read commands the script uses.
echo 'svc-hla-agent ALL=(root) NOPASSWD: /usr/bin/docker ps *, /usr/bin/docker inspect *' | sudo tee /etc/sudoers.d/hla-websites
sudo chmod 440 /etc/sudoers.d/hla-websites && sudo visudo -cf /etc/sudoers.d/hla-websites
# (docker inspect through sudo could also show a container's environment to that user; the gateway's template never asks.)

# traefik: read access to the dynamic config folder (only used when the API is not reachable), and curl + openssl.
sudo apt-get install -y curl openssl
sudo setfacl -R -m u:svc-hla-agent:rX /opt/traefik/dynamic && sudo setfacl -d -m u:svc-hla-agent:rX /opt/traefik/dynamic
```

**Traefik API (optional, preferred)**: in Traefik's static config enable the API on an entry point that only listens on
the traefik host itself, e.g.

```yaml
api: {}                       # no `insecure: true` on a public entry point
entryPoints:
  traefik:
    address: "127.0.0.1:8080"   # in Docker: publish it as "127.0.0.1:8080:8080"
```

(or `api.insecure: true` with the `traefik` entry point published only on `127.0.0.1:8080`). Without the API the gateway
reads the files; `routes_error` then says so (e.g. "Traefik API not reachable (curl: (7) ...); routes read from
/opt/traefik/dynamic"). The HTTP checks connect to `127.0.0.1:443` (and `:80` for plain-http routes) on the traefik host,
so requests from there must be allowed by the routes' allow-lists for a 200; a 403 from an allow-list counts as protected.

## Managed routes (domains and subdomains)

`gateway/routes.py` (stdlib only) gives sites on `public-website` a public address on Traefik. The gateway owns exactly
**one** file on the traefik host, `TRAEFIK_MANAGED_FILE` (default `/opt/traefik/dynamic/hla-managed.yml`), and never
writes, renames or deletes any other file there. Hand-written files (`nw-staging.yml`, `paperclip.yml`, …) and Docker labels
are only **read** (through the Traefik API) to find conflicts; the dashboard lists them read-only.

**Desired state** lives in the gateway database (`managed_routes`: name, host, environment production|staging, backend
`http://192.168.30.11:<port>` (only the website VM: its address, `public-website` and `WEBSITE_BACKEND_HOSTS`), optional path
prefix, protection, basic-auth user and the password's secret name, cert resolver, extra entry points, project, status,
created/updated by and at). The whole file is rendered from it, deterministically (sorted, no timestamps), with a header
`# managed by the Homelab Agents platform — do not edit by hand; changes are overwritten`. Every router, service and middleware
is named `hla-<route>` / `hla-<route>-lan` / `hla-<route>-auth`. Every value passes a strict pattern first (host
names, route names, users, resolvers, entry points, backends, paths) and is written as a quoted string.

**Each change** (create, edit, delete, approve a request):

1. Read routers, services, middlewares and entry points from the Traefik API (`TRAEFIK_API_URL`, on the traefik host).
   If the API does not answer, nothing is changed. A name that another router/service/middleware already uses (`@file` names
   are global across all files) or a host that another router already serves (any provider) is refused with the name of
   that router, before anything is written.
2. Render and check the text locally: parsed back and compared with the database, no duplicate keys, allow-lists exactly
   `192.168.0.0/16` and `10.0.0.0/8`, no `172.16.0.0/12` anywhere outside the comment, `hla-` prefix on every name.
3. Upload with one fixed script (the content travels on stdin with its sha256; no request data is in the command): under an
   `flock`, the new content goes to a temp file in the backup folder, is checked against the sha256, the current file is
   copied to `<backup>/hla-managed.yml.prev` and `<backup>/hla-managed.yml.<UTC time>` (newest 30 kept), then written
   into the managed file in place and checked again.
4. Poll the Traefik API (every second, up to 10 s) until every managed router is there with its rule, service and
   middlewares, `enabled` and without an `error`, removed routers are gone, and **no other router that was enabled before
   stopped working** (e.g. a clashing middleware name in another file).
5. On any failure (Traefik error, not loaded in time, API gone, another router broken) the previous content (as read by the
   script just before the write) is written back and checked, the database is left as it was, and the error says what
   Traefik reported. The rendered file is also kept in the gateway database (`route_files`, newest 30).

Backups never go into the dynamic folder: Traefik's file provider loads **every** file there, whatever its name.

**Protections**: `public`; `lan_only` = `ipAllowList` with `192.168.0.0/16` and `10.0.0.0/8` only, default `ipStrategy`
(never `172.16.0.0/12`: Traefik sits on a Docker bridge in that range and IPv6 clients arrive through docker-proxy with
such addresses; the file says so in a comment); `basic_auth` = Traefik `basicAuth` (`removeHeader: true`);
`lan_only+basic_auth` = both (allow-list first). Staging defaults to `lan_only+basic_auth`, production to `public`. The
password is generated in the generated secrets as `TRAEFIK_BASIC_<ROUTE>` (letters and digits, 32
characters) through `gateway/agent_secrets.py` and hashed here with apr1 (MD5-crypt, `$apr1$`, the same as
`htpasswd -m` / `openssl passwd -apr1`). Only the hash goes into the file (and the gateway database); the password never
leaves the secret store and the gateway. The dashboard shows the user name and the secret's name; read the password in
Infisical (Infisical backend) or with `secrets export TRAEFIK_BASIC_<ROUTE> --generated --out FILE` (local backend).
Changing the user, or `refresh_password: true` after you changed the value, hashes it again. Removing basic auth keeps
the secret.

**TLS**: entry point `websecure` (plus validated extra entry points, never `traefik`), `tls.certResolver` = the resolver
most hand-written routers use (from the API), or `TRAEFIK_CERT_RESOLVER`. HTTP is redirected to HTTPS by the `web` entry point.

**DNS check**: the gateway resolves the name (A and AAAA) and compares it with `PUBLIC_IPS` (default `203.0.113.10`): `ok`;
`lan` (resolves to Traefik's LAN address `192.168.30.103` through a local override; public DNS is not visible from here);
`elsewhere`; `missing`; each with the exact record to add, e.g. `shop-staging A 203.0.113.10 (in the DNS zone of
example.com)`. Saving with missing DNS is allowed and warned: the certificate cannot be issued until the record exists.

**Domains**: the list in the gateway database (setting `route_domains`), edited on the dashboard. Every route host must be a
configured domain or a subdomain of one. The dashboard suggests the registrable domains seen in the existing routers.

```
GET    /v1/routes                     admin   routes (with Traefik router status and DNS), domains, suggestions, requests,
                                              other routers (name, provider, hosts, rule; never middleware values), cert resolver
GET    /v1/routes/domains             admin   the domains only (no SSH, no DNS)
GET    /v1/routes/requests[?status=]  admin   agent requests
PUT    /v1/routes/NAME                admin   {host, environment, backend_port | backend, path_prefix, protection, basic_user,
                                              cert_resolver, entrypoints, project_id, enabled, create, refresh_password, actor}
DELETE /v1/routes/NAME                admin   (X-HLA-Actor)
PUT    /v1/routes/domains             admin   {domains: [...], actor}
POST   /v1/routes/check-dns           admin   {host, protection}
POST   /v1/routes/request             tool    {agent_id, host, backend_port, environment, protection, reason, project_id}
POST   /v1/routes/requests/ID/approve admin   {actor, name, protection, backend_port, environment, path_prefix, basic_user}
POST   /v1/routes/requests/ID/reject  admin   {actor, note}
```

**Agents** never change Traefik. `POST /v1/routes/request` only stores a **pending request** (one per host; a second one
updates it) and needs level **≥ 4 on the `traefik` server** (the level that allows Compose deploys there; no new service).
The owner approves it on the dashboard ("Needs you"), which runs the normal change above. **Audit** (server `routes`):
`route_created`, `route_updated`, `route_deleted` (host, backend, protection, user and secret name, actor), `route_rollback`
(with Traefik's error and whether the restore was confirmed), `route_domains_saved`, `agent_route_requested`,
`agent_route_denied`, `route_request_rejected`. No password, hash or middleware value is ever in the audit log or a reply.

Settings in `.env.gateway`: `TRAEFIK_MANAGED_FILE` (default `/opt/traefik/dynamic/hla-managed.yml`; must end in `.yml`),
`TRAEFIK_ROUTE_BACKUP_DIR` (default `/opt/traefik/managed-backups`; refused inside the managed file's folder),
`TRAEFIK_CERT_RESOLVER` (optional), `PUBLIC_IPS` (comma-separated, default `203.0.113.10`), and from the inventory
`TRAEFIK_API_URL`, `WEBSITE_BACKEND_HOSTS`.

**One-time setup on the traefik host** (as root; least privilege: `svc-hla-agent` can write the one file and the backup
folder, nothing else; it cannot create files in the dynamic folder, so it cannot add other configuration there):

```bash
sudo apt-get install -y acl util-linux curl          # setfacl, flock; sha256sum and base64 come with coreutils
# The managed file, created by root, readable by Traefik, writable by svc-hla-agent through an ACL.
printf '# managed by the Homelab Agents platform\nhttp: {}\n' | sudo tee /opt/traefik/dynamic/hla-managed.yml >/dev/null
sudo chown root:root /opt/traefik/dynamic/hla-managed.yml && sudo chmod 644 /opt/traefik/dynamic/hla-managed.yml
sudo setfacl -m u:svc-hla-agent:rw /opt/traefik/dynamic/hla-managed.yml
# The dynamic folder: search (and read, already granted for the inventory) only; no write.
sudo setfacl -m u:svc-hla-agent:rx /opt/traefik/dynamic
# The backup folder, outside the dynamic folder.
sudo install -d -o root -g root -m 750 /opt/traefik/managed-backups
sudo setfacl -m u:svc-hla-agent:rwx /opt/traefik/managed-backups
sudo setfacl -d -m u:svc-hla-agent:rwX /opt/traefik/managed-backups
# Check: the file and the backup folder are writable, the dynamic folder is not.
sudo -u svc-hla-agent sh -c 'test -w /opt/traefik/dynamic/hla-managed.yml && test -w /opt/traefik/managed-backups && ! test -w /opt/traefik/dynamic && echo ok'
```

Why in place and not `mv`: an atomic rename would need write access on the dynamic folder, which would let the service
user create any file Traefik loads. The write is one `cat` of a small file under an `flock`, followed by a sha256 check;
Traefik may see the file empty for an instant (it then serves the hand-written routes only) and reloads on the final
content, which step 4 confirms. Keep the file in place: if it is deleted or replaced by an editor (new inode), the ACL is
gone; run the three file commands again. The file must exist before the first route (the gateway says so if it does not).

## Proxmox VE (services, VMs the platform creates)

`gateway/proxmox.py` (stdlib + paramiko) talks to the Proxmox VE API of every node in the inventory
(`services.proxmox.nodes`). Each node is a service `proxmox:<node>` with its own level, **0 (off)** until you raise it
on the Policy page or the dashboard's **Infrastructure** view. A node is a name (it uses `services.proxmox.url`) or
`{name, url, tls_name, label}` for a separate install. VMIDs in `reserved_vmids` and every server's `vmid` are never
offered for new VMs.

**Credentials**: `Proxmox/<node>/TOKEN_ID` (`user@realm!name`) and `Proxmox/<node>/TOKEN_SECRET` in the secret store,
cached 60 s (older folders may also hold `NODE_IP` / `NODE_PORT`, which must then equal the inventory URL). The secret
never leaves the gateway: it is replaced by `[secret:PVE_<NODE>_TOKEN_SECRET]` in every reply and audit row.

**TLS** (`verify_tls: true`, the default): the gateway connects to the node's address and checks the certificate chain
against that node's own CA, `certs/<node>-ca.pem` (copy the node's `/etc/pve/pve-root-ca.pem`; without the file the
system CAs are used, for a publicly trusted certificate), and the name against `tls_name` (default: the URL's host),
sent as SNI, so a certificate that does not list the IP still works. The image copies `certs/` to `/srv/certs`.
Overrides: `PROXMOX_CA_DIR`, `PROXMOX_CA_<NODE>` (file; a missing explicit file is an error), `PROXMOX_TLS_NAME_<NODE>`.
`verify_tls: false` switches both checks off for every node (encrypted, not authenticated); not recommended.

**Levels** (min(agent level, `proxmox:<node>` level); unhinged mode lifts level-5 agents to 5, never to 6):

| Level | Operations |
|---|---|
| 1 | `inventory` (node status, VMs with status/CPU/memory/disk/tags/IP from cloud-init or the guest agent, storages with content types and free space, ISO/template/import images, bridges, next free VMID, what the node token may do), `snapshots`, `preview_create` |
| 4 | `start`, `shutdown`, `stop`, `reboot`, `snapshot_create` |
| 5 | `create_vm` and `create_audit_token`, each after you approved the exact request (approval card) |
| 6 | `snapshot_rollback`; `create_vm` / `create_audit_token` without approval (also in unhinged mode); `destroy_vm` only with an approval of that VM **every time** |

Never touched by agents: VMs named in `PROXMOX_PROTECTED_NAMES` (default `agents-vm,secrets-vm`; power and rollback
refused, the dashboard may), VMIDs 100–108 and 204 are never created, and `destroy_vm` only reaches VMs this gateway
created (row in `managed_vms` **and** tag `hla-managed` **and** the same name). Containers (LXC) are listed only.

**Approvals** reuse the chat approval path. Below the needed level the gateway answers `403 approval_required` with
`approval_text` (the readable spec, e.g. `proxmox create_vm on pve2 / vmid: 205 / name: monitoring / …`) and the normalised
`request`. The dashboard shows the text on the approval card; approving sends `POST /v1/approvals {server: "proxmox:pve2",
command: <that text>, actor}` (admin token, one use within 5 minutes, audited as `chat_approval`) and replays the stored
request as the agent, so its level still applies. The digest is of the node service and the exact text, so another spec,
node or VM unlocks nothing.

**create_vm** `{node, vmid, name, cores 1-8, memory 512-16384 MiB, disk 8-256 GiB, storage, bridge, ip "a.b.c.d/nn",
gateway, dns (1-3 IPv4), macaddr?, image "debian-13"}`, checked live on both nodes: VMID free on both (and not 100–108/204),
name unused as VM, server or service, storage with `images` content and enough space, bridge exists, IP inside
`PROXMOX_ALLOWED_NETS` (default `192.168.10.0/24`), not the network/broadcast/gateway, not configured on any VM, not a
server's address and nothing answering there (TCP 22/80/443), MAC unicast and unused. Then:

1. Image: with an `import` storage (PVE ≥ 8.3) the Debian 13 generic cloud image is used from
   `<storage>:import/debian-13-genericcloud-amd64.qcow2`, or downloaded there with `download-url` and the SHA-512 the gateway
   read from Debian's `SHA512SUMS` (every line must be `<128 hex>  <file>`). Without import support a template named
   `debian-13*` is cloned (full clone). With neither the request fails and says what to set up.
2. The VM: `scsi0: <storage>:0,import-from=…`, resized to the disk size, `ide2: <storage>:cloudinit`, `serial0: socket`,
   `vga: serial0`, `agent: enabled=1`, `ciuser: svc-hla-agent`, `sshkeys` (URL-encoded), `ipconfig0`, `nameserver`,
   tag `hla-managed`, start on boot, and a description with who asked and who approved. Then started. Every task UPID is
   polled until it stops (with time limits).
3. Onboarding: before the VM is created the gateway generates an ed25519 key pair; the private key goes into the credential
   store as `SSH_KEY_<NAME>` (kind `other`, encrypted), the public key into cloud-init. After boot it waits up to 5 minutes
   for SSH, records the ed25519 host key fingerprint on first contact (trust on first use: compare it with the VM console,
   `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`), checks that `svc-hla-agent` logs in and has `sudo -n`, installs
   and starts `qemu-guest-agent`, and registers the server.
4. Registered servers live in `servers_dynamic` (name, host, user, key credential, host fingerprint, node, VMID, created
   by/at) and are added to the server list at **level 0**. `execute()` uses their key from the credential store and the
   pinned fingerprint; static server names can never be taken. Raise the level like any server's.

Platform-created VMs get **passwordless sudo** for `svc-hla-agent` (Debian cloud images give the cloud-init user that),
so they are like servers prepared with `ssh-fleet --admin`: the level is what limits agents there. If onboarding fails the
VM stays (status `onboarding_failed`) and the dashboard's **Onboard** button retries; one create or destroy runs per node
at a time.

**destroy_vm** `{node, vmid}`: stops the VM, destroys it with `purge=1` and `destroy-unreferenced-disks=1`, removes the
server (and its level and deploy apps) and deletes `SSH_KEY_<NAME>`.

**create_audit_token** `{node, rotate?}`: user `hla-monitoring@pve` (created if missing) with role `PVEAuditor` on `/`,
and a privilege-separated token `hla-monitoring@pve!monitoring` with `PVEAuditor` on `/`. Its id and secret are stored
in the Agent Generated project as `PVE_<NODE>_AUDIT_TOKEN_ID` and `PVE_<NODE>_AUDIT_TOKEN_SECRET` (e.g.
`PVE_PVE2_AUDIT_TOKEN_SECRET`), so agents write `{{secret:PVE_PVE2_AUDIT_TOKEN_SECRET}}` into an exporter config without
seeing it. An existing token whose secret is not stored is only replaced with `rotate: true`. If the node token lacks
the privileges, the reply (and the dashboard) gives the `pveum` commands to run instead.

| Endpoint | Token | Body / query → reply |
|---|---|---|
| `GET /v1/proxmox/inventory` | tool or admin | `?agent_id=&node=`: the nodes the agent has level 1 on (audited `proxmox_inventory`); admin: both nodes, `servers` (created here, with level) and `managed` VMs; `refresh=1` (admin) skips the 15 s cache |
| `POST /v1/proxmox` | tool | `{agent_id, node, operation, …}`; `approval_required` as above |
| `POST /v1/proxmox` | admin | `{actor, node, operation, …}`: the dashboard acts as the owner (no approval card), plus `onboard {vmid}` |

**Audit**: every change and refusal: `proxmox_<operation>` (node service, VM, spec summary, task UPID, result, approver),
`proxmox_approval_required`, `proxmox_denied`; agent reads `proxmox_inventory`; the dashboard's reads are not logged.

**Node token privileges.** `GET /access/permissions` of the token is shown per node on the Infrastructure view (Read,
Power, Snapshots, Rollback, Create VM, Download image, Destroy, Audit token). Proxmox decides in the end; a refusal is
passed on with its permission check (`proxmox_permission_denied`). A least-privilege alternative to a root token
(run on each node as root; on PVE 8 use `VM.Monitor` instead of `VM.GuestAgent.Audit`):

```bash
pveum role add HomelabAgents --privs "Sys.Audit Sys.AccessNetwork VM.Audit VM.Allocate VM.Clone VM.PowerMgmt VM.Snapshot \
  VM.Snapshot.Rollback VM.Config.Disk VM.Config.CPU VM.Config.Memory VM.Config.Network VM.Config.Cloudinit \
  VM.Config.Options VM.Config.HWType VM.GuestAgent.Audit Datastore.Audit Datastore.AllocateSpace Datastore.AllocateTemplate SDN.Use"
pveum user add hla-agents@pve --comment "Homelab Agents SSH gateway"
pveum user token add hla-agents@pve gateway --privsep 1      # prints the secret once: TOKEN_SECRET
for p in /vms /storage /nodes /sdn/zones/localnetwork; do
  pveum acl modify "$p" --users hla-agents@pve --roles HomelabAgents
  pveum acl modify "$p" --tokens 'hla-agents@pve!gateway' --roles HomelabAgents
done
# secret store: Proxmox/<node>/TOKEN_ID = hla-agents@pve!gateway, Proxmox/<node>/TOKEN_SECRET = <the secret>
#   (python -m gateway.cli secrets set Proxmox/<node>/TOKEN_SECRET, value on stdin)
```

`create_audit_token` also needs `User.Modify` on `/access`, `Realm.AllocateUser` on `/access/realm/pve` and
`Permissions.Modify` on `/`. `Permissions.Modify` on `/` lets the token grant itself anything, so rather leave it out and
run the four commands the gateway prints instead:

```bash
pveum user add hla-monitoring@pve --comment "Read-only monitoring (Homelab Agents)"
pveum acl modify / --users hla-monitoring@pve --roles PVEAuditor
pveum user token add hla-monitoring@pve monitoring --privsep 1 --comment "Homelab Agents monitoring"
pveum acl modify / --tokens 'hla-monitoring@pve!monitoring' --roles PVEAuditor
```

then store the id and the printed secret in the Agent Generated project as `PVE_<NODE>_AUDIT_TOKEN_ID` / `_SECRET`.

Settings in `.env.gateway`: `PROXMOX_ALLOWED_NETS` (comma-separated IPv4 networks for new VMs, default `192.168.10.0/24`),
`PROXMOX_PROTECTED_NAMES`, and the CA/TLS overrides above.

## Omada controller (services)

`gateway/omada.py` (HLA_OMADA_V1) reaches the Omada Software Controller at `services.omada.url` of the inventory
through its **Open API**, as service `omada:controller` (starts at level 0 = off). `site` there is the site used when
a request names none and the controller has several. Agents use the `omada` tool; the dashboard shows the connection on
the Infrastructure view.

**Setup.** In the controller: Global View → Settings → Platform Integration → Open API → add an application in **Client**
mode with the sites it may manage. Put its `CLIENT_ID` and `CLIENT_SECRET` (and optionally `APPLICATION_NAME`) in the secret store as `Omada/CLIENT_ID`,
`Omada/CLIENT_SECRET`, `Omada/APPLICATION_NAME`. Restart the gateway, open Infrastructure → Omada, compare the shown
certificate fingerprint with the one your browser shows for the controller URL, and click **Trust**. Then set the
agents' level there (or on the Policy page).

**Flow.** `GET /api/info` (no credentials) → `omadacId`; `POST /openapi/authorize/token?grant_type=client_credentials`
`{omadacId, client_id, client_secret}` → an access token (renewed a minute before `expiresIn`, and once more when the
controller answers -44112/-44113 or HTTP 401); calls go to `/openapi/v1/{omadacId}/...` with
`Authorization: AccessToken=<token>`. Replies are `{errorCode, msg, result}`; a non-zero `errorCode` is an error.

**TLS.** With `verify_tls: true` in the inventory the chain (against `certs/omada-ca.pem` / `OMADA_CA` when present, else
the system CAs) and the host name are always verified. Otherwise: with `certs/omada-ca.pem` (or `OMADA_CA`) the chain is verified against that file only (`OMADA_TLS_NAME` adds a
name check). Otherwise the leaf certificate's SHA-256 is pinned: first contact records it as *pending*, and nothing with
credentials (not even the token request) is sent until the owner trusts it; a different certificate later blocks every
call again (`certificate_changed`) until the owner trusts the new one. Only `/api/info` is read from an untrusted certificate.

**Levels** (min(agent, `omada:controller`); unhinged mode never applies here, also not in `/v1/policy/effective`):

| Level | Operations |
|---|---|
| 1 | `status`, `sites`, `clients` / `devices` (per site, filter by mac, ip or name; at most 500 scanned), `get` (any path below `/openapi/v1/{omadacId}`, text in parts of 10000 characters) |
| 5 | `write` (POST/PUT/PATCH/DELETE below `/sites/{siteId}/...`, body up to 3000 characters) after the owner approved the exact text `omada write on controller / method / path / body` (approval card, single use) |
| 6 | `write` without approval |

Every call is audited (`omada_*`, with the path and the write body); the client secret and the tokens are masked in every
reply and audit row (`[secret:OMADA_CLIENT_SECRET]`, `[secret:OMADA_ACCESS_TOKEN]`). The dashboard (admin token) reads
`status`/`sites` without audit rows and is the only one that can `trust_certificate`.

The Open API's exact paths for site settings (DHCP reservations, LAN networks) differ between controller versions; agents
are told to read them from the controller's API documentation and with `get` before they propose a write.

## Safety notes

- Verify each VM's fingerprint on its console; a fingerprint from first contact is not independently trusted.
- Keep verified backups of the VMs, particularly Traefik, database and file servers.
- `agents-vm` and `secrets-vm` are deliberately not in the server list, and agents cannot power or roll them back on Proxmox.
- The host key of a VM the gateway created is trusted on first contact; check it on the VM console (Infrastructure view).
- Restrict SSH to 192.168.10.202 at the firewall; the `from=` restriction on the key is not a firewall.
- Approvals are not identity-bound: anyone with Docker access on this VM can grant them.
