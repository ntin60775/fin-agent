---
name: mp-diagnose
description: Diagnosis loop for hard bugs and performance regressions — find the root cause and a minimal repro, then hand the fix to /ship. Use when the user says "diagnose"/"debug this", or reports something broken/throwing/failing/slow.
---

# Diagnose

A discipline for hard bugs: build a tight red/green feedback loop, reproduce, minimise,
hypothesise, instrument — then **report the root cause, don't fix it here**. The fix goes
through `dev-flow` (code changes happen only via `/ship`).

Skip phases only when explicitly justified.

## Redact

This skill has you show commands, outputs and captured artifacts. **Redact every secret
first** — write `<REDACTED>` in its place. Build loops against env vars, so the credential
stays in the environment. Captured artifacts carry auth headers: quote only the lines that
carry the signal.

## Phase 1 — Build a feedback loop

**This is the skill.** A tight pass/fail signal that goes red on *this* bug is what
bisection, hypothesis-testing and instrumentation all consume. Without one, staring at
code won't save you.

Try in roughly this order: (1) failing test at the seam that reaches the bug; (2) curl/HTTP
script against a running server; (3) CLI invocation with a fixture input, diffing stdout;
(4) headless-browser script asserting on DOM/console/network; (5) replay a captured trace;
(6) throwaway harness (minimal subset, mocked deps); (7) property/fuzz loop for
"sometimes wrong" bugs; (8) bisection harness for regressions between two states;
(9) differential loop (old vs new version); (10) HITL script as last resort
(`scripts/hitl-loop.template.sh`).

**Tighten it** once it exists: faster, sharper signal (assert the exact symptom), more
deterministic (pin time/seed, isolate fs/network).

**Non-deterministic bugs:** the goal is a higher reproduction rate, not a clean repro —
loop 100×, parallelise, add stress.

**Completion:** one command, already run at least once (shown redacted), that is
red-capable, deterministic, fast (seconds), and agent-runnable. If you can't build one,
stop and say so — ask for the environment, a captured artifact, or temporary prod
instrumentation. Do not hypothesise without a loop.

## Phase 2 — Reproduce + minimise

Run the loop; watch it go red. Confirm it's the *user's* failure mode, not a nearby one.
Shrink the repro to the smallest scenario that still goes red — cut inputs/callers/config
one at a time, re-running each cut. A minimal repro shrinks the hypothesis space and
becomes the regression test later.

## Phase 3 — Hypothesise

Generate **3–5 ranked, falsifiable hypotheses** before testing any. Format: "If <X> is the
cause, then <changing Y> makes it disappear / <changing Z> makes it worse." Show the ranked
list to the user before testing (cheap checkpoint; proceed with your ranking if AFK).

## Phase 4 — Instrument

Each probe maps to a specific prediction; change one variable at a time. Prefer
debugger/REPL over logs; tag every debug log `[DEBUG-<id>]` for one-grep cleanup. For perf
regressions, measure first (baseline → bisect), fix second.

## Phase 5 — Report, don't fix

Diagnosis ends at the root cause. **Do not fix the code here** — code changes go through
`/ship`. Produce, for the operator:

- **Root cause** — the single cause, stated as a falsifiable claim.
- **Minimal repro** — the smallest scenario that still goes red (becomes the regression test).
- **Recommended fix** — what the change should be, and where the correct test seam is.
- **Prevention** — what would have stopped this bug (architectural note → hand to
  `mp-improve-codebase-architecture`).

Then hand off: `/grilling` (mp-grill-with-docs) turns this into a plan contract, then
`/to-tickets`, then `/ship` one ticket (or the file plan) at a time. After the fix
lands, add a `gotcha` in the KB (root cause + how to avoid).
