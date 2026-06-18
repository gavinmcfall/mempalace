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

M.main()
