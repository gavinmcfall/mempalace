# syntax=docker/dockerfile:1.7
# MemPalace LAN bridge: upstream mempalace (pgvector) behind supergateway
# (stdio -> streamablehttp). supergateway is used instead of mcp-proxy because
# mcp-proxy 0.12.0's streamable-http is incompatible with Claude Code's MCP
# client (handshake OK, tools/list fails); supergateway works with it.
# Pinned upstream + supergateway versions so upgrades are deliberate.
FROM python:3.12-slim-bookworm

# Pin: MemPalace develop @ this commit (validated 2026-06-19, reports 3.4.1).
ARG MEMPALACE_REF=afa749c141e60cf9c797c602f0d667e722fa0a75
ARG SUPERGATEWAY_VERSION=3.4.3

RUN pip install --no-cache-dir \
      "mempalace[pgvector] @ https://github.com/MemPalace/mempalace/archive/${MEMPALACE_REF}.tar.gz"

# Node + supergateway (stdio->streamableHttp bridge)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g "supergateway@${SUPERGATEWAY_VERSION}" \
    && apt-get purge -y curl gnupg && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY bridge/serve.py /app/serve.py
COPY bridge/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENV MEMPALACE_BACKEND=pgvector \
    MEMPALACE_PALACE_PATH=/data/palace \
    MEMPALACE_EMBEDDING_MODEL=minilm \
    HOME=/data \
    PYTHONUNBUFFERED=1

# Rootless per home-operations standard; k8s sets fsGroup so /data (PVC) is writable.
USER 65534:65534

EXPOSE 8080
ENTRYPOINT ["/app/entrypoint.sh"]
