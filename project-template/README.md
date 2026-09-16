# Project Template — spec-driven · fully tested · dual-agent · human-gated

Starting skeleton for new projects. Copy it, fill the `<SLOT>` markers, and the working
discipline is in force from commit one. Four pillars:

1. **Spec-driven** — no implementation without an approved spec (`specs/_TEMPLATE.md`,
   golden rule S1–S5 in `CLAUDE.md`).
2. **Complete testing** — tests written with the code, effect-asserting, machine review
   loop before any human gate (`docs/testing-strategy.md`).
3. **Dual-agent (Claude + Codex)** — Claude Code orchestrates and holds the binding
   review; Codex is THE implementation lane (Claude does not author code inline) and an
   extra cross-family review finder, dispatched ONLY over the `mcp__codex__codex` MCP tool
   (shell `codex exec` is deprecated), with a dev-model fallback ladder
   (`docs/orchestration.md` §5.2/§5.2.1, `AGENTS.md`).
4. **Human gates** — exactly four, listed at the bottom of `CLAUDE.md`: G1 spec/AC
   approval · G2 mid-flight tradeoffs · G3 review-loop escalations · G4 final sign-off
   before push. Everything else is automated — including the machine gate that closes a
   ticket: the compile+suite convergence of a worker hand-back (T8). The push to the main
   branch (after G4, suite green) is the terminal state — there is no CI gate.

## Bootstrap checklist (do these in the new project)

1. Export this template into the new project location and init a repo there:
   ```bash
   scripts/new-project.sh project-template <dest>   # from the ai-project-templates clone
   cd <dest> && git init
   ```
2. **Register the codex MCP server — do this FIRST, it fails silently when missing:**
   ```bash
   claude mcp add codex -s user -- codex mcp-server   # user scope: all repos get it
   claude mcp list                                    # must show codex ✔ Connected
   ```
   Restart the session, then confirm `ToolSearch "select:mcp__codex__codex"` resolves.
   Pipelines degrade *gracefully* without it — they fall back to a same-family auditor and
   note it in a field nobody reads — so a missing server silently voids every cross-family
   guarantee (O4/S5). See `docs/orchestration.md` §5.1.
3. Search the repo for `<` slot markers and fill every one:
   - `CLAUDE.md`: `<PROJECT_NAME>`, `<BUILD_CMD>`, `<TEST_CMD>`, `<RUN_CMD>`,
     `<TICKET>` key convention (e.g. `AB-12`), `<MAIN_BRANCH>`,
     `<SERIALIZED_RESOURCES>` (things only one session may use at a time, if any).
   - `AGENTS.md`: same constraint slots.
   - `scripts/worktree.sh`: `MAIN_BRANCH` default.
   - Codex dev-lane model defaults to **`gpt-6-astra` @ high effort** with a fallback ladder
     (`gpt-6-astra` → `gpt-5.6-sol` → `claude-fable-5-1`; `docs/orchestration.md` §5.2.1) —
     already set; pass `model:` explicitly on each `mcp__codex__codex` dispatch, and adjust
     the ladder rungs only if a project differs.
   - `scripts/tickets.sh` + `scripts/worktree.sh`: `TICKET_PREFIX` — your ticket-key prefix
     (e.g. `AB`, `PROJ`); `worktree.sh` also reads `MAIN_BRANCH`.
   - `AGENTS.md` + `scripts/codex-dispatch-template.md`: `<LINT_CMD>` — the lint/typecheck
     that DOES run inside the Codex sandbox (the one build-ish gate the worker owns, T8).
4. Write the real **Build / run** section of `CLAUDE.md` for your stack.
5. Stand up the machine close-gate: a **compile-validator worker** that does nothing but
   build a worktree's diff (T8). Until it exists, run it by hand — never skip it.
6. Add the **copy/docs fast-path check** (Workflow C): a script that exits 0 only when every
   file in the diff matches the fast-path globs. Without it there is no fast path — "it's a
   small change" is not the check.
7. Connect your issue tracker; record the key convention in `CLAUDE.md` §Version control.
8. Delete this checklist from the README and describe the actual project instead.
9. Optional hardening (port from a mature project when needed): a byte-budget check for
   `CLAUDE.md` (pre-commit hook), a `/feature`-style pipeline skill, path-scoped rules under
   `.claude/rules/`, a project-specific test-impact map, per-layer distilled review briefs
   with a drift check (scales review cost to diff size), and a dual-lane token/USD meter
   over both agents' session logs (answers "is the Codex lane actually used, or merely
   documented?" — and shows that context per turn, not model tier, is where the money goes).
   A project that DOES have CI can re-add a CI close-gate, but the base template has none.

## Layout

```
CLAUDE.md                    constitution + index (Claude Code reads this)
AGENTS.md                    thin Codex shim — points at CLAUDE.md, lists Codex deltas
specs/_TEMPLATE.md           spec template; every section required
docs/orchestration.md        orchestrator pattern, the Codex lane, hand-back convergence
docs/testing-strategy.md     test layers, workflows A/B/C, review-loop mechanics, sign-off
docs/architecture.md         file map + invariants + change-log (kept current, H2)
docs/test-impact-map.md      area → suites to re-run on change
docs/decision-log.md         dated human decisions (G1–G3 outcomes)
scripts/worktree.sh          one worktree per ticket (new / land / rm / list)
scripts/tickets.sh           git-backed ticket board (tickets/*.md, shared across worktrees)
scripts/codex-dispatch-template.md   the dispatch prompt contract
tests/test_tickets.py, tests/test_worktree.py, tests/tickets_width_test.*
                             the scripts' own regression suites (pytest / standalone);
                             keep them green when editing scripts/ (T3)
.claude/rules/               path-scoped auto-loading detail rules (see its README)
.claude/settings.json        pre-commit housekeeping reminder hook
.codex/config.toml           project-level Codex config (MCP servers etc.)
```
