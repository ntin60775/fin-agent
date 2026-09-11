---
description: Scan the codebase for deepening opportunities, present them as a visual HTML report, then grill through the chosen candidate — ending in a plan contract file (docs/plans/<slug>.md). Argument = optional direction (module, subsystem, or pain point).
args: "[direction] (empty → infer hot spots from git history)"
drives: "mp-improve-codebase-architecture skill"
---

Run the **mp-improve-codebase-architecture** skill on the direction: `$ARGUMENTS`.

- `$ARGUMENTS` = optional scope hint (a module, subsystem, or pain point). **Empty** →
  the skill infers hot spots from `git log` (YAGNI: weight where change is landing).
- The skill holds the discipline: KB-first (`CONTEXT.md` vocabulary; recorded decisions
  in `docs/decisions/` are not re-litigated), a sub-agent scan for architectural
  friction using the fixed deep-module vocabulary and the deletion test, then a
  self-contained **HTML report** written to the OS temp dir (never into the repo) with
  before/after candidate cards and a top recommendation.
- Once the user picks a candidate, the skill runs the `/grilling` loop
  (`mp-grill-with-docs`): the decision tree is walked, terms crystallise into
  `CONTEXT.md`, load-bearing choices into `docs/decisions/`, and the outcome is the
  **plan contract** — `docs/plans/<slug>.md` (`node_type: plan`, `status: draft`).
- It does NOT refactor anything and does NOT launch `/ship` — the operator runs
  `/to-tickets` (optional) and then starts `/ship` by hand.
