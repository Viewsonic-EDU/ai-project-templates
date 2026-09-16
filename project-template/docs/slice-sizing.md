# Slice sizing

Ported from a mature downstream project (2026-09-17). Gate **G0** in `CLAUDE.md`; the
measurement side is `scripts/context-ledger.sh` and `docs/context-ledger.md`.

## Unit

**1.0 slice = 60% of the orchestrator's context window consumed between
`worktree.sh new` and `worktree.sh rm`.** Include review, conflict handling, push and
cleanup in the estimate. This measures input plus cache creation plus cache read,
not accumulated API billing or output tokens. The default window is 1000000 tokens;
the owner confirms it against `/context` at G4 (override: `CONTEXT_LEDGER_WINDOW`).

## G0 procedure and report

Before **every** ticket's `worktree.sh new`, including the copy/docs fast path:

1. Estimate the features below, apply the correction factor and set the ticket's
   displayed `size:` with `scripts/tickets.sh set-size KEY N`.
2. Run `scripts/context-ledger.sh now` from the orchestrator's own Claude session.
3. Report the line verbatim beside `size:` and the raw estimate. For example:

   ```text
   size: 0.5 (raw 0.48)
   usage=180000 window=1000000 used=18.00% remaining=820000 slices=0.30
   ```

4. The human decides **same session** or **push & cleanup, then a fresh session**.
   Report `unmeasured: <reason>` as-is; the human decides on that evidence too.
5. After the decision, `worktree.sh new` captures the baseline automatically. At G1,
   show the full derivation and split/merge proposal. Later size changes do not alter
   the start marker's prediction, which preserves the G0 calibration decision.

## Feature table and weights

These initial weights are **UNCALIBRATED**. Count numbered AC items, including scope-walk
BUILD additions; count every new/edited tracked file, including tests, fixtures and docs.
Count expected review rounds **beyond the first**, not all rounds. Count each distinct
serialized resource required and each applicable extra-cost feature once. Rename the four
extra-cost rows to whatever is expensive for YOUR stack (keep the order; the script and
tests only depend on the weights), e.g. a native/mobile build only the orchestrator can run.

| Feature | Weight |
|---|---|
| AC items | 0.03 |
| Files touched | 0.01 |
| Serialized resources | 0.10 |
| Server deploy | 0.25 |
| GUI eyeball handover | 0.25 |
| T4a live capture | 0.25 |
| Orchestrator-run native build | 0.25 |
| Review rounds beyond first | 0.10 |

`raw = sum(count × weight) × factor`. The initial **factor is 0.6**. On the downstream
project a back-computation across its last 17 landed tickets gave mean 0.84 and median
1.0, while the owner's felt average was 0.5: `0.5 / 0.84 ≈ 0.6`. That first calibration
point was the owner's felt average, **not a measurement** — treat 0.6 as a placeholder
until your own ledger has five rows (`calibrate` below).

Compare the **raw** result to thresholds before rounding: raw > 1.0 requires a split
proposal at G1; raw < 0.25 is a merge candidate. The human adjudicates. Display `size:`
rounded to the nearest 0.25 (ties upward), minimum 0.25. A displayed 1.0 can still have
a raw value above the split threshold; show both.

## Worked examples

The vectors below follow the table's order: AC, files, resources, deploy, GUI, capture,
native build, extra review rounds. Counts include all artifacts in each hypothetical slice.

| Example | Counts | Factor | Raw | Displayed size | Proposal |
|---|---|---|---|---|---|
| split | 40,20,1,1,0,0,0,1 | 0.6 | 1.11 | 1.00 | Split at G1; raw exceeds 1.0 despite displayed 1.0. |
| merge | 3,3,0,0,0,0,0,0 | 0.6 | 0.072 | 0.25 | Merge candidate; raw is below 0.25. |
| sibling-conflict | 12,8,0,0,0,0,0,2 | 0.6 | 0.384 | 0.50 | One slice; serialize the conflicting sibling. |

Calibration example: a feature sum of 1.38 with current factor 0.6 gives raw 0.828,
displayed prediction 0.75. If actual is 0.75, the ratio is 1.00. With median ratio 1.00,
`next factor = 0.6 × 1.00 = 0.6`; the same features still give raw 0.828 and size 0.75.

## Parallel safety

Put this statement beside each slice's `blocked_by:` line:

```text
blocked_by: [<TICKET>-901]
Parallel-safety: safe beside <TICKET>-902; conflicts with <TICKET>-901 on
scripts/tickets.sh and tests/test_tickets.py; serialized resources: none.
```

This is the sibling-conflict example above: include the extra expected review/conflict
rounds in the estimate. Name both shared files and serialized resources, or explicitly
say none; different tickets alone do not establish independence.

## Calibration and ledger operation

Run `scripts/context-ledger.sh calibrate`: `factor=<median> (n=<k>)` is the median of the
last five numeric actual/predicted ratios (even count: mean of the two middle values).
For n > 0, update with `next factor = current factor × median(actual/predicted)` and apply
the next factor to the next feature sum. Despite the printed `factor=` label, the numeric
result is a ratio, not a replacement for the current factor. With no numeric rows it prints
`factor=0.60 (n=0)`: use the initial factor 0.6 directly, without multiplying it again.
Compacted and unmeasured rows are excluded; junk rows produce one warning.
Every ~10 rows the human re-tunes weights by hand from recurring overshoots; do not fit
an automatic regression to five samples.

`start KEY [--predicted N]` freezes prediction (CLI, ticket size, else `n/a`), window and
counters in `<git-common-dir>/context-ledger/KEY.json`. `record KEY` appends to the primary
checkout's `docs/context-ledger.md` and removes the marker. The next `land` commits it with
the board, including ledger-only changes. Hooks are best-effort; direct invalid requests
exit 1 without modifying state. Measurement failures exit 0 with `unmeasured: <reason>`.

