---
node_type: index
title: Решения
service: _platform
status: active
updated: 2026-09-26
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
- [month-by-month-convergence.md](month-by-month-convergence.md) — сходимость связки — проходом по месяцам, а не глобальной петлёй
- [deal-balance-canon.md](deal-balance-canon.md) — остаток сделки один: долг на дату = тело − движения + начисленное
- [single-free-money-basis.md](single-free-money-basis.md) — свободные деньги — одно число на одном базисе: потолок и остаток бюджета снесены, предложение — срез
- [card-schemas.md](card-schemas.md) — карточки покрыты схемами через frontmatter, проза свободна
- [what-means-out.md](what-means-out.md) — «выбрался» — это покрытые расходы, а не закрытые долги
- [wallet-pays-what-it-has.md](wallet-pays-what-it-has.md) — кошелёк платит тем, что у него есть; дыра — «денег нет нигде»
- [floor-excludes-events.md](floor-excludes-events.md) — событие уже в остатке — в число минимума не кладётся
- [ontology-full-scope.md](ontology-full-scope.md) — модель знаний распространяется на каждый документ зоны: без исключений для корневых и пакетных
