"""Прокат сделок по месяцам: что платится, что копится и когда закрывается.

Отвечает на вопрос «когда я выйду из долгов», считая вперёд по месяцам: проценты
по базе начисления сделки, обязательные вхождения по графику и свободные деньги по
выбранной стратегии. По дням внутри месяца деньги считает касса (`solver`): прокат
сделок отдаёт ей расписание (`DealMonth.payments`), а обратно получает бюджет
досрочек — чередует шаги сторон драйвер прохода по месяцам, и состояние каждой
стороны между вызовами объявлено явно (`_DealsRoll` в `roll`,
`_CashRoll`/`_CashMonth` в `solver`; `docs/decisions/schedule-to-cash.md`,
`docs/decisions/month-by-month-convergence.md`).

Правила, запертые здесь:

- **Платит только то, что должен я.** Сделки, где должны мне, в прокат не входят:
  они перечисляются требованиями (`Expectation`) и в кассу не подмешиваются.
- **Освободившийся платёж остаётся в бюджете.** Бюджет месяца — обязательная
  нагрузка, посчитанная на начало проката, плюс свободные деньги: закрылась сделка
  раньше срока — её платёж идёт другим. Это и есть снежный ком.
- **Копилка.** Сделки одной единицы закрытия гасят друг друга вместе: платежи
  копятся в котёл, остаток не падает, цель (сумма по единице) фиксирована и не
  растёт — процентов внутри копилки нет.
- **Второй приоритет сам не платится.** Прокат показывает срез свободных
  денег месяца — сколько их не нашло места (`DealMonth.offer`) — и предлагает
  направить его туда; считать погашение он начинает только после
  согласия владельца (`consent_to_second`). Ждать при этом не бесплатно: долг
  растёт по ставке сделки.
- **Пробел — не ноль.** Сделка с неизвестной ставкой или без правила графика в
  прокат не входит и перечисляется пробелом (`Gap`).
- **Досрочка — тоже платёж.** Она уходит в расписание платежом без вхождения
  (`ScheduledPayment.planned is None`): деньги покидают кассу, и касса обязана это
  видеть. Свободные деньги считаются до досрочек — это и есть их бюджет.

Прокат месячный: платежи внутри месяца агрегируются в его итог, а дни у платежей
остаются — по ним начисляются проценты у дневной ставки. Считает их одна функция
в `settlements` (`accrued_interest`): прокат передаёт ей месяц целиком — остаток
на его начало и платежи месяца, — и берёт число без второго округления. Точку
отсчёта и бюджет досрочек движок не выдумывает: и то и другое передаётся снаружи.
"""
from __future__ import annotations

import calendar
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .model import Account, Income, Payment, Scenario, Transfer, kopek
from .settlements import (I_OWE, OWED_TO_ME, Deal, Occurrence, Settlements,
                          Wallet, accrued_interest, deal_amount_at,
                          deal_balance, deal_holder_at, funding_wallet,
                          occurrences, planned_date)
from .solver import _CashMonth, _CashRoll

DAYS_IN_YEAR = Decimal(365)

#: Стратегии досрочек: лавина — дорогая ставка первой, снежный ком — мелкий остаток.
AVALANCHE = "avalanche"
SNOWBALL = "snowball"
STRATEGIES = (AVALANCHE, SNOWBALL)

#: Причины конца окна — по порядку важности: если причины совпали, побеждает
#: первая, потому что она полезнее владельцу (закрылись долги — вопрос был об этом).
WINDOW_DEBTS_CLOSED = "долги закрыты"
WINDOW_INCOME_ENDS = "кончились доходы"
WINDOW_MONTH_CAP = "кончился предел месяцев"
WINDOW_REASONS = (WINDOW_DEBTS_CLOSED, WINDOW_INCOME_ENDS, WINDOW_MONTH_CAP)

#: Почему срок по графику не наступил: за отведённые месяцы долг не закрылся.
#: Одна формулировка на оба случая — платёж не покрывает проценты и график
#: кончился, а остаток остался: различать их — новая логика ради диагностики.
PAYOFF_NOT_CLOSED = "за отведённые месяцы долг не закрылся"

#: Почему у срока по графику вместо даты первый месяц: долг погашен до начала
#: проката — закрывать нечего. Тем же словом закрыт и второй приоритет.
PAYOFF_CLOSED_BEFORE = "закрыт к началу проката: закрывать нечего"


# --- что прокат отдаёт наружу ----------------------------------------------