A missing start produces an unmeasured row. The explicit manual form is
`record KEY --start-tokens N --predicted N --note TEXT`; it uses `unknown` for turns,
dispatches and compactions. When current usage is unavailable, the upper bound
`N <= current usage` cannot be checked; the row stays unmeasured. Existing state cannot be
overridden: explicit `record --predicted` or `--window` flags are refused when a start
marker exists. A previously recorded KEY with no open marker prints `already recorded KEY`,
preserving exactly one row per ticket.

`dispatches` means **dispatch attempts, not verified fan-out**. Inclusive intervals
that overlap another open marker carry `overlapping KEY` notes; those deltas are not
exclusive portions of a session. Synthetic errors never supply usage. Compaction markers
are unverified schemas; a ≥30% drop is explicitly a heuristic (`compacted?`).

**How the script finds YOUR session.** It walks up from its own PID to the nearest
ancestor process named `claude`, then reads `<events dir>/<that pid>.json`, written by
`.claude/hooks/session-events.sh` (registered in `.claude/settings.json` for SessionStart
and UserPromptSubmit). Without that hook every command reports `unmeasured: no events
file for pid N` — the ledger never breaks `worktree.sh`, it just measures nothing. It
needs `python3` on `PATH`. Run `now`/`start`/`record` from the orchestrator's OWN shell,
never from a dispatched worker: a worker's ancestor is a different process.

Overrides: `CONTEXT_LEDGER_WINDOW`, then `--window N` (CLI wins; default 1000000);
`CLAUDE_CONTROL_EVENTS_DIR` (default `~/.claude-control/events`);
`CONTEXT_LEDGER_STATE_DIR`; `CONTEXT_LEDGER_FILE`; `CONTEXT_LEDGER_DISPATCH_TOOLS`
(space-separated, default `Agent mcp__codex__codex`); `TICKET_PREFIX` (same as
`tickets.sh`). The row retains the start window even if the environment changes later.

## Batch-split adversarial check

For **≥3 slices**, the orchestrator makes **one fresh cross-family read-only dispatch**
before G1; skip for <3. Use [the dispatch template](../scripts/batch-split-check-template.md).
Inputs are each slice's **AC section plus its frontmatter `blocked_by:` line**, the rubric
below and the established-gates section by path. Do not pass implementation or conversational
history. Use `mcp__codex__codex`, `sandbox: read-only`,
`approval-policy: never`, the O2 model at high effort. Verify the actual lane per O7.

| Rubric item | Decidability question |
|---|---|
| R1 — effect-decidable | Can a named observation decide the effect, rather than presence (T2)? |
| R2 — single-run acceptance | Can one specified acceptance run reach a verdict? A multi-stage ESTABLISHED gate such as T4a is not a violation. |
| R3 — closed vocabulary | Are gates and command signatures drawn from the established list, or explicitly defined as this slice's deliverable? |
| R4 — non-vacuous | Can a plausible wrong artifact fail the clause, with an observable counterexample? |

VETO list: **circular `blocked_by`** dependencies; **vacuously-true AC**, such as
"the command runs without error" without a required effect. The reviewer returns only
`clause / rubric item / why`, quoting each problematic clause (or `none` for a clean
split). Attach the result **verbatim** to the G1 message. **Human-only adjudication**:
findings, including VETOs, never automatically block or rewrite the split.

The fixture corpus under `tests/fixtures/slice_split/` includes a three-slice cycle, a
vacuous clause and a clean split. Exercise the rubric once with a real dispatch when you
bootstrap the project and record both bad-fixture VETOs and no clean-fixture VETO in the
Eyeball log.

## Established gates & commands

This closed vocabulary is seeded from the constitution's human-gate table and the shell
usage/help surfaces. Keep it current when adding a gate or command; a new deliverable
may define its own signature explicitly in its AC.

| Gate | Established meaning |
|---|---|
| G0 | Before `worktree.sh new`: same session vs push & fresh session, from size and remaining context. |
| G1 | Before code: approve scope, architecture and the split (presented as the G1 brief, `docs/orchestration.md` §7). |
| G2 | Mid-implementation space/time or scope decision. |
| G3 | Review findings that require a human decision. |
| G4 | Final sign-off on the running artifact before push. |
| T4a | Trace real behavior, capture evidence, then lock a golden regression. |
| T5 | Binding cross-family review/fix loop. |
| T8 | Compile gate, impacted suites, then review. |

- `scripts/worktree.sh new KEY`, `land KEY`, `rm KEY`, `list`.
- `scripts/tickets.sh [board]`, `watch`, `board [--all]`, `list [status]`, `show KEY`,
  `new KEY "title" [--wishlist] [--epic PARENT]`, `new-epic KEY "title"`,
  `mv KEY status [--force]`, `close KEY [--force]`, `block KEY B1 [B2…]`,
  `unblock KEY B1 [B2…]`, `ready`, `set-worktree KEY [KEY|PATH]`, `get-worktree KEY`,
  `set-epic CHILD PARENT`, `epic KEY`, `set-size KEY [N]`.
- `scripts/context-ledger.sh now`, `start KEY [--predicted N]`,
  `record KEY [--start-tokens N] [--predicted N] [--note TEXT]`, `calibrate`;
  each accepts `--window N`.
- Build/test commands: `<BUILD_CMD>`, `<TEST_CMD>`, `<LINT_CMD>` (CLAUDE.md §Build / run);
  worker dispatch may name pre-provisioned equivalents. No gate grants serialized
  resources implicitly.
