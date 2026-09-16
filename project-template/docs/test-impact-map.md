# Test impact map

Which suites to re-run when an area changes (Workflow B). Every shipped feature registers
its row at close-out (Workflow A step 6).

| Area / path | Targeted suites | Shared-layer suites |
|-------------|-----------------|---------------------|
| `scripts/tickets.sh` | `bash -n`; `pytest tests/test_tickets.py` (real CLI in disposable git repos, PTY frames); `python3 tests/tickets_width_test.py` (standalone width regression) | `tests/test_worktree.py` (board through land), `tests/test_context_ledger.py` (size/prefix contract) |
| `scripts/worktree.sh` | `bash -n`; `pytest tests/test_worktree.py` (local bare origins; ledger hooks) | `tests/test_context_ledger.py` |
| `scripts/context-ledger.sh`, `docs/slice-sizing.md`, `docs/context-ledger.md`, `scripts/batch-split-check-template.md`, `specs/_TEMPLATE.md` sizing section, CLAUDE.md gate table | `pytest tests/test_context_ledger.py` (CLI rows, process-API fixture + real ancestor walk, doc arithmetic and link contracts) | `tests/test_worktree.py`; the live `now` from a real session and the `/context` window confirmation stay orchestrator/G4 checks |
| | | |
