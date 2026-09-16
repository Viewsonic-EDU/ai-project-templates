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
