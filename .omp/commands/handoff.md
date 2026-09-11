---
description: Compact the current conversation into a handoff document under .scratch/ so another agent can continue the work. Argument = what the next session will be used for.
args: "[what the next session will do]"
drives: "mp-handoff skill"
---

Run the **mp-handoff** skill.

- `$ARGUMENTS` = what the next session will focus on (tailors the doc). **Empty** →
  summarize the whole conversation.
- The skill writes `.scratch/handoff-<slug>.md` — a bridge between sessions, not a KB
  doc: it references the KB (`docs/plans/`, `docs/decisions/`) instead of restating it,
  reports progress (not intent), names suggested skills, and redacts secrets/PII.
- It does NOT write the ship contract (`mp-grill-with-docs` authors it) and does NOT
  edit code (code changes go through `/ship`).
