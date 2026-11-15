# Proxmox CA certificates

The gateway checks the TLS certificate of each Proxmox node against that node's own CA (`gateway/proxmox.py`): one
file per node in `config/inventory.yaml` (`services.proxmox.nodes`), named `<node>-ca.pem`, e.g. `pve-ca.pem` for node
`pve`. The name checked is the node's `tls_name`, else the host name of its URL.

They are the nodes' `/etc/pve/pve-root-ca.pem` (public certificates, no keys). `./dc` copies them here from
`../agent-platform-starter/` when they are missing; `*.pem` files are not committed. The image copies this folder to
`/srv/certs/` (`PROXMOX_CA_DIR`).

Optional: `omada-ca.pem`, the CA of the Omada controller's certificate (`gateway/omada.py`). Without it the gateway pins
the controller's self-signed certificate by its SHA-256, which the owner trusts on the dashboard (Infrastructure → Omada).
