# pgvector Migration Implementation Plan (Path B)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan phase-by-phase. Steps use checkbox (`- [ ]`) syntax. **This plan has hard GATES — do not proceed past a gate until its verification passes.** Several later phases are intentionally deferred until the Phase 0 spike resolves unknowns in upstream mempalace's pgvector backend.

**Goal:** Move the shared memory palace from ChromaDB-on-PVC (served via the fork's HTTP transport) to **pgvector on the cluster `postgres18-cluster`**, so every client (workstation, wife, ABMS, MCP) connects directly by DSN. Retire the HTTP transport.

**Architecture:** Pivot the deployed mempalace from the fork to **upstream `develop`** (which has the pgvector backend + RFC 001). Create a dedicated `mempalace` Postgres DB. Transfer all 324k drawers + their existing 384-dim vectors from ChromaDB into pgvector (no re-embed). Repoint clients to `--backend pgvector` with a DSN. Hard cutover; keep the ChromaDB PVC as a cold backup.

**Tech Stack:** mempalace (upstream develop), pgvector 0.8.1 on CloudNative-PG Postgres 18, Python 3.10 (pipx), home-ops GitOps (Flux).

**Decisions locked (from scope):** transfer vectors (not re-embed); new `mempalace` DB; hard cutover keeping ChromaDB backup; LAN-direct (no off-LAN requirement; Tailscale later if needed).

**Key facts:**
- Cluster Postgres primary: `10.90.3.210:5432`, superuser `postgres` (pw in `~/.secrets` `CNPG_PASSWORD`). pgvector `vector` 0.8.1 available, not yet installed.
- Source palace: ChromaDB at k8s `cortex/mempalace` PVC, collection `mempalace_drawers`, **323,931 drawers, dim 384, L2 space**, all `added_by=gavin`.
- Upstream pgvector env: `MEMPALACE_BACKEND=pgvector`, `MEMPALACE_PGVECTOR_LIVE_URL=<dsn>`; CLI `--backend pgvector`.
- Upstream has **no** HTTP transport (fork-only). Path B retires it.

---

## Phase 0 — De-risking spike (GATE: do not provision cluster until this passes)

Prove the three load-bearing assumptions locally against a throwaway Postgres, using **upstream develop** checked out in a scratch worktree. Nothing here touches the cluster or the live palace.

### Task 0.1: Throwaway Postgres + pgvector + upstream mempalace

**Files:** none (scratch environment)

- [ ] **Step 1: Run a local pgvector Postgres**
```bash
docker run -d --name mp-pgtest -e POSTGRES_PASSWORD=test -e POSTGRES_DB=mempalace -p 55432:5432 pgvector/pgvector:pg18
sleep 5
docker exec mp-pgtest psql -U postgres -d mempalace -c "CREATE EXTENSION IF NOT EXISTS vector; SELECT extversion FROM pg_extension WHERE extname='vector';"
```
Expected: prints a `vector` version (e.g. 0.8.x).

- [ ] **Step 2: Check out upstream develop in a scratch venv**
```bash
cd /tmp && rm -rf mp-upstream && git clone --branch develop https://github.com/MemPalace/mempalace.git mp-upstream
cd /tmp/mp-upstream
python3.10 -m venv .venv && .venv/bin/pip install -e ".[dev,pgvector]" 2>&1 | tail -3
```
Expected: install succeeds (note: confirms `[pgvector]` extra exists; if the extra name differs, read `pyproject.toml` `[project.optional-dependencies]` and use the real name).

- [ ] **Step 3: Verify the backend registers and the search CLI exposes `--backend`**
```bash
cd /tmp/mp-upstream
.venv/bin/python -c "from mempalace.backends import available_backends; print(available_backends())"
.venv/bin/python -m mempalace.cli search --help 2>&1 | grep -- --backend
```
Expected: `pgvector` appears in available backends; `--backend` flag present.

**GATE 0.1:** If the `[pgvector]` extra or backend registration doesn't exist as expected, STOP — re-read upstream `pyproject.toml` + `backends/__init__.py` and update the plan's env/extra names before continuing.

### Task 0.2: Prove vector-transfer (the riskiest assumption)

The migration inserts pre-computed 384-dim vectors that pgvector did **not** generate. RFC 001's embedder-identity contract may reject foreign vectors. This task proves whether transfer works or whether we must re-embed.

**Files:** Create `/tmp/mp-upstream/spike_transfer.py`

- [ ] **Step 1: Write a spike that inserts foreign vectors and searches**
```python
# /tmp/mp-upstream/spike_transfer.py
import os
os.environ["MEMPALACE_BACKEND"] = "pgvector"
os.environ["MEMPALACE_PGVECTOR_LIVE_URL"] = "postgresql://postgres:test@localhost:55432/mempalace"
from mempalace.backends import get_backend
be = get_backend("pgvector")
col = be.get_collection(os.environ["MEMPALACE_PGVECTOR_LIVE_URL"], collection_name="mempalace_drawers", create=True)
# Insert 3 drawers with explicit 384-dim vectors (simulating ChromaDB transfer)
import random
def vec(seed):
    random.seed(seed); return [random.random() for _ in range(384)]
col.upsert(
    documents=["alpha doc about kubernetes", "beta doc about postgres", "gamma doc about memory"],
    ids=["d1", "d2", "d3"],
    metadatas=[{"wing":"w","room":"r","added_by":"gavin"}]*3,
    embeddings=[vec(1), vec(2), vec(3)],
)
print("count:", col.count())
res = col.query(query_embeddings=[vec(2)], n_results=2)
print("query ids:", res.get("ids"))
```

- [ ] **Step 2: Run it**
```bash
cd /tmp/mp-upstream && .venv/bin/python spike_transfer.py
```
Expected: `count: 3` and the nearest-neighbour query returns `d2` first.

**GATE 0.2 — the decision point:**
- **If it works:** vector-transfer is viable → proceed with the locked plan (Phase 2 transfers vectors).
- **If the backend rejects foreign vectors / requires a matching embedder sidecar:** inspect `mempalace/backends/_sidecar.py` + `embedding_wrapper.py`. Either (a) write the embedder sidecar identifying all-MiniLM-L6-v2 (384) so the backend trusts the vectors, or (b) fall back to re-embed (change the migration approach — note this reverses a scope decision and should be flagged to Gavin). Update Phase 2 accordingly.

### Task 0.3: Prove stdio MCP serves search from pgvector

**Files:** none

- [ ] **Step 1: Run the stdio MCP against pgvector and call search**
```bash
cd /tmp/mp-upstream
export MEMPALACE_BACKEND=pgvector
export MEMPALACE_PGVECTOR_LIVE_URL="postgresql://postgres:test@localhost:55432/mempalace"
printf '%s\n' \
  '{"jsonrpc":"2.0","method":"initialize","id":0,"params":{}}' \
  '{"jsonrpc":"2.0","method":"tools/call","id":1,"params":{"name":"mempalace_search","arguments":{"query":"postgres"}}}' \
  | .venv/bin/python -m mempalace.mcp_server 2>/dev/null | tail -2
```
Expected: a JSON-RPC result containing the `beta doc about postgres` drawer.

- [ ] **Step 2: Tear down the spike**
```bash
docker rm -f mp-pgtest
```

**GATE 0 (overall):** All three tasks green → assumptions hold, proceed to Phase 1. Record the confirmed env-var names and the transfer verdict in this file before continuing.

---

## Phase 1 — Provision cluster Postgres (GitOps)

**Files:**
- Modify: `~/home-ops/kubernetes/apps/database/postgres18/cluster/...` (CNPG cluster — add `vector` to managed extensions / `initdb`)
- Create: `~/home-ops/kubernetes/apps/cortex/mempalace/app/db-init.sql` or a CNPG `Database`/bootstrap resource for the `mempalace` DB + role
- Create: ExternalSecret for the mempalace DSN (1Password `onepassword-connect`)

- [ ] **Step 1: Add a `mempalace` database + role via CNPG**
Read the existing CNPG cluster manifest first (`kubectl -n database get cluster postgres18-cluster -o yaml | grep -A20 bootstrap`). Add a `Database` CR (CNPG v1.24+) or a managed role + DB. Enable the `vector` extension in that DB.
Example CNPG `Database` resource:
```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Database
metadata:
  name: mempalace
  namespace: database
spec:
  cluster: { name: postgres18-cluster }
  name: mempalace
  owner: mempalace
  extensions:
    - name: vector
      ensure: present
```
(If this CNPG version lacks the `Database`/`extensions` CR, fall back to an `initdb.postInitApplicationSQL` or a one-shot Job running `CREATE DATABASE mempalace; CREATE ROLE mempalace ...; \c mempalace; CREATE EXTENSION vector;`.)

- [ ] **Step 2: Create the role + password as a managed secret**
Use CNPG managed roles (password from a Secret) or a SOPS/ExternalSecret. Store the DSN `postgresql://mempalace:<pw>@postgres18-cluster-rw.database.svc:5432/mempalace`.

- [ ] **Step 3: Commit + PR + merge (home-ops), reconcile Flux**
```bash
cd ~/home-ops && git switch -c feat/mempalace-pgvector-db origin/main
# ... add files ...
git -c commit.gpgsign=false commit -m "feat(mempalace): pgvector database on postgres18-cluster"
git push -u origin feat/mempalace-pgvector-db && gh pr create --base main ...
# after merge:
flux reconcile source git flux-system && flux reconcile kustomization cluster-apps -n flux-system
```

- [ ] **Step 4: Verify the DB + extension exist**
```bash
kubectl -n database exec postgres18-cluster-4 -- psql -U postgres -d mempalace -tAc "SELECT extversion FROM pg_extension WHERE extname='vector';"
```
Expected: prints the vector version (extension installed in the `mempalace` DB).

**GATE 1:** `mempalace` DB reachable with `vector` installed before migrating data.

---

## Phase 2 — Migrate data (transfer vectors, overnight)

**Files:** Create `~/my_other_repos/mempalace/scripts/migrate_chroma_to_pgvector.py`

> Approach confirmed by GATE 0.2. If GATE 0.2 forced re-embed, replace this phase's transfer logic with a re-mine into the pgvector backend instead.

- [ ] **Step 1: Write the migration script**
Reads every drawer from the ChromaDB source (id, document, metadata, embedding) in batches and upserts into the pgvector collection with the same vectors. Run it from a pod that can reach both the ChromaDB PVC (read) and Postgres. Because the PVC is RWO and held by the server, run against a **scaled-down** copy or a `volsync` restore of the chroma PVC into a scratch PVC (do NOT mount the live PVC while the server runs).
```python
# scripts/migrate_chroma_to_pgvector.py
import os, sys, chromadb
from mempalace.backends import get_backend
SRC = sys.argv[1]               # path to chroma palace dir
DSN = os.environ["MEMPALACE_PGVECTOR_LIVE_URL"]
src = chromadb.PersistentClient(path=SRC).get_collection("mempalace_drawers")
dst = get_backend("pgvector").get_collection(DSN, collection_name="mempalace_drawers", create=True)
total = src.count(); print("source drawers:", total)
B = 1000; done = 0
while done < total:
    batch = src.get(include=["documents","metadatas","embeddings"], limit=B, offset=done)
    ids = batch["ids"]
    if not ids: break
    dst.upsert(documents=batch["documents"], ids=ids,
               metadatas=batch["metadatas"], embeddings=batch["embeddings"])
    done += len(ids); print(f"migrated {done}/{total}")
print("dst count:", dst.count())
```
Note: ChromaDB offset pagination is O(n²) — for 324k this is slow but acceptable overnight. If too slow, switch to a single `get(limit=total)` (we proved ~6s for metadata; with embeddings it's larger memory but one pass).

- [ ] **Step 2: Snapshot the source first**
```bash
cd ~/home-ops && task volsync:snapshot NS=cortex APP=mempalace
```

- [ ] **Step 3: Run the migration (overnight) from a scratch pod**
Restore the chroma PVC to a scratch RWO PVC via volsync (so the live server keeps running), mount it + run the script with `MEMPALACE_PGVECTOR_LIVE_URL` pointing at the cluster DB. (Alternative: scale the server to 0 and mount the live PVC read-only — simpler but incurs downtime.)
Expected final line: `dst count: 323931`.

- [ ] **Step 4: Verify parity**
```bash
kubectl -n database exec postgres18-cluster-4 -- psql -U postgres -d mempalace -tAc "SELECT count(*) FROM mempalace_drawers;"  # adjust table name to backend's schema
```
Expected: `323931`. Then run 3 sample searches via `mempalace search --backend pgvector "<query>"` and compare top hits to the ChromaDB palace.

**GATE 2:** Counts match and sample searches return sensible, matching results before repointing any client.

---

## Phase 3 — Repoint clients to pgvector

**Files:**
- Modify: `~/.secrets` (workstation) — add `MEMPALACE_BACKEND=pgvector`, `MEMPALACE_PGVECTOR_LIVE_URL=<dsn>`; remove `MEMPAL_REMOTE_URL`
- Reinstall: workstation pipx mempalace from upstream develop (or fork rebased — see Phase 5)
- Modify: `~/.claude/rules-engine/inject.sh` + `score_results.py` — `mempalace search` → `mempalace search --backend pgvector` (fixes ABMS #1)
- Modify: `~/my_other_repos/mempalace/docs/SHARED-PALACE-SETUP.md` — rewrite wife's guide for pgvector DSN (drop HTTP MCP add; local stdio MCP + `--backend pgvector`)
- Modify: Claude Code MCP registration — replace HTTP `mempalace` server with stdio `mempalace mcp` (backend=pgvector via env)

- [ ] **Step 1: Workstation env + reinstall**, verify `mempalace search --backend pgvector "remote url mine"` returns hits in <1s.
- [ ] **Step 2: Re-register the MCP** as stdio with pgvector env; verify `mcp__mempalace__*` tools work in a fresh session and `mempalace_status` shows 323,931.
- [ ] **Step 3: Fix ABMS** — update inject.sh + score_results.py to pass `--backend pgvector`; trigger an injection and confirm the Memory layer returns current results (ABMS #1 resolved).
- [ ] **Step 4: Rewrite the wife's guide** for the pgvector/DSN model; copy to Windows Downloads.

**GATE 3:** Workstation + ABMS both reading/writing pgvector; status correct.

---

## Phase 4 — Decommission HTTP transport (keep backup)

**Files:**
- Modify: `~/home-ops/kubernetes/apps/cortex/mempalace/...` — remove the HTTP deployment/route/secret (or repurpose as nothing). Keep the ChromaDB PVC (do not delete) as cold backup.
- Modify: workstation hooks/cron — `mine` writes go to pgvector backend (env), drop `--remote-url`.

- [ ] **Step 1: Confirm nothing still depends on `https://mempalace.nerdz.cloud/mcp`** (grep configs; check wife hasn't onboarded to HTTP yet).
- [ ] **Step 2: Remove the HTTP route + deployment via GitOps PR; retain PVC + volsync.**
- [ ] **Step 3: Update hooks** (`_build_mine_cmd` / cron wrapper) to mine into pgvector (no `--remote-url`); verify a hook-triggered mine lands in pgvector attributed `gavin`.

**GATE 4:** All writes/reads flow through pgvector; HTTP server gone; ChromaDB PVC retained, untouched, as rollback.

---

## Phase 5 — (Decision, not yet scheduled) Reconcile fork features onto upstream

Path B deploys **upstream**. Confirm which fork-only features still matter once HTTP is retired:
- `MEMPAL_AGENT` hook attribution — **must keep** (re-apply the ~5-line change onto upstream `hooks_cli.py`, upstream as a PR).
- `mine --remote-url` / RemoteCollection / HTTP transport / auth — **retired** (no longer needed).
- This session's status-perf single-get fix — check if upstream already solved it (it changed metadata fetch in RFC 001); only port if upstream is still slow.

- [ ] Open a PR to upstream (or maintain a thin fork) carrying only `MEMPAL_AGENT`. Verify hooks attribute correctly on the upstream build.

---

## Self-review notes
- **Biggest risk:** GATE 0.2 (foreign-vector transfer vs embedder-identity). The plan explicitly branches there rather than assuming. Do not skip Phase 0.
- **RWO PVC during migration:** Phase 2 reads chroma via a volsync restore to a scratch PVC so the live server isn't disturbed; fallback is scale-to-0.
- **Reversibility:** ChromaDB PVC + volsync snapshots retained through Phase 4; cutover is reversible by repointing clients back to the HTTP server until the PVC is deleted (which this plan never does).
- **Schema/table names** in Phase 2/3 verification are placeholders pending the backend's actual pgvector schema — confirm the real table name during GATE 0 and substitute.
