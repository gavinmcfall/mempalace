# Scope: Migrate the shared palace to pgvector (cluster Postgres)

**Date:** 2026-06-09
**Status:** Scoping — decision required before planning
**Author context:** Gavin; shared household palace; ABMS issue #1 (injection reads stale local palace)

## Goal

Make one shared, network-accessible memory store that **every** client queries directly — the MCP server, ABMS injection, Gavin's workstation, and his wife's machine — instead of a single-pod ChromaDB-on-PVC reachable only through the HTTP MCP server. This fixes ABMS #1 (stale local palace), removes the RWO single-pod bottleneck, and gives true multi-user shared search.

## Why pgvector

Upstream mempalace (RFC 001) added pluggable, network-capable backends: **pgvector** (Postgres) and **qdrant**. A network DB means clients connect directly via DSN — shared search "just works" everywhere, no HTTP search proxy. Gavin already runs `postgres18-cluster` (CNPG, 3 replicas, pgBackRest backups).

## Findings (investigation 2026-06-09)

1. **Cluster Postgres is ready.** `postgres18-cluster` has `vector` (pgvector) **0.8.1 available** (not yet installed) and `pg_trgm` 1.6. A `cortex` database already exists. No vectorchord/vectorscale — plain pgvector, which is what mempalace's backend uses.
2. **Palace embedding model:** dimension **384**, L2 space → all-MiniLM-L6-v2. Any pgvector target must use the same embedder (384-dim) or carry the existing vectors.
3. **THE BIG ONE — fork/upstream divergence:**
   - Fork (`gavinmcfall/mempalace develop`) is **40 commits ahead**, upstream (`MemPalace/mempalace develop`) is **949 commits ahead**. Merge-base `6614b9b`.
   - **Fork-only:** HTTP transport (`transport/http.py`, `auth/`, `backends/remote.py`), `mine --remote-url`, k8s deploy, this session's fixes. **Upstream has NO HTTP transport** (`serve-http` refs: 0) — the fork invented it.
   - **Upstream-only:** pgvector + qdrant backends, RFC 001 (backend-neutral search, embedder-identity contract, embedding sidecar), chromadb 1.5.4, 949 commits of other work.
   - Neither branch alone has both HTTP-serving AND pgvector.
4. **pgvector may make the HTTP transport unnecessary.** The fork built HTTP transport solely to share one ChromaDB-on-PVC. With a shared network DB, every client connects directly (stdio MCP + `--backend pgvector` + DSN). The HTTP transport, RemoteCollection, bearer auth, Cloudflare path, and `mine --remote-url` could all be retired.
   - **Caveat — off-LAN access:** direct Postgres works on the LAN (both machines are on it). Roaming/off-LAN access would need Postgres-over-Tailscale (cluster has Tailscale) or keeping the HTTP transport. **Decision needed.**
5. **Migration data path — two options:**
   - **Re-embed (simple, slow):** re-mine all transcripts into the pgvector backend; embeddings recomputed locally. ~324k drawers → overnight-scale. Clean, uses supported `mine` path, no internal extraction.
   - **Vector transfer (fast, fiddly):** `col.get(include=embeddings)` from ChromaDB (384-dim vectors already exist) → insert into pgvector with the same vectors (the backend accepts an `embeddings` arg). No recompute. Must satisfy RFC 001 embedder-identity (write the embedder sidecar so pgvector trusts the vectors). Faster but touches more internals.

## The core decision

This is **not** "swap a backend flag." The deployed mempalace (fork) doesn't contain the pgvector backend at all. Three strategic paths:

### Path A — Modernize: merge fork onto upstream, then adopt pgvector
Reconcile the 949-commit gap (rebase/merge the 40 fork commits onto upstream develop), keeping HTTP transport AND gaining pgvector. Then migrate data + deploy with `MEMPALACE_BACKEND=pgvector`.
- **Pro:** keeps everything (HTTP off-LAN access + pgvector + modern base).
- **Con:** large, conflict-heavy merge — fork's HTTP/backend changes overlap areas upstream refactored hard (backends, searcher, mcp_server). Highest effort/risk.

### Path B — Pivot to upstream + retire HTTP transport (recommended to evaluate)
Deploy mempalace **from upstream develop** (has pgvector) on pgvector. Drop the HTTP transport entirely; every client uses stdio MCP + `--backend pgvector` + DSN to cluster Postgres. Re-implement only the small must-keeps as needed (MEMPAL_AGENT is already upstreamable; `--remote-url` becomes moot).
- **Pro:** simplest end state; no HTTP/auth/Cloudflare/RemoteCollection to maintain; modern upstream; shared search native.
- **Con:** loses off-LAN access unless Postgres-over-Tailscale; abandons the fork's HTTP work; need to confirm no other fork-only feature is load-bearing.

### Path C — Surgical: cherry-pick pgvector backend onto the fork
Pull just `backends/pgvector.py` + registry + `--backend` plumbing onto the fork's older base.
- **Pro:** smallest change; keeps HTTP transport.
- **Con:** likely doesn't graft — pgvector depends on RFC 001 scaffolding (embedder-identity, embedding_wrapper, registry, chromadb 1.5.4 base) that the fork lacks. High chance of a deep dependency rabbit hole. Least recommended.

## Open questions (need Gavin's input)

1. **Off-LAN access required?** If yes → keep HTTP transport (Path A) or expose Postgres via Tailscale. If LAN-only is fine → Path B is much simpler.
2. **Re-embed vs vector-transfer** for the 324k drawers? (Overnight either way; transfer is faster but fiddlier.)
3. **DB/credentials:** new `mempalace` database on `postgres18-cluster`, or reuse `cortex` DB with a `mempalace` schema? CNPG user + ExternalSecret for the DSN.
4. **Cutover:** dual-write period (chroma + pgvector) for safety, or hard cutover with the chroma palace retained as backup?

## Rough work breakdown (Path B, the simpler one)

1. Enable `vector` extension + create DB/role on `postgres18-cluster` (GitOps; CNPG `initdb`/extension).
2. Store DSN as ExternalSecret; add `MEMPALACE_BACKEND=pgvector` + `MEMPALACE_PGVECTOR_LIVE_URL` to the deployment.
3. Migrate data (overnight): extract 324k drawers from the chroma palace → load into pgvector (re-embed or vector-transfer).
4. Repoint clients: workstation + wife use `--backend pgvector` (DSN via env); ABMS `inject.sh`/`score_results.py` use `mempalace search --backend pgvector`.
5. Verify parity (counts, sample searches) vs the chroma palace; keep chroma PVC as cold backup.
6. Decommission HTTP transport bits once parity confirmed.

## Recommendation

Lead with **Path B** unless off-LAN access is a hard requirement. It collapses the most moving parts (no HTTP/auth/Cloudflare/RemoteCollection), aligns with where upstream is going, and natively solves ABMS #1 + multi-user. Confirm the off-LAN question first — it's the hinge.

**Related:** [[abms-staleness]], [[shared-palace-wife]], [[remote-url-implementation]], [[k8s-memory-limit]]
