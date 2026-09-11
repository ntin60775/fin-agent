---
description: Grill the user relentlessly about a plan, decision, or idea — rounds over the design tree until shared understanding, building the domain model (CONTEXT.md + decisions) as it goes, ending in a plan contract file (docs/plans/<slug>.md). Argument = the topic to grill.
args: "<topic> (empty → ask what to grill)"
drives: "mp-grill-with-docs skill"
---

Run the **mp-grill-with-docs** skill on the topic: `$ARGUMENTS`.

- `$ARGUMENTS` = the plan, decision, or idea to stress-test. **Empty** → ask what to grill.
- The skill holds the discipline: a `grilling` session (design tree, rounds, frontier —
  the agent finds facts itself, the decisions are the user's) with `domain-modeling`
  active — glossary terms go to `CONTEXT.md`, load-bearing choices to `docs/decisions/`
  as they crystallise.
- When the frontier is empty and the user confirms shared understanding, it writes the
  **plan contract** — `docs/plans/<slug>.md` (`node_type: plan`, `status: draft`) — and
  stops. It writes a file, not a folder: the folder with tickets is created later, only
  by `/to-tickets`.
- It does NOT author tickets and does NOT launch `/ship` — the operator runs
  `/to-tickets` (or says "разбей на тикеты") and then starts `/ship` by hand.
