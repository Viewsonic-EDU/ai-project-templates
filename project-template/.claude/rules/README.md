# Path-scoped rules

Detail files that auto-load into Claude Code's context when a matching file is read or
edited — the H2 routing target for "area-deep implementation detail that should
auto-surface while coding".

Format — markdown with a `paths:` glob in the frontmatter:

```markdown
---
paths: src/ui/**/*.tsx
---
# UI implementation detail
- rule …
```

Codex does NOT auto-load these — `AGENTS.md` instructs it to read matching files here
before editing, and every Codex dispatch names them explicitly
(`scripts/codex-dispatch-template.md`).
