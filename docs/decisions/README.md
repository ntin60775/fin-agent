---
node_type: index
title: Решения
service: _platform
status: active
updated: 2026-09-14
links:
  part_of: [../README.md]
---

# Решения

Несущие выборы с обоснованием: что рассматривали, что отпало и почему. Один
выбор — один файл. Формат — по `kb-curate`, тип `decision`.

- [zone-split.md](zone-split.md) — движок вынесен в отдельную зону от финансовых данных
- [request-channel.md](request-channel.md) — заявки из финансовой зоны идут в `inbox/`, а не правятся в коде
- [library-not-database.md](library-not-database.md) — ядро — библиотека, а не база данных
- [unknown-is-not-zero.md](unknown-is-not-zero.md) — неизвестное значение даёт `None`, а не ноль
- [counterparty-model.md](counterparty-model.md) — кредитор уходит, остаётся контрагент, роли выводятся из истории
- [derived-balances.md](derived-balances.md) — остаток и сальдо считаются, а не хранятся
- [movement-settles-occurrence.md](movement-settles-occurrence.md) — факт по вхождению несёт движение, а не правка
- [schedule-to-cash.md](schedule-to-cash.md) — долги отдают кассе расписание, касса возвращает бюджет досрочек
- [card-schemas.md](card-schemas.md) — карточки покрыты схемами через frontmatter, проза свободна
- [what-means-out.md](what-means-out.md) — «выбрался» — это покрытые расходы, а не закрытые долги
- [wallet-pays-what-it-has.md](wallet-pays-what-it-has.md) — кошелёк платит тем, что у него есть; дыра — «денег нет нигде»
