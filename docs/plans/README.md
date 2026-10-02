---
node_type: index
title: Планы
service: _platform
status: active
updated: 2026-10-02
links:
  part_of: [../README.md]
---

# Планы

Контракт плана: **Goal** (зачем), **Done** (что считается сделанным), **Scope**
(что затронуто), **Constraints** (стоп-точки), **Context** (что уже установлено),
**Tickets** (разбивка). Пишется до кода.

План остаётся файлом, пока не разбит на тикеты; разбивка — `/to-tickets`,
выполнение — `/ship` по одному тикету.

- [long-debts-single-values/](long-debts-single-values/README.md) — длинный долг считается, у величин один базис: сходимость проходом по месяцам, единое определение свободных денег и остатка сделки
- [penalties-and-charges/](penalties-and-charges/README.md) — пени, штрафы и начисления от события: долг должен уметь расти — начисления от события, неустойка от просрочки, долг по частям, распределение платежа правилом зоны (закрыт 2026-10-02)
- [restructuring-and-enforcement/](restructuring-and-enforcement/README.md) — реструктуризация и взыскание: условия меняются со дня записью-версией, принуждение видно кассе — удержания из дохода и арестованных кошельков, ограниченные неприкосновенным остатком
- [obligations/](obligations/README.md) — взаиморасчёты и прогноз: контрагенты, сделки, связка кассы и долгов
- [zone-split.md](zone-split.md) — разделение проекта на архитектурную и финансовую зоны
- [engine-wording.md](engine-wording.md) — докстринги движка называют вещи как глоссарий
- [engine-messages.md](engine-messages.md) — сообщения движка называют кошелёк кошельком
- [model-audit-fixes/](model-audit-fixes/README.md) — аудит математической модели: недостатки 01–09, от критического (прожитый минимум не списывается) до контрактных

**Порядок.** Считается по блокирующим связям, а не по дате: план или тикет берётся,
когда все его `depends_on` закрыты.

- `restructuring-and-enforcement` — грилл пройден (3 раунда, Q1–Q14), разбит на
  тикеты, все `draft` — ждёт `/ship` по одному тикету за запуск (см.
  `restructuring-and-enforcement/README.md`);
- `penalties-and-charges`, `obligations`, `long-debts-single-values`, `zone-split`,
  `engine-wording`, `engine-messages` и `model-audit-fixes` — архивные, порядок
  не задают.
