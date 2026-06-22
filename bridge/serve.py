"""MemPalace bridge entrypoint.

Imports the upstream MCP server, applies a fail-safe performance patch to
``mempalace_status`` (aggregate wing/room counts via SQL ``GROUP BY`` on the
pgvector backend — ~344s/OOM -> ~0.4s at 418k drawers), then runs the stdio
JSON-RPC loop via ``M.main()``.

DURABILITY: the patch is wrapped so that ANY failure (upstream renamed an
internal, signature changed, etc.) logs a warning and falls through to running
the server UNPATCHED. The bridge always starts; worst case status is slow again,
never a crash loop. Run as ``python serve.py`` (NOT ``-m mempalace.mcp_server``:
the -m re-execution would redefine and clobber the patched objects)."""
import sys

import mempalace.mcp_server as M

try:
    from mempalace.backends.pgvector import PgVectorCollection, _quote_identifier

    def _group_counts(inner, field):
        sql = (
            f"SELECT metadata->>%s AS k, count(*) "
            f"FROM {_quote_identifier(inner._table)} GROUP BY 1"
        )
        rows = inner._client._execute(sql, [field], fetch=True)
        return {(r[0] if r[0] is not None else "unknown"): int(r[1]) for r in rows}

    _orig_status = M.tool_status

    def tool_status_fast():
        try:
            col = M._get_collection(create=M._backend_db_exists())
            if not col:
                return M._collection_error_or_no_palace()
            inner = getattr(col, "_inner", col)
            if not isinstance(inner, PgVectorCollection):
                return _orig_status()
            return {
                "total_drawers": col.count(),
                "wings": _group_counts(inner, "wing"),
                "rooms": _group_counts(inner, "room"),
                "protocol": M.PALACE_PROTOCOL,
                "aaak_dialect": M.AAAK_SPEC,
                "backend": M._selected_backend_name(),
            }
        except Exception as exc:  # runtime fallback: degrade to upstream status
            print(f"[serve.py] fast status failed at runtime ({exc!r}); "
                  f"falling back to upstream status", file=sys.stderr)
            return _orig_status()

    M.tool_status = tool_status_fast
    M.TOOLS["mempalace_status"]["handler"] = tool_status_fast
    print("[serve.py] status SQL-GROUP-BY patch applied", file=sys.stderr)
except Exception as exc:  # import/patch-time fallback: run unpatched
    print(f"[serve.py] status patch NOT applied ({exc!r}); running upstream "
          f"unpatched (status will be slow but functional)", file=sys.stderr)


# --- Auto-save ingest tool (B-ii) -------------------------------------------
# Remote clients can't write the shared palace directly (mempalace abspaths the
# local palace path, so a client's palace.id never matches the bridge's
# /data/palace -> a different table). So expose an ingest tool: clients POST a
# conversation transcript and the bridge mines it server-side, with the correct
# palace identity. The mine runs DETACHED so it never blocks the stdio MCP loop
# (reads stay responsive); the full pipeline (chunk/entity/AAAK/dedup) is reused.
try:
    import os as _os

    def tool_ingest_transcript(content, filename="session.jsonl", agent=None):
        import tempfile
        import subprocess

        try:
            palace_path = _os.environ.get("MEMPALACE_PALACE_PATH", "/data/palace")
            ag = (agent or _os.environ.get("MEMPAL_AGENT") or "mempalace")
            d = tempfile.mkdtemp(prefix="mp_ingest_")
            base = _os.path.basename(filename or "session.jsonl")
            if not base.endswith(".jsonl"):
                base += ".jsonl"
            fp = _os.path.join(d, base)
            with open(fp, "w", encoding="utf-8") as f:
                f.write(content or "")
            # repr() makes all interpolations safe Python string literals.
            code = (
                "from mempalace.convo_miner import mine_convos;"
                f"mine_convos({d!r}, palace_path={palace_path!r}, agent={ag!r}, "
                "extract_mode='exchange');"
                f"import shutil; shutil.rmtree({d!r}, ignore_errors=True)"
            )
            log = open("/data/ingest.log", "a")
            subprocess.Popen(
                [sys.executable, "-c", code],
                stdout=log, stderr=log, env=_os.environ, start_new_session=True,
            )
            return {"success": True, "queued": True,
                    "bytes": len(content or ""), "agent": ag}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc)}

    M.TOOLS["mempalace_ingest_transcript"] = {
        "description": (
            "Ingest a conversation transcript (Claude .jsonl content) into the "
            "shared palace. Mined server-side (chunk/entity/AAAK/dedup) with the "
            "correct palace identity; runs in the background. Use for auto-save "
            "from remote clients that cannot write the shared backend directly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string",
                            "description": "Raw transcript file content (.jsonl)"},
                "filename": {"type": "string",
                             "description": "Original filename (optional)"},
                "agent": {"type": "string",
                          "description": "Attribution agent name, e.g. gavin"},
            },
            "required": ["content"],
        },
        "handler": tool_ingest_transcript,
    }
    print("[serve.py] ingest tool registered", file=sys.stderr)
except Exception as exc:  # never let the ingest tool break startup
    print(f"[serve.py] ingest tool NOT registered ({exc!r})", file=sys.stderr)

M.main()
