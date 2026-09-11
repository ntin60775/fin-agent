---
name: mp-to-tickets
description: Break a plan (docs/plans/<slug>.md or docs/plans/<slug>/) or the current conversation into tracer-bullet tickets with blocking edges, written as docs/plans/<slug>/NN-<ticket>.md. Use when the user says "разбей на тикеты" or runs /to-tickets.
disable-model-invocation: true
---

# To Tickets

Break a plan, spec, or conversation into a set of **tickets**: tracer-bullet vertical
slices, each declaring the tickets that **block** it.

Argument: `$ARGUMENTS` — a plan path (`docs/plans/<slug>.md`, `docs/plans/<slug>/`, or
its `README.md`) or a topic. **Empty** → use the most recent plan (a file
`docs/plans/<slug>.md` or a folder `docs/plans/<slug>/`, by `updated:`).

## Process

### 0. Migrate a file plan to a folder

A plan is a **file** (`docs/plans/<slug>.md`) until this skill runs. If the target plan
is a file, first promote it to the folder form — the folder is created **only here**:

1. `mkdir docs/plans/<slug>/` and `git mv docs/plans/<slug>.md docs/plans/<slug>/README.md`
   (preserves history).
2. **Rewrite the plan's own outgoing links**: it moved one level deeper — relative
   links in its frontmatter and body gain one `../` (e.g. `../../.omp/commands/ship.md`
   → `../../../.omp/commands/ship.md`; `../ontology.md` → `../../ontology.md`).
3. **Rewrite incoming links** from other docs that pointed at `docs/plans/<slug>.md`
   (grep the KB for `<slug>.md`) — they now point at `docs/plans/<slug>/README.md`.
4. Add a line to `docs/plans/README.md`'s index for the plan folder.

If the target is already a folder, skip this step.

### 1. Gather context

Read the plan contract (`Goal`, `Done`, `Scope`, `Context`) and the `Context` it
points at. Search the KB (`gitmark search`) for related docs and decisions. If you have
not explored the codebase, do so to understand the current state. Ticket titles and
descriptions use the project's domain glossary vocabulary (`CONTEXT.md`) and respect
decisions in `docs/decisions/`.

Look for opportunities to prefactor the code to make the implementation easier. "Make
the change easy, then make the easy change."

### 2. Draft vertical slices

Break the work into **tracer bullet** tickets.

- Each slice cuts a narrow but COMPLETE path through every layer (schema, API, UI,
  tests): vertical, NOT a horizontal slice of one layer.
- A completed slice is demoable or verifiable on its own.
- Each slice is sized to fit in a single fresh context window — one `/ship` run.
- Any prefactoring should be its own first ticket.

Give each ticket its **blocking edges**: the other tickets that must complete before it
can start. A ticket with no blockers can start immediately.

**Wide refactors are the exception to vertical slicing.** A wide refactor is one
mechanical change (rename a column, retype a shared symbol) whose blast radius fans
across the whole codebase, so no vertical slice can land green. Sequence it as
**expand–contract**: expand (add the new form beside the old), migrate call sites in
batches sized by blast radius (each batch its own ticket, blocked by the expand), then
contract (delete the old form, blocked by every migrate batch).

### 3. Quiz the user

Present the proposed breakdown as a numbered list. For each ticket, show:

- **Title**: short descriptive name
- **Blocked by**: which other tickets (if any) must complete first
- **What it delivers**: the end-to-end behaviour this ticket makes work

Ask the user:

- Does the granularity feel right? (too coarse / too fine)
- Are the blocking edges correct: does each ticket only depend on tickets that genuinely
  gate it?
- Should any tickets be merged or split further?

Iterate until the user approves the breakdown.

### 4. Publish the tickets

Write one file per ticket under the plan folder, numbered from `01` in dependency order
(blockers first): `docs/plans/<slug>/NN-<slug>.md`. One ticket per file, never a single
combined file.

Template:

```markdown
---
node_type: ticket
title: <Ticket title>
service: _platform
status: draft
updated: YYYY-MM-DD
links:
  part_of: [README.md]
  depends_on: [01-<blocker>.md]   # only if blocked by another ticket
---

# NN: <Ticket title>

**What to build:** the end-to-end behaviour this ticket makes work, from the user's
perspective, not a layer-by-layer implementation list.

**Blocked by:** the numbers/titles of the tickets that gate this one, or "None (can
start immediately)".

- [ ] Acceptance criterion 1
- [ ] Acceptance criterion 2
```

Also update the plan's `README.md` (the parent contract — the migrated file, or the
existing folder README): add a `Tickets` section listing the tickets in order with
their status, and set `status: active` only if the operator confirms shipping has
started (otherwise leave `draft`).
Avoid specific file paths or code snippets in tickets: they go stale fast. Exception: if
a prototype produced a snippet that encodes a decision more precisely than prose can
(state machine, reducer, schema, type shape), inline it and note briefly that it came
from a prototype. Trim to the decision-rich parts, not a working demo.

### 5. Lint + reindex

`gitmark.py lint` then `gitmark.py index`. Report the ticket list and **stop**. Do NOT
launch `/ship` — the operator starts it by hand, one ticket at a time.
