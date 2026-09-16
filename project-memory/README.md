# project-memory — a shared agent-memory hub

One PRIVATE git repo that centralizes **Claude Code auto-memory** for every project on your
machines, so it follows you across machines and accounts. **Codex** shares the same store
through its global memory protocol, so both agents remember the same things.

> **Scope:** this is a **one-per-person (or one-per-team) template**, not a per-project one.
> You instantiate it ONCE, push it to your own private repo, and every project you work on
> is adopted automatically. It pairs with `../project-template` (the per-project process
> constitution) but works on its own.

## The problem it solves

Claude Code has built-in persistent memory: it writes one-fact-per-file markdown into

```
~/.claude/projects/<hash>/memory/
```

where `<hash>` is the project's **absolute path** with `/`, `.`, `_` replaced by `-`. That
path is bound to one machine and one home directory, `~/.claude` is not a git repo, and a
second agent (Codex) has no access to it at all. So memory does not travel and is not shared.

## The idea

Move each project's memory folder into this repo **keyed by repo basename** (machine
independent) and symlink it back to wherever Claude expects it on each machine.

```
                 ┌────────────────────────────────────────────┐
                 │  ~/claude-memory  (PRIVATE git repo)        │
                 │  memory/<repo-a>/       ← real files        │
                 │  memory/<repo-b>/                           │
                 │  global/CLAUDE.md       ← global constitution│
                 │  global/codex-AGENTS.md ← Codex protocol     │
                 │  global/statusline.sh   ← optional           │
                 └────────────────────┬───────────────────────┘
                                      │ git push / pull
             ┌────────────────────────┼────────────────────────┐
        machine A                machine B                machine C
  ~/.claude/projects/<hashA>/memory ─symlink─► memory/<repo-a> ◄─symlink─ <hashC>/memory
```

Because the key is the **name**, a repo cloned to `/Users/alice/code/app` and
`/home/bob/work/app` share one memory folder.

## Layout

```
memory/<repo>/          real memory files per project (the source of truth) — starts empty
global/CLAUDE.md        global Claude constitution  → symlinked to ~/.claude/CLAUDE.md
global/codex-AGENTS.md  Codex memory protocol       → symlinked to ~/.codex/AGENTS.md
global/statusline.sh    Claude Code status line     → symlinked to ~/.claude/statusline.sh (optional)
bin/sync-memory.sh      the sync engine (idempotent)
bootstrap.sh            one-time per-machine setup
config.local.sh         per-machine CODE_ROOT (gitignored, created by bootstrap)
sync.log                every action, appended (gitignored)
```

## Instantiate (once per person)

1. Copy this directory out of the templates repo:
   ```bash
   ../scripts/new-project.sh project-memory ~/claude-memory
   ```
   The clone location `~/claude-memory` is a **convention baked into `global/*.md`** (Codex
   has no other way to find the store). If you clone elsewhere, update those two files.
2. Fill the `<SLOT>` markers in `global/CLAUDE.md` and `global/codex-AGENTS.md`:
   `<OWNER>` (your name), `<CODE_ROOT>` (where your repos live), and `<repo>` in the
   "New machine setup" line of `global/CLAUDE.md` (your private remote URL).
3. Create a **PRIVATE** remote — memory contains personal and project-sensitive facts — and
   push:
   ```bash
   cd ~/claude-memory && git init -b main && git add -A && git commit -m "init memory hub"
   git remote add origin <your-private-repo> && git push -u origin main
   ```
4. Run the bootstrap (see below). If your repos are not under `~/Documents/code`, pass the
   root on the first run; it is written to `config.local.sh` and read by every later sync:
   ```bash
   CODE_ROOT=~/work ./bootstrap.sh
   ```
   Done — every project under `CODE_ROOT` is now synced.

## Each additional machine

Prerequisites: Claude Code, `git` with a global `user.name` / `user.email` (the Stop hook
commits as you), `python3` (present on macOS), `jq` only for the status line.

```bash
git clone <your-private-repo> ~/claude-memory
cd ~/claude-memory && ./bootstrap.sh
```

`bootstrap.sh` is non-destructive and re-runnable. It:
1. writes `config.local.sh` with this machine's `CODE_ROOT` (default `~/Documents/code`);
2. merges two hooks **and the `statusLine` key** into `~/.claude/settings.json`, after backing
   the file up; a file that is not valid JSON aborts the bootstrap untouched. An existing
   custom status line is kept under `statusLineBackup`, never clobbered. The hook commands
   embed this machine's absolute path to the clone (re-run bootstrap if you move it); the
   status-line command uses `"$HOME"` so it is username independent;
3. runs the first sync, creating every symlink and placing existing memory.

## Everyday use — automatic

| Hook | Runs | Effect |
|---|---|---|
| **SessionStart** | `sync-memory.sh --session-start` | pull + repair symlinks, no push (fast start) |
| **Stop** | `sync-memory.sh --stop` (background) | adopt new memory + commit + push |

Manual: `bash ~/claude-memory/bin/sync-memory.sh`. Flags: `--dry-run` (touches no memory or
symlink; still appends to `sync.log` and takes the lock), `--no-push`, `--quiet`.

**The one rule:** put projects under `CODE_ROOT`. Anything there is adopted on the next sync.
No per-project setup.

## Which repos are scanned

- Every top-level directory under `CODE_ROOT` is a candidate.
- A top-level directory with **no `.git` of its own** is a grouping folder: its immediate
  children are scanned too, but only real clones where `.git` is a **directory**. This
  deliberately excludes git worktrees (their `.git` is a file), so `<x>-worktrees/` stays out.
