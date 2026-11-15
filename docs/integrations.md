# Integrations

Every integration is optional. It is **off unless its block exists** under `services:` in `config/inventory.yaml`,
and agents can use it only after you raise its level on the Policy page (new services start at 0). After editing the
inventory run `docker compose restart` (it is read at start).

Credentials go in the gateway's secret store, never in the inventory, and never reach an agent:

```bash
docker compose exec ssh-gateway-api python -m gateway.cli secrets set HomeAssistant/HA_TOKEN   # asks without echo
docker compose exec ssh-gateway-api python -m gateway.cli secrets status                       # is the store usable?
```

| Integration | Secret names |
|---|---|
| SSH server `<name>` | `servers/<name>/SSH_PRIVATE_KEY` (`secrets import-ssh-key`), `servers/<name>/SSH_HOST_FINGERPRINT` (`secrets pin-host-key`) |
| Proxmox node `<node>` | `Proxmox/<node>/TOKEN_ID`, `Proxmox/<node>/TOKEN_SECRET` |
| Omada | `Omada/CLIENT_ID`, `Omada/CLIENT_SECRET` (`Omada/APPLICATION_NAME` optional) |
| Home Assistant | `HomeAssistant/HA_TOKEN` |
| GitHub | `GitHub/GITHUB_TOKEN`, or the Connections page, or `GITHUB_TOKEN` in `.env.gateway` |

The component READMEs hold the full reference for each integration: the levels and every operation.

