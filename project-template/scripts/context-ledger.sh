#!/usr/bin/env bash
# Usage: context-ledger.sh now | start KEY [--predicted N] | record KEY [--start-tokens N] [--predicted N] [--note TEXT] | calibrate; all accept --window N
set -euo pipefail
CONTEXT_LEDGER_WINDOW_DEFAULT=1000000
CONTEXT_LEDGER_FACTOR_DEFAULT=0.60
CONTEXT_LEDGER_TABLE_HEADER='| date | ticket | predicted | actual | ratio | compactions | turns | dispatches | window | notes |'
TICKET_PREFIX="${TICKET_PREFIX:-TICKET}"   # BOOTSTRAP: your ticket-key prefix (same as tickets.sh)

# Exact JSON handling is deliberately isolated here: never grep transcript usage.
json_helper() {
  CONTEXT_LEDGER_TABLE_HEADER="$CONTEXT_LEDGER_TABLE_HEADER" TICKET_PREFIX="$TICKET_PREFIX" python3 - "$@" <<'PY'
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

USAGE_KEYS = ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')
COMPACTION_KEYS = ('isCompactSummary', 'compact_boundary')
HEADER = os.environ['CONTEXT_LEDGER_TABLE_HEADER']
PREFIX = os.environ['TICKET_PREFIX']


def read_state(path):
    try:
        state = json.loads(Path(path).read_text())
        required = {'session_id', 'transcript_path', 'usage', 'turns', 'dispatches',
                    'lines', 'window', 'predicted', 'ts'}
        if not isinstance(state, dict) or not required <= state.keys():
            raise ValueError()
        if any(type(state[k]) is not int or state[k] < 0
               for k in ('turns', 'dispatches', 'lines', 'window', 'ts')):
            raise ValueError()
        if state['window'] <= 0 or (state['usage'] is not None and
                                   type(state['usage']) is not int):
            raise ValueError()
        if any(not isinstance(state[k], str) for k in ('session_id', 'transcript_path', 'predicted')):
            raise ValueError()
        if state['predicted'] != 'n/a' and not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', state['predicted']):
            raise ValueError()
        return state
    except (OSError, ValueError, TypeError):
        sys.exit('context-ledger.sh: invalid state file')


def scan(path, baseline_path, tools):
    state = read_state(baseline_path) if baseline_path else {}
    boundary = state.get('lines', 0)
    previous = state.get('usage')
    requests = set()
    dispatches = compactions = lines = 0
    usage = None
    last_request = None
    pending_marker = False
    for lines, line in enumerate(Path(path).open(), 1):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        marker = (any(record.get(k) is True for k in COMPACTION_KEYS) or
                  record.get('type') in ('summary', 'compact_boundary') or
                  record.get('subtype') == 'compact_boundary')
        event = bool(marker and lines > boundary)
        if marker:
            # A separate summary line and the next fresh usage drop are one event.
            pending_marker = True
        message = record.get('message')
        if record.get('type') == 'assistant' and isinstance(message, dict):
            raw = message.get('usage')
            request = record.get('requestId')
            valid = (isinstance(request, str) and bool(request) and
                     message.get('model') != '<synthetic>' and isinstance(raw, dict) and
                     all(type(raw.get(k)) is int for k in USAGE_KEYS))
            if valid:
                usage = sum(raw[k] for k in USAGE_KEYS)
                fresh = request not in requests
                if fresh:
                    if (lines > boundary and not pending_marker and
                            previous is not None and previous > 0):
                        event = event or usage * 10 <= previous * 7
                    pending_marker = False
                requests.add(request)
                last_request = request
                previous = usage
                for block in message.get('content', []):
                    if (isinstance(block, dict) and block.get('type') == 'tool_use'
                            and block.get('name') in tools):
                        dispatches += 1
        compactions += int(event)
    # The currently executing request belongs to the NEXT interval at both cuts.
    print('no usage line' if usage is None else '')
    print(usage if usage is not None else '')
    print(max(0, len(requests) - int(last_request is not None)))
    print(dispatches)
    print(lines)
    print(compactions)


mode, *args = sys.argv[1:]
if mode == 'event':
    path, started = args
    try:
        data = json.loads(Path(path).read_text())
        start = time.mktime(time.strptime(' '.join(started.split()), '%a %b %d %H:%M:%S %Y'))
        reason = ''
        if data.get('event') == 'SessionEnd':
            reason = 'stale events file'
        elif data.get('ts', 0) < start:
            reason = 'stale events file (pid reuse)'
        transcript = Path(data.get('transcript_path') or '/nonexistent-transcript')
        if not reason:
            if not transcript.is_file() or not os.access(transcript, os.R_OK):
                reason = 'transcript missing'
            elif transcript.stat().st_mtime < start:
                reason = 'stale events file'
        print(reason)
        print(data.get('session_id', ''))
        print(data.get('transcript_path', ''))
    except (OSError, ValueError, TypeError, AttributeError):
        print('stale events file\n\n')
elif mode == 'scan':
    scan(args[0], args[1], set(args[2].split()))
elif mode == 'state-read':
    state = read_state(args[0])
    for key in ('session_id', 'transcript_path', 'usage', 'turns', 'dispatches',
                'lines', 'window', 'predicted', 'ts', 'reason'):
        print(state.get(key) if state.get(key) is not None else '')
elif mode == 'state-write':
    path, session, transcript, usage, turns, dispatches, lines, window, predicted, ts, reason = args
    data = dict(session_id=session, transcript_path=transcript, usage=int(usage) if usage else None,
                turns=int(turns), dispatches=int(dispatches), lines=int(lines), window=int(window),
                predicted=predicted, ts=int(ts), reason=reason)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data) + '\n')
