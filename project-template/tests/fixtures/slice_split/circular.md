# Circular split — expected VETO circular blocked_by

## SAMPLE-901
blocked_by: [SAMPLE-902]
### Acceptance criteria
1. `create` writes a ticket whose stored title equals the supplied title.

## SAMPLE-902
blocked_by: [SAMPLE-903]
### Acceptance criteria
1. `show` prints the title written by `create` byte-for-byte.

## SAMPLE-903
blocked_by: [SAMPLE-901]
### Acceptance criteria
1. `remove` deletes the named ticket while preserving another ticket's bytes.
