# syntax=docker/dockerfile:1.7
# MemPalace LAN bridge: mempalace's NATIVE HTTP MCP transport (one process) behind
# caddy (Host-rewriting reverse proxy). Replaces the old supergateway stdio->http
# bridge, whose STATEFUL transport spawned one serve.py child per MCP session and
# never reaped orphans — ~90 leaked processes (~5.8Gi) OOMKilled the pod daily.
# serve.py now runs `mempalace.mcp_server --transport http` on loopback; caddy on
# :8080 rewrites Host to the loopback literal so the DNS-rebinding Host-pin accepts
# proxied requests (keeps clients unchanged — no bearer token needed).
FROM python:3.12-slim-bookworm

# Pin: MemPalace develop @ this commit (2026-06-28, post-v3.5.0). Bumped from
# afa749c1 (3.4.1) for the large-palace fixes that unblock mining on the 419k
# shared palace: SQL-pushdown pagination for pgvector get() (#1840) and skip the
# document column on metadata-only fetches (#1892) — together they kill the
# full-table O(n×doc) fetch that stalled every mine — plus surrogate/NUL
# mine-abort fixes (#1833/#1829) and an ONNX intra-op thread cap (#1068).
ARG MEMPALACE_REF=8ec284db5dc57afd9fde1402a3912e71382dac6e
ARG CADDY_VERSION=2.8.4

RUN pip install --no-cache-dir \
      "mempalace[pgvector] @ https://github.com/MemPalace/mempalace/archive/${MEMPALACE_REF}.tar.gz"

# caddy — Host-rewriting reverse proxy in front of the loopback-bound native MCP
# HTTP server. One well-behaved proxy process (no per-session spawning), so it
# does not reintroduce the supergateway leak. curl is only needed to fetch it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSL "https://github.com/caddyserver/caddy/releases/download/v${CADDY_VERSION}/caddy_${CADDY_VERSION}_linux_amd64.tar.gz" \
       | tar -xz -C /usr/local/bin caddy \
    && chmod +x /usr/local/bin/caddy \
    && /usr/local/bin/caddy version \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY bridge/serve.py /app/serve.py
COPY bridge/Caddyfile /app/Caddyfile
COPY bridge/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh \
    && caddy validate --config /app/Caddyfile --adapter caddyfile

ENV MEMPALACE_BACKEND=pgvector \
    MEMPALACE_PALACE_PATH=/data/palace \
    MEMPALACE_EMBEDDING_MODEL=minilm \
    HOME=/data \
    PYTHONUNBUFFERED=1

# Rootless per home-operations standard; k8s sets fsGroup so /data (PVC) is writable.
USER 65534:65534

EXPOSE 8080
ENTRYPOINT ["/app/entrypoint.sh"]
