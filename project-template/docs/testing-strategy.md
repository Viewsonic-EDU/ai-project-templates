# Testing strategy

The systematic process behind constitution rules T1–T8. Three workflows: **A** (new feature),
**B** (changing existing code), **C** (the copy/docs fast path).

## Test layers

| Layer | What it proves | When it runs |
|-------|----------------|--------------|
| Unit | each piece of logic in isolation | with every change (T1) |
| Feature / integration | components wired together behave per AC | with every slice |
| E2E happy path | the user-visible flow works in the running artifact | before "done" (T4) |
| Compile + suite gate | the hand-back actually builds and passes | after every worker hand-back, BEFORE review (T8) |
| Machine review loop | fresh eyes on the whole diff vs the spec | before the human gate (T5) |
| Human sign-off (G4) | the final look at the real artifact | last, before push (T6) — the push closes the ticket |

**The compile gate is not optional and not part of the review loop (T8).** Reviewers read the
DIFF: they catch neither a compile error nor a runtime failure, so a hand-back goes
lint → compilation-validator → impacted suites → *then* T5. Never converge compile errors
round-by-round on the main thread, and never accept a syntax parse as evidence of compiling.

**Effect, not presence (T2):** every test asserts what the control/behavior DOES (clicking
X produces Y), never merely that it exists.

## Workflow A — new feature

1. **Spec time (G1):** fill the spec's `## Test plan` — which layers, which suites, the
   exact E2E happy-path steps mapped to each AC.
2. **Implement with tests (T1/T2).** All green, no weakened tests (T3).
3. **Exercise the happy path in the RUNNING artifact** (T4): follow the exact steps from
   the test plan; automate as E2E where drivable, otherwise hand the exact steps to the
   human as look-points.
4. **Converge the hand-back (T8)** — compile gate, then the impacted suites — *before* any
   reviewer reads the diff.
5. **Machine review loop (T5)** until clean — see mechanics below.
6. **Look-point list → human sign-off (G4):** every new/changed surface or behavior, steps
   to reach it, what to check, flagging anything not machine-verified.
7. On approval: push with the ticket key — the ticket is **Done once pushed** with the suite
   green (T8) and G4 given; then register the feature's row in `docs/test-impact-map.md` and
   remove the worktree.

## Workflow B — changing existing code

1. Look up the touched area in `docs/test-impact-map.md`; re-run the targeted suites plus
   any shared-layer suites.
2. Same close ritual: T4 honest verification → T8 convergence → T5 loop → G4 sign-off →
   push (closes the ticket).

## Workflow C — the copy/docs fast path

For a change whose diff is **entirely** copy, strings, or docs, the full ritual buys nothing
and costs a human round. Such a change skips local build/test, the T5 loop and G4, and goes
straight to push.

**The entry check must be MECHANICAL, never a judgment call** — a script that exits 0 only
when every file in the diff matches the fast-path globs (docs, `*.md`, localization/string
resources, comments-only changes). "It's only a small change" is not the check; one code file
in the diff and the change takes the full ritual. Keep the glob list in that script, and treat
any escape found later (T7) as a reason to tighten the globs, not to widen the judgment.

## Honest verification (T4)

- "Compiles + unit tests green" is NOT done — the feature must be exercised end-to-end in
  the running artifact.
- When something can't be driven automatically, write the test anyway if possible, list
  the undriven surfaces as look-points, and **state plainly what was and was NOT
  verified** — never "works" for something only reasoned about. Applies to follow-up
  fixes too.
- **Wording discipline for fenced workers:** a worker that cannot run the real build must say
  *"not compiled — build blocked; syntax-parsed only"*. Any phrasing that implies compilation
  ("static verification passed") on the strength of a parse is the defect, not the blocked
  build itself.

## Live-trace root-cause (T4a)

When a defect's correctness lives in **real server / network / runtime** behavior and code
reading alone leaves you less than confident, don't reason a fix into existence:

1. **Instrument the hops** — request shape → response → async/push delivery → render — with
   temporary debug-only logging.
2. **Run the REAL artifact and capture real traffic.** The first diverging hop names the bug.
3. **Only then write the fix**, and turn the capture into a **golden-fixture** regression test.
4. **Write ADVERSARIAL mocks, not happy ones** — field-type drift, the push that never
   arrives, a URL that changes per call, `200`-but-empty. A happy-assumption mock is precisely
   what ships a false green: the suite passes and the feature is broken in production.

