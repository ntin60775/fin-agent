---
name: mp-code-review
description: Review the changes since a fixed point (commit, branch, tag, or merge-base) along two axes — Standards (does the code follow this repo's documented standards?) and Spec (does the code match the originating plan/ticket?). Runs both reviews in parallel sub-agents and reports them side by side. Use when the user wants to review a branch, a PR, work-in-progress changes, or a named piece of code.
---

# Code review

Two-axis review of the diff between `HEAD` and a fixed point the user supplies:

- **Standards** — does the code conform to this repo's documented standards?
- **Spec** — does the code faithfully implement the originating plan contract / ticket?

Both axes run as **parallel sub-agents** so they don't pollute each other's context, then
this skill aggregates their findings.

The review is **read-only**: it never fixes code. Code changes go through `/ship` only.
The output is a report; acting on it is a separate decision the operator makes (step 6).

## Why two axes

A change can pass one axis and fail the other:

- Code that follows every standard but implements the wrong thing → **Standards pass, Spec fail.**
- Code that does exactly what the plan asked but breaks the project's conventions → **Spec pass, Standards fail.**

Reporting them separately stops one axis from masking the other.

## Redact

The report quotes diffs and code. **Redact every secret first** — write `<REDACTED>`. Never
carry values from `.env`, keys, tokens, or credential files into the report or the chat.

## Process

### 1. Pin the fixed point

Whatever the user said is the fixed point (a commit SHA, branch name, tag, `main`,
`HEAD~5`, …). If they didn't specify one, ask for it.

Capture the diff command once: `git diff <fixed-point>...HEAD` (three-dot, so the
comparison is against the merge-base). Note the commit list via
`git log <fixed-point>..HEAD --oneline`.

**Untracked work is invisible to that diff.** If the review target is a working tree
(not a branch), enumerate the extras with `git ls-files --others --exclude-standard` and
`git status --short`, and hand that file list to the sub-agents so they read those files
whole. Keep the review read-only: never `git add` (not even `-N`) to make files appear in
a diff. State in the report which scope was reviewed: committed history, working tree, or
both.

### 2. Identify the spec source

OntoShip keeps the spec in the KB, not in an external issue tracker. Look in this order:

1. A plan or ticket path the user passed (`docs/plans/<slug>.md`, `docs/plans/<slug>/NN-<ticket>.md`).
2. A plan/ticket matching the branch name or feature — `gitmark search "<feature>"`, then
   check `docs/plans/`.
3. A plan reference in the commit messages (`docs/plans/...`, a slug, a ticket number).
4. If nothing is found, ask the user where the spec is. If they say there isn't one, the
   **Spec** sub-agent skips and reports "no spec available".

### 3. Identify the standards sources

Anything in the repo that documents how code should be written:

- `.omp/rules/*.md` and the root `AGENTS.md` (the package's own rules ship with the repo);
- `CODING_STANDARDS.md` / `CONTRIBUTING.md` if present;
- `CONTEXT.md` — the ubiquitous language: naming that drifts from a defined term is a
  standards finding;
- `docs/decisions/` — a **recorded decision is not re-litigated**; code contradicting one
  is reported as a hard finding, citing the decision doc.

On top of what the repo documents, the Standards axis always carries the **smell baseline**
below: a fixed set of Fowler code smells (_Refactoring_, ch. 3) that applies even when a
repo documents nothing. Two rules bind it:

- **The repo overrides.** A documented repo standard always wins; where it endorses
  something the baseline would flag, suppress the smell.
- **Always a judgement call.** Each smell is a labelled heuristic ("possible Feature
  Envy"), never a hard violation. Like any standard here, skip anything tooling already
  enforces (formatter, linter, type checker).

Each smell reads *what it is* → *how to fix*; match it against the diff:

- **Mysterious Name**: a function, variable, or type whose name doesn't reveal what it does or holds. → rename it; if no honest name comes, the design's murky.
- **Duplicated Code**: the same logic shape appears in more than one hunk or file in the change. → extract the shared shape, call it from both.
- **Feature Envy**: a method that reaches into another object's data more than its own. → move the method onto the data it envies.
- **Data Clumps**: the same few fields or params keep travelling together (a type wanting to be born). → bundle them into one type, pass that.
- **Primitive Obsession**: a primitive or string standing in for a domain concept that deserves its own type. → give the concept its own small type.
- **Repeated Switches**: the same `switch`/`if`-cascade on the same type recurs across the change. → replace with polymorphism, or one map both sites share.
- **Shotgun Surgery**: one logical change forces scattered edits across many files in the diff. → gather what changes together into one module.
- **Divergent Change**: one file or module is edited for several unrelated reasons. → split so each module changes for one reason.
- **Speculative Generality**: abstraction, parameters, or hooks added for needs the spec doesn't have. → delete it; inline back until a real need shows.
- **Message Chains**: long `a.b().c().d()` navigation the caller shouldn't depend on. → hide the walk behind one method on the first object.
- **Middle Man**: a class or function that mostly just delegates onward. → cut it, call the real target direct.
- **Refused Bequest**: a subclass or implementer that ignores or overrides most of what it inherits. → drop the inheritance, use composition.

### 4. Spawn both sub-agents in parallel

**Standards sub-agent prompt** — include:

- The full diff command and commit list.
- The list of standards-source files found in step 3, **plus the smell baseline from step
  3** pasted in full (the sub-agent has no other access to it).
- The brief: "Report, per file/hunk where relevant, (a) every place the diff violates a
  documented standard: cite the standard (file + the rule); and (b) any baseline smell you
  spot: name it and quote the hunk. Distinguish hard violations from judgement calls:
  documented-standard breaches can be hard, but baseline smells are always judgement
  calls, and a documented repo standard overrides the baseline. Skip anything tooling
  enforces. Under 400 words."

**Spec sub-agent prompt** — include:

- The diff command and commit list.
- The path or fetched contents of the spec (plan contract / ticket).
- The brief: "Report: (a) requirements the spec asked for that are missing or partial;
  (b) behaviour in the diff that wasn't asked for (scope creep); (c) requirements that
  look implemented but where the implementation looks wrong. Quote the spec line for each
  finding. Under 400 words."

If the spec is missing, skip the Spec sub-agent and note this in the final report.

### 5. Aggregate

Present the two reports under `## Standards` and `## Spec` headings, verbatim or lightly
cleaned. Do **not** merge or rerank findings across axes — that separation is the point.

End with a one-line summary per axis: total findings, and the worst issue _within that
axis_ (if any). Never pick a single winner across axes.

Write the same report to `.scratch/code-review-<timestamp>.md` — an ephemeral artifact
like the handoff doc, **not** a KB doc (`docs/` is for durable knowledge only). Quote the
path back to the user.

### 6. Decision gate

Ask the operator what to do. Do not proceed on your own:

- **Nothing to fix** → stop; the report stays in `.scratch/`.
- **Fix it** → hand the findings to `/grilling` (`mp-grill-with-docs`), which turns them
  into a **plan contract** (`docs/plans/<slug>.md`, `node_type: plan`, `status: draft`);
  then `/to-tickets` if it needs slicing; then `/ship` — started by hand, one ticket at a
  time.
- **A finding is a durable trap** → offer a `gotcha` doc in the KB (via `kb-curate`).
- **A finding contradicts a recorded decision** → offer to update or supersede that
  `decision` doc, never to silently work around it.

## Do not

- Do not edit code — delivery happens only through `/ship`.
- Do not author a plan contract or tickets here — `mp-grill-with-docs` and `mp-to-tickets`
  own those.
- Do not launch `/ship`.
- Do not write the report into `docs/` — it is ephemeral (`.scratch/`).
