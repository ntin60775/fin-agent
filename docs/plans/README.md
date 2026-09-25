---
node_type: index
title: Планы
service: _platform
status: active
updated: 2026-09-25
links:
  part_of: [../README.md]
---

# Планы

Контракт плана: **Goal** (зачем), **Done** (что считается сделанным), **Scope**
(что затронуто), **Constraints** (стоп-точки), **Context** (что уже установлено),
**Tickets** (разбивка). Пишется до кода.

План остаётся файлом, пока не разбит на тикеты; разбивка — `/to-tickets`,
выполнение — `/ship` по одному тикету.

- [zone-split.md](zone-split.md) — разделение проекта на архитектурную и финансовую зоны
- [engine-wording.md](engine-wording.md) — докстринги движка называют вещи как глоссарий
- [engine-messages.md](engine-messages.md) — сообщения движка называют кошелёк кошельком
- [obligations/](obligations/README.md) — взаиморасчёты и прогноз: контрагенты, сделки, связка кассы и долгов
- [model-audit-fixes/](model-audit-fixes/README.md) — аудит математической модели: недостатки 01–09, от критического (прожитый минимум не списывается) до контрактных
- [long-debts-single-values/](long-debts-single-values/README.md) — длинный долг считается, у величин один базис: сходимость проходом по месяцам, единое определение свободных денег и остатка сделки

**Порядок.** Считается по блокирующим связям, а не по дате: план или тикет берётся,
когда все его `depends_on` закрыты.

- `obligations` — цепочка по зависимостям тикетов: **08 → 09 → 10**
  (см. `obligations/README.md`, там же правило «тикет выполняется, когда все
  блокирующие закрыты»);
- `model-audit-fixes` — цепочка solver'а: **01 → 02 → (03, 04)**, 09 ждёт 01;
  05–08 независимы (см. `model-audit-fixes/README.md`);
- `long-debts-single-values` — 01, 02 и 03 независимы, 04 после них, 05 и 06 — после
  04, 07 и 08 — после 06 (см. `long-debts-single-values/README.md`);
- `zone-split`, `engine-wording` и `engine-messages` — архивные, порядок не
  задают.
