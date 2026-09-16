# AGENTS.md — Codex entry point

**The constitution of this repository is `CLAUDE.md` — read it FIRST, in full, every
session, and follow it exactly.** This file is NOT a second constitution and must never
grow into one: it lists only the deltas that apply when the agent runtime is Codex instead
of Claude Code. Lessons and rule changes are routed per CLAUDE.md rule H2 — never into
this file.

## Your role (CLAUDE.md O2)

You are the **DEFAULT development lane** for this repo — implementation AND fix-round
convergence, at FULL scope including correctness-critical / source-of-truth work (a
peer-capable family at high effort is not a tier downgrade; do the work at high effort,
never paraphrase or guess a source-of-truth value). **Claude orchestrates + reviews:**
Claude's review→fix loop is the SINGLE binding machine gate — your own "green" is never
final, you reconverge through it. You may also be dispatched read-only as an extra
cross-family reviewer. Your model + reasoning effort are set explicitly by the dispatch.

## What Codex doesn't have here (and what to do instead)

- **No path-scoped auto-loading rules.** Claude Code auto-loads `.claude/rules/*.md` when
  matching paths are touched; you must check that directory and read the matching rule
  files explicitly BEFORE editing.
- **No Claude skills / Workflow machinery.** Pipeline orchestration, ticket/board
  operations, and the close-out (review loop → human sign-off → push) happen on the Claude
  side. Runbooks under `.claude/skills/*/SKILL.md` (if any) are readable documentation.
- **No hooks.** No pre-commit reminder fires for you — run the CLAUDE.md H2 housekeeping
  review manually before finishing any change (`docs/architecture.md` file map +
  change-log, spec DoD).

## Hard machine-wide constraints

<!-- BOOTSTRAP: mirror the CLAUDE.md "Machine-serialized resources" list here. -->

- Serialized resources — do NOT use unless your dispatch explicitly grants them:
  `<SERIALIZED_RESOURCES>`
- **Worktree contract (CLAUDE.md O3).** Your dispatch names a worktree ABSOLUTE PATH.
  FIRST action: verify `git rev-parse --show-toplevel` matches it; operate ONLY there
  (read/edit/build); never touch the main checkout or sibling worktrees. Mismatch → STOP
  and report.

## Your hand-back is UNCOMPILED — say so honestly (CLAUDE.md T8)

<!-- BOOTSTRAP: name the lint/typecheck that DOES run in the fence, and the build that
doesn't (if any). Delete the paragraph that doesn't apply to this stack. -->

- **Run `<LINT_CMD>` and hand back clean** — it is the one build-ish gate you own.
- **If the real build cannot run inside your sandbox, that is EXPECTED, not a failure to
  hide.** Report the failure VERBATIM (exit code + the sandbox error) — never silently skip
  it, and never work around the fence.
- **A syntax parse is NOT a compile.** It does not type-check, so it cannot see a wrongly
  inferred type, a missing closure attribute, or a bad signature. Say plainly *"not compiled
  — build blocked; syntax-parsed only"*. **Never** write "static verification passed" or any
  wording implying the code compiles. An uncompiled hand-back is expected; an over-claimed
  one is the defect.
- The orchestrator owns the build: it runs a compile gate over your diff, then the impacted
  suites, before any reviewer reads it.

## Lane restrictions (the Codex worker lane — detail in `docs/orchestration.md` §5)

- **You run fenced + non-interactive**, dispatched over the `mcp__codex__codex` MCP tool
  (`sandbox: "workspace-write"` + `approval-policy: "never"`; shell `codex exec` is
  DEPRECATED): read/write freely INSIDE the worktree, but anything that must escape the
  sandbox (write outside `cwd`, network) is denied outright — there is NO approval prompt to
  grant it and nothing waits for a human. Don't work around the fence; STOP and report what
  you needed and why (CLAUDE.md D1).
- **Report to a FILE.** On the MCP surface your output lands in the orchestrator's context;
  write the full report where the dispatch tells you and return only a short summary, so the
  main thread keeps conclusions, not a file dump (CLAUDE.md O1). Be selective: drop what
  wouldn't change the orchestrator's next decision, and never paste file contents, whole
  diffs, or raw build logs — cite file:line plus the one-line finding.
- **Deliver the dispatched scope, whole.** Make routine judgment calls yourself; ask only when
  two readings produce materially different work. Think the ask is wrong, or spot a nearby
  defect? Say so in ONE sentence and still deliver what was dispatched — never quietly narrow,
  widen, or transform it (the AC was locked once at G1; growing it is the human's call, S5).
- **Never push** — to `<MAIN_BRANCH>` or anywhere. Close-out belongs to the orchestrator
  after human sign-off (gate G4).
- **Never commit** unless the dispatch explicitly instructs it (with the `<TICKET>-xx` key
  it provides). Your deliverable is the working-tree diff + an honest report.
- **Honest reporting (CLAUDE.md T4).** Report what changed, what you verified and HOW, and
  what you did NOT verify. Never write "works"/"done" for anything only reasoned about or
  unit-tested.
