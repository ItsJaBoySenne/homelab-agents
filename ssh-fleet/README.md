# Homelab Agents SSH Fleet Bootstrap

Prepares a Linux VM for `../ssh-gateway`: a locked `svc-hla-agent` account with one Ed25519 key that only
works from the gateway VM's address. After installing, store the key and the host fingerprint in Infisical under
`/servers/<name>` and add the server to `SERVERS` in `ssh-gateway/gateway/core.py`.

## Quick path

`../setup.sh` does most of this for you: for every server in `config/inventory.yaml` it generates a key with
`01-generate-keys.sh`, imports the private key into the gateway's secret store and prints a one-liner per server that
copies `02-install-on-server.sh` there and runs it with the public key inline:

```bash
scp ssh-fleet/02-install-on-server.sh admin@192.168.1.21:/tmp/hla-install.sh && \
  ssh -t admin@192.168.1.21 "sudo bash /tmp/hla-install.sh 'ssh-ed25519 AAAA... hla-gateway-server1' 192.168.1.10 --user svc-hla-agent; rm -f /tmp/hla-install.sh"
```

The rest of this page is the manual route and the background.

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
# or with the key inline and another account name (must match "user" in config/inventory.yaml):
# sudo bash /tmp/02-install-on-server.sh 'ssh-ed25519 AAAA... hla-gateway-server1' 192.168.1.10 --user svc-hla-agent
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

## 4. Import into Infisical

In **Agent Platform**, environment `prod`, create exactly one secrets folder per server, e.g. `/servers/server1`, `/servers/server2`, etc.

Inside each folder create:

- `SSH_HOST`: the server's IP address
- `SSH_USER`: `svc-hla-agent`
- `SSH_PRIVATE_KEY`: the *entire* content of that server's private file (WITHOUT `.pub`) from `~/.ssh/hla-fleet/<name>`, including BEGIN/END OpenSSH lines and embedded newlines
- `SSH_HOST_FINGERPRINT`: ED25519 SHA256 host fingerprint verified from *target console*
- `SSH_KEY_PASSPHRASE`: optional; leave it out for keys from this kit (the gateway tolerates it missing).

To retrieve the private key for a secure, isolated Infisical secret entry (not in chat or shell history):

```bash
cat ~/.ssh/hla-fleet/server1
```

Prefer using Infisical's supported secret import/upload functionality where available rather than copying into a clipboard that syncs across devices. Ensure the Infisical service account can read only necessary folders, particularly not self-host secrets or production admin credentials.

## 5. Add the server to the gateway

Add `'<name>': ('<ip>', '/servers/<name>')` to `SERVERS` in `../ssh-gateway/gateway/core.py`, redeploy the
gateway API, and set the server's level on the dashboard's Policy page (new servers start at level 1).