elif mode == 'predicted':
    path = Path(args[0])
    if not path.exists():
        print('n/a')
    else:
        blocks = path.read_text().split('---', 2)
        match = re.search(r'^size:[ \t]*([^\n]*)$', blocks[1], re.M) if len(blocks) > 2 else None
        value = match[1].strip() if match else ''
        print(value if re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', value) else 'n/a')
elif mode == 'manual-valid':
    sys.exit(0 if int(args[0]) <= int(args[1]) else 1)
elif mode == 'numbers':
    end, start, window, predicted = args
    raw = (int(end) - int(start)) / (0.6 * int(window))
    print(f'{raw:.2f}')
    print(f'{raw / float(predicted):.2f}' if predicted != 'n/a' and float(predicted) > 0 else 'n/a')
elif mode == 'now':
    usage, window = map(int, args)
    print(f'usage={usage} window={window} used={100 * usage / window:.2f}% '
          f'remaining={window - usage} slices={usage / (0.6 * window):.2f}')
elif mode == 'overlaps':
    directory, key, session, transcript, ts = args
    if not session or not transcript:
        sys.exit(0)
    for path in sorted(Path(directory).glob(PREFIX + '-*.json')):
        if path.stem == key:
            continue
        try:
            other = json.loads(path.read_text())
            if (other['session_id'] == session and other['transcript_path'] == transcript
                    and other['ts'] <= int(ts)):
                print('overlapping ' + path.stem)
        except (OSError, ValueError, KeyError, TypeError):
            continue
elif mode == 'rows':
    path, action, value = args
    rows = []
    warned = False
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            if not line.startswith('|') or line == HEADER or re.fullmatch(r'[| :\-]+', line):
                continue
            cells = [c.strip() for c in line.strip('|').split('|')]
            if len(cells) != 10 or not re.fullmatch(re.escape(PREFIX) + r'-[0-9]+', cells[1]):
                warned = True
                continue
            rows.append(cells)
    if action == 'contains':
        sys.exit(0 if any(row[1] == value for row in rows) else 1)
    ratios = []
    for row in rows:
        if row[4] == 'n/a':
            continue
        try:
            ratios.append(float(row[4]))
        except ValueError:
            warned = True
    if warned:
        print('context-ledger.sh: warning: skipped junk ledger rows', file=sys.stderr)
    selected = ratios[-5:]
    factor = statistics.median(selected) if selected else float(value)
    print(f'factor={factor:.2f} (n={len(selected)})')
PY
}

