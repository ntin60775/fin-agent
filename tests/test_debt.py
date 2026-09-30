"""Тесты долговой стороны — синтетические фикстуры, без личных данных."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_CEILING
from decimal import Decimal as D

from finance_core import (BODY, CAP_NONE, CAP_SHARE, CAP_SUM, LEGAL, PARTS,
                          PENALTY, AllocationRule, Charge, Counterparty, Deal,
                          Movement, PenaltyCap, PenaltyRule, PenaltyStep,
                          ScheduleRule, Settlements, Wallet,
                          accrued_penalty, compare_deal_strategies,
                          overdue_amount, roll_deals)
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
