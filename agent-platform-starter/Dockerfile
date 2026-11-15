# HLA_ENGINES_V1: the official Claude Code and Codex CLIs (subscription engines, engines.py), installed with npm in a
# Node stage; only node, the two packages and their native binaries are copied into the app image.
FROM node:22.11.0-bookworm-slim AS engines
RUN npm install -g --prefix /opt/engines @anthropic-ai/claude-code@2.1.325 @openai/codex@0.169.0 \
    && npm cache clean --force

FROM python:3.12-slim

WORKDIR /app

COPY --from=engines /usr/local/bin/node /usr/local/bin/node
COPY --from=engines /opt/engines /opt/engines
ENV PATH=/opt/engines/bin:$PATH

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application
COPY app.py .
COPY queue_worker.py .
COPY approvals.py .
COPY runs.py .
COPY sandbox.py .
COPY schedules.py .
COPY projects.py .
COPY push.py .
COPY inbox.py .
COPY audit_view.py .
COPY fact_recheck.py .
COPY costs.py .
COPY voice.py .
COPY chief.py .
COPY models.py .
COPY connections.py .
COPY model_registry.py .
COPY providers.py .
COPY mcp_admin.py .
COPY websites.py .
COPY public_routes.py .
COPY proxmox_admin.py .
COPY omada_admin.py .
COPY attachments.py .
COPY engines.py .
COPY engine_mcp_relay.py .
COPY subscriptions.py .

# Modular components
COPY tool_context/ ./tool_context/
COPY streaming/ ./streaming/
COPY memory/ ./memory/
COPY brain/ ./brain/
COPY threads/ ./threads/
COPY agent_tool/ ./agent_tool/

# Dashboard
COPY ui/ ./ui/

# Optional CA certificates placed next to this Dockerfile (never committed): proxmox-ca.pem (Proxmox node),
# pve2-ca.pem (sandbox host), infisical-ca.pem (self-signed Infisical). Public certificates: readable by the
# unprivileged app user whatever their mode on the host. requirements.txt is listed so the COPY also works when
# there are no *-ca.pem files at all (the features that need one then report it as missing).
COPY --chmod=0644 requirements.txt *-ca.pem /app/

# HLA_ATTACHMENTS_V1: the attachment store (a named volume in compose.yaml). Created here, owned by the app user, so a
# new volume starts writable for it (Docker copies this directory's ownership into an empty named volume).
RUN mkdir -p /data/attachments && chown 65532:65532 /data/attachments
# HLA_ENGINES_V1: the engines' sign-ins (HOME per engine) and per-run directories (the engine_auth volume), app user only.
RUN mkdir -p /data/engines && chown 65532:65532 /data/engines && chmod 700 /data/engines

USER 65532:65532

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
