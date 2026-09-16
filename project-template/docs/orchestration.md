# Orchestration & the dual-agent lanes

How the main thread (the orchestrator) runs work and spawns workers. Constitution anchors:
**O1–O5**. Applies to every session, manual or pipeline.

## 1. The main thread is an orchestrator (O1)

**Why:** the conversation is the *volatile* medium — it gets summarized (lossy) and suffers
lost-in-the-middle as it grows. Anything that must survive belongs in a file, not in chat.

- **Delegate the token-heavy work.** Implementation, multi-file reads, builds, and every
  fix round run in disposable workers. A worker returns *conclusions* (what changed, what
  was verified, the look-point list) — never file dumps.
- **State lives in files:** ACs/scope → the spec; human feedback → the spec's
  `## Eyeball log` (§2); human decisions → `docs/decision-log.md`; lessons → the owning
  gate doc (H2).
- **Don't accumulate N fix rounds on one thread.** Each round = a *fresh* fixer fed
  (worktree path + spec/AC + the specific feedback + the Eyeball log).
- **Inline trivial edits** (≤3 lines, no build) may stay on the main thread. The test:
  *"can I write this correctly right now without reading the file?"* — no → dispatch.
- **Context per turn is the first-order cost lever.** Measured on a mature project, ~90% of
  spend was cache read/write — i.e. how much context each turn carries — which dwarfs both
  effort knobs in §4. Compact earlier, split the work, and keep conclusions (not file dumps)
  on the main thread *before* arguing about model tiers.

**Fan-out vs serialize.** Independent defects (different files, different controls) → fixers
in PARALLEL. Defects sharing a file, or depending on each other → serialize; parallel edits
to one file clobber each other. Builds, test runs and any serialized resource are singular,
so the rhythm is **parallel EDIT → ONE build + review at reconverge**, never N fixers each
building.

**When to throw the slice away instead of patching.** If ≥~3 defects share ONE root cause (a
wrong structure, a mis-read source of truth), re-solve the slice fresh rather than patching —
a find→fix→break loop is the symptom that the base itself is wrong, and patching a wrong base
is the one case where "re-do it" is genuinely faster.

## 2. The Eyeball log (O1)

Every spec carries a `## Eyeball log`. Each round of human feedback is appended there,
**dated, before it is acted on**. It survives summarization, guards against re-breaking an
earlier-corrected point, and is the durable hand-off to a fresh no-context fixer.

## 3. The fixer DoD contract (O1)

Every dispatched worker (either lane) carries this contract — paste it into the dispatch:

```
Worktree: <ABS PATH> — operate ONLY here (read/edit/build); never touch main or siblings.
Inputs: spec/AC + this ONE task + the Eyeball log.
Definition of Done (do ALL, report EACH):
  1. Understand/reproduce first — name the root cause or cite the spec AC you implement.
  2. Implement / fix.
  3. Add the tests that lock it (effect-asserting, T2) — a regression test the NEXT worker
     cannot silently re-break.
  4. Build + run the impacted suites (docs/test-impact-map.md) — green, or it is NOT done.
     Run whatever lint/typecheck this sandbox allows and hand back clean; if the build itself
     is blocked here, say so verbatim — a syntax parse is NOT a compile (T8).
  5. Report honestly (T4): what changed · what you verified and HOW · what you did NOT
     verify · open questions.
Scope: do THIS task at the scope stated. Make routine judgment calls yourself; ask only when
  two readings produce materially different work. Think the ask is wrong, or spot a nearby
  defect? Say so in ONE sentence in the report and still deliver what was dispatched — never
  quietly narrow, widen, or transform it (the AC was locked once at G1; growing it is an
  escalation for the human, S5). Finish the WHOLE item, not the easy half: write "done" only
  when it is done; if part is genuinely blocked, complete everything else and say plainly
  what is missing and why.
Report length: the main thread consumes conclusions, not dumps (O1). Be precise but
  SELECTIVE — drop what would not change the orchestrator's next decision, rather than
  compressing sentences into fragments. Never paste file contents, whole diffs, or raw
  build/test logs; cite file:line plus the one-line finding. Files you WRITE (spec sections,
  Eyeball-log lines) get the length their content needs — no padding, no restated summaries.
```