@dataclass
class ScheduledPayment:
    """Платёж расписания: что, когда и с какого кошелька уходит.

    `planned` — плановая дата вхождения, по которой платёж опознаётся; `None` —
    досрочка: вхождения у неё нет, деньги уходят сверх графика. `unit` — копилка,
    если платёж идёт в неё, а не в остаток сделки.

    `debt` — платёж долговой: у сделки с остатком платёж гасит долг, у
    регулярного расхода — нет, а досрочка долговая всегда. Признак нужен кассе:
    долговой платёж кредитным лимитом не финансируется (`Payment.debt`).
    """
    date: date
    amount: Decimal
    deal: str
    counterparty: str
    wallet: str | None
    planned: date | None = None
    unit: str | None = None
    debt: bool = True


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
    месяц, `paid` — ушло за месяц, из него `prepaid` — досрочки, `short` —
    урезано: сколько из обязательного `want` не заплачено (сумма `want − amount`
    по обязательным вхождениям месяца, урезанным пулом, остатком или котлом;
    аррерис в следующий месяц не переносится). `offer` — **срез свободных
    денег месяца**, которым не нашлось места: свободные деньги месяца минус
    ушедшие досрочки — столько предлагается направить во второй приоритет.
    `payments` — расписание месяца: дата, сумма, кошелёк и контрагент каждого
    платежа.
    """
    index: int
    month: date
    balances: dict[str, Decimal]
    units: dict[str, UnitMonth]
    total: Decimal
    interest: Decimal
    paid: Decimal
    prepaid: Decimal
    short: Decimal
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
    """Прокат сделок: месяцы, обе даты срока и пробелы.

    `freedom` — **срок с досрочками**: месяц закрытия последней сделки первого
    приоритета при текущем бюджете досрочек; None вместе с `stalled` значит, что
    за окно долги не закрылись. Регулярные расходы на срок не влияют: закрывать
    там нечего.

    `payoff_by_graph` — **срок по графику**: месяц, когда те же сделки закрылись
    бы своими платежами, без досрочек. Гарантированный верх: считается тем же
    прокатом с нулевым бюджетом досрочек и до `max_months`, поэтому окном кассы
    не сужается и есть у любого графика. `payoff_by_graph_reason` называет
    причину рядом с датой: `PAYOFF_NOT_CLOSED` — за отведённые месяцы долг не
    закрылся (даты тогда нет), `PAYOFF_CLOSED_BEFORE` — закрывать было нечего
    (дата тогда первый месяц проката). Дата без причины и пустое поле без неё
    были бы неотличимы от «не посчитали».

    `second_freedom` — месяц закрытия второго приоритета. Сам он платится только
    по согласию владельца, и тогда прокат идёт, пока не закроется и он; закрыться
    он может и без согласия — взыскание, попавшее в единицу закрытия, гасится
    вместе с ней первым приоритетом, и дата тоже видна.

    `window` — окно проката: до какого месяца он заглядывает и почему там
    кончилось (`Window`).
    """
    months: list[DealMonth]
    total_interest: Decimal
    freedom: date | None
    payoff_by_graph: date | None
    payoff_by_graph_reason: str | None
    start_total: Decimal
    stalled: bool
    expectations: list[Expectation]
    gaps: list[Gap]
    window: Window
    second_freedom: date | None = None

    @property
    def total_paid(self) -> Decimal:
        return sum((s.paid for s in self.months), Decimal(0))


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


def horizon(start: date, max_months: int) -> date:
    """Последний день окна проката: дальше прокат не заглядывает.

    Одно правило на прокат и на действия: за горизонт не заглядывает ни платёж,
    ни мост, ни перенос.
    """
    return _month_end(_month_start(start, max_months - 1))


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

    У обязательного платежа процентом от остатка сумма вхождения становится
    известна только здесь: в бюджете она считается от остатка на начало проката
    (бюджет фиксируют заранее), а в платеже — от остатка на начало месяца.
    """
    if occ.remaining is not None:
        return occ.remaining
    percent = deal.schedule.percent if deal.schedule is not None else None
    if percent is None:
        return Decimal(0)
    return kopek(balance * percent)


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
    if deal.wallet is None:
        return "финансирующий кошелёк не задан: платить не с чего"
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


# --- окно проката -----------------------------------------------------------

@dataclass(frozen=True)
class Window:
    """Окно проката: до какого месяца он заглядывает и почему там кончилось.

    Окно — минимум из трёх границ: последний платёж по сделкам с остатком (по
    графику), конец видимых доходов и `max_months`. За окном вопрос кончился:
    месяц за его концом был бы выдумкой, а не расчётом.

    `reason` — одно значение из объявленного набора (`WINDOW_REASONS`) по
    порядку важности: «долги закрыты» → «кончились доходы» → «кончился предел
    месяцев». Совпадение причин безвредно: первый по порядку ответ полезнее.
    """
    months: int
    until: date
    reason: str


def _last_payment(deal: Deal, book: Settlements) -> date | None:
    """Последнее вхождение сделки по графику; None — последнего платежа нет.

    График, а не прокат: прокат зависит от свободных денег, и окно стало бы
    результатом того, что само от него зависит. График при этом —
    гарантированный верх: досрочки закрывают долг раньше, но никогда позже.

    Конец есть только у ряда с числом платежей (`count` и `start`); у процента
    от остатка и у платежа без числа раз последнего вхождения нет. Отдельный
    первый платёж — единственное вхождение такой сделки. Перенесённое вхождение
    платится по назначенной дате, поэтому правка, уводящая платёж позже, окно
    удлиняет.
    """
    rule = deal.schedule
    if rule is None:
        return None
    if rule.days:
        if rule.count is None or rule.start is None:
            return None
        days = sorted(rule.days)
        months, position = divmod(rule.count - 1, len(days))
        first = _month_start(rule.start, months)
        when = planned_date(first.year, first.month, days[position],
                            rule.shift_weekend)
    elif rule.first is not None:
        when = planned_date(rule.first.date.year, rule.first.date.month,
                            rule.first.date.day, rule.shift_weekend)
    else:
        return None
    # Поздняя правка побеждает: перенесённый платёж уходит назначенной датой.
    moved: dict[date, date | None] = {}
    for edit in book.edits:
        if edit.deal == deal.uid:
            moved[edit.planned] = edit.moved_to
    for assigned in moved.values():
        if assigned is not None and assigned > when:
            when = assigned
    return when


def _rollable(book: Settlements,
              start: date) -> tuple[set[str], dict[str, str | None]]:
    """Кто в прокате: закрытые сделки и пробелы — по сделке и по единице закрытия.

    Закрытое прокатывать нечего — и пробелом оно не считается: сначала
    закрытость, потом всё остальное. Иначе погашенная сделка без ставки
    объявила бы пробелом всю свою копилку и живые долги не прокатались бы.
    """
    closed = {d.uid for d in book.deals
              if d.amount is not None and d.direction == I_OWE
              and (deal_balance(book, d.uid, _facts_at(book, d.uid, start))
                   or Decimal(0)) <= 0}
    reasons: dict[str, str | None] = {
        d.uid: (None if d.uid in closed else _gap_reason(d)) for d in book.deals}
    broken = _broken_units(book, reasons)
    for deal in book.deals:
        if reasons[deal.uid] is None and deal.closure_unit is not None:
            reasons[deal.uid] = broken.get(deal.closure_unit)
    return closed, reasons


