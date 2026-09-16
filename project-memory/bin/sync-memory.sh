#!/usr/bin/env bash
# sync-memory.sh — Central agent-memory sync for Claude (and Codex via shared dir).
#
# Keeps every repo's Claude auto-memory in ONE git repo (this repo, cloned to ~/claude-memory by convention), by
# symlinking each project's ~/.claude/projects/<hash>/memory into memory/<repo>/.
# Keyed by repo NAME (machine-independent), so it works across machines/accounts
# whose absolute paths differ.
#
# Usage:
#   sync-memory.sh [--dry-run] [--session-start] [--stop] [--no-push] [--quiet]
#
#   --dry-run        show what would happen; changes no memory (still appends sync.log, takes the lock)
#   --session-start  pull + link only (no push)   [used by SessionStart hook]
#   --stop           full: adopt + commit + push  [used by Stop hook]
#   (no flag)        full manual sync
#
# Idempotent & safe: never overwrites conflicting memory (backs up + keeps both).
set -uo pipefail

# ---------- locate self ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MEM_DIR="$REPO_DIR/memory"
GLOBAL_DIR="$REPO_DIR/global"
LOG="$REPO_DIR/sync.log"
LOCKDIR="$REPO_DIR/.sync.lock.d"

# ---------- config ----------
CODE_ROOT="$HOME/Documents/code"
# shellcheck disable=SC1091
[ -f "$REPO_DIR/config.local.sh" ] && source "$REPO_DIR/config.local.sh"
PROJECTS="$HOME/.claude/projects"

DRY_RUN=0; DO_PUSH=1; DO_PULL=1; VERBOSE=1
for a in "$@"; do
  case "$a" in
    --dry-run)       DRY_RUN=1 ;;
    --session-start) DO_PUSH=0 ;;
    --stop)          DO_PUSH=1 ;;
    --no-push)       DO_PUSH=0 ;;
    --quiet)         VERBOSE=0 ;;
  esac
done

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { printf '%s %s\n' "$(ts)" "$*" >> "$LOG"; [ "$VERBOSE" = 1 ] && echo "$*"; return 0; }
run() { if [ "$DRY_RUN" = 1 ]; then echo "  [dry-run] $*"; else "$@"; fi; }

# ---------- lock (mkdir is atomic; macOS has no flock) ----------
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  # steal if stale (>10 min)
  if [ -d "$LOCKDIR" ]; then
    age=$(( $(date +%s) - $(stat -f %m "$LOCKDIR" 2>/dev/null || echo 0) ))
    if [ "$age" -gt 600 ]; then
      log "stale lock ($age s) — stealing"; rmdir "$LOCKDIR" 2>/dev/null; mkdir "$LOCKDIR" 2>/dev/null
    else
      log "another sync running — skip"; exit 0
    fi
  fi
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT

encode() { printf '%s' "$1" | sed 's/[/._]/-/g'; }

