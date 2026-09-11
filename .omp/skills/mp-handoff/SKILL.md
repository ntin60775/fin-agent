---
name: mp-handoff
description: Compact the current conversation into a handoff document for another agent to pick up — a bridge between sessions, not a source of knowledge. Use to continue work in a fresh session, or to hand a research/prototype result back to the main session.
disable-model-invocation: true
---

# Handoff

Write a handoff document so a fresh agent can continue the work. Save it under
`.scratch/handoff-<slug>.md` in the project — **not** the KB (`docs/`), and not the OS
temp dir.

## Rules

- **Reference, don't duplicate.** The durable part (goal, decisions, plan) already lives in
  the KB (`docs/plans/`, `docs/decisions/`); link it, never restate it.
- **Report progress, not intent.** The intent is the contract's job. Here: what was done,
  what remains, what's blocking, next steps.
- **Suggested skills** section — name the skills the next agent should invoke.
- **Redact** any secrets / PII.

## Research handoff (prototype / check in a fresh session)

When the handoff carries a hypothesis check back to the main session, structure it as:

- **Hypothesis** — what was being tested
- **Checked** — what was actually run / built
- **Result** — confirmed / refuted / inconclusive, with evidence
- **Recommendation** — for the decision-maker (not a decision)

The main session consumes this and continues the `/grilling` discussion.

## Do not

- Do not write the ship contract here — `mp-grill-with-docs` authors it.
- Do not edit code (code changes go through `/ship`).
