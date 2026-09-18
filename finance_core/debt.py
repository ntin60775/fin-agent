"""Долговая сторона: модель долга, амортизация и прокат месяцев.

Кассовое ядро (`solver`) отвечает «хватит ли денег в этом месяце».
Эта сторона отвечает на другой вопрос — **когда долги закроются и сколько
стоят проценты**, если каждый месяц платить обязательные платежи и
направлять свободные деньги по выбранной стратегии.

Модель намеренно бедная: остаток, ставка (годовая или дневная), обязательный
платёж, приоритет, разрешена ли досрочка. Чего в исходных данных нет, движок
не выдумывает: такие долги в прокат не входят, и вызывающий код перечисляет
их как пробелы.
"""
from __future__ import annotations

import calendar
import dataclasses
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

KOPEK = Decimal("0.01")
DAYS_IN_YEAR = Decimal(365)


@dataclass
class Debt:
    """Один долг. Ставка — либо годовая, либо дневная; 0 = беспроцентный."""
    name: str
    balance: Decimal
    rate_per_year: Decimal | None = None
    rate_per_day: Decimal | None = None
    payment: Decimal | None = None          # обязательный платёж в месяц
    second_priority: bool = False           # взыскание: вне обязательной нагрузки
    prepay: bool = True                     # разрешена ли досрочка из свободных денег

    @property
    def annual_rate(self) -> Decimal:
        """Ставка в год — по ней ранжирует стратегия «лавина»."""
        if self.rate_per_day is not None:
            return self.rate_per_day * DAYS_IN_YEAR
        return self.rate_per_year or Decimal(0)


@dataclass
class MonthSnapshot:
    index: int
    date: date
    balances: dict[str, Decimal]
    total: Decimal
    interest: Decimal
    paid: Decimal
    unused: Decimal = Decimal(0)   # свободные деньги, которым не нашлось места


@dataclass
class Plan:
    snapshots: list[MonthSnapshot]
    total_interest: Decimal
    months: int
    freedom: date | None          # месяц закрытия последнего долга первого приоритета
    start_total: Decimal
    stalled: bool = False         # за max_months долг не закрылся

    @property
    def total_paid(self) -> Decimal:
        return sum((s.paid for s in self.snapshots), Decimal(0))

    @property
    def total_unused(self) -> Decimal:
        return sum((s.unused for s in self.snapshots), Decimal(0))

    @property
    def idle(self) -> Decimal:
        """Свободные деньги, простоявшие без дела, пока долги ещё были живы.

        Последний месяц не считается: там остаток бюджета неизбежен —
        долгов больше не осталось.
        """
        return sum((s.unused for s in self.snapshots[:-1]), Decimal(0))


def _next_month(day: date) -> date:
    return date(day.year + (day.month == 12), day.month % 12 + 1, 1)


def _days_in_month(day: date) -> int:
    return calendar.monthrange(day.year, day.month)[1]


def _monthly_rate(debt: Debt, days: int) -> Decimal:
    if debt.rate_per_day is not None:
        return debt.rate_per_day * days
    if debt.rate_per_year is not None:
        return debt.rate_per_year / 12
    return Decimal(0)


def _order(active: Sequence[Debt], strategy: str) -> list[Debt]:
    if strategy == "avalanche":       # сначала самая дорогая ставка
        return sorted(active, key=lambda d: d.annual_rate, reverse=True)
    if strategy == "snowball":        # сначала самый маленький остаток
        return sorted(active, key=lambda d: d.balance)
    raise ValueError(f"неизвестная стратегия: {strategy!r}")


def roll_forward(debts: Iterable[Debt], start: date, monthly_extra: Decimal,
                 strategy: str = "avalanche", max_months: int = 600) -> Plan:
    """Прокатить долги по месяцам от `start`.

    Бюджет на долги = обязательные платежи + `monthly_extra`. Освободившийся
    после закрытия долга платёж не исчезает, а идёт в тот же бюджет — это и
    есть «снежный ком». Долг с `prepay=False` получает только обязательный
    платёж; свободные деньги, которым не нашлось места, считаются как unused.
    """
    work = [dataclasses.replace(d) for d in debts]
    serviced = [d for d in work if not d.second_priority]
    budget = monthly_extra + sum((d.payment or Decimal(0)) for d in serviced)

    snapshots: list[MonthSnapshot] = []
    total_interest = Decimal(0)
    start_total = sum((d.balance for d in serviced), Decimal(0))
    month = start

    for index in range(1, max_months + 1):
        days = _days_in_month(month)
        interest = Decimal(0)
        for d in serviced:
            if d.balance <= 0:
                continue
            accrued = (d.balance * _monthly_rate(d, days)).quantize(KOPEK, ROUND_HALF_UP)
            if accrued > 0:
                d.balance += accrued
                interest += accrued
        total_interest += interest

        pool, paid = budget, Decimal(0)
        for d in serviced:                       # 1. обязательные платежи
            if d.balance <= 0 or not d.payment:
                continue
            amount = min(d.balance, d.payment, pool)
            d.balance -= amount
            pool -= amount
            paid += amount
        targets = [x for x in serviced if x.balance > 0 and x.prepay]
        for d in _order(targets, strategy):      # 2. свободные деньги по стратегии
            if pool <= 0:
                break
            amount = min(d.balance, pool)
            d.balance -= amount
            pool -= amount
            paid += amount

        snapshots.append(MonthSnapshot(
            index, month, {d.name: d.balance for d in work},
            sum((d.balance for d in serviced), Decimal(0)), interest, paid,
            pool if pool > 0 else Decimal(0),
        ))

        if all(d.balance <= 0 for d in serviced):
            return Plan(snapshots, total_interest, index, month, start_total)
        month = _next_month(month)

    return Plan(snapshots, total_interest, max_months, None, start_total,
                stalled=True)


def compare_strategies(debts: Iterable[Debt], start: date, monthly_extra: Decimal,
                       strategies: Sequence[str] = ("avalanche", "snowball")
                       ) -> list[tuple[str, Plan]]:
    """Прогнать один и тот же портфель разными стратегиями."""
    debts = list(debts)
    return [(s, roll_forward(debts, start, monthly_extra, strategy=s))
            for s in strategies]