# A rebase/merge in progress, unmerged index entries, or conflict markers left by a failed
# autostash apply: any of these means a human must resolve before anything is committed.
conflicted() {
  local g; g="$(git -C "$REPO_DIR" rev-parse --git-dir 2>/dev/null)" || return 1
  case "$g" in /*) ;; *) g="$REPO_DIR/$g" ;; esac
  if [ -d "$g/rebase-merge" ] || [ -d "$g/rebase-apply" ] || [ -f "$g/MERGE_HEAD" ]; then return 0; fi
  if git -C "$REPO_DIR" diff --name-only --diff-filter=U 2>/dev/null | grep -q .; then return 0; fi
  if grep -rlE '^(<<<<<<< |>>>>>>> )' "$MEM_DIR" "$GLOBAL_DIR" 2>/dev/null | grep -q .; then return 0; fi
  return 1
}

log "=== sync start (dry_run=$DRY_RUN push=$DO_PUSH) code_root=$CODE_ROOT ==="

# ---------- pull latest ----------
if [ "$DO_PULL" = 1 ] && [ "$DRY_RUN" = 0 ] && git -C "$REPO_DIR" remote | grep -q .; then
  if git -C "$REPO_DIR" pull --rebase --autostash >>"$LOG" 2>&1; then log "pulled"; else log "pull failed (continuing)"; fi
  conflicted && log "CONFLICT after pull: unresolved merge in $REPO_DIR — resolve by hand (git status); nothing will be committed or pushed"
fi

run mkdir -p "$MEM_DIR"

# ---------- enumerate repos: top level + one level inside GROUPING folders ----------
# A top-level dir with no .git of its own (e.g. code/<group>/) is a grouping folder: its
# immediate children are scanned too, but ONLY real clones — ".git is a DIRECTORY" is the
# test, which excludes git worktrees (their .git is a FILE) so code/<x>-worktrees/ stays out.
# The central key is always the repo BASENAME, so memory follows the repo name across
# machines no matter how deep it sits.
code_enc="$(encode "$CODE_ROOT")"
repo_paths=""
for repo in "$CODE_ROOT"/*/; do
  [ -d "$repo" ] || continue
  repo_paths="${repo_paths}${repo%/}
"
  [ -e "${repo%/}/.git" ] && continue            # a repo itself — do not descend
  for sub in "$repo"*/; do
    [ -d "$sub" ] || continue
    [ -d "${sub%/}/.git" ] || continue           # real clone only (worktree .git is a file)
    repo_paths="${repo_paths}${sub%/}
"
  done
done

# ---------- scan repos ----------
# bash 3.2 (macOS default) has no associative arrays; track "hash<TAB>name<TAB>abs" lines.
seen_lines=""
adopted=0; linked=0; fixed=0; merged=0; collisions=0

while IFS= read -r abs <&3; do
  [ -n "$abs" ] || continue
  name="$(basename "$abs")"
  hash="$(encode "$abs")"
  target="$PROJECTS/$hash/memory"
  central="$MEM_DIR/$name"

  # collision: same encoded hash, or (now that nesting is allowed) same basename from a
  # different path — both would make two repos fight over one central memory dir.
  prev="$(printf '%s\n' "$seen_lines" | awk -F'\t' -v h="$hash" '$1==h{print $3; exit}')"
  if [ -n "$prev" ]; then
    log "COLLISION: '$abs' & '$prev' → same hash '$hash'; skipping '$abs'"
    collisions=$((collisions+1)); continue
  fi
  prev="$(printf '%s\n' "$seen_lines" | awk -F'\t' -v n="$name" '$2==n{print $3; exit}')"
  if [ -n "$prev" ]; then
    log "COLLISION: '$abs' & '$prev' → same repo name '$name'; skipping '$abs'"
    collisions=$((collisions+1)); continue
  fi
  seen_lines="${seen_lines}${hash}	${name}	${abs}
"

  # Before nesting was scanned here, a nested repo fell through to the SWEEP below and got
  # a PATH-derived central key (memory/<path-derived-key>). It is keyed by basename now, so
  # migrate that dir across once — guarded, so an existing basename central is never clobbered.
  case "$hash" in
    "$code_enc"-*) legacy="${hash#${code_enc}-}" ;;
    *)             legacy="$hash" ;;
  esac
  if [ "$legacy" != "$name" ] && [ -d "$MEM_DIR/$legacy" ]; then
    if [ -d "$central" ]; then
      log "WARN $name: both memory/$legacy and memory/$name exist — leaving legacy alone, merge by hand"
    else
      log "MIGRATE $name: central memory/$legacy → memory/$name (nested repo, now keyed by name)"
      run mv "$MEM_DIR/$legacy" "$central" || log "FAIL $name: migrate"
      [ "$DRY_RUN" = 1 ] && central="$MEM_DIR/$legacy"   # dry-run: classify against reality
    fi
  fi

  # classify target
  t_link=0; t_dir=0; t_absent=1
  if [ -L "$target" ]; then t_link=1; t_absent=0
  elif [ -d "$target" ]; then t_dir=1; t_absent=0; fi
  c_exists=0; [ -d "$central" ] && c_exists=1

  # nothing anywhere → skip
  [ "$t_absent" = 1 ] && [ "$c_exists" = 0 ] && continue

  # Case C: already a symlink → verify/repair
  if [ "$t_link" = 1 ]; then
    dest="$(readlink "$target")"
    if [ "$dest" != "$central" ]; then
      log "FIX  $name: relink $target → $central (was $dest)"
      if run rm "$target" && run ln -s "$central" "$target"; then fixed=$((fixed+1)); else log "FAIL $name: relink"; fi
    fi
    [ "$c_exists" = 0 ] && log "WARN $name: symlink points to missing central $central"
    continue
  fi

  # Case A: local real dir, no central → ADOPT (move into repo + symlink)
  if [ "$t_dir" = 1 ] && [ "$c_exists" = 0 ]; then
    log "ADOPT $name: move $target → $central + symlink"
    if run mv "$target" "$central" && run ln -s "$central" "$target"; then adopted=$((adopted+1)); else log "FAIL $name: adopt (memory left in place)"; fi
    continue
  fi

  # Case D: local real dir AND central exists → MERGE + backup, never clobber
  if [ "$t_dir" = 1 ] && [ "$c_exists" = 1 ]; then
    bak="$target.bak.$(date +%Y%m%d%H%M%S)"
    log "MERGE $name: local dir + central both exist → backup $bak, merge, symlink"
    if [ "$DRY_RUN" = 0 ]; then
      cp -R "$target" "$bak"
      for f in "$target"/*; do
        [ -e "$f" ] || continue
        b="$(basename "$f")"
        if [ ! -e "$central/$b" ]; then
          cp -R "$f" "$central/$b"; log "  + new: $b"
        elif ! diff -q "$f" "$central/$b" >/dev/null 2>&1; then
          keep="$central/${b%.*}.local-$(date +%s).${b##*.}"
          cp -R "$f" "$keep"; log "  ! conflict: $b kept as $(basename "$keep")"
        fi
      done
      rm -rf "$target"; ln -s "$central" "$target"
    else
      echo "  [dry-run] would backup+merge+symlink $name"
    fi
    merged=$((merged+1))
    continue
  fi

  # Case B: no local, central exists → LINK (this machine picks up synced memory)
  if [ "$t_absent" = 1 ] && [ "$c_exists" = 1 ]; then
    log "LINK $name: create $target → $central"
    if run mkdir -p "$(dirname "$target")" && run ln -s "$central" "$target"; then linked=$((linked+1)); else log "FAIL $name: link"; fi
    continue
  fi
done 3<<EOF
$repo_paths
EOF

# ---------- sweep: adopt leftover real memory dirs whose repo is NOT under code root ----------
# (orphans, e.g. a project that was deleted/renamed) — preserve them so nothing is lost.
# code_enc is computed above, before the scan loop.
if [ -d "$PROJECTS" ]; then
  for md in "$PROJECTS"/*/memory; do
    [ -d "$md" ] || continue
    [ -L "$md" ] && continue                       # already a symlink
    h="$(basename "$(dirname "$md")")"
    # skip if a repo under code root already owns this hash (handled in the loop above)
    if printf '%s\n' "$seen_lines" | awk -F'\t' -v x="$h" '$1==x{f=1} END{exit !f}'; then continue; fi
    case "$h" in
      "$code_enc"-*) key="${h#${code_enc}-}" ;;
      *)             key="$h" ;;
    esac
    central="$MEM_DIR/$key"
    if [ -d "$central" ]; then
      log "SWEEP skip '$key': central already exists — leaving $md"; continue
    fi
    log "SWEEP orphan '$key' (no repo under code root): move $md → $central + symlink"
    if run mv "$md" "$central" && run ln -s "$central" "$md"; then adopted=$((adopted+1)); else log "FAIL sweep '$key' (memory left in place)"; fi
  done
