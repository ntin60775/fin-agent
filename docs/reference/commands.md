---
node_type: reference
title: Реестр команд и навыков
service: _platform
status: active
updated: 2026-09-11
links:
  part_of: [README.md]
---

# Реестр команд и навыков

Сводные таблицы **генерируются** из frontmatter файлов `.omp/` — руками их не
правят. Рассинхрон ловит линтер (I7), пересобрать — `gitmark inventory`.

```bash
python3 .omp/skills/kb-search/gitmark.py inventory          # перегенерировать
python3 .omp/skills/kb-search/gitmark.py inventory --check  # проверить (exit 1 при рассинхроне)
```

## Команды

<!-- BEGIN inventory:commands -->
| Command | What it does | Args | Drives |
|---|---|---|---|
| `/architecture` | Scan the codebase for deepening opportunities, present them as a visual HTML report, then grill through the chosen candidate — ending in a plan contract file (docs/plans/<slug>.md). Argument = optional direction (module, subsystem, or pain point). | [direction] (empty → infer hot spots from git history) | mp-improve-codebase-architecture skill |
| `/code-review` | Two-axis review of the diff since a fixed point — Standards (repo rules + Fowler smell baseline) and Spec (originating plan/ticket), run as parallel sub-agents, reported side by side. Argument = the fixed point (commit, branch, tag, merge-base). | <fixed-point> (empty → ask) | mp-code-review skill |
| `/doc` | Compose or update a knowledge-base document for the given topic following the OntoShip ontology (node_type, frontmatter, typed links, folder README index). Wraps the kb-curate skill. | <topic> | kb-curate skill + GitMark CLI |
| `/grill` | Grill the user relentlessly about a plan, decision, or idea — rounds over the design tree until shared understanding, WITHOUT writing anything to the KB (no CONTEXT.md, no decisions, no plan contract). Русские триггеры: погрилл, погриль меня, грилл. Argument = the topic to grill. | <topic> (empty → ask what to grill) | grilling skill |
| `/grilling` | Grill the user relentlessly about a plan, decision, or idea — rounds over the design tree until shared understanding, building the domain model (CONTEXT.md + decisions) as it goes, ending in a plan contract file (docs/plans/<slug>.md). Argument = the topic to grill. | <topic> (empty → ask what to grill) | mp-grill-with-docs skill |
| `/handoff` | Compact the current conversation into a handoff document under .scratch/ so another agent can continue the work. Argument = what the next session will be used for. | [what the next session will do] | mp-handoff skill |
| `/kb-map` | Build the OntoShip KB graph (gitmark map) — collapsible tree + rendered markdown + force/radial link graph as a self-contained HTML — and point the user to it. | [output-path] (default docs-map.html) | GitMark CLI map / index |
| `/kb` | Search the project knowledge base via GitMark (FTS5 bm25 + trigram/fuzzy). Argument = query; no argument shows stat. | <query> (empty → stat + usage) | GitMark CLI search / stat / index |
| `/onto-doc` | Build the ENTIRE knowledge base for this repo — survey the codebase, then dispatch kb-curate curator agents per area to produce docs/ (per-service READMEs, reference specs, runbooks, decisions, entry point) following the OntoShip ontology, then lint + index + map. Use to bootstrap or rebuild a project's whole KB. | [scope] (empty → whole repo) | kb-curate skill via Task fan-out + GitMark CLI |
| `/prototype` | Build a throwaway prototype to answer a design question — logic/state (single HTML file) or UI (radically different variations on one route). Returns data + a recommendation, never commits code. Argument = the question. | <question> (empty → ask) | mp-prototype skill |
| `/ship` | Run the OntoShip dev-flow on one ticket from a plan (docs/plans/<slug>/) or a whole file plan (docs/plans/<slug>.md) or an ad-hoc description — worktree → implement → tests → review → dev-tests → prod-tests → ship. One ticket (or one file plan) per run, strictly sequential. Launched only by hand. | [plan path] / [ticket path] / <ad-hoc> (empty → most recent plan) | dev-flow skill |
| `/to-tickets` | Break a plan (docs/plans/<slug>.md or docs/plans/<slug>/) or the current conversation into tracer-bullet tickets with blocking edges under the plan folder. Argument = plan path or topic; empty = most recent plan. | [plan path] (empty → most recent plan) | mp-to-tickets skill |
<!-- END inventory:commands -->

## Навыки

