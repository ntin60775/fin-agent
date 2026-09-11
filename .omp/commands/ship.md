---
description: Run the OntoShip dev-flow on one ticket from a plan (docs/plans/<slug>/) or a whole file plan (docs/plans/<slug>.md) or an ad-hoc description — worktree → implement → tests → review → dev-tests → prod-tests → ship. One ticket (or one file plan) per run, strictly sequential. Launched only by hand.
args: "[plan path] / [ticket path] / <ad-hoc> (empty → most recent plan)"
drives: "dev-flow skill"
---

Drive one ticket through the **OntoShip dev-flow**.

## Entry

`$ARGUMENTS` is one of:

1. **A plan folder** (`docs/plans/<slug>/` or its `README.md`) — pick the **first
   ticket** (by `NN` order) whose `status` is not `archived`. If all tickets are
   archived, report the plan is complete and stop.
2. **A file plan** (`docs/plans/<slug>.md`, `node_type: plan`) — execute the plan as a
   **single slice**: the full loop on the plan's `Goal`/`Done`/acceptance criteria, no
   tickets. Mark the plan `status: archived` once merged.
3. **A ticket path** (`docs/plans/<slug>/NN-<ticket>.md`) — execute that specific ticket.
4. **An ad-hoc description** — run the full loop without a ticket.
5. **Empty** — take the most recent plan (a file `docs/plans/<slug>.md` or a folder
   `docs/plans/<slug>/`, by `updated:`); a file plan is shipped as a single slice, a
   folder plan via its first non-archived ticket.

`docs/plans/<slug>` without an extension resolves to whichever exists: the file or the
folder.

**Verification (ticket entry):** read the ticket; check its `Blocked by` tickets are all
`archived` (if not, report which blockers remain and stop). Check `Context` in the parent
contract is still accurate (code hasn't drifted) and update it if not, re-confirming with
the operator. Set the ticket `status: active`. The operator's hand launch *is* the
confirmation.

**Verification (file-plan entry):** read the plan; check `Context` is still accurate
(code hasn't drifted) and update it if not, re-confirming with the operator. Set the plan
`status: active`. The operator's hand launch *is* the confirmation.

## The loop

1. **Research** — understand from facts (logs, traces, code); reproduce before fixing.
2. **Goal** — take from the ticket's `What to build` + acceptance criteria (or the plan's `Goal`/`Done` for a file plan).
3. **Isolate** — work in a dedicated `git worktree`.
4. **Implement** — code to the ticket's acceptance criteria.
5. **Tests** — write/adjust unit + E2E.
6. **Independent review** — run the bundled `reviewer` omp sub-agent (read-only) over
   the diff, on the `@reviewer` model role (fallback `@slow`).
7. **Dev-tests** — MR + commits into `dev`; run the full suite. Red → fix, don't merge.
8. **Prod-tests** — E2E/smoke against the real prod contour.
9. **Ship** — merge `dev → main` and deploy (build-before-stop + healthcheck-poll).
   Mark the ticket (or the file plan) `status: archived` once merged.

## Stop-points (from the parent contract's `Constraints`)

- `stop-before-commit` — after review, stop with the uncommitted diff in the worktree;
  commit and everything after wait for the operator's "continue". Default for 1C projects.
- `stop-after-mr` — after opening the MR, stop for the operator's review.
- `no-deploy` — skip the deploy step.

A stop-point is a hard pause: report and wait, never resume on your own.

## Sequential discipline

One ticket per `/ship` run. After a ticket is archived, the operator launches `/ship`
again for the next one. Never batch multiple tickets in one run.

Keep the gates (tests + independent review). Don't skip the spec — it's the carrier of
knowledge, not a throwaway ticket.
