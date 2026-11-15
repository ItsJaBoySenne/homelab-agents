# Configuration

`./setup.sh` writes everything on this page for you. Read on to change a setting by hand, to understand what setup
did, or to install without it.

- [Files](#files)
- [Compose layout](#compose-layout)
- [Router settings](#router-settings-agent-platform-starterenv)
- [Gateway settings](#gateway-settings-ssh-gatewayenvgateway)
- [Secret store](#secret-store)
- [Inventory](#inventory-configinventoryyaml)
- [Adding a server](#adding-a-server)
- [setup.sh reference](#setupsh-reference)
- [Manual install](#manual-install-without-setupsh)
- [Split installs](#split-installs)
- [Moving from the per-folder install](#moving-from-the-per-folder-install)

## Files

| File | Created from | Holds secrets | Read by |
|---|---|---|---|
| `agent-platform-starter/.env` | `agent-platform-starter/.env.example` | yes (mode 600) | router, queue worker, Postgres, backup (and compose `${...}` in that folder) |
| `ssh-gateway/.env.gateway` | `ssh-gateway/.env.gateway.example` | yes (mode 600) | gateway API and CLI |
| `config/inventory.yaml` | `config/inventory.example.yaml` | no | router and gateway, read-only at `/config/inventory.yaml` |
| `.env` (top level) | written by setup | no | `docker compose` itself (`COMPOSE_PROFILES`) |

After changing a file: `docker compose up -d` (recreates what changed). `./setup.sh --check` validates all four.

Setup backs a file up before it changes it, as `<file>.backup-YYYYmmdd-HHMMSS` next to it (git ignores them; delete
old ones when you no longer need them, they contain the same secrets).

## Compose layout

`compose.yaml` at the top runs the whole stack as one Compose project, `homelab-agents`:

| Service | From | Notes |
|---|---|---|
| `agent-router` | `agent-platform-starter/compose.yaml` | dashboard and API on `HLA_LISTEN_ADDRESS:8088`; healthcheck on `/health` |
| `queue-worker` | same | background runs |
| `database` | same | Postgres 17, volume `agent_db` |
| `backup` | same | nightly `pg_dump` into `agent-platform-starter/backups/` |
| `kokoro` | same | profile `voice` only: text-to-speech for the Chief |
| `ssh-gateway-api` | `ssh-gateway/compose.tools-api.yaml` | no published port; reachable only on `hla-tool-net` |
| `ssh-gateway` | `ssh-gateway/compose.yaml` | profile `tools` only: the gateway CLI for one-off commands |

It does not copy those service definitions: it `include`s the component compose files, each with its own settings file
and project directory, and adds a small override from `deploy/`:

- `deploy/router.yaml`: mounts `config/inventory.yaml` read-only into the router, adds the router healthcheck, puts
  `kokoro` behind the `voice` profile and lets this project create the `hla-tool-net` network.
- `deploy/gateway.yaml`: mounts the inventory and the `gateway_secrets` volume (`/data/secrets`, the built-in secret
  store) into the gateway API and CLI, and adds a healthcheck.

So the hardening (read-only root file system, tmpfs, dropped capabilities, `no-new-privileges`, memory and PID
limits) is defined once, in the component folders, and both ways of running the stack share it. The per-folder files
still work on their own for [split installs](#split-installs); `./dc` in each folder runs `docker compose` against
the top-level project when one is installed (the service names are the same), so `./dc logs agent-router` and the
backup scripts work either way. `HLA_STANDALONE=1 ./dc ...` forces the folder's own compose files.

Volumes (named `homelab-agents_<name>`): `agent_db` (Postgres), `agent_attachments` (uploaded files),
`engine_auth` (subscription sign-ins), `gateway_audit` (the gateway's policy, audit and credential database) and
`gateway_secrets` (the built-in secret store).

Useful commands:

```bash
docker compose ps                                   # state and health
docker compose logs -f agent-router                 # router log
docker compose up -d --build                        # apply code or settings changes
docker compose --profile tools run --rm ssh-gateway --help    # gateway CLI
docker compose exec ssh-gateway-api python -m gateway.cli secrets list
```

## Router settings (`agent-platform-starter/.env`)

`.env.example` documents every setting next to its default. The important groups:

| Setting | Default | Meaning |
|---|---|---|
| `DASHBOARD_PASSWORD` | generated | Dashboard login. `./setup.sh --reset-password` replaces it. |
| `POSTGRES_PASSWORD` | generated | Database password. Set once; changing it later needs the same change inside Postgres. |
| `AGENT_PLATFORM_TOKEN` | generated | Bearer token for the HTTP API (`/tasks`, automation). |
| `HLA_LISTEN_ADDRESS` | `127.0.0.1` | Host address for port 8088. A LAN IP, or `0.0.0.0` for every interface. |
| `OPENROUTER_API_KEY` | empty | OpenRouter models (`provider/model` ids). |
| `XAI_API_KEY` | empty | xAI models called directly (`xai/<model>` ids). |
| `OPENROUTER_WORKER_MODEL`, `AGENT_ALLOWED_MODELS` | set by setup | Seed the model registry; afterwards Settings › Models decides. |
| `SSH_TOOL_ENABLED` | `true` (setup) | Agents get gateway tools. |
| `TOOL_GATEWAY_TOKEN`, `POLICY_ADMIN_TOKEN` | generated | Same values as in `.env.gateway`; must differ from each other. |
| `HLA_RISK_REVIEW`, `TYPESAFE_API_KEY` | `false`, empty | Optional TypeSafe risk scoring before runs. |
| `HLA_TRUSTED_PROXY_IPS`, `HLA_APPROVER_USERS`, `HLA_APPROVER_GROUPS` | empty | Approver identity from an SSO proxy ([security](security.md#https-and-single-sign-on)). |
| `HLA_VOICE_LANGUAGE` | `en-US` | Default car mode language: `en-US`, `en-GB`, `nl-BE`, `nl-NL`. Each browser can pick another. |
| `VOICE_TTS_*`, `VOICE_STT_*` | empty | Server speech for the Chief ([integrations](integrations.md#voice-the-chief)). |
| `BACKUP_TIME`, `BACKUP_TZ`, `BACKUP_KEEP_DAYS`, `BACKUP_KEEP_WEEKS` | `03:30`, `UTC`, `14`, `8` | Nightly backups. |
| `HLA_ENGINE_TIMEOUT`, `HLA_CLAUDE_SUB_MODELS`, `HLA_CHATGPT_SUB_MODELS` | defaults | Subscription engines. |

## Gateway settings (`ssh-gateway/.env.gateway`)

| Setting | Default | Meaning |
|---|---|---|
| `TOOL_GATEWAY_TOKEN` | generated | The router's token for tool calls (at least 32 characters). |
| `POLICY_ADMIN_TOKEN` | generated | Lets the dashboard change policy; without it policy is read-only. |
| `HLA_SECRETS_BACKEND` | `local` | `local` or `infisical` ([secret store](#secret-store)). |
| `HLA_SECRETS_KEY` | generated | Key of the built-in store: 32 random bytes, URL-safe base64. |
| `GATEWAY_CREDENTIAL_KEY` | generated | Key for credentials entered on the Connections page. |
| `INFISICAL_*` | empty | Only with `HLA_SECRETS_BACKEND=infisical`. |
| `GITHUB_TOKEN` | empty | GitHub pull requests (or store it on the Connections page). |
| `TRAEFIK_*`, `WEBSITE_BACKEND_HOSTS` | examples | Websites view and managed routes, only if you run Traefik. |

## Secret store

The gateway keeps SSH keys and service tokens in one of two places:

- **`local`** (default): an encrypted file in the `gateway_secrets` volume, key `HLA_SECRETS_KEY` in `.env.gateway`.
  Nothing else to run. Manage it with the gateway CLI inside the running container:

  ```bash
  docker compose exec -T ssh-gateway-api python -m gateway.cli secrets set NAME < value.txt      # value on stdin
  docker compose exec ssh-gateway-api python -m gateway.cli secrets list                          # names only
  docker compose exec ssh-gateway-api python -m gateway.cli secrets delete NAME
  docker compose exec -T ssh-gateway-api python -m gateway.cli secrets import-ssh-key web1 /dev/stdin < ~/.ssh/hla-fleet/web1
  ```

  Values are read from stdin so they never land in your shell history or the process list. Back up the key and the
  volume together: one without the other is useless. Generate a key by hand with
  `python3 -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"`.

- **`infisical`**: your existing [Infisical](https://infisical.com) project. Set `HLA_SECRETS_BACKEND=infisical`
  and `INFISICAL_URL`, `INFISICAL_CLIENT_ID`, `INFISICAL_CLIENT_SECRET`, `INFISICAL_PROJECT_ID`, `INFISICAL_ENV` for
  a machine identity with read access to the server folders. The folder layout is in
  [ssh-gateway/README.md](../ssh-gateway/README.md) and [ssh-fleet/README.md](../ssh-fleet/README.md).

## Inventory (`config/inventory.yaml`)

The servers and services the gateway may talk to. Mounted read-only into the router and the gateway at
`/config/inventory.yaml`; edit it on the host and run `docker compose up -d`. An empty inventory is fine.

```yaml
servers:
  - name: web1                     # the name agents and the Policy page use: lowercase, digits, dashes
    host: 192.168.1.20             # IP or DNS name
    user: svc-hla-agent            # default
    port: 22                       # default
    description: "Docker host with the family apps (Nextcloud, Immich)"   # agents read this
    sandbox: false                 # true: a disposable VM where agents may get a free shell
services:                          # each one optional
  proxmox: {url: "https://192.168.1.5:8006", nodes: [pve], reserved_vmids: [100, 101], verify_tls: true}
  omada: {url: "https://192.168.1.2:8043", site: Default, verify_tls: false}
  homeassistant: {url: "http://192.168.1.30:8123"}
  github: {}
public_ips: [203.0.113.10]         # your public address(es), for managed website routes
```

`config/inventory.example.yaml` is the annotated reference. Credentials never go in this file: they live in the
secret store.

## Adding a server

1. Add it: `./setup.sh` (answer *yes* to "Add a server?"), `./setup.sh --yes --server web1=192.168.1.20`, or edit
   the inventory.
2. Let setup generate its key (one Ed25519 key per server, in `~/.ssh/hla-fleet/`) and import it into the gateway.
3. Run the printed one-liner. It copies `ssh-fleet/02-install-on-server.sh` to the server and runs it with sudo:
   creates the locked `svc-hla-agent` account and installs the public key, usable only from the gateway's address.
4. On the Policy page, give the server a level (start with 1, read-only diagnostics).

[ssh-fleet/README.md](../ssh-fleet/README.md) covers doing this by hand, host key verification and the optional
`--admin` (passwordless sudo) mode.

## setup.sh reference

| Option | Environment | Meaning |
|---|---|---|
| `--yes`, `-y` | | Never ask (also automatic when stdin is not a terminal). |
| `--check` | | Validate, change nothing. Exit code 1 on problems. |
| `--no-start` | | Write the configuration only. |
| `--listen ADDR` | `HLA_LISTEN_ADDRESS` | Dashboard address. |
| `--provider P` | `HLA_PROVIDER` | `openrouter`, `xai`, `subscription` or `skip`. Keys come from `OPENROUTER_API_KEY` / `XAI_API_KEY` in the environment. |
| `--model ID` | `HLA_DEFAULT_MODEL` | Default model. |
| `--voice`, `--no-voice` | `HLA_VOICE=yes/no` | Kokoro text-to-speech. |
| `--server name=[user@]host[:port]` | `HLA_SERVERS` (space separated) | Add servers (repeatable). |
| `--ssh-keys`, `--no-ssh-keys` | `HLA_SSH_KEYS=yes/no` | Generate and import per-server keys. |
| `--gateway-ip IP` | `HLA_GATEWAY_IP` | Source address the servers see (for `from=`). |
| `--reset-password` | | New dashboard password. |
| | `FLEET_KEYS` | Where server keys go (default `~/.ssh/hla-fleet`). |
| | `HLA_WAIT_SECONDS` | How long to wait for the router to become healthy (default 240). |

Re-running is safe: existing values are kept (secrets are never regenerated, except the dashboard password with
`--reset-password`), placeholders from the examples are filled or blanked, and each changed file is backed up first.
If Docker is only usable with `sudo`, setup asks for your password right before it starts the stack.

## Manual install (without setup.sh)

```bash
cp agent-platform-starter/.env.example agent-platform-starter/.env
cp ssh-gateway/.env.gateway.example ssh-gateway/.env.gateway
cp config/inventory.example.yaml config/inventory.yaml        # then empty its lists, or keep what applies
chmod 600 agent-platform-starter/.env ssh-gateway/.env.gateway
# Fill in: DASHBOARD_PASSWORD, POSTGRES_PASSWORD, AGENT_PLATFORM_TOKEN (openssl rand -hex 32),
# TOOL_GATEWAY_TOKEN and POLICY_ADMIN_TOKEN (two different `openssl rand -hex 32`, same values in both files),
# HLA_SECRETS_BACKEND=local and HLA_SECRETS_KEY (see "Secret store"), a model key, SSH_TOOL_ENABLED=true.
./setup.sh --check
docker compose up -d --build                  # add --profile voice for Kokoro
```

## Split installs

The component folders still run on their own, e.g. the gateway on a separate, more locked-down VM. Each folder's
compose file expects an external Docker network `hla-tool-net` (`docker network create hla-tool-net`) and is run
with its `./dc` and `HLA_STANDALONE=1`:

```bash
cd ssh-gateway && sudo env HLA_STANDALONE=1 ./dc up -d --build ssh-gateway-api
cd ../agent-platform-starter && sudo env HLA_STANDALONE=1 ./dc up -d --build
```

The router reaches the gateway at `http://ssh-gateway-api:8099` on that network, so for two hosts you need a network
that spans both (for example a Docker Swarm overlay or a WireGuard link with matching names) and the inventory mounted
into both containers yourself. The single-project install is the supported default.

## Moving from the per-folder install

Earlier versions ran the two folders as separate Compose projects (`agent-platform-starter_*` and `ssh-gateway_*`
volumes). The single project uses `homelab-agents_*` volumes, so moving over means copying the data once:

```bash
cd agent-platform-starter && sudo env HLA_STANDALONE=1 ./dc down && cd ../ssh-gateway && sudo env HLA_STANDALONE=1 ./dc down && cd ..
for v in agent_db agent_attachments engine_auth; do
  sudo docker volume create homelab-agents_$v
  sudo docker run --rm -v agent-platform-starter_$v:/from:ro -v homelab-agents_$v:/to alpine sh -c 'cp -a /from/. /to/'
done
sudo docker volume create homelab-agents_gateway_audit
sudo docker run --rm -v ssh-gateway_gateway_audit:/from:ro -v homelab-agents_gateway_audit:/to alpine sh -c 'cp -a /from/. /to/'
sudo docker network rm hla-tool-net          # the project creates its own
./setup.sh                                    # keeps your existing .env files and fills in what is new
```

Keep the old volumes until everything checks out, then remove them with `docker volume rm`.
