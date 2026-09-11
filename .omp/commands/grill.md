---
description: "Grill the user relentlessly about a plan, decision, or idea — rounds over the design tree until shared understanding, WITHOUT writing anything to the KB (no CONTEXT.md, no decisions, no plan contract). Русские триггеры: погрилл, погриль меня, грилл. Argument = the topic to grill."
args: "<topic> (empty → ask what to grill)"
drives: "grilling skill"
---

Run the **grilling** skill on the topic: `$ARGUMENTS`.

- `$ARGUMENTS` = the plan, decision, or idea to stress-test. **Empty** → ask what to grill.
- The skill holds the discipline: design tree, rounds, frontier — the agent finds facts
  itself (read-only scout sub-agents), the decisions are the user's. Done when the frontier is empty and
  the user confirms shared understanding.
- This is the **plain** grill — an optical entry, not an alternative output. It writes
  NOTHING durable: no `CONTEXT.md` terms, no `docs/decisions/`, no plan contract. The
  whole resolution lives and dies in the session context.
- If the conclusions turn out to be worth keeping, the operator re-runs `/grilling`
  (`mp-grill-with-docs`) on the same thread — that variant builds the domain model
  inline and ends in a plan contract (`docs/plans/<slug>.md`).
- It does NOT author tickets, does NOT write the plan contract, does NOT launch `/ship`.