fi

# ---------- orphan centrals (repo not present on this machine) ----------
if [ -d "$MEM_DIR" ]; then
  for c in "$MEM_DIR"/*/; do
    [ -d "$c" ] || continue
    n="$(basename "$c")"
    # match against the names actually scanned — a nested repo is not at "$CODE_ROOT/$n"
    printf '%s\n' "$seen_lines" | awk -F'\t' -v n="$n" '$2==n{f=1} END{exit !f}' \
      || log "ORPHAN central memory/$n (repo not cloned here) — left untouched"
  done
fi

# ---------- global shared files (Claude constitution + Codex memory protocol + statusline) ----------
link_global() {
  local src="$1" dst="$2"
  [ -f "$src" ] || { log "GLOBAL src missing: $src"; return; }
  if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then return; fi
  if [ -e "$dst" ] && [ ! -L "$dst" ]; then
    local gb="$dst.bak.$(date +%Y%m%d%H%M%S)"
    log "GLOBAL backup existing $dst → $gb"; run mv "$dst" "$gb" || { log "FAIL global backup $dst"; return; }
  fi
  log "GLOBAL link $dst → $src"
  run mkdir -p "$(dirname "$dst")" && run rm -f "$dst" && run ln -s "$src" "$dst" || log "FAIL global link $dst"
}
link_global "$GLOBAL_DIR/CLAUDE.md"        "$HOME/.claude/CLAUDE.md"
link_global "$GLOBAL_DIR/codex-AGENTS.md"  "$HOME/.codex/AGENTS.md"
link_global "$GLOBAL_DIR/statusline.sh"    "$HOME/.claude/statusline.sh"

log "summary: adopted=$adopted linked=$linked fixed=$fixed merged=$merged collisions=$collisions"

# ---------- commit + push ----------
if [ "$DRY_RUN" = 0 ] && conflicted; then
  log "SKIP commit/push: unresolved merge in $REPO_DIR (see above)"
elif [ "$DRY_RUN" = 0 ]; then
  git -C "$REPO_DIR" add -A >>"$LOG" 2>&1
  if git -C "$REPO_DIR" diff --cached --quiet 2>/dev/null; then
    log "no changes to commit"
  else
    host="$(hostname -s 2>/dev/null || hostname)"
    git -C "$REPO_DIR" commit -m "sync from ${host} $(ts)" >>"$LOG" 2>&1 && log "committed"
  fi
  # Push whenever asked and there are unpushed commits (a prior --session-start may have
  # committed without pushing) — not only when THIS run made a commit.
  if [ "$DO_PUSH" = 1 ] && git -C "$REPO_DIR" remote | grep -q .; then
    if [ -n "$(git -C "$REPO_DIR" log --branches --not --remotes --oneline 2>/dev/null)" ]; then
      git -C "$REPO_DIR" pull --rebase --autostash >>"$LOG" 2>&1
      if conflicted; then
        log "CONFLICT before push: resolve by hand in $REPO_DIR; not pushing"
      else
        git -C "$REPO_DIR" push >>"$LOG" 2>&1 && log "pushed" || log "push FAILED (see log)"
      fi
    fi
  fi
fi
log "=== sync done ==="
exit 0
