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

from .model import Income, Scenario

KOPEK = Decimal("0.01")
DAYS_IN_MONTH = Decimal(30)


@dataclass
class CashMonth:
    """Касса за месяц: остатки, дыра, нехватка до прожиточного минимума, свободные деньги."""
    index: int
    month: date
    balances: dict[str, Decimal]
    hole: Decimal | None
    hole_date: date | None
    floor_gap: Decimal | None
    floor_gap_date: date | None
    free: Decimal


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


def _month_start(day: date, index: int) -> date:
    """Первый день месяца через `index` месяцев от месяца даты."""
    year, month = day.year, day.month + index
    return date(year + (month - 1) // 12, (month - 1) % 12 + 1, 1)


def _month_end(day: date) -> date:
    """Последний день месяца даты."""
    import calendar
    return date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])



def _validate_accounts(scenario: Scenario) -> None:
    """Проверка счетов: недоступный не может финансировать платёж."""
    known = {a.name for a in scenario.accounts}
    for p in scenario.payments:
        if p.account is not None and p.account not in known:
            raise ValueError(
                f"платёж {p.counterparty or p.creditor!r}: неизвестный счёт {p.account!r}; "
                f"объявлены: {sorted(known)}")
        if p.account is not None:
            acct = next(a for a in scenario.accounts if a.name == p.account)
            if not acct.available:
                raise ValueError(
                    f"платёж {p.counterparty or p.creditor!r}: счёт {p.account!r} недоступен")
    for i in scenario.income:
        if i.account not in known:
            raise ValueError(
                f"приход: неизвестный счёт {i.account!r}; объявлены: {sorted(known)}")
    for t in scenario.transfers:
        if t.from_account not in known:
            raise ValueError(
                f"перевод (откуда): неизвестный счёт {t.from_account!r}")
        if t.to_account not in known:
            raise ValueError(
                f"перевод (куда): неизвестный счёт {t.to_account!r}")
    for p in scenario.payments:
        if p.counterparty is None and p.creditor is None:
            raise ValueError("платёж без контрагента: нужен уид контрагента "
                             "или строковое имя кредитора")


def roll_cash(scenario: Scenario, start: date, max_months: int = 600,
              main: str | None = None) -> list[CashMonth]:
    """Прокатить кассу по месяцам от `start`.

    Месяцы календарные; первый — с `start` до конца месяца, неполный.
    Остатки предыдущего месяца — вход следующего. Доходы раньше платежей
    в один день. Даты доходов и платежей вычислены вызывающим по тем же
    правилам: кламп на конец месяца и сдвиг с выходного.

    Платёж с `prepaid=True` — досрочка: она уходит после обязательных платежей
    месяца, поэтому свободные деньги считаются до неё. Неизвестный прожиточный
    минимум даёт `floor_gap = None` («не оценено»), а не ноль.
    """
    _validate_accounts(scenario)
    bal: dict[str, Decimal] = {a.name: a.balance for a in scenario.accounts}
    if main is None:
        main = next(iter(bal))
    elif main not in bal:
        raise ValueError(
            f"основной счёт: неизвестный счёт {main!r}; объявлены: {sorted(bal)}")

    # Доходы: даты вычислены вызывающим по тем же правилам, что и платежи
    incomes = sorted(scenario.income, key=lambda i: i.date)

    payments = sorted(scenario.payments, key=lambda p: p.date)
    transfers = sorted(scenario.transfers, key=lambda t: t.date)

    months: list[CashMonth] = []
    for index in range(1, max_months + 1):
        month = _month_start(start, index - 1)
        end = _month_end(month)
        window_start = start if index == 1 else month

        # События месяца: доходы раньше платежей в один день; досрочки — после
        # обязательных: свободные деньги становятся известны, когда месяц прожит.
        regular: list[tuple[date, str, Decimal, str]] = []
        prepaid: list[tuple[date, str, Decimal, str]] = []
        for i in incomes:
            if window_start <= i.date <= end:
                regular.append((i.date, i.account, i.amount, "income"))
        for p in payments:
            if not window_start <= p.date <= end:
                continue
            row = (p.date, p.account, -p.amount, "payment")
            (prepaid if p.prepaid else regular).append(row)
        for t in transfers:
            if window_start <= t.date <= end:
                regular.append((t.date, t.from_account, -t.amount, "transfer"))
                regular.append((t.date, t.to_account, t.amount, "transfer"))

        # income before payment on same day
        regular.sort(key=lambda e: (e[0], 0 if e[3] == "income" else 1))
        prepaid.sort(key=lambda e: e[0])

        start_bal = dict(bal)
        timeline: list[Step] = []
        for d, acct, delta, _ in regular:
            bal[acct] = bal[acct] + delta
            timeline.append(Step(d, acct, delta, bal[acct]))

        # Нехватка до прожиточного минимума: по обязательным событиям месяца —
        # досрочка тратится уже после того, как на жизнь отложено.
        main_steps = [s for s in timeline if s.account == main]
        floor_gap, floor_gap_date = _assess_floor(scenario, main_steps)

        # Свободные деньги: доступные остатки минус прожиточный минимум месяца.
        # Считаются до досрочек — это и есть бюджет досрочек.
        total_money = Decimal(0)
        for a in scenario.accounts:
            if not a.available:
                continue
            if a.is_credit:
                total_money += max(bal[a.name], Decimal(0))
            else:
                total_money += bal[a.name]
        if scenario.living_floor_monthly is not None:
            days = (end - window_start).days + 1
            monthly_living = (scenario.living_floor_monthly * Decimal(days)
                              / DAYS_IN_MONTH).quantize(KOPEK, rounding=ROUND_CEILING)
            free = max(total_money - monthly_living, Decimal(0))
        else:
            free = total_money

        # Досрочки — после обязательных: свободные деньги уже посчитаны, второй
        # раз в бюджет они не попадут, а касса увидит, что деньги ушли.
        for d, acct, delta, _ in prepaid:
            bal[acct] = bal[acct] + delta
            timeline.append(Step(d, acct, delta, bal[acct]))

        # Дыра: худший остаток доступных некредитных счетов, включая старт
        hole: Decimal | None = None
        hole_date: date | None = None
        for a in scenario.accounts:
            if a.available and not a.is_credit and start_bal[a.name] < 0:
                if hole is None or start_bal[a.name] < hole:
                    hole = start_bal[a.name]
                    hole_date = window_start
        for s in timeline:
            acct = next(a for a in scenario.accounts if a.name == s.account)
            if acct.available and not acct.is_credit and s.balance_after < 0:
                if hole is None or s.balance_after < hole:
                    hole = s.balance_after
                    hole_date = s.date
        if hole is not None:
            hole = abs(hole)

        months.append(CashMonth(
            index=index, month=month, balances=dict(bal),
            hole=hole, hole_date=hole_date,
            floor_gap=floor_gap, floor_gap_date=floor_gap_date,
            free=free,
        ))

    return months