This is a *recommended* method for low-confidence, cross-layer or backend-coupled defects —
not a mandatory gate on every change.

## Machine review loop mechanics (T5)

- **Entry condition:** the diff already compiles and the impacted suites are green (T8).
  Reviewing an unbuilt diff wastes a whole round on defects a compiler finds for free.
- **Inputs:** the diff vs `<MAIN_BRANCH>` (run `git add -N` on new files first so the diff
  is complete), the spec + AC, the Eyeball log.
- **Roster:** ≥1 fresh no-context reviewer. The BINDING reviewer is the opposite model
  family from the author (O4): Codex-authored → Claude reviews; Claude-authored → add a
  read-only Codex reviewer (`mcp__codex__codex` with `sandbox: "read-only"`, or `codex
  review`). **Reviewer ≠ fixer, always.**
- **Anti-hallucination:** a finding must cite files actually in the diff — else dismiss.
- **Rounds:** findings → a fresh fixer carrying the DoD contract
  (`docs/orchestration.md` §3) → re-review. A minor-only round (≤5 trivial findings) fixes
  and exits without re-review. Cap ~4 rounds, then hand off to the human with the open
  list.
- **Escalation (G3):** a finding needing a human DECISION (AC scope / space-time /
  architecture) stops the loop → structured choice to the human → resume. A finding that
  would GROW the AC locked at G1 is exactly this case (S5) — never a silent expansion.
- **Exit:** no MAJOR finding (blocker/major) — minor/nit never block a stop (fix them or log
  them, but they do not hold the gate) (T5).
- **Scale the loop to the diff.** A 3-line change and a 3,000-line slice should not pay the
  same reviewer cost. Cheapest version that works: fewer reviewers on small diffs, and hand
  each reviewer a scoped checklist rather than the whole discipline corpus. If that
  distillation is ever automated, it must be checked for drift against the source docs (e.g.
  a pre-commit hook) — a derived checklist that silently falls behind its source is worse
  than no checklist.

## Escape lines (T7)

A defect found by the human after the loop was "clean" gets a dated line in the change-log
below naming the stage that should have caught it (spec? unit? E2E? review loop?), and
that stage's doc/checklist gets the generalized lesson (H2). This is how the process
learns.

---

Change-log / escape lines:
- 2026-09-14 (human ruling): **removed the T9 CI gate** — these projects have no CI, so
  the terminal state is a push to `<MAIN_BRANCH>` after G4 with the suite green (T8). Test
  layers are now T1–T8; the "CI on the pushed SHA" row and the workflow CI steps are gone.
- ported from a mature downstream project (2026-09): review-loop **exit condition graded** — stop on no MAJOR,
  minor/nit never block (T5); drift check moved to a pre-commit hook (this template has no CI
  gate). Generalized testing lessons (strip the domain noun):
  ① **golden tests cover intermediate output, not just the final payload** — replay the whole
  capture through the PRODUCTION normalizer; don't merely count items or inspect the final
  schema object. ② **assert a cache/perf effect SEPARATELY from correctness** — measure real
  latency under a controlling harness at representative size; avoid timing-race unit
  thresholds. ③ for terminal/TUI work, exercise complete input bursts before/after state
  changes on the SHIPPED shell, and **a bounded wait must check the effect before declaring
  timeout**. ④ for LLM/SDK integrations, **assert the schema that actually reaches the model**
  (incl. closed-object constraints) and the reasoning flag in the serialized request — a
  signature or syntax parse cannot establish compatibility; measure each sequential model/tool
  hop live and budget the whole interaction (zero-delay fakes validate no budget).
- (template) created.
- ported from a mature downstream project (2026-07-28): the **T8 compile+suite gate** added as its own layer
  ahead of the review loop (reviewers read the diff and catch neither compile nor runtime
  failure); the **T9 CI gate** added as the closing layer (pushed ≠ done); **Workflow C**, the
  mechanically-checked copy/docs fast path; **T4a live-trace root-cause + adversarial mocks**;
  review-loop entry condition + cost-scaling note; honest wording rule for fenced workers that
  cannot build.