die() { printf 'context-ledger.sh: %s\n' "$*" >&2; exit 1; }
integer() { [[ "$1" =~ ^[0-9]+$ ]]; }
cmd=${1:-}; [ "$#" -gt 0 ] && shift
case "$cmd" in now|start|record|calibrate) ;; *) die 'expected now, start, record or calibrate' ;; esac
key= predicted= start_tokens= note= window=${CONTEXT_LEDGER_WINDOW:-$CONTEXT_LEDGER_WINDOW_DEFAULT}
window_source=CONTEXT_LEDGER_WINDOW
if [ "$cmd" = start ] || [ "$cmd" = record ]; then
  key=${1:-}; [ "$#" -gt 0 ] && shift
  [[ "$key" =~ ^${TICKET_PREFIX}-[0-9]+$ ]] || die "KEY must match ${TICKET_PREFIX}-[0-9]+"
fi
while [ "$#" -gt 0 ]; do
  case "$1" in
    --window|--predicted|--start-tokens|--note) ;;
    *) die 'unknown or extra argument' ;;
  esac
  [ "$#" -ge 2 ] || die "missing value for $1"
  case "$1" in
    --window) window=$2; window_source=--window ;;
    --predicted)
      [ "$cmd" = start ] || [ "$cmd" = record ] || die '--predicted requires start or record'
      [[ "$2" =~ ^[0-9]+(\.[0-9]+)?$ ]] || die '--predicted must be numeric'
      predicted=$2 ;;
    --start-tokens)
      [ "$cmd" = record ] || die '--start-tokens requires record'
      integer "$2" || die '--start-tokens must be a nonnegative integer'
      start_tokens=$2 ;;
    --note)
      [ "$cmd" = record ] || die '--note requires record'
      case "$2" in *'|'*|*'\'*|*$'\n'*|*$'\r'*) die '--note cannot contain pipe, newline or backslash' ;; esac
      note=$2 ;;
  esac
  shift 2
done
integer "$window" && [[ "$window" =~ [1-9] ]] || die "$window_source must be a positive integer"
common=$(cd "$(git rev-parse --git-common-dir)" && pwd)
main_root=$(dirname "$common")
state_dir=${CONTEXT_LEDGER_STATE_DIR:-$common/context-ledger}
ledger=${CONTEXT_LEDGER_FILE:-$main_root/docs/context-ledger.md}
events=${CLAUDE_CONTROL_EVENTS_DIR:-$HOME/.claude-control/events}
tools=${CONTEXT_LEDGER_DISPATCH_TOOLS:-Agent mcp__codex__codex}
state=$state_dir/$key.json
saved=()
if [ -n "$key" ] && [ -e "$state" ]; then
  # Validate even a replaced start before doing any writes (AC15).
  decoded=$(json_helper state-read "$state") || exit 1
  while IFS= read -r line; do saved+=("$line"); done <<< "$decoded"
  [ -z "$start_tokens" ] || die '--start-tokens cannot override an existing state file'
  if [ "$cmd" = record ]; then
    [ -z "$predicted" ] || die '--predicted cannot override prediction frozen at start'
    [ "$window_source" != --window ] || die '--window cannot override window frozen at start'
  fi
fi
if [ "$cmd" = calibrate ]; then
  json_helper rows "$ledger" calibrate "$CONTEXT_LEDGER_FACTOR_DEFAULT"; exit
fi
if [ "$cmd" = record ] && [ ! -e "$state" ] && json_helper rows "$ledger" contains "$key"; then
  echo "already recorded $key"; exit 0
fi

# Only the ancestor's event file is eligible; no directory scan / cwd fallback.
pid=$$; claude_pid=; started=; reason=; session=; transcript=
for ((hop=0; hop<32; hop++)); do
  comm=$(ps -o comm= -p "$pid" 2>/dev/null) || break
  comm=${comm##*/}
  if [ "$comm" = claude ]; then claude_pid=$pid; break; fi
  pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ') || break
  case "$pid" in ''|0|1) break ;; esac
done
if [ -z "$claude_pid" ]; then reason='no claude ancestor'
elif [ ! -f "$events/$claude_pid.json" ]; then reason="no events file for pid $claude_pid"
else
  started=$(LC_ALL=C ps -o lstart= -p "$claude_pid" 2>/dev/null) || started=
  event_values=()
  while IFS= read -r line; do event_values+=("$line"); done < <(json_helper event "$events/$claude_pid.json" "$started")
  reason=${event_values[0]:-}; session=${event_values[1]:-}; transcript=${event_values[2]:-}
