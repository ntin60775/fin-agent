"""Детерминированный решатель кассового каскада.

События (доходы, платежи, переводы) сортируются по дате; для каждого
счёта ведётся бегущий остаток. В один день сначала приходит доход, потом
уходит перевод, потом платёж: переводят до того, как тратят.

Считаются три разные вещи:

- **дыра** — нехватка денег суммарно по доступным кошелькам, «денег нет нигде»;
- **необеспеченность** — платёж, которому не хватило ёмкости своего кошелька:
  деньги есть, но не на нём, и лечится это переводом;
- **нехватка до прожиточного минимума** — насколько остатка не хватает,
  чтобы прожить до следующего прихода. Дыры может не быть, а нехватка быть:
  остаток +2 000 при нужных 15 000 — это дефицит 13 000.

Кошелёк платит тем, что у него есть (`docs/decisions/wallet-pays-what-it-has.md`):
платёж, которому не хватило ёмкости своего кошелька, не проходит целиком, а
досрочка — исключение по сумме: её сумму назначает движок, поэтому он назначает
столько, сколько кошелёк может отдать. Свободный лимит кредитного кошелька идёт
только под жизненный расход: долговой платёж (`Payment.debt`) лимитом не
финансируется, а перевод — не платёж по долгу, и лимит к нему применим. Дыру
движок не создаёт сам — она приходит снаружи, остатками, которые уже в минусе.

Неизвестное — `None`, не ноль. Не задан прожиточный минимум (`?`) —
`floor_gap` равен None: честный ответ «не оценено», а не ноль.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_CEILING, Decimal

from .model import Account, Scenario, wallet_capacity, wallet_money

KOPEK = Decimal("0.01")
DAYS_IN_MONTH = Decimal(30)

#: Что именно не прошло из-за ёмкости кошелька.
KIND_PAYMENT = "payment"
KIND_PREPAID = "prepaid"
KIND_TRANSFER = "transfer"
UNSECURED_KINDS = (KIND_PAYMENT, KIND_PREPAID, KIND_TRANSFER)

#: Вид события в кассе: доход приходит, перевод и платёж уходят.
KIND_INCOME = "income"


@dataclass(frozen=True)
class TransferHint:
    """Подсказка перевода: откуда и сколько, чтобы платёж прошёл.

    Движок переводов не создаёт: перевод стоит денег и банк может его не
    пропустить. Он говорит, что перевести, — перекладывает деньги зона.
    """
    source: str
    amount: Decimal


@dataclass(frozen=True)
class Unsecured:
    """Платёж или перевод, которому не хватило ёмкости своего кошелька.

    `amount` — сколько назначено, `short` — сколько до него не дотянулось: столько
    и надо перевести. Обязательство платится целиком или не платится вовсе, и
    тогда `paid` равен нулю, хотя кошелёк отдал бы и то, что на нём лежало.
    Досрочка — исключение по сумме: она проходит настолько, насколько кошелёк
    может отдать. `hint` — что перевести и откуда; None, когда переводить
    неоткуда: денег нет нигде (это дыра) или они уже на этом кошельке, но их мало
    (тогда лечится не перевод, а дата).
    """
    date: date
    account: str
    amount: Decimal
    short: Decimal
    kind: str
    hint: TransferHint | None = None

    @property
    def paid(self) -> Decimal:
        """Сколько дошло до получателя.

        Обязательство платится целиком или не платится вовсе — значит ноль;
        досрочка проходит настолько, насколько кошелёк может отдать.
        """
        if self.kind == KIND_PREPAID:
            return self.amount - self.short
        return Decimal(0)


def _unpaid(rows: list[Unsecured]) -> Decimal:
    """Сколько из назначенного не прошло: эти деньги уже обещаны.

    Это не сумма нехваток: платёж, которому не хватило двухсот, не проходит
    целиком, и не прошло здесь всё назначенное. Сколько перевести до конкретного
    платежа — в его `short`.
    """
    return sum((u.amount - u.paid for u in rows), Decimal(0))


@dataclass
class CashMonth:
    """Касса за месяц: остатки, дыра, необеспеченность, нехватка до прожиточного минимума, свободные деньги."""
    index: int
    month: date
    balances: dict[str, Decimal]
    #: Дыра месяца: нехватка суммарно по доступным кошелькам; 0 — нехватки нет.
    hole: Decimal
    hole_date: date | None
    floor_gap: Decimal | None
    floor_gap_date: date | None
    #: Свободные деньги месяца — состояние месяца: доступные остатки минус
    #: прожиточный минимум, резерв обязательств и обещанное. Может быть
    #: отрицательным: нагрузка больше денег — это нехватка, а не бюджет. Где
    #: величина становится бюджетом досрочек, берётся `prepay_budget`.
    free: Decimal
    #: Платежи и переводы месяца, которым не хватило ёмкости своего кошелька.
    unsecured: list[Unsecured] = field(default_factory=list)

    @property
    def unsecured_total(self) -> Decimal:
        """Сколько за месяц не прошло из-за ёмкости своего кошелька."""
        return _unpaid(self.unsecured)

    @property
    def prepay_budget(self) -> Decimal:
        """Бюджет досрочек: свободные деньги, но не меньше нуля.

        Отрицательные свободные деньги — нехватка, а не бюджет: досрочке нечего
        отдать. Бюджет досрочек не бывает отрицательным.
        """
        return max(self.free, Decimal(0))


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
    #: Дыра: нехватка денег суммарно по доступным кошелькам, «денег нет нигде».
    #: 0 — нехватки нет. Это не необеспеченный платёж: там деньги есть, но не на
    #: том кошельке, и это видно в `unsecured`.
    hole: Decimal = Decimal(0)
    #: Дата худшей нехватки. None — нехватки нет или она была уже на старте:
    #: события её не создавали, движок дыру не создаёт.
    hole_date: date | None = None
    #: Платежи и переводы, которым не хватило ёмкости своего кошелька.
    unsecured: list[Unsecured] = field(default_factory=list)

    @property
    def unsecured_total(self) -> Decimal:
        """Сколько не прошло из-за ёмкости своего кошелька.

        Деньги на непрошедшее уже обещаны, поэтому и потолок частного транша, и
        свободные деньги месяца считаются без них.
        """
        return _unpaid(self.unsecured)

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


def _money_total(scenario: Scenario, bal: dict[str, Decimal]) -> Decimal:
    """Деньги всех кошельков: из них складывается ликвидность.

    Недоступный кошелёк не даёт ничего, минус на некредитном остаётся минусом,
    свободный лимит кредитного деньгами не считается — правило одно на оба
    носителя (`model.wallet_money`).
    """
    return sum((wallet_money(bal[a.name], is_credit=a.is_credit,
                             available=a.available)
                for a in scenario.accounts), Decimal(0))


def _shortage(scenario: Scenario, bal: dict[str, Decimal]) -> Decimal:
    """Нехватка суммарно по доступным кошелькам — «денег нет нигде».

    Необеспеченный платёж сюда не входит: там деньги есть, но не на том кошельке.
    """
    return max(-_money_total(scenario, bal), Decimal(0))


def _account(scenario: Scenario, name: str) -> Account:
    """Счёт по имени: что имя объявлено, проверил `_validate`."""
    return next(a for a in scenario.accounts if a.name == name)


def _room(scenario: Scenario, bal: dict[str, Decimal], account: str, *,
          debt: bool) -> Decimal | None:
    """Сколько кошелёк может отдать под платёж; None — ёмкость не оценена.

    Долговой платёж лимита не получает — правило кошелька (`wallet_capacity`).
    """
    a = _account(scenario, account)
    return wallet_capacity(bal[a.name], is_credit=a.is_credit,
                           available=a.available, limit=a.limit, debt=debt)


def _hint(scenario: Scenario, bal: dict[str, Decimal], account: str,
          short: Decimal) -> TransferHint | None:
    """Что перевести и откуда: кошелёк с наибольшим доступным остатком.

    Своего кошелька подсказка не предлагает: переводить с него на него нечего.
    Переводить неоткуда — подсказки нет: это уже дыра, а не перевод. Нехватка
    больше самого полного кошелька — подсказка говорит, сколько он может отдать:
    обещать больше нечего. Свободный лимит в подсказке не участвует: она
    предлагает свои деньги, а перевод с кредитного — заём, и решает о нём
    владелец, а не движок.
    """
    best: tuple[Decimal, str] | None = None
    for a in scenario.accounts:
        if a.name == account:
            continue
        money = wallet_money(bal[a.name], is_credit=a.is_credit,
                             available=a.available)
        if money > 0 and (best is None or money > best[0]):
            best = (money, a.name)
    if best is None:
        return None
    return TransferHint(source=best[1], amount=min(short, best[0]))


def _day_order(kind: str) -> int:
    """Порядок событий внутри дня: доход → перевод → платёж."""
    if kind == KIND_INCOME:
        return 0
    return 1 if kind == KIND_TRANSFER else 2


@dataclass
class _Event:
    """Событие кассы: что и с какого счёта уходит (или приходит)."""
    date: date
    kind: str
    account: str
    amount: Decimal
    to_account: str | None = None    # у перевода — куда
    debt: bool = True                # долговой: кредитным лимитом не финансируется


def _apply_event(scenario: Scenario, bal: dict[str, Decimal], event: _Event,
                 unsecured: list[Unsecured]) -> list[Step]:
    """Провести событие, не давая кошельку уйти в минус.

    Доход приходит всегда: деньги не берутся с кошелька, а приходят на него.
    Перевод и платёж проверяются ёмкостью своего кошелька: обязательство платится
    целиком или не платится вовсе, досрочка — настолько, насколько кошелёк может
    отдать. Ёмкость — `wallet_capacity`: у долгового платежа это деньги кошелька,
    у жизненного — ещё и свободный лимит. Не хватило — событие видно в
    `unsecured` вместе с подсказкой, что перевести; деньги при этом остаются там,
    где лежали.
    """
    if event.kind == KIND_INCOME:
        bal[event.account] += event.amount
        return [Step(event.date, event.account, event.amount, bal[event.account])]

    room = _room(scenario, bal, event.account, debt=event.debt)
    amount, short = event.amount, Decimal(0)
    if room is not None and amount > room:
        # Обязательство — целиком или никак; досрочку движок назначает сам,
        # поэтому назначает столько, сколько кошелёк может отдать.
        short = event.amount - room
        amount = room if event.kind == KIND_PREPAID else Decimal(0)
    if short > 0:
        unsecured.append(Unsecured(
            date=event.date, account=event.account, amount=event.amount,
            short=short, kind=event.kind,
            hint=_hint(scenario, bal, event.account, short)))
    if amount == 0:
        return []      # досрочка, которой кошелёк не может дать ничего
    bal[event.account] -= amount
    steps = [Step(event.date, event.account, -amount, bal[event.account])]
    if event.to_account is not None:
        bal[event.to_account] += amount
        steps.append(Step(event.date, event.to_account, amount,
                          bal[event.to_account]))
    return steps


def _validate(scenario: Scenario, main: str | None = None) -> None:
    """Явная ошибка вместо KeyError при опечатке в имени счёта.

    Правило одно на оба пути — кассу и прокат. Платёж обязан ссылаться на
    контрагента: уидом (новая форма) или строкой имени (старая, живёт до переезда
    зоны). Платить и переводить можно только с доступного кошелька: с
    арестованной карты не заплатить, её можно только гасить.

    Сумма события кассы положительная — направление несёт само событие, а не
    знак. Правило одно на оба носителя: во взаиморасчётах его держит `Movement`,
    в кассе — `Payment`, `Transfer` и `Income`; у правила один носитель, иначе
    два места разойдутся. Досрочка всегда долговая: её движок направляет на
    долг, а долг кредитным лимитом не платится — `debt=False` у неё
    противоречие, а не выбор.
    """
    if not scenario.accounts:
        raise ValueError("счета: не объявлено ни одного — считать нечего")
    for i in scenario.income:
        if i.amount <= 0:
            raise ValueError(
                f"приход на {i.account!r}: сумма должна быть положительной, "
                f"а направление несут сами данные")
    for p in scenario.payments:
        if p.amount <= 0:
            raise ValueError(
                f"платёж {p.counterparty or p.creditor!r}: сумма должна быть "
                f"положительной, а направление несут сами данные")
    for t in scenario.transfers:
        if t.amount <= 0:
            raise ValueError(
                f"перевод {t.from_account!r} → {t.to_account!r}: сумма должна "
                f"быть положительной, а направление несут сами данные")
    known = {a.name for a in scenario.accounts}
    by_name = {a.name: a for a in scenario.accounts}
    refs = []
    if main is not None:
        refs.append((main, "основной счёт"))
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
        if p.prepaid and not p.debt:
            raise ValueError(
                f"досрочка {p.counterparty or p.creditor!r}: досрочка всегда "
                f"долговая — её движок направляет на долг, а долг лимитом "
                f"не платится")
        if p.account is not None and not by_name[p.account].available:
            raise ValueError(
                f"платёж {p.counterparty or p.creditor!r}: счёт {p.account!r} недоступен")
    for t in scenario.transfers:
        if not by_name[t.from_account].available:
            raise ValueError(
                f"перевод: счёт {t.from_account!r} недоступен — с него взять нельзя")


def run(scenario: Scenario, main: str) -> Result:
    """Прогнать каскад: дыра, необеспеченность, нехватка до прожиточного минимума.

    В один день сначала приходит доход, потом уходит перевод, потом платёж:
    переводят до того, как тратят. Платёж, которому не хватило ёмкости своего
    кошелька, не проходит — деньги остаются там, где лежали, а необеспеченность
    видна в `Result.unsecured`. Долговой платёж лимитом не платится: свободный
    лимит идёт только под жизненный расход (`Payment.debt`). Дыра при этом не
    создаётся: она приходит снаружи, остатками, которые уже в минусе.
    """
    _validate(scenario, main)
    bal: dict[str, Decimal] = {a.name: a.balance for a in scenario.accounts}

    events: list[_Event] = []
    for i in scenario.income:
        events.append(_Event(i.date, KIND_INCOME, i.account, i.amount))
    for t in scenario.transfers:
        events.append(_Event(t.date, KIND_TRANSFER, t.from_account, t.amount,
                             t.to_account, debt=False))   # перевод — не долг
    for p in scenario.payments:
        kind = KIND_PREPAID if p.prepaid else KIND_PAYMENT
        events.append(_Event(p.date, kind, p.account, p.amount, debt=p.debt))

    # Стабильная сортировка: внутри дня события идут по виду, а внутри вида —
    # в порядке добавления. Дата не сдвигается, касса только считает.
    events.sort(key=lambda e: (e.date, _day_order(e.kind)))

    timeline: list[Step] = []
    unsecured: list[Unsecured] = []
    # Дыра на старте события не создавали: даты у неё нет.
    hole, hole_date = _shortage(scenario, bal), None
    for event in events:
        timeline += _apply_event(scenario, bal, event, unsecured)
        gap = _shortage(scenario, bal)
        if gap > hole:
            hole, hole_date = gap, event.date

    main_steps = [s for s in timeline if s.account == main]
    if main_steps:
        lowest = min(main_steps, key=lambda s: s.balance_after)
        min_balance, min_date = lowest.balance_after, lowest.date
    else:
        min_balance, min_date = bal[main], None

    floor_gap, floor_gap_date = _assess_floor(scenario, main_steps)
    return Result(min_balance, min_date, bal[main], dict(bal), timeline,
                  floor_gap, floor_gap_date, hole, hole_date, unsecured)


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

    = остаток основного счёта после всех обязательств минус резерв на
    обязательства начала следующего месяца и минус то, что не прошло: непрошедший
    платёж не отменён, деньги на него уже обещаны.
    """
    return result.end_balance - reserve - result.unsecured_total


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
    #: Дыра: нехватка суммарно по доступным кошелькам; 0 — нехватки нет.
    hole: Decimal = Decimal(0)
    #: Необеспеченность: сколько не прошло из-за ёмкости своего кошелька.
    unsecured_total: Decimal = Decimal(0)


