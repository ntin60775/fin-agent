---
name: domain-modeling
description: Build and sharpen the project's domain model — challenge terms against CONTEXT.md, stress-test with edge-case scenarios, and update CONTEXT.md and docs/decisions/ inline. Use when discussing codebase terminology, writing or editing CONTEXT.md, or recording or editing a decision.
---

# Domain Modeling

Actively build and sharpen the project's domain model as you design. This is the *active*
discipline: challenging terms, inventing edge-case scenarios, and writing the glossary and
decisions down the moment they crystallise. (Merely *reading* `CONTEXT.md` for vocabulary
is not this skill: that's a one-line habit any skill can do. This skill is for when you're
changing the model, not just consuming it.)

## File structure

- **`CONTEXT.md`** (repo root) — the glossary: the project's ubiquitous language. Terms
  with definitions and `_Avoid_` lists. Devoid of implementation details — a glossary and
  nothing else.
- **`docs/decisions/`** — decision docs (`node_type: decision`), one per load-bearing
  choice. Follow `kb-curate` for frontmatter and links.

Create files lazily: only when you have something to write. If no `CONTEXT.md` exists,
create one when the first term is resolved. If no `docs/decisions/` exists, create it when
the first decision is needed.

## During the session

### Challenge against the glossary

When the user uses a term that conflicts with the existing language in `CONTEXT.md`, call
it out immediately. "Your glossary defines 'cancellation' as X, but you seem to mean Y.
Which is it?"

### Sharpen fuzzy language

When the user uses vague or overloaded terms, propose a precise canonical term. "You're
saying 'account': do you mean the Customer or the User? Those are different things."

### Discuss concrete scenarios

When domain relationships are being discussed, stress-test them with specific scenarios.
Invent scenarios that probe edge cases and force the user to be precise about the
boundaries between concepts.

### Cross-reference with code

When the user states how something works, check whether the code agrees. If you find a
contradiction, surface it: "Your code cancels entire Orders, but you just said partial
cancellation is possible. Which is right?"

### Update CONTEXT.md inline

When a term is resolved, update `CONTEXT.md` right there. Don't batch these up: capture
them as they happen. Use the existing format: a bold term, a definition, and an `_Avoid_`
line. `CONTEXT.md` should be totally devoid of implementation details. Do not treat it as
a spec, a scratch pad, or a repository of implementation decisions. It is a glossary and
nothing else.

### Offer decisions sparingly

Only offer to create a decision doc when all three are true:

1. **Hard to reverse**: the cost of changing your mind later is meaningful
2. **Surprising without context**: a future reader will wonder "why did they do it this way?"
3. **The result of a real trade-off**: there were genuine alternatives and you picked one
   for specific reasons

If any of the three is missing, skip the decision. Write it per `kb-curate`
(`node_type: decision`, `status: active`, typed links, folder README index line), then run
`gitmark lint` and `gitmark index`.
