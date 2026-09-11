---
node_type: index
title: База знаний ядра расчёта
service: _platform
status: active
updated: 2026-09-11
links:
  part_of: [../AGENTS.md]
---

# База знаний ядра расчёта

Markdown — источник правды. Всё производное — поисковый индекс
(`.gitmark/index.db`) и HTML-карта — регенерируется из md и в git не коммитится.
Модель знаний (типы документов, свойства, типизированные ссылки) — в
[ontology.md](ontology.md).

## Reference

- [how-to-work.md](reference/how-to-work.md) — **как работать с двумя зонами: где открывать агента, кто что меняет**
- [commands.md](reference/commands.md) — реестр команд и навыков (генерируемая часть — `gitmark inventory`)
- [../CONTEXT.md](../CONTEXT.md) — доменные термины ядра: как называть вещи, чтобы не расходиться

## Decisions

- [zone-split.md](decisions/zone-split.md) — почему движок вынесен в отдельную зону от финансовых данных
- [library-not-database.md](decisions/library-not-database.md) — почему ядро — библиотека, а не база данных
- [unknown-is-not-zero.md](decisions/unknown-is-not-zero.md) — почему неизвестное значение даёт `None`, а не ноль

## Движок

- [finance_core/README.md](../finance_core/README.md) — устройство, API, инварианты

## Производные

```bash
G="python3 .omp/skills/kb-search/gitmark.py"
$G index                 # построить индекс
$G search "<запрос>"     # bm25 ∪ trigram ∪ fuzzy
$G lint                  # инварианты I1–I7
$G map -o docs-map.html  # HTML-карта и граф
$G inventory             # перегенерировать таблицы реестра
```
