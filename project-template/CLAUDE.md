# CLAUDE.md — <PROJECT_NAME>

> **This file is a CONSTITUTION + INDEX, not a manual** (byte budget 40,000 — enforce with a
> lint / pre-commit hook once the project matures). Each rule is one line with an anchor (S1, T5…); operational
> detail lives in the owning gate doc (`docs/…`) or a path-scoped rule (`.claude/rules/*.md`)
> that auto-loads in context. Route lessons per H2 — never grow the constitution.

## Build / run

<!-- BOOTSTRAP: replace with the real commands for this stack. -->

```bash
<BUILD_CMD>    # build
<TEST_CMD>     # run all tests
<RUN_CMD>      # run the app / service locally
```

## Version control / tickets

- **Every task maps to a ticket** `<TICKET>-xx`; the key MUST appear in the branch name or
  commit message. Open the ticket before starting, at latest before committing. The board
  is `scripts/tickets.sh` (git-tracked `tickets/*.md`, shared across worktrees; `board` /
  `watch` to view, `TICKET_PREFIX` sets the key prefix).
- **Every code-touching task runs in its own git worktree**: `scripts/worktree.sh new
  <TICKET>-xx` (marks an EXISTING board ticket in-progress — open it first with
  `scripts/tickets.sh new`); build and test inside it so
  the main checkout stays clean. At close, `scripts/worktree.sh land <TICKET>-xx` carries the
  feature branch AND the board to `<MAIN_BRANCH>` in one ref update.
- **Close ritual, in order:** build green → hand-back convergence (T8) → machine review loop
  (T5) → human sign-off (G4) → push to `<MAIN_BRANCH>`. Never push before the human approves;
  the ticket is **Done once it is pushed to `<MAIN_BRANCH>`** with the suite green (T8) and G4
  given.
- **Exception — the copy/docs fast path:** a diff that a MECHANICAL check proves is entirely
  copy/docs skips local build/test, T5 and G4 → straight to push. Anything else takes the
  full ritual; "it's only a small change" is not the check.

## The golden rule: spec before code

- **S1.** No implementation without an approved spec (gate G1). "Build feature X" → the
  first deliverable is a **spec draft only**; wait for explicit approval.
- **S2.** Specs live in `specs/NNNN-name.md`, copied from `specs/_TEMPLATE.md` — **every
  section is required**, including `## Test plan` and the DoD checklist.
- **S3.** The ONLY human gate at spec time is `## Decisions requiring human input` — each a
  structured choice (2–4 options + Recommended + tradeoff) in three categories: **AC scope /
  space-time tradeoffs / core architecture**. A tradeoff surfacing mid-implementation
  interrupts and asks then (gate G2).
- **S4.** A too-big feature is SPLIT, not built in one pass: propose an epic + vertical
  slices (spine first); the human confirms the split as part of G1.
