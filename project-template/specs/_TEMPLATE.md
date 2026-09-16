# NNNN — <Feature name>

Ticket: `<TICKET>-xx` · Status: **draft** | approved | shipped

## Summary

One paragraph: what this feature is and why now.

## Source of truth / background

What defines correct behavior — reference implementation, product doc, design file, API
contract. Record OBSERVED behavior (run/read the source), never assumptions. Extract exact
values (colors, sizes, limits, error codes) — never paraphrase them.

## Acceptance criteria

Numbered, testable statements. Each maps to at least one test in the Test plan.

1. …
2. …

## Size estimate & slice split

Use `docs/slice-sizing.md`: enumerate numbered AC items (including walk BUILD), every
new/edited file (tests/fixtures/docs too), resources and extra costs. Show the table ×
weights × current calibration factor derivation, raw result and resulting `size:`.

| Feature | Count | Weight | Contribution |
|---|---|---|---|
| … | | | |

Factor: … (initial 0.6; for n > 0 from `scripts/context-ledger.sh calibrate`,
`next factor = current factor × median(actual/predicted)`). Raw: …; `size:` …
(nearest 0.25, minimum 0.25). Raw > 1.0: split proposal for G1. Raw < 0.25: merge note.
For each slice: `blocked_by: […]`; Parallel-safety: safe beside …; conflicts with …
on files …; serialized resources … (or none). For ≥3 slices, attach the cross-family
batch-split check result verbatim at G1; human-only adjudication.

## Design / architecture

The chosen approach; alternatives considered and why they lost. New files/modules and
where they live (mirror into `docs/architecture.md` on merge).

## Decisions requiring human input (gate G1)

The ONLY human gate at spec time. Each decision is a structured choice — 2–4 options,
one marked **(Recommended)**, with tradeoffs — in one of three categories:

- **AC scope** — what's in/out of this slice.
- **Space-time tradeoffs** — cost vs speed vs quality calls. (One surfacing
  mid-implementation → interrupt and ask then, gate G2.)
- **Core architecture** — choices expensive to reverse.

| # | Category | Question | Options (Recommended first) |
|---|----------|----------|------------------------------|
| 1 | | | |

## Deliberately NOT in scope

Every omission vs the source of truth, each tagged **dropped** (with reason; UI/assets
removed) or **deferred** (visible/noted, coming in slice NNNN).

## Test plan

- **Unit:** …
- **Feature / integration:** …
- **E2E happy path (exact steps):** open X → do Y → expect Z. Mapped to AC #.
- **Not machine-drivable** (→ becomes look-points at G4): …

## Definition of Done

- [ ] Front scope walk done and the AC locked at G1 (S5) — later growth escalated, not silent
- [ ] All ACs met, each covered by an effect-asserting test (T2)
- [ ] All tests green; none skipped or weakened (T3)
- [ ] Happy path exercised in the RUNNING artifact; verification stated honestly (T4)
- [ ] Hand-backs converged BEFORE review: compile gate → impacted suites (T8)
- [ ] Machine review loop clean (T5); escalations resolved (G3)
- [ ] `docs/architecture.md` + `docs/test-impact-map.md` updated (H2)
- [ ] Look-point list delivered; human sign-off received (G4)
- [ ] **Pushed to `<MAIN_BRANCH>` with the ticket key** — the ticket is then Done and the
      worktree removed (Done = pushed after G4 with the suite green, T8)

## Eyeball log

Append each round of human feedback here, dated, BEFORE acting on it.

- YYYY-MM-DD R1: …
