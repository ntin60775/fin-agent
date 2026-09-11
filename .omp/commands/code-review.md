---
description: Two-axis review of the diff since a fixed point — Standards (repo rules + Fowler smell baseline) and Spec (originating plan/ticket), run as parallel sub-agents, reported side by side. Argument = the fixed point (commit, branch, tag, merge-base).
args: "<fixed-point> (empty → ask)"
drives: "mp-code-review skill"
---

Run the **mp-code-review** skill on the diff since: `$ARGUMENTS`.

- `$ARGUMENTS` = the fixed point (`main`, a SHA, a tag, `HEAD~5`, …). **Empty** → ask for it.
- The skill holds the discipline: pin the fixed point and confirm the diff is non-empty,
  find the spec in the KB (`docs/plans/`), collect the standards sources
  (`.omp/rules/`, `AGENTS.md`, `CONTEXT.md`, `docs/decisions/`), then run **both axes as
  parallel sub-agents** and report them side by side — never merged, never reranked
  across axes.
- Output: a report in the chat plus `.scratch/code-review-<timestamp>.md` (ephemeral, not
  a KB doc). Secrets are redacted.
- It is **read-only**: it does not fix code, does not author the plan contract, does not
  launch `/ship`. If the operator decides to fix, the findings go to `/grilling` →
  `/to-tickets` → `/ship`.
