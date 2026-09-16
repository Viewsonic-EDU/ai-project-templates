#!/usr/bin/env bash
# bootstrap.sh — one-time setup on a new machine.
#   git clone <repo> ~/claude-memory && cd ~/claude-memory && ./bootstrap.sh
#
# Does: (1) create per-machine config.local.sh, (2) merge the SessionStart/Stop hooks into
# this machine's ~/.claude/settings.json (non-destructively), (3) run the first sync so all
# symlinks are created and memory is in place.
set -uo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETTINGS="$HOME/.claude/settings.json"

echo "==> bootstrap: $REPO_DIR"

# 1. per-machine config
if [ ! -f "$REPO_DIR/config.local.sh" ]; then
  # CODE_ROOT may be passed on the first run: CODE_ROOT=~/work ./bootstrap.sh
  # config.local.sh is SOURCED by sync-memory.sh, so a user-supplied path is written
  # shell-quoted (printf %q) — never interpolated raw into code. The default stays a
  # literal $HOME reference so it expands on each machine.
  if [ -n "${CODE_ROOT:-}" ]; then
    root_line="CODE_ROOT=$(printf '%q' "$CODE_ROOT")"; shown="$CODE_ROOT"
  else
    root_line='CODE_ROOT="$HOME/Documents/code"'; shown='$HOME/Documents/code'
  fi
  {
    echo '# Per-machine config (gitignored — NOT synced). Edit CODE_ROOT if your repos'
    echo '# live somewhere else. This file is sourced by bin/sync-memory.sh.'
    echo "$root_line"
  } > "$REPO_DIR/config.local.sh"
  echo "    created config.local.sh (CODE_ROOT=$shown)"
else
  echo "    config.local.sh already exists — keeping"
fi

# 2. merge hooks into ~/.claude/settings.json (needs python3; present on macOS)
python3 - "$SETTINGS" "$REPO_DIR" <<'PY'
import json, os, sys, shlex
settings_path, repo_dir = sys.argv[1], sys.argv[2]
# The hook command is executed by a shell: quote the script path, never interpolate it raw.
script = shlex.quote(os.path.join(repo_dir, "bin", "sync-memory.sh"))
start_cmd = f'bash {script} --session-start'
stop_cmd  = f'bash {script} --stop >/dev/null 2>&1 &'
os.makedirs(os.path.dirname(settings_path), exist_ok=True)
if os.path.exists(settings_path):
    try:
        with open(settings_path) as f: s = json.load(f)
    except Exception as e:
        print(f"    ERROR: {settings_path} exists but is not valid JSON ({e}).")
        print("    Refusing to overwrite it - fix the file (or move it away) and re-run bootstrap.")
        sys.exit(1)
    if not isinstance(s, dict):
        print(f"    ERROR: {settings_path} is not a JSON object; refusing to overwrite."); sys.exit(1)
    import shutil, time
    bak = f"{settings_path}.bak.{time.strftime('%Y%m%d%H%M%S')}"
    shutil.copy2(settings_path, bak)
    print(f"    backed up existing settings -> {bak}")
else:
    s = {}
hooks = s.setdefault("hooks", {})

def ensure(event, command):
    arr = hooks.setdefault(event, [])
    for group in arr:
        for h in group.get("hooks", []):
            if "sync-memory.sh" in h.get("command", ""):
                h["command"] = command  # update in place
                return "updated"
    arr.append({"hooks": [{"type": "command", "command": command}]})
    return "added"

r1 = ensure("SessionStart", start_cmd)
r2 = ensure("Stop", stop_cmd)

# statusLine — the script itself is symlinked into ~/.claude by sync-memory.sh
# (link_global), but settings.json is per-machine and NOT synced, so the key has
# to be merged in here. "$HOME" (not an absolute path) keeps it portable across
# machines/usernames — verified that the statusLine command is shell-expanded.
sl_cmd = 'bash "$HOME/.claude/statusline.sh"'
existing = s.get("statusLine")
if isinstance(existing, dict) and existing.get("command") == sl_cmd:
    r3 = "already set"
elif existing:
    # don't silently clobber a hand-rolled statusline on this machine
    s.setdefault("statusLineBackup", existing)
    s["statusLine"] = {"type": "command", "command": sl_cmd}
    r3 = "replaced (previous kept under statusLineBackup)"
else:
    s["statusLine"] = {"type": "command", "command": sl_cmd}
    r3 = "added"

with open(settings_path, "w") as f:
    json.dump(s, f, indent=2); f.write("\n")
print(f"    hooks: SessionStart {r1}, Stop {r2} -> {settings_path}")
print(f"    statusLine: {r3}")
PY
[ $? -eq 0 ] || { echo "==> bootstrap ABORTED: settings.json was not touched and no sync ran." >&2; exit 1; }

# 3. first sync — the sync log is the source of truth; surface FAIL/CONFLICT lines here.
echo "==> running first sync..."
if bash "$REPO_DIR/bin/sync-memory.sh" | tee "$REPO_DIR/.bootstrap-sync.out" && ! grep -qE '^(FAIL|CONFLICT)' "$REPO_DIR/.bootstrap-sync.out"; then
  rm -f "$REPO_DIR/.bootstrap-sync.out"
  echo "==> done. Memory is linked and this machine is set up."
else
  echo "==> first sync reported problems (see lines above and $REPO_DIR/sync.log)." >&2
  rm -f "$REPO_DIR/.bootstrap-sync.out"; exit 1
fi
