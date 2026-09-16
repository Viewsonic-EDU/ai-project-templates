#!/usr/bin/env bash
# Ticket board — one file per ticket, status is the single source of truth.
#
# The store lives ONCE in the main checkout's tickets/ dir and is shared by every
# worktree: tickets.sh always resolves it via `git rev-parse --git-common-dir`, so a
# status change made from any worktree lands on one board. Per-ticket files keep
# parallel edits conflict-free (different ticket = different file).
#
# Usage:
#   tickets.sh                             live TUI on a terminal; static board in pipes
#   tickets.sh watch                       live read-only board (TICKETS_INTERVAL=2)
#   tickets.sh board                       kanban view (todo | in-progress | done)
#   tickets.sh list [status]               one line per ticket, optionally filtered
#   tickets.sh show <TICKET>               print one ticket file
#   tickets.sh new <TICKET> "title"        create a ticket (status=todo; title required)
#   tickets.sh mv <TICKET> <status> [--force]  set status; force overrides blockers on done
#   tickets.sh close <TICKET> [--force]    shorthand for: mv <TICKET> done [--force]
#   tickets.sh set-worktree <TICKET> [KEY|PATH]  store a portable key; omit to clear
#   tickets.sh block <TICKET> B1 [B2…]     add ticket dependencies
#   tickets.sh unblock <TICKET> B1 [B2…]   remove ticket dependencies
#   tickets.sh ready                       todo tasks partitioned by dependency readiness
set -euo pipefail
shopt -s nullglob

STATUSES="wishlist todo in-progress done"
TODAY="$(date +%F)"

# --- resolve the single shared store (main checkout, not the current worktree) ------
GIT_COMMON="$(cd "$(git rev-parse --git-common-dir 2>/dev/null)" 2>/dev/null && pwd)" \
  || { echo "tickets.sh: not inside a git repository" >&2; exit 1; }
