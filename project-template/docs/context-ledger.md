# Context ledger

One row per measured ticket interval, from `worktree.sh new` to `worktree.sh rm`,
appended by `scripts/context-ledger.sh record` (see `docs/slice-sizing.md`). The table
starts empty; no historical backfill. Rows are committed by the next `worktree.sh land`.

| date | ticket | predicted | actual | ratio | compactions | turns | dispatches | window | notes |
|---|---|---|---|---|---|---|---|---|---|

## Legend

- **date:** local recording day; **ticket:** slice key; **predicted:** size frozen at start.
- **actual:** `(end input+cache − start input+cache)/(0.6 × window)`, two decimals.
  `> 1.0 (compacted?)` denotes a marker or a ≥30% drop heuristic; no numeric ratio.
- **ratio:** unrounded actual / predicted, displayed to two decimals; `n/a` if unavailable.
- **compactions:** events after start; a marker and the drop on the same or next fresh
  valid assistant record count once.
- **turns:** distinct valid `requestId` deltas; the active request at a cut belongs to the
  next interval. Synthetic errors do not contribute.
- **dispatches:** dispatch attempts, not verified fan-out. `Agent` and `mcp__codex__codex`
  starts count; `mcp__codex__codex-reply` continuations do not.
- **window:** the token window frozen at start; default 1000000, pending owner confirmation.
- **notes:** `unmeasured: <reason>` explains missing measurements; manual starts have
  `unknown` counters. `overlapping KEY` means inclusive intervals share context costs;
  do not sum overlapping rows as exclusive attribution. Only markers still open when a
  row is recorded are noted; the later-recorded row is not annotated for a closed marker.

The shared file lives in the primary checkout; the next `worktree.sh land` stages it
with the board. See [slice sizing](slice-sizing.md) for G0, calibration and limitations.
