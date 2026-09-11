---
name: dev-flow
description: 'The spec-driven development loop for shipping a feature/fix fast and safely on top of a GitMark KB — one ticket (or one file plan) at a time: worktree → implement → tests → independent review → dev-tests → prod-tests → ship (MR → dev → main). Use when starting a feature or fix, or when asked "how do we build/ship a change here".'
---

# dev-flow — from ticket to production

A battle-tested loop for shipping with an AI agent: one ticket reaches production in
roughly **40 minutes to 2 hours**. The spec is plain **markdown written via the KB
skills**, the work happens in an **isolated git worktree**, and **only green reaches
prod**. The KB (see `kb-curate`) is the carrier of knowledge — onboarding, hand-off and
scaling all start from it, not from the code.

## Entry: ticket, file plan, or ad-hoc

`/ship` is launched by hand with one of:

1. **A plan folder** (`docs/plans/<slug>/`) — pick the **first ticket** (by `NN` order)
   whose `status` is not `archived`. If all are archived, the plan is complete.
2. **A file plan** (`docs/plans/<slug>.md`, `node_type: plan`) — execute the plan as a
   **single slice**: the full loop on the plan's `Goal`/`Done`/acceptance criteria, no
   tickets. Mark the plan `status: archived` once merged.
3. **A ticket path** (`docs/plans/<slug>/NN-<ticket>.md`) — execute that ticket.
4. **An ad-hoc description** — run the full loop without a ticket.
5. **Empty** — most recent plan (a file or a folder, by `updated:`); a file plan is
   shipped as a single slice, a folder plan via its first non-archived ticket.

`docs/plans/<slug>` without an extension resolves to whichever exists: the file or the
folder.

**Verification (ticket entry):** read the ticket; check `Blocked by` tickets are all
`archived` (if not, report blockers and stop). Check `Context` in the parent contract is
still accurate; update if not, re-confirming with the operator. Set ticket `status:
active`. The operator's hand launch *is* the confirmation (no second gate).

**Verification (file-plan entry):** read the plan; check `Context` is still accurate;
update if not, re-confirming with the operator. Set the plan `status: active`. The
operator's hand launch *is* the confirmation.

## The loop

1. **Research** — understand from facts, not guesses: read logs, traces, and the code
   itself. Reproduce before fixing.
2. **Goal** — take from the ticket's `What to build` + acceptance criteria (or the plan's `Goal`/`Done` for a file plan).
3. **Isolate** — work in a dedicated **`git worktree`**: `main` stays untouched, parallel
   agents don't collide, and rollback is just dropping the worktree.
4. **Implement** — code to the ticket's acceptance criteria inside the worktree; keep
   doc↔code linked (`implemented_by`).
5. **Tests** — write/adjust unit + E2E for the ticket. The test is part of the feature,
   not an afterthought.
6. **Independent review** — run the bundled **`reviewer`** omp sub-agent (read-only)
   over the diff for logic and security bugs before rollout, on the `@reviewer` model
   role when configured (`modelRoles.reviewer`), falling back to `@slow`. A second
   model catches what the author's model misses — on a real production codebase this
   pass caught **191 bugs** before they reached prod.
7. **Dev-tests** — open an **MR with the commits into the `dev` branch**; run the full
   suite there. Red → fix in the worktree, don't merge.
8. **Prod-tests** — E2E/smoke against the **real prod contour**, not only mocks or dev.
   Verify behaviour where users live.
9. **Ship** — merge **`dev → main`** and deploy (build the new image *before* stopping the
   old container, then poll the healthcheck to measure real downtime). Mark the ticket
   (or the file plan) `status: archived` once merged.

## Stop-points (from the plan contract's `Constraints`)

- `stop-before-commit` — after review (step 6), stop with the uncommitted diff in the
  worktree; commit and everything after wait for the operator's "continue". Default for
  1C projects (see the `ship-1c` rule).
- `stop-after-mr` — after opening the MR (step 7), stop for the operator's review.
- `no-deploy` — skip the deploy in step 9.

A stop-point is a hard pause: the agent reports and waits, it never resumes on its own.

## Sequential discipline

One ticket (or one file plan) per `/ship` run. After a ticket is archived, the operator
launches `/ship` again for the next. Never batch multiple tickets in one run.

## Git-flow

```
git worktree  →  MR + commits  →  dev branch (dev-tests)  →  main (prod-tests + deploy)  →  prod
```

## Principles

- **Ticket = one fresh context window.** Each ticket is sized to fit in a single agent
  session — tracer-bullet, vertical, demoable on its own.
- **Spec = markdown + skills.** No ceremony, but ontological — so it stays searchable,
  linkable and graphable (`kb-search` / `gitmark map`).
- **Worktree isolation by default** — clean parallelism and clean rollback.
- **Tests and independent review are gates, not afterthoughts.**
- **Verify on prod**, not just dev.
- **md + git = source of truth**; everything derived (search index, graph) is regenerated.

## When to apply

Starting any non-trivial feature or fix. For a one-line change you can collapse steps,
but keep the gates (review + tests) — they are where the 191 bugs were caught.
