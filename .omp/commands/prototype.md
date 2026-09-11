---
description: Build a throwaway prototype to answer a design question — logic/state (single HTML file) or UI (radically different variations on one route). Returns data + a recommendation, never commits code. Argument = the question.
args: "<question> (empty → ask)"
drives: "mp-prototype skill"
---

Run the **mp-prototype** skill on the question: `$ARGUMENTS`.

- `$ARGUMENTS` = the design question to answer. **Empty** → ask what to prototype.
- The skill picks the branch: "does this logic/state model feel right?" → a single
  shareable HTML file (LOGIC.md); "what should this look like?" → several radically
  different UI variations on one route (UI.md).
- Throwaway from day one, trivial to run, no persistence by default, state surfaced
  after every action.
- It produces **data, not a decision**: the question, the verdict (confirmed / refuted /
  inconclusive), what was observed, and a recommendation. The decision belongs to the
  operator. It does NOT commit code — the validated decision goes into the KB (a
  `decision` doc), never the prototype.
