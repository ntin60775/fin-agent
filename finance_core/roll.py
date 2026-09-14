"""Прокат сделок по месяцам: что платится, что копится и когда закрывается.

Отвечает на вопрос «когда я выйду из долгов», считая вперёд по месяцам: проценты
по базе начисления сделки, обязательные вхождения по графику и свободные деньги по
выбранной стратегии. По дням внутри месяца деньги считает касса (`solver`): прокат
сделок отдаёт ей расписание, а обратно получает бюджет досрочек — связывает их
функция-композиция без состояния (`docs/decisions/schedule-to-cash.md`).

Правила, запертые здесь:

- **Платит только то, что должен я.** Сделки, где должны мне, в прокат не входят:
  они перечисляются требованиями (`Expectation`) и в кассу не подмешиваются.
- **Освободившийся платёж остаётся в бюджете.** Бюджет месяца — обязательная
  нагрузка, посчитанная на начало проката, плюс свободные деньги: закрылась сделка
  раньше срока — её платёж идёт другим. Это и есть снежный ком.
- **Копилка.** Сделки одной единицы закрытия гасят друг друга вместе: платежи
  копятся в котёл, остаток не падает, цель (сумма по единице) фиксирована и не
  растёт — процентов внутри копилки нет.
- **Второй приоритет сам не платится.** Прокат показывает свободный остаток и
  предлагает направить его туда; считать погашение он начинает только после
  согласия владельца (`consent_to_second`). Ждать при этом не бесплатно: долг
  растёт по ставке сделки.
- **Пробел — не ноль.** Сделка с неизвестной ставкой или без правила графика в
  прокат не входит и перечисляется пробелом (`Gap`).

Прокат месячный: платежи внутри месяца агрегируются в его итог, а дни у платежей
остаются — по ним начисляются проценты у дневной ставки. Точку отсчёта и бюджет
досрочек движок не выдумывает: и то и другое передаётся снаружи.
"""
from __future__ import annotations

import calendar
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .settlements import (I_OWE, OWED_TO_ME, Deal, Occurrence, Settlements,
                          deal_amount_at, deal_balance, deal_holder_at,
                          funding_wallet, occurrences, planned_date)

KOPEK = Decimal("0.01")
DAYS_IN_YEAR = Decimal(365)

#: Стратегии досрочек: лавина — дорогая ставка первой, снежный ком — мелкий остаток.
AVALANCHE = "avalanche"
SNOWBALL = "snowball"
STRATEGIES = (AVALANCHE, SNOWBALL)


# --- что прокат отдаёт наружу ----------------------------------------------

@dataclass
class ScheduledPayment:
    """Платёж расписания: что, когда и с какого кошелька уходит по графику.

    `planned` — плановая дата вхождения, по которой платёж опознаётся; `unit` —
    копилка, если платёж идёт в неё, а не в остаток сделки.
    """
    date: date
    amount: Decimal
    deal: str
    counterparty: str
    wallet: str | None
    planned: date
    unit: str | None = None


@dataclass
class UnitMonth:
    """Копилка в месяце: цель, котёл и сколько осталось до закрытия."""
    target: Decimal
    pot: Decimal

    @property
    def remaining(self) -> Decimal:
        return max(self.target - self.pot, Decimal(0))

    @property
    def closed(self) -> bool:
        return self.pot >= self.target


@dataclass
class DealMonth:
    """Месяц проката сделок: начислено, уплачено и что осталось.

    `balances` — остатки сделок на конец месяца. У сделки в копилке остаток не
    падает, пока единица не закроется: платежи копятся, и это видно в `units`.
    `total` — итог по сделкам первого приоритета, `interest` — начислено за
    месяц, `paid` — ушло за месяц, из него `prepaid` — досрочки. `free` —
    свободные деньги, которым не нашлось места; `offer` — сколько из них
    предлагается направить во второй приоритет. `payments` — расписание месяца:
    дата, сумма, кошелёк и контрагент каждого платежа.
    """
    index: int
    month: date
    balances: dict[str, Decimal]
    units: dict[str, UnitMonth]
    total: Decimal
    interest: Decimal
    paid: Decimal
    prepaid: Decimal
    free: Decimal
    offer: Decimal
    payments: list[ScheduledPayment]


