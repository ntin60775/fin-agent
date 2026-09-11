"""Тесты долговой стороны — синтетические фикстуры, без личных данных."""
from __future__ import annotations

from datetime import date
from decimal import ROUND_CEILING
from decimal import Decimal as D

import pytest

from finance_core import Debt, compare_strategies, roll_forward

START = date(2026, 1, 1)


# --- амортизация ----------------------------------------------------------

def test_interest_free_payoff_is_ceiling_of_division():
    plan = roll_forward([Debt("рассрочка", D("10000"), payment=D("3000"))], START, D(0))
    assert plan.months == 4              # ceil(10 000 / 3 000)
    assert plan.total_interest == D("0")


def test_revolving_payoff_matches_annuity_formula():
    """Срок совпадает с формулой аннуитета — независимая проверка движка."""
    balance, payment, rate = D("120000"), D("5000"), D("0.24")
    plan = roll_forward([Debt("карта", balance, rate_per_year=rate, payment=payment)],
                        START, D(0))
    r = rate / 12
    n = (-(1 - r * balance / payment).ln() / (1 + r).ln()).to_integral_value(
        rounding=ROUND_CEILING)
    assert plan.months == n


def test_daily_rate_uses_calendar_days():
    """Дневная ставка умножается на число дней месяца: январь дороже февраля."""
    debt = Debt("мфо", D("100000"), rate_per_day=D("0.001"), payment=D("20000"))
    jan = roll_forward([debt], date(2026, 1, 1), D(0), max_months=1)
    feb = roll_forward([debt], date(2026, 2, 1), D(0), max_months=1)
    assert jan.snapshots[0].interest == D("3100.00")
    assert feb.snapshots[0].interest == D("2800.00")


def test_annual_rate_normalizes_daily_debt():
    """Дневная ставка приводится к годовой — иначе лавина ранжирует неверно."""
    assert Debt("мфо", D("1"), rate_per_day=D("0.005")).annual_rate == D("0.005") * 365
    assert Debt("карта", D("1"), rate_per_year=D("0.4")).annual_rate == D("0.4")
    assert Debt("рассрочка", D("1")).annual_rate == D("0")


def test_payment_below_interest_never_pays_off():
    plan = roll_forward([Debt("долг", D("100000"), rate_per_day=D("0.0052"),
                              payment=D("400"))], START, D(0), max_months=24)
    assert plan.freedom is None and plan.stalled
    assert plan.snapshots[-1].total > D("100000")


def test_snapshots_advance_by_month():
    plan = roll_forward([Debt("долг", D("3000"), payment=D("1000"))], START, D(0))
    assert [s.date for s in plan.snapshots] == [
        date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]
    assert plan.freedom == date(2026, 3, 1)


# --- стратегии и бюджет ---------------------------------------------------

def _two_debts() -> list[Debt]:
    return [Debt("дорогой", D("100000"), rate_per_year=D("0.4"), payment=D("3000")),
            Debt("дешёвый", D("40000"), rate_per_year=D("0.1"), payment=D("3000"))]


def test_avalanche_cheaper_than_snowball():
    """Дорогие первыми — дешевле; мелкие первыми — быстрее по числу закрытий."""
    avalanche = roll_forward(_two_debts(), START, D("20000"))
    snowball = roll_forward(_two_debts(), START, D("20000"), strategy="snowball")
    assert avalanche.total_interest < snowball.total_interest


def test_unknown_strategy_is_rejected():
    with pytest.raises(ValueError):
        roll_forward(_two_debts(), START, D("0"), strategy="как-нибудь")


def test_freed_minimum_stays_in_the_budget():
    """Закрылся долг — его платёж не исчезает, а идёт в дело: простоя нет."""
    debts = [Debt("малый", D("1000"), payment=D("1000")),
             Debt("большой", D("50000"), rate_per_year=D("0.3"), payment=D("5000"))]
    assert roll_forward(debts, START, D(0)).idle == D("0")


def test_budget_never_exceeds_minimums_plus_extra():
    """За месяц нельзя заплатить больше, чем минималки + свободные деньги."""
    debts = [Debt("a", D("100000"), payment=D("1000")),
             Debt("b", D("100000"), payment=D("2000"))]
    plan = roll_forward(debts, START, D("500"))
    assert all(s.paid <= D("3500") for s in plan.snapshots)


def test_prepay_false_leaves_money_idle():
    """Запрет досрочки заставляет свободные деньги простаивать."""
    debts = [Debt("быстрый", D("10000"), payment=D("1000")),
             Debt("защищённый", D("300000"), rate_per_year=D("0.353"),
                  payment=D("16700"), prepay=False)]
    plan = roll_forward(debts, START, D("60000"))
    free = roll_forward([Debt("быстрый", D("10000"), payment=D("1000")),
                         Debt("защищённый", D("300000"), rate_per_year=D("0.353"),
                              payment=D("16700"))], START, D("60000"))
    assert plan.idle > 0
    assert plan.months > free.months
    assert plan.total_interest > free.total_interest


def test_second_priority_is_not_serviced():
    debts = [Debt("график", D("10000"), payment=D("10000")),
             Debt("взыскание", D("54331"), second_priority=True)]
    plan = roll_forward(debts, START, D(0))
    assert plan.start_total == D("10000")
    assert plan.snapshots[-1].balances["взыскание"] == D("54331")


def test_compare_strategies_runs_the_same_portfolio():
    rows = compare_strategies(_two_debts(), START, D("20000"))
    assert [name for name, _ in rows] == ["avalanche", "snowball"]
    assert all(p.start_total == D("140000") for _, p in rows)
