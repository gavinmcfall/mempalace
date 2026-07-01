#!/bin/sh
# MemPalace bridge entrypoint: ensure palace markers exist (idempotent, guarded),
# then run supergateway in front of the patched stdio MCP server.
set -e

: "${MEMPALACE_BACKEND:=pgvector}"
: "${MEMPALACE_PALACE_PATH:=/data/palace}"
: "${MEMPALACE_EMBEDDING_MODEL:=minilm}"
export MEMPALACE_BACKEND MEMPALACE_PALACE_PATH MEMPALACE_EMBEDDING_MODEL

# Build the DSN from parts if not supplied explicitly (keeps the password out of
# the manifest env — PGUSER/PGPASSWORD come from a Secret). statement_timeout is
# a safety net: any single SQL that runs >120s aborts (loudly) instead of hanging
# a mine forever and holding the serialize-lock (see the seq-scan incident).
if [ -z "${MEMPALACE_PGVECTOR_DSN}" ]; then
  export MEMPALACE_PGVECTOR_DSN="postgresql://${PGUSER}:${PGPASSWORD}@${PGHOST:-postgres18-cluster-rw.database.svc.cluster.local}:${PGPORT:-5432}/${PGDATABASE:-mempalace}?options=-c%20statement_timeout%3D120000"
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

# Ensure a GIN index on metadata. The miner's per-file dedup/purge filters with
# `metadata @> {...}`; without a GIN index that is a full seq scan of the whole
# palace, so every mine hangs once the palace grows large (the drawers never land
# and the stuck mine holds the serialize-lock). Idempotent + runs every boot so
# the index is guaranteed regardless of when the table was created.
python - <<'PY' || echo "[entrypoint] gin index ensure skipped (continuing)"
import os, psycopg
from mempalace.palace import get_collection
pp = os.environ["MEMPALACE_PALACE_PATH"]
c = get_collection(pp, "mempalace_drawers", create=True)
inner = c._inner
table = inner._backend._table_name(
    palace=inner._palace, collection_name="mempalace_drawers", config=inner._config)
with psycopg.connect(os.environ["MEMPALACE_PGVECTOR_DSN"], autocommit=True) as conn:
    conn.execute(
        'CREATE INDEX IF NOT EXISTS "idx_%s_meta_gin" ON "%s" '
        'USING gin (metadata jsonb_path_ops)' % (table, table))
print("[entrypoint] gin index ensured on", table)
PY

# Run the NATIVE MCP HTTP transport (one ThreadingHTTPServer process, no
# per-session spawning) on loopback, with caddy in front on :8080. This replaces
# supergateway, whose stateful stdio->http bridge spawned one serve.py per MCP
# session and never reaped orphans — ~90 leaked processes (~5.8Gi) OOMKilled the
# pod daily. serve.py imports mcp_server as M and calls M.main(), which parses
# these args and serves HTTP with the custom ingest tool + status patch registered.
# The native server pins Host to loopback (DNS-rebinding guard); caddy rewrites
# Host to 127.0.0.1:8765 so proxied requests are accepted — clients stay unchanged.
python /app/serve.py --transport http --host 127.0.0.1 --port 8765 &
SERVE_PID=$!
caddy run --config /app/Caddyfile --adapter caddyfile &
CADDY_PID=$!

# POSIX supervisor (dash has no `wait -n`): if either process exits, tear the
# other down and exit non-zero so Kubernetes restarts the container.
while kill -0 "$SERVE_PID" 2>/dev/null && kill -0 "$CADDY_PID" 2>/dev/null; do
  sleep 5
done
echo "[entrypoint] serve.py ($SERVE_PID) or caddy ($CADDY_PID) exited; shutting down"
kill "$SERVE_PID" "$CADDY_PID" 2>/dev/null
exit 1
