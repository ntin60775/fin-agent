"""Тесты долговой стороны — синтетические фикстуры, без личных данных."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_CEILING
from decimal import Decimal as D

from finance_core import (BODY, CAP_NONE, CAP_SHARE, CAP_SUM, COSTS, INTEREST,
                          LEGAL, OWED_TO_ME, PARTS, PENALTY, TRIGGER_OVERDUE,
                          AllocationRule, Charge, Counterparty, Deal, Movement,
                          PenaltyCap, PenaltyRule, PenaltyStep, ScheduleRule,
                          Settlements, TriggerRule, Wallet, accrued_interest,
                          accrued_penalty, compare_deal_strategies, deal_balance,
                          deal_growth, deal_parts, overdue_amount, roll_deals)
from finance_core.settlements import _principal_left

START = date(2026, 1, 1)


def _counterparty(uid: str = "банк", name: str = "Банк", **kw) -> Counterparty:
    base = dict(uid=uid, name=name, kind=LEGAL, subtype="банк", groups=("долги",))
    base.update(kw)
    return Counterparty(**base)


def _deal(uid: str = "заём", amount: D | None = D("1000"), **kw) -> Deal:
    base = dict(uid=uid, title="Заём", counterparty="банк", amount=amount,
                start=START, rate_per_year=D("0"), wallet="карта")
    base.update(kw)
    return Deal(**base)


def _book(*deals, counterparties=(), wallets=None, movements=(), edits=(),
          charges=()) -> Settlements:
    if wallets is None:
        wallets = [Wallet("карта", "Карта", "карта", D("0"))]
    return Settlements(
        counterparties=[_counterparty(), *counterparties],
        wallets=list(wallets),
        deals=list(deals),
        movements=list(movements),
        edits=list(edits),
        charges=list(charges),
    )


def _rule(days=(20,), **kw) -> ScheduleRule:
    return ScheduleRule(days=days, **kw)


# --- амортизация ----------------------------------------------------------

def test_interest_free_payoff_is_ceiling_of_division():
    book = _book(_deal("рассрочка", amount=D("10000"),
                       schedule=_rule(payment=D("3000"))))
    roll = roll_deals(book, START, D(0))
    assert len(roll.months) == 4              # ceil(10 000 / 3 000)
    assert roll.total_interest == D("0")


def test_revolving_payoff_matches_annuity_formula():
    """Срок совпадает с формулой аннуитета — независимая проверка движка."""
    balance, payment, rate = D("120000"), D("5000"), D("0.24")
    book = _book(_deal("карта", amount=balance, rate_per_year=rate,
                       schedule=_rule(payment=payment)))
    roll = roll_deals(book, START, D(0))
    r = rate / 12
    n = (-(1 - r * balance / payment).ln() / (1 + r).ln()).to_integral_value(
        rounding=ROUND_CEILING)
    assert len(roll.months) == n


def test_daily_rate_uses_calendar_days():
    """Дневная ставка умножается на число дней месяца: январь дороже февраля."""
    deal = _deal("мфо", amount=D("100000"), rate_per_day=D("0.001"),
                 schedule=_rule(payment=D("20000")))
    jan = roll_deals(_book(deal), date(2026, 1, 1), D(0), max_months=1)
    feb = roll_deals(_book(deal), date(2026, 2, 1), D(0), max_months=1)
    # Платёж 20-го числа сдвигает остаток на день начисления: январь —
    # 19×100 + 12×80 = 2860. Февральский прокат стартует с канона на конец
    # января, а в книге движений нет (январский платёж — дело самого проката),
    # поэтому база — 100 000 + 31×100 = 103 100, и февраль —
    # 19×103,10 + 9×83,10 = 2706,80: январь всё так дороже февраля.
    assert jan.months[0].interest == D("2860.00")
    assert feb.months[0].interest == D("2706.80")


def test_annual_rate_normalizes_daily_debt():
    """Дневная ставка приводится к годовой — иначе лавина ранжирует неверно."""
    book = _book(
        _deal("дневной", amount=D("10000"), rate_per_day=D("0.005"),
              schedule=_rule(payment=D("1000"))),
        _deal("годовой", amount=D("10000"), rate_per_year=D("0.4"),
              schedule=_rule(payment=D("1000"))),
    )
    roll = roll_deals(book, START, D("5000"))
    # Дневная 0.005/день = 1.825/год — дороже годовых 0.4.
    # Лавина гасит дневную ставку первой: её остаток падает быстрее.
    assert roll.months[0].balances["дневной"] < roll.months[0].balances["годовой"]


def test_payment_below_interest_never_pays_off():
    book = _book(_deal("долг", amount=D("100000"), rate_per_day=D("0.0052"),
                       schedule=_rule(payment=D("400"))))
    roll = roll_deals(book, START, D(0), max_months=24)
    assert roll.freedom is None and roll.stalled
    assert roll.months[-1].total > D("100000")


def test_snapshots_advance_by_month():
    book = _book(_deal("долг", amount=D("3000"),
                       schedule=_rule(payment=D("1000"))))
    roll = roll_deals(book, START, D(0))
    assert [m.month for m in roll.months] == [
        date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]
    assert roll.freedom == date(2026, 3, 1)


# --- стратегии и бюджет ---------------------------------------------------

def _two_deals() -> list[Deal]:
    return [_deal("дорогой", amount=D("100000"), rate_per_year=D("0.4"),
                  schedule=_rule(payment=D("3000"))),
            _deal("дешёвый", amount=D("40000"), rate_per_year=D("0.1"),
                  schedule=_rule(payment=D("3000")))]


def test_freed_minimum_stays_in_the_budget():
    """Закрылся долг — его платёж не исчезает, а идёт в дело: простоя нет."""
    book = _book(
        _deal("малый", amount=D("1000"), schedule=_rule(payment=D("1000"))),
        _deal("большой", amount=D("50000"), rate_per_year=D("0.3"),
              schedule=_rule(payment=D("5000"))),
    )
    roll = roll_deals(book, START, D(0))
    # Пока есть открытый долг — свободные деньги не простаивают:
    # освободившийся платёж малого идёт в досрочку большого. Всё, что не
    # заплачено по графику (short), уходит досрочкой (prepaid) — тот же
    # смысл, что у прежнего «остаток пула (DealMonth.free) равен нулю»,
    # снесённого вместе с остатком бюджета месяца.
    assert all(m.prepaid == m.short for m in roll.months if m.total > 0)


def test_budget_never_exceeds_minimums_plus_extra():
    """За месяц нельзя заплатить больше, чем минималки + свободные деньги."""
    book = _book(
        _deal("a", amount=D("100000"), schedule=_rule(payment=D("1000"))),
        _deal("b", amount=D("100000"), schedule=_rule(payment=D("2000"))),
    )
    roll = roll_deals(book, START, D("500"))
    assert all(m.paid <= D("3500") for m in roll.months)


def test_prepay_false_leaves_money_idle():
    """Запрет досрочки заставляет свободные деньги простаивать."""
    book_locked = _book(
        _deal("быстрый", amount=D("10000"), schedule=_rule(payment=D("1000"))),
        _deal("защищённый", amount=D("300000"), rate_per_year=D("0.353"),
              schedule=_rule(payment=D("16700")), prepay=False),
    )
    book_free = _book(
        _deal("быстрый", amount=D("10000"), schedule=_rule(payment=D("1000"))),
        _deal("защищённый", amount=D("300000"), rate_per_year=D("0.353"),
              schedule=_rule(payment=D("16700"))),
    )
    locked = roll_deals(book_locked, START, D("60000"))
    free = roll_deals(book_free, START, D("60000"))
    # Деньги, которые запрет не пустил в досрочку, в дело не идут: с месяца 2
    # (разрешённый «быстрый» закрыт) запрещённый прокат не досрочит ничего,
    # хотя бюджет — 60 000 в месяц. В месяце 1 досрочка уходит в разрешённую
    # сделку — это не нарушение запрета.
    assert all(m.prepaid == 0 for m in locked.months[1:])
    assert (sum(m.prepaid for m in locked.months)
            < sum(m.prepaid for m in free.months))
    assert len(locked.months) > len(free.months)
    assert locked.total_interest > free.total_interest


def test_second_priority_is_not_serviced():
    book = _book(
        _deal("график", amount=D("10000"), schedule=_rule(payment=D("10000"))),
        _deal("взыскание", amount=D("54331"), rate_per_year=D("0.1"),
              second_priority=True),
    )
    roll = roll_deals(book, START, D("0"))
    assert roll.start_total == D("10000")
    # Второй приоритет не платится из свободных денег:
    # по взысканию нет ни одного платежа.
    assert all(p.deal != "взыскание" for m in roll.months for p in m.payments)
    # Пока ждёт, взыскание растёт по ставке.
    assert roll.months[-1].balances["взыскание"] > D("54331")


def test_compare_strategies_runs_the_same_portfolio():
    rows = compare_deal_strategies(_book(*_two_deals()), START, D("20000"))
    assert [name for name, _ in rows] == ["avalanche", "snowball"]
    assert all(r.start_total == D("140000") for _, r in rows)


# --- начисления и закрытость (04) --------------------------------------------

def test_a_charge_into_body_counts_into_closure():
    """Начисление в тело растит тело: закрытость его видит (правка 3)."""
    deal = _deal("заём", amount=D("10000"), schedule=_rule(payment=D("10000")),
                 allocations=(AllocationRule(PARTS),))
    paid = [Movement(date(2026, 1, 20), D("10000"), "заём")]
    plain = _book(deal, movements=paid)
    assert _principal_left(plain, "заём", date(2026, 1, 31)) == D("0")
    grown = _book(deal, movements=paid,
                  charges=[Charge("дозаем", date(2026, 1, 1), D("500"),
                                  "заём", BODY)])
    assert _principal_left(grown, "заём", date(2026, 1, 31)) == D("500")
    penalized = _book(deal, movements=paid,
                      charges=[Charge("неустойка", date(2026, 1, 1), D("500"),
                                      "заём", PENALTY)])
    assert _principal_left(penalized, "заём", date(2026, 1, 31)) == D("0")


def test_a_paid_off_debt_has_no_overdue_even_with_a_live_schedule():
    """Погашенный долг просрочки не держит: график не закрыт, а сумма — ноль."""
    book = _book(_deal("заём", amount=D("2000"),
                       schedule=_rule(days=(10,), payment=D("500"))),
                 movements=[Movement(date(2026, 1, 5), D("2000"), "заём")])
    assert overdue_amount(book, "заём", date(2026, 1, 11)) == D("0")


# --- формула неустойки (06) --------------------------------------------------

def _penalty_rule(*steps, cap=None) -> PenaltyRule:
    """Правило неустойки: ступени и потолок; без явного потолка — «без потолка»."""
    return PenaltyRule(steps=tuple(steps),
                       cap=cap if cap is not None else PenaltyCap(CAP_NONE))


DUE = date(2026, 1, 10)
STREAM = [(DUE, D("1000"), D("1000"))]


def test_penalty_accrues_by_days_from_the_day_after_the_due():
    """Замер: ставка ступени × база дня; первый день — после срока, последний —
    дата расчёта включительно; пустой отрезок и пустые потоки — ноль."""
    rule = _penalty_rule(PenaltyStep(0, D("0.001")))
    assert accrued_penalty(rule, STREAM, DUE, DUE) == D("0")
    first = DUE + timedelta(days=1)
    assert accrued_penalty(rule, STREAM, first, first) == D("1.00")
    assert accrued_penalty(rule, STREAM, first, date(2026, 1, 15)) == D("5.00")
    assert accrued_penalty(rule, STREAM, date(2026, 1, 20), DUE) == D("0")
    assert accrued_penalty(rule, (), first, date(2026, 1, 15)) == D("0")


def test_penalty_step_applies_from_its_own_day_inclusively():
    """Возраст ровно N — ступень «от N»: до порога одна ставка, с порога другая."""
    rule = _penalty_rule(PenaltyStep(0, D("0.001")), PenaltyStep(3, D("0.002")))
    # 11–12 января — возраст 1–2, по 1,00; 13–15 — возраст 3–5, по 2,00.
    assert accrued_penalty(rule, STREAM, date(2026, 1, 11),
                           date(2026, 1, 15)) == D("8.00")


def test_penalty_day_of_payment_reduces_base_before_that_days_accrual():
    """День платежа уменьшает базу до начисления за этот день — перенос стоит денег."""
    rule = _penalty_rule(PenaltyStep(0, D("0.001")))
    # 11–12 января по 1,00; 13-го погашено 400 — база 600, далее три дня по 0,60.
    assert accrued_penalty(rule, STREAM, date(2026, 1, 11), date(2026, 1, 15),
                           payments=[(date(2026, 1, 13), D("400"))]) == D("3.80")


def test_penalty_payments_settle_the_oldest_stream_first():
    """Платёж гасит самые старые просрочки первыми (Q13)."""
    rule = _penalty_rule(PenaltyStep(0, D("0.001")))
    streams = [(date(2026, 1, 1), D("500"), D("500")),
               (DUE, D("500"), D("500"))]
    # 11–14 января по 1,00; 15-го гасится старый поток целиком, у нового 400 → 0,40.
    assert accrued_penalty(rule, streams, date(2026, 1, 11), date(2026, 1, 15),
                           payments=[(date(2026, 1, 15), D("600"))]) == D("4.40")


def test_penalty_cap_sum_cuts_growth_and_keeps_it_monotone():
    """Потолок-сумма обрезает прирост: набранное не растёт и не списывается."""
    rule = _penalty_rule(PenaltyStep(0, D("0.001")),
                         cap=PenaltyCap(CAP_SUM, D("3")))
    assert accrued_penalty(rule, STREAM, date(2026, 1, 11),
                           date(2026, 1, 15)) == D("3.00")
    assert accrued_penalty(rule, STREAM, date(2026, 1, 11),
                           date(2026, 2, 28)) == D("3.00")
    assert accrued_penalty(rule, STREAM, date(2026, 1, 11), date(2026, 1, 20),
                           payments=[(date(2026, 1, 16), D("900"))]) == D("3.00")


def test_penalty_cap_share_takes_original_overdue_amounts():
    """Доля считается от исходных просроченных сумм (Q36), не от остатков."""
    rule = _penalty_rule(PenaltyStep(0, D("0.001")),
                         cap=PenaltyCap(CAP_SHARE, D("0.001")))
    streams = [(date(2026, 1, 1), D("500"), D("500")), (DUE, D("1000"), D("300"))]
    # Потолок 0,001 × 1500 = 1,50; два дня по 0,80 дают 1,60 — обрезано до 1,50.
    assert accrued_penalty(rule, streams, date(2026, 1, 11),
                           date(2026, 1, 20)) == D("1.50")


def test_penalty_cap_is_rounded_like_every_accrual():
    """Потолок округлён тем же kopek HALF_UP: 3,005 → 3,01 — второго пути нет."""
    rule = _penalty_rule(PenaltyStep(0, D("0.001")),
                         cap=PenaltyCap(CAP_SUM, D("3.005")))
    assert accrued_penalty(rule, STREAM, date(2026, 1, 11),
                           date(2026, 1, 20)) == D("3.01")


def test_penalty_zero_rate_accrues_nothing_and_already_keeps_the_cap():
    """Нулевая ставка не начисляет; набранное до отрезка входит в потолок."""
    rule = _penalty_rule(PenaltyStep(0, D("0")))
    assert accrued_penalty(rule, STREAM, date(2026, 1, 11),
                           date(2026, 1, 15)) == D("0")
    capped = _penalty_rule(PenaltyStep(0, D("0.001")),
                           cap=PenaltyCap(CAP_SUM, D("3")))
    assert accrued_penalty(capped, STREAM, date(2026, 1, 11), date(2026, 1, 15),
                           already=D("2.50")) == D("0.50")


# --- what-if «тянуть до даты» (13) --------------------------------------------

def _growth_deal(**kw) -> Deal:
    """Сделка what-if: платёж десятого числа без сдвига выходных, раскладка есть."""
    base = dict(schedule=_rule(days=(10,), payment=D("300"), shift_weekend=False),
                allocations=(AllocationRule(PARTS),))
    base.update(kw)
    return _deal(**base)


def test_growth_debt_without_payments_matches_manual_formula():
    """Приёмка 13-1: долг без платежей «набежал» к дате — разбивка по частям
    и дельта за период сходятся с ручным замером формулы; начисления-факты
    (доза в тело, издержки) входят своими датами, дельта частей сходится
    с дельтой канона."""
    penalty = _penalty_rule(PenaltyStep(0, D("0.001")), PenaltyStep(15, D("0.002")))
    deal = _growth_deal(rate_per_year=D("0.24"), penalties=(penalty,))
    book = _book(deal, charges=[Charge("дозаем", date(2026, 1, 20), D("100"),
                                       "заём", BODY),
                                Charge("издержки", date(2026, 2, 1), D("40"),
                                       "заём", COSTS)])
    since, until = date(2026, 1, 15), date(2026, 2, 20)
    # Ручной замер: проценты — база 1000, доза 20-го входит в базу того же
    # месяца (январь: 1100 × 0.02 = 22.00; февраль: 1122 × 0.02 × 20/28 =
    # 16.03); на 15-е января — 1000 × 0.02 × 15/31 = 9.68.
    assert accrued_interest(deal, D("1000"), START, until, [],
                            [(date(2026, 1, 20), D("100"))]) == D("38.03")
    assert accrued_interest(deal, D("1000"), START, since, [], []) == D("9.68")
    # Неустойка: два потока по 300 со своих сроков (долг без неустойки больше
    # 600 — обрезки нет); 11–24 января по 0.30, дальше 0.60, второй поток
    # с 11-го февраля по 0.30: 14×0.30 + 27×0.60 + 10×0.30 = 23.40; на 15-е
    # января — 5 × 0.30 = 1.50.
    streams = [(date(2026, 1, 10), D("300"), D("300")),
               (date(2026, 2, 10), D("300"), D("300"))]
    assert accrued_penalty(penalty, streams, date(2026, 1, 11), until) == D("23.40")
    assert accrued_penalty(penalty, streams[:1], date(2026, 1, 11),
                           since) == D("1.50")
    growth = deal_growth(book, "заём", since, until)
    assert growth.on == until
    assert growth.parts == {BODY: D("1100"), INTEREST: D("38.03"),
                            PENALTY: D("23.40"), COSTS: D("40")}
    assert growth.grown == {BODY: D("100"), INTEREST: D("28.35"),
                            PENALTY: D("21.90"), COSTS: D("40")}
    # Дельта частей сходится с дельтой канона: инвариант «канон = сумма
    # частей» держится и на приросте (Q34 — раскладка известна).
    assert (sum(growth.grown.values())
            == deal_balance(book, "заём", until) - deal_balance(book, "заём", since))


def test_growth_streams_go_overdue_on_their_own_due_with_own_steps():
    """Приёмка 13-2: каждое вхождение просрочивается со своего срока, ступени
    применяются по возрасту своей просрочки, а не первой."""
    deal = _growth_deal(
        schedule=_rule(days=(5, 20), payment=D("250"), shift_weekend=False),
        penalties=(_penalty_rule(PenaltyStep(0, D("0.001")),
                                 PenaltyStep(10, D("0.003"))),))
    book = _book(deal)
    # Первая просрочка: срок 05-го, к 08-му — три дня по 0.25; 09–14 января —
    # возраст 4–9, шесть дней по 0.25. Вторая (срок 20-го) ещё не просрочена.
    assert deal_growth(book, "заём", date(2026, 1, 8),
                       date(2026, 1, 14)).grown[PENALTY] == D("1.50")
    # К 30-му: первая — 01-06..01-30 (9 × 0.25 + 16 × 0.75), вторая —
    # 01-21..01-30 (9 × 0.25 + 1 × 0.75): её ступень 0.003 — с 30-го,
    # возраст 10 включительно, по своему сроку.
    assert deal_growth(book, "заём", date(2026, 1, 8),
                       date(2026, 1, 30)).grown[PENALTY] == D("16.50")
    # Три просрочки к 18-му февраля, каждая растёт по своей шкале:
    # 28.50 + 17.25 + 5.25 = 51.00; дельта от 0.75 на 08-е.
    growth = deal_growth(book, "заём", date(2026, 1, 8), date(2026, 2, 18))
    assert growth.parts[PENALTY] == D("51.00")
    assert growth.grown[PENALTY] == D("50.25")


def test_growth_keeps_partial_payments_made_before_since():
    """Приёмка 13-3: частичные платежи до `since` учтены — база растёт только
    от неуплаченного; платёж после срока исходную просроченную сумму не меняет
    (Q36), база дня — текущий остаток потока."""
    penalty = _penalty_rule(PenaltyStep(0, D("0.001")))
    deal = _growth_deal(rate_per_year=D("0.24"), penalties=(penalty,))
    book = _book(deal, movements=[Movement(date(2026, 1, 15), D("150"), "заём",
                                           occurrence=date(2026, 1, 10))])
    # Неустойка: до платежа (11–14 января) база дня — 300, после (15-е и
    # позже) — 150: 4 × 0.30 + 37 × 0.15; второй поток целиком 10 × 0.30.
    assert accrued_penalty(penalty, [(date(2026, 1, 10), D("300"), D("300"))],
                           date(2026, 1, 11), date(2026, 1, 14)) == D("1.20")
    assert accrued_penalty(penalty, [(date(2026, 1, 10), D("300"), D("150"))],
                           date(2026, 1, 15), date(2026, 2, 20)) == D("5.55")
    assert accrued_penalty(penalty, [(date(2026, 2, 10), D("300"), D("300"))],
                           date(2026, 2, 11), date(2026, 2, 20)) == D("3.00")
    # Проценты: платёж 15-го уменьшает остаток со своего дня; годовая — платёж
    # месяца начисление января не меняет (20 дней января включительно к 20-му),
    # февраль — от 870 за 20/28.
    assert accrued_interest(deal, D("1000"), START, date(2026, 1, 20),
                            [(date(2026, 1, 15), D("150"))]) == D("12.90")
    assert accrued_interest(deal, D("1000"), START, date(2026, 2, 20),
                            [(date(2026, 1, 15), D("150"))]) == D("32.43")
    growth = deal_growth(book, "заём", date(2026, 1, 20), date(2026, 2, 20))
    assert growth.parts == {BODY: D("850"), INTEREST: D("32.43"),
                            PENALTY: D("9.75"), COSTS: D("0")}
    assert growth.grown == {BODY: D("0"), INTEREST: D("19.53"),
                            PENALTY: D("7.65"), COSTS: D("0")}


def test_growth_drops_payments_of_the_segment_and_keeps_the_state_at_since():
    """13-Т2: платежи отрезка (since, until] в what-if не платятся — вхождение
    просрочивается и капает, как без платежа; движение ровно на `since` —
    часть состояния: проекция режет книгу по `since` включительно."""
    deal = _growth_deal(penalties=(_penalty_rule(PenaltyStep(0, D("0.001"))),))
    paid = Movement(date(2026, 2, 15), D("300"), "заём",
                    occurrence=date(2026, 2, 10))
    book = _book(deal, movements=[paid])
    since, until = date(2026, 1, 20), date(2026, 2, 20)
    growth = deal_growth(book, "заём", since, until)
    # Платёж 15-го февраля закрыл бы второй поток — в what-if его нет:
    # раскладка равна книге без этого движения, тело не тает, второй поток
    # капает все десять дней (02-11..02-20 по 0.30).
    assert growth.parts == deal_parts(_book(deal), "заём", until)
    assert growth.parts == {BODY: D("1000"), INTEREST: D("0"),
                            PENALTY: D("15.30"), COSTS: D("0")}
    assert growth.grown[PENALTY] == D("12.30")   # в том числе 3.00 второго потока
    on_since = _book(deal, movements=[Movement(date(2026, 1, 20), D("150"),
                                               "заём", occurrence=date(2026, 1, 10))])
    kept = deal_growth(on_since, "заём", date(2026, 1, 20), until)
    assert kept.parts == deal_parts(on_since, "заём", until)
    assert kept.parts[BODY] == D("850")


def test_growth_without_payments_is_deal_parts_of_the_same_functions():
    """Приёмка 13-4: прямой ассертир «те же функции» — без платежей what-if
    на дату равен `deal_parts` на ту же дату; дельта — разность частей."""
    deal = _growth_deal(rate_per_year=D("0.24"),
                        penalties=(_penalty_rule(PenaltyStep(0, D("0.001"))),))
    book = _book(deal, charges=[Charge("издержки", date(2026, 1, 25), D("40"),
                                       "заём", COSTS)])
    for day in (START, date(2026, 1, 20), date(2026, 2, 20)):
        grown = deal_growth(book, "заём", day, day)
        assert grown.parts == deal_parts(book, "заём", day)
        assert grown.grown == {part: D(0) for part in PARTS}
    since, until = date(2026, 1, 10), date(2026, 2, 20)
    forward = deal_growth(book, "заём", since, until)
    assert forward.parts == deal_parts(book, "заём", until)
    before = deal_parts(book, "заём", since)
    assert forward.grown == {part: forward.parts[part] - before[part]
                             for part in PARTS}


def test_growth_trigger_fires_on_its_condition_date_into_its_part():
    """Приёмка 13-5: штраф триггера срабатывает в дату условия (R5-Q29: «срок
    прошёл» — дата срока) и попадает в grown своей частью; неустойка не
    смешана — свой поток и своя часть."""
    triggers = (TriggerRule(uid="триг", condition=TRIGGER_OVERDUE, part=COSTS,
                            basis="не уплатил в срок", charge_amount=D("50")),)
    deal = _growth_deal(penalties=(_penalty_rule(PenaltyStep(0, D("0.001"))),),
                        triggers=triggers)
    book = _book(deal)
    before = deal_growth(book, "заём", date(2026, 1, 5), date(2026, 1, 9))
    assert before.parts[COSTS] == D("0") and before.grown[COSTS] == D("0")
    on_due = deal_growth(book, "заём", date(2026, 1, 5), date(2026, 1, 10))
    assert on_due.parts[COSTS] == D("50") and on_due.grown[COSTS] == D("50")
    assert on_due.grown[PENALTY] == D("0")    # неустойка — с 11-го, штраф — в издержках
    after = deal_growth(book, "заём", date(2026, 1, 5), date(2026, 1, 12))
    assert after.grown[COSTS] == D("50")      # штраф одноразовый
    assert after.grown[PENALTY] == D("0.60")  # 11–12 января по 0.30


def test_growth_edges_zero_period_and_since_before_all_facts():
    """Края 13-Т3: пустой период — нули и разбивка на `since`; `since` раньше
    всех фактов — канон на него равен телу версий, рост идёт от него."""
    deal = _growth_deal(amount=D("600"),
                        penalties=(_penalty_rule(PenaltyStep(0, D("0.001"))),))
    book = _book(deal)
    zero = deal_growth(book, "заём", date(2026, 2, 1), date(2026, 1, 20))
    assert zero.on == date(2026, 1, 20)
    assert zero.grown == {part: D(0) for part in PARTS}
    assert zero.parts == deal_parts(book, "заём", date(2026, 2, 1))
    before_all = date(2025, 12, 1)
    assert deal_parts(book, "заём", before_all) == {
        BODY: D("600"), INTEREST: D("0"), PENALTY: D("0"), COSTS: D("0")}
    growth = deal_growth(book, "заём", before_all, date(2026, 1, 20))
    # Единственный рост — неустойка первого потока: 11–20 января по 0.30.
    assert growth.grown == {BODY: D("0"), INTEREST: D("0"),
                            PENALTY: D("3.00"), COSTS: D("0")}


def test_growth_without_allocation_is_a_gap_while_the_canon_answers():
    """Край Q34: раскладки нет — обе разбивки `None`; сумма и «набежало»
    отвечает канон прежнего пути (производная неустойка в него не входит —
    прогноз неполный, это и есть пробел). Пустой период и без раскладки
    отвечает нулями: «набежать за ноль дней» известно (13-Т3)."""
    deal = _growth_deal(allocations=(),
                        penalties=(_penalty_rule(PenaltyStep(0, D("0.001"))),))
    book = _book(deal)
    growth = deal_growth(book, "заём", date(2026, 1, 5), date(2026, 1, 20))
    assert growth.parts is None and growth.grown is None
    assert deal_parts(book, "заём", date(2026, 1, 20)) is None
    assert overdue_amount(book, "заём", date(2026, 1, 20)) == D("300")
    assert deal_balance(book, "заём", date(2026, 1, 5)) == D("1000")
    assert deal_balance(book, "заём", date(2026, 1, 20)) == D("1000")
    zero = deal_growth(book, "заём", date(2026, 1, 20), date(2026, 1, 5))
    assert zero.grown == {part: D(0) for part in PARTS}


def test_growth_of_regular_expense_and_claim_grows_nothing():
    """Край: у регулярного расхода и требования растить нечего — обе разбивки
    `None` (Q23)."""
    expense = _growth_deal(amount=None, uid="аренда", title="Аренда")
    claim = _growth_deal(direction=OWED_TO_ME, uid="требование",
                         title="Требование")
    book = _book(expense, claim)
    for uid in ("аренда", "требование"):
        grown = deal_growth(book, uid, date(2026, 1, 5), date(2026, 2, 20))
        assert grown.parts is None and grown.grown is None


def test_growth_caps_penalty_like_history_and_no_scale_accrues_nothing():
    """Края 13-Т3: потолок обрезает накопленную неустойку, как в истории;
    шкалы нет — неустойка не капает (пробел 12-Т3), хотя просрочка жива."""
    capped = _growth_deal(
        penalties=(_penalty_rule(PenaltyStep(0, D("0.001")),
                                 cap=PenaltyCap(CAP_SUM, D("2"))),))
    book = _book(capped)
    # 11–15 января — 5 × 0.30, потолок ещё не достигнут; к 20-му набежало бы
    # 3.00 — обрезано до 2.00, как в истории (06).
    assert deal_growth(book, "заём", date(2026, 1, 5),
                       date(2026, 1, 15)).grown[PENALTY] == D("1.50")
    assert deal_growth(book, "заём", date(2026, 1, 5),
                       date(2026, 1, 20)).grown[PENALTY] == D("2.00")
    bare = _growth_deal()
    plain = _book(bare)
    assert overdue_amount(plain, "заём", date(2026, 1, 20)) == D("300")
    grown = deal_growth(plain, "заём", date(2026, 1, 5), date(2026, 1, 20))
    assert grown.parts[PENALTY] == D("0") and grown.grown[PENALTY] == D("0")