@dataclass
class Expectation:
    """Требование: деньги, которые должны мне. В кассу не подмешиваются."""
    deal: str
    counterparty: str
    date: date
    amount: Decimal


@dataclass
class Gap:
    """Пробел: сделка, которая в прокат не вошла, и почему."""
    deal: str
    reason: str


@dataclass
class DealRoll:
    """Прокат сделок: месяцы, срок и пробелы.

    `freedom` — месяц закрытия последней сделки первого приоритета; None вместе
    с `stalled` значит, что за отведённые месяцы долги не закрылись. Регулярные
    расходы на срок не влияют: закрывать там нечего.
    """
    months: list[DealMonth]
    total_interest: Decimal
    freedom: date | None
    start_total: Decimal
    stalled: bool
    expectations: list[Expectation]
    gaps: list[Gap]

    @property
    def total_paid(self) -> Decimal:
        return sum((s.paid for s in self.months), Decimal(0))

    @property
    def total_free(self) -> Decimal:
        return sum((s.free for s in self.months), Decimal(0))


# --- состояние проката -----------------------------------------------------

@dataclass
class _Open:
    """Сделка в прокате: остаток и её место в книге."""
    deal: Deal
    balance: Decimal
    unit: str | None = None
    second: bool = False

    def pay(self, amount: Decimal) -> None:
        self.balance -= amount


@dataclass
class _Unit:
    """Копилка в прокате: цель, котёл и участники."""
    uid: str
    target: Decimal
    pot: Decimal = Decimal(0)
    second: bool = False
    members: list[str] = field(default_factory=list)

    def pay(self, amount: Decimal) -> None:
        self.pot += amount

    @property
    def remaining(self) -> Decimal:
        return max(self.target - self.pot, Decimal(0))

    @property
    def closed(self) -> bool:
        return self.pot >= self.target


@dataclass
class _Target:
    """Куда идут свободные деньги: сделка или копилка."""
    uid: str
    rate: Decimal
    remaining: Decimal
    pay: Callable[[Decimal], None]


# --- календарь -------------------------------------------------------------

def _month_start(day: date, index: int) -> date:
    """Первый день месяца через `index` месяцев от месяца даты."""
    year, month = day.year, day.month + index
    return date(year + (month - 1) // 12, (month - 1) % 12 + 1, 1)


def _month_end(day: date) -> date:
    """Последний день месяца даты."""
    return date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])


def _month_index(start: date, when: date) -> int:
    """Номер месяца проката, в котором лежит дата; до начала — первый."""
    return max((when.year - start.year) * 12 + when.month - start.month + 1, 1)


def _service_date(deal: Deal, when: date, start: date) -> date:
    """Когда платёж случится: просроченное вхождение — в первый месяц проката.

    Дат в прошлом в расписании быть не может: платёж уходит в тот месяц, когда
    его заплатят, — в свой день месяца, но не раньше начала проката. Сам долг от
    этого не меняется: он и так в остатке, а платёж его уменьшает.
    """
    if when >= start:
        return when
    rule = deal.schedule
    shift = rule.shift_weekend if rule is not None else True
    return max(planned_date(start.year, start.month, when.day, shift), start)


def _rate(deal: Deal) -> Decimal:
    """Ставка в год — по ней ранжирует стратегия «лавина»."""
    if deal.rate_per_day is not None:
        return deal.rate_per_day * DAYS_IN_YEAR
    return deal.rate_per_year or Decimal(0)


