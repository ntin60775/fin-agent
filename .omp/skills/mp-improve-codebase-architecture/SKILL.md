---
name: mp-improve-codebase-architecture
description: Scan a codebase for deepening opportunities, present them as a visual HTML report, then grill through whichever one you pick — ending in an OntoShip plan contract (docs/plans/<slug>.md). Driven by the /architecture command.
disable-model-invocation: true
---

# Improve Codebase Architecture

Surface architectural friction and propose **deepening opportunities** — refactors that turn shallow modules into deep ones. The aim is testability and AI-navigability.

## Architecture vocabulary

This skill speaks one fixed vocabulary. Use these terms exactly in every suggestion — don't drift.

- **module** — a unit of code with an interface and an implementation
- **interface** — what a module exposes to its callers
- **implementation** — the code behind the interface
- **depth / deep / shallow** — a deep module has a small interface over a large implementation; a shallow module's interface is nearly as complex as its implementation
- **seam** — the place where two modules meet
- **adapter** — an implementation of an interface for a specific environment (HTTP in prod, in-memory in tests)
- **leverage** — the ratio of callers to a module's interface: one interface, many call sites
- **locality** — related code lives together, so bugs concentrate in one module

Never substitute: `component`, `service`, `unit` (for module) · `API`, `signature` (for interface) · `boundary` (for seam) · `layer`, `wrapper` (for module).

Three principles guide every candidate:

- **The deletion test** — would deleting a suspected-shallow module *concentrate* complexity, or just move it? "Yes, concentrates" is the signal.
- **The interface is the test surface** — test through the interface, never the internals.
- **One adapter = hypothetical seam, two = real** — a seam is justified only when it has (or will have) more than one adapter.

## Process

### 1. Explore

**Scope before you scan — YAGNI.** Deepening a module pays off by making future changes to it easier, so put extra weight on the parts of the codebase that have recently changed. Decide *where* to look before you look:

- If the user named a direction — a module, a subsystem, a pain point — take it, and skip the inference below.
- Otherwise, walk back a good stretch of the commit history (`git log --oneline`) to find the codebase's hot spots — the files and areas that keep coming up — and let those paths pull your attention first. If the changes are scattered with no clear hot spot, widen the net.

Search the KB first (`gitmark search`) for the project's domain vocabulary (`CONTEXT.md`) and any recorded decisions in the area you're touching — decisions live in `docs/decisions/` (`node_type: decision`), and this command should not re-litigate them.

Then spawn a sub-agent to walk the codebase. Don't follow rigid heuristics — explore organically and note where you experience friction:

- Where does understanding one concept require bouncing between many small modules?
- Where are modules **shallow** — interface nearly as complex as the implementation?
- Where have pure functions been extracted just for testability, but the real bugs hide in how they're called (no **locality**)?
- Where do tightly-coupled modules leak across their seams?
- Which parts of the codebase are untested, or hard to test through their current interface?

Apply the **deletion test** to anything you suspect is shallow.

### 2. Present candidates as an HTML report

Write a self-contained HTML file to the OS temp directory so nothing lands in the repo. Resolve the temp dir from `$TMPDIR`, falling back to `/tmp` (or `%TEMP%` on Windows), and write to `<tmpdir>/architecture-review-<timestamp>.html` so each run gets a fresh file. Open it for the user — `xdg-open <path>` on Linux, `open <path>` on macOS, `start <path>` on Windows — and tell them the absolute path.

The report uses **Tailwind via CDN** for layout and styling, and **Mermaid via CDN** for diagrams where a graph/flow/sequence reliably communicates the structure. Mix Mermaid with hand-crafted CSS/SVG visuals — Mermaid for graph-shaped relationships, hand-built divs/SVG for the editorial ones. Each candidate gets a **before/after visualisation**. Be visual.

For each candidate, render a card with:

- **Files** — which files/modules are involved
- **Problem** — why the current architecture is causing friction
- **Solution** — plain English description of what would change
- **Benefits** — explained in terms of locality and leverage, and how tests would improve
- **Before / After diagram** — side-by-side, custom-drawn, illustrating the shallowness and the deepening
- **Recommendation strength** — one of `Strong`, `Worth exploring`, `Speculative`, rendered as a badge

End the report with a **Top recommendation** section: which candidate you'd tackle first and why.

**Use the project's domain vocabulary for the domain, and the architecture vocabulary above for the architecture.** If `CONTEXT.md` defines "Order," talk about "the Order intake module" — not "the FooBarHandler," and not "the Order service."

**Decision conflicts**: if a candidate contradicts a recorded decision in `docs/decisions/`, only surface it when the friction is real enough to warrant reopening it. Mark it clearly in the card (e.g. a warning callout). Don't list every theoretical refactor a decision forbids.

See [HTML-REPORT.md](HTML-REPORT.md) for the full HTML scaffold, diagram patterns, and styling guidance.

Do NOT propose interfaces yet. After the file is written, ask the user: "Which of these would you like to explore?"

### 3. Grilling loop

Once the user picks a candidate, run the `/grilling` skill to walk the decision tree with them — constraints, dependencies, the shape of the deepened module, what sits behind the seam, what tests survive.

Side effects happen inline as decisions crystallize — record them in the KB via **`kb-curate`**:

- **Naming a deepened module after a concept not in the glossary?** Add the term to `CONTEXT.md`, then re-run `gitmark index`.
- **Sharpening a fuzzy term during the conversation?** Update `CONTEXT.md` right there.
- **User rejects the candidate with a load-bearing reason?** Offer to record it as a `decision` doc in `docs/decisions/` (`node_type: decision`, `supersedes`/`relates_to` links, folder README index) so future reviews don't re-suggest it — framed as: *"Want me to record this as a decision so future reviews don't re-suggest it?"* Only offer when the reason would actually be needed by a future explorer — skip ephemeral reasons and self-evident ones.
- **Want to explore alternative interfaces for the deepened module?** Spawn two sub-agents in parallel, each designing a different interface for the module, then compare and pick.

Run `gitmark lint` and `gitmark index` at the end of any KB write.

### 4. Close the loop in the KB

The grilling loop is `mp-grill-with-docs`: when the frontier is empty and the user
confirms shared understanding, it writes the **plan contract** —
`docs/plans/<slug>.md` (`node_type: plan`, `status: draft`) — then `gitmark lint` +
`gitmark index`.

Stop there. Do NOT refactor, do NOT author tickets, do NOT launch `/ship`: the operator
runs `/to-tickets` (optional) and then starts `/ship` by hand. Delivery of code happens
only through `/ship`.
