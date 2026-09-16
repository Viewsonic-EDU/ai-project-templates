# Global Constitution — <OWNER>'s Agent Environment

> Symlinked from `~/.claude/CLAUDE.md`. Loaded into **every** Claude Code session on this
> machine, regardless of project. Synced across all machines via the `~/claude-memory` git
> repo. Edit it there. Changes here affect all projects — treat with the same care as a
> project constitution.

## Cross-Repo Memory Sync (Workflow: memory-sync)

All Claude auto-memory for every repo under `<CODE_ROOT>` (default `~/Documents/code`) is
centralized in a single git repo, **`~/claude-memory`** (a PRIVATE repo), and symlinked back
into each project so Claude reads/writes it transparently. This lets <OWNER> develop across
different machines and accounts with one shared memory.

**How it works**
- Central store: `~/claude-memory/memory/<repo-name>/` holds the real files.
- Each machine symlinks `~/.claude/projects/<hash>/memory` → the central folder.
- Keyed by **repo name** (not the path hash), so it works even when absolute paths differ
  between machines/accounts.
- `bin/sync-memory.sh` pulls, adopts new memory, repairs symlinks, commits, and pushes.
- Hooks: **SessionStart** pulls + links; **Stop** adopts + commits + pushes.

**The one rule for <OWNER>**
> Put new projects under `<CODE_ROOT>`. Anything there gets its memory auto-synced.
> New repos are adopted automatically on the next sync — nothing to configure per project.

**Shared with Codex**
- Codex participates in the same memory store via its global `~/.codex/AGENTS.md` protocol
  (also synced from this repo). Both agents point at `~/claude-memory/memory/<project>/`.

**New machine setup:** `git clone <repo> ~/claude-memory && cd ~/claude-memory && ./bootstrap.sh`

**Operational details & troubleshooting:** see `~/claude-memory/README.md`.

<!-- Add further machine-wide rules below. Anything project-specific belongs in that
     project's own CLAUDE.md, never here. -->
