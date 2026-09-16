# Decision log

Every human decision made at gates G1–G3 (and any other human ruling that changes scope,
architecture, or process) gets a dated row. Specs reference rows instead of restating
them. This is what prevents re-litigating a settled decision three sessions later.

| Date | Gate | Decision | Context / spec |
|------|------|----------|----------------|
| YYYY-MM-DD | G1 | … | specs/NNNN |
| _2026-01-15_ | _G1 / AB-12_ | _re-split A→B→C: ship the spine (A) first, defer B/C_ | _specs/0012_ |
| _2026-01-18_ | _scope (CUT)_ | _drop find-object (M3) — not needed; dead code + assets removed (I4)_ | _AB-14_ |
| _2026-01-20_ | _G3 / AB-12_ | _Overrides the 01-15 routing call: adopt the single-queue design_ | _specs/0012_ |

_(Rows above in italics are format examples — delete them.) Idioms: `scope (CUT)` for a
dropped feature · `Overrides <date>` when a ruling supersedes an earlier one · `re-split` when
a scope walk restructures the slices · `<Gate> / <TICKET>` to tie a decision to its ticket._
