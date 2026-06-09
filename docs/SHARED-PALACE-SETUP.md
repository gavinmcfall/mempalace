# MemPalace — Shared Palace Setup (Claude Code on WSL)

> **For the agent following this guide:** You are setting up MemPalace on this machine so that this person's Claude Code sessions share the same memory palace hosted at `https://mempalace.nerdz.cloud`. Work through the steps in order. Each step has a verification command — run it and confirm the expected output before moving on. Where a step needs a secret or a human decision, stop and ask.

## What this sets up

- The `mempalace` CLI (pinned to Python 3.10)
- Three Claude Code hooks (session-start, stop, precompact) that auto-save this session's transcripts to the shared palace
- The `mempalace` MCP server so Claude can **search and recall** the shared memory
- A weekly safety-net sync (cron)

When done: every Claude Code session here contributes to — and can search — the same shared palace.

## Prerequisites

- Running inside **WSL (Ubuntu)** with Claude Code installed
- `python3.10` available at `/usr/bin/python3.10` (verify in Step 1)
- `pipx` installed (`python3 -m pip install --user pipx` if missing)
- **The bearer token** for the palace — ask Gavin for `MEMPALACE_TOKEN` (a 64-char hex string). Do not guess it. He has it in his `~/.secrets` / the k8s `mempalace-secret`.
- **Your attribution name** — pick a short lowercase handle (e.g. your first name) so your memories are tagged as yours in the shared palace.

---

## Step 1 — Verify Python 3.10 and install the CLI

MemPalace must run on Python 3.10. Installing under any other version will break silently when the system Python changes.

```bash
/usr/bin/python3.10 --version   # expect: Python 3.10.x
```

If that fails, stop and ask the human to install it: `sudo apt install python3.10 python3.10-venv`.

Install from Gavin's fork (the `develop` branch has the HTTP/remote features):

```bash
pipx install --python /usr/bin/python3.10 "git+https://github.com/gavinmcfall/mempalace.git@develop"
```

**Verify:**
```bash
mempalace --help | head -3
ls -l ~/.local/pipx/venvs/mempalace/bin/python3   # must resolve to python3.10
```
Expected: the help banner prints, and the venv python points at `python3.10` (not bare `python3`). If it points at `python3`, the install used the wrong interpreter — re-run with the explicit `--python /usr/bin/python3.10`.

---

## Step 2 — Configure secrets and sync settings

