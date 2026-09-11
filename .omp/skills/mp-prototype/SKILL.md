---
name: mp-prototype
description: Build a throwaway prototype to answer a design question — returns data and a recommendation for the decision-maker, it does not commit anything to the code. Use when the user wants to sanity-check a state model or logic, or explore what a UI should look like.
---

# Prototype

A prototype is **throwaway code that answers a question**. The question decides the shape.

## Pick a branch

- **"Does this logic / state model feel right?"** → [LOGIC.md](LOGIC.md): a single shareable
  HTML file (free-play buttons + tabbed walkthroughs) that pushes the state machine through
  hard-to-reason-about cases.
- **"What should this look like?"** → [UI.md](UI.md): several radically different UI
  variations on one route, switchable via a URL param + floating bar.

If the question is ambiguous and the user isn't reachable, default to whichever matches
the surrounding code (backend → logic; page/component → UI) and state the assumption.

## Rules

1. **Throwaway from day one, clearly marked.** Locate it near where it'd be used; name it
   so a reader sees "prototype". No tests, no abstractions, only enough error handling to run.
2. **Trivial to run** — one command in the task runner, or a double-clicked HTML file.
3. **No persistence by default.** State in memory; a scratch DB/file named "PROTOTYPE — wipe me".
4. **Surface the state** after every action / variant switch.

## Finish — data, not a decision

The prototype answers a question; it does **not** decide or commit. Produce for the
decision-maker (the main session, or via `mp-handoff` if in a fresh session):

- the question asked, and the verdict (hypothesis confirmed / refuted / inconclusive);
- what was observed (state traces, screenshots, the prototype path);
- a recommendation — but the decision belongs to the operator, not here.

Return to the discussion: `/grilling` (mp-grill-with-docs) turns the outcome into a
plan contract, then `/to-tickets`, then `/ship`. The main branch
keeps only the validated decision (a `decision` doc in the KB), never the prototype.