MAIN_ROOT="$(dirname "$GIT_COMMON")"
TICKETS_DIR="$MAIN_ROOT/tickets"
WT_ROOT="${WT_ROOT:-$(dirname "$MAIN_ROOT")/$(basename "$MAIN_ROOT")-worktrees}"
[[ "$WT_ROOT" = /* ]] || WT_ROOT="$MAIN_ROOT/$WT_ROOT"

# BOOTSTRAP: your ticket-key prefix (e.g. AB, PROJ). Keys are then <PREFIX>-N / <PREFIX>-Nx.
# The usage text above writes them as <TICKET>; help prints the configured prefix.
TICKET_PREFIX="${TICKET_PREFIX:-TICKET}"

# --- helpers -----------------------------------------------------------------------
die() { echo "tickets.sh: $*" >&2; exit 1; }

valid_key()    { [[ "$1" =~ ^${TICKET_PREFIX}-[0-9]+[a-z]*$ ]]; }
valid_status() { case " $STATUSES " in *" $1 "*) return 0;; *) return 1;; esac; }

file_for() { printf '%s/%s.md' "$TICKETS_DIR" "$1"; }

# read one frontmatter field (first `---` block only), trimmed
field() { # file name
  awk -v k="$2" '
    /^---[ \t]*$/ { n++; next }
    n==1 && $0 ~ "^"k":" {
      sub("^"k":[ \t]*", ""); sub(/[ \t]+$/, ""); print; exit
    }
  ' "$1"
}

# rewrite one frontmatter field in place (first `---` block only)
set_field() { # file name value
  local f=$1 name=$2 val=$3 tmp
  tmp="$(mktemp)"
  awk -v k="$name" -v v="$val" '
    BEGIN { n=0; done=0 }
    /^---[ \t]*$/ { n++; print; next }
    n==1 && !done && $0 ~ "^"k":" { print k": "v; done=1; next }
    { print }
  ' "$f" > "$tmp" && mv "$tmp" "$f"
}

require_ticket() { # key
  valid_key "$1" || die "bad key '$1' (expected ${TICKET_PREFIX}-N or ${TICKET_PREFIX}-Nx)"
  [ -f "$(file_for "$1")" ] || die "no such ticket: $1 (create it with: tickets.sh new $1)"
}

# Insert or replace inside the first frontmatter block, then copy the body verbatim
# (including a body without a final newline). Legacy setters retain their behavior.
upsert_field() { # file name value
  local f=$1 name=$2 val=$3 tmp line n=0 found=0
  tmp="$(mktemp)"
  {
    while IFS= read -r line; do
      if [[ "$line" =~ ^---[[:blank:]]*$ ]]; then
        n=$((n + 1))
        if [ "$n" -eq 2 ]; then
          [ "$found" -eq 1 ] || printf '%s: %s\n' "$name" "$val"
          printf '%s\n' "$line"
          cat
          break
        fi
      fi
      if [ "$n" -eq 1 ] && [ "$name" = blocked_by ] && [ "$found" -eq 1 ] && [[ "$line" == blocked_by:* ]]; then continue; fi
      if [ "$n" -eq 1 ] && [ "$found" -eq 0 ] && [[ "$line" == "$name:"* ]]; then
        printf '%s: %s\n' "$name" "$val"
        found=1
      else
        printf '%s\n' "$line"
      fi
    done
  } < "$f" > "$tmp"
  mv "$tmp" "$f"
}

# Parse all-or-nothing: no output until every entry validates. Missing is empty,
# but an explicitly empty value is malformed. Whole-key sets preserve first order.
blockers() { # file; 0 = valid, 2 = malformed
  local raw inner entry seen=" " result=
  raw=$(field "$1" blocked_by)
  if [ -z "$raw" ]; then
    if awk '/^---[ \t]*$/ { n++; next } n==1 && /^blocked_by:/ { found=1 }
      END { exit !found }' "$1"; then return 2; fi
    return 0
  fi
  [[ "$raw" == \[*\] ]] || return 2
  inner=${raw#\[}; inner=${inner%\]}
  inner=${inner#"${inner%%[![:space:]]*}"}; inner=${inner%"${inner##*[![:space:]]}"}
  [ -n "$inner" ] || return 0
  while :; do
    entry=${inner%%,*}
    entry=${entry#"${entry%%[![:space:]]*}"}; entry=${entry%"${entry##*[![:space:]]}"}
    valid_key "$entry" || return 2
    case "$seen" in
      *" $entry "*) ;;
      *) seen+="$entry "; result+="$entry"$'\n' ;;
    esac
    [[ "$inner" == *,* ]] || break
    inner=${inner#*,}
  done
  printf '%s' "$result"
}

write_blockers() { # file [keys...]
  local f=$1 value= sep= key; shift
  for key in "$@"; do value+="$sep$key"; sep=", "; done
  upsert_field "$f" blocked_by "[$value]"
  upsert_field "$f" updated "$TODAY"
}

unresolved_blockers() { # key -> blocker<TAB>reason; malformed is fail-closed
  local key=$1 parsed blocker f status
  if ! parsed=$(blockers "$(file_for "$key")"); then
    echo "tickets.sh: warn: $key has malformed blocked_by" >&2
    printf '\tmalformed\n'
    return 0
  fi
  [ -n "$parsed" ] || return 0
  while IFS= read -r blocker; do
    f=$(file_for "$blocker")
    if [ ! -f "$f" ]; then
      echo "tickets.sh: warn: $key has missing blocker $blocker" >&2
      printf '%s\tmissing\n' "$blocker"
    else
      status=$(field "$f" status); status=${status:-unknown}
      [ "$status" = done ] || printf '%s\t%s\n' "$blocker" "$status"
    fi
  done <<< "$parsed"
}

blocker_description() { # unresolved records, names or reasons
  local record key reason sep=
  [ -n "$1" ] || return 0
  while IFS= read -r record; do
    key=${record%%$'\t'*}; reason=${record#*$'\t'}
    if [ "$reason" = malformed ]; then
      if [ "$2" = reasons ]; then printf '%s(malformed blocked_by)' "$sep"
      else printf '%smalformed' "$sep"
      fi
    elif [ "$2" = reasons ]; then printf '%s%s (%s)' "$sep" "$key" "$reason"
    else printf '%s%s' "$sep" "$key"
    fi
    sep=", "
  done <<< "$1"
}

# Iterative depth-first traversal avoids both recursion and arbitrary depth limits.
# Valid keys contain no whitespace; a plain-string visited set terminates old cycles.
reaches() { # target from; 0 = no reach, 1 = reach, 3 = malformed graph
  local target=$1 pending=$2 visited=" " node parsed blocker
  while [ -n "$pending" ]; do
    node=${pending%% *}
    if [[ "$pending" == *" "* ]]; then pending=${pending#* }; else pending=; fi
    [ "$node" != "$target" ] || return 1
    case "$visited" in *" $node "*) continue ;; esac
    visited+="$node "
    [ -f "$(file_for "$node")" ] || continue
    if ! parsed=$(blockers "$(file_for "$node")"); then
      echo "tickets.sh: warn: $node has malformed blocked_by" >&2
      return 3
    fi
    [ -n "$parsed" ] || continue
    while IFS= read -r blocker; do pending="$blocker${pending:+ $pending}"; done <<< "$parsed"
  done
  return 0
}

cmd_dependencies() { # command key blockers...
  local command=$1 key=${2:-} f parsed blocker original=" " requested=" " check
  local -a result=()
  shift
  [ "$#" -ge 2 ] || die "usage: tickets.sh $command ${TICKET_PREFIX}-N B1 [B2…]"
  require_ticket "$key"
  if [ "$command" = block ] && is_epic "$key"; then die "epic cannot have blockers: $key"; fi
  f=$(file_for "$key")
  if ! parsed=$(blockers "$f"); then
    echo "tickets.sh: warn: $key has malformed blocked_by" >&2
    die "$key has malformed blocked_by; refusing $command"
  fi
  # Resolution supplies the same stale-edge diagnostics as the other readers.
  unresolved_blockers "$key" > /dev/null
  if [ -n "$parsed" ]; then
    while IFS= read -r blocker; do original+="$blocker "; done <<< "$parsed"
  fi
  shift
  for blocker in "$@"; do
    valid_key "$blocker" || die "bad key '$blocker' (expected ${TICKET_PREFIX}-N or ${TICKET_PREFIX}-Nx)"
    case "$requested" in *" $blocker "*) continue ;; esac
    requested+="$blocker "
    if [ "$command" = block ]; then
      require_ticket "$blocker"
      [ "$blocker" != "$key" ] || die "$key cannot block itself"
      is_epic "$blocker" && die "epic cannot be a blocker: $blocker"
      check=0; reaches "$key" "$blocker" || check=$?
      case "$check" in
        1) die "$blocker creates a dependency cycle reaching $key" ;;
        3) die "$blocker reaches malformed blocked_by; refusing block" ;;
      esac
    else
      case "$original" in *" $blocker "*) ;; *) die "$blocker is not a blocker of $key" ;; esac
    fi
  done
  if [ -n "$parsed" ]; then
    while IFS= read -r blocker; do
      if [ "$command" = unblock ]; then
        case "$requested" in *" $blocker "*) continue ;; esac
      fi
      result+=("$blocker")
    done <<< "$parsed"
  fi
  if [ "$command" = block ]; then
    for blocker in $requested; do
      case "$original" in *" $blocker "*) ;; *) result+=("$blocker") ;; esac
    done
  fi
  if [ "${#result[@]}" -gt 0 ]; then write_blockers "$f" "${result[@]}"
  else write_blockers "$f"
  fi
  echo "$key blocked_by $(field "$f" blocked_by)"
}

is_epic() {
  valid_key "$1" && [ -f "$(file_for "$1")" ] &&
    [ "$(field "$(file_for "$1")" type)" = epic ]
}

require_epic() {
  require_ticket "$1"
  is_epic "$1" || die "$1 is not an epic (create one with: tickets.sh new-epic)"
}

validate_parent() { # child parent (before any write)
  [ "$1" != "$2" ] || die "a ticket cannot be its own epic: $1"
  is_epic "$1" && die "an epic cannot be a child: $1 (nested epics are not supported)"
  require_epic "$2"
}

epic_children() { # parent; paths in board glob order
  local f
  for f in "$TICKETS_DIR"/*.md; do
    [ "$(field "$f" epic)" != "$1" ] || printf '%s\n' "$f"
  done
}

epic_counts() { # parent; done todo wishlist total, derived afresh from child files
  local f status done_count=0 todo_count=0 wishlist_count=0 total=0
  while IFS= read -r f; do
    status="$(field "$f" status)"
    total=$((total + 1))
    case "$status" in
      done) done_count=$((done_count + 1)) ;;
      todo) todo_count=$((todo_count + 1)) ;;
      wishlist) wishlist_count=$((wishlist_count + 1)) ;;
    esac
  done < <(epic_children "$1")
  printf '%s %s %s %s\n' "$done_count" "$todo_count" "$wishlist_count" "$total"
}

# Internal only: no public move guard, child callbacks, or no-op timestamp writes.
set_epic_status() { # key status done total
  local f; f="$(file_for "$1")"
  [ "$(field "$f" status)" != "$2" ] || return 0
  upsert_field "$f" status "$2"
  upsert_field "$f" updated "$TODAY"
  echo "$1 -> $2 ($3/$4 children done)"
}

# Best-effort shared parent: no lock/index; each invocation scans ALL children.
recompute_epic() {
  local key=$1 done_count todo_count wishlist_count total in_progress status
  if ! is_epic "$key"; then
    echo "tickets.sh: warn: epic '$key' is missing or not an epic; skipping recompute" >&2
    return 0
  fi
  read -r done_count todo_count wishlist_count total < <(epic_counts "$key")
  [ "$total" -gt 0 ] || return 0
  in_progress=$((total - done_count - todo_count - wishlist_count))
  if [ "$in_progress" -gt 0 ]; then status=in-progress
  elif [ "$done_count" -eq "$total" ]; then status=done
  elif [ "$done_count" -gt 0 ]; then status=in-progress
  elif [ "$todo_count" -gt 0 ]; then status=todo
  else status=wishlist
  fi
  set_epic_status "$key" "$status" "$done_count" "$total"
}

epic_suffix() { # file key
  local done_count todo_count wishlist_count total
  if [ "$(field "$1" type)" = epic ]; then
    read -r done_count todo_count wishlist_count total < <(epic_counts "$2")
    printf ' [epic %s/%s]' "$done_count" "$total"
  fi
}

# --- commands ----------------------------------------------------------------------
cmd_create() {
  local command=$1; shift
  local key=${1:-} title=${2:-} parent= status=todo seen_epic=no
  valid_key "$key" || die "usage: tickets.sh $command ${TICKET_PREFIX}-N \"title\""
  [ -n "$title" ] || die "title required: tickets.sh $command $key \"a descriptive title\" (do not repeat the key)"
  [ "$title" = "$key" ] && die "title must not equal the key '$key' — give a descriptive title"
  local f; f="$(file_for "$key")"
  [ -f "$f" ] && die "ticket already exists: $key"
  shift 2
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --wishlist)
        [ "$command" = new ] && [ "$status" = todo ] || die "usage: tickets.sh $command $key \"title\""
        status=wishlist; shift ;;
      --epic)
        [ "$command" = new ] && [ "$seen_epic" = no ] && [ "$#" -ge 2 ] && [[ "$2" != --* ]] \
          || die "usage: tickets.sh $command $key \"title\" --epic PARENT"
        parent=$2; seen_epic=yes; shift 2 ;;
      *) die "usage: tickets.sh $command $key \"title\"" ;;
    esac
  done
  if [ "$seen_epic" = yes ]; then validate_parent "$key" "$parent"; fi
  mkdir -p "$TICKETS_DIR"
  cat > "$f" <<EOF
---
key: $key
title: $title
status: $status
spec:
worktree:
blocked_by: []
updated: $TODAY
---
EOF
  if [ "$command" = new-epic ]; then upsert_field "$f" type epic; fi
  if [ -n "$parent" ]; then upsert_field "$f" epic "$parent"; fi
  echo "created $key ($status)"
  if [ -n "$parent" ]; then recompute_epic "$parent"; fi
}

cmd_new() { cmd_create new "$@"; }
cmd_new_epic() { cmd_create new-epic "$@"; }

cmd_mv() {
  local key=${1:-} status=${2:-} force=no unresolved description
  [ "$#" -eq 2 ] || { [ "$#" -eq 3 ] && [ "$3" = --force ]; } \
    || die "usage: tickets.sh mv ${TICKET_PREFIX}-N <status> [--force]"
  [[ "$key" != --* && "$status" != --* ]] || die "usage: tickets.sh mv ${TICKET_PREFIX}-N <status> [--force]"
  if [ "$#" -eq 3 ]; then force=yes; fi
  require_ticket "$key"
  valid_status "$status" || die "bad status '$status' (one of: $STATUSES)"
  is_epic "$key" && die "epic status is derived from its children; move/close the children of $key instead"
  if [ "$status" = done ]; then
    unresolved=$(unresolved_blockers "$key")
    if [ -n "$unresolved" ]; then
      description=$(blocker_description "$unresolved" reasons)
      [ "$force" = yes ] || die "$key has unresolved blocker(s): $description"
      echo "tickets.sh: warn: $key closed over unresolved blocker(s): $description" >&2
    fi
  fi
  set_field "$(file_for "$key")" status "$status"
  set_field "$(file_for "$key")" updated "$TODAY"
  echo "$key -> $status"
  local parent; parent="$(field "$(file_for "$key")" epic)"
  if [ -n "$parent" ]; then recompute_epic "$parent"; fi
}

cmd_close() {
  [ "$#" -eq 1 ] || { [ "$#" -eq 2 ] && [ "$2" = --force ]; } \
    || die "usage: tickets.sh close ${TICKET_PREFIX}-N [--force]"
  local key=$1; shift
  cmd_mv "$key" done "$@"
}

cmd_set_epic() {
  local key=${1:-} parent=${2:-} f old_parent
  require_ticket "$key"
  validate_parent "$key" "$parent"
  f="$(file_for "$key")"; old_parent="$(field "$f" epic)"
  upsert_field "$f" epic "$parent"
  upsert_field "$f" updated "$TODAY"
  echo "$key -> epic $parent"
  if [ -n "$old_parent" ] && [ "$old_parent" != "$parent" ]; then
    recompute_epic "$old_parent"
  fi
  recompute_epic "$parent"
}

cmd_epic() {
  local key=${1:-} f done_count todo_count wishlist_count total
  require_epic "$key"
  read -r done_count todo_count wishlist_count total < <(epic_counts "$key")
  echo "$key [epic $done_count/$total] ($done_count/$total children done)"
  if [ "$total" -eq 0 ]; then
    echo "No children."
    return 0
  fi
  while IFS= read -r f; do
    printf '%-12s %-13s %s\n' "$(field "$f" key)" "$(field "$f" status)" "$(field "$f" title)"
  done < <(epic_children "$key")
}

cmd_set_worktree() {
  local key=${1:-} path=${2:-}
  require_ticket "$key"
  if [ -n "$path" ]; then
    # Accept the conventional absolute path for existing callers, but never persist it.
    [ "$path" = "$key" ] || [ "$path" = "$WT_ROOT/$key" ] \
      || die "worktree must be $key or WT_ROOT/$key (set WT_ROOT for a custom location)"
    path=$key
  fi
  set_field "$(file_for "$key")" worktree "$path"
  set_field "$(file_for "$key")" updated "$TODAY"
}

cmd_get_worktree() {
  local key=${1:-} stored
  require_ticket "$key"
  stored="$(field "$(file_for "$key")" worktree)"
  [ -n "$stored" ] || return 0
  [ "$stored" = "$key" ] || die "worktree field is not a portable key: $key"
  printf '%s/%s\n' "$WT_ROOT" "$key"
}

cmd_show() {
  require_ticket "${1:-}"
  cat "$(file_for "$1")"
}

# --- read-only presentation ---------------------------------------------------------
# Prefix a payload with the AC1 tuple. sort -n also handles cores beyond shell range.
key_order() { # key
  local digits=${1#"${TICKET_PREFIX}-"} suffix
  suffix=${digits##*[0-9]}; digits=${digits%"$suffix"}
  digits=${digits#"${digits%%[!0]*}"}; digits=${digits:-0}
  if [[ "$digits" =~ ^[0-9]+$ ]] && [ "${#digits}" -le 18 ]; then
    digits=$((10#$digits))
  fi
  printf '%s\t_%s\t%s\t' "$digits" "$suffix" "$1"
}

sort_keys() { # decorated key_order + payload records -> ordered payloads
  LC_ALL=C sort -t $'\t' -k1,1n -k2,2 -k3,3 | cut -f4-
}

# Normalize only representable non-negative decimal integers; invalid knobs are ignored.
presentation_int() {
  local value=$1
  [[ "$value" =~ ^[0-9]+$ ]] || return 1
  value=${value#"${value%%[!0]*}"}; value=${value:-0}
  [ "${#value}" -le 19 ] || return 1
  if [ "${#value}" -eq 19 ] && [[ "$value" > 9223372036854775807 ]]; then return 1; fi
  printf '%s' "$value"
}

is_pos_int() {
  local value
  value=$(presentation_int "$1") && [ "$value" != 0 ]
}

cell() { # text width; pad/truncate by terminal columns, before adding ANSI.
  CELL_TEXT=$1 LC_ALL=C awk -v w="$2" '
    function columns(cp) {
      if ((cp>=768 && cp<=879) || (cp>=8203 && cp<=8207) || cp==65279)
        return 0 # U+0300–036F, U+200B–200F, U+FEFF
      if ((cp>=4352 && cp<=4447) || (cp>=9001 && cp<=9002) ||
          (cp>=11904 && cp<=12350) || (cp>=12353 && cp<=13311) ||
          (cp>=13312 && cp<=19903) || (cp>=19968 && cp<=40959) ||
          (cp>=40960 && cp<=42191) || (cp>=44032 && cp<=55203) ||
          (cp>=63744 && cp<=64255) || (cp>=65040 && cp<=65049) ||
          (cp>=65072 && cp<=65135) || (cp>=65280 && cp<=65376) ||
          (cp>=65504 && cp<=65510) || (cp>=127744 && cp<=128591) ||
          (cp>=129280 && cp<=129535) || (cp>=131072 && cp<=262141))
        return 2 # East-Asian Wide/Fullwidth; individual emoji, not ZWJ clusters.
      return 1
    }
    BEGIN {
      if (w<=0) exit
      for (b=1; b<256; b++) ord[sprintf("%c", b)]=b
      text=ENVIRON["CELL_TEXT"]
      for (i=1; i<=length(text); i+=size) {
        b=ord[substr(text, i, 1)]; size=1; cp=b
        if (b>=194 && b<=223) { size=2; cp=b-192 }
        else if (b>=224 && b<=239) { size=3; cp=b-224 }
        else if (b>=240 && b<=244) { size=4; cp=b-240 }
        for (j=1; j<size; j++) {
          next_byte=ord[substr(text, i+j, 1)]
          if (next_byte<128 || next_byte>191) { size=1; cp=b; break }
          cp=cp*64+next_byte-128
        }
        glyph[++n]=substr(text, i, size)
        width[n]=columns(cp); total+=width[n]
      }
      # Reserve the ellipsis only if the complete text actually overflows.
      limit=w; if (total>w) limit=w-1
      for (i=1; i<=n; i++) {
        if (used+width[i]>limit) break
        printf "%s", glyph[i]; used+=width[i]
      }
      if (total>w) { printf "…"; used++ }
      for (i=used; i<w; i++) printf " "
    }
  '
}

detect_cols() { # Caller captures stdout_is_tty before command substitution pipes stdout.
  local cols=${TICKETS_COLS:-}
  if ! is_pos_int "$cols"; then
    cols=
    if [ "$stdout_is_tty" = yes ]; then
      # Captured tput stdout is a pipe on macOS; read the controlling TTY first.
      cols=$(stty size 2>/dev/null </dev/tty | awk '{print $2}') || cols=
      if ! is_pos_int "$cols"; then cols=$(tput cols 2>/dev/null) || cols=; fi
    fi
    if ! is_pos_int "$cols"; then cols=${COLUMNS:-}; fi
    if ! is_pos_int "$cols"; then cols=120; fi
  fi
  presentation_int "$cols"
}

detect_rows() {
  local rows
  rows=$(stty size 2>/dev/null </dev/tty | awk '{print $1}') || rows=
  if ! is_pos_int "$rows"; then rows=$(tput lines 2>/dev/null) || rows=; fi
  if ! is_pos_int "$rows"; then rows=${LINES:-24}; fi
  if ! is_pos_int "$rows"; then rows=24; fi
  presentation_int "$rows"
}

detect_colors() { # Assigns caller-scoped C_* variables via Bash dynamic scope.
  local use_color=no
  C_HDR= C_PROG= C_DONE= C_EPIC= C_DIM= C_RST=
  case "${TICKETS_COLOR:-}" in
    always) use_color=yes ;;
    never) ;;
    *) if [ "${NO_COLOR+x}" != x ] && [ -t 1 ]; then use_color=yes; fi ;;
  esac
  if [ "$use_color" = yes ]; then
    C_HDR=$'\e[1m'; C_PROG=$'\e[1m'; C_DONE=$'\e[2m'
    C_EPIC=$'\e[36m'; C_DIM=$'\e[2m'; C_RST=$'\e[0m'
  fi
}

board_cell() { # text style child blocked; compose visible prefixes BEFORE sizing.
  local text=$1 padded prefix=
  if [ "$3" = yes ]; then text="↳ $text"; fi
  if [ "$4" = yes ]; then prefix="! "; fi
  padded=$(cell "$prefix$text" "$W")
  if [ "$3" = yes ]; then
    padded="${prefix}${C_DIM}↳ ${C_RST}${2}${padded#"${prefix}↳ "}"
  fi
  printf '%s%s%s' "$2" "$padded" "$C_RST"
}

board_row() { # file; updates the caller's line and per-column group_epic in order.
  local f=$1 key status title kind parent style= child=no blocked=no unresolved
  key=$(field "$f" key); status=$(field "$f" status); title=$(field "$f" title)
  kind=$(field "$f" type); parent=$(field "$f" epic)
  line="$key  $title$(epic_suffix "$f" "$key")"
  if [ "$kind" = epic ]; then
    group_epic=$key
  elif [ -n "$parent" ] && [ "$parent" = "$group_epic" ]; then
    child=yes
  else
    group_epic=
  fi
  case "$status" in
    in-progress) style=$C_PROG ;;
    wishlist|done) style=$C_DONE ;;
  esac
  if [ "$kind" = epic ]; then style+=$C_EPIC; fi
  if [ "$status" != done ]; then
    unresolved=$(unresolved_blockers "$key")
    if [ -n "$unresolved" ]; then blocked=yes; fi
  fi
  line=$(board_cell "$line" "$style" "$child" "$blocked")
}

cmd_list() {
  local want=${1:-}
  [ -z "$want" ] || valid_status "$want" || die "bad status '$want' (one of: $STATUSES)"
  local f key status title unresolved suffix
  for f in "$TICKETS_DIR"/*.md; do
    [ -e "$f" ] || continue
    key="$(field "$f" key)"; status="$(field "$f" status)"; title="$(field "$f" title)"
    [ -z "$want" ] || [ "$status" = "$want" ] || continue
    suffix=
    if [ "$status" != done ]; then
      unresolved=$(unresolved_blockers "$key")
      if [ -n "$unresolved" ]; then suffix=" [blocked: $(blocker_description "$unresolved" names)]"; fi
    fi
    key_order "$key"
    printf '%-12s %-13s %s%s%s\n' "$key" "$status" "$title" "$(epic_suffix "$f" "$key")" "$suffix"
  done | sort_keys
}

cmd_ready() {
  local f key title unresolved ready= blocked= nr=0 nb=0
  for f in "$TICKETS_DIR"/*.md; do
    [ "$(field "$f" status)" = todo ] && [ "$(field "$f" type)" != epic ] || continue
    key=$(field "$f" key); title=$(field "$f" title)
    unresolved=$(unresolved_blockers "$key")
    if [ -n "$unresolved" ]; then
      nb=$((nb + 1))
      blocked+="$(key_order "$key")$key  $title  ← waiting on $(blocker_description "$unresolved" reasons)"$'\n'
    else
      nr=$((nr + 1))
      ready+="$(key_order "$key")$(printf '%-12s %-13s %s' "$key" todo "$title")"$'\n'
    fi
  done
  printf 'READY (%s)\n' "$nr"
  if [ -n "$ready" ]; then printf '%s' "$ready" | sort_keys; fi
  printf 'BLOCKED (%s)\n' "$nb"
  if [ -n "$blocked" ]; then printf '%s' "$blocked" | sort_keys; fi
}

cmd_board() {
  local cols W done_limit=10 arg stdout_is_tty=no
  if [ -t 1 ]; then stdout_is_tty=yes; fi
  cols=$(detect_cols)
  W=$(((cols - 9) / 4)); [ "$W" -ge 24 ] || W=24
  done_limit=$(presentation_int "${TICKETS_DONE_LIMIT:-}") || done_limit=10
  for arg in "$@"; do [ "$arg" != --all ] || done_limit=0; done

  local C_HDR= C_PROG= C_DONE= C_EPIC= C_DIM= C_RST=
  detect_colors

  local -a wish=() todo=() prog=() done_=()
  local f key status line prefix group_epic=
  for f in "$TICKETS_DIR"/*.md; do
    [ -e "$f" ] || continue
    key="$(field "$f" key)"; status="$(field "$f" status)"
    prefix=$(key_order "$key")
    case "$status" in
      wishlist)    wish+=("$prefix$f") ;;
      todo)        todo+=("$prefix$f") ;;
      in-progress) prog+=("$prefix$f") ;;
      done)        done_+=("$(field "$f" updated)"$'\t'"$prefix$f") ;;
      *) echo "tickets.sh: warn: $key has invalid status '$status'" >&2 ;;
    esac
  done
  local n0=${#wish[@]} n1=${#todo[@]} n2=${#prog[@]} n3=${#done_[@]} shown=0
  # Bash 3.2 + nounset needs guarded expansion for empty arrays.
  if [ "$n0" -gt 0 ]; then
    prefix=$(printf '%s\n' "${wish[@]}" | sort_keys); wish=()
    group_epic=
    while IFS= read -r f; do board_row "$f"; wish+=("$line"); done <<< "$prefix"
  fi
  if [ "$n1" -gt 0 ]; then
    prefix=$(printf '%s\n' "${todo[@]}" | sort_keys); todo=()
    group_epic=
    while IFS= read -r f; do board_row "$f"; todo+=("$line"); done <<< "$prefix"
  fi
  if [ "$n2" -gt 0 ]; then
    prefix=$(printf '%s\n' "${prog[@]}" | sort_keys); prog=()
    group_epic=
    while IFS= read -r f; do board_row "$f"; prog+=("$line"); done <<< "$prefix"
  fi
  if [ "$n3" -gt 0 ]; then
    prefix=$(printf '%s\n' "${done_[@]}" |
      LC_ALL=C sort -t $'\t' -k1,1r -k2,2nr -k3,3r -k4,4r | cut -f5-)
    done_=()
    group_epic=
    while IFS= read -r f; do
      if [ "$done_limit" -gt 0 ] && [ "$shown" -ge "$done_limit" ]; then break; fi
      board_row "$f"
      done_+=("$line"); shown=$((shown + 1))
    done <<< "$prefix"
    if [ "$shown" -lt "$n3" ]; then
      done_+=("$(board_cell "… +$((n3 - shown)) more (--all)" "$C_DIM" no no)")
    fi
  fi
  local max=$n0 height=${#done_[@]} i gutter underline
  [ "$n1" -le "$max" ] || max=$n1
  [ "$n2" -le "$max" ] || max=$n2; [ "$height" -le "$max" ] || max=$height
  gutter="${C_DIM} │ ${C_RST}"
  underline=$(printf '%*s' "$W" ''); underline=${underline// /-}
  printf '%s%s%s%s%s%s%s\n' "$(board_cell "WISHLIST ($n0)" "$C_HDR" no no)" "$gutter" \
    "$(board_cell "TODO ($n1)" "$C_HDR" no no)" "$gutter" \
    "$(board_cell "IN-PROGRESS ($n2)" "$C_HDR" no no)" "$gutter" "$(board_cell "DONE ($n3)" "$C_HDR" no no)"
  printf '%s%s%s%s%s%s%s\n' "$underline" "$gutter" "$underline" "$gutter" "$underline" "$gutter" "$underline"
  local blank; blank=$(cell '' "$W")
  for ((i=0; i<max; i++)); do
    printf '%s%s%s%s%s%s%s\n' "${wish[$i]:-$blank}" "$gutter" "${todo[$i]:-$blank}" "$gutter" "${prog[$i]:-$blank}" "$gutter" "${done_[$i]:-$blank}"
  done
}

cmd_watch() {
  if ! { [ -t 0 ] && [ -t 1 ]; }; then
    echo 'tickets.sh: watch needs a terminal on stdin and stdout; showing board once' >&2
    cmd_board "$@"
    return 0
  fi

  local SAVED_STTY RESTORED=no pager_keys= interval=${TICKETS_INTERVAL:-2}
  SAVED_STTY=$(stty -g)
  _watch_restore() {
    local code=$?
    case "$code" in 130|143) code=0 ;; esac
    if [ "$RESTORED" = no ]; then
      RESTORED=yes
      stty "$SAVED_STTY" || true
      printf '\e[?25h\e[?1049l'
      if [ -n "$pager_keys" ]; then rm -f "$pager_keys"; fi
    fi
    exit "$code"
  }
  trap _watch_restore EXIT INT TERM
  printf '\e[?1049h\e[?25l'
  stty -echo -icanon min 0 time 1
  if ! is_pos_int "$interval"; then interval=2; fi
  interval=$(presentation_int "$interval")

  # less reads source bindings from LESSKEYIN. Other PAGERs retain their own controls.
  pager_keys=$(mktemp)
  cat > "$pager_keys" <<'EOF'
#command
q quit
h quit
\kl quit
\e[D quit
EOF

  local SEL_KEY= SEL_INDEX=0 OFFSET=0 rows=24 viewport=23
  local cols= W= done_limit=10
  local C_HDR= C_PROG= C_DONE= C_EPIC= C_DIM= C_RST=
  local -a WATCH_KEYS=() WATCH_ROWS=() WATCH_LINES=() WATCH_ROW_KEYS=()

  _watch_model() {
    local -a records=() done_records=()
    local f key status prefix group count shown line group_epic= i found=no
    WATCH_KEYS=(); WATCH_ROWS=(); WATCH_LINES=(); WATCH_ROW_KEYS=()
    for f in "$TICKETS_DIR"/*.md; do
      [ -e "$f" ] || continue
      key=$(field "$f" key); status=$(field "$f" status)
      prefix=$(key_order "$key")
      case "$status" in
        wishlist|todo|in-progress) records+=("$prefix$status"$'\t'"$f") ;;
        done) done_records+=("$(field "$f" updated)"$'\t'"$prefix$f") ;;
        *) echo "tickets.sh: warn: $key has invalid status '$status'" >&2 ;;
      esac
    done
    local ordered= done_ordered=
    if [ "${#records[@]}" -gt 0 ]; then
      ordered=$(printf '%s\n' "${records[@]}" | sort_keys)
    fi
    if [ "${#done_records[@]}" -gt 0 ]; then
      done_ordered=$(printf '%s\n' "${done_records[@]}" |
        LC_ALL=C sort -t $'\t' -k1,1r -k2,2nr -k3,3r -k4,4r | cut -f5-)
    fi
    for group in $STATUSES; do
      local -a files=()
      if [ "$group" = done ]; then
        if [ -n "$done_ordered" ]; then
          while IFS= read -r f; do files+=("$f"); done <<< "$done_ordered"
        fi
      elif [ -n "$ordered" ]; then
        while IFS=$'\t' read -r status f; do
          if [ "$status" = "$group" ]; then files+=("$f"); fi
        done <<< "$ordered"
      fi
      count=${#files[@]}; shown=0; group_epic=
      case "$group" in
        wishlist) line="WISHLIST ($count)" ;;
        todo) line="TODO ($count)" ;;
        in-progress) line="IN-PROGRESS ($count)" ;;
        done) line="DONE ($count)" ;;
      esac
      WATCH_LINES+=("$(board_cell "$line" "$C_HDR" no no)"); WATCH_ROW_KEYS+=("")
      if [ "$count" -eq 0 ]; then continue; fi
      for f in "${files[@]}"; do
        if [ "$group" = done ] && [ "$done_limit" -gt 0 ] && [ "$shown" -ge "$done_limit" ]; then
          break
        fi
        key=$(field "$f" key)
        WATCH_ROWS+=("${#WATCH_LINES[@]}"); WATCH_KEYS+=("$key")
        board_row "$f"
        WATCH_LINES+=("$line"); WATCH_ROW_KEYS+=("$key")
        shown=$((shown + 1))
      done
      if [ "$shown" -lt "$count" ]; then
        WATCH_LINES+=("$(board_cell "… +$((count - shown)) more" "$C_DIM" no no)")
        WATCH_ROW_KEYS+=("")
      fi
    done
    for ((i=0; i<${#WATCH_KEYS[@]}; i++)); do
      if [ "${WATCH_KEYS[$i]}" = "$SEL_KEY" ]; then SEL_INDEX=$i; found=yes; break; fi
    done
    if [ "$found" = no ]; then
      if [ "$SEL_INDEX" -ge "${#WATCH_KEYS[@]}" ]; then SEL_INDEX=$((${#WATCH_KEYS[@]} - 1)); fi
      if [ "$SEL_INDEX" -lt 0 ]; then SEL_INDEX=0; fi
      SEL_KEY=${WATCH_KEYS[$SEL_INDEX]:-}
    fi
  }

  _watch_move() {
    [ "${#WATCH_KEYS[@]}" -gt 0 ] || return 0
    case "$1" in
      up) if [ "$SEL_INDEX" -gt 0 ]; then SEL_INDEX=$((SEL_INDEX - 1)); fi ;;
      down) if [ "$SEL_INDEX" -lt "$((${#WATCH_KEYS[@]} - 1))" ]; then SEL_INDEX=$((SEL_INDEX + 1)); fi ;;
    esac
    SEL_KEY=${WATCH_KEYS[$SEL_INDEX]}
  }

  _watch_key() {
    case "$1" in
      $'\e[A'|k) action=up ;;
      $'\e[B'|j) action=down ;;
      $'\r'|$'\n'|''|$'\e[C'|l) action=open ;;
      q|$'\e'|$'\e[D'|h|$'\003') action=quit ;;
      r) action=refresh ;;
      *) action=none ;;
    esac
  }

  _watch_measure() {
    local arg stdout_is_tty=no
    if [ -t 1 ]; then stdout_is_tty=yes; fi
    cols=$(detect_cols)
    done_limit=$(presentation_int "${TICKETS_DONE_LIMIT:-}") || done_limit=10
    for arg in "$@"; do [ "$arg" != --all ] || done_limit=0; done

    detect_colors

    # The same detected terminal width now serves one flat column.
    W=$cols
    rows=$(detect_rows)
    viewport=$((rows - 1)); [ "$viewport" -gt 0 ] || viewport=1
  }

  _watch_render() {
    local selected=${WATCH_ROWS[$SEL_INDEX]:-0} max_offset i line style frame=$'\e[H'
    max_offset=$((${#WATCH_LINES[@]} - viewport))
    [ "$max_offset" -ge 0 ] || max_offset=0
    [ "$OFFSET" -le "$max_offset" ] || OFFSET=$max_offset
    [ "$selected" -ge "$OFFSET" ] || OFFSET=$selected
    if [ "$selected" -ge "$((OFFSET + viewport))" ]; then OFFSET=$((selected - viewport + 1)); fi
    for ((i=OFFSET; i<OFFSET+viewport; i++)); do
      line=${WATCH_LINES[$i]:-}
      if [ -n "$SEL_KEY" ] && [ "${WATCH_ROW_KEYS[$i]:-}" = "$SEL_KEY" ]; then
        # Remove board style resets before wrapping the entire already-padded row.
        for style in "$C_HDR" "$C_PROG" "$C_DONE" "$C_EPIC" "$C_DIM" "$C_RST"; do
          if [ -n "$style" ]; then line=${line//"$style"/}; fi
        done
        line=$'\e[7m'"$line"$'\e[0m'
      fi
      frame+="$line"$'\e[K\r\n'
    done
    frame+="$(cell "${interval}s | $(date +%H:%M:%S) | ↑↓/jk move  Enter/→/l open  r refresh  q/Esc/←/h quit" "$W")"$'\e[K\e[J'
    printf '%s' "$frame"
  }

  _watch_refresh() {
    _watch_measure "$@"
    _watch_model
    _watch_render
  }

  local key suffix action
  _watch_refresh "$@"
  while :; do
    if IFS= read -rsn1 -t "$interval" key; then
      if [ "$key" = $'\e' ]; then
        suffix=
        # Bash 3.2 needs an integer timeout: VTIME alone does not bound read -n
        # when no continuation arrives. A lone ESC quits after at most one second.
        IFS= read -rsn2 -t 1 suffix || true
        key+=$suffix
      fi
      _watch_key "$key"
      case "$action" in
        quit) exit 0 ;;
        up|down) _watch_move "$action"; _watch_render ;;
        refresh) _watch_refresh "$@" ;;
        open)
          if [ -n "$SEL_KEY" ]; then
            stty "$SAVED_STTY"
            printf '\e[?25h'
            # A removed ticket or a failed/early-exiting pager returns to the live board.
            cmd_show "$SEL_KEY" | LESSKEYIN="$pager_keys" ${PAGER:-less -R} || true
            stty -echo -icanon min 0 time 1
            printf '\e[?25l'
            _watch_refresh "$@"
          else
            _watch_render
          fi ;;
        *) _watch_render ;;
      esac
    else
      _watch_refresh "$@"
    fi
  done
}

usage() {
  { sed -n '3,21p' "$0" | sed 's/^# \{0,1\}//'
    cat <<'EOF'
  tickets.sh new-epic <TICKET> "title"  create an epic (status derived from children)
  tickets.sh new <TICKET> "title" --epic PARENT  create a child of an epic
  tickets.sh set-epic CHILD PARENT       attach/re-parent a task to an epic
  tickets.sh epic <TICKET>               list children and done/total progress
  tickets.sh get-worktree <TICKET>       resolve the stored key using WT_ROOT
EOF
  } | sed "s/<TICKET>/${TICKET_PREFIX}-N/g"
  exit "${1:-0}"
}

# --- dispatch ----------------------------------------------------------------------
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then return 0; fi
cmd=${1:-board}; if [ "$#" -eq 0 ] && [ -t 1 ]; then cmd=watch; fi; shift || true
case "$cmd" in
  board)         cmd_board "$@" ;;
  watch)         cmd_watch "$@" ;;
  list)          cmd_list "$@" ;;
  ready)         cmd_ready "$@" ;;
  block|unblock) cmd_dependencies "$cmd" "$@" ;;
  show)          cmd_show "$@" ;;
  new)           cmd_new "$@" ;;
  new-epic)      cmd_new_epic "$@" ;;
  set-epic)      cmd_set_epic "$@" ;;
  epic)          cmd_epic "$@" ;;
  mv)            cmd_mv "$@" ;;
  close)         cmd_close "$@" ;;
  set-worktree)  cmd_set_worktree "$@" ;;
  get-worktree)  cmd_get_worktree "$@" ;;
  -h|--help|help) usage 0 ;;
  *) echo "tickets.sh: unknown command '$cmd'" >&2; usage 1 ;;
esac
