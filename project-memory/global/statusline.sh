#!/bin/bash
# Claude Code statusLine
# Fields: worktree-vs-main (LOUD) + branch | model (short form) | reasoning effort |
#         context window remaining | 5h/7d rate-limit REMAINING + reset countdown |
#         codex 7-day quota remaining (snapshot + age) | active Claude account
#
# Every quota is drawn as a BAR as well as a number: a bare percentage has to be
# read and compared, a bar is seen. All four bars fill with what is LEFT and are
# coloured by severity (green / yellow / red), so "running out" is visible
# without reading anything. ctx included — it reads "30% left", not "70% used",
# so the whole line has ONE direction and needs no mental switching.
#
# Managed by the statusline-setup agent — ask Claude to update it, don't hand-edit.
#
# This file lives in <memory repo>/global/ and is symlinked to
# ~/.claude/statusline.sh on every machine by bin/sync-memory.sh (link_global),
# so an edit here reaches all machines on the next sync.

input=$(cat)

# Every field below is parsed with jq. Without it the line would silently
# degrade to all-"n/a", which is indistinguishable from a broken payload — say
# so explicitly instead, since a fresh machine may not have jq installed.
if ! command -v jq >/dev/null 2>&1; then
  printf '\033[1;31mstatusline: jq not found\033[0m \033[2m(brew install jq)\033[0m\n'
  exit 0
fi

# 0. Quota bar rendering, shared by every percentage field below.
#
#    Cells are drawn with the 1/8-width block glyphs, so a 10-column bar has 80
#    distinguishable steps instead of 10: a few-percent move actually changes
#    the picture rather than being rounded into the same column, which is the
#    whole reason for showing a bar next to the number.
#
#    The bar always represents REMAINING, matching the numbers beside it.
BAR_W=10
BAR_GREEN=$'\033[32m'
BAR_YELLOW=$'\033[33m'
BAR_RED=$'\033[1;31m'
BAR_RESET=$'\033[0m'
DIM=$'\033[2m'

bar() {
  # $1 = percent REMAINING (0-100)
  awk -v p="$1" -v w="$BAR_W" 'BEGIN {
    if (p < 0) p = 0
    if (p > 100) p = 100
    e[1]="▏"; e[2]="▎"; e[3]="▍"; e[4]="▌"; e[5]="▋"; e[6]="▊"; e[7]="▉"
    t = p * w / 100
    f = int(t)
    r = int((t - f) * 8 + 0.5)
    if (r == 8) { f++; r = 0 }
    for (i = 0; i < f && i < w; i++) printf "█"
    if (f < w && r > 0) { printf "%s", e[r]; f++ }
    for (i = f; i < w; i++) printf "░"
  }'
}

bar_color() {
  # Severity of what is LEFT — the thresholds are about "should I care yet",
  # not even thirds: below 20% left a 5-hour window can end mid-task.
  awk -v p="$1" 'BEGIN { print (p < 20) ? "red" : ((p < 50) ? "yellow" : "green") }'
}

# Renders "<bar> <pct>%" already coloured by severity.
gauge() {
  # $1 = percent remaining
  local pct="$1" c
  case "$(bar_color "$pct")" in
    red) c="$BAR_RED" ;;
    yellow) c="$BAR_YELLOW" ;;
    *) c="$BAR_GREEN" ;;
  esac
  printf '%s%s %.0f%%%s' "$c" "$(bar "$pct")" "$pct" "$BAR_RESET"
}

# 1. Reasoning effort (effort.level) — only present when the model supports it.
effort=$(echo "$input" | jq -r '.effort.level // empty')
[ -z "$effort" ] && effort="n/a"

# 1b. Model name, short "family version" form (e.g. "opus 4.8", "sonnet 5",
#     "fable 5", "haiku 4.5") — placed just before effort, since effort only
#     reads meaningfully in the context of WHICH model loaded it.
#
#     Derived primarily from model.id, which is the stable machine-readable
#     name (id "claude-opus-4-8" -> family "opus" + dash-joined version
#     tokens "4-8" -> dotted "4.8"; a trailing 8-digit YYYYMMDD token, as in
#     "claude-haiku-4-5-20251001", is dropped rather than joined in). This
#     is preferred over display_name because it's a stable machine-parsed
#     shape rather than free text. display_name is only a fallback for ids
#     that don't match the "claude-<family>-<digits...>" shape (e.g. an
#     older "claude-3-5-sonnet-..." id) — for those, strip a leading
#     "Claude " and lowercase what's left.
model_id=$(echo "$input" | jq -r '.model.id // empty')
model_display=$(echo "$input" | jq -r '.model.display_name // empty')

