# Proxmox CA certificates

The gateway checks the TLS certificate of each Proxmox node against that node's own CA (`gateway/proxmox.py`):

| File | Node | Checked name |
|---|---|---|
| `proxmox-ca.pem` | node 1 `proxmox` (192.168.20.30) | `proxmox.home.arpa` |
| `pve2-ca.pem` | node 2 `pve2` (192.168.20.31) | `pve2.example.com` |

They are the nodes' `/etc/pve/pve-root-ca.pem` (public certificates, no keys). `./dc` copies them here from
`../agent-platform-starter/` when they are missing; `*.pem` files are not committed. The image copies this folder to
`/srv/certs/` (`PROXMOX_CA_DIR`).

Optional: `omada-ca.pem`, the CA of the Omada controller's certificate (`gateway/omada.py`). Without it the gateway pins
the controller's self-signed certificate by its SHA-256, which the owner trusts on the dashboard (Infrastructure → Omada).
