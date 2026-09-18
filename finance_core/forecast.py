"""Прогноз: когда ступени «выбрался» достигнуты и что этому мешает.

Отчёт отвечает на вопрос владельца набором дат, а не одной
(`docs/decisions/what-means-out.md`): ступень 1 — позади последний месяц с
дефицитом (устойчивость, а не один удачный месяц), ступень 2 — первый месяц со
свободными деньгами. Плюс даты по приоритетам и подушке.

Отчёт идёт до момента, когда ступени достигнуты, а не до фиксированного числа
месяцев. Не достигнуты — сказано прямо: ступень называет причину, а не показывает
последнюю цифру проката. Неизвестное не подставляется нулём: без прожиточного
минимума ступени не измерены, а прогноз помечен неполным.

Неизвестные разбираются **по одному**: база плюс сдвиг даты от каждого параметра
отдельно (`forecast_shifts`) — комбинированная вилка смешала бы причины, и не было
бы видно, что задаёт ширину.

Личных данных здесь нет: книга, точка отсчёта, пороги и цели приходят снаружи.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, fields, replace
from datetime import date
from decimal import Decimal
from typing import Any

from .model import Income, Payment, Transfer
from .roll import AVALANCHE, Expectation, Gap, MonthsRoll, roll_months
from .settlements import FAMILY, I_OWE, Settlements, Wallet, deal_balance
from .solver import CashMonth


# --- ступени и даты --------------------------------------------------------

@dataclass
class Milestone:
    """Дата-цель: месяц, когда достигнута, или причина, почему нет.

    Не достигнута — сказано прямо: `month=None` и названа причина, а не показана
    последняя цифра отчёта.
    """
    month: date | None = None
    reason: str | None = None

    @property
    def reached(self) -> bool:
        """Достигнута ли цель."""
        return self.month is not None


@dataclass
class Deficit:
    """Месяц с дефицитом: дыра или нехватка до прожиточного минимума.

    Дефицит — не «плохой месяц»: ступень 1 держится на **последнем** таком месяце,
    а не на первом удачном. Требование в кассу не подмешивается, но видно, закрыло
    бы его поступление дыру или нет.
    """
    index: int
    month: date
    hole: Decimal
    hole_date: date | None
    floor_gap: Decimal | None
    floor_gap_date: date | None
    #: Требования, до которых можно дотянуться к дате дыры.
    expectations_due: Decimal = Decimal(0)

    @property
    def covers_hole(self) -> bool | None:
        """Закрыло бы требование дыру; None — дыры нет, закрывать нечего."""
        if self.hole <= 0:
            return None
        return self.expectations_due >= self.hole


@dataclass
class FamilyTransfer:
    """Передача внутри семьи: деньги ушли члену семьи — кому, сколько и, у разовой
    передачи, на что.

    В кассе это расход, в отчёте — отдельная строка с детализацией, а не строка
    среди платежей по долгам.
    """
    date: date
    counterparty: str
    amount: Decimal
    purpose: str | None = None


@dataclass
class Discrepancy:
    """Расхождение фактического и расчётного остатка — открытый вопрос.

    Факт введён извне и расчёт не заменяет: расхождение закрывается записью
    недостающих данных, а не правкой числа. Вопросом становится только несошедшаяся
    сверка — сошедшейся здесь не бывает.
    """
    deal: str
    on: date
    observed: Decimal
    computed: Decimal | None

    @property
    def difference(self) -> Decimal | None:
        """Насколько факт разошёлся с расчётом; None — сверять нечего."""
        if self.computed is None:
            return None
        return self.observed - self.computed


@dataclass
class Shift:
    """Сдвиг прогноза от одного параметра: на сколько месяцев переехали ступени.

    База плюс **один** параметр: комбинированная вилка смешала бы причины, и не
    было бы видно, что задаёт ширину. Сдвиг знаковый — выше минимум, дата позже,
    больше приход, дата раньше, — а ширину задаёт размах: параметр расширяет вилку
    и тогда, когда тянет дату назад.
    """
    label: str
    step1: date | None
    step2: date | None
    step1_shift: int | None
    step2_shift: int | None

    @property
    def months(self) -> int | None:
        """Насколько параметр расширяет вилку: наибольший сдвиг ступени по величине."""
        moves = [abs(m) for m in (self.step1_shift, self.step2_shift)
                 if m is not None]
        if not moves:
            return None
        return max(moves)


# --- вход ------------------------------------------------------------------

@dataclass
class ForecastInput:
    """Что нужно прогнозу: книга, точка отсчёта, касса месяца, пороги и цели.

    Точка отсчёта и остатки кошельков передаются снаружи — движок не спрашивает
    «сегодня». `living_floor` и `obligation_reserve` — пороги зоны, а не движка:
    неизвестный минимум не подставляется нулём. `cushion` — цель подушки: из
    свободных денег она не вычитается, иначе величина занижается и цель становится
    недостижимой на бумаге. `consent_to_second` — согласие владельца направлять
    свободный остаток во второй приоритет: без него погашение не считается.
    """
    book: Settlements
    start: date
    wallets: list[Wallet]
    incomes: list[Income] = field(default_factory=list)
    one_offs: list[Payment] = field(default_factory=list)
    living_floor: Decimal | None = None
    obligation_reserve: Decimal | None = None
    transfers: list[Transfer] = field(default_factory=list)
    income_horizon: date | None = None
    cushion: Decimal | None = None
    consent_to_second: bool = False
    strategy: str = AVALANCHE
    max_months: int = 600
    max_iterations: int = 100
    main: str | None = None


# --- отчёт -----------------------------------------------------------------

@dataclass
class Forecast:
    """Прогноз: обе ступени, даты по приоритетам, вилка и открытые вопросы.

    `months` — месяцы отчёта: они кончаются там, где ступени достигнуты, а не на
    фиксированном числе. `assumed` — месяцы, посчитанные на допущении (после
    дыры): цифра на допущении не выглядит фактом. `questions` — необъяснённые
    расхождения факта с расчётом: пока они есть, прогноз неполон.
    `unsecured_total` — сколько за прокат не прошло из-за ёмкости своего кошелька:
    не ноль значит, что прогноз долгов держится на переводе. `interest` — рубли
    процентов за прокат целиком: прогноз, закрывающий долги раньше, платит меньше.
    """
    start: date
    months: list[CashMonth]
    assumed: list[int]
    step1: Milestone
    step2: Milestone
    first_priority: Milestone
    second_priority: Milestone
    cushion: Milestone
    deficits: list[Deficit]
    expectations: list[Expectation]
    family: list[FamilyTransfer]
    questions: list[Discrepancy]
    gaps: list[Gap]
    living_floor: Decimal | None = None
    obligation_reserve: Decimal | None = None
    unsecured_total: Decimal = Decimal(0)
    interest: Decimal = Decimal(0)

    @property
    def reached(self) -> bool:
        """Достигнуты ли обе ступени «выбрался»."""
        return self.step1.reached and self.step2.reached

    @property
    def complete(self) -> bool:
        """Полон ли прогноз: минимум известен, нехватка измерена, пробелов нет.

        Неполный прогноз не выдаётся за полный: причина названа в ступенях,
        `questions` и `gaps`.
        """
        return (self.living_floor is not None and not self.gaps
                and not self.questions
                and all(cm.floor_gap is not None for cm in self.months))

    @property
    def family_total(self) -> Decimal:
        """Сколько за отчёт ушло членам семьи."""
        return sum((t.amount for t in self.family), Decimal(0))


# --- расчёт ----------------------------------------------------------------

def forecast(inp: ForecastInput) -> Forecast:
    """Построить прогноз: ступени, даты по приоритетам, вилку и открытые вопросы.

    Прокат месяцев даёт кассу по месяцам; отчёт читает из неё дефицит, свободные
    деньги и допущения. Отчёт идёт до момента, когда ступени достигнуты; не
    достигнуты — ступень называет причину. Без прожиточного минимума ступени не
    измерены: ноль не подставляется.
    """
    roll = roll_months(
        inp.book, inp.start, inp.wallets, inp.incomes, inp.one_offs,
        inp.living_floor, obligation_reserve=inp.obligation_reserve,
        transfers=inp.transfers, income_horizon=inp.income_horizon,
        strategy=inp.strategy, max_months=inp.max_months,
        max_iterations=inp.max_iterations,
        consent_to_second=inp.consent_to_second, main=inp.main)
    if not roll.cash_months:
        raise ValueError(
            f"прогноз: прокат пуст — max_months должен быть положительным, "
            f"передано {inp.max_months}")

    deficits = _deficits(roll)
    step1 = _step_last_deficit(roll, deficits, inp.living_floor)
    step2 = _step_free_money(roll, step1, inp.living_floor)
    until = _until(roll, step2)
    months = roll.cash_months[:until]
    return Forecast(
        start=inp.start,
        months=months,
        assumed=[i for i in roll.assumed if i <= until],
        step1=step1,
        step2=step2,
        first_priority=_first_priority(roll),
        second_priority=_second_priority(inp, roll),
        cushion=_cushion(roll.cash_months, inp.cushion),
        deficits=[d for d in deficits if d.index <= until],
        expectations=_within(roll.deal_roll.expectations, roll, until),
        family=_family(inp, roll, until),
        questions=_questions(inp.book),
        gaps=roll.deal_roll.gaps,
        living_floor=inp.living_floor,
        obligation_reserve=inp.obligation_reserve,
        unsecured_total=roll.unsecured_total,
        interest=roll.deal_roll.total_interest,
    )


def forecast_shifts(inp: ForecastInput,
                    variations: Mapping[str, Any]) -> list[Shift]:
    """Разобрать неизвестные по одному: база плюс сдвиг от каждого параметра.

    Ключ — имя поля `ForecastInput`, значение — чем его заменить в этом варианте
    («минимум выше на столько-то»). Каждый вариант считается от базы, а не от
    предыдущего: комбинированная вилка смешала бы причины, и не было бы видно, что
    задаёт ширину.
    """
    known = {f.name for f in fields(ForecastInput)}
    unknown = sorted(set(variations) - known)
    if unknown:
        raise ValueError(f"неизвестный параметр прогноза: {unknown}; "
                         f"объявлены: {sorted(known)}")
    base = forecast(inp)
    return [_shift(label, base, forecast(replace(inp, **{label: value})))
            for label, value in variations.items()]


def widest(shifts: Iterable[Shift]) -> Shift | None:
    """Кто задаёт ширину вилки: параметр с наибольшим сдвигом по величине.

    None — сдвинуть нечего или не с чем сравнить: ступени не достигнуты.
    """
    known = [s for s in shifts if s.months is not None]
    if not known:
        return None
    return max(known, key=lambda s: s.months)


# --- ступени ---------------------------------------------------------------

def _deficits(roll: MonthsRoll) -> list[Deficit]:
    """Месяцы с дефицитом: дыра или нехватка до прожиточного минимума."""
    rows: list[Deficit] = []
    for cm in roll.cash_months:
        hole = cm.hole > 0
        gap = cm.floor_gap is not None and cm.floor_gap > 0
        if not hole and not gap:
            continue
        rows.append(Deficit(
            index=cm.index, month=cm.month, hole=cm.hole, hole_date=cm.hole_date,
            floor_gap=cm.floor_gap, floor_gap_date=cm.floor_gap_date,
            expectations_due=_due_by(roll.deal_roll.expectations, cm.hole_date)))
    return rows


def _due_by(expectations: Iterable[Expectation], when: date | None) -> Decimal:
    """Сколько требований доходит к дате: видно, закрыло бы оно дыру или нет."""
    if when is None:
        return Decimal(0)
    return sum((e.amount for e in expectations if e.date <= when), Decimal(0))


def _step_last_deficit(roll: MonthsRoll, deficits: list[Deficit],
                       living_floor: Decimal | None) -> Milestone:
    """Ступень 1: позади последний месяц с дефицитом — устойчивость, не удача.

    Без прожиточного минимума дефицит не оценён; не оценена и нехватка в месяце,
    до которого не видно прихода (`floor_gap = None`). «Не оценено» отсутствием
    дефицита не объявляется: судить о нём нечем, и ступень не выдаётся за
    достигнутую.
    """
    if living_floor is None:
        return Milestone(reason="прожиточный минимум неизвестен — дефицит не оценён")
    last = deficits[-1].index if deficits else 0
    if last >= len(roll.cash_months):
        return Milestone(
            reason=f"дефицит держится до конца проката: последний месяц с "
                   f"дефицитом — {last}")
    blind = [cm.index for cm in roll.cash_months[:last + 1]
             if cm.floor_gap is None]
    if blind:
        return Milestone(
            reason="нехватка до прожиточного минимума не оценена: в месяце "
                   f"{blind[0]} судить нечем — прихода впереди не видно или по "
                   f"основному счёту в нём нет событий")
    return Milestone(month=roll.cash_months[last].month)


def _step_free_money(roll: MonthsRoll, step1: Milestone,
                     living_floor: Decimal | None) -> Milestone:
    """Ступень 2: первый месяц со свободными деньгами — сверх ступени 1.

    Свободные деньги считаются до досрочек: они же и есть бюджет досрочек.
    """
    if living_floor is None:
        return Milestone(
            reason="прожиточный минимум неизвестен — свободные деньги не оценены")
    if step1.month is None:
        return Milestone(
            reason="ступень 1 не достигнута — свободные деньги считаются после неё")
    for cm in roll.cash_months:
        if cm.month >= step1.month and cm.free > 0:
            return Milestone(month=cm.month)
    return Milestone(reason="свободных денег за отведённые месяцы не появилось")


def _until(roll: MonthsRoll, step2: Milestone) -> int:
    """До какого месяца идёт отчёт: ступени достигнуты — до них, иначе — весь прокат."""
    if step2.month is None:
        return len(roll.cash_months)
    for cm in roll.cash_months:
        if cm.month >= step2.month:
            return cm.index
    return len(roll.cash_months)


# --- даты по приоритетам ---------------------------------------------------

def _first_priority(roll: MonthsRoll) -> Milestone:
    """Когда закрыт обязательный график: закрывать нечего — закрыт сразу."""
    if roll.deal_roll.freedom is None:
        return Milestone(
            reason="обязательный график не закрылся за отведённые месяцы")
    return Milestone(month=roll.deal_roll.freedom)


def _second_priority(inp: ForecastInput, roll: MonthsRoll) -> Milestone:
    """Когда закрыт второй приоритет: считается только при согласии владельца."""
    if not inp.consent_to_second:
        return Milestone(
            reason="второй приоритет не считается без согласия владельца: движок "
                   "показывает свободный остаток и предлагает направить его туда")
    if roll.deal_roll.second_freedom is not None:
        return Milestone(month=roll.deal_roll.second_freedom)
    claims = [d for d in inp.book.deals if d.second_priority and d.direction == I_OWE]
    if not claims:
        return Milestone(reason="второго приоритета в книге нет")
    # В прокат входит не всякое требование: у пробела (нет ставки, нет суммы) своя
    # строка отчёта, и закрытием его отсутствие в месяцах проката не объявляется.
    gapped = {g.deal for g in roll.deal_roll.gaps}
    rolled = [d for d in claims if d.uid not in gapped]
    if not rolled:
        return Milestone(
            reason="второй приоритет не смоделирован: пробел — не закрытие")
    left = [d for d in rolled
            if roll.deal_roll.months[-1].balances.get(d.uid, Decimal(0)) > 0]
    if not left:
        return Milestone(
            month=roll.cash_months[0].month,
            reason="закрыт к началу проката: закрывать нечего")
    return Milestone(
        reason="второй приоритет не закрылся за отведённые месяцы")


def _cushion(months: list[CashMonth], target: Decimal | None) -> Milestone:
    """Когда свободных денег на руках хватит на подушку.

    Подушка — цель, а не расход: из свободных денег она не вычитается. Считать
    накопление суммой по месяцам нечего: свободные деньги — остаток, а не поток,
    и неистраченное уже входит в свободные деньги следующего месяца. Цель
    достигнута, когда остатка хватает, — по всему прокату, а не только по месяцам
    отчёта: цель может лежать дальше ступеней, как и даты по приоритетам.
    """
    if target is None:
        return Milestone(reason="цель подушки не задана")
    for cm in months:
        if cm.free >= target:
            return Milestone(month=cm.month)
    return Milestone(reason="свободных денег на подушку не хватило")


# --- отдельные строки отчёта -----------------------------------------------

def _within(rows: list[Expectation], roll: MonthsRoll, until: int
            ) -> list[Expectation]:
    """Требования в окне отчёта: дальше его срока строки не показываются."""
    return [e for e in rows if _in_window(e.date, roll, until)]


def _family(inp: ForecastInput, roll: MonthsRoll, until: int) -> list[FamilyTransfer]:
    """Передачи внутри семьи: отдельной строкой, с детализацией — кому и на что.

    Деньги уходят и разовым платежом, и по графику сделки: строка показывает то,
    что у формы есть, — разовый платёж несёт назначение, у сделки назначения не
    бывает, и передача по графику называет кому и сколько.
    """
    family = {c.uid for c in inp.book.counterparties if FAMILY in c.groups}
    rows = [FamilyTransfer(date=p.date, counterparty=p.counterparty,
                           amount=p.amount, purpose=p.purpose)
            for p in inp.one_offs
            if p.counterparty in family and _in_window(p.date, roll, until)]
    rows += [FamilyTransfer(date=sp.date, counterparty=sp.counterparty,
                            amount=sp.amount)
             for dm in roll.deal_roll.months
             for sp in dm.payments
             if sp.counterparty in family and _in_window(sp.date, roll, until)]
    rows.sort(key=lambda t: t.date)
    return rows


def _in_window(when: date, roll: MonthsRoll, until: int) -> bool:
    """Попадает ли дата в окно отчёта: его месяцы, а не всё, что передали.

    Строки за окном не показываются: платежа, которого прокат не видел, в отчёте
    быть не должно.
    """
    first = roll.cash_months[0].month
    if when < first:
        return False
    return (when.year - first.year) * 12 + when.month - first.month < until


def _questions(book: Settlements) -> list[Discrepancy]:
    """Необъяснённые расхождения факта с расчётом — открытые вопросы прогноза.

    Сошедшаяся сверка вопросом не является; расхождение закрывается записью
    недостающих данных, а не правкой числа. Наблюдение, которое нечем сверить
    (у регулярного расхода остатка нет), вопросом не объявляется: иначе прогноз
    остался бы неполным навсегда — и не из-за расхождения.
    """
    rows: list[Discrepancy] = []
    for fact in book.observed:
        row = Discrepancy(fact.deal, fact.on, fact.amount,
                          deal_balance(book, fact.deal, fact.on))
        if row.computed is not None and row.difference != 0:
            rows.append(row)
    rows.sort(key=lambda d: (d.on, d.deal))
    return rows


# --- сдвиги ----------------------------------------------------------------

def _shift(label: str, base: Forecast, variant: Forecast) -> Shift:
    """Сдвиг ступеней от одного параметра: база против варианта."""
    return Shift(
        label=label,
        step1=variant.step1.month,
        step2=variant.step2.month,
        step1_shift=_months_between(base.step1.month, variant.step1.month),
        step2_shift=_months_between(base.step2.month, variant.step2.month),
    )


def _months_between(base: date | None, moved: date | None) -> int | None:
    """На сколько месяцев переехала дата; None — она не достигнута в одном расчёте."""
    if base is None or moved is None:
        return None
    return (moved.year - base.year) * 12 + moved.month - base.month
