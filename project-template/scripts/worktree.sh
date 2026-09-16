#!/usr/bin/env bash
# One git worktree per ticket (CLAUDE.md §Version control).
# Usage: worktree.sh new <TICKET> | land <TICKET> | rm <TICKET> | list
set -euo pipefail

MAIN_BRANCH="${MAIN_BRANCH:-main}"   # BOOTSTRAP: set your default branch
TICKET_PREFIX="${TICKET_PREFIX:-TICKET}"   # BOOTSTRAP: your ticket-key prefix (e.g. AB, PROJ)
REPO_ROOT="$(git rev-parse --show-toplevel)"
# Worktrees live beside the PRIMARY checkout, even when invoked from a linked worktree.
GIT_COMMON="$(cd "$(git rev-parse --git-common-dir)" && pwd)"
MAIN_ROOT="$(dirname "$GIT_COMMON")"
WT_ROOT="${WT_ROOT:-$(dirname "$MAIN_ROOT")/$(basename "$MAIN_ROOT")-worktrees}"
[[ "$WT_ROOT" = /* ]] || WT_ROOT="$MAIN_ROOT/$WT_ROOT"
export WT_ROOT
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TICKETS_SH="$SCRIPT_DIR/tickets.sh"

cmd="${1:-}"; ticket="${2:-}"

case "$cmd" in
  new)
    [ -n "$ticket" ] || { echo "usage: worktree.sh new <TICKET>" >&2; exit 1; }
    mkdir -p "$WT_ROOT"
    git -C "$REPO_ROOT" worktree add -b "$ticket" "$WT_ROOT/$ticket" "$MAIN_BRANCH"
    # Ticket board (optional): mark an existing ticket in-progress. Do NOT auto-create with a
    # junk title — tickets.sh requires a descriptive title (T2); create the ticket first.
    if [ -x "$TICKETS_SH" ]; then
      if "$TICKETS_SH" show "$ticket" >/dev/null 2>&1; then
        "$TICKETS_SH" mv "$ticket" in-progress >/dev/null
        "$TICKETS_SH" set-worktree "$ticket" "$ticket" >/dev/null   # portable key; path derives from WT_ROOT
        echo "ticket $ticket -> in-progress" >&2
      else
        echo "note: no board ticket $ticket yet — create it with: scripts/tickets.sh new $ticket \"<title>\"" >&2
      fi
    fi
    echo "$WT_ROOT/$ticket"
    ;;
  land)
    [[ "$ticket" =~ ^${TICKET_PREFIX}-[0-9]+[a-z]*$ ]] || { echo "usage: worktree.sh land <TICKET>" >&2; exit 1; }
    # tickets.sh shares the primary checkout's board, even when invoked in a linked worktree.
    worktrees="$(git -C "$REPO_ROOT" worktree list --porcelain)"
    MAIN_ROOT="$(printf '%s\n' "$worktrees" | sed -n '1s/^worktree //p')"
    ticket_worktree="$(printf '%s\n' "$worktrees" | awk -v branch="refs/heads/$ticket" '
      /^worktree / { path = substr($0, 10) }
      $0 == "branch " branch { print path }
    ')"
    if [ ! -f "$MAIN_ROOT/tickets/$ticket.md" ] || [ ! -d "$ticket_worktree" ] ||
       ! git -C "$REPO_ROOT" show-ref --verify --quiet "refs/heads/$ticket"; then
      echo "unknown ticket/worktree: $ticket" >&2; exit 1
    fi
    if [ "$(git -C "$MAIN_ROOT" symbolic-ref --quiet --short HEAD || true)" != "$MAIN_BRANCH" ]; then
      echo "main working tree must be on $MAIN_BRANCH before landing $ticket" >&2; exit 1
    fi
    # A plain commit includes the existing index: refuse unrelated staged work before pushing.
    if ! git -C "$MAIN_ROOT" diff --cached --quiet -- . ':!tickets/*.md'; then
      echo "unstage non-tickets/*.md paths on the main working tree before landing $ticket" >&2; exit 1
    fi
    git -C "$MAIN_ROOT" fetch origin >&2
    branch_ahead="$(git -C "$MAIN_ROOT" rev-list --count "origin/$MAIN_BRANCH..$ticket")"
    main_ahead="$(git -C "$MAIN_ROOT" rev-list --count "origin/$MAIN_BRANCH..$MAIN_BRANCH")"
    if [ "$branch_ahead" -gt 0 ]; then
      if ! git -C "$MAIN_ROOT" merge-base --is-ancestor "origin/$MAIN_BRANCH" "$ticket"; then
        echo "rebase $ticket onto origin/$MAIN_BRANCH first" >&2; exit 1
      fi
      # On retry, main may already contain the feature plus an unpushed board commit.
      if ! git -C "$MAIN_ROOT" merge --ff-only "$ticket" >&2; then
        echo "local $MAIN_BRANCH cannot fast-forward to $ticket; preserve pending board commits and reconcile local $MAIN_BRANCH before re-running 'worktree.sh land $ticket'" >&2
        exit 1
      fi
    fi
    git -C "$MAIN_ROOT" add -- 'tickets/*.md'
    if git -C "$MAIN_ROOT" diff --cached --quiet; then
      if [ "$branch_ahead" -eq 0 ] && [ "$main_ahead" -eq 0 ]; then
        echo "$ticket has no commits ahead of origin/$MAIN_BRANCH and no pending board changes; nothing to land" >&2
        exit 1
      fi
      echo "board already current" >&2
    else
      if ! git -C "$MAIN_ROOT" commit -m "$ticket: board sync (land)" >&2; then
        echo "board commit failed; resolve the commit error and re-run 'worktree.sh land $ticket' to retry; nothing was pushed" >&2
        exit 1
      fi
    fi
    # One ref update carries the feature and board together, or leaves both local for retry.
    if ! git -C "$MAIN_ROOT" push origin "$MAIN_BRANCH" >&2; then
      echo "land push failed; pending commits remain on local $MAIN_BRANCH. For a transient failure, re-run 'worktree.sh land $ticket' to retry" >&2
      echo "if origin/$MAIN_BRANCH moved, fetch origin, rebase $ticket onto origin/$MAIN_BRANCH, and reconcile local $MAIN_BRANCH preserving pending board commits before re-running 'worktree.sh land $ticket'" >&2
      exit 1
    fi
    if [ "$main_ahead" -gt 0 ]; then
      echo "recovered / pushed pending commit(s) from local $MAIN_BRANCH" >&2
    fi
    echo "after G4/done, run 'scripts/worktree.sh rm $ticket' to remove the worktree" >&2
    echo "$ticket_worktree"
    ;;
  rm)
    [ -n "$ticket" ] || { echo "usage: worktree.sh rm <TICKET>" >&2; exit 1; }
    git -C "$REPO_ROOT" worktree remove "$WT_ROOT/$ticket"
    git -C "$REPO_ROOT" branch -d "$ticket" || echo "branch $ticket not fully merged — delete manually with -D if intended" >&2
    # Removal is not "done" (done = pushed to main after G4 with the suite green). Leave status; just remind.
    if [ -x "$TICKETS_SH" ]; then
      echo "note: ticket $ticket status unchanged — run 'scripts/tickets.sh close $ticket' when it's truly done" >&2
    fi
    ;;
  list)
    git -C "$REPO_ROOT" worktree list
    ;;
  *)
    echo "usage: worktree.sh new <TICKET> | land <TICKET> | rm <TICKET> | list" >&2; exit 1
    ;;
esac
