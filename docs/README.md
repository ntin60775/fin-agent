---
node_type: index
title: База знаний ядра расчёта
service: _platform
status: active
updated: 2026-09-15
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

Все решения — [decisions/README.md](decisions/README.md).

- [zone-split.md](decisions/zone-split.md) — почему движок вынесен в отдельную зону от финансовых данных
- [request-channel.md](decisions/request-channel.md) — почему заявки из финансовой зоны идут в `inbox/`, а не правятся в коде на месте
- [library-not-database.md](decisions/library-not-database.md) — почему ядро — библиотека, а не база данных
- [unknown-is-not-zero.md](decisions/unknown-is-not-zero.md) — почему неизвестное значение даёт `None`, а не ноль
- [counterparty-model.md](decisions/counterparty-model.md) — почему кредитор уходит, а роли выводятся из истории
- [derived-balances.md](decisions/derived-balances.md) — почему остаток и сальдо считаются, а не хранятся
- [movement-settles-occurrence.md](decisions/movement-settles-occurrence.md) — почему факт по вхождению несёт движение, а не правка
- [schedule-to-cash.md](decisions/schedule-to-cash.md) — почему связка двусторонняя: расписание туда, бюджет досрочек обратно
- [wallet-pays-what-it-has.md](decisions/wallet-pays-what-it-has.md) — почему кошелёк платит тем, что у него есть, а дыра — «денег нет нигде»
- [card-schemas.md](decisions/card-schemas.md) — почему схема карточки — frontmatter и линтер, а не JSON Schema
- [what-means-out.md](decisions/what-means-out.md) — почему «выбрался» — это покрытые расходы, а не закрытые долги

## Планы

- [plans/](plans/README.md) — контракты планов: [zone-split.md](plans/zone-split.md), [obligations/](plans/obligations/README.md)

## Ops

Все процедуры — [ops/README.md](ops/README.md).

- [перенос-на-другую-машину.md](ops/перенос-на-другую-машину.md) — что копировать, что нужно на новой машине, как проверить
- [публикация.md](ops/публикация.md) — как выложить зону в открытый репозиторий и что проверить до первого push

## Движок

- [finance_core/README.md](../finance_core/README.md) — устройство, API, инварианты

## Производные

```bash
python3 skill://kb-search/gitmark.py index                 # построить индекс
python3 skill://kb-search/gitmark.py search "<запрос>"     # bm25 ∪ trigram ∪ fuzzy
python3 skill://kb-search/gitmark.py lint                  # инварианты I1–I8
python3 skill://kb-search/gitmark.py map -o docs-map.html  # HTML-карта и граф
python3 skill://kb-search/gitmark.py inventory             # перегенерировать таблицы реестра
```

Адрес `skill://kb-search/gitmark.py` разрешает агентская среда: он ведёт к файлу
пакета, где бы тот ни стоял, — поэтому работает и там, где `.omp/skills/` нет.
В переменную такую команду положить нельзя: в присваивании `skill://` не
разрешается.