- [Servers over SSH](#servers-over-ssh)
- [Proxmox VE](#proxmox-ve)
- [TP-Link Omada](#tp-link-omada)
- [Home Assistant](#home-assistant)
- [GitHub pull requests](#github-pull-requests)
- [MCP servers](#mcp-servers)
- [HTTP tools](#http-tools)
- [Deployments](#deployments)
- [Websites and routes](#websites-and-routes)
- [Subscription engines](#subscription-engines)
- [Voice: the Chief](#voice-the-chief)

## Servers over SSH

`servers:` in the inventory, one key per server (see [Adding a server](configuration.md#adding-a-server)). Levels:
1 read-only diagnostics, 2 install allowlisted packages, 3 restart allowlisted services and containers, 4 Compose
deploys, 5 shell commands you approve once each, 6 free shell (meant for a `sandbox: true` VM).
Reference: [ssh-gateway/README.md](../ssh-gateway/README.md#policy).

## Proxmox VE

```yaml
services:
  proxmox: {url: "https://192.168.1.5:8006", nodes: [pve], reserved_vmids: [100, 101], verify_tls: true}
```

Use an API token (`user@realm!name`): `Proxmox/pve/TOKEN_ID` and `Proxmox/pve/TOKEN_SECRET` for node `pve`. The Infrastructure view then shows nodes and VMs; with
higher levels agents can power VMs, take snapshots and create new Debian VMs (always with your approval of the exact
spec). `reserved_vmids` are never used for new VMs. With `verify_tls: true` put the node's CA
(`/etc/pve/pve-root-ca.pem`) in `ssh-gateway/certs/<node>-ca.pem` ([certs/README.md](../ssh-gateway/certs/README.md)). Reference: [ssh-gateway/README.md](../ssh-gateway/README.md#proxmox-ve-services-vms-the-platform-creates).

## TP-Link Omada

```yaml
services:
  omada: {url: "https://192.168.1.2:8043", site: Default, verify_tls: false}
```

Create an Open API application in **Client** mode in the controller (Settings → Platform Integration → Open API) and
store it as `Omada/CLIENT_ID` and `Omada/CLIENT_SECRET`. With `verify_tls: false` the controller's self-signed certificate is pinned by
fingerprint: compare it on Infrastructure → Omada and click **Trust**.
Reference: [ssh-gateway/README.md](../ssh-gateway/README.md#omada-controller-services).

## Home Assistant

```yaml
services:
  homeassistant: {url: "http://192.168.1.30:8123"}
```

Store a long-lived access token of a dedicated Home Assistant user as `HomeAssistant/HA_TOKEN`. Level 1 reads states and dashboards, 4 calls
services and saves dashboards (each save is backed up first), 6 allows any API call.
Reference: [ssh-gateway/README.md](../ssh-gateway/README.md#home-assistant-services).

## GitHub pull requests

```yaml
services:
  github: {}
```

A fine-grained token with *Contents* and *Pull requests* read/write on the repositories you allow, entered on
Connections (or `GITHUB_TOKEN` in `.env.gateway`). Allow repositories on Policy → GitHub. Projects can then work in
pull-request mode: agents push a branch and open a PR; you merge. Protect `main` with branch protection.
Reference: [ssh-gateway/README.md](../ssh-gateway/README.md#github-pull-requests-services).

## MCP servers

Register remote MCP servers (Streamable HTTP) on the **MCP** page. The gateway holds their tokens, every server is a
service `mcp:<name>` (level 0 until you raise it) and every tool has its own level. stdio-only servers run behind a
small HTTP proxy. Reference: [ssh-gateway/README.md](../ssh-gateway/README.md#mcp-servers-services).

## HTTP tools

Simple API calls you define on **Connections** (method, URL template, credential). Agents call them through the
gateway, which adds the credential, refuses private addresses unless you allow them, and audits every call.
Reference: [ssh-gateway/README.md](../ssh-gateway/README.md#http-tools-services).

## Deployments

Register a Git + Compose app (server, folder, repository, optional health URL) on the Policy page. Each deploy is tested
on the sandbox first, then deployed, verified and rolled back automatically on failure.
Reference: [ssh-gateway/README.md](../ssh-gateway/README.md#deployments-gitcompose-level-4).

## Websites and routes

If your public websites run behind Traefik, the **Websites** view lists them with live status, and **Domains & routes**
lets you give a site a public address (written to one managed Traefik file, checked and rolled back on error). Set the
`TRAEFIK_*` settings in `.env.gateway` and `public_ips` in the inventory.
Reference: [ssh-gateway/README.md](../ssh-gateway/README.md#websites-read-only-inventory).

## Subscription engines

Agents can run on your own **Claude Pro/Max** or **ChatGPT Plus/Pro** plan. The official Claude Code and Codex CLIs
are installed in the router image and do all the talking to the vendor; the platform never reads their tokens and
switches off all of their built-in tools (no shell, no files, no web), so every tool call still goes through the
gateway. Sign in under **Settings › Models › Subscriptions**; the sign-ins live in the `engine_auth` volume.
Models are `claude-sub/<model>` and `chatgpt-sub/<model>`. Use them in line with the vendor's terms for your plan.
Reference: [agent-platform-starter/README.md](../agent-platform-starter/README.md#subscription-engines).

## Voice: the Chief

The Chief's car mode (**Chief** in the menu, or `/ui/#chief`) listens and speaks with the browser out of the box.
`HLA_VOICE_LANGUAGE` sets the default language (`en-US`; also `en-GB`, `nl-BE`, `nl-NL`); each device can switch in
the car mode settings.

Server speech is optional:

- **Text-to-speech, Kokoro** (in the stack): `./setup.sh --voice`, or set `COMPOSE_PROFILES=voice` in `.env` and
  `VOICE_TTS_BASE_URL=http://kokoro:8880/v1`, `VOICE_TTS_MODEL=kokoro`, `VOICE_TTS_VOICE=af_heart` in the router `.env`,
  then `docker compose up -d`. English voices; needs about 2 GB RAM. Pick **Server** under car mode's ⚙.
- **Speech-to-text**: any OpenAI-compatible service (self-hosted speaches / faster-whisper, or OpenAI):
  `VOICE_STT_BASE_URL`, `VOICE_STT_API_KEY`, `VOICE_STT_MODEL`.

Reference: [agent-platform-starter/README.md](../agent-platform-starter/README.md#voice-the-chiefs-car-mode).
