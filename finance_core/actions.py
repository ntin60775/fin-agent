"""Действия и цена варианта: правка сценария как объект, вариант как список действий.

Действие — объект: перенести платёж на дату (или без даты — отложить), взять мост,
погасить досрочно из свободных денег месяца, направить свободный остаток. Вариант —
список действий, применённый к сценарию **целиком**: прокат идёт месяц за месяцем
через весь список разом, а не заново после каждого действия. Сценарий под вариант не
переписывается: `applied` собирает новый вход, база остаётся как была.

Цена варианта — набор измерений, а не одно число: рубли процентов, дни просрочки,
влияние на кассу, сдвиг дат выхода и ступеней, изменение свободных денег. Измерения
разные, потому что действия разные: у переноса на два дня цена — дни просрочки и
минус дыра, у моста — рубли процентов.

Движок отклоняет только **невозможное** (`impossible`): сдвиг за горизонт, мост без
дыры, мост больше доступного лимита кошелька, досрочка больше остатка сделки,
конфликт действий внутри варианта. Бюджет досрочки меряется не по базе, а по
**входу варианта** — база плюс все действия, кроме всех досрочек: перенос,
освободивший деньги месяца, идёт в зачёт, и порядок действий не влияет.
Невыгодное не отклоняется — оно показывается ценой, а решение принимает человек.

Сам движок перебирает узкий набор (`variants`): сдвиги платежей в окне и мост под
дыру. Широкое планирование — по запросу: список действий собирается снаружи, числа
(ставка моста, окно сдвигов) в движок не вшиты.

Принятое действие становится фактом (`facts`): правкой вхождения, движением по
сделке или новой сделкой. Объект-действие в данных не хранится.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal

from .forecast import Forecast, ForecastInput, forecast
from .model import Income, KOPEK, Payment
from .settlements import (OUT, Deal, Movement, OccurrenceEdit, ScheduleRule,
                          Settlements, deal_balance, deal_holder_at,
                          funding_wallet, occurrences)


# --- действия --------------------------------------------------------------

@dataclass(frozen=True)
class Move:
    """Перенести платёж: вхождение по плановой дате переезжает на `to`.

    Плановая дата не меняется — она и опознаёт вхождение; переезжает та, когда
    платить. Отложение — тот же перенос без даты (`to=None`): платить пока некуда,
    и вхождение ждёт договорённости, а не пропадает.
    """
    deal: str
    planned: date
    to: date | None = None


@dataclass(frozen=True)
class Bridge:
    """Мост: деньги приходят сейчас и возвращаются через `days` дней под ставку.

    Ставка дневная и передаётся снаружи — числа в движок не вшиты. Сумма к
    возврату считается здесь: `amount` плюс проценты по ставке за срок (округление
    вверх — цена ошибки дороже копейки).
    """
    counterparty: str
    wallet: str
    date: date
    amount: Decimal
    days: int
    rate_per_day: Decimal
    title: str = "мост"


@dataclass(frozen=True)
class Prepay:
    """Погасить досрочно из свободных денег месяца.

    Деньги берутся из свободного остатка месяца, в котором стоит дата: больше
    остатка — действие невозможно. Кошелёк — по умолчанию тот, что финансирует
    сделку. У сделки из единицы закрытия деньги уходят в копилку: единица
    закрывается разом и не одной сделкой в одиночку.
    """
    deal: str
    date: date
    amount: Decimal
    wallet: str | None = None


@dataclass(frozen=True)
class Direct:
    """Направить свободный остаток: согласие владельца на второй приоритет.

    Сам по себе второй приоритет не платится — движок показывает свободный
    остаток и предлагает. Это действие и есть согласие: с ним прокат считает
    погашение второго приоритета, без него — нет.
    """


Action = Move | Bridge | Prepay | Direct


@dataclass(frozen=True)
class Variant:
    """Вариант: список действий, применяемый к сценарию целиком."""
    label: str
    actions: tuple[Action, ...] = ()


class ImpossibleAction(ValueError):
    """Действие невозможно: так его нельзя выполнить, а не «невыгодно»."""


# --- база ------------------------------------------------------------------

@dataclass(frozen=True)
class Base:
    """База для вариантов: исходный сценарий, его прогноз и горизонт проката.

    Прогноз считается один раз: цена варианта сравнивает с ним, а не считает базу
    заново. Горизонт — последний день окна проката: за него действие не заглядывает.
    Окно может быть короче `max_months` — тогда горизонт сужается вместе с ним:
    за окном вопрос кончился, и действие там невозможно.
    """
    inp: ForecastInput
    forecast: Forecast
    horizon: date


def baseline(inp: ForecastInput) -> Base:
    """Собрать базу: прогноз исходного сценария и горизонт проката."""
    plan = forecast(inp)
    # Горизонт — конец окна, а не предел месяцев: окно кончается там, где
    # кончился вопрос, и вариант катается в том же окне.
    return Base(inp=inp, forecast=plan, horizon=plan.window.until)


# --- что невозможно --------------------------------------------------------

def impossible(base: Base, variant: Variant) -> str | None:
    """Почему вариант невозможен; None — возможен.

    Отклоняется только невозможное; невыгодное считается и показывается ценой.
    Порядок: конфликт, сами действия, затем бюджет досрочек — по входу варианта
    (`_entry_forecast`): перенос, освободивший деньги месяца, идёт в зачёт, а
    вход не собирается, пока действия не прошли проверку — невалидное действие
    названо причиной, а не ошибкой сборки. Первая причина и возвращается:
    дальше проверять нечего.
    """
    conflict = _conflict(variant)
    if conflict is not None:
        return conflict
    for action in variant.actions:
        reason = _reason(base, action)
        if reason is not None:
            return reason
    entry = _entry_forecast(base, variant)
    for action in variant.actions:
        if isinstance(action, Prepay):
            reason = _prepay_budget_reason(action, entry)
            if reason is not None:
                return reason
    return _totals(base, variant, entry)


def _entry_forecast(base: Base, variant: Variant) -> Forecast | None:
    """Прогноз входа варианта — по нему меряется бюджет досрочек; None — досрочек нет.

    Вход — база плюс все действия, кроме всех досрочек: исключены все разом,
    поэтому бюджет не зависит от порядка действий. Досрочки одни — вход это сама
    база, прогноз уже посчитан; лишний прогноз заводится только когда вариант
    что-то двигает кроме досрочек. Вызывается после проверки действий: вход
    собирается только из действий, уже названных возможными.
    """
    actions = tuple(a for a in variant.actions if not isinstance(a, Prepay))
    if len(actions) == len(variant.actions):
        return None
    if not actions:
        return base.forecast
    return forecast(_variant_input(base, actions))


def _totals(base: Base, variant: Variant, entry: Forecast | None) -> str | None:
    """Проверка по сумме: вместе действия обещают больше, чем каждое по силам.

    Каждая досрочка по отдельности может укладываться в остаток сделки и в
    бюджет месяца, а вместе они обещают больше, чем есть: вариант применяется
    целиком, поэтому и проверяется целиком. Бюджет месяца — по входу варианта,
    как и у проверки одной досрочки (`_budget_at`).
    """
    book = base.inp.book
    by_deal: dict[str, Decimal] = {}
    by_month: dict[date, Decimal] = {}
    for action in variant.actions:
        if not isinstance(action, Prepay):
            continue
        by_deal[action.deal] = by_deal.get(action.deal, Decimal(0)) + action.amount
        month = action.date.replace(day=1)
        by_month[month] = by_month.get(month, Decimal(0)) + action.amount
    for deal_uid, total in by_deal.items():
        deal = _deal(book, deal_uid)
        if deal is None:
            continue                 # неизвестную сделку назвала проверка действия
        on = min(a.date for a in variant.actions
                 if isinstance(a, Prepay) and a.deal == deal_uid)
        remaining = _remaining(book, deal, on)
        if total > remaining:
            return (f"досрочки по сделке {deal_uid!r} вместе больше остатка "
                    f"({remaining})")
    for month, total in by_month.items():
        free = _budget_at(entry, month)
        if free is not None and total > free:
            return (f"досрочки месяца {month} вместе больше свободных денег "
                    f"({free})")
    by_wallet: dict[str, Decimal] = {}
    for action in variant.actions:
        if isinstance(action, Bridge):
            by_wallet[action.wallet] = (by_wallet.get(action.wallet, Decimal(0))
                                        + action.amount)
    for wallet_uid, total in by_wallet.items():
        wallet = _wallet(book, wallet_uid)
        if wallet is None or wallet.free_limit is None:
            continue                 # неизвестный кошелёк назвала проверка действия
        if total > wallet.free_limit:
            return (f"мосты с кошелька {wallet_uid!r} вместе больше доступного "
                    f"лимита ({wallet.free_limit})")
    return None


def _conflict(variant: Variant) -> str | None:
    """Конфликт действий: два действия правят одно и то же.

    У согласия владельца цели нет — повтор безвреден, а не конфликт.
    """
    seen: set[tuple] = set()
    for action in variant.actions:
        target = _target(action)
        if target is None:
            continue
        if target in seen:
            return f"конфликт действий: {target[0]} {target[1]!r} назначен дважды"
        seen.add(target)
    return None


def _target(action: Action) -> tuple | None:
    if isinstance(action, Move):
        return ("перенос", action.deal, action.planned)
    if isinstance(action, Prepay):
        return ("досрочка", action.deal, action.date)
    if isinstance(action, Bridge):
        return ("мост", action.wallet, action.date)
    return None


def _reason(base: Base, action: Action) -> str | None:
    if isinstance(action, Move):
        return _move_reason(base, action)
    if isinstance(action, Bridge):
        return _bridge_reason(base, action)
    if isinstance(action, Prepay):
        return _prepay_reason(base, action)
    return None                      # согласие владельца невозможным не бывает


def _move_reason(base: Base, action: Move) -> str | None:
    book = base.inp.book
    found = occurrences(book, action.deal, action.planned, action.planned)
    if not found:
        return (f"перенос {action.deal!r} от {action.planned}: такого вхождения "
                f"по графику нет")
    [occ] = found
    if not occ.payable:
        return (f"перенос {action.deal!r} от {action.planned}: вхождение "
                f"{occ.status} — переносить нечего")
    if action.to is not None and action.to > base.horizon:
        return (f"перенос {action.deal!r} от {action.planned}: дата {action.to} "
                f"за горизонтом проката ({base.horizon})")
    return None


def _bridge_reason(base: Base, action: Bridge) -> str | None:
    book = base.inp.book
    if action.amount <= 0:
        return "мост: сумма должна быть положительной"
    if action.days <= 0:
        return "мост: срок должен быть положительным"
    if action.rate_per_day < 0:
        return "мост: ставка не может быть отрицательной"
    wallet = _wallet(book, action.wallet)
    if wallet is None:
        return f"мост: неизвестный кошелёк {action.wallet!r}"
    if action.counterparty not in {c.uid for c in book.counterparties}:
        return f"мост: неизвестный контрагент {action.counterparty!r}"
    if _uid(action) in {d.uid for d in book.deals}:
        return f"мост {action.date}: сделка {_uid(action)!r} уже объявлена"
    if not any(cm.hole > 0 for cm in base.forecast.months):
        return "мост при отсутствии дыры: закрывать нечего"
    repay = _repay_date(action)
    if repay > base.horizon:
        return (f"мост {action.date}: возврат {repay} за горизонтом проката "
                f"({base.horizon})")
    free = wallet.free_limit
    if free is None:
        return (f"мост: лимит кошелька {action.wallet!r} неизвестен — оценить "
                f"мост нечем")
    if action.amount > free:
        return (f"мост {action.amount}: больше доступного лимита кошелька "
                f"{action.wallet!r} ({free})")
    return None


def _prepay_reason(base: Base, action: Prepay) -> str | None:
    """Досрочка: положительная сумма, остаток сделки и кошелёк.

    Бюджет месяца здесь не при чём: он меряется по входу варианта
    (`_prepay_budget_reason`) и в этом порядке — после проверки всех действий.
    """
    book = base.inp.book
    if action.amount <= 0:
        return "досрочка: сумма должна быть положительной"
    deal = _deal(book, action.deal)
    if deal is None:
        return f"досрочка: неизвестная сделка {action.deal!r}"
    if not deal.closing:
        return (f"досрочка {action.deal!r}: регулярный расход не гасится — "
                f"закрывать нечего")
    wallet = action.wallet or funding_wallet(deal)
    if wallet is None:
        return (f"досрочка {action.deal!r}: финансирующий кошелёк не задан — "
                f"платить не с чего")
    if _wallet(book, wallet) is None:
        return f"досрочка {action.deal!r}: неизвестный кошелёк {wallet!r}"
    remaining = _remaining(book, deal, action.date)
    if action.amount > remaining:
        return (f"досрочка {action.deal!r}: {action.amount} больше остатка "
                f"({remaining})")
    return None


def _prepay_budget_reason(action: Prepay, entry: Forecast | None) -> str | None:
    """Бюджет одной досрочки — по прогнозу входа варианта.

    Вход — база плюс все действия, кроме всех досрочек: перенос, освободивший
    деньги месяца, идёт в зачёт, а исключённые все досрочки разом делают
    проверку независимой от порядка действий.
    """
    free = _budget_at(entry, action.date)
    if free is None:
        return (f"досрочка {action.deal!r}: месяц {action.date} за отчётом — "
                f"свободных денег не видно")
    if action.amount > free:
        return (f"досрочка {action.deal!r}: {action.amount} больше свободных "
                f"денег месяца ({free})")
    return None


# --- применение ------------------------------------------------------------

def applied(base: Base, variant: Variant) -> ForecastInput:
    """Вход варианта: база плюс все действия списка — целиком и без правки базы.

    Невозможное действие не применяется: вместо молчаливой правки — ошибка с
    причиной (`impossible` называет её же).
    """
    reason = impossible(base, variant)
    if reason is not None:
        raise ImpossibleAction(f"{variant.label}: {reason}")
    return _variant_input(base, variant.actions)


def _variant_input(base: Base, actions: tuple[Action, ...]) -> ForecastInput:
    """Сборка входа: база плюс переданные действия — целиком и без правки базы.

    Общая для применённого варианта и его входа без досрочек (`_entry_forecast`):
    проверка и применение собирают вход одним и тем же кодом.
    """
    book, one_offs, incomes = base.inp.book, list(base.inp.one_offs), list(base.inp.incomes)
    consent = base.inp.consent_to_second
    for action in actions:
        if isinstance(action, Move):
            book = replace(book, edits=[*book.edits, _move_edit(action)])
        elif isinstance(action, Prepay):
            movement, payment = _prepay_facts(book, action)
            book = replace(book, movements=[*book.movements, movement])
            one_offs.append(payment)
        elif isinstance(action, Bridge):
            deal, income = _bridge_facts(action)
            book = replace(book, deals=[*book.deals, deal])
            incomes.append(income)
        else:
            consent = True
    # Окно базы передаётся, только если вариант не меняет входов окна: согласие
    # владельца продлевает окно до закрытия второго приоритета, и вариант с ним
    # обязан посчитать своё окно, а не кататься в базовом. Остальные действия
    # горизонтом отсечены: сдвиг и мост за окном невозможны (`impossible`),
    # а досрочка окно только укорачивает.
    window = base.forecast.window if consent == base.inp.consent_to_second else None
    return replace(base.inp, book=book, one_offs=one_offs, incomes=incomes,
                   consent_to_second=consent, window=window)


def facts(base: Base, variant: Variant) -> list[OccurrenceEdit | Movement | Deal]:
    """Записи журнала для принятого варианта: правка, движение, сделка.

    Объект-действие в данных не хранится: принятое действие становится фактом —
    правкой вхождения (перенос), движением по сделке (досрочка) или новой сделкой
    с движением денег (мост). Согласие владельца фактом не становится: это
    решение о расчёте, а не запись о деньгах.
    """
    reason = impossible(base, variant)
    if reason is not None:
        raise ImpossibleAction(f"{variant.label}: {reason}")
    rows: list[OccurrenceEdit | Movement | Deal] = []
    book = base.inp.book
    for action in variant.actions:
        if isinstance(action, Move):
            rows.append(_move_edit(action))
        elif isinstance(action, Prepay):
            movement, _ = _prepay_facts(book, action)
            rows.append(movement)
        elif isinstance(action, Bridge):
            deal, _ = _bridge_facts(action)
            rows.append(deal)
    return rows


def _move_edit(action: Move) -> OccurrenceEdit:
    """Перенос как правка вхождения: дата — назначенная, `None` — отложение."""
    return OccurrenceEdit(deal=action.deal, planned=action.planned,
                          postponed=True, moved_to=action.to)


def _prepay_facts(book: Settlements, action: Prepay) -> tuple[Movement, Payment]:
    """Досрочка двумя сторонами: движение по сделке и платёж кассы.

    Одна правка, два носителя — так модель и устроена: долговая сторона читает
    движение, кассовая платёж. Платёж идёт **до** расчёта свободных денег месяца:
    иначе месяц пообещал бы эти деньги дважды — и явной досрочкой, и стратегией.
    """
    deal = _deal(book, action.deal)
    assert deal is not None                    # проверено `impossible`
    wallet = action.wallet or funding_wallet(deal)
    movement = Movement(
        date=action.date, amount=action.amount, deal=action.deal, direction=OUT,
        paid_to=deal_holder_at(book, action.deal, action.date), wallet=wallet,
        purpose="досрочное погашение")
    # Досрочка долговая: кредитным лимитом она не платится — признак платежа
    # назван явно, чтобы это не зависело от значения по умолчанию.
    payment = Payment(date=action.date, amount=action.amount, account=wallet,
                      counterparty=deal.counterparty, debt=True)
    return movement, payment


def _bridge_facts(action: Bridge) -> tuple[Deal, Income]:
    """Мост двумя сторонами: сделка с условиями и приход денег в кассу.

    Отдельной сущности «мост» нет: в данных это новая сделка (условия и график
    возврата) и деньги, пришедшие на кошелёк. Ставка у сделки нулевая: проценты
    уже вшиты в сумму к возврату — дневное начисление проката считает месяц
    целиком и мост, взятый в середине месяца, удорожило бы.
    """
    repay = _repay_date(action)
    total = action.amount + _bridge_cost(action)
    deal = Deal(
        uid=_uid(action), title=action.title, counterparty=action.counterparty,
        amount=total, rate_per_year=Decimal(0), wallet=action.wallet,
        kind="мост",
        schedule=ScheduleRule(days=(repay.day,), payment=total, count=1,
                              start=repay.replace(day=1)))
    income = Income(date=action.date, amount=action.amount, account=action.wallet)
    return deal, income


def _bridge_cost(action: Bridge) -> Decimal:
    """Проценты моста по переданной ставке: срок в днях, округление вверх."""
    return (action.amount * action.rate_per_day
            * Decimal(action.days)).quantize(KOPEK, ROUND_CEILING)


def _repay_date(action: Bridge) -> date:
    """Когда возвращать мост: дата прихода плюс срок."""
    return action.date + timedelta(days=action.days)


def _uid(action: Bridge) -> str:
    """Уид моста: по дате и кошельку — два моста в один день не столкнутся."""
    return f"мост-{action.date.isoformat()}-{action.wallet}"


# --- цена ------------------------------------------------------------------

@dataclass(frozen=True)
class Price:
    """Цена варианта: набор измерений, а не одно число.

    `interest` — рубли процентов за прокат: плюс — дороже базы, минус — дешевле.
    `delay_days` — дни просрочки, внесённые переносом; None — платёж отложен без
    даты, и просрочка не измерена. Дыра меряется тремя величинами: `hole_*` —
    худшая глубина, `hole_date_*` — дата первой дыры (когда началась),
    `hole_months_*` — сколько месяцев с дырой (сколько держится). Рядом —
    `unsecured_*`: сколько за прокат не прошло из-за ёмкости своего кошелька, —
    вариант может и пообещать деньги, которых на кошельке нет. Сдвиги — в
    месяцах, None — дата не достигнута в одном из расчётов. `free_*` — свободные
    деньги последнего месяца окна: величина на руках, а не поток, и «чем кончился
    прокат» сравнимо, а сумма по месяцам посчитала бы одни деньги много раз.
    """
    label: str
    interest: Decimal
    delay_days: int | None
    hole_before: Decimal
    hole_after: Decimal
    hole_date_before: date | None
    hole_date_after: date | None
    hole_months_before: int
    hole_months_after: int
    unsecured_before: Decimal
    unsecured_after: Decimal
    first_priority: int | None
    second_priority: int | None
    step1: int | None
    step2: int | None
    free_before: Decimal
    free_after: Decimal

    @property
    def hole_change(self) -> Decimal:
        """Как изменилась дыра: плюс — глубже, минус — мельче или пропала."""
        return self.hole_after - self.hole_before

    @property
    def free_change(self) -> Decimal:
        """Как изменились свободные деньги тесного месяца: плюс — свободнее."""
        return self.free_after - self.free_before


def price(base: Base, variant: Variant) -> Price:
    """Цена варианта: база против варианта, измерения рядом.

    Невозможный вариант не считается: цена невозможного — не цена, а причина
    (`impossible`). Невыгодный считается и показывается: решение за человеком.
    """
    moved = forecast(applied(base, variant))
    months = min(len(base.forecast.months), len(moved.months))
    before, after = base.forecast.months[:months], moved.months[:months]
    before_hole, before_date, before_months = _holes(before)
    after_hole, after_date, after_months = _holes(after)
    return Price(
        label=variant.label,
        interest=moved.interest - base.forecast.interest + _bridges_cost(variant),
        delay_days=_delay_days(variant),
        hole_before=before_hole, hole_after=after_hole,
        hole_date_before=before_date, hole_date_after=after_date,
        hole_months_before=before_months, hole_months_after=after_months,
        unsecured_before=base.forecast.unsecured_total,
        unsecured_after=moved.unsecured_total,
        first_priority=_months_between(base.forecast.first_priority.month,
                                       moved.first_priority.month),
        second_priority=_months_between(base.forecast.second_priority.month,
                                        moved.second_priority.month),
        step1=_months_between(base.forecast.step1.month, moved.step1.month),
        step2=_months_between(base.forecast.step2.month, moved.step2.month),
        free_before=_final_free(before), free_after=_final_free(after),
    )


def prices(base: Base, variants: list[Variant]) -> list[Price]:
    """Цены вариантов в порядке передачи — строка таблицы на каждый."""
    return [price(base, variant) for variant in variants]


def _final_free(months) -> Decimal:
    """Свободные деньги последнего месяца окна: чем прокат кончился.

    Свободные деньги — величина на руках, а не поток: сумма по месяцам посчитала
    бы одни и те же деньги столько раз, сколько месяцев в окне.
    """
    if not months:
        return Decimal(0)
    return months[-1].free


def _holes(months) -> tuple[Decimal, date | None, int]:
    """Дыра окна: худшая глубина, дата первой и сколько месяцев с дырой.

    Одного числа мало: дыра может стать мельче и продержаться дольше — это
    разные изменения, и владельцу видно только то, что названо.
    """
    if not months:
        return Decimal(0), None, 0
    worst = max(months, key=lambda cm: cm.hole)
    first = next((cm.hole_date for cm in months if cm.hole > 0), None)
    return worst.hole, first, sum(1 for cm in months if cm.hole > 0)


def _delay_days(variant: Variant) -> int | None:
    """Дни просрочки, внесённые переносом: от плановой даты до назначенной.

    Перенос раньше срока просрочки не создаёт — дни считаются только вперёд.
    Отложение без даты просрочку не измеряет: даты нет, а ноль соврал бы.
    """
    total = 0
    for action in variant.actions:
        if not isinstance(action, Move):
            continue
        if action.to is None:
            return None
        total += max((action.to - action.planned).days, 0)
    return total


def _bridges_cost(variant: Variant) -> Decimal:
    """Проценты мостов варианта: цена берётся по переданной ставке."""
    return sum((_bridge_cost(a) for a in variant.actions
                if isinstance(a, Bridge)), Decimal(0))


def _months_between(base: date | None, moved: date | None) -> int | None:
    """На сколько месяцев переехала дата; None — она не достигнута в одном расчёте."""
    if base is None or moved is None:
        return None
    return (moved.year - base.year) * 12 + moved.month - base.month


# --- перебор ---------------------------------------------------------------

def variants(base: Base, *, window_days: int = 7,
             bridge: Bridge | None = None) -> list[Variant]:
    """Узкий набор вариантов: сдвиги платежей в окне и мост под дыру.

    Сдвиг предлагается для платежей, стоящих в окне вокруг первой дыры: их
    переносят на дату ближайшего прихода — деньги приходят, платёж проходит.
    Мост предлагается под саму дыру: сумма — дыра, дата — её дата. Числа (срок
    окна, ставка моста) приходят снаружи: в движке их нет.

    Невозможное движок не предлагает: в наборе только те варианты, которые
    можно выполнить. Дыры нет — предлагать нечего.
    """
    hole = next((cm for cm in base.forecast.months if cm.hole > 0), None)
    if hole is None or hole.hole_date is None:
        return []
    rows: list[Variant] = []
    income = next((i.date for i in base.inp.incomes if i.date > hole.hole_date), None)
    if income is not None and income <= base.horizon:
        for deal, planned in _payments_in_window(base, hole.hole_date, window_days):
            rows.append(Variant(
                label=f"сдвиг {deal} {planned} → {income}",
                actions=(Move(deal=deal, planned=planned, to=income),)))
    if bridge is not None:
        rows.append(Variant(
            label=f"мост {hole.hole_date} {hole.hole}",
            actions=(replace(bridge, date=hole.hole_date, amount=hole.hole),)))
    return [variant for variant in rows if impossible(base, variant) is None]


def _payments_in_window(base: Base, since: date, window_days: int
                        ) -> list[tuple[str, date]]:
    """Платежи, стоящие в окне от дыры: что можно сдвинуть, чтобы её закрыть."""
    until = since + timedelta(days=window_days)
    rows: list[tuple[str, date]] = []
    for deal in base.inp.book.deals:
        if deal.second_priority:
            continue                 # второй приоритет платится не по графику
        for occ in occurrences(base.inp.book, deal.uid, since, until):
            if occ.payable:
                rows.append((deal.uid, occ.planned))
    return rows


# --- мелкие ----------------------------------------------------------------

def _wallet(book: Settlements, uid: str):
    return next((w for w in book.wallets if w.uid == uid), None)


def _deal(book: Settlements, uid: str) -> Deal | None:
    return next((d for d in book.deals if d.uid == uid), None)


def _remaining(book: Settlements, deal: Deal, on: date) -> Decimal:
    """Сколько осталось закрыть по сделке: больше этого досрочкой не заплатить.

    У сделки из единицы закрытия деньги идут в копилку, но записываются по
    сделке — движением по ней. Больше её остатка не записать, а единицу в
    одиночку не закрыть: копилка закрывается разом, своими платежами.
    """
    return deal_balance(book, deal.uid, on) or Decimal(0)


def _budget_at(fc: Forecast | None, when: date) -> Decimal | None:
    """Бюджет досрочек месяца в прогнозе входа; None — месяц не виден в окне входа.

    Бюджет, а не свободные деньги: отрицательные свободные деньги — нехватка, и
    досрочке они ничего не дают (`CashMonth.prepay_budget`). Окно — у самого
    входа: месяц за его отчётом не виден, а не нулевой — отказ по неизвестному,
    а не разрешение.
    """
    if fc is None:
        return None
    for cm in fc.months:
        if cm.month == when.replace(day=1):
            return cm.prepay_budget
    return None
