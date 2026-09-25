"""Детерминированный решатель кассового каскада.

События (доходы, платежи, переводы) сортируются по дате; для каждого
кошелька ведётся бегущий остаток. В один день сначала приходит доход, потом
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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_CEILING, Decimal

from .model import Account, KOPEK, Scenario, wallet_capacity, wallet_money

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
    #: Дата худшей нехватки: созданная событием — дата события; стартовая
    #: (предшествующая первым событиям) — `window_start` месяца: для первого
    #: `start`, для остальных 1-е число — окно известно, движок знает, когда
    #: месяц начался. Нехватки нет → None.
    hole_date: date | None
    floor_gap: Decimal | None
    floor_gap_date: date | None
    #: Свободные деньги месяца — состояние месяца: доступные остатки минус
    #: накопленный по окну прожиточный минимум (все месяцы окна до текущего
    #: включительно), резерв обязательств и обещанное. Может быть
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
    min_balance: Decimal          # минимум остатка основного кошелька за линию
    min_date: date | None
    end_balance: Decimal          # остаток основного кошелька в конце линии
    balances: dict[str, Decimal]  # финальный остаток каждого кошелька
    #: Свободные деньги в конце линии — «сколько свободно» и «сколько можно
    #: отдать» в одном числе: деньги всех доступных кошельков (`_money_total`)
    #: минус обещанное (`_unpaid`), минус накопленный прожиточный минимум и
    #: минус резерв обязательств (`Scenario.obligation_reserve`). Прожитое —
    #: отрезок от первого события линии до последнего с включительными
    #: границами: `F × дней / 30` вверх до копейки — та же пропорция дня,
    #: что и в свободных деньгах кассы. Неизвестный минимум не вычитается,
    #: как и в `CashMonth.free`: неизвестное — `None`, а не ноль.
    free: Decimal
    timeline: list[Step]
    #: Худшая нехватка до прожиточного минимума (включает саму дыру).
    #: None = прожиточный минимум неизвестен → реальный дефицит НЕ оценён.
    floor_gap: Decimal | None = None
    floor_gap_date: date | None = None
    #: Дыра: нехватка денег суммарно по доступным кошелькам, «денег нет нигде».
    #: 0 — нехватки нет. Это не необеспеченный платёж: там деньги есть, но не на
    #: том кошельке, и это видно в `unsecured`.
    hole: Decimal = Decimal(0)
    #: Дата худшей нехватки: созданная событием — дата события. Стартовая
    #: дыра (предшествовала первым событиям) даты не имеет: `run()` окна не
    #: имеет, а правило даты стартовой дыры общее на оба пути — окно есть →
    #: `window_start` месяца, окна нет → None: движок не спрашивает «сегодня»
    #: и не выдумает, когда дыра началась. Нехватки нет → None.
    hole_date: date | None = None
    #: Платежи и переводы, которым не хватило ёмкости своего кошелька.
    unsecured: list[Unsecured] = field(default_factory=list)

    @property
    def unsecured_total(self) -> Decimal:
        """Сколько не прошло из-за ёмкости своего кошелька.

        Деньги на непрошедшее уже обещаны, поэтому и свободные деньги линии
        (`Result.free`), и свободные деньги месяца считаются без них.
        """
        return _unpaid(self.unsecured)

    @property
    def total(self) -> Decimal:
        """Финальная суммарная ликвидность по всем кошелькам сценария."""
        return sum(self.balances.values(), Decimal(0))


def _days_to_next_income(scenario: Scenario, day: date, *,
                         today: bool = False) -> int | None:
    """Сколько дней от `day` до ближайшего прихода (в окне или на горизонте).

    `today=True` — приход в самый `day` считается уже впереди (0 дней): так
    оценивается точка до событий дня, приход этого дня в остаток ещё не лёг.
    """
    for inc in sorted(i.date for i in scenario.income):
        if inc > day or (today and inc == day):
            return (inc - day).days
    horizon = scenario.income_horizon
    if horizon is not None and (horizon > day or (today and horizon == day)):
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
    """Кошелёк по имени: что имя объявлено, проверил `_validate`."""
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
    """Событие кассы: что и с какого кошелька уходит (или приходит)."""
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
    """Явная ошибка вместо KeyError при опечатке в имени кошелька.

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
        raise ValueError("кошельки: не объявлено ни одного — считать нечего")
    for i in scenario.income:
        if i.amount <= 0:
            raise ValueError(
                f"приход на {i.account!r}: сумма должна быть положительной, "
                f"а направление несут сами данные")
    for p in scenario.payments:
        if p.amount <= 0:
            raise ValueError(
                f"платёж {p.counterparty!r}: сумма должна быть "
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
        refs.append((main, "основной кошелёк"))
    refs += [(i.account, "приход") for i in scenario.income]
    refs += [(p.account, f"платёж {p.counterparty!r}")
             for p in scenario.payments]
    refs += [(t.from_account, "перевод (откуда)") for t in scenario.transfers]
    refs += [(t.to_account, "перевод (куда)") for t in scenario.transfers]
    for name, what in refs:
        if name not in known:
            raise ValueError(
                f"{what}: неизвестный кошелёк {name!r}; объявлены: {sorted(known)}")
    for p in scenario.payments:
        if p.counterparty is None:
            raise ValueError("платёж без контрагента: нужен уид контрагента")
        if p.prepaid and not p.debt:
            raise ValueError(
                f"досрочка {p.counterparty!r}: досрочка всегда "
                f"долговая — её движок направляет на долг, а долг лимитом "
                f"не платится")
        if p.account is not None and not by_name[p.account].available:
            raise ValueError(
                f"платёж {p.counterparty!r}: кошелёк {p.account!r} недоступен")
    for t in scenario.transfers:
        if not by_name[t.from_account].available:
            raise ValueError(
                f"перевод: кошелёк {t.from_account!r} недоступен — с него взять нельзя")


def run(scenario: Scenario, main: str) -> Result:
    """Прогнать каскад: дыра, необеспеченность, нехватка до прожиточного минимума.

    В один день сначала приходит доход, потом уходит перевод, потом платёж:
    переводят до того, как тратят. Платёж, которому не хватило ёмкости своего
    кошелька, не проходит — деньги остаются там, где лежали, а необеспеченность
    видна в `Result.unsecured`. Долговой платёж лимитом не платится: свободный
    лимит идёт только под жизненный расход (`Payment.debt`). Дыра при этом не
    создаётся: она приходит снаружи, остатками, которые уже в минусе.
    Нехватка до прожиточного минимума считается от начала линии: прожитое с
    первого события до даты шага уже израсходовано, но ликвидность его не
    теряла — требование сравнивается с совокупной доступной ликвидностью всех
    кошельков за вычетом прожитого. Оценивается каждое событие линии. Раньше
    первого события позиция неизвестна: движок не спрашивает «сегодня».
    Свободные деньги (`Result.free`) считаются здесь же той же формулой, что
    у кассы: все доступные кошельки минус обещанное, минус накопленный
    прожиточный минимум (с первого события по последнее) и минус резерв
    обязательств из сценария — «сколько свободно» и «сколько можно отдать»
    в конце линии отвечает одно число.
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
    # Точки оценки floor — зеркало дыры: каждое событие линии меряется по
    # совокупной доступной ликвидности всех кошельков после него. Одна
    # агрегация на событие: и дыра, и точка оценки floor.
    floor_points: list[tuple[date, Decimal]] = []
    for event in events:
        timeline += _apply_event(scenario, bal, event, unsecured)
        total = _money_total(scenario, bal)
        floor_points.append((event.date, total))
        gap = max(-total, Decimal(0))
        if gap > hole:
            hole, hole_date = gap, event.date

    main_steps = [s for s in timeline if s.account == main]
    if main_steps:
        lowest = min(main_steps, key=lambda s: s.balance_after)
        min_balance, min_date = lowest.balance_after, lowest.date
    else:
        min_balance, min_date = bal[main], None

    # Прожитое: в линии окна нет — точка отсчёта одна на всю линию, её начало.
    # Раньше первого события позиция неизвестна, и движок её не выдумывает.
    floor_gap, floor_gap_date = _assess_floor(
        scenario, floor_points, since=events[0].date if events else None)

    # Свободные деньги в конце линии: та же формула, что у свободных денег
    # кассы, — деньги всех доступных кошельков минус обещанное, минус
    # накопленный прожиточный минимум и минус резерв обязательств. Прожитое —
    # отрезок с первого события линии по последнее, включительно: то же
    # правило отрезка, что в кассе, чтобы у построчного и месячного путей не
    # было двух определений одной величины. Неизвестный минимум не
    # вычитается — как и в `CashMonth.free`.
    total_money = _money_total(scenario, bal) - _unpaid(unsecured)
    reserved = scenario.obligation_reserve or Decimal(0)
    if scenario.living_floor_monthly is not None and events:
        days = (events[-1].date - events[0].date).days + 1
        accrued = (scenario.living_floor_monthly * Decimal(days)
                   / DAYS_IN_MONTH).quantize(KOPEK, rounding=ROUND_CEILING)
    else:
        accrued = Decimal(0)
    free = total_money - accrued - reserved

    return Result(min_balance, min_date, bal[main], dict(bal), free, timeline,
                  floor_gap, floor_gap_date, hole, hole_date, unsecured)


def _consumed(monthly: Decimal, since: date | None, day: date) -> Decimal:
    """Прожитое от точки отсчёта до дня шага: `F × дней / 30` вверх до копейки.

    Дни — разность дат: день отсчёта не прожит целиком, поэтому в самой точке
    отсчёта прожитого ещё нет. Точки отсчёта нет — нет и прожитого: без линии
    (`run()`) шагов с оценкой не бывает вовсе.
    """
    if since is None or day <= since:
        return Decimal(0)
    days = (day - since).days
    return (monthly * Decimal(days) / DAYS_IN_MONTH).quantize(KOPEK,
                                                             rounding=ROUND_CEILING)


def _assess_floor(scenario: Scenario,
                  event_points: list[tuple[date, Decimal]],
                  *,
                  since: date | None = None,
                  accrued: Decimal = Decimal(0),
                  entry: Decimal | None = None,
                  ) -> tuple[Decimal | None, date | None]:
    """Худшая нехватка доступной ликвидности до прожиточного минимума за линию.

    Базис — совокупная доступная ликвидность всех кошельков (`_money_total`,
    тот же `wallet_money`, что у дыры и свободных денег): прожить на деньгах
    другого кошелька можно, а основной кошелёк — метрика, а не весь мир.
    Прожитое с точки отсчёта уже израсходовано, но ликвидность его не теряла:
    требование до прихода сравнивается с ликвидностью за вычетом прожитого,
    иначе нехватка занижена ровно на эту сумму. Точка отсчёта — `since`,
    прожитое до неё — `accrued`: в прокате это начало месяца окна (первого —
    сам `start`) и накопленный минимум прошлых месяцев, в `run()` — первое
    событие линии, без накопления. Прожитое — та же пропорция `F × дней / 30`
    с округлением до копейки вверх, что и накопленный минимум окна: два учёта
    не расходятся.

    Оцениваются два вида точек: события списком `event_points` (ликвидность
    после каждого обязательного события, зеркало дыры; в `run()` — после
    каждого события линии) и — когда заданы и `entry`, и `since` — вход месяца
    точкой `since` с входной ликвидностью: месяц без событий получает оценку
    по входу, а голод до первого прихода виден сразу, как дыра. Приход в сам
    день входа считается сегодняшним (0 дней впереди): голодать не перед чем,
    ликвидность дня оценивает событие после прихода.
    """
    monthly = scenario.living_floor_monthly
    if monthly is None:
        return None, None  # прожиточный минимум неизвестен — честное «не оценено»

    points: list[tuple[date, Decimal, bool]] = []
    if entry is not None and since is not None:
        points.append((since, entry, True))
    points.extend((day, money, False) for day, money in event_points)

    worst: tuple[Decimal, date] | None = None
    assessed = 0
    for day, liquidity, today in points:
        days = _days_to_next_income(scenario, day, today=today)
        if days is None:
            continue
        assessed += 1
        required = (monthly * days / DAYS_IN_MONTH).quantize(KOPEK,
                                                            rounding=ROUND_CEILING)
        consumed = accrued + _consumed(monthly, since, day)
        gap = required - (liquidity - consumed)
        if gap > 0 and (worst is None or gap > worst[0]):
            worst = (gap, day)
    if assessed == 0:
        # Ни одной точки с известным приходом впереди — ноль был бы выдумкой.
        return None, None
    return worst if worst is not None else (Decimal(0), None)


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
    #: Свободные деньги в конце линии — то же число, что `Result.free`:
    #: «сколько свободно» и «сколько можно отдать» — один вопрос, один ответ.
    free: Decimal
    floor_gap: Decimal | None     # None = прожиточный минимум неизвестен
    #: Дыра: нехватка суммарно по доступным кошелькам; 0 — нехватки нет.
    hole: Decimal = Decimal(0)
    #: Необеспеченность: сколько не прошло из-за ёмкости своего кошелька.
    unsecured_total: Decimal = Decimal(0)


def outcome(label: str, scenario: Scenario, main: str) -> Outcome:
    """Итог одного варианта: свободные деньги читаются в конце линии."""
    r = run(scenario, main)
    return Outcome(label, r.min_balance, r.min_date, r.end_balance,
                   r.free, r.floor_gap, r.hole, r.unsecured_total)


def compare(variants: Mapping[str, Scenario], main: str) -> list[Outcome]:
    """Прогнать несколько сценариев и вернуть итоги в порядке передачи.

    Каждый вариант читает свои свободные деньги в конце линии, а порог
    резерва берётся только из самого сценария (`Scenario.obligation_reserve`):
    двух источников одного порога не бывает.
    """
    return [outcome(label, s, main) for label, s in variants.items()]


def _month_start(day: date, index: int) -> date:
    """Первый день месяца через `index` месяцев от месяца даты."""
    year, month = day.year, day.month + index
    return date(year + (month - 1) // 12, (month - 1) % 12 + 1, 1)


def _month_end(day: date) -> date:
    """Последний день месяца даты."""
    import calendar
    return date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])


# --- прокат по месяцам: явное состояние -------------------------------------

@dataclass
class _CashState:
    """Явное состояние кассы между месяцами: остатки кошельков и накопленный минимум.

    Драйвер держит его между вызовами шага и показывает глазами: повтор месяца
    (внутренняя сходимость) восстанавливает снимок `snapshot()` и начинает шаг
    заново. Непрошедшее живёт в шаге месяца (`_CashMonth.unsecured`) — оно своё
    у месяца и в следующий не переносится: деньги остались на кошельке, а
    обещание кончилось вместе с месяцем.
    """
    balances: dict[str, Decimal]
    living_accrued: Decimal = Decimal(0)

    def snapshot(self) -> "_CashState":
        """Снимок состояния на входе месяца: повтор начинается с него."""
        return _CashState(dict(self.balances), self.living_accrued)

    def restore(self, snap: "_CashState") -> None:
        self.balances.clear()
        self.balances.update(snap.balances)
        self.living_accrued = snap.living_accrued


class _CashRoll:
    """Прокат кассы: месяц за месяцем, состояние переносится явно.

    `month()` собирает события месяца и отдаёт шаг (`_CashMonth`); шаг
    разбит на два вызова — обязательные события дают свободные деньги,
    досрочки уходят после них. Прокат (`roll_cash`) гоняет шаги подряд;
    связка с сделками (`roll_months`) вставляет между ними расчёт сделок.
    """

    def __init__(self, scenario: Scenario, start: date,
                 main: str | None = None) -> None:
        _validate(scenario, main)
        self.scenario = scenario
        self.start = start
        self.state = _CashState({a.name: a.balance for a in scenario.accounts})
        self.main = main if main is not None else next(iter(self.state.balances))
        # Шаг текущего месяца: его читает драйвер, если предохранитель
        # внутреннего круга сработал и шаг остался незаконченным.
        self.step: "_CashMonth" | None = None
        # Доходы: даты вычислены вызывающим по тем же правилам, что и платежи
        self.incomes = sorted(scenario.income, key=lambda i: i.date)
        self.payments = sorted(scenario.payments, key=lambda p: p.date)
        self.transfers = sorted(scenario.transfers, key=lambda t: t.date)

    def entry(self) -> _CashState:
        """Снимок состояния на входе месяца."""
        return self.state.snapshot()

    def restore(self, snap: _CashState) -> None:
        """Вернуть состояние к снимку: повтор месяца начинается с него."""
        self.state.restore(snap)

    def month(self, index: int,
              payments: Sequence[Payment] = ()) -> "_CashMonth":
        """Шаг месяца: события сценария в его окне плюс переданные платежи.

        Шаг кладёт и в состояние (`self.step`): его читает драйвер, если
        предохранитель внутреннего круга месяца сработал.
        """
        self.step = _CashMonth(self, index, payments)
        return self.step


class _CashMonth:
    """Шаг одного месяца кассы: обязательные события → свободные деньги → досрочки.

    Порядок задан и не меняется: `regular()` проводит обязательные события и
    считает свободные деньги **до** досрочек — это и есть бюджет месяца;
    `finish()` проводит досрочки (деньги уже уходят) и собирает итог месяца.
    Между вызовами шаг держит явное состояние месяца: непрошедшее
    (`unsecured`), дыру, оценку прожитого и свободные деньги — его видно
    глазами и можно проверить тестом.
    """

    def __init__(self, roll: _CashRoll, index: int,
                 payments: Sequence[Payment] = ()) -> None:
        self.roll = roll
        scenario = roll.scenario
        self.index = index
        self.month = _month_start(roll.start, index - 1)
        self.end = _month_end(self.month)
        self.window_start = roll.start if index == 1 else self.month
        self.unsecured: list[Unsecured] = []
        self.timeline: list[Step] = []
        self.hole = Decimal(0)
        self.hole_date: date | None = None
        self.floor_gap: Decimal | None = None
        self.floor_gap_date: date | None = None
        self.free = Decimal(0)
        # Переданные шагу платежи не входят в проверенный сценарий — они
        # приходят месяца от месяца, и правило к ним применяется здесь.
        if payments:
            _validate(Scenario(accounts=list(scenario.accounts),
                               payments=list(payments)))

        regular: list[_Event] = []
        prepaid: list[_Event] = []
        for i in roll.incomes:
            if self.window_start <= i.date <= self.end:
                regular.append(_Event(i.date, KIND_INCOME, i.account, i.amount))
        for t in roll.transfers:
            if self.window_start <= t.date <= self.end:
                regular.append(_Event(t.date, KIND_TRANSFER, t.from_account,
                                      t.amount, t.to_account,
                                      debt=False))       # перевод — не долг
        for source in (roll.payments, payments):
            for p in source:
                if not self.window_start <= p.date <= self.end:
                    continue
                kind = KIND_PREPAID if p.prepaid else KIND_PAYMENT
                (prepaid if p.prepaid else regular).append(
                    _Event(p.date, kind, p.account, p.amount, debt=p.debt))
        regular.sort(key=lambda e: (e.date, _day_order(e.kind)))
        prepaid.sort(key=lambda e: e.date)
        self.regular_events = regular
        self.prepaid_events = prepaid

    def regular(self) -> Decimal:
        """Обязательные события месяца: дыра, прожитое и свободные деньги до досрочек."""
        roll = self.roll
        scenario = roll.scenario
        bal = roll.state.balances
        # Дыра месяца: нехватка приходит снаружи — остатками на начало месяца.
        # Вход месяца — первая точка оценки floor: та же агрегатная ликвидность
        # до событий. Одна агрегация на момент: и дыра, и floor.
        start_total = _money_total(scenario, bal)
        self.hole = max(-start_total, Decimal(0))
        self.hole_date = self.window_start if self.hole > 0 else None
        # Точки оценки floor — зеркало дыры: каждое обязательное событие меряется
        # по совокупной ликвидности после него. Досрочка не входит: она тратит
        # уже отложенное на жизнь и оценивается после обязательных.
        floor_points: list[tuple[date, Decimal]] = []
        for event in self.regular_events:
            self.timeline += _apply_event(scenario, bal, event, self.unsecured)
            total = _money_total(scenario, bal)
            floor_points.append((event.date, total))
            gap = max(-total, Decimal(0))
            if gap > self.hole:
                self.hole, self.hole_date = gap, event.date

        # Нехватка до прожиточного минимума: точка отсчёта — `window_start`
        # (первого месяца — `start` окна) плюс накопленный минимум месяцев до
        # текущего (`living_accrued`), частичный месяц до даты шага считает
        # `_assess_floor`; вход месяца оценивается по `start_total`.
        self.floor_gap, self.floor_gap_date = _assess_floor(
            scenario, floor_points, since=self.window_start,
            accrued=roll.state.living_accrued, entry=start_total)

        # Свободные деньги: доступные остатки минус накопленный по окну
        # прожиточный минимум — все месяцы окна до текущего включительно: в
        # таком порядке и копится. Дальше минус резерв обязательств и минус то,
        # что не прошло: непрошедшее обязательство не отменено — деньги на него
        # уже обещаны. Считаются до досрочек. Это состояние месяца: может быть
        # отрицательным — нагрузка больше денег. Бюджетом досрочек величина
        # становится не здесь, а через `prepay_budget`: он не бывает
        # отрицательным. По всем кошелькам: владелец видит свои деньги целиком,
        # а недосягаемое для кошелька сделки показывается необеспеченностью с
        # подсказкой перевода.
        total_money = _money_total(scenario, bal) - _unpaid(self.unsecured)
        reserved = scenario.obligation_reserve or Decimal(0)
        if scenario.living_floor_monthly is not None:
            days = (self.end - self.window_start).days + 1
            roll.state.living_accrued += (
                scenario.living_floor_monthly * Decimal(days)
                / DAYS_IN_MONTH).quantize(KOPEK, rounding=ROUND_CEILING)
            self.free = total_money - roll.state.living_accrued - reserved
        else:
            self.free = total_money - reserved
        return self.free

    @property
    def prepay_budget(self) -> Decimal:
        """Бюджет досрочек месяца: свободные деньги, но не меньше нуля."""
        return max(self.free, Decimal(0))

    def finish(self, prepaid: Sequence[Payment] = ()) -> CashMonth:
        """Досрочки месяца: свободные деньги уже посчитаны, теперь они уходят."""
        roll = self.roll
        scenario = roll.scenario
        bal = roll.state.balances
        # Досрочки приходят месяца от месяца, а не из проверенного сценария —
        # правило к ним применяется здесь же, как и к платежам шага.
        if prepaid:
            _validate(Scenario(accounts=list(scenario.accounts),
                               payments=list(prepaid)))
        events = list(self.prepaid_events)
        for p in prepaid:
            if not self.window_start <= p.date <= self.end:
                continue
            events.append(_Event(p.date, KIND_PREPAID, p.account, p.amount,
                                 debt=p.debt))
        events.sort(key=lambda e: e.date)
        for event in events:
            self.timeline += _apply_event(scenario, bal, event, self.unsecured)
            gap = _shortage(scenario, bal)
            if gap > self.hole:
                self.hole, self.hole_date = gap, event.date
        return CashMonth(
            index=self.index, month=self.month, balances=dict(bal),
            hole=self.hole, hole_date=self.hole_date,
            floor_gap=self.floor_gap, floor_gap_date=self.floor_gap_date,
            free=self.free, unsecured=self.unsecured)


def roll_cash(scenario: Scenario, start: date, max_months: int = 600,
              main: str | None = None) -> list[CashMonth]:
    """Прокатить кассу по месяцам от `start`.

    Месяцы календарные; первый — с `start` до конца месяца, неполный.
    Остатки предыдущего месяца — вход следующего. В один день сначала приходит
    доход, потом уходит перевод, потом платёж. Даты доходов и платежей вычислены
    вызывающим по тем же правилам: кламп на конец месяца и сдвиг с выходного.

    Платёж с `prepaid=True` — досрочка: она уходит после обязательных платежей
    месяца, поэтому свободные деньги считаются до неё. Вычитается прожиточный
    минимум, накопленный по окну: все месяцы окна до текущего включительно,
    слагаемое каждого — `F × дней / 30` вверх до копейки по самому месяцу.
    Прожитое прошлых месяцев переносимые остатки не теряют — без накопления
    минимума свободные деньги месяцев ≥ 2 завышались ровно на его сумму.
    Нехватка до прожиточного минимума считает то же прожитое: точка отсчёта —
    начало месяца окна (первого — сам `start`), прожито = накопленный минимум
    месяцев окна до текущего плюс частичный месяц до даты шага, и требование
    до прихода сравнивается с совокупной доступной ликвидностью всех кошельков
    за вычетом прожитого — тот же базис, что у дыры и свободных денег.
    Оцениваются вход месяца (ликвидность до событий — так месяц без событий
    получает число, а не None) и каждое обязательное событие после него —
    зеркало дыры; досрочка после отложенного на жизнь точкой не становится.
    Приход в самый день входа считается сегодняшним. Событие в самой точке
    отсчёта прожитого ещё не имеет.
    Из свободных денег вычитается и резерв обязательств
    (`Scenario.obligation_reserve`) — деньги, оставленные под платежи начала
    следующего месяца. Свободные деньги — состояние месяца и могут быть
    отрицательными, когда нагрузка больше денег; бюджет досрочек из них делает
    `CashMonth.prepay_budget` — не меньше нуля.
    Неизвестный прожиточный минимум даёт `floor_gap = None` («не оценено»),
    а не ноль. Платёж и перевод,
    которым не хватило ёмкости своего кошелька, не проходят: они видны в
    `CashMonth.unsecured` с подсказкой, что перевести, а деньги остаются на месте.
    Свободный лимит кредитного кошелька идёт только под жизненный расход:
    долговой платёж (`Payment.debt`) лимитом не финансируется.
    """
    roll = _CashRoll(scenario, start, main)
    months: list[CashMonth] = []
    for index in range(1, max_months + 1):
        step = roll.month(index)
        step.regular()
        months.append(step.finish())
    return months
