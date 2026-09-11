---
name: mp-grill-with-docs
description: A relentless interview to sharpen a plan or design, which also builds the project's domain model (CONTEXT.md glossary + decision docs) as it goes, ending in a plan contract (docs/plans/<slug>.md). Use when the user wants to stress-test a plan or idea before implementing.
disable-model-invocation: true
---

Run a **grilling** session with **domain-modeling** active: interview the user
relentlessly about the plan/decision/idea, and as terms and decisions crystallise,
capture them inline — glossary terms into `CONTEXT.md`, load-bearing choices into
`docs/decisions/` (per the `domain-modeling` skill).

When the frontier is empty and shared understanding is reached, write the outcome as a
**plan contract**:

1. Search the KB first (`python3 .omp/skills/kb-search/gitmark.py search "<topic>"`) — if
   a plan for this topic already exists, edit it, don't duplicate.
2. Create/update the plan **file** `docs/plans/<slug>.md` — `node_type: plan`,
   `status: draft`, body fields `Goal`, `Done`, `Scope`, `Constraints`, `Context`
   (see `kb-curate`). No `Tasks` or `Tickets` section — the decomposition, if any,
   is the tickets, added later by `mp-to-tickets`. A plan is a file until
   `/to-tickets` turns it into a folder; do NOT create the folder yourself.
3. Lint + reindex (`gitmark.py lint`, `gitmark.py index`).
4. Report the path and **stop**. Do NOT author tickets and do NOT launch `/ship` — the
   operator runs `/to-tickets` (or says "разбей на тикеты") and then starts `/ship` by
   hand.
