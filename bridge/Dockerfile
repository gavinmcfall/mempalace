# syntax=docker/dockerfile:1.7
# MemPalace LAN bridge: upstream mempalace (pgvector) behind supergateway
# (stdio -> streamablehttp). supergateway is used instead of mcp-proxy because
# mcp-proxy 0.12.0's streamable-http is incompatible with Claude Code's MCP
# client (handshake OK, tools/list fails); supergateway works with it.
# Pinned upstream + supergateway versions so upgrades are deliberate.
FROM python:3.12-slim-bookworm

# Pin: MemPalace develop @ this commit (2026-06-28, post-v3.5.0). Bumped from
# afa749c1 (3.4.1) for the large-palace fixes that unblock mining on the 419k
# shared palace: SQL-pushdown pagination for pgvector get() (#1840) and skip the
# document column on metadata-only fetches (#1892) — together they kill the
# full-table O(n×doc) fetch that stalled every mine — plus surrogate/NUL
# mine-abort fixes (#1833/#1829) and an ONNX intra-op thread cap (#1068).
ARG MEMPALACE_REF=8ec284db5dc57afd9fde1402a3912e71382dac6e
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

# Raise supergateway's HTTP body limit. It calls express.json() with no options,
# so Express defaults to a 100kb body limit — full Claude transcript ingests
# (mempalace_ingest_transcript) are MBs and were silently rejected with
# "PayloadTooLargeError: request entity too large", so nothing got saved.
# supergateway 3.4.3 exposes no flag for this, so patch the pinned dist. The grep
# guard fails the build if a version bump changes the express.json() call shape.
RUN SG=/usr/lib/node_modules/supergateway/dist/gateways \
    && sed -i "s/express\.json()/express.json({ limit: '50mb' })/g" \
         "$SG/stdioToStatefulStreamableHttp.js" \
         "$SG/stdioToStatelessStreamableHttp.js" \
    && grep -q "limit: '50mb'" "$SG/stdioToStatefulStreamableHttp.js"

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
