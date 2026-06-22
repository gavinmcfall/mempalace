#!/bin/sh
# MemPalace bridge entrypoint: ensure palace markers exist (idempotent, guarded),
# then run supergateway in front of the patched stdio MCP server.
set -e

: "${MEMPALACE_BACKEND:=pgvector}"
: "${MEMPALACE_PALACE_PATH:=/data/palace}"
: "${MEMPALACE_EMBEDDING_MODEL:=minilm}"
export MEMPALACE_BACKEND MEMPALACE_PALACE_PATH MEMPALACE_EMBEDDING_MODEL

# Build the DSN from parts if not supplied explicitly (keeps the password out of
# the manifest env — PGUSER/PGPASSWORD come from a Secret).
if [ -z "${MEMPALACE_PGVECTOR_DSN}" ]; then
  export MEMPALACE_PGVECTOR_DSN="postgresql://${PGUSER}:${PGPASSWORD}@${PGHOST:-postgres18-cluster-rw.database.svc.cluster.local}:${PGPORT:-5432}/${PGDATABASE:-mempalace}"
fi

mkdir -p "${MEMPALACE_PALACE_PATH}"

# One-time palace init: embedder sidecar + pgvector backend marker. Guarded by
# the marker's existence so that on a persistent volume it runs exactly once;
# a later upstream change to the marker API can never break an already-init'd PVC.
if [ ! -f "${MEMPALACE_PALACE_PATH}/pgvector_backend.json" ]; then
  echo "[entrypoint] initializing palace markers in ${MEMPALACE_PALACE_PATH}"
  mempalace palace set-embedder --model "${MEMPALACE_EMBEDDING_MODEL}" || \
    echo "[entrypoint] set-embedder failed (continuing)"
  python - <<'PY' || echo "[entrypoint] marker write failed (will retry next boot)"
import os
from mempalace.palace import get_collection
c = get_collection(os.environ["MEMPALACE_PALACE_PATH"], "mempalace_drawers", create=True)
i = c._inner
i._backend._write_marker(i._palace, i._config)
print("[entrypoint] marker written")
PY
fi

# supergateway wraps the patched stdio MCP server and exposes it over
# streamable-http at /mcp (Claude Code compatible). It inherits this env (incl.
# MEMPALACE_PGVECTOR_DSN) and passes it to the child stdio process.
exec supergateway \
  --stdio "python /app/serve.py" \
  --outputTransport streamableHttp \
  --streamableHttpPath /mcp \
  --healthEndpoint /healthz \
  --port "${BRIDGE_PORT:-8080}"
