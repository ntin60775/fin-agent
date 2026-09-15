---
node_type: reference
title: GitMark ontology — a knowledge model over code
service: _platform
status: active
updated: 2026-09-15
tags: [ontology, palantir, node-type, links, frontmatter]
links:
  relates_to: [reference/commands.md, ../CONTEXT.md]
---

# GitMark ontology — a knowledge model over code

> Rules for **how to maintain** a knowledge base (not just how to search it). The idea
> is borrowed from **Palantir's Ontology** (Gotham/Foundry): an organization is modeled
> as a graph of typed **objects**, their **properties**, and the **links** between them —
> a "digital twin." GitMark applies the same model to project documentation: every `.md`
> document is an **object** with a **type** and **properties**, and markdown links are
> **typed links**. The result is an ontology not of data, but of **knowledge over code**.

Why: so the KB doesn't rot into a pile of files. Type + properties + links make it
navigable, checkable (via the linter), and graphable (`gitmark map`).

## Semantic layer — Objects, Properties, Links

### Object types (`node_type`)

Each document has exactly one `node_type` — its "table" in the ontology.

| node_type | what it is | lives in |
|---|---|---|
| `service` | overview/index of one service/component | `docs/services/<svc>/README.md` |
| `reference` | cross-cutting spec (not about one service) | `docs/reference/` |
| `runbook` | operational procedure ("how to X") | `docs/ops/` |
| `gotcha` | a pitfall + how to avoid it | `docs/ops/` |
| `decision` | an architectural/product decision (ADR) | `docs/decisions/` |
| `plan` | a plan/design before implementation — the ship contract | `docs/plans/<slug>.md` (file; `mp-to-tickets` promotes it to `docs/plans/<slug>/README.md`) |
| `ticket` | a tracer-bullet vertical slice of a plan, one `/ship` run each | `docs/plans/<slug>/NN-<ticket>.md` |
| `guide` | how to use something (clients, public API) | varies |
| `index` | a folder's table of contents | any `README.md` |

Rule: if unsure, a spec is `reference`, a how-to is `guide`. Add a new type only if
none fit and there will be ≥3 such documents.

### Properties (frontmatter)

YAML frontmatter at the top of the file — the "columns" of the object row.

```yaml
---
node_type: service          # REQUIRED — one of the table above
title: Billing              # human-readable object name
service: billing            # which service; use a sentinel (e.g. _platform) for cross-cutting
status: active              # active | draft | deprecated | archived
updated: 2026-06-06          # last meaningful edit (YYYY-MM-DD)
tags: [payments, api]       # free-form labels for search/grouping
links:                      # typed links (see below), optional
  documents: [../../src/billing]
  depends_on: [../reference/architecture.md]
  supersedes: [old-billing.md]
---
```

Required: `node_type`. Strongly recommended for load-bearing docs
(`service|reference|runbook|plan|decision`): `title`, `service`, `status`, `updated`.

`service` is a **controlled vocabulary** you define for your project.

### Link types (`links`)

Links are markdown links `[text](path.md)`. The link type is declared by a key under
`links:`; inline links default to `relates_to`. `gitmark map` draws the graph from them.

| link type | meaning | direction |
|---|---|---|
| `documents` | this doc describes that code/service | doc → code |
| `depends_on` | read that one first to understand this | doc → doc |
| `supersedes` | replaces a stale document | new → old |
| `relates_to` | adjacent topic (default for inline links) | doc ↔ doc |
| `implemented_by` | where it lives in code | doc → source file |
| `part_of` | belongs to a larger index | doc → index |

The doc→code link (`documents`/`implemented_by`) is what makes this an ontology **over
code**: a document is explicitly tied to the files/component it describes.

## Kinetic layer — Actions (curation rules)

In Palantir, **Actions** sit on top of the semantics — what you can do with objects.
Here, Actions = the **curation procedures** a human/agent runs (see `kb-curate` skill):
CREATE → classify, place, frontmatter, link, index. UPDATE → bump `updated`/`status`.
DEPRECATE → `status` + `supersedes`. LINK → no orphans. REINDEX → `gitmark index`.

