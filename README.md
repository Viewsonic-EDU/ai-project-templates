# ai-project-templates

Reference templates for running software projects with AI coding agents (Claude Code and
Codex) under a disciplined, human-gated process. Two templates, complementary but
independent:

| Template | Scope | Instantiate |
|---|---|---|
| [`project-template/`](project-template/) | **one per project** — the process constitution a codebase is developed under | once per new repo |
| [`project-memory/`](project-memory/) | **one per person or team** — the cross-repo memory hub the agents remember with | once, then every machine clones it |

```bash
git clone git@github.com:Viewsonic-EDU/ai-project-templates.git
cd ai-project-templates
scripts/new-project.sh project-template ~/Documents/code/my-app   # a new project
scripts/new-project.sh project-memory   ~/claude-memory           # your memory hub
```

`scripts/new-project.sh` exports one subdirectory with no git history; each template's own
README then walks through filling its `<SLOT>` markers.

## Why two templates

An AI agent working on a codebase needs two things a human engineer brings implicitly:

1. **A way of working** — what counts as done, when to stop and ask, who reviews whom, what
   never gets pushed without a human. That is per project, lives with the code, and must be
   the same for every agent and every session touching that code. → `project-template`.
2. **Memory** — who the user is, what they have corrected before, what a project's goals and
   constraints are beyond the code. That is per person, spans every project they work on, and
   must follow them across machines and accounts. → `project-memory`.

Mixing the two is the common mistake: process rules leak into memory and become
un-reviewable; memory leaks into the constitution and becomes stale. Keeping them apart
means a project's `CLAUDE.md` is reviewable in a pull request, and a person's memory is
private and portable.

## project-template — spec-driven · fully tested · dual-agent · human-gated

A skeleton for a new repository. Copy it, fill the `<SLOT>` markers, and the discipline is
in force from commit one. Four pillars:

- **Spec before code.** No implementation without an approved spec. A feature request
  yields a spec draft first; the human approves scope, architecture, and the slice split.
- **Complete testing.** Tests are written with the code and assert effects, not presence. A
  worker's hand-back is compiled and run by the orchestrator before any review. No red
  builds, no weakened tests.
- **Dual-agent, cross-family review.** Claude Code orchestrates and holds the binding
  review; Codex is the implementation lane. Whoever authored a diff, the *other* model
  family decides it is clean, because same-family reviewers share blind spots.
- **Exactly four human gates.** G1 spec approval · G2 mid-flight tradeoffs · G3 review-loop
  escalations · G4 sign-off before push. Everything else is automated and must not block on
  the human. The one exception is a change a mechanical check proves to be copy/docs only,
  which may push without G4.

What you get: `CLAUDE.md` (a constitution of one-line anchored rules), `AGENTS.md` for
Codex, `specs/_TEMPLATE.md`, `docs/` (testing strategy, orchestration, architecture,
decision log), a git-backed ticket board and worktree helper under `scripts/`, and a Codex
dispatch template. Start at [`project-template/README.md`](project-template/README.md).

## project-memory — a shared agent-memory hub

Claude Code writes persistent memory under `~/.claude/projects/<hash>/memory/`, where the
hash is the project's absolute path. It is bound to one machine, is not in git, and Codex
cannot see it. `project-memory` moves each project's memory into one **private** git repo
keyed by **repo name**, symlinks it back on every machine, and gives Codex the same store
through its global `AGENTS.md`. Two Claude Code hooks keep it synced: pull on session start,
commit and push on stop. The one rule for the user: put projects under one code root, and
they are adopted automatically.

What you get: the sync engine, a re-runnable per-machine bootstrap, a global Claude
constitution and Codex protocol with `<SLOT>` markers, and an optional status line. Start at
[`project-memory/README.md`](project-memory/README.md).

## How they fit together

- The memory hub's global `CLAUDE.md` is loaded into every session on the machine; a
  project's `CLAUDE.md` from `project-template` is loaded on top of it. Global holds only
  machine-wide facts (where memory lives); everything about *how to build* stays in the
  project.
- Both templates assume the same two agents and the same memory file format, so a Codex
  worker dispatched by the project process reads the same `MEMORY.md` Claude does.
- Neither requires the other. A team can adopt the process without the memory hub, or the
  memory hub without the process.

## Contributing lessons back

Templates improve by porting what a real project learned. When a downstream project changes
a rule, gate, or script and it holds up, port it here in a dedicated commit and note it in
that template's change-log (`project-template/docs/architecture.md`, or the end of
`project-memory/README.md`) without naming the project (this repo is public). Editing
rules for this repo are in [`CLAUDE.md`](CLAUDE.md).
