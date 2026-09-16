# Global Agent Instructions (Codex)

> This file is symlinked from `~/.codex/AGENTS.md` and is synced across all of <OWNER>'s
> machines via the `~/claude-memory` git repo. Edit it there.

## Persistent Memory Protocol (shared with Claude)

<OWNER> runs both Codex and Claude. They **share one persistent memory store**. Each
project's memory lives in a per-project directory inside the shared repo:

```
~/claude-memory/memory/<PROJECT>/
```

`<PROJECT>` = the basename of the MAIN repository root, e.g. for
`~/Documents/code/my-app` it is `my-app`. Inside a linked git worktree
(`my-app-worktrees/TICKET-1/`) it is still `my-app`, never the worktree name — the sync
engine keys memory by the main clone, so this is the only key both agents share:

```bash
common="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"  # main repo's .git, even from a worktree
if [ -n "$common" ]; then basename "$(dirname "$common")"; else basename "$PWD"; fi
```

Claude reads/writes this same directory through a symlink, so anything you write here,
Claude sees — and vice versa.

### On starting work in a project
1. Read `~/claude-memory/memory/<PROJECT>/MEMORY.md` — the one-line index of what is known.
2. Open the specific memory files whose descriptions look relevant to the task.

### When you learn something worth remembering long-term
Write it into the shared store, following the **same format Claude uses** so both agents
stay consistent:

1. Create `~/claude-memory/memory/<PROJECT>/<short-kebab-slug>.md`:
   ```markdown
   ---
   name: <short-kebab-case-slug>
   description: <one-line summary — used to decide relevance during recall>
   metadata:
     type: user | feedback | project | reference
   ---

   <the fact. For feedback/project, add **Why:** and **How to apply:** lines.
    Link related memories with [[their-name]].>
   ```
2. Add a one-line pointer to `MEMORY.md`: `- [Title](file.md) — hook`.
3. If the directory or `MEMORY.md` does not exist yet, create them.

### What to save / not save
- **Save:** durable facts about <OWNER>, their preferences, project goals/constraints not in
  the code, external references (URLs/tickets), and guidance they have given.
- **Don't save:** things the repo already records (code structure, git history), or facts
  that only matter to the current conversation.
- Before adding, check for an existing file covering the same thing and update it instead
  of duplicating. Delete memories that turn out to be wrong.

> Note: unlike Claude, you have no automatic memory tool — following this protocol is how
> you participate in the shared memory. Do it deliberately at the start and end of work.