def _debts_end(book: Settlements, start: date, closed: set[str],
               reasons: dict[str, str | None],
               consent_to_second: bool) -> date | None:
    """Последний платёж по сделкам с остатком; None — конец по долгам не наступит.

    Регулярный расход остатка не имеет и окно не продлевает: закрывать там
    нечего. Сделка без числа платежей границы не даёт вовсе: её ряд не
    кончается, и окно, кончившееся раньше, закрыло бы вопрос при живом долге.
    Второй приоритет входит в границу только при согласии владельца: без
    согласия он не платится.

    Просроченное вхождение платится в первый месяц проката (`_service_date`),
    поэтому дат в прошлом у границы не бывает.
    """
    last: date | None = None
    for deal in book.deals:
        if deal.direction != I_OWE or deal.amount is None:
            continue                       # регулярный расход: закрывать нечего
        if deal.uid in closed or reasons.get(deal.uid) is not None:
            continue                       # закрыта или пробел: в прокат не входит
        if deal.second_priority and not consent_to_second:
            continue                       # без согласия он не платится
        found = _last_payment(deal, book)
        if found is None:
            return None                    # ряда нет конца — нет и границы
        when = _service_date(deal, found, start)
        last = when if last is None else max(last, when)
    return last


def roll_window(book: Settlements, start: date, max_months: int, *,
                income_horizon: date | None = None,
                consent_to_second: bool = False) -> Window:
    """Где кончается окно проката и почему: минимум из трёх границ.

    Границы — последний платёж по сделкам с остатком (по графику), конец
    видимых доходов и `max_months`. Конец видимых доходов — объявленный
    горизонт данных о доходах: список приходов может быть видимым срезом, а не
    концом данных, и неизвестное не подставляется нулём. Горизонт не задан —
    доходы окно не ограничивают.

    При совпадении границ побеждает причина, первая по порядку важности:
    «долги закрыты» → «кончились доходы» → «кончился предел месяцев».
    """
    closed, reasons = _rollable(book, start)
    bounds: list[tuple[int, str]] = [(max_months, WINDOW_MONTH_CAP)]
    if income_horizon is not None:
        bounds.append((_month_index(start, income_horizon), WINDOW_INCOME_ENDS))
    debts = _debts_end(book, start, closed, reasons, consent_to_second)
    if debts is not None:
        bounds.append((_month_index(start, debts), WINDOW_DEBTS_CLOSED))
    order = {reason: place for place, reason in enumerate(WINDOW_REASONS)}
    months, reason = min(bounds, key=lambda bound: (bound[0], order[bound[1]]))
    return Window(months, horizon(start, months), reason)


# --- прокат ----------------------------------------------------------------

