# Batch-split check dispatch

Use once before G1 for ≥3 slices; skip for <3 (`docs/slice-sizing.md` §Batch-split
adversarial check). Send a fresh cross-family read-only `mcp__codex__codex` dispatch,
`sandbox: read-only`, `approval-policy: never`, the O2 model at high effort (only the
documented availability fallback ladder, `docs/orchestration.md` §5.2.1). Verify actual
lane metadata per O7. The orchestrator attaches the output verbatim to the G1 message.

---

Worktree: `<absolute worktree path>` — first verify `git rev-parse --show-toplevel`.
Operate only here; no edits, commands that write, network or serialized resources.

Review the proposed split from ONLY these inputs:

1. Each slice's `## Acceptance criteria` and its frontmatter `blocked_by:` line,
   labelled with its ticket key: `<paste packets>`.
2. Rubric R1–R4: `docs/slice-sizing.md` §Batch-split adversarial check.
3. Closed vocabulary: `docs/slice-sizing.md` §Established gates & commands.

R1: effect-decidable (T2). R2: single-run acceptance; multi-stage ESTABLISHED gates
such as T4a are valid. R3: closed gate/command vocabulary. R4: non-vacuous evidence.
VETO on circular `blocked_by` or vacuously-true AC (e.g. "the command runs without error").

Return ONLY `clause / rubric item / why`, quoting the problematic clause. Prefix a VETO
reason with `VETO circular blocked_by` or `VETO vacuously-true AC`; for a clean split return
`none`. Do not propose implementation, grow the AC or adjudicate a finding. All findings
are human-adjudicated, never automatically blocking.
