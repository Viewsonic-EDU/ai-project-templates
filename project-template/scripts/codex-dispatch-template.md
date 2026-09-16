# Codex dispatch prompt template

Fill this in, then dispatch it as the `prompt` of `mcp__codex__codex` (`docs/orchestration.md`
§5.2 — the ONLY surface; shell `codex exec` is deprecated), with `cwd` pinned to the worktree
abs path, `model` per the §5.2.1 ladder, `sandbox: "workspace-write"`,
`approval-policy: "never"`. Every section is required — Codex has no path-scoped rules and no
session memory; this prompt carries everything. **The Scope and Report-length blocks go on EVERY dispatch, not just fix rounds:**
Claude Code ships equivalent wording in its own system prompt, so a Claude worker gets that
discipline for free and a Codex worker inherits none of it.

---

Worktree: `<ABS PATH>` — operate ONLY here (read/edit/build); never touch the main
checkout or sibling worktrees. FIRST action: verify `git rev-parse --show-toplevel`
matches this path; mismatch → STOP and report.

Ticket: `<TICKET>-xx`

Spec: `specs/NNNN-<name>.md` — read it fully, including `## Eyeball log`.

Read BEFORE editing: `.claude/rules/<matching rule files, if any>`, plus
`docs/architecture.md` (file map + invariants).

Task: <the bounded task — one slice / one fix, with its AC numbers>

Definition of Done (do ALL, report EACH):
1. Understand/reproduce first — name the root cause (file:line) or cite the AC you
   implement.
2. Implement / fix.
3. Add the tests that lock it — effect-asserting (T2), never presence-asserting; a
   regression test the NEXT worker cannot silently re-break.
4. Build + run the impacted suites (`docs/test-impact-map.md`) — green, or it is NOT
   done. Do NOT use these serialized resources unless granted here: <list or "none">.
   Run `<LINT_CMD>` and hand back clean — that is the one build-ish gate you own.
5. Never skip, comment out, or weaken a test to get green (T3).
6. **If the build is blocked in your sandbox, report the failure VERBATIM (exit code +
   error) and say plainly "not compiled — syntax-parsed only".** Never use wording that
   implies the code compiles on the strength of a parse; a parse does not type-check. An
   uncompiled hand-back is EXPECTED (the orchestrator runs the compile gate, T8) — an
   over-claimed one is the defect.

Scope: do THIS task at the scope stated. Make routine judgment calls yourself; ask only
when two readings produce materially different work. Think the ask is wrong, or spot a
nearby defect? Say so in ONE sentence in the report and still deliver what was dispatched —
never quietly narrow, widen, or transform it (the AC was locked once at G1; growing it is
an escalation for the human, S5). Finish the WHOLE item, not the easy half: write "done"
only when it is done; if part is genuinely blocked, complete everything else and say
plainly what is missing and why.

Restrictions: never push; never commit; deliverable = working-tree diff + the report.

Report format (honest — T4). Write the FULL report to `<scratchpad>/codex-<TICKET>-xx.md`
and return only a SHORT summary — on the MCP surface your output lands in the orchestrator's
context, so keep it conclusions, not a file dump (O1):
- What changed (files + one-line summaries)
- What you verified and HOW (exact commands / suites)
- What you did NOT verify
- Open questions / decisions you deferred (D1)

Report length: be precise but SELECTIVE — drop what would not change the orchestrator's next
decision, rather than compressing sentences into fragments or abbreviations. Never paste file
contents, whole diffs, or raw build/test logs; cite file:line plus the one-line finding.
Files you WRITE (spec sections, Eyeball-log lines) get the length their content needs — no
padded sections, restated summaries, or boilerplate.