fi
usage=; turns=0; dispatches=0; lines=0; compactions=0
if [ -z "$reason" ]; then
  scan_state=
  if [ "$cmd" = record ] && [ -e "$state" ]; then scan_state=$state; fi
  values=()
  while IFS= read -r line; do values+=("$line"); done < <(json_helper scan "$transcript" "$scan_state" "$tools")
  reason=${values[0]:-no usage line}; usage=${values[1]:-}
  # An empty first line means success (:- would substitute the failure default).
  if [ "${#values[@]}" -ge 6 ]; then reason=${values[0]}; fi
  turns=${values[2]:-0}; dispatches=${values[3]:-0}; lines=${values[4]:-0}; compactions=${values[5]:-0}
fi
if [ "$cmd" = now ]; then
  if [ -n "$reason" ]; then echo "unmeasured: $reason"; else json_helper now "$usage" "$window"; fi
  exit 0
fi
if [ -z "$predicted" ]; then predicted=$(json_helper predicted "$main_root/tickets/$key.md"); fi
ts=$(date +%s)
if [ "$cmd" = start ]; then
  json_helper state-write "$state" "$session" "$transcript" "$usage" "$turns" "$dispatches" "$lines" "$window" "$predicted" "$ts" "$reason"
  if [ -n "$reason" ]; then echo "unmeasured: $reason"; else echo "ledger start $key usage=$usage predicted=$predicted"; fi
  exit 0
fi
actual=n/a; ratio=n/a; base=; row_turns=unknown; row_dispatches=unknown; row_compactions=unknown
if [ -e "$state" ]; then
  window=${saved[6]}; predicted=${saved[7]}; base=${saved[2]}
  if [ -z "$reason" ] || [ "$reason" = 'no usage line' ]; then
    if [ "${saved[0]}" != "$session" ]; then reason='session changed'
    elif [ "${saved[1]}" != "$transcript" ]; then reason='transcript replaced'
    elif [ "$lines" -lt "${saved[5]}" ]; then reason='transcript truncated'
    elif [ -n "$reason" ]; then :
    elif [ -n "${saved[9]:-}" ]; then reason=${saved[9]}
    elif [ -z "$base" ]; then reason='no usage line'
    elif [ "$usage" -lt "$base" ] && [ "$compactions" -eq 0 ]; then reason='negative delta'
    else
      row_turns=$((turns - saved[3])); row_dispatches=$((dispatches - saved[4])); row_compactions=$compactions
    fi
  fi
elif [ -n "$start_tokens" ]; then
  if [ -n "$usage" ] && ! json_helper manual-valid "$start_tokens" "$usage"; then die '--start-tokens must not exceed current usage'; fi
  base=$start_tokens
  note="manual start $start_tokens${note:+; $note}"
else reason='no start marker'
fi
if [ -z "$reason" ]; then
  if [ -e "$state" ] && [ "$compactions" -gt 0 ]; then actual='> 1.0 (compacted?)'
  else
    numbers=()
    while IFS= read -r line; do numbers+=("$line"); done < <(json_helper numbers "$usage" "$base" "$window" "$predicted")
    actual=${numbers[0]}; ratio=${numbers[1]}
  fi
else
  note="${note:+$note; }unmeasured: $reason"
  echo "unmeasured: $reason"
fi
while IFS= read -r overlap; do
  [ -z "$overlap" ] || note="${note:+$note; }$overlap"
done < <(json_helper overlaps "$state_dir" "$key" "$session" "$transcript" "$ts")
mkdir -p "$(dirname "$ledger")"
if [ ! -f "$ledger" ]; then
  printf '%s\n' "$CONTEXT_LEDGER_TABLE_HEADER" '|---|---|---|---|---|---|---|---|---|---|' > "$ledger"
fi
printf '| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |\n' "$(date +%F)" "$key" "$predicted" "$actual" "$ratio" "$row_compactions" "$row_turns" "$row_dispatches" "$window" "$note" >> "$ledger"
rm -f "$state"
