FROM python:3.12-slim
WORKDIR /srv
RUN pip install --no-cache-dir 'paramiko>=3.5,<5'
COPY gateway /srv/gateway
RUN mkdir /data && chown 65532:65532 /data
USER 65532:65532
ENTRYPOINT ["python", "-m", "gateway.cli"]
