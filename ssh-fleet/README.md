# Homelab Agents SSH Fleet Bootstrap

Prepares a Linux VM for `../ssh-gateway`: a locked `svc-hla-agent` account with one Ed25519 key that only
works from the gateway VM's address. After installing, add the server to `config/inventory.yaml` and store the key and the host fingerprint in the
gateway's secret store (`servers/<name>/...`, section 4).

## Safety before deployment

- Snapshot/backup each VM and validate restores; do one VM at a time.
- The `--admin` option is equivalent to granting root to anyone holding that VM's private key. Use on **disposable dev VM first**. For Traefik, DB, file server, Infisical, and agent VM, provision non-admin access initially. Consider keeping Infisical and the agent host **outside autonomous agent control** entirely.
- Keep target VM's existing admin login working. Restrict SSH network access on the firewall to the gateway VM's IP when compatible with your normal admin access and monitoring.
- Key material must not be sent to AI models, logs, Git, chat, shared files, or the remote VMs. Infisical should have a separate narrow read-only machine identity for gateway retrieval; don't put Infisical bootstrap credentials in prompts.
- Public keys are constrained to the gateway VM's source IP using authorized_keys `from=`, and forwarding is disabled. This constrains *key use*, not privileged root actions. Verify that source IP reflects the IP SSH servers actually see (NAT can change it).
- Each target receives a distinct Ed25519 key. Generated keys have **no passphrase for noninteractive operation**. Infisical protects stored keys. If gateway identity is compromised, attackers may still retrieve every key it can read; scope identities tightly.

The examples below use `192.168.1.10` for the VM that runs the agent platform and SSH gateway, and `server1`
(`192.168.1.21`) for a server you want the agents to reach. Replace them with your own names and addresses.

## 1. On the agents VM, generate keys

From `homelab-agents/ssh-fleet`:

```bash
bash 01-generate-keys.sh server1 server2     # one key per server name
```

Files are created in `~/.ssh/hla-fleet/<name>` (private) and `<name>.pub` (public), permissions 600/644. **Never overwrite existing keys** without coordinated rotation.

## 2. Install public key on EACH target VM

For example from the agents VM (using your existing admin SSH account, here `admin`):

```bash
scp ~/.ssh/hla-fleet/server1.pub admin@192.168.1.21:/tmp/hla-agent.pub
scp 02-install-on-server.sh admin@192.168.1.21:/tmp/02-install-on-server.sh
```

SSH in using your existing admin account and run (the second argument is the agents VM's address):

```bash
sudo bash /tmp/02-install-on-server.sh /tmp/hla-agent.pub 192.168.1.10
# ONLY on disposable dev VM AFTER backup and explicit risk review:
# sudo bash /tmp/02-install-on-server.sh /tmp/hla-agent.pub 192.168.1.10 --admin
sudo rm -f /tmp/hla-agent.pub /tmp/02-install-on-server.sh
```

The script creates/uses `svc-hla-agent`, locks password if newly created, installs a source-IP-constrained public key, and prints the target's ED25519 SSH HOST key fingerprint. `--admin` grants passwordless root; without it no extra sudo policy is added. An account that *already exists* may already have sudo privileges from an earlier setup; inspect `/etc/sudoers.d/90-hla-agent` and `sudo -l -U svc-hla-agent`.

Repeat with the correct key and address for every server. Suggested starting roles:

| Kind of server | Initial recommended role |
| --- | --- |
| media / app servers | manual approval for changes |
| reverse proxy, network controller, database, file server | no sudo initially |
| disposable dev / test VM | `--admin` only here |
| the agents VM itself | **prefer not managed by its own agents** |
| your secret store (Infisical) | **prefer not managed by agents** |

If your admin account has a different name, substitute it. For servers not reachable over SSH, copy installer/public key using a Proxmox console or your existing access route. Don't change SSH daemon config automatically.

## 3. Verify SSH host key and test connection

On the target's trusted Proxmox console:

```bash
sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

On the agents VM, verify this matches the presented SSH host fingerprint before accepting the host key on your first connection; then test:

```bash
ssh -i ~/.ssh/hla-fleet/server1 -o IdentitiesOnly=yes -o BatchMode=yes svc-hla-agent@192.168.1.21 'whoami; hostname; id'
```

You can later run `bash 03-verify-from-agents.sh hosts.txt` (one `<name> <address>` per line, e.g. `server1 192.168.1.21`)
once each target SSH host key is pinned in the local known_hosts. `03-verify...` does not disable host-key verification.

## 4. Store the key in the gateway's secret store

Add the server to `config/inventory.yaml` first (step 5), then, on the gateway host (the key is piped, never on the
command line, and the CLI never prints it):

```bash
cd ssh-gateway
sudo ./dc exec -T ssh-gateway-api python -m gateway.cli secrets import-ssh-key server1 /dev/stdin < ~/.ssh/hla-fleet/server1
# pin the host key: either the fingerprint you verified on the target console ...
sudo ./dc exec -T ssh-gateway-api python -m gateway.cli secrets set servers/server1/SSH_HOST_FINGERPRINT <<< 'SHA256:...'
# ... or record it on first contact and compare what it prints with the console
sudo ./dc exec -T ssh-gateway-api python -m gateway.cli secrets pin-host-key server1
```

(`host_fingerprint: SHA256:...` on the server in the inventory works too.) Keys from this kit have no passphrase; a
passphrase-protected key also needs `servers/<name>/SSH_KEY_PASSPHRASE`.

With `HLA_SECRETS_BACKEND=infisical` the same names are Infisical folders: `/servers/<name>` with `SSH_PRIVATE_KEY`
(the whole private file including BEGIN/END lines), `SSH_HOST_FINGERPRINT` and optionally `SSH_KEY_PASSPHRASE`; give the
gateway's machine identity read access to only those folders.

## 5. Add the server to the gateway

Add `- {name: <name>, host: <ip>, description: "..."}` under `servers:` in `config/inventory.yaml` (see
`config/inventory.example.yaml`; `user`/`port` if they differ from svc-hla-agent/22), restart the gateway API and the
agent platform, and set the server's level on the dashboard's Policy page (new servers start at level 1).
