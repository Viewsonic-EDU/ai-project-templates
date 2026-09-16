# Vacuous split — expected VETO vacuously-true AC

## SAMPLE-901
blocked_by: []
### Acceptance criteria
1. The command runs without error.

## SAMPLE-902
blocked_by: [SAMPLE-901]
### Acceptance criteria
1. `show` prints the stored title byte-for-byte.

## SAMPLE-903
blocked_by: [SAMPLE-902]
### Acceptance criteria
1. `remove` deletes the named ticket and preserves other tickets' bytes.
