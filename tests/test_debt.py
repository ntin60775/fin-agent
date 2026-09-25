"""Тесты долговой стороны — синтетические фикстуры, без личных данных."""
from __future__ import annotations

from datetime import date
from decimal import ROUND_CEILING
from decimal import Decimal as D

from finance_core import (LEGAL, Counterparty, Deal, ScheduleRule,
                          Settlements, Wallet, compare_deal_strategies,
                          roll_deals)

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


def _book(*deals, counterparties=(), wallets=None, movements=(), edits=()) -> Settlements:
    if wallets is None:
        wallets = [Wallet("карта", "Карта", "карта", D("0"))]
    return Settlements(
        counterparties=[_counterparty(), *counterparties],
        wallets=list(wallets),
        deals=list(deals),
        movements=list(movements),
        edits=list(edits),
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
