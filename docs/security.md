# Security

Homelab Agents gives language models the ability to act on your servers. The design assumes a model can be wrong,
confused or manipulated (for example by text it reads on a web page or in a log file) and limits what that can do.

## Model

**The gateway is the only component with credentials.** SSH private keys, API tokens and generated passwords live in
the gateway's secret store (built-in encrypted file or Infisical). The router, the queue worker and the models never
receive them. When an agent needs a secret in a file or command it writes `{{secret:NAME}}`; the gateway fills it in
on the server, and anything that comes back shows `[secret:NAME]` instead of the value.

**Every call is checked twice.** The router only offers an agent the tools its level allows, and the gateway checks the
same policy again on every call with its own copy. An agent acts at the **lower** of its own level and the target's:

| Level | Allows |
|---|---|
| 0 | Nothing |
| 1 | Read-only diagnostics (Home Assistant: read; secrets: list and use) |
| 2 | Install allowlisted packages |
| 3 | Restart allowlisted services and containers |
| 4 | Compose deploys and registered app deploys (Home Assistant: call services, save dashboards; secrets: create) |
| 5 | Shell commands you approve, once each |
| 6 | Free shell without approval (meant for a disposable sandbox VM) |

Services (Proxmox, Omada, GitHub, MCP servers, HTTP tools) have their own levels and start at 0. Creating or destroying
VMs, managed routes and similar changes always wait for your approval of the exact request. **Unhinged mode**
(1 h, 8 h, 24 h or until switched off) lets level-5 agents skip approvals; it is off by default and every action is
still audited.

**Narrow SSH access.** Each server gets its own Ed25519 key, installed on a locked service account
(`svc-hla-agent`, no password) with `from="<gateway address>"`, and agent, X11 and port forwarding disabled. No sudo
is granted unless you choose `--admin` for a disposable VM. The gateway pins each server's host key.

**Hardened containers.** Read-only root file systems, `cap_drop: ALL`, `no-new-privileges`, tmpfs for scratch space,
memory and PID limits on the gateway. The gateway publishes no port; only the router reaches it, over the internal
`hla-tool-net` network, with a bearer token. Subscription CLIs run with a scrubbed environment and every built-in tool
off.

**Audit.** Every command, file write, deploy, API call, approval and refusal is recorded by the gateway with the agent
and the conversation it came from (Audit page). Secret values never appear in it.

**Optional risk review.** `HLA_RISK_REVIEW=true` scores each request with TypeSafe first and holds risky ones for
approval. It is off by default; levels and approvals apply either way.

## Recommendations

- Start every server at level 1 and raise it only where you need more. Use level 6 only on a sandbox VM you can reset.
- Keep the machine running Homelab Agents, your secret store and your backups **out of the agents' reach** (do not add
  them to the inventory, or keep them at level 0-1).
- Use a separate token per integration with the narrowest permissions it offers (a dedicated Home Assistant user, a
  fine-grained GitHub token for selected repositories, a Proxmox token limited to what you allow).
- Protect your Git `main` branch before enabling pull-request mode.
- Back up `HLA_SECRETS_KEY` and the `gateway_secrets` volume together, somewhere off the machine.
- Treat the backup dumps in `agent-platform-starter/backups/` like the database: they contain every conversation.
- Keep the dashboard on `127.0.0.1` or your LAN unless HTTPS and SSO are in front of it.

## HTTPS and single sign-on

The dashboard has its own password login, which is fine on a trusted LAN. To use it from anywhere, to install it as
a phone app with push notifications (browsers require HTTPS for both) and to record who approved what, put a reverse
proxy with single sign-on in front. Any combination works: Traefik, Caddy or nginx, with Authelia, Authentik or another
forward-auth provider.

1. Keep `HLA_LISTEN_ADDRESS=127.0.0.1` when the proxy runs on the same host (or the host's LAN IP when it runs
   elsewhere) and proxy `https://agents.example.com/` to `http://<that address>:8088/`. Streaming uses
   Server-Sent Events: disable response buffering for the site (nginx: `proxy_buffering off;`).
2. Protect the site with forward auth and pass the user headers through, e.g. Caddy + Authelia:

   ```
   agents.example.com {
       forward_auth 127.0.0.1:9091 {
           uri /api/authz/forward-auth
           copy_headers Remote-User Remote-Groups
       }
       reverse_proxy 127.0.0.1:8088
   }
   ```

3. Tell the router which proxy it may trust for those headers, in `agent-platform-starter/.env`:

   ```
   HLA_TRUSTED_PROXY_IPS=172.18.0.1        # the address the router sees the proxy connect from (exact match)
   HLA_APPROVER_USERS=alice                # optional: only these users may approve
   HLA_APPROVER_GROUPS=admins              # optional
   HLA_REQUIRE_APPROVER_IDENTITY=true      # optional: approvals need a verified user
   PUSH_SUBJECT=https://agents.example.com # contact for Web Push (your URL or mailto:)
   ```

   With the published port, a proxy on the same host usually reaches the router from the Docker bridge gateway
   address rather than 127.0.0.1; the router log shows the client address of each request.

Never expose the gateway, Postgres or Kokoro: they have no published ports, keep it that way.

## Reporting a vulnerability

See [SECURITY.md](../SECURITY.md).
