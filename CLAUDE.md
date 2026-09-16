# CLAUDE.md — ai-project-templates

> This repo holds **templates**, not a project. Its own working rules are few; the full
> process constitution lives INSIDE `project-template/CLAUDE.md` and applies to projects
> instantiated from it, not to editing this repo.

## What is here

- `project-template/` — the per-project process constitution: spec-driven, fully tested,
  dual-agent (Claude orchestrates, Codex implements), four human gates.
- `project-memory/` — the one-per-person cross-repo agent-memory hub (Claude + Codex).
- `scripts/new-project.sh <template> <dest>` — exports one template out of this monorepo.
- `README.md` — the concept and purpose of each template and how they relate.

## Rules for editing this repo

- **R1. Templates stay generic.** Real values never land here: no owner names, no internal
  project, product or ticket names, no real machine paths (generic examples such as
  `/Users/alice/code/app` are fine), no credentials. Configuration points are
  `<SLOT>` markers (`<PROJECT_NAME>`, `<OWNER>`, `<CODE_ROOT>`, …) listed in each template's
  README. This repo is public.
- **R2. Each template is self-contained.** A template must work when exported alone by
  `scripts/new-project.sh`; it may reference the other template in prose, never by relative
  path in code or config.
- **R3. Lessons flow upstream.** A process improvement proven in a downstream project is
  ported here in a dedicated change; record it as "ported from a mature downstream project
  (<date>)" without naming the project — in `project-template/docs/architecture.md`'s
  change-log, or the `## Change-log` at the end of `project-memory/README.md`.
- **R4. Scripts are verified before commit.** A changed shell script is run at least in
  `--dry-run` or against a throwaway `HOME` / directory; the commit message says what was run.
- **R5. Nothing machine-local is tracked.** `.claude/settings.local.json`, sync logs, and
  per-machine config stay in `.gitignore`.
