---
description: Code changes go through dev-flow only; the ship trigger is human-only.
alwaysApply: true
---

# Ship gate

- Code changes in this repo happen **only** through the `dev-flow` (`/ship`).
- Entry skills (`mp-grill-with-docs`, `mp-diagnose`, `mp-prototype`, `mp-handoff`,
  `mp-to-tickets`, `mp-code-review`, `mp-improve-codebase-architecture`) end in a plan
  contract (file or folder) / ticket under `docs/plans/`, a handoff/review report under
  `.scratch/` — they never edit code.
- `/ship` is launched **only by hand** by the operator, never by the agent on its own.
- One ticket (or one file plan) per `/ship` run, strictly sequential.