def outcome(label: str, scenario: Scenario, main: str, reserve: Decimal) -> Outcome:
    r = run(scenario, main)
    return Outcome(label, r.min_balance, r.min_date, r.end_balance,
                   optional_cap(r, reserve), r.floor_gap, r.hole,
                   r.unsecured_total)


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



def roll_cash(scenario: Scenario, start: date, max_months: int = 600,
              main: str | None = None) -> list[CashMonth]:
    """Прокатить кассу по месяцам от `start`.

    Месяцы календарные; первый — с `start` до конца месяца, неполный.
    Остатки предыдущего месяца — вход следующего. В один день сначала приходит
    доход, потом уходит перевод, потом платёж. Даты доходов и платежей вычислены
    вызывающим по тем же правилам: кламп на конец месяца и сдвиг с выходного.

    Платёж с `prepaid=True` — досрочка: она уходит после обязательных платежей
    месяца, поэтому свободные деньги считаются до неё. Из свободных денег
    вычитается и резерв обязательств (`Scenario.obligation_reserve`) — деньги,
    оставленные под платежи начала следующего месяца. Свободные деньги — состояние
    месяца и могут быть отрицательными, когда нагрузка больше денег; бюджет
    досрочек из них делает `CashMonth.prepay_budget` — не меньше нуля.
    Неизвестный прожиточный минимум даёт `floor_gap = None` («не оценено»),
    а не ноль. Платёж и перевод,
    которым не хватило ёмкости своего кошелька, не проходят: они видны в
    `CashMonth.unsecured` с подсказкой, что перевести, а деньги остаются на месте.
    Свободный лимит кредитного кошелька идёт только под жизненный расход:
    долговой платёж (`Payment.debt`) лимитом не финансируется.
    """
    _validate(scenario)
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

        # События месяца: доход, перевод и платёж идут по порядку дня; досрочки —
        # после обязательных: свободные деньги становятся известны, когда месяц
        # прожит.
        regular: list[_Event] = []
        prepaid: list[_Event] = []
        for i in incomes:
            if window_start <= i.date <= end:
                regular.append(_Event(i.date, KIND_INCOME, i.account, i.amount))
        for t in transfers:
            if window_start <= t.date <= end:
                regular.append(_Event(t.date, KIND_TRANSFER, t.from_account,
                                      t.amount, t.to_account,
                                      debt=False))       # перевод — не долг
        for p in payments:
            if not window_start <= p.date <= end:
                continue
            kind = KIND_PREPAID if p.prepaid else KIND_PAYMENT
            (prepaid if p.prepaid else regular).append(
                _Event(p.date, kind, p.account, p.amount, debt=p.debt))

        regular.sort(key=lambda e: (e.date, _day_order(e.kind)))
        prepaid.sort(key=lambda e: e.date)

        unsecured: list[Unsecured] = []
        timeline: list[Step] = []
        # Дыра месяца: нехватка приходит снаружи — остатками на начало месяца.
        hole = _shortage(scenario, bal)
        hole_date = window_start if hole > 0 else None
        for event in regular:
            timeline += _apply_event(scenario, bal, event, unsecured)
            gap = _shortage(scenario, bal)
            if gap > hole:
                hole, hole_date = gap, event.date

        # Нехватка до прожиточного минимума: по обязательным событиям месяца —
        # досрочка тратится уже после того, как на жизнь отложено.
        main_steps = [s for s in timeline if s.account == main]
        floor_gap, floor_gap_date = _assess_floor(scenario, main_steps)

        # Свободные деньги: доступные остатки минус прожиточный минимум месяца,
        # минус резерв обязательств и минус то, что не прошло: непрошедшее
        # обязательство не отменено — деньги на него уже обещаны. Считаются до
        # досрочек. Это состояние месяца: может быть отрицательным — нагрузка
        # больше денег. Бюджетом досрочек величина становится не здесь, а через
        # `CashMonth.prepay_budget`: он не бывает отрицательным.
        # По всем кошелькам: владелец видит свои деньги целиком, а недосягаемое
        # для кошелька сделки показывается необеспеченностью с подсказкой перевода.
        total_money = _money_total(scenario, bal) - _unpaid(unsecured)
        reserved = scenario.obligation_reserve or Decimal(0)
        if scenario.living_floor_monthly is not None:
            days = (end - window_start).days + 1
            monthly_living = (scenario.living_floor_monthly * Decimal(days)
                              / DAYS_IN_MONTH).quantize(KOPEK, rounding=ROUND_CEILING)
            free = total_money - monthly_living - reserved
        else:
            free = total_money - reserved

        # Досрочки — после обязательных: свободные деньги уже посчитаны, второй
        # раз в бюджет они не попадут, а касса увидит, что деньги ушли.
        for event in prepaid:
            timeline += _apply_event(scenario, bal, event, unsecured)
            gap = _shortage(scenario, bal)
            if gap > hole:
                hole, hole_date = gap, event.date

        months.append(CashMonth(
            index=index, month=month, balances=dict(bal),
            hole=hole, hole_date=hole_date,
            floor_gap=floor_gap, floor_gap_date=floor_gap_date,
            free=free, unsecured=unsecured,
        ))

    return months
