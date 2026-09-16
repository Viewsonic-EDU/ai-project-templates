#!/usr/bin/env bash
# Records the current Claude Code session's identity per process so
# scripts/context-ledger.sh can measure the ORCHESTRATOR's own context use (G0,
# docs/slice-sizing.md). Registered in .claude/settings.json for SessionStart and
# UserPromptSubmit. $PPID is the `claude` process that invoked this hook; the ledger
# walks up its own ancestors to that PID and reads <events dir>/<pid>.json.
# Override the location with CLAUDE_CONTROL_EVENTS_DIR (the ledger reads the same var).
set -u
dir="${CLAUDE_CONTROL_EVENTS_DIR:-$HOME/.claude-control/events}"
mkdir -p "$dir" 2>/dev/null || exit 0
payload="$(cat)"
CLAUDE_HOOK_PAYLOAD="$payload" CLAUDE_HOOK_PPID="$PPID" CLAUDE_HOOK_DIR="$dir" python3 -c '
import json, os, sys, time
try:
    data = json.loads(os.environ["CLAUDE_HOOK_PAYLOAD"])
except ValueError:
    sys.exit(0)
event = data.get("hook_event_name"); session = data.get("session_id")
if not event or not session:
    sys.exit(0)
row = {"event": event, "session_id": session, "cwd": data.get("cwd", ""),
       "transcript_path": data.get("transcript_path", ""), "ts": int(time.time())}
path = os.path.join(os.environ["CLAUDE_HOOK_DIR"], os.environ["CLAUDE_HOOK_PPID"] + ".json")
tmp = path + ".tmp"
with open(tmp, "w") as fh:
    json.dump(row, fh)
os.replace(tmp, path)
' 2>/dev/null
exit 0