def _payment_amount(deal: Deal, occ: Occurrence, balance: Decimal) -> Decimal:
    """Сколько платить по вхождению: сумма правила или процент от остатка.

    У минималки процентом от остатка сумма вхождения становится известна только
    здесь: в бюджете она считается от остатка на начало проката (бюджет фиксируют
    заранее), а в платеже — от остатка на начало месяца.
    """
    if occ.remaining is not None:
        return occ.remaining
    percent = deal.schedule.percent if deal.schedule is not None else None
    if percent is None:
        return Decimal(0)
    return (balance * percent).quantize(KOPEK, ROUND_HALF_UP)


def _month_interest(deal: Deal, balance: Decimal, month: date,
                    day_payments: dict[int, Decimal]) -> Decimal:
    """Проценты за месяц по базе начисления сделки.

    Дневная ставка считается по дням: платёж дня уменьшает остаток до начисления
    за этот день — поэтому перенос платежа внутри месяца стоит денег. Годовая —
    начисление месячное: остаток на начало месяца, и перенос её не удорожает.
    """
    if deal.rate_per_day is not None:
        days = calendar.monthrange(month.year, month.month)[1]
        total = Decimal(0)
        left = balance
        for day in range(1, days + 1):
            left -= day_payments.get(day, Decimal(0))
            if left <= 0:
                break
            total += (left * deal.rate_per_day).quantize(KOPEK, ROUND_HALF_UP)
        return total
    if deal.rate_per_year is not None:
        return (balance * deal.rate_per_year / 12).quantize(KOPEK, ROUND_HALF_UP)
    return Decimal(0)


def _order(targets: Sequence[_Target], strategy: str) -> list[_Target]:
    if strategy == AVALANCHE:       # сначала самая дорогая ставка
        return sorted(targets, key=lambda t: t.rate, reverse=True)
    return sorted(targets, key=lambda t: t.remaining)     # сначала мелкий остаток


# --- что в прокат не входит ------------------------------------------------

def _gap_reason(deal: Deal) -> str | None:
    """Почему сделка не годится для проката; None — годится.

    Неизвестное в прокат не подставляется нулём: без ставки не понять, растёт ли
    долг, без правила графика — сколько и когда платить. Требованию правило
    нужно по той же причине: без него не сказать, когда придут деньги. Второму
    приоритету правило и не нужно: он платится не по графику, а из свободных.
    """
    rule = deal.schedule
    if deal.direction == OWED_TO_ME:
        if rule is None:
            return "правило графика не задано: когда придут деньги — неизвестно"
        if rule.percent is not None:
            return "сумма требования не определена: процент от остатка — не про требование"
        return None
    if (deal.amount is not None and deal.rate_per_year is None
            and deal.rate_per_day is None):
        return "ставка не задана: неизвестно, растёт ли долг"
    if deal.second_priority:
        return None
    if rule is None:
        return "правило графика не задано: платить нечем"
    if rule.days and rule.payment is None and rule.percent is None:
        return "сумма платежа не задана: правило не говорит, сколько платить"
    return None


def _broken_units(book: Settlements, reasons: dict[str, str | None]) -> dict[str, str]:
    """Единицы закрытия, которые неполны: копилка закрывается вся целиком."""
    broken: dict[str, str] = {}
    for deal in book.deals:
        unit = deal.closure_unit
        if unit is None or deal.direction != I_OWE or unit in broken:
            continue
        for member in book.deals:
            reason = reasons.get(member.uid)
            if member.closure_unit == unit and reason is not None:
                broken[unit] = (f"единица закрытия {unit!r} неполна: "
                                f"сделка {member.uid!r} — {reason}")
                break
    return broken


def _facts_at(book: Settlements, deal_uid: str, start: date) -> date:
    """Дата, на которую остаток сделки — факт: движения это факт, когда бы ни случились.

    Прокат считает вперёд от `start`, но платёж, случившийся позже начала (начало
    месяца, а платёж — в середине), уже факт: остаток на начало обязан его видеть,
    иначе вхождение, исполненное заранее, посчитается дважды.
    """
    on = start
    for m in book.movements:
        if m.deal == deal_uid and m.date > on:
            on = m.date
    return on