- **S5.** **The scope walk runs at the FRONT and locks the AC once.** Before G1, a
  CROSS-FAMILY pass (auditor ≠ the spec author's family) re-walks the source of truth against
  what the draft accounts for — using an objective, enumerable denominator, not a vibes-walk
  — and seeds G1's build / defer / drop menu, so full scope is found at PLANNING. T5 is only
  a backstop: a gap it finds that would GROW the locked AC escalates (G3), never expands
  silently.

## Implementation rules

- **I1.** One vertical slice at a time — each ships something the user can see, run, and
  test end-to-end.
- **I2.** Idiomatic code for this stack; shared visual/config constants live in ONE central
  place, never inlined at call sites.
- **I3.** Never silently shrink scope: N behaviors/controls in the source of truth → N in
  the product, or each omission recorded in the spec's `## Deliberately NOT in scope` with
  a reason.
- **I4.** Dropped ≠ deferred: a CUT feature is recorded with a reason and its dead code and
  assets removed; a DEFERRED one stays visible and is clearly noted.

## Testing rules (detail: `docs/testing-strategy.md`)

- **T1.** Tests are written WITH the code, never after. Every piece of logic gets a unit
  test; every slice gets ≥1 E2E happy path mapped to the spec's acceptance criteria.
- **T2.** Test every control/behavior's **EFFECT, not its presence** — "the button exists"
  is not a test.
- **T3.** No red builds. Never skip, comment out, or weaken a test to get green.
- **T4.** **Honest verification:** "compiles + unit tests green" is NOT done — exercise the
  feature in the RUNNING artifact, and state plainly what was and was NOT verified. Never
  write "works" for something only reasoned about.
- **T4a.** **Runtime correctness: trace, don't guess.** When a defect's correctness lives in
  real server / network / runtime behavior and code-reading leaves you less than confident,
  instrument the hops and capture REAL traffic BEFORE writing a fix — the first diverging hop
  names the bug. Turn that capture into a golden-fixture regression test, and write
  ADVERSARIAL mocks (field-type drift, the push never arrives, 200-but-empty) — a
  happy-assumption mock is what ships a false green.
- **T5.** **Machine review loop before any human gate:** fresh no-context reviewer(s), fed
  only diff + AC + spec; **reviewer ≠ fixer**, and the binding reviewer is the OTHER model
  family from the author (O4). Mechanical defects auto-fix + re-review; the loop **STOPS
  ONLY when the reviewer reports no MAJOR finding (blocker/major) — minor/nit never block a
  stop**. A finding needing a human DECISION stops the loop → escalate (gate G3).
- **T6.** **Human sign-off (G4) is the FINAL gate:** report the built artifact + a
  **look-point list** (every new/changed surface or behavior, steps to reach it, what to
  check). Push only after approval.
- **T7.** A human-found defect gets an **escape line** in `docs/testing-strategy.md`'s
  change-log naming the stage that should have caught it — that stage's doc gets the lesson.
- **T8.** **A worker hand-back is UNCOMPILED and UNTESTED — converge in this ORDER, always:**
  ① the worker runs whatever lint/typecheck its sandbox DOES allow and hands back clean;
  ② a compilation-validator worker builds the diff FIRST — **never converge compile errors
  round-by-round on the main thread**; ③ the orchestrator runs the impacted suites (it owns
  the serialized resources); ④ only THEN the T5 loop, which reads the DIFF and therefore
  catches neither compile nor runtime failure. A syntax parse is NOT a compile — no worker
  may report wording that implies otherwise (D1 applies: a semantic call escalates, it is
  never patched to green inline).

## Refactor / housekeeping

- **H1.** Refactor in dedicated passes; no observable-behavior change; tests stay green and
  unchanged in intent.
- **H2.** **Lesson routing — after every change:** a rule/gate change → one line HERE; a
  defect pattern → the owning doc (`testing-strategy` / `architecture` / `_TEMPLATE`); deep
  area detail → a path-scoped `.claude/rules/*.md`. Update `docs/architecture.md` (file
  map + change-log) in the same change.

## Orchestration & dual-agent (detail: `docs/orchestration.md`)

- **O1.** The main thread is an ORCHESTRATOR — durable state lives in FILES (spec, AC,
  Eyeball log), never the conversation. Implementation, every fix round, AND every review
  run in disposable workers — **never inline on the main thread, no exceptions** — so the
  orchestrator carries CONCLUSIONS, never a full diff or dump. **Context carried per turn is
  the dominant cost** — far more than any model-tier choice — so keep the orchestrator's
  context flat: conclusions in, dumps out.
- **O2.** **Worker family is a routing knob orthogonal to model tier.** Codex (pinned
  `gpt-6-astra` @ high effort, with a **fallback ladder — `gpt-6-astra` → `gpt-5.6-sol` →
  `claude-fable-5-1`, effort ALWAYS high — stepped down ONE rung ONLY when the current one is
  unavailable** (outage / quota / repeated dispatch failure), never by preference; dispatched
  **ONLY via the `mcp__codex__codex` MCP tool** — `codex exec`/shell is **DEPRECATED, do not
  use it** — the tool takes `prompt`/`model`/`sandbox`/`approval-policy`/`cwd`; continue a
  thread with `mcp__codex__codex-reply`) is **THE development lane**: ALL code work —
  implementation AND fix-round convergence, at FULL scope, including correctness-critical /
  source-of-truth work — is dispatched to a Codex AGENT; a peer-capable family at high effort
  is NOT a tier downgrade. **Claude does NOT author or fix code inline** — it orchestrates and
  holds the binding review (O4). Never-downgrade governs weak model TIER only (don't send
  judgment work to a cheap model), never the family choice.
  **Fallback-ladder caveat:** the third rung `claude-fable-5-1` is Claude-FAMILY and cannot run
  on the Codex MCP surface — it dispatches as a Claude Fable-5 agent, so the dev lane goes
  same-family and O4's cross-family review CANNOT hold. That rung is the last resort (both GPT
  rungs down): record the same-family limitation as an O7-style FINDING line, still run the T5
  loop, and never claim the review was cross-family (mechanics: `docs/orchestration.md` §5.2).