<!-- BEGIN inventory:skills -->
| Skill | What it does |
|---|---|
| `dev-flow` | The spec-driven development loop for shipping a feature/fix fast and safely on top of a GitMark KB — one ticket (or one file plan) at a time: worktree → implement → tests → independent review → dev-tests → prod-tests → ship (MR → dev → main). Use when starting a feature or fix, or when asked "how do we build/ship a change here". |
| `domain-modeling` | Build and sharpen the project's domain model — challenge terms against CONTEXT.md, stress-test with edge-case scenarios, and update CONTEXT.md and docs/decisions/ inline. Use when discussing codebase terminology, writing or editing CONTEXT.md, or recording or editing a decision. |
| `grilling` | Grill the user relentlessly about a plan, decision, or idea. Use when the user wants to stress-test their thinking, or uses any 'grill' trigger phrases. |
| `kb-curate` | Rules for maintaining a markdown knowledge base (GitMark) — apply when adding, editing, moving, or deleting documentation (.md). A lightweight code-ontology: every document has a type, properties (frontmatter), and typed links. Keeps the KB structured instead of a pile of files. Use on "add a doc", "record a decision", "update the docs", "reorganize docs". |
| `kb-search` | Search a project's markdown knowledge base (docs/, README files, *.md) via the GitMark CLI — FTS5 ranking (bm25) plus trigram/fuzzy matching — instead of grepping across files. Use when you need to find where something is documented, "where do the docs say X", before reading files at random, or to generate an HTML overview/graph of the knowledge base. Handles substrings, typos, and non-Latin scripts. |
| `mp-code-review` | Review the changes since a fixed point (commit, branch, tag, or merge-base) along two axes — Standards (does the code follow this repo's documented standards?) and Spec (does the code match the originating plan/ticket?). Runs both reviews in parallel sub-agents and reports them side by side. Use when the user wants to review a branch, a PR, work-in-progress changes, or a named piece of code. |
| `mp-diagnose` | Diagnosis loop for hard bugs and performance regressions — find the root cause and a minimal repro, then hand the fix to /ship. Use when the user says "diagnose"/"debug this", or reports something broken/throwing/failing/slow. |
| `mp-grill-with-docs` | A relentless interview to sharpen a plan or design, which also builds the project's domain model (CONTEXT.md glossary + decision docs) as it goes, ending in a plan contract (docs/plans/<slug>.md). Use when the user wants to stress-test a plan or idea before implementing. |
| `mp-handoff` | Compact the current conversation into a handoff document for another agent to pick up — a bridge between sessions, not a source of knowledge. Use to continue work in a fresh session, or to hand a research/prototype result back to the main session. |
| `mp-improve-codebase-architecture` | Scan a codebase for deepening opportunities, present them as a visual HTML report, then grill through whichever one you pick — ending in an OntoShip plan contract (docs/plans/<slug>.md). Driven by the /architecture command. |
| `mp-prototype` | Build a throwaway prototype to answer a design question — returns data and a recommendation for the decision-maker, it does not commit anything to the code. Use when the user wants to sanity-check a state model or logic, or explore what a UI should look like. |
| `mp-to-tickets` | Break a plan (docs/plans/<slug>.md or docs/plans/<slug>/) or the current conversation into tracer-bullet tickets with blocking edges, written as docs/plans/<slug>/NN-<ticket>.md. Use when the user says "разбей на тикеты" or runs /to-tickets. |
<!-- END inventory:skills -->

Ниже — что делает каждая команда.

Каждая команда — тонкая обёртка над навыком: команда задаёт намерение, навык знает
как. Полное описание — в файле команды.

## /kb
Поиск по базе знаний (`gitmark search`) вместо слепого перебора файлов.

## /kb-map
HTML-карта базы знаний: дерево, рендер markdown и граф связей.

## /doc
Записать знание в базу: агент сам выбирает тип и папку, ставит frontmatter и
типизированные ссылки. Сначала ищет — и правит существующее, а не плодит дубль.

## /onto-doc
Собрать или пересобрать базу знаний целиком: обзор кода → области → куратор на
каждую область → линт, индекс, граф.

## /grill
Допрос по дереву решений без записи в базу: фронт вопросов за раунд,
рекомендованные ответы, решения за человеком.

## /grilling
То же, но с доменной моделью: термины кристаллизуются в глоссарий,
несущие выборы — в решения, итог — в контракт плана.

## /architecture
Скан архитектуры: кандидаты на углубление с диаграммами «до/после». Ничего не
рефакторит — выбранный кандидат уходит в грилл и контракт плана.

## /code-review
Ревью диффа по двум независимым осям — стандарты репозитория и исходная спека;
отчёт в `.scratch/`, read-only.

## /to-tickets
Разбить план на тикеты-срезы: каждый размером в одно свежее контекстное окно,
с объявленными блокировками.

## /handoff
Свернуть разговор в `.scratch/` — чтобы свежий агент продолжил, а не начинал заново.

## /prototype
Одноразовый прототип под вопрос проектирования: данные и рекомендация, без коммита.

## /ship
Выполнить **один** тикет (или один файл-план) по гейт-пайплайну. Запускается
только вручную.
