---
description: Break a plan (docs/plans/<slug>.md or docs/plans/<slug>/) or the current conversation into tracer-bullet tickets with blocking edges under the plan folder. Argument = plan path or topic; empty = most recent plan.
args: "[plan path] (empty → most recent plan)"
drives: "mp-to-tickets skill"
---

Run the **mp-to-tickets** skill.

- `$ARGUMENTS` = a plan path (`docs/plans/<slug>.md`, `docs/plans/<slug>/`, or its
  `README.md`) or a topic. **Empty** → use the most recent plan (a file or a folder, by
  `updated:`).
- If the plan is a **file**, the skill first promotes it to the folder form
  (`git mv docs/plans/<slug>.md docs/plans/<slug>/README.md`, rewriting the plan's
  outgoing links for the extra depth and the incoming links from other docs) — the
  folder is created **only here**.
- The skill then drafts tracer-bullet vertical slices (each sized to one fresh context
  window / one `/ship` run), gives each its blocking edges, quizzes the user on
  granularity and edges, and writes `docs/plans/<slug>/NN-<ticket>.md` (blockers first)
  plus the plan's `Tickets` section.
- It lints + reindexes and stops. It does NOT launch `/ship` — the operator starts it by
  hand, one ticket at a time.