short_model() {
  # $1 = model id, $2 = display_name
  local id="$1" display="$2" family rest tok joined=""
  if [[ "$id" =~ ^claude-([a-z]+)-([0-9].*)$ ]]; then
    family="${BASH_REMATCH[1]}"
    rest="${BASH_REMATCH[2]}"
    local parts=()
    IFS='-' read -ra parts <<< "$rest"
    for tok in "${parts[@]}"; do
      [[ "$tok" =~ ^[0-9]{8}$ ]] && continue   # drop a YYYYMMDD date stamp
      if [ -z "$joined" ]; then joined="$tok"; else joined="${joined}.${tok}"; fi
    done
    if [ -n "$joined" ]; then
      echo "${family} ${joined}"
      return
    fi
  fi
  if [ -n "$display" ]; then
    local d="${display#Claude }"
    d="${d#claude }"
    echo "$d" | tr '[:upper:]' '[:lower:]'
    return
  fi
  echo "n/a"
}

model_short=$(short_model "$model_id" "$model_display")
[ -z "$model_short" ] && model_short="n/a"

# 2/5. Worktree + branch — resolved from the session's ACTUAL cwd, never a
#      hard-coded path: this project runs every ticket in its own git worktree
#      (main checkout's directory name also varies per machine). Detected
#      STRUCTURALLY per project convention: git-dir == git-common-dir means the
#      primary checkout; different means a linked worktree.
dir=$(echo "$input" | jq -r '.workspace.current_dir // .cwd // empty')
branch=""
wt_label=""
if [ -n "$dir" ] && [ -d "$dir" ]; then
  branch=$(git --no-optional-locks -C "$dir" branch --show-current 2>/dev/null)
  if [ -z "$branch" ]; then
    branch=$(git --no-optional-locks -C "$dir" rev-parse --short HEAD 2>/dev/null)
  fi

  abs_paths=$(git --no-optional-locks -C "$dir" rev-parse --path-format=absolute --git-dir --git-common-dir 2>/dev/null)
  if [ -n "$abs_paths" ]; then
    gd=$(printf '%s\n' "$abs_paths" | sed -n '1p')
    gcd=$(printf '%s\n' "$abs_paths" | sed -n '2p')
  else
    # older git without --path-format=absolute: resolve manually
    gd=$(git --no-optional-locks -C "$dir" rev-parse --absolute-git-dir 2>/dev/null)
    raw_gcd=$(git --no-optional-locks -C "$dir" rev-parse --git-common-dir 2>/dev/null)
    case "$raw_gcd" in
      /*) gcd="$raw_gcd" ;;
      *) gcd=$(cd "$dir" 2>/dev/null && cd "$raw_gcd" 2>/dev/null && pwd) ;;
    esac
  fi
  if [ -n "$gd" ] && [ -n "$gcd" ]; then
    if [ "$gd" = "$gcd" ]; then
      wt_label="main"
    else
      top=$(git --no-optional-locks -C "$dir" rev-parse --show-toplevel 2>/dev/null)
      wt_label=$(basename "$top" 2>/dev/null)
    fi
  fi
fi
# Fallback for a Claude Code built-in --worktree session if git resolution
# above didn't turn up anything (e.g. dir not found/readable).
if [ -z "$branch" ]; then
  branch=$(echo "$input" | jq -r '.worktree.branch // empty')
fi
[ -z "$branch" ] && branch="n/a"
[ -z "$wt_label" ] && wt_label="?"

RED_BOLD=$'\033[1;31m'
GREEN_BOLD=$'\033[1;32m'
GREY=$'\033[2;37m'
if [ "$wt_label" = "?" ]; then
  # Not a git checkout at all — must NOT reuse the green that means
  # "safely inside a worktree".
  wt_color="$GREY"
  combo="no-git"
elif [ "$wt_label" = "main" ]; then
  wt_color="$RED_BOLD"          # unmistakable: you are in the PRIMARY checkout
  combo="MAIN:${branch}"
else
  wt_color="$GREEN_BOLD"
  # worktree name and branch are usually redundant (ABC-123 vs
  # ABC-123-slug) — elide the shared prefix instead of repeating it.
  case "$branch" in
    "$wt_label") combo="$wt_label" ;;
    "$wt_label"-*) combo="${wt_label}/${branch#"$wt_label"-}" ;;
    *) combo="${wt_label}:${branch}" ;;
  esac
fi

# 3. Context status — remaining_percentage preferred, used_percentage fallback,
#    plus raw token counts (formatted k/M) when available. All can be null
#    before the session's first turn.
used_pct=$(echo "$input" | jq -r '.context_window.used_percentage // empty')
remaining_pct=$(echo "$input" | jq -r '.context_window.remaining_percentage // empty')
in_tokens=$(echo "$input" | jq -r '.context_window.total_input_tokens // empty')
win_size=$(echo "$input" | jq -r '.context_window.context_window_size // empty')

fmt_tokens() {
  awk -v n="$1" 'BEGIN {
    if (n >= 1000000) { v = n / 1000000; if (v == int(v)) printf "%dM", v; else printf "%.1fM", v }
    else if (n >= 1000) { printf "%dk", int((n + 500) / 1000) }
    else { printf "%d", n }
  }'
}

if [ -n "$remaining_pct" ]; then
  ctx=$(gauge "$remaining_pct")
elif [ -n "$used_pct" ]; then
  ctx=$(gauge "$(awk -v u="$used_pct" 'BEGIN { printf "%.4f", 100 - u }')")
else
  ctx="n/a"
fi
if [ -n "$in_tokens" ] && [ -n "$win_size" ] && [ "$win_size" -gt 0 ] 2>/dev/null; then
  ctx="${ctx} ($(fmt_tokens "$in_tokens")/$(fmt_tokens "$win_size"))"
fi

# 4. Claude.ai subscription rate limits — 5-hour session + 7-day weekly windows.
#    Absent entirely for non-subscriber / API-key sessions or before the first
#    API response of the session. The JSON only gives used_percentage +
#    resets_at (epoch seconds) — there is no remaining_percentage field for
#    rate_limits (unlike context_window, which has one) — so remaining% and
#    the reset countdown are BOTH derived here.
now_epoch=$(date +%s)

fmt_rate_limit() {
  # $1 = used_percentage, $2 = resets_at (epoch seconds)
  local used="$1" resets="$2"
  if [ -z "$used" ]; then
    echo "n/a"
    return
  fi
  local left
  left=$(awk -v u="$used" 'BEGIN { printf "%.4f", 100 - u }')
  local out
  out=$(gauge "$left")
  if [ -n "$resets" ]; then
    # Cascading d/h/m so the 7-day window (often >24h out) doesn't render as
    # an unreadable "77h0m" — same "biggest nonzero unit + one more" shape
    # used for the 5h window ("2h14m").
    local delta d h m rem cd
    delta=$(awk -v r="$resets" -v n="$now_epoch" 'BEGIN { d = r - n; if (d < 0) d = 0; printf "%d", d }')
    d=$((delta / 86400))
    rem=$((delta % 86400))
    h=$((rem / 3600))
    m=$(((rem % 3600) / 60))
    if [ "$d" -gt 0 ]; then
      cd="${d}d${h}h"
    elif [ "$h" -gt 0 ]; then
      cd="${h}h${m}m"
    else
      cd="${m}m"
    fi
    # Same "↻" + dim-parens shape as the codex field below: all three quota
    # readouts must render their reset countdown identically.
    out="${out} ${DIM}(↻${cd})${BAR_RESET}"
  fi
  echo "$out"
}

five_h_used=$(echo "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty')
five_h_reset=$(echo "$input" | jq -r '.rate_limits.five_hour.resets_at // empty')
seven_d_used=$(echo "$input" | jq -r '.rate_limits.seven_day.used_percentage // empty')
seven_d_reset=$(echo "$input" | jq -r '.rate_limits.seven_day.resets_at // empty')

five_h=$(fmt_rate_limit "$five_h_used" "$five_h_reset")
seven_d=$(fmt_rate_limit "$seven_d_used" "$seven_d_reset")

# 5b. Codex weekly (7-day) quota.
#
#     Codex exposes no live quota API. The only local source is the rate_limits
#     block the CLI writes into a session's rollout log on each API response, so
#     this is a SNAPSHOT that is exactly as fresh as the last codex run — never
#     a live reading.
#
#     Which window is the 7-day one is read from window_minutes (10080), not
#     assumed to be `primary`: the primary/secondary split differs by plan, and
#     on this account `secondary` is null while `primary` carries the weekly
#     window. If no window is exactly weekly, the longest one is used.
#
#     What's shown next to the gauge is WHEN THE QUOTA RESETS, not when the
#     snapshot was captured: the window object carries `resets_at` — an ABSOLUTE
#     epoch-seconds stamp (verified in a real rollout payload: {"used_percent":
#     12.0,"window_minutes":10080,"resets_at":1785914057}), same shape as
#     claude.ai's own rate_limits.*.resets_at above — so the countdown needs no
#     snapshot-capture-time proxy and stays correct however stale the snapshot
#     is. If `resets_at` is missing (older codex CLI), this falls back to the
#     previous "(age ago)" snapshot-freshness display rather than inventing a
#     reset time.
codex_gauge="n/a"
codex_dir="$HOME/.codex/sessions"
if [ -d "$codex_dir" ]; then
  # Newest-first by MTIME, not by filename: a resumed older session writes the
  # freshest snapshot while sorting earlier by its creation-stamped name.
  # The newest file can predate the first API response and carry no
  # rate_limits at all, so walk down a few until one yields a reading.
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    line=$(grep 'rate_limits' "$f" 2>/dev/null | tail -1)
    [ -n "$line" ] || continue
    cdx_data=$(printf '%s' "$line" | jq -r '
      [.payload.rate_limits.primary, .payload.rate_limits.secondary]
      | map(select(type == "object"))
      | ((map(select(.window_minutes == 10080)) | first) // (sort_by(.window_minutes) | last))
      | if . == null then empty else [(.used_percent // empty), (.resets_at // empty)] | @tsv end' 2>/dev/null)
    [ -n "$cdx_data" ] || continue
    cdx_used=$(printf '%s' "$cdx_data" | cut -f1)
    cdx_resets_at=$(printf '%s' "$cdx_data" | cut -f2)
    [ -n "$cdx_used" ] || continue

    cdx_left=$(awk -v u="$cdx_used" 'BEGIN { printf "%.4f", 100 - u }')
    codex_gauge=$(gauge "$cdx_left")

    # mtime (BSD stat, GNU fallback) is only the FALLBACK "snapshot age"
    # display now — the reset countdown comes straight from resets_at.
    mt=$(stat -f %m "$f" 2>/dev/null || stat -c %Y "$f" 2>/dev/null)
    if [ -n "$cdx_resets_at" ]; then
      reset_epoch="${cdx_resets_at%.*}"
      rdelta=$((reset_epoch - now_epoch))
      [ "$rdelta" -lt 0 ] && rdelta=0
      rd=$((rdelta / 86400))
      rrem=$((rdelta % 86400))
      rh=$((rrem / 3600))
      rm=$(((rrem % 3600) / 60))
      if [ "$rd" -gt 0 ]; then
        reset_s="↻${rd}d${rh}h"
      elif [ "$rh" -gt 0 ]; then
        reset_s="↻${rh}h${rm}m"
      else
        reset_s="↻${rm}m"
      fi
      codex_gauge="${codex_gauge} ${DIM}(${reset_s})${BAR_RESET}"
    elif [ -n "$mt" ]; then
      age=$((now_epoch - mt))
      [ "$age" -lt 0 ] && age=0
      if [ "$age" -ge 86400 ]; then
        age_s="$((age / 86400))d ago"
      elif [ "$age" -ge 3600 ]; then
        age_s="$((age / 3600))h ago"
      elif [ "$age" -ge 60 ]; then
        age_s="$((age / 60))m ago"
      else
        # "0m ago" reads as a broken clock rather than as "current".
        age_s="now"
      fi
      codex_gauge="${codex_gauge} ${DIM}(${age_s})${BAR_RESET}"
    fi
    break
  done <<<"$(find "$codex_dir" -name 'rollout-*.jsonl' -print0 2>/dev/null | xargs -0 ls -t 2>/dev/null | head -5)"
fi

# 6. Active Claude Code account — this is the MACHINE'S single global login
#    (~/.claude.json → .oauthAccount), NOT a per-session value: it reflects
#    whichever account is currently signed in on this machine at render time.
acct="n/a"
claude_json="$HOME/.claude.json"
if [ -r "$claude_json" ]; then
  email=$(jq -r '.oauthAccount.emailAddress // empty' "$claude_json" 2>/dev/null)
  if [ -n "$email" ]; then
    acct="${email%.*}"   # drop only the trailing TLD — keeps local-part AND org/domain, the two possible discriminators
  fi
fi

RESET=$'\033[0m'
CYAN=$'\033[36m'
MAGENTA=$'\033[35m'
BRIGHT_WHITE=$'\033[1;97m'   # was BLUE — dark blue is unreadable on a black terminal bg

# The four quota fields carry no per-field identity colour any more: they are
# coloured by SEVERITY inside gauge(), which is what the eye should be picking
# up. Only their dim labels distinguish them.
seg_wt="${wt_color}${combo}${RESET}"
seg_model="${MAGENTA}${model_short}${RESET}"
seg_eff="${DIM}eff:${RESET}${CYAN}${effort}${RESET}"
seg_ctx="${DIM}ctx ${RESET}${ctx}"
seg_rl="${DIM}5h ${RESET}${five_h} ${DIM}7d ${RESET}${seven_d}"
seg_cdx="${DIM}cdx ${RESET}${codex_gauge}"
seg_acct="${DIM}acct:${RESET}${BRIGHT_WHITE}${acct}${RESET}"

printf '%s  %s  %s  %s  %s  %s  %s\n' "$seg_wt" "$seg_model" "$seg_eff" "$seg_ctx" "$seg_rl" "$seg_cdx" "$seg_acct"
