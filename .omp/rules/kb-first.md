---
description: Before answering questions about this project, search the knowledge base via kb-search instead of grepping blind; never duplicate an existing doc.
alwaysApply: true
---

# KB first

This repo ships a searchable knowledge base (`docs/` + READMEs). Before answering a
question about the project, its services, or its code — **search the KB first**:

```bash
python3 .omp/skills/kb-search/gitmark.py search "<topic>" -k 8
```

- `file:line · heading · snippet` results; open the 1–2 most relevant files.
- Typo/substring/non-Latin tolerant (trigram + fuzzy) — search with concrete nouns.
- When writing a new doc (`/doc`, `kb-curate`): search first, and if the topic exists —
  **edit the existing doc**, never create a second one.
