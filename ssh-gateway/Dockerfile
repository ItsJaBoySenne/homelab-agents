FROM python:3.12-slim
WORKDIR /srv
# cryptography (the local secret store's Fernet) also comes with paramiko; pyyaml reads config/inventory.yaml.
RUN pip install --no-cache-dir 'paramiko>=3.5,<5' 'cryptography>=42' 'pyyaml>=6,<7'
COPY gateway /srv/gateway
# /data/secrets: the local secret store (named volume gateway_secrets); Docker copies this owner and mode into a new volume.
RUN mkdir -p /data/secrets && chown 65532:65532 /data /data/secrets && chmod 700 /data/secrets
USER 65532:65532
ENTRYPOINT ["python", "-m", "gateway.cli"]