- **O3.** Pin every worker to its worktree ABSOLUTE PATH and verify after every spawn:
  main checkout clean, worktree diff contains ONLY your files. A review loop's "clean" is
  worthless until its finding titles name YOUR files. Re-base a stale worktree before
  review AND before push — a base another session has moved makes the whole batch show up
  as your diff.
- **O4.** **Cross-family review is the binding gate, by construction:** whoever authored the
  diff, the OTHER family's review loop decides clean — Codex-authored → Claude reviews;
  Claude-authored → a Codex read-only pass joins the loop. Same-family reviewers share blind
  spots, so a worker's own "green" is never the gate.
- **O5.** Codex lane restrictions (mirrored in `AGENTS.md`): runs fenced + non-interactive
  (`sandbox: workspace-write` + `approval-policy: never`); never push; never commit unless
  dispatched with the ticket key; never take a serialized resource it wasn't granted; writes
  the full report to a FILE and returns a short summary (O1); honest report (T4) required.
- **O6.** **Name every spawn `<TICKET> <action>`** — the agent list is the only place the
  human sees concurrent work, and three default labels are indistinguishable. A `Workflow`'s
  row text is FIXED (its `title`/`description` inputs are ignored and `meta` can't
  interpolate), so keep the first sentence of `meta.description` short and stage-naming, and
  make the run's first act a `log()` of its identity — `<TICKET> · round N · <worktree>`.
- **O7.** **Verify the lane was USED — never assume it from the docs.** Codex writes a
  session log per dispatch (`~/.codex/sessions/**/rollout-*.jsonl`); its `session_meta` names
  `cwd` / `source` / `originator` — confirm the run landed in the WORKTREE, on the surface you
  chose. Tooling that silently degrades to a same-family fallback is a FINDING, not a
  footnote: it retroactively voids every cross-family claim (O4) back to the last check.

## When in doubt

- **D1.** Spec vs reality conflict, or anything ambiguous → STOP and ask the human. Never
  guess or silently pick an interpretation.
- **D2.** Confidence triage: *determinable* by running/reading/testing → verify it YOURSELF
  before "done"; *user preference / scope / tradeoff* → ask, phrased as user-observable
  outcomes with a recommendation. At hand-off, report each uncertain point as
  **✓ verified-by-X** or **→ needs-your-call**.

## Machine-serialized resources

<!-- BOOTSTRAP: list resources only ONE session/worker may use at a time (a test DB, a
device, a simulator, an exclusive test runner) — or delete this section. -->

- `<SERIALIZED_RESOURCES>`

## Human gates (the complete list)

| Gate | When | The human decides |
|------|------|-------------------|
| **G1** | before any code | spec approval: AC scope, architecture, the slice split (S1–S4) |
| **G2** | mid-implementation | space-time / scope tradeoffs that surface (S3) |
| **G3** | during the review loop | escalated findings needing a decision (T5) |
| **G4** | before push | final sign-off on the running artifact, via the look-point list (T6) |

Everything not in this table is automated and must not block on the human.