## The plan as a ship contract

A plan is a **file** `docs/plans/<slug>.md` (`node_type: plan`) until it is broken into
tickets. `mp-to-tickets` is the only step that creates the **folder form**
`docs/plans/<slug>/`: it moves the file to the folder's `README.md` (history
preserved, links rewritten for the extra depth) and adds the **tickets**
(`NN-<ticket>.md`, `node_type: ticket`) — tracer-bullet vertical slices, each declaring
the tickets that block it.

The plan contract carries the fields the entry skills produce and the dev-flow
consumes:

- `Goal` — why this change (one clear goal)
- `Done` — the observable done-criterion
- `Scope` — files/services touched (required)
- `Constraints` — stop-points: `stop-before-commit`, `stop-after-mr`, `no-deploy`
- `Context` — what the entry phase already established (root cause, prototype verdict,
  resolved design-tree branches)
- `Tickets` — the decomposition, in order, with status (folder form only, added by `mp-to-tickets`)

Each ticket carries: `What to build` (the end-to-end behaviour the slice makes work),
`Blocked by` (the tickets that gate it), `Status`, and acceptance criteria.

**`/ship` executes one ticket at a time, strictly sequentially.** `/ship <folder>` takes
the first ticket (by `NN` order) whose `status` is not `archived`; a finished ticket is
marked `archived`. A **file plan** (`/ship docs/plans/<slug>.md`) is shipped as a single
slice — the full loop on the plan's `Goal`/`Done`, no tickets.

**Lifecycle:** plan `draft` (written by `mp-grill-with-docs`) → `active` (`/ship`
started, operator-confirmed) → `archived` (shipped as a single slice, or all tickets
done). Tickets: `draft` (written by `mp-to-tickets`) → `active` (being shipped) →
`archived` (shipped). Only `mp-grill-with-docs` authors a plan contract and
`mp-to-tickets` authors tickets; `mp-diagnose`, `mp-prototype`, `mp-handoff`,
`mp-code-review`, `mp-improve-codebase-architecture` feed `Context`/`Goal` but never author
them — and never launch `/ship`: the operator starts it by hand. Their own output
(root cause, prototype data, handoff, review report, deepening report) is **ephemeral**:
`.scratch/` or the OS temp dir, never a KB doc.

## Invariants (checked by `gitmark lint`)

- **I1.** Every load-bearing doc has frontmatter with a valid `node_type`.
- **I2.** `node_type`/`service`/`status` values are within their vocabularies.
- **I3.** No orphans: a load-bearing doc has ≥1 incoming or outgoing link.
- **I4.** No broken links (a markdown link to a missing file).
- **I5.** Every `docs/**` folder has a `README.md` index.
- **I6.** A `supersedes` target has `status: deprecated|archived`.
- **I7.** The command registry is in sync: every `.omp/commands/*.md` has `args:`/`drives:`
  frontmatter, a row in the generated summary table and a `## /cmd` section in
  `docs/reference/commands.md` — and vice versa (checked by `gitmark inventory --check`).
- **I8.** The knowledge model has not drifted: `docs/ontology.md` and the package copy
  `skills/kb-curate/ontology.md` agree from the first `## ` heading onward (the title and the
  header notes may differ). ERR in the package's own repo, WARN in a consumer.

## Why this, not a wiki/Notion

- **md+git** is already the source of truth. The ontology adds *structure on top* without
  changing the medium — frontmatter and links are plain markdown, readable in any viewer.
- The object/link graph gives Foundry-like navigation with no platform: `gitmark map`
  renders it from the same files.
- Types + invariants keep the KB from degrading into a pile as it grows — the exact pain
  Palantir's ontology solves for data, applied here to knowledge over code.

Prototype model: [Palantir Ontology overview](https://www.palantir.com/docs/foundry/ontology/overview)
· [Core concepts](https://www.palantir.com/docs/foundry/ontology/core-concepts).