# --- прокат ----------------------------------------------------------------

def roll_deals(book: Settlements, start: date, monthly_extra: Decimal,
               strategy: str = AVALANCHE, max_months: int = 600,
               consent_to_second: bool = False) -> DealRoll:
    """Прокатить сделки по месяцам от `start`.

    Бюджет месяца — обязательная нагрузка по графику плюс `monthly_extra`;
    нагрузка считается один раз, на начало проката, поэтому освободившийся платёж
    остаётся в бюджете. Обязательные вхождения платятся по датам, свободные
    деньги уходят по стратегии — сначала обязательный график, потом досрочки.

    Второй приоритет не платится, пока владелец не дал согласия
    (`consent_to_second`); без согласия его свободный остаток показывается
    предложением (`DealMonth.offer`).
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"неизвестная стратегия: {strategy!r}")

    # Закрытое прокатывать нечего — и пробелом оно не считается: сначала
    # закрытость, потом всё остальное. Иначе погашенная сделка без ставки
    # объявила бы пробелом всю свою копилку и живые долги не прокатались бы.
    closed = {d.uid for d in book.deals
              if d.amount is not None and d.direction == I_OWE
              and (deal_balance(book, d.uid, _facts_at(book, d.uid, start))
                   or Decimal(0)) <= 0}
    reasons = {d.uid: (None if d.uid in closed else _gap_reason(d))
               for d in book.deals}
    broken = _broken_units(book, reasons)

    gaps: list[Gap] = []
    open_deals: dict[str, _Open] = {}
    open_units: dict[str, _Unit] = {}
    receivables: list[Deal] = []

    for deal in book.deals:
        if deal.uid in closed:
            continue                       # уже закрыта: прокатывать нечего
        reason = reasons[deal.uid]
        if reason is None and deal.closure_unit is not None:
            reason = broken.get(deal.closure_unit)
        if reason is not None:
            gaps.append(Gap(deal.uid, reason))
            continue
        if deal.direction == OWED_TO_ME:
            receivables.append(deal)
            continue
        open_deals[deal.uid] = _Open(
            deal, deal_balance(book, deal.uid, _facts_at(book, deal.uid, start))
            or Decimal(0), deal.closure_unit, deal.second_priority)

    for uid, opened in open_deals.items():
        unit = opened.unit
        if unit is None:
            continue
        if unit not in open_units:
            open_units[unit] = _Unit(uid=unit, target=Decimal(0),
                                     second=opened.deal.second_priority)
        target = open_units[unit]
        on = _facts_at(book, uid, start)
        amount = deal_amount_at(book, uid, on) or Decimal(0)
        target.members.append(uid)
        target.target += amount
        target.second = target.second and opened.deal.second_priority
        # Котёл начинается с того, что уже накоплено: остаток сделки — это
        # цель минус накопленное, поэтому накопленное — цель минус остаток.
        target.pot += max(amount - opened.balance, Decimal(0))

    for uid, unit in open_units.items():
        unit.pot = min(unit.pot, unit.target)
        for member in unit.members:
            # В копилке остаток сделки не падает: он стоит, пока единица не
            # закроется, — платежи копятся в котёл, а не в остаток.
            open_deals[member].balance = (deal_amount_at(book, member,
                                                         _facts_at(book, member, start))
                                          or Decimal(0))
            if unit.closed:
                open_deals[member].balance = Decimal(0)

    horizon = _month_start(start, max_months - 1)
    until = _month_end(horizon)

    # Вхождения: окно от начала графика и самых ранних правок — правка может
    # увести вхождение в прокатываемый месяц из-за его начала.
    plan: dict[str, list[Occurrence]] = {}
    for uid, opened in open_deals.items():
        since = start
        rule = opened.deal.schedule
        if rule is not None:
            if rule.start is not None:
                since = min(since, rule.start)
            if rule.first is not None:
                since = min(since, rule.first.date)
        for edit in book.edits:
            if edit.deal == uid:
                since = min(since, edit.planned)
        plan[uid] = occurrences(book, uid, since, until)

    # Бюджет: обязательная нагрузка по месяцам, посчитанная на начало проката.
    baseline: dict[int, Decimal] = {}
    due: dict[int, list[tuple[date, str, Occurrence]]] = {}
    for uid, found in plan.items():
        opened = open_deals[uid]
        if opened.second:
            continue            # второй приоритет платится не по графику, а из свободных
        for occ in found:
            if not occ.payable:
                continue
            when = _service_date(opened.deal, occ.due, start)
            index = _month_index(start, when)
            if index > max_months:
                continue
            due.setdefault(index, []).append((when, uid, occ))
            baseline[index] = (baseline.get(index, Decimal(0))
                               + _payment_amount(opened.deal, occ, opened.balance))
    for rows in due.values():
        rows.sort(key=lambda row: (row[0], row[1]))

    months: list[DealMonth] = []
    total_interest = Decimal(0)
    freedom: date | None = None
    stalled = False
    start_total = sum((o.balance for o in open_deals.values() if not o.second),
                      Decimal(0))

    for index in range(1, max_months + 1):
        month = _month_start(start, index - 1)
        rows = due.get(index, [])
        pool = baseline.get(index, Decimal(0)) + monthly_extra
        paid = Decimal(0)
        prepaid = Decimal(0)
        payments: list[ScheduledPayment] = []

        # 1. Проценты: до платежей месяца, по базе начисления каждой сделки.
        interest = Decimal(0)
        for uid, opened in open_deals.items():
            if opened.unit is not None or opened.balance <= 0:
                continue                   # внутри копилки проценты не идут
            by_day: dict[int, Decimal] = {}
            for when, row_uid, occ in rows:
                if row_uid == uid:
                    by_day[when.day] = (by_day.get(when.day, Decimal(0))
                                        + _payment_amount(opened.deal, occ,
                                                          opened.balance))
            accrued = _month_interest(opened.deal, opened.balance, month, by_day)
            opened.balance += accrued
            interest += accrued
        total_interest += interest
        # Остаток на начало месяца: от него считается минималка процентом —
        # платёж дня на неё не влияет.
        month_start = {uid: o.balance for uid, o in open_deals.items()}

        # 2. Обязательные вхождения месяца — по датам, из общего бюджета.
        for when, uid, occ in rows:
            opened = open_deals[uid]
            unit = open_units[opened.unit] if opened.unit is not None else None
            want = _payment_amount(opened.deal, occ, month_start[uid])
            if unit is not None:
                room = unit.remaining
            elif opened.deal.amount is None:
                room = want             # регулярный расход: остатка нет, платится целиком
            else:
                room = opened.balance
            amount = min(want, room, pool)
            if amount <= 0:
                continue
            pool -= amount
            paid += amount
            if unit is not None:
                unit.pay(amount)
            elif opened.deal.amount is not None:
                opened.pay(amount)
            payments.append(ScheduledPayment(
                date=when, amount=amount, deal=uid,
                counterparty=deal_holder_at(book, uid, when),
                wallet=funding_wallet(opened.deal), planned=occ.planned,
                unit=opened.unit))

        # 3. Свободные деньги — по стратегии, сначала обязательный график.
        for target in _order(_targets(open_deals, open_units, False), strategy):
            if target.remaining <= 0:
                continue
            amount = min(target.remaining, pool)
            if amount <= 0:
                break
            target.pay(amount)
            pool -= amount
            paid += amount
            prepaid += amount

        # 4. Второй приоритет: сам не платится — предлагается.
        second_open = any(o.second and o.balance > 0 for o in open_deals.values())
        second_open = second_open or any(u.second and not u.closed
                                         for u in open_units.values())
        if consent_to_second:
            for target in _order(_targets(open_deals, open_units, True), strategy):
                if target.remaining <= 0:
                    continue
                amount = min(target.remaining, pool)
                if amount <= 0:
                    break
                target.pay(amount)
                pool -= amount
                paid += amount
                prepaid += amount

        for unit in open_units.values():
            if unit.closed:
                for member in unit.members:
                    open_deals[member].balance = Decimal(0)

        months.append(DealMonth(
            index=index, month=month,
            balances={uid: o.balance for uid, o in open_deals.items()},
            units={uid: UnitMonth(u.target, u.pot) for uid, u in open_units.items()},
            total=sum((o.balance for o in open_deals.values() if not o.second),
                      Decimal(0)),
            interest=interest, paid=paid, prepaid=prepaid, free=pool,
            offer=pool if second_open and not consent_to_second else Decimal(0),
            payments=payments,
        ))

        if _all_closed(open_deals, open_units):
            freedom = month
            break
    else:
        stalled = True

    return _result(book, months, total_interest, freedom, start_total, stalled,
                   receivables, gaps, start)


def _targets(open_deals: dict[str, _Open], open_units: dict[str, _Unit],
             second: bool) -> list[_Target]:
    """Куда могут пойти свободные деньги: сделки и копилки одного приоритета."""
    rows: list[_Target] = []
    for uid, opened in open_deals.items():
        if opened.second != second or opened.unit is not None:
            continue
        if opened.deal.amount is None:
            continue                       # регулярный расход не досрочится
        rows.append(_Target(uid, _rate(opened.deal), opened.balance, opened.pay))
    for uid, unit in open_units.items():
        if unit.second != second:
            continue
        rows.append(_Target(uid, Decimal(0), unit.remaining, unit.pay))
    return rows


def _all_closed(open_deals: dict[str, _Open], open_units: dict[str, _Unit]) -> bool:
    """Закрыто ли всё, что закрывается: регулярные расходы и второй приоритет — нет."""
    for opened in open_deals.values():
        if opened.second or opened.deal.amount is None:
            continue
        if opened.balance > 0:
            return False
    return all(u.closed for u in open_units.values() if not u.second)


def _result(book: Settlements, months: list[DealMonth], total_interest: Decimal,
            freedom: date | None, start_total: Decimal, stalled: bool,
            receivables: Iterable[Deal], gaps: list[Gap], start: date) -> DealRoll:
    """Собрать прокат: месяцы плюс то, что в них не входило.

    Требования перечисляются за те же месяцы, что прокатаны: дальше срока проката
    отчёт не идёт.
    """
    first = _month_start(start, 0)
    last = _month_end(months[-1].month) if months else _month_end(first)
    expectations: list[Expectation] = []
    for deal in receivables:
        for occ in occurrences(book, deal.uid, first, last):
            if not occ.payable or occ.remaining is None or occ.remaining <= 0:
                continue
            expectations.append(Expectation(deal.uid,
                                            deal_holder_at(book, deal.uid, occ.due),
                                            occ.due, occ.remaining))
    expectations.sort(key=lambda e: e.date)
    return DealRoll(months, total_interest, freedom, start_total, stalled,
                    expectations, gaps)


def compare_deal_strategies(book: Settlements, start: date, monthly_extra: Decimal,
                            strategies: Sequence[str] = STRATEGIES,
                            max_months: int = 600,
                            consent_to_second: bool = False
                            ) -> list[tuple[str, DealRoll]]:
    """Прогнать один и тот же портфель сделок разными стратегиями."""
    return [(name, roll_deals(book, start, monthly_extra, strategy=name,
                              max_months=max_months,
                              consent_to_second=consent_to_second))
            for name in strategies]
