# Clean split — expected no VETO

## SAMPLE-901
blocked_by: []
### Acceptance criteria
1. The new command `create KEY TITLE` writes KEY's ticket with exactly TITLE and status todo.

## SAMPLE-902
blocked_by: [SAMPLE-901]
### Acceptance criteria
1. `scripts/tickets.sh show KEY` prints the stored title and status byte-for-byte and leaves the ticket unchanged.

## SAMPLE-903
blocked_by: [SAMPLE-901]
### Acceptance criteria
1. The new command `remove KEY` deletes KEY's ticket and preserves every other ticket's bytes.
