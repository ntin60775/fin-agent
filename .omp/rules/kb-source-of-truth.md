---
description: md+git is the source of truth; derived artifacts are regenerated, never edited or committed.
alwaysApply: true
---

# KB source of truth

The project knowledge base is plain **markdown + README indexes + git**. Everything
**derived** — the search index (`.gitmark/`) and the HTML overview/graph (`*-map.html`,
e.g. `docs-map.html`) — is **regenerated** from md by the GitMark CLI:

```bash
python3 .omp/skills/kb-search/gitmark.py index
python3 .omp/skills/kb-search/gitmark.py map -o docs-map.html
```

- NEVER edit derived artifacts by hand; NEVER commit `.gitmark/` or `*-map.html`.
- Edit knowledge only in `.md` files; re-run `index` (and `map` when the structure
  changed) after editing docs.
- The entry point is `AGENTS.md`; every folder's `README.md` is its index.
