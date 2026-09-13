"""Детерминированный решатель кассового каскада.

События (доходы, платежи, переводы) сортируются по дате; для каждого
счёта ведётся бегущий остаток. Считаются две разные вещи:

- **дыра** — уход основного счёта (обычно зарплатной карты) в минус;
- **нехватка до прожиточного минимума** — насколько остатка не хватает,
  чтобы прожить до следующего прихода. Дыры может не быть, а нехватка
  быть: остаток +2 000 при нужных 15 000 — это дефицит 13 000.

Второе считается только если прожиточный минимум задан. Не задан (`?`) —
`floor_gap` равен None, и это честный ответ «не оценено», а не ноль.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_CEILING, Decimal

from .model import Scenario

KOPEK = Decimal("0.01")
DAYS_IN_MONTH = Decimal(30)


@dataclass
class Step:
    date: date
    account: str
    delta: Decimal
    balance_after: Decimal


@dataclass
class Result:
    min_balance: Decimal          # минимум остатка основного счёта за линию
    min_date: date | None
    end_balance: Decimal          # остаток основного счёта в конце линии
    balances: dict[str, Decimal]  # финальный остаток каждого счёта
    timeline: list[Step]
    #: Худшая нехватка до прожиточного минимума (включает саму дыру).
    #: None = прожиточный минимум неизвестен → реальный дефицит НЕ оценён.
    floor_gap: Decimal | None = None
    floor_gap_date: date | None = None

    @property
    def hole(self) -> Decimal:
        """Дефицит основного счёта: 0, если в минус он не уходил."""
        return min(self.min_balance, Decimal(0))

    @property
    def total(self) -> Decimal:
        """Финальная суммарная ликвидность по всем счетам сценария."""
        return sum(self.balances.values(), Decimal(0))


def _days_to_next_income(scenario: Scenario, day: date) -> int | None:
    """Сколько дней от `day` до ближайшего прихода (в окне или на горизонте)."""
    for inc in sorted(i.date for i in scenario.income):
        if inc > day:
            return (inc - day).days
    horizon = scenario.income_horizon
    if horizon is not None and horizon > day:
        return (horizon - day).days
    return None


def _validate(scenario: Scenario, main: str) -> None:
    """Явная ошибка вместо KeyError при опечатке в имени счёта.

    Платёж обязан ссылаться на контрагента: уидом (новая форма) или строкой
    имени (старая, живёт до переезда зоны).
    """
    known = {a.name for a in scenario.accounts}
    refs = [(main, "основной счёт")]
    refs += [(i.account, "приход") for i in scenario.income]
    refs += [(p.account, f"платёж {p.counterparty or p.creditor!r}")
             for p in scenario.payments]
    refs += [(t.from_account, "перевод (откуда)") for t in scenario.transfers]
    refs += [(t.to_account, "перевод (куда)") for t in scenario.transfers]
    for name, what in refs:
        if name not in known:
            raise ValueError(
                f"{what}: неизвестный счёт {name!r}; объявлены: {sorted(known)}")
    for p in scenario.payments:
        if p.counterparty is None and p.creditor is None:
            raise ValueError("платёж без контрагента: нужен уид контрагента "
                             "или строковое имя кредитора")


def run(scenario: Scenario, main: str) -> Result:
    """Прогнать каскад: дыра, нехватка до прожиточного минимума, таймлайн."""
    _validate(scenario, main)
    bal: dict[str, Decimal] = {a.name: a.balance for a in scenario.accounts}

    events: list[tuple[date, str, Decimal]] = []
    for i in scenario.income:
        events.append((i.date, i.account, i.amount))
    for p in scenario.payments:
        events.append((p.date, p.account, -p.amount))
    for t in scenario.transfers:
        events.append((t.date, t.from_account, -t.amount))
        events.append((t.date, t.to_account, t.amount))

    # Стабильная сортировка по дате: события одного дня идут в порядке добавления.
    events.sort(key=lambda e: e[0])

    timeline: list[Step] = []
    for d, acct, delta in events:
        bal[acct] = bal[acct] + delta
        timeline.append(Step(d, acct, delta, bal[acct]))

    main_steps = [s for s in timeline if s.account == main]
    if main_steps:
        lowest = min(main_steps, key=lambda s: s.balance_after)
        min_balance, min_date = lowest.balance_after, lowest.date
    else:
        min_balance, min_date = bal[main], None

    floor_gap, floor_gap_date = _assess_floor(scenario, main_steps)
    return Result(min_balance, min_date, bal[main], dict(bal), timeline,
                  floor_gap, floor_gap_date)


def _assess_floor(scenario: Scenario,
                  main_steps: list[Step]) -> tuple[Decimal | None, date | None]:
    """Худшая нехватка остатка до прожиточного минимума за линию."""
    monthly = scenario.living_floor_monthly
    if monthly is None:
        return None, None  # прожиточный минимум неизвестен — честное «не оценено»

    worst: tuple[Decimal, date] | None = None
    assessed = 0
    for step in main_steps:
        days = _days_to_next_income(scenario, step.date)
        if days is None:
            continue
        assessed += 1
        required = (monthly * days / DAYS_IN_MONTH).quantize(KOPEK,
                                                            rounding=ROUND_CEILING)
        gap = required - step.balance_after
        if gap > 0 and (worst is None or gap > worst[0]):
            worst = (gap, step.date)
    if assessed == 0:
        # Ни одного шага с известным приходом впереди — ноль был бы выдумкой.
        return None, None
    return worst if worst is not None else (Decimal(0), None)


def optional_cap(result: Result, reserve: Decimal) -> Decimal:
    """Сколько можно отдать частному кредитору в этом месяце.

    = остаток основного счёта после всех обязательств минус резерв
    на обязательства начала следующего месяца.
    """
    return result.end_balance - reserve


def cover_cost(hole: Decimal, days: int,
               rate_per_year: Decimal | None = None,
               rate_per_day: Decimal | None = None) -> Decimal:
    """Стоимость покрытия дыры заёмными деньгами на `days` дней.

    Укажите либо годовую ставку (rate_per_year), либо дневную (rate_per_day).
    """
    if rate_per_day is None:
        if rate_per_year is None:
            raise ValueError("нужна rate_per_year или rate_per_day")
        rate_per_day = rate_per_year / Decimal(365)
    return abs(hole) * rate_per_day * days


@dataclass
class Outcome:
    """Сжатый итог одного варианта — строка таблицы сравнения."""
    label: str
    min_balance: Decimal
    min_date: date | None
    end_balance: Decimal
    cap: Decimal                  # потолок частного транша при заданном резерве
    floor_gap: Decimal | None     # None = прожиточный минимум неизвестен

    @property
    def hole(self) -> Decimal:
        return min(self.min_balance, Decimal(0))


def outcome(label: str, scenario: Scenario, main: str, reserve: Decimal) -> Outcome:
    r = run(scenario, main)
    return Outcome(label, r.min_balance, r.min_date, r.end_balance,
                   optional_cap(r, reserve), r.floor_gap)


def compare(variants: Mapping[str, Scenario], main: str,
            reserve: Decimal) -> list[Outcome]:
    """Прогнать несколько сценариев и вернуть итоги в порядке передачи."""
    return [outcome(label, s, main, reserve) for label, s in variants.items()]
