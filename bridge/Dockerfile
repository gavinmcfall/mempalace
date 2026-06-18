# syntax=docker/dockerfile:1.7
# MemPalace LAN bridge: upstream mempalace (pgvector) behind mcp-proxy
# (stdio -> streamablehttp). Pinned to a specific upstream commit so upgrades
# are deliberate (bump MEMPALACE_REF, rebuild, re-validate, deploy).
FROM python:3.12-slim-bookworm

# Pin: MemPalace develop @ this commit (validated 2026-06-19, reports 3.4.1).
ARG MEMPALACE_REF=afa749c141e60cf9c797c602f0d667e722fa0a75

RUN pip install --no-cache-dir \
      "mempalace[pgvector] @ https://github.com/MemPalace/mempalace/archive/${MEMPALACE_REF}.tar.gz" \
      mcp-proxy

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