def roll_deals(book: Settlements, start: date, monthly_extra: Decimal,
               strategy: str = AVALANCHE, max_months: int = 600,
               consent_to_second: bool = False,
               budgets: Mapping[int, Decimal] | None = None,
               income_horizon: date | None = None,
               window: Window | None = None) -> DealRoll:
    """Прокатить сделки по месяцам от `start`.

    Бюджет месяца — обязательная нагрузка по графику плюс `monthly_extra`;
    нагрузка считается один раз, на начало проката, поэтому освободившийся платёж
    остаётся в бюджете. Обязательные вхождения платятся по датам, свободные
    деньги уходят по стратегии — сначала обязательный график, потом досрочки.

    `budgets` — бюджет досрочек по месяцам (индекс → сумма); если задан,
    используется вместо `monthly_extra`. Нужен для связки с кассой: свободные
    деньги месяца становятся бюджетом досрочек. Кассы в этом пути нет, и
    свободные деньги месяца — сам переданный бюджет: из них и считается
    срез-предложение второму приоритету.

    Второй приоритет не платится, пока владелец не дал согласия
    (`consent_to_second`); без согласия срез свободных денег, которым не
    нашлось места, показывается предложением (`DealMonth.offer`). Согласие
    считается до конца: прокат идёт, пока не закроется и второй приоритет, —
    иначе даты его закрытия не видно (`DealRoll.second_freedom`).

    Окно проката считается здесь же (`roll_window`): дальше последнего платежа
    по долгам, конца видимых доходов и `max_months` прокат не заглядывает, а
    причина конца окна видна в `DealRoll.window`. Готовое окно можно передать
    снаружи (`window`) — так цена варианта считает его в окне базы: сравнение
    идёт по одной линии, а не по разным. Переданное окно используется как есть и
    обязано быть посчитано по этой же книге и с этим же согласием — иначе отчёт
    разойдётся с книгой.

    Отдаёт **обе даты** одного вопроса «когда выйду из долгов»: срок по графику
    (`DealRoll.payoff_by_graph` — тот же прокат с нулевым бюджетом досрочек, до
    `max_months`) и срок с досрочками (`DealRoll.freedom` — при текущем бюджете).
    Окно кассы срок по графику не сужает: он гарантированный верх и есть у
    любого графика. Не закрылось — сказано причиной рядом с датой
    (`DealRoll.payoff_by_graph_reason`), а не пустым полем.
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"неизвестная стратегия: {strategy!r}")

    # Срок по графику — отдельный проход без бюджета досрочек; он считается
    # один раз на весь прокат, а не повторяется внутри связки с кассой.
    graph = _graph_pass(book, start, strategy, max_months, consent_to_second)
    payoff, reason = _payoff_pair(graph, start)

    return _roll_pass(book, start, monthly_extra, strategy, max_months,
                      consent_to_second, budgets, income_horizon, window,
                      payoff, reason)


def _graph_pass(book: Settlements, start: date, strategy: str,
                max_months: int, consent_to_second: bool) -> DealRoll:
    """Срок по графику: тот же прокат сделок, но без бюджета досрочек.

    Окно у него своё — по графику и `max_months`: окно кассы (конец видимых
    доходов) его не сужает, иначе у долга, переживающего доходы, даты бы не
    было. Даты в этом проходе не считаны (`None`): срок по графику — это его
    `freedom`, а пара «дата и причина» собирается `_payoff_pair`.
    """
    return _roll_pass(book, start, Decimal(0), strategy, max_months,
                      consent_to_second, budgets=None, income_horizon=None,
                      window=None, payoff_by_graph=None,
                      payoff_by_graph_reason=None)


def _payoff_pair(graph: DealRoll, start: date) -> tuple[date | None, str | None]:
    """Дата и причина срока по графику — по прокату с нулевым бюджетом."""
    if graph.start_total <= 0 and not graph.gaps:
        # Закрывать было нечего: долг погашен до начала проката. Пробел сюда не
        # попадает: не смоделированный долг — не закрытый, а не посчитанный.
        return _month_start(start, 0), PAYOFF_CLOSED_BEFORE
    if graph.start_total <= 0 or graph.freedom is None:
        # Пробелы вместо прокатываемых долгов либо долг не закрылся за
        # отведённые месяцы: даты нет, причина названа рядом.
        return None, PAYOFF_NOT_CLOSED
    return graph.freedom, None


@dataclass
class _DealsEntry:
    """Снимок состояния сделок на входе месяца: повтор шага начинается с него."""
    balances: dict[str, Decimal]
    pots: dict[str, Decimal]
    total_interest: Decimal


@dataclass
class _MonthLoad:
    """Шаг месяца сделок: обязательные платежи и остаток пула до досрочек.

    `pool` — зафиксированная нагрузка месяца плюс бюджет: из него платятся
    обязательные вхождения, остаток уходит в досрочки (`finish`). `paid` —
    уплачено на этом шаге, `short` — урезано из обязательного, `interest` —
    начислено процентов, `payments` — расписание месяца на этом шаге.
    """
    index: int
    month: date
    pool: Decimal
    paid: Decimal
    short: Decimal
    interest: Decimal
    payments: list[ScheduledPayment]


class _DealsRoll:
    """Прокат сделок: явное состояние между месяцами.

    Состояние — открытые остатки (`open_deals`), котлы (`open_units`) и
    зафиксированная нагрузка (`baseline` и `due`, посчитанные один раз на
    старте). Шаг месяца разделён на два вызова: `begin()` считает проценты и
    обязательные вхождения (их суммы зависят от остатков сделок и пула),
    `finish()` — досрочки и итог месяца. Между ними драйвер спрашивает кассу,
    сколько денег свободно: бюджет месяца приходит из кассы того же месяца.
    Состояние объявлено полями — его видно глазами и можно проверить тестом,
    а не спрятано в замыкании.
    """

    def __init__(self, book: Settlements, start: date, *, strategy: str,
                 consent_to_second: bool, max_months: int = 600,
                 income_horizon: date | None = None,
                 window: Window | None = None) -> None:
        self.book = book
        self.start = start
        self.strategy = strategy
        self.consent_to_second = consent_to_second

        closed, reasons = _rollable(book, start)
        self.window = window if window is not None else roll_window(
            book, start, max_months, income_horizon=income_horizon,
            consent_to_second=consent_to_second)
        # Шаг текущего месяца: его читает драйвер, если предохранитель
        # внутреннего круга сработал и шаг остался незаконченным.
        self.load: _MonthLoad | None = None

        self.gaps: list[Gap] = []
        self.open_deals: dict[str, _Open] = {}
        self.open_units: dict[str, _Unit] = {}
        self.receivables: list[Deal] = []

        for deal in book.deals:
            if deal.uid in closed:
                continue                       # уже закрыта: прокатывать нечего
            reason = reasons[deal.uid]
            if reason is not None:
                self.gaps.append(Gap(deal.uid, reason))
                continue
            if deal.direction == OWED_TO_ME:
                self.receivables.append(deal)
                continue
            self.open_deals[deal.uid] = _Open(
                deal, deal_balance(book, deal.uid, _facts_at(book, deal.uid, start))
                or Decimal(0), deal.closure_unit, deal.second_priority)

        for uid, opened in self.open_deals.items():
            unit = opened.unit
            if unit is None:
                continue
            if unit not in self.open_units:
                self.open_units[unit] = _Unit(uid=unit, target=Decimal(0),
                                              second=opened.deal.second_priority)
            target = self.open_units[unit]
            on = _facts_at(book, uid, start)
            amount = deal_amount_at(book, uid, on) or Decimal(0)
            target.members.append(uid)
            target.target += amount
            target.second = target.second and opened.deal.second_priority
            # Котёл начинается с того, что уже накоплено: остаток сделки — это
            # цель минус накопленное, поэтому накопленное — цель минус остаток.
            target.pot += max(amount - opened.balance, Decimal(0))

        for uid, unit in self.open_units.items():
            unit.pot = min(unit.pot, unit.target)
            for member in unit.members:
                # В копилке остаток сделки не падает: он стоит, пока единица не
                # закроется, — платежи копятся в котёл, а не в остаток.
                self.open_deals[member].balance = (deal_amount_at(book, member,
                                                                  _facts_at(book, member, start))
                                                   or Decimal(0))
                if unit.closed:
                    self.open_deals[member].balance = Decimal(0)

        until = self.window.until

        # Вхождения: окно от начала графика и самых ранних правок — правка может
        # увести вхождение в прокатываемый месяц из-за его начала.
        plan: dict[str, list[Occurrence]] = {}
        for uid, opened in self.open_deals.items():
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

        # Зафиксированная нагрузка: обязательная нагрузка по месяцам,
        # посчитанная на начало проката. Бюджет месяца её не пересчитывает —
        # суммы обязательного берутся из остатков сделок.
        baseline: dict[int, Decimal] = {}
        due: dict[int, list[tuple[date, str, Occurrence]]] = {}
        for uid, found in plan.items():
            opened = self.open_deals[uid]
            if opened.second:
                continue            # второй приоритет платится не по графику, а из свободных
            for occ in found:
                if not occ.payable:
                    continue
                when = _service_date(opened.deal, occ.due, start)
                index = _month_index(start, when)
                if index > self.window.months:
                    continue
                due.setdefault(index, []).append((when, uid, occ))
                baseline[index] = (baseline.get(index, Decimal(0))
                                   + _payment_amount(opened.deal, occ, opened.balance))
        for rows in due.values():
            rows.sort(key=lambda row: (row[0], row[1]))
        self.baseline = baseline
        self.due = due

        self.months: list[DealMonth] = []
        self.total_interest = Decimal(0)
        self.freedom: date | None = None
        self.second_freedom: date | None = None
        self.done = False
        self.start_total = sum((o.balance for o in self.open_deals.values()
                                if not o.second), Decimal(0))
        # Второй приоритет был открыт на начало проката: закрытие пустого
        # приоритета датой не объявляется.
        self.second_start = _second_open(self.open_deals, self.open_units)

    def entry(self) -> _DealsEntry:
        """Снимок состояния на входе месяца: повтор шага начинается с него."""
        return _DealsEntry(
            {uid: o.balance for uid, o in self.open_deals.items()},
            {uid: u.pot for uid, u in self.open_units.items()},
            self.total_interest)

    def restore(self, snap: _DealsEntry) -> None:
        """Вернуть состояние к снимку: внутренний круг месяца начинается заново."""
        for uid, balance in snap.balances.items():
            self.open_deals[uid].balance = balance
        for uid, pot in snap.pots.items():
            self.open_units[uid].pot = pot
        self.total_interest = snap.total_interest

    def begin(self, index: int, extra: Decimal) -> _MonthLoad:
        """Шаг месяца: проценты по базе начисления и обязательные вхождения.

        Суммы обязательного берутся из остатков сделок и пула —
        зафиксированная нагрузка месяца плюс `extra` (бюджет месяца, он
        приходит от кассы). Шаг повторяется с новым бюджетом после `restore()`.
        """
        month = _month_start(self.start, index - 1)
        rows = self.due.get(index, [])
        pool = self.baseline.get(index, Decimal(0)) + extra
        paid = Decimal(0)
        short = Decimal(0)
        payments: list[ScheduledPayment] = []

        # 1. Проценты: до платежей месяца, по базе начисления каждой сделки.
        # Их считает settlements (`accrued_interest`): слагаемые округлены
        # там, второй раз проценты здесь не округляются.
        interest = Decimal(0)
        for uid, opened in self.open_deals.items():
            if opened.unit is not None or opened.balance <= 0:
                continue                   # внутри копилки проценты не идут
            # Платежи месяца — с суммой вхождения на начало месяца: на них
            # считается дневная база, а платится по шагу 2 сколько выйдет.
            month_payments = [
                (when, _payment_amount(opened.deal, occ, opened.balance))
                for when, row_uid, occ in rows if row_uid == uid]
            accrued = accrued_interest(opened.deal, opened.balance, month,
                                       _month_end(month), month_payments)
            opened.balance += accrued
            interest += accrued
        self.total_interest += interest
        # Остаток на начало месяца: от него считается обязательный платёж процентом —
        # платёж дня на неё не влияет.
        month_start = {uid: o.balance for uid, o in self.open_deals.items()}

        # 2. Обязательные вхождения месяца — по датам, из общего бюджета.
        for when, uid, occ in rows:
            opened = self.open_deals[uid]
            unit = (self.open_units[opened.unit]
                    if opened.unit is not None else None)
            want = _payment_amount(opened.deal, occ, month_start[uid])
            if unit is not None:
                room = unit.remaining
            elif opened.deal.amount is None:
                room = want             # регулярный расход: остатка нет, платится целиком
            else:
                room = opened.balance
            amount = min(want, room, pool)
            # Урезание видно всегда, даже когда не прошло ничего: недоплата
            # месяца — сумма want − max(amount, 0) (в отрицательном пуле не
            # заплачено ровно want), аррерисом она не станет.
            short += want - max(amount, Decimal(0))
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
                counterparty=deal_holder_at(self.book, uid, when),
                wallet=funding_wallet(opened.deal), planned=occ.planned,
                unit=opened.unit,
                # Сделка с остатком — долг, регулярный расход — жизнь.
                debt=opened.deal.amount is not None))
        self.load = _MonthLoad(index, month, pool, paid, short, interest,
                               payments)
        return self.load

    def finish(self, load: _MonthLoad, free: Decimal) -> list[ScheduledPayment]:
        """Шаг месяца: досрочки по стратегии, второй приоритет и итог месяца.

        Досрочки идут из остатка пула — обязательные уже заплачены, а
        освободившийся платёж остаётся в бюджете. `free` — свободные деньги
        месяца (в связке — `CashMonth.free`, в прокате без кассы — бюджет,
        который передал вызывающий): предложение второму приоритету считается
        срезом этих денег, а не остатком пула. Возвращает досрочки месяца:
        их касса проведёт после обязательных, когда деньги месяца уже
        посчитаны.
        """
        month = load.month
        pool, paid = load.pool, load.paid
        prepaid = Decimal(0)
        prepayments: list[ScheduledPayment] = []
        open_deals, open_units = self.open_deals, self.open_units
        consent = self.consent_to_second

        # 3. Свободные деньги — по стратегии, сначала обязательный график.
        for target in _order(_targets(open_deals, open_units, False),
                             self.strategy):
            if target.remaining <= 0:
                continue
            amount = min(target.remaining, pool)
            if amount <= 0:
                break
            target.pay(amount)
            pool -= amount
            paid += amount
            prepaid += amount
            payment = _prepayment(self.book, open_deals, open_units, target,
                                  amount, month)
            load.payments.append(payment)
            prepayments.append(payment)

        # 4. Второй приоритет: сам не платится — предлагается. Предложение —
        # срез свободных денег месяца, которым не нашлось места: свободные
        # деньги минус ушедшие досрочки, а не остаток пула. Отрицательный
        # срез — нехватка, а не предложение: не бывает его меньше нуля, как и
        # бюджета досрочек.
        second_open = _second_open(open_deals, open_units)
        if consent:
            for target in _order(_targets(open_deals, open_units, True),
                                 self.strategy):
                if target.remaining <= 0:
                    continue
                amount = min(target.remaining, pool)
                if amount <= 0:
                    break
                target.pay(amount)
                pool -= amount
                paid += amount
                prepaid += amount
                payment = _prepayment(self.book, open_deals, open_units, target,
                                      amount, month)
                load.payments.append(payment)
                prepayments.append(payment)

        for unit in open_units.values():
            if unit.closed:
                for member in unit.members:
                    open_deals[member].balance = Decimal(0)

        offer = (max(free - prepaid, Decimal(0))
                 if second_open and not consent else Decimal(0))

        self.months.append(DealMonth(
            index=load.index, month=month,
            balances={uid: o.balance for uid, o in open_deals.items()},
            units={uid: UnitMonth(u.target, u.pot) for uid, u in open_units.items()},
            total=sum((o.balance for o in open_deals.values() if not o.second),
                      Decimal(0)),
            interest=load.interest, paid=paid, prepaid=prepaid,
            short=load.short, offer=offer,
            payments=load.payments,
        ))

        first_done = _all_closed(open_deals, open_units)
        second_done = not _second_open(open_deals, open_units)
        if first_done and self.freedom is None:
            self.freedom = month
        if self.second_start and second_done and self.second_freedom is None:
            self.second_freedom = month
        self.done = first_done and (second_done or not consent)
        return prepayments

    def result(self, payoff_by_graph: date | None,
               payoff_by_graph_reason: str | None) -> DealRoll:
        """Собрать прокат: месяцы плюс то, что в них не входило.

        `stalled` — прокат кончился без свободы первого приоритета: закрывать
        было нечего или не удалось. Второй приоритет сюда не входит — о нём
        говорит `second_freedom`.
        """
        return _result(self.book, self.months, self.total_interest,
                       self.freedom, payoff_by_graph, payoff_by_graph_reason,
                       self.start_total, self.freedom is None,
                       self.receivables, self.gaps, self.start, self.window,
                       self.second_freedom)


def _roll_pass(book: Settlements, start: date, monthly_extra: Decimal,
               strategy: str, max_months: int, consent_to_second: bool,
               budgets: Mapping[int, Decimal] | None,
               income_horizon: date | None, window: Window | None,
               payoff_by_graph: date | None,
               payoff_by_graph_reason: str | None) -> DealRoll:
    """Один прокат сделок: обе даты приходят снаружи — их считает `roll_deals`."""
    if strategy not in STRATEGIES:
        raise ValueError(f"неизвестная стратегия: {strategy!r}")

    deals = _DealsRoll(book, start, strategy=strategy,
                       consent_to_second=consent_to_second,
                       max_months=max_months, income_horizon=income_horizon,
                       window=window)
    for index in range(1, deals.window.months + 1):
        # Бюджет месяца приходит снаружи: из словаря связки или одним числом.
        # В этом пути кассы рядом нет — переданный бюджет и есть свободные
        # деньги месяца, их срезом считается предложение второму приоритету.
        extra = (budgets.get(index, Decimal(0)) if budgets is not None
                 else monthly_extra)
        deals.finish(deals.begin(index, extra), extra)
        if deals.done:
            break
    return deals.result(payoff_by_graph, payoff_by_graph_reason)


def _prepayment(book: Settlements, open_deals: dict[str, _Open],
                open_units: dict[str, _Unit], target: _Target,
                amount: Decimal, month: date) -> ScheduledPayment:
    """Досрочка как платёж расписания: вхождения нет, деньги уходят сверх графика.

    Дата — конец месяца: свободные деньги становятся известны, когда обязательные
    платежи месяца уже прошли. У копилки платёж числится за первым участником.
    Досрочка всегда долговая: движок направляет её на долг, а долг кредитным
    лимитом не платится.
    """
    unit = open_units.get(target.uid)
    uid = unit.members[0] if unit is not None else target.uid
    when = _month_end(month)
    return ScheduledPayment(
        date=when, amount=amount, deal=uid,
        counterparty=deal_holder_at(book, uid, when),
        wallet=funding_wallet(open_deals[uid].deal), planned=None,
        unit=unit.uid if unit is not None else None, debt=True)


def _targets(open_deals: dict[str, _Open], open_units: dict[str, _Unit],
             second: bool) -> list[_Target]:
    """Куда могут пойти свободные деньги: сделки и копилки одного приоритета.

    Сделка с `prepay=False` в список не попадает: досрочка ей запрещена, и
    свободные деньги идут дальше по стратегии. Копилка досрочится вся целиком,
    поэтому запрет любого её участника запрещает и копилку: иначе досрочка
    обошла бы запрет через единицу закрытия. Ставка копилки — максимальная
    ставка участника; она участвует только в ранжировании (`_order`), на суммы
    не влияет.
    """
    rows: list[_Target] = []
    for uid, opened in open_deals.items():
        if opened.second != second or opened.unit is not None:
            continue
        if opened.deal.amount is None:
            continue                       # регулярный расход не досрочится
        if not opened.deal.prepay:
            continue                       # досрочка этой сделки запрещена
        rows.append(_Target(uid, _rate(opened.deal), opened.balance, opened.pay))
    for uid, unit in open_units.items():
        if unit.second != second:
            continue
        if any(not open_deals[member].deal.prepay for member in unit.members):
            continue                       # запрет участника запрещает копилку
        # Ставка копилки — максимальная ставка участника: дорогая копилка
        # гасится первой, а не после любой standalone-сделки с ненулевой ставкой.
        rate = max(_rate(open_deals[member].deal) for member in unit.members)
        rows.append(_Target(uid, rate, unit.remaining, unit.pay))
    return rows


def _all_closed(open_deals: dict[str, _Open], open_units: dict[str, _Unit]) -> bool:
    """Закрыто ли всё, что закрывается: регулярные расходы и второй приоритет — нет."""
    for opened in open_deals.values():
        if opened.second or opened.deal.amount is None:
            continue
        if opened.balance > 0:
            return False
    return all(u.closed for u in open_units.values() if not u.second)


def _second_open(open_deals: dict[str, _Open],
                 open_units: dict[str, _Unit]) -> bool:
    """Открыт ли ещё второй приоритет: сам он не платится, но закрыться может."""
    if any(o.second and o.balance > 0 for o in open_deals.values()):
        return True
    return any(u.second and not u.closed for u in open_units.values())


def _result(book: Settlements, months: list[DealMonth], total_interest: Decimal,
            freedom: date | None, payoff_by_graph: date | None,
            payoff_by_graph_reason: str | None, start_total: Decimal,
            stalled: bool, receivables: Iterable[Deal], gaps: list[Gap],
            start: date, window: Window,
            second_freedom: date | None = None) -> DealRoll:
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
    return DealRoll(months, total_interest, freedom, payoff_by_graph,
                    payoff_by_graph_reason, start_total, stalled,
                    expectations, gaps, window, second_freedom)


#: Внутренний предел итераций месяца: бюджет месяца ↔ непрошедшее ↔ свободные
#: деньги связаны через обещанное (непрошедший платёж не отменён, деньги на
#: него уже обещаны), поэтому круг внутри месяца считается, а не угадывается.
#: Предел мал: исчерпание — сигнал патологии внутри месяца, а не длинного графика.
_MONTH_PASSES = 10


class ConvergenceError(Exception):
    """Внутренний круг месяца не сошёлся за предел итераций.

    Это предохранитель: он ловит патологию связки внутри месяца, а не длинный
    график. Драйвер ловит его и превращает в признак результата
    (`MonthsRoll.unconverged`) — расчёт не роняется исключением, а прогноз
    становится неполным.
    """


@dataclass
class MonthsRoll:
    """Прокат месяцев: долги + касса, месяц за месяцем.

    `deal_roll` — прокат сделок; `cash_months` — касса по месяцам; `assumed` —
    месяцы, посчитанные на допущении (после дыры прокат не останавливается);
    `unconverged` — месяцы, которые не сошлись за внутренний предел: признак
    несходимости живёт в результате и делает прогноз неполным, а расчёт не
    роняется исключением.
    """
    deal_roll: DealRoll
    cash_months: list["CashMonth"]
    assumed: list[int] = field(default_factory=list)
    unconverged: list[int] = field(default_factory=list)

    @property
    def converged(self) -> bool:
        """Сошёлся ли расчёт: False — хотя бы один месяц не сошёлся за предел."""
        return not self.unconverged

    @property
    def unsecured_total(self) -> Decimal:
        """Сколько за весь прокат не прошло из-за ёмкости своего кошелька.

        Не ноль — прогноз долгов держится на переводе: столько денег не дошло до
        получателей, и долг закроется только после того, как их переведут.
        Каждый такой платёж назван в `cash_months` вместе с кошельком-источником.
        """
        return sum((cm.unsecured_total for cm in self.cash_months), Decimal(0))

    @property
    def window(self) -> Window:
        """Окно проката: до какого месяца он заглядывает и почему там кончилось."""
        return self.deal_roll.window


def _as_payment(sp: ScheduledPayment) -> Payment:
    """Платёж проката в модели кассы: кошелёк, контрагент и признаки платежа."""
    return Payment(
        date=sp.date,
        amount=sp.amount,
        account=sp.wallet,
        counterparty=sp.counterparty,
        prepaid=sp.planned is None,
        debt=sp.debt,
    )


def _converge_month(deals: _DealsRoll, cash: _CashRoll,
                    index: int) -> tuple[_MonthLoad, _CashMonth]:
    """Сходимость одного месяца: бюджет кассы ↔ обязательные сделок.

    Порядок внутри месяца задан: обязательные вхождения считаются от остатков
    сделок, касса по ним считает свободные деньги, бюджет месяца берётся из
    кассы **того же** месяца. Бюджет входит в пул обязательных, поэтому связка
    замкнута: бюджет → обязательные → касса → бюджет, и круг повторяется, пока
    бюджет не перестанет меняться.

    Не сошлось за `_MONTH_PASSES` шагов — внутренний предел исчерпан, и
    `ConvergenceError` (предохранитель) ловит патологию внутри месяца. Шаг
    остаётся в состоянии сторон (`deals.load`, `cash.step`): драйвер читает
    оттуда последний проход и помечает месяц, а расчёт не роняет.
    """
    entry = deals.entry()
    cash_entry = cash.entry()
    budget = Decimal(0)
    seen: list[ScheduledPayment] | None = None
    for _ in range(_MONTH_PASSES):
        deals.restore(entry)
        load = deals.begin(index, extra=budget)
        if load.payments != seen:
            # Платежи месяца изменились — касса считает месяц заново с
            # состояния на входе месяца; не изменились — результат прошлого
            # прохода верен и кассу трогать не нужно.
            cash.restore(cash_entry)
            step = cash.month(index, [_as_payment(p) for p in load.payments])
            step.regular()
            seen = load.payments
        new_budget = step.prepay_budget
        if new_budget == budget:
            return load, step
        budget = new_budget
    raise ConvergenceError(
        f"месяц {index}: расчёт не сошёлся за {_MONTH_PASSES} итераций; "
        f"последний бюджет: {budget}")


def roll_months(book: Settlements, start: date,
                wallets: list[Wallet],
                incomes: list[Income],
                one_offs: list[Payment],
                living_floor: Decimal | None,
                obligation_reserve: Decimal | None = None,
                transfers: list[Transfer] = (),
                income_horizon: date | None = None,
                strategy: str = AVALANCHE,
                max_months: int = 600,
                consent_to_second: bool = False,
                main: str | None = None,
                window: Window | None = None) -> MonthsRoll:
    """Прокатить месяцы: драйвер идёт месяц за месяцем, обе стороны — шагами.

    Порядок внутри месяца строг: обязательные вхождения сделок → касса месяца
    (сколько денег свободно) → досрочки из этих денег → пересчёт кассы (деньги
    ушли). Бюджет месяца приходит из кассы **того же** месяца, а не из
    прошлого: правка бюджета не раскатывается вперёд на десятки итераций, и
    связка стоит одного прохода по графику. Публичного предела итераций нет —
    у зоны один предел, `max_months`.

    Внутри месяца сходимость остаётся: бюджет, непрошедшее и свободные деньги
    связаны через обещанное (непрошедший платёж не отменён, деньги на него уже
    обещаны). Круг считает `_converge_month`; за внутренний предел не сошлось —
    срабатывает `ConvergenceError`, драйвер ловит его и помечает месяц:
    признак несходимости живёт в результате (`MonthsRoll.unconverged`) и
    делает прогноз неполным, а расчёт не роняется исключением.

    В бюджет досрочек идёт `CashMonth.prepay_budget`, а не `free`: отрицательные
    свободные деньги — нехватка, а не бюджет, и пул месяца они не уменьшают.
    Поэтому связка сходится и когда прожиточный минимум неизвестен, а денег
    не хватает.

    Кроме расписания касса принимает приходы, переводы и разовые платежи
    (`one_offs` — то, что не из сделок); `income_horizon` — конец видимых
    доходов: до него измеряется прожиточный минимум после последнего прихода в
    окне, и им же кончается окно проката (`roll_window`); `obligation_reserve` —
    чтобы не раздать досрочками деньги, оставленные под начало следующего месяца.
    `window` — готовое окно: цена варианта передаёт окно базы, и оно обязано быть
    посчитано по той же книге и с тем же согласием.

    Месяцы после дыры помечаются как посчитанные на допущении: прокат не
    останавливается и не уходит в минус молча.
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"неизвестная стратегия: {strategy!r}")

    accounts = [
        Account(
            name=w.uid,
            balance=w.balance,
            is_credit=w.is_credit,
            available=w.available,
            limit=w.limit,
        )
        for w in wallets
    ]
    if main is None and wallets:
        main = wallets[0].uid
    scenario = Scenario(
        accounts=accounts,
        income=list(incomes),
        payments=list(one_offs),
        transfers=list(transfers),
        living_floor_monthly=living_floor,
        obligation_reserve=obligation_reserve,
        income_horizon=income_horizon,
    )

    # Срок по графику — отдельный проход без бюджета досрочек и без кассы; он
    # считается один раз на весь прокат, а не повторяется на каждой
    # сходимости, как повторяла его прежняя глобальная петля.
    graph = _graph_pass(book, start, strategy, max_months, consent_to_second)
    payoff, reason = _payoff_pair(graph, start)

    deals = _DealsRoll(book, start, strategy=strategy,
                       consent_to_second=consent_to_second,
                       max_months=max_months, income_horizon=income_horizon,
                       window=window)
    # Касса идёт по тому же окну, что и долги: за окном вопрос кончился, и
    # месяц за ним был бы выдумкой (600 месяцев кассы на 24-месячный долг) —
    # окно держит драйвер ниже.
    cash = _CashRoll(scenario, start, main)

    cash_months: list["CashMonth"] = []
    unconverged: list[int] = []
    deals_done = False
    for index in range(1, deals.window.months + 1):
        if deals_done:
            # Долги закрыты: событий от сделок больше нет — касса идёт одна.
            step = cash.month(index)
            step.regular()
            cash_months.append(step.finish())
            continue

        try:
            load, step = _converge_month(deals, cash, index)
        except ConvergenceError:
            # Признак несходимости живёт в результате: прогноз становится
            # неполным, а расчёт не роняется исключением. Незаконченный шаг
            # стороны читают из своего состояния — это последний проход месяца.
            unconverged.append(index)
            load, step = deals.load, cash.step

        # Досрочки уходят из остатка пула — обязательные уже заплачены, — а
        # касса проводит их после обязательных: деньги месяца уже посчитаны.
        # Свободные деньги месяца для предложения второму приоритету — те же
        # свободные деньги кассы (`step.free`), срез, а не пересчёт.
        prepayments = deals.finish(load, step.free)
        deals_done = deals.done
        cash_months.append(step.finish([_as_payment(p) for p in prepayments]))

    deal_roll = deals.result(payoff, reason)

    # Месяцы после дыры — на допущении
    assumed: list[int] = []
    hole_seen = False
    for cm in cash_months:
        if cm.hole > 0:
            hole_seen = True
        if hole_seen:
            assumed.append(cm.index)

    return MonthsRoll(deal_roll, cash_months, assumed, unconverged)


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
