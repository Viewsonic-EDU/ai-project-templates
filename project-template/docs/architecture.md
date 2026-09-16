# Architecture

Living map of the codebase. Updated in the SAME change as any file add/move/rename,
responsibility shift, shared pattern introduction, or invariant change (rule H2).

## File map

<!-- BOOTSTRAP: fill as the project takes shape. One line per top-level module/folder. -->

| Path | Responsibility |
|------|----------------|
| | |

## Layers / data flow

<!-- A small diagram or bullet flow: UI → logic → data → persistence, or your stack's
equivalent. Keep it current — this is the first thing a fresh worker reads. -->

## Invariants

Constraints the code must never violate (threading model, ownership rules, "X is the only
writer of Y", …). A worker that must break one STOPS and escalates (D1).

- …

---

Change-log:
- ported from a mature downstream project (2026-09-17): **G0 context-budget slice sizing.**
  New gate G0 (CLAUDE.md gate table, S4): before `worktree.sh new` the orchestrator sizes the
  ticket (`docs/slice-sizing.md` — weighted feature table × calibration factor, 0.25 steps,
  raw > 1.0 → split proposal, < 0.25 → merge) and reports its own measured context use
  (`scripts/context-ledger.sh now`); the human picks same session vs a fresh one.
  `worktree.sh new`/`rm` call `context-ledger.sh start`/`record` best-effort; `record`
  appends to `docs/context-ledger.md`, which the next `land` commits with the board;
  `calibrate` gives the median actual/predicted of the last five rows. `tickets.sh` gains
  `set-size` and a `size:` frontmatter field shown as `[N]` on board/list. Spec template
  gains `## Size estimate & slice split`. ≥3-slice splits get one read-only cross-family
  batch check (`scripts/batch-split-check-template.md`, rubric R1–R4, VETO on circular
  `blocked_by` / vacuous AC, human-adjudicated). The ledger finds the orchestrator's session
  through `.claude/hooks/session-events.sh` (new, registered for SessionStart +
  UserPromptSubmit) — the downstream project relied on a machine-global hook; the template
  ships its own so it stays self-contained (R2). Genericized: `TICKET_PREFIX` in the ledger
  script, the four extra-cost feature rows renamed (weights kept), the gates/commands
  vocabulary rebuilt from this template's scripts, the downstream backlog section dropped,
  the ledger table empty. Tests: `tests/test_context_ledger.py` (fixture process API + real
  ancestor walk, doc arithmetic/link contracts) with `tests/fixtures/context_ledger` (scrubbed
  transcript records) and `tests/fixtures/slice_split`; tickets/worktree/width suites
  extended. Suites green here (tickets 336, worktree 35, ledger 106, width regression);
  `context-ledger.sh now` verified live from a real Claude session.
- ported from a mature downstream project (2026-09-17): **ticket dependencies + minor-clean
  before G4.** `scripts/tickets.sh` gains `block`/`unblock`/`ready`, a close gate (a ticket
  with unresolved blockers needs `close --force`), exhaustive cycle checks, fail-closed
  malformed `blocked_by:` handling and width-safe board/watch/list markers; ticket files carry
  `blocked_by: []`. Also adopted the portable worktree key (`set-worktree` stores the key,
  `get-worktree` derives the path from `WT_ROOT`, defaulting beside the PRIMARY checkout even
  from a linked worktree) — the template had skipped it. `worktree.sh` resolves `WT_ROOT` the
  same way; it still refuses to auto-create a board ticket (title required, T2). Help output
  prints the configured `TICKET_PREFIX`. CLAUDE.md T5 + `docs/orchestration.md` §5.6: a
  review stop that still carries MINOR findings gets ONE Codex fix round + T8 rerun, no
  re-review, before G4. Tests: `tests/test_tickets.py` (77 cases, real CLI in disposable git
  repos, PTY frames) and `tests/test_worktree.py` (24 cases, local bare origins) shipped with
  the neutral prefix `SAMPLE`; the one downstream test that replayed that project's real
  board files was dropped. Suites green here (308 + 26 + width regression).
- ported from a mature downstream project (2026-09-17): `docs/orchestration.md` §7 "The G1
  brief" — G1 is a plain-text flow chart in chat, decisions marked at their step; the spec
  stays machine-facing; the S5 auditor cross-checks brief ↔ spec. Docs-only; CLAUDE.md S3
  gains one sentence; file map unchanged.
- 2026-09-16 (TICKET-1): fixed `scripts/tickets.sh` `cell()` — it padded/truncated by UTF-8
  code-point count, so CJK/full-width titles overflowed the column and wrapped, and the `watch`
  TUI's cursor-addressed redraw left scroll residue. Now pure-awk UTF-8 decode + terminal-width
  (wide=2 / combining=0 / else 1) truncation/padding to exactly W. Added
  `tests/tickets_width_test.*` (portable width regression). Verified port from a mature downstream project
  (cross-family reviewed + fuzzed there; suite green here). Board left intentionally empty
  (template hygiene) — key rides the branch/commit, no board ticket file committed.
- ported from a mature downstream project (2026-09): added `scripts/tickets.sh` (git-backed ticket board) and
  `worktree.sh` `new`/`land`/`rm` board integration; removed `scripts/codex-dev.sh` (the
  deprecated `codex exec` wrapper — dispatch is now MCP-only, `docs/orchestration.md` §5.2).
  See README layout for the scripts map.
- (template) created.
