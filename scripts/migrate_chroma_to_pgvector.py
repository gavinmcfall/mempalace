#!/usr/bin/env python3
"""
migrate_chroma_to_pgvector.py — one-time transfer of a ChromaDB palace into pgvector.

Copies every drawer (id, document, metadata, AND its existing embedding vector)
from a ChromaDB palace into a pgvector-backed mempalace collection. No
re-embedding — the 384-dim vectors are carried across as-is (verified viable by
the GATE 0 spike: the embedder-identity contract only hard-fails on a genuine
model/dimension mismatch, not on foreign-but-matching vectors).

Run with an UPSTREAM mempalace (the pgvector backend lives upstream, not in the
fork). The GATE 0 spike left a ready venv at /tmp/mp-upstream/.venv.

Usage:
    MEMPALACE_BACKEND=pgvector \\
    MEMPALACE_PGVECTOR_DSN='postgresql://mempalace:<pw>@<host>:5432/mempalace' \\
    MEMPALACE_PALACE_PATH=/some/local/anchor/path \\
    /tmp/mp-upstream/.venv/bin/python migrate_chroma_to_pgvector.py <chroma_palace_dir>

Notes:
  - <chroma_palace_dir> is a ChromaDB persistent dir (contains chroma.sqlite3).
  - MEMPALACE_PALACE_PATH is REQUIRED by the pgvector backend (it anchors a local
    mismatch-protection marker); it does not have to equal the chroma dir.
  - Idempotent: re-running upserts by the same drawer ids (safe to resume).
"""
import os
import sys
import time

BATCH = 2000  # rows per page; single-pass get(limit=total) also works but uses more RAM


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    chroma_dir = sys.argv[1]
    dsn = os.environ.get("MEMPALACE_PGVECTOR_DSN") or os.environ.get("MEMPALACE_PGVECTOR_LIVE_URL")
    if not dsn:
        print("ERROR: set MEMPALACE_PGVECTOR_DSN", file=sys.stderr)
        return 2

    import chromadb
    from mempalace.backends import get_backend

    src = chromadb.PersistentClient(path=chroma_dir).get_collection("mempalace_drawers")
    total = src.count()
    print(f"source drawers: {total}")

    dst = get_backend("pgvector").get_collection(
        dsn, collection_name="mempalace_drawers", create=True
    )

    done = 0
    t0 = time.time()
    while done < total:
        batch = src.get(
            include=["documents", "metadatas", "embeddings"],
            limit=BATCH,
            offset=done,
        )
        ids = batch.get("ids") or []
        if not ids:
            break
        dst.upsert(
            documents=batch["documents"],
            ids=ids,
            metadatas=batch["metadatas"],
            embeddings=batch["embeddings"],
        )
        done += len(ids)
        rate = done / max(time.time() - t0, 0.001)
        print(f"  migrated {done}/{total} ({rate:.0f}/s)", flush=True)

    dst_count = dst.count()
    print(f"done. source={total} dst={dst_count}")
    return 0 if dst_count >= total else 1


if __name__ == "__main__":
    raise SystemExit(main())
