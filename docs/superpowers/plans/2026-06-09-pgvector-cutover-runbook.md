# pgvector Cutover Runbook — run tonight when Claude Code is idle

Execute top-to-bottom once Gavin confirms Claude Code conversations are idle (so brief
mempalace downtime is harmless). Prereq: the `mempalace` Postgres DB + role + `vector`
extension already provisioned (Phase 1) and the DSN known.

Variables (fill in):
- `DSN = postgresql://mempalace:<pw>@postgres18-cluster-rw.database.svc:5432/mempalace`
  (off-cluster from the workstation use the LAN IP: `postgresql://mempalace:<pw>@10.90.3.210:5432/mempalace`)
- `UP=/tmp/mp-upstream/.venv/bin/python` (upstream mempalace venv from the GATE 0 spike)
- `ANCHOR=$HOME/.mempalace/palace` (local path for MEMPALACE_PALACE_PATH marker)

## 1. Quiesce writes (idle confirmed)
- Confirm no active Claude sessions are mining: `ps aux | grep "mempalace mine" | grep -v grep` → empty.

## 2. Snapshot the source (safety net)
```bash
cd ~/home-ops && task volsync:snapshot NS=cortex APP=mempalace
```

## 3. Migrate ChromaDB → pgvector (the overnight copy)
Scale the HTTP server to 0 so the migration can read the RWO PVC directly (downtime OK — Claude idle):
```bash
flux suspend hr mempalace -n cortex
kubectl -n cortex scale deploy/mempalace --replicas=0
kubectl -n cortex wait --for=delete pod -l app.kubernetes.io/name=mempalace --timeout=90s
```
Run the migration in a pod that mounts the PVC and reaches Postgres. Use a python:3.12-slim
pod, pip-install upstream mempalace + pgvector, then run `scripts/migrate_chroma_to_pgvector.py`
(copy the script into the pod, or bake a tiny image). Reads `/data/palace`, writes `DSN`.
Expected final line: `done. source=323931 dst=323931` (or higher if drawers were added since).
- Set the embedder identity once after load: `mempalace palace set-embedder --model minilm`
  (run via the same upstream venv/pod against `ANCHOR`/DSN) to satisfy RFC 001 and silence the warning.

## 4. Verify parity
```bash
# table name = mempalace_<sha256(palace_path)[:16]>_mempalace_drawers
kubectl -n database exec <primary> -- psql -U postgres -d mempalace -tAc "\dt mempalace_*"
kubectl -n database exec <primary> -- psql -U postgres -d mempalace -tAc "SELECT count(*) FROM <table>;"   # expect 323931
```
Run 3 sample searches via `MEMPALACE_BACKEND=pgvector MEMPALACE_PGVECTOR_DSN=$DSN MEMPALACE_PALACE_PATH=$ANCHOR $UP -m mempalace.cli search "remote url mine"` and eyeball vs the old palace.

## 5. Repoint the workstation
- Reinstall pipx mempalace from upstream develop:
  `pipx uninstall mempalace && pipx install --python /usr/bin/python3.10 "git+https://github.com/MemPalace/mempalace.git@develop"`
  - Re-apply the MEMPAL_AGENT change onto upstream first (it's fork-only) OR install from a fork branch rebased on upstream that carries it. (See Phase 5.)
- Edit `~/.secrets`:
  - add `export MEMPALACE_BACKEND=pgvector`
  - add `export MEMPALACE_PGVECTOR_DSN="$DSN"`
  - add `export MEMPALACE_PALACE_PATH="$ANCHOR"`
  - remove `export MEMPAL_REMOTE_URL=...` (no longer used)
  - keep `MEMPAL_AGENT=gavin`
- `source ~/.secrets`
- Verify: `mempalace search --backend pgvector "kubernetes memory limit"` returns hits < 1s.

## 6. Re-register the MCP (stdio + pgvector)
```bash
claude mcp remove mempalace
claude mcp add mempalace -- /usr/bin/env MEMPALACE_BACKEND=pgvector \
  MEMPALACE_PGVECTOR_DSN="$DSN" MEMPALACE_PALACE_PATH="$ANCHOR" \
  python -m mempalace.mcp_server
```
(Adjust to the pipx mempalace entrypoint; the wrapper just needs the three env vars set.)
Restart Claude Code; confirm `mcp__mempalace__mempalace_status` returns 323931.

## 7. Fix ABMS (resolves issue #1)
- Edit `~/.claude/rules-engine/inject.sh` and `score_results.py`: change `mempalace search ...`
  → `mempalace search --backend pgvector ...` (env already exports the DSN/anchor).
- Trigger an injection; confirm the Memory layer returns current results.

## 8. Update the hooks/cron to write pgvector
- `_build_mine_cmd` / `mempalace-sync.sh`: drop `--remote-url`; with `MEMPALACE_BACKEND=pgvector`
  in the environment, `mine` writes to pgvector directly. Verify a hook-triggered mine lands in pgvector.

## 9. Decommission HTTP transport (after parity confirmed)
- home-ops PR: remove the mempalace HTTP deployment/route/secret. **Keep the ChromaDB PVC + volsync** as cold backup (do NOT delete).
- Leave the cluster Postgres + pgvector as the sole store.

## Rollback (any step before 9)
- Re-point `~/.secrets` back to `MEMPAL_REMOTE_URL`, re-add the HTTP MCP, `flux resume hr mempalace -n cortex` + scale to 1. The ChromaDB palace is untouched.

## Wife / Claude Desktop (after cutover)
- Rewrite `docs/SHARED-PALACE-SETUP.md` for pgvector: local stdio MCP + the three env vars + LAN DSN. No HTTP endpoint, no bearer token.