Add these to `~/.secrets` (create the file if it doesn't exist). **Replace the placeholders.**

```bash
# MemPalace (shared k8s palace)
export MEMPALACE_TOKEN=<paste-the-token-Gavin-gives-you>
export MEMPAL_DIR="$HOME/.claude/projects"
export MEMPAL_REMOTE_URL="https://mempalace.nerdz.cloud"
export MEMPAL_AGENT="<your-name>"      # attributes your drawers as added_by=<your-name>
```

Make sure your shell loads it. If `~/.secrets` isn't already sourced by your `~/.zshrc` / `~/.bashrc`, add:
```bash
[ -f "$HOME/.secrets" ] && source "$HOME/.secrets"
```

**Verify** (open a new shell or `source ~/.secrets` first):
```bash
echo "token set: ${MEMPALACE_TOKEN:+yes}"
echo "remote: $MEMPAL_REMOTE_URL"
echo "agent: $MEMPAL_AGENT"
```
Expected: `token set: yes`, the URL, and your name. If token is blank, fix `~/.secrets` before continuing.

---

## Step 3 — Add the auto-save hooks

These go in `~/.claude/settings.json` under a top-level `"hooks"` key. **Merge** with any existing hooks — do not overwrite the file. The exact blocks (matching Gavin's working setup):

```json
{
  "hooks": {
    "SessionStart": [
      { "matcher": "", "hooks": [
        { "type": "command", "command": "mempalace hook run --hook session-start --harness claude-code", "timeout": 30 }
      ]}
    ],
    "Stop": [
      { "matcher": "", "hooks": [
        { "type": "command", "command": "mempalace hook run --hook stop --harness claude-code", "timeout": 30 }
      ]}
    ],
    "PreCompact": [
      { "matcher": "auto", "hooks": [
        { "type": "command", "command": "mempalace hook run --hook precompact --harness claude-code", "timeout": 60 }
      ]}
    ]
  }
}
```

If `~/.claude/settings.json` already has a `hooks` object, add these three event arrays into it rather than replacing.

**Verify** the JSON is valid and the hook fires:
```bash
jq -e '.hooks.Stop[0].hooks[0].command' ~/.claude/settings.json
echo '{"session_id":"setup-test","stop_hook_active":false}' | mempalace hook run --hook stop --harness claude-code
```
Expected: the jq prints the command string (exit 0), and the hook prints `{}` (no error/traceback). A Python traceback here means Step 1 used the wrong Python — recheck the venv interpreter.

---

## Step 4 — Add the MCP server (search / recall)

This lets Claude query the shared palace. Claude Code accepts the bearer token directly as a header.

```bash
source ~/.secrets
claude mcp add mempalace https://mempalace.nerdz.cloud/mcp \
  --transport http \
  --scope user \
  -H "Authorization: Bearer $MEMPALACE_TOKEN"
```

**Verify:**
```bash
claude mcp list | grep -i mempalace
```
Expected: `mempalace: https://mempalace.nerdz.cloud/mcp (HTTP) - ✔ Connected`.
If it shows `✘ Failed to connect`, the token is wrong or the server is briefly down — confirm the token with Gavin and retry.

---

## Step 5 — Weekly safety-net sync (cron)

The Stop hook syncs after every ~15 exchanges, but short sessions can miss files. A weekly cron catches anything left behind. Create the wrapper script:

```bash
cat > ~/.local/bin/mempalace-sync.sh <<'EOF'
#!/bin/bash
# Weekly safety-net sync of Claude transcripts -> shared k8s mempalace.
set -euo pipefail
source "$HOME/.secrets"
LOCK="$HOME/.mempalace/hook_state/mine.lock"
mkdir -p "$(dirname "$LOCK")"
exec flock -n "$LOCK" \
  mempalace mine "$HOME/.claude/projects" --mode convos \
    --remote-url "$MEMPAL_REMOTE_URL" \
    --remote-token "$MEMPALACE_TOKEN" \
    ${MEMPAL_AGENT:+--agent "$MEMPAL_AGENT"} \
    >> "$HOME/.mempalace/sync-cron.log" 2>&1
EOF
chmod +x ~/.local/bin/mempalace-sync.sh
```

Add the cron entry (Sundays 4am):
```bash
( crontab -l 2>/dev/null; echo "0 4 * * 0 $HOME/.local/bin/mempalace-sync.sh" ) | crontab -
```

**Verify:**
```bash
crontab -l | grep mempalace-sync
service cron status >/dev/null 2>&1 && echo "cron running" || echo "WARNING: start cron with: sudo service cron start"
```
Expected: the cron line prints and cron is running. In WSL, cron is not always started at boot — if it's not running, tell the human to enable it (`sudo service cron start`, and optionally add it to WSL startup).

---

## Step 6 — First backfill (one-time)

This sends this machine's existing Claude transcripts into the shared palace. It is incremental and safe to re-run — a local state file tracks what's already sent, and the server de-duplicates by content hash, so nothing doubles up.

```bash
source ~/.secrets
mempalace mine "$HOME/.claude/projects" --mode convos \
  --remote-url "$MEMPAL_REMOTE_URL" \
  --remote-token "$MEMPALACE_TOKEN" \
  ${MEMPAL_AGENT:+--agent "$MEMPAL_AGENT"}
```

This can take a while if there's a lot of history (it sends one drawer per HTTP call). Let it finish; it prints a summary (`Files processed / skipped / Drawers filed`). If it errors with `403 Forbidden`, the installed build is too old — confirm Step 1 installed from `@develop` (older builds trip Cloudflare's bot check).

---

## Step 7 — Final verification

```bash
# Restart Claude Code first so the new MCP server loads, then in a session the
# mempalace tools should be available. From the shell you can sanity-check the
# endpoint directly:
source ~/.secrets
curl -s -o /dev/null -w "healthz: %{http_code}\n" https://mempalace.nerdz.cloud/healthz
```
Expected: `healthz: 200`.

Inside a **fresh** Claude Code session, the `mcp__mempalace__*` tools (e.g. `mempalace_search`, `mempalace_status`) should be callable. A `mempalace_status` call returns the shared `total_drawers` count — confirming you're on the same palace as Gavin.

---

## Notes & gotchas

- **It's a shared palace.** Everything you save is visible to Gavin and vice-versa. `MEMPAL_AGENT` tags who added each memory, but there's no privacy partition — don't store anything you wouldn't want shared.
- **Same token = shared secret.** Keep `~/.secrets` private (`chmod 600 ~/.secrets`).
- **Python 3.10 pinning is load-bearing.** If `mempalace` ever fails with `ModuleNotFoundError: No module named 'mempalace'`, the system `python3` was upgraded out from under the venv — reinstall with `pipx install --python /usr/bin/python3.10 ...`.
- **Claude Desktop** uses a different setup (the `mcp-remote` bridge in `claude_desktop_config.json`) — see Gavin / the Desktop guide, not this file.
- **claude.ai web** is not supported yet (it requires OAuth, which the palace doesn't expose; bearer tokens aren't accepted by web connectors).