- The central key is always the **basename**, so nesting depth does not matter.

## What one sync does per project (idempotent)

| Local `~/.claude/.../memory` | Central `memory/<repo>` | Action |
|---|---|---|
| real dir with files | missing | **ADOPT**: move into repo, replace with symlink |
| missing | exists | **LINK**: create the symlink (machine picks up synced memory) |
| already a symlink | exists | verify / repair the link target |
| real dir with files | exists | **MERGE**: back up local, copy new files in, keep conflicts as `*.local-<ts>`, then symlink |
| missing | exists, repo not cloned here | leave untouched, log `ORPHAN` |

Two extra passes: **SWEEP** adopts leftover real memory dirs whose repo is no longer under
`CODE_ROOT` (deleted or renamed projects) so nothing is lost; **orphan centrals** are only
logged.

## Safety

- Never overwrites conflicting memory: backs up (`*.bak.<timestamp>`) and keeps both copies.
- **Collision detection**: two repos whose names encode to the same hash (`foo_bar` vs
  `foo-bar`) or two nested repos with the same basename are detected; the second is skipped
  with a warning, never silently merged.
- A `mkdir` lock prevents concurrent runs (macOS has no `flock`); stale locks over 10 min are
  stolen.
- Every run does `pull --rebase --autostash` before pushing. Memory is one-fact-per-file, so
  real conflicts are rare; `MEMORY.md` (the index) is the likely conflict point. If a pull or
  autostash leaves a conflict, the sync logs `CONFLICT` and **refuses to commit or push**
  until you resolve it by hand in `~/claude-memory` (`git status`), so conflict markers never
  propagate to other machines.

## Memory file format (shared by Claude and Codex)

One file, one fact, with frontmatter:

```markdown
---
name: <short-kebab-case-slug>
description: <one-line summary — used to decide relevance during recall>
metadata:
  type: user | feedback | project | reference
---

<the fact. For feedback/project add **Why:** and **How to apply:** lines.
 Link related memories with [[their-name]].>
```

Then add one line to that project's `MEMORY.md`: `- [Title](file.md) — hook`. `MEMORY.md`
is the index loaded every session: one line per memory, never the content.

Types: **user** (who the person is, preferences) · **feedback** (guidance given, with the
why) · **project** (goals/constraints not derivable from code; absolute dates) ·
**reference** (URLs, dashboards, tickets). Do not save what the repo already records.

## Codex integration

`~/.codex/AGENTS.md` is symlinked to `global/codex-AGENTS.md`, which spells out the protocol:
same directory (`memory/<PROJECT>/`, `<PROJECT>` = basename of the MAIN repository root,
resolved through `git rev-parse --git-common-dir` so a linked worktree maps to its main clone,
not to the worktree name), same file format. Codex has **no automatic memory tool**, so the
protocol tells it to read `MEMORY.md` at the start of work and write deliberately at the end.

## Status line (optional)

`global/statusline.sh` is synced the same way to `~/.claude/statusline.sh`. It shows
worktree-vs-main (main checkout in red), branch, model, reasoning effort, context remaining,
5h/7d rate-limit remaining as bars, Codex quota, and the active Claude account. Requires
`jq`. The account field reads the machine's single global login, not per-session. Delete the
file and the `statusLine` merge in `bootstrap.sh` if you do not want it.

## Troubleshooting

- **Memory not syncing?** Run the sync by hand and read `sync.log`. Confirm the project
  appears in the scan and `ls -la ~/.claude/projects/<hash>/memory` is a symlink into
  `memory/<repo>`.
- **A project is not picked up?** It must be under `CODE_ROOT`, or one level inside a
  grouping folder with no `.git`, and be a real clone. Worktrees are excluded on purpose.
- **`COLLISION` warning?** Two repos share a name or hash. Rename one or merge the central
  folders by hand.
- **`ORPHAN central`?** Normal: the store has memory for a repo this machine has not cloned.
- **Push failed?** Usually a `MEMORY.md` conflict: `cd ~/claude-memory && git status`, resolve,
  push.
- **Hooks not firing?** Check `hooks.SessionStart` / `hooks.Stop` in `~/.claude/settings.json`
  for the two `sync-memory.sh` commands. Re-run `./bootstrap.sh`.
- **New machine has a different username?** Works: the store is keyed by repo name, the
  status line uses `"$HOME"`, and the hook paths are generated per machine by `bootstrap.sh`.
- **Moved the clone?** Re-run `./bootstrap.sh`; the hook commands embed the clone's path.

## Handover checklist

- [ ] a **private** repo holds the memory
- [ ] it contains `bin/sync-memory.sh`, `bootstrap.sh`, `global/`, `.gitignore`
- [ ] every machine: clone + `./bootstrap.sh` once
- [ ] `~/.claude/settings.json` has the SessionStart / Stop hooks
- [ ] `config.local.sh` `CODE_ROOT` points where your projects live
- [ ] all projects whose memory should sync live under `CODE_ROOT`
- [ ] optional: `jq` installed, status line on; `~/.codex/AGENTS.md` symlinked if you use Codex

## Change-log

- 2026-09-16 — extracted as a template: `<OWNER>` / `<CODE_ROOT>` slots; sync refuses to
  commit or push an unresolved merge; `run()` no longer re-parses paths through `eval`;
  bootstrap backs up `settings.json` and aborts on invalid JSON; `CODE_ROOT` accepted on the
  first bootstrap; Codex memory key resolves a linked worktree to its main clone.
