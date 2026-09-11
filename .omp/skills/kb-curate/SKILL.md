---
name: kb-curate
description: 'Rules for maintaining a markdown knowledge base (GitMark) — apply when adding, editing, moving, or deleting documentation (.md). A lightweight code-ontology: every document has a type, properties (frontmatter), and typed links. Keeps the KB structured instead of a pile of files. Use on "add a doc", "record a decision", "update the docs", "reorganize docs".'
---

# kb-curate — how to maintain the knowledge base (GitMark ontology)

Full model: `docs/ontology.md`. This skill is the operational checklist. Principle:
**md+git is the source of truth, with an ontology on top** (object types / properties /
links — inspired by Palantir Foundry/Gotham, but for documentation over code).

## Before writing — search, don't duplicate

```bash
python3 .omp/skills/kb-search/gitmark.py search "<topic>"
```
If the topic already exists — **edit the existing doc**, don't create a second one.

## When ADDING knowledge (CREATE)

1. **Pick a `node_type`**: `service` · `reference` · `runbook` · `gotcha` · `decision`
   · `plan` · `ticket` · `guide` · `report` · `index`. Unsure → spec = `reference`, how-to = `guide`.
2. **Put it in the right folder** (type → folder): service-specific →
   `docs/services/<svc>/`; cross-cutting → `docs/reference/`; ops procedure →
   `docs/ops/`; plan → `docs/plans/<slug>.md` (a file; `mp-to-tickets` promotes it to
   the folder `docs/plans/<slug>/` with `README.md` + `NN-<ticket>.md`); decision →
   `docs/decisions/`.
3. **Add frontmatter** (min `node_type`; for load-bearing docs also `title`, `service`,
   `status: active`, `updated: YYYY-MM-DD`):
   ```yaml
   ---
   node_type: runbook
   title: Deploy the gateway
   service: api
   status: active
   updated: 2026-06-06
   links:
     documents: [../../scripts/deploy.sh]
     depends_on: [../reference/architecture.md]
   ---
   ```
4. **Add ≥1 link** — to code (`documents`/`implemented_by`) or a sibling doc
   (`depends_on`/`relates_to`). No orphans.
5. **Add a line to the folder's `README.md`** (its index): `- [Title](file.md) — hook`.

## Writing a plan contract (`docs/plans/<slug>.md`)

A plan is a **file** `docs/plans/<slug>.md` (`node_type: plan`) until it is broken into
tickets. `mp-to-tickets` is the only step that creates the **folder form**
`docs/plans/<slug>/`: it moves the file to `docs/plans/<slug>/README.md` (`git mv`,
history preserved, links rewritten for the extra depth) and adds the **tickets**
(`NN-<ticket>.md`, `node_type: ticket`) — tracer-bullet vertical slices, each declaring
the tickets that block it. `/ship` executes one ticket at a time, strictly sequentially;
a file plan is shipped as a single slice.

Plan contract body fields:

- `Goal` — why (one clear goal)
- `Done` — observable done-criterion
- `Scope` — files/services touched (required)
- `Constraints` — stop-points: `stop-before-commit`, `stop-after-mr`, `no-deploy`
- `Context` — what the entry phase established (root cause, prototype verdict, resolved branches)
- `Tickets` — the decomposition, in order, with status (added by `mp-to-tickets`, folder form only)

Ticket body fields: `What to build` (end-to-end behaviour), `Blocked by` (ticket
numbers), acceptance criteria.

Lifecycle: plan `draft` (written by `mp-grill-with-docs`) → `active` (`/ship` started)
→ `archived` (shipped as a single slice, or all tickets done). Tickets: `draft` (written
by `mp-to-tickets`) → `active` (being shipped) → `archived` (shipped). Only
`mp-grill-with-docs` authors a plan contract and `mp-to-tickets` authors tickets; entry
skills feed `Context`/`Goal`, never the contract.

## When EDITING (UPDATE)

- Meaning changed → bump `updated:`. Doc is stale → `status: deprecated` and set
  `supersedes: [old.md]` on the replacement. Junk → delete (git keeps history).

## When MOVING (reorganizing)

- `git mv` (preserves history), then **rewrite every link** to it and update the
  README indexes of both folders.

## Always at the end

```bash
python3 .omp/skills/kb-search/gitmark.py lint     # invariants I1–I6
python3 .omp/skills/kb-search/gitmark.py index    # rebuild search
```
`lint` flags: missing/broken frontmatter, type outside vocabulary, orphans (0 links),
broken links, folder without README. Fix until clean.

## Vocabularies (don't invent values)

- `node_type`: service|reference|runbook|gotcha|decision|plan|ticket|guide|report|index
- `status`: active|draft|deprecated|archived
- `service`: your project's controlled vocabulary (define it in `docs/ontology.md`)