**Paste the Scope and Report-length blocks on EVERY dispatch, not just fix rounds.** Claude
Code ships equivalent wording in its own system prompt, so a Claude worker gets that
discipline for free — **a Codex worker inherits none of it**, and both failure modes it
prevents (silently growing the locked AC; handing back a wall of text that lands in the
orchestrator's context) have no other guard in that lane.

## 4. Worker effort routing (O2)

Triage before every spawn (one sentence of thought). Four knobs:

| Knob | Low effort | High effort |
|---|---|---|
| **model tier** | cheap for mechanical chores / pure location-search | strong for judgment, cross-layer, correctness |
| **thoroughness** | single file | trace every hop / run the app |
| **structure** | single agent | fan-out + adversarial verify |
| **family** (§5) | — | **Codex ↔ Claude** — orthogonal to the other three |

Never downgrade correctness-critical work (source-of-truth extraction, spec writing, review)
to a weak MODEL TIER. **Unsure → bias high** — a missed pass costs a human round, not tokens.

**The family knob is the one most often mis-set** — treat it as orthogonal to model tier.
The model-tier row sizes only the **Claude-family** spawns (explores, reviewers, orchestrator
chores). Code-writing — implementation AND fix-round convergence — routes to **Codex
regardless of the tier choice** (§5). Reading the tier row as if it covered code-writing is
the mis-route ("sometimes I open a Codex, sometimes an opus"); it doesn't — this knob does.

## 5. The Codex dev/review lane (cross-family — the DEFAULT dev lane)

**Division of labor (O2):** Codex is the DEFAULT development lane — implementation AND
fix-round convergence, at FULL scope (including correctness-critical / source-of-truth
work). Claude orchestrates (dispatch, worktree/state discipline, the fix-round loop,
close-out) and holds the binding review. A Codex dispatch is a disposable worker context,
so rules O1/O3 apply to it verbatim.

**Why a second family:** (a) **cost** — implementation + build-fix iteration is the
token-heaviest phase and runs on a separate quota; (b) **quality** — the "fresh no-context
reviewer" (T5) is strongest when the reviewer's model FAMILY differs from the author's;
same-family reviewers share blind spots.

### 5.1 Install the MCP server FIRST — a missing one fails SILENTLY

**Do this during bootstrap, before anything cites `mcp__codex__codex`:**

```bash
claude mcp add codex -s user -- codex mcp-server   # user scope: every repo gets it
claude mcp list                                    # must show codex ✔ Connected
```

A newly added server needs a **session restart** before its tools resolve. Verify
`ToolSearch "select:mcp__codex__codex"` actually returns a schema.

**Why this is a P0 step and not a footnote.** Well-built pipelines degrade *gracefully* when
the tool is missing — they fall back to a same-family auditor and record that in a field
nobody reads. Nothing breaks loudly, and every "cross-family" claim (O4) since the server
went missing is void. On a mature project this ran undetected for ten days, silently making
the cross-family front walk (S5) same-family. **Treat any `claude-fallback` /
"no codex tool" line in a run's output as a FINDING: re-check registration before accepting
that run's result.**

### 5.2 Dispatch — the `mcp__codex__codex` MCP tool is the ONLY surface

Shell `codex exec` is **DEPRECATED — do not use it.** EVERY dispatch — from the main thread
or inside a Workflow/skill — goes through **`mcp__codex__codex`**; continue a thread with
**`mcp__codex__codex-reply(threadId, prompt)`** instead of cold re-briefing. The call
auto-backgrounds after ~2 min and delivers a completion notification, so it does not block
the main thread; paired with report-to-a-FILE (below) it keeps the orchestrator's context
flat (O1).

```
mcp__codex__codex(
  cwd:     "<WORKTREE ABS PATH>",
  model:   "gpt-6-astra",                        # ladder rung 1; step down ONLY when unavailable (§5.2.1)
  config:  { model_reasoning_effort: "high", approval_policy: "never" },
  sandbox: "workspace-write",
  prompt:  "<dispatch prompt — scripts/codex-dispatch-template.md>"
)
```

- **`approval-policy: "never"` is REQUIRED.** Codex's default (`on-request`) surfaces as an
  interactive Accept/Decline that a background dispatch has no one to answer, so the call just
  hangs. `never` + the `workspace-write` fence IS the autonomous mode: writes freely INSIDE
  the worktree, denies anything outside (stops and reports), never blocks on input.
- **Report to a FILE.** The prompt MUST say *write the full report to
  `<scratchpad>/codex-<TICKET>-xx.md` and return only a short summary* — else the transport
  silently breaks O1.
- `sandbox: "workspace-write"` is an OS-level write fence around the worktree — stronger than
  prompt-level pinning, but §6's post-spawn verification STILL runs every time. Read-only
  review passes use `sandbox: "read-only"`.
- **Pin the model + reasoning effort explicitly every dispatch** — don't rely on ambient
  `~/.codex/config.toml`; the repo `.codex/config.toml` is a versioned setup reference only.
  **Known gap:** the effort pin is passed but NOT verifiable from the dispatch side (a probe
  asked for its own setting and answered "not exposed to me"). Pass it every time; never
  claim it was confirmed.
- **The dispatch prompt must carry everything** — Codex auto-loads `AGENTS.md` but has no
  path-scoped rules and no session memory. Use `scripts/codex-dispatch-template.md`: the
  worktree pin + verify sentence, spec path + AC + Eyeball log, explicit rules files to
  read, the §3 DoD contract **including the Scope and Report-length blocks**, the ticket key,
  the honest-report format, the model+effort pin.
- **Long runs.** MCP dispatches auto-background after a couple of minutes and deliver a
  completion notification. If a long stage hits the MCP idle-abort ("timed out"), the knob is
  `CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT` (ms, in `~/.claude/settings.json` `env`; `0` disables
  it — global to ALL MCP servers).

### 5.2.1 Dev-model fallback ladder

| Rung | Model | Surface |
|---|---|---|
| 1 | `gpt-6-astra` | Codex `mcp__codex__codex` |
| 2 | `gpt-5.6-sol` | Codex `mcp__codex__codex` |
| 3 | `claude-fable-5-1` | Claude Fable-5 `Agent` (last resort) |

- **Effort is ALWAYS `high` on every rung** — the ladder trades MODEL, never effort.
- **Step down ONLY on unavailability** (outage / quota / repeated dispatch failure), and only
  ONE rung, never by preference. The ladder is **not sticky** — every new dispatch re-attempts
  rung 1 first.
- **Rung 3 breaks cross-family review (O4).** `claude-fable-5-1` is Claude-FAMILY and cannot
  run on the Codex MCP surface — it dispatches as a Claude Fable-5 `Agent`, so dev + review go
  same-family. It is the LAST resort (both GPT rungs down): still run the T5 loop, but record
  the same-family limitation as an O7-style FINDING line and NEVER claim the review was
  cross-family.

### 5.3 Verify the lane was USED, not merely documented (O7)

Every dispatch writes `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`, whose first
`session_meta` line carries `cwd`, `source`, `originator` and the branch. **Two checks after
any Codex-lane work:** `source` is **`mcp`** (the only sanctioned surface; `exec` / `cli`
means a deprecated shell or hand dispatch — a FINDING, per O7), and `cwd` is the `<TICKET>-xx`
WORKTREE — a dispatch whose `cwd` is the main checkout violated O3 no matter what its report
says.

### 5.4 Hand-back convergence — a numbered gate, not a judgment call (T8)

**Assume every hand-back is UNCOMPILED and UNTESTED.** A fenced worker often cannot run the
real build at all (nested sandboxes, cache paths outside the fence), falls back to a syntax
parse, and reports something like *"static verification passed"* — which does **not**
type-check: a wrongly-inferred type sails straight through. Run these in order, every time:

0. **The worker lints itself** with whatever its sandbox DOES support, and hands back clean.
1. **`compilation-validator` FIRST** — a worker pinned to the worktree ABS path that does
   nothing but build the diff. **Never converge compile errors yourself, round by round, on
   the main thread** — that is exactly what this step prevents (one missing closure attribute
   surfacing at three call sites got blind-patched a round each).
2. **Then the orchestrator runs the impacted suites** (`docs/test-impact-map.md`) — it owns
   the serialized resources; a worker never takes one it wasn't granted.
3. **Only THEN the T5 review loop.** Reviewers read the **diff**, so they catch neither
   compile nor runtime failure — build+test is a SEPARATE gate that neither lane covers.

If the validator stops on a *semantic* call (a signature change, an optional-ing, an error
type spreading), that is an escalation to the main thread (D1) — never a licence to patch it
to green inline.

### 5.5 What routes to this lane

The DEFAULT is Codex for ALL implementation and mechanical
build-fix convergence, at FULL scope (parity / source-of-truth / cross-layer included — a
peer-capable family at high effort is not a tier downgrade, O2). Keep work on the Claude
side only when orchestration itself is the point (fix-loop bookkeeping) or the task is too
entangled with live conversation state to hand off cleanly.

### 5.6 Review integration (T5/O4) — Claude's review loop is the SINGLE binding gate

- Whoever authored the diff, **Claude's review→fix loop is the one binding machine gate**
  before the human gate (G4). A Codex worker's "green" is never the gate — the orchestrator
  re-verifies build/tests, and the loop reconverges after every fix round.
- A fresh read-only Codex pass (`sandbox: "read-only"`, or `codex review`) MAY join the
  finder fan-out as an extra CROSS-FAMILY finder, but never gates alone.
- Every finding passes the anti-hallucination check: it must name files actually in the
  diff, else dismiss. **A reviewer's citation is a claim, not evidence** — "I could not find
  it" is not "it does not exist"; check the branch and the call path before acting.

## 6. Worker isolation verification (O3)

Every spawned worker can silently edit the WRONG tree. Two hard rules:

- **Pin + instruct.** Pass the worktree ABSOLUTE PATH and say "operate ONLY inside this
  worktree"; a structured `cwd`/`-C` arg is necessary but NOT sufficient.
- **Verify after EVERY spawn.** `git status` the MAIN checkout for strays AND confirm the
  worktree `git diff <MAIN_BRANCH> --stat` names ONLY your intended files. A review
  loop's "clean" is worthless until the finding titles name YOUR files.

**A stale base amplifies all of this.** If a parallel session pushed `<MAIN_BRANCH>` after
your worktree was created, `git diff <MAIN_BRANCH>` shows that whole batch as *your* diff —
so re-fetch and rebase, then re-verify the diff scope, right before review AND right before
push. And when reverting strays from the main checkout, don't infer "no parallel session"
from a single process check (a session between turns shows no process): if main is being
actively re-written, STOP and surface it rather than playing whack-a-mole.

## 6.5 Naming a spawn so the agent list stays readable (O6)

Several workers run concurrently and the agent list is the only place the human sees them.

- **`Agent` spawns — you control the label.** Its `description` is the row text: write
  `<TICKET> <action>` (`AB-12 drive to green build`, `AB-12 fix round 2`). A description that
  doesn't name the ticket is indistinguishable once three are running.
- **`Workflow` runs — the label is FIXED and cannot be parameterized.** The tool's `title` /
  `description` inputs are ignored; the list shows `meta.description`, and `meta` must be a
  pure literal, so N concurrent runs of one workflow are IDENTICAL rows by construction. So:
  keep the FIRST SENTENCE of `meta.description` short and stage-naming, and make the run's
  first act a `log()` of its identity — `<TICKET> · round N · <worktree>`. That narrator line
  is the ONLY place concurrent instances are distinguishable.

---

Change-log:
- 2026-09-14 (human ruling): **removed §7 The CI gate (T9)** — these projects have no CI, so
  the terminal state is a push to the main branch after G4 with the suite green (T8). See
  CLAUDE.md close ritual + testing-strategy.md change-log.
- ported from a mature downstream project (2026-09 process work): §5.2 collapsed to **`mcp__codex__codex` as the
  ONLY surface** — shell `codex exec` DEPRECATED (removed the CLI table row, code block, and
  the heredoc/`< /dev/null` caveat); §5.2.1 **dev-model fallback ladder** added
  (`gpt-6-astra` → `gpt-5.6-sol` → `claude-fable-5-1`, effort ALWAYS high, step one rung on
  unavailability only, not sticky; rung 3 breaks O4 cross-family review → FINDING); primary
  pin bumped `gpt-5.6-sol` → `gpt-6-astra`; §5.3 provenance now expects `source: mcp`.
  CLAUDE.md O1/O2/T5 + README + AGENTS.md updated to match; `scripts/codex-dev.sh` (a
  `codex exec` wrapper) removed. Also shipped `scripts/tickets.sh` (git-backed board) +
  `worktree.sh` `new`/`land`/`rm` integration.
- (template) created — orchestrator pattern, Eyeball log, DoD contract, effort routing,
  the Codex worker lane, isolation verification.
- ported from a mature downstream project: §5 dispatch moved shell `codex exec` → the `mcp__codex__codex`
  MCP tool (`codex-reply` to continue a thread; `approval-policy:never` for non-interactive;
  report-to-file; shell form kept as fallback). Codex re-scoped from a bounded secondary
  lane to the DEFAULT dev lane at full scope; §4 family knob clarified as orthogonal to
  model tier. CLAUDE.md O2/O4/O5 + AGENTS.md updated to match.
- ported from a mature downstream project (2026-07-28 sweep of its process work):
  - §5.1 **install the codex MCP server during bootstrap** — a missing one degrades to a
    same-family fallback SILENTLY and voids every cross-family claim (it ran undetected for
    ten days there). The fallback flag is a finding, not a footnote.
  - §5.2 **two standing surfaces, picked by caller** (MCP inside agents, `codex exec -o`
    from the main thread) — replaces "MCP primary, shell fallback"; CLI prompt passing
    corrected to `"$(cat file)" < /dev/null`.
  - §5.3 **provenance check** (`session_meta` `cwd`/`source`/`originator`) → CLAUDE.md O7.
  - §5.4 **hand-back convergence gate** (lint → compilation-validator → suites → review
    loop; a syntax parse is not a compile) → CLAUDE.md T8.
  - §7 **the CI gate** (pushed ≠ done; cancelled ≠ green; worktree+ticket are the gate's
    state; fix budget in a file) → CLAUDE.md T9.
  - §3 DoD contract gained the **Scope** and **Report length** blocks, pasted on EVERY
    dispatch — Claude gets that discipline from its system prompt, Codex inherits none.
  - §1 fan-out/serialize rhythm, "≥3 defects sharing a root cause → re-solve, don't patch",
    and context-per-turn as the first-order cost lever; §6 stale-base amplification;
    §6.5 spawn naming → CLAUDE.md O6.
