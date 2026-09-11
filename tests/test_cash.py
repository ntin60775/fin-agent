"""Тесты кассовой стороны — синтетические фикстуры, без личных данных."""
from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal as D

import pytest

from finance_core import (Account, Income, Payment, Scenario, Transfer,
                          compare, cover_cost, optional_cap, run)


# --- каскад ---------------------------------------------------------------

def test_events_apply_in_date_order():
    """События применяются по дате, остаток ведётся по каждому счёту."""
    s = Scenario(
        accounts=[Account("main", D("1000"))],
        income=[Income(date(2026, 3, 20), D("500"), "main")],
        payments=[Payment(date(2026, 3, 5), D("200"), "a", "main"),
                  Payment(date(2026, 3, 25), D("300"), "b", "main")],
    )
    r = run(s, main="main")
    assert [(x.date, x.balance_after) for x in r.timeline] == [
        (date(2026, 3, 5), D("800")),
        (date(2026, 3, 20), D("1300")),
        (date(2026, 3, 25), D("1000")),
    ]
    assert r.end_balance == D("1000")


def test_timeline_never_goes_back_in_time():
    s = Scenario(
        accounts=[Account("main", D("1000")), Account("other", D("0"))],
        income=[Income(date(2026, 3, 20), D("500"), "main")],
        payments=[Payment(date(2026, 3, 5), D("200"), "a", "main")],
        transfers=[Transfer(date(2026, 3, 6), D("100"), "main", "other")],
    )
    dates = [x.date for x in run(s, main="main").timeline]
    assert dates == sorted(dates)


def test_unknown_account_gives_clear_error():
    """Опечатка в имени счёта — понятная ошибка, а не KeyError."""
    s = Scenario(accounts=[Account("main", D("100"))],
                 payments=[Payment(date(2026, 1, 1), D("10"), "x", "typo")])
    with pytest.raises(ValueError, match="typo"):
        run(s, main="main")
    with pytest.raises(ValueError, match="основной счёт"):
        run(Scenario(accounts=[Account("main", D("100"))]), main="нет-такого")


def test_payment_hits_its_funding_account():
    """Платёж списывается со своего счёта, а не с основного.

    Это регресс на класс ошибок «какой картой платим»: если платёж уходит
    не со своего счёта, результат меняется.
    """
    s = Scenario(
        accounts=[Account("main", D("1000")), Account("card", D("500"))],
        payments=[Payment(date(2026, 1, 10), D("300"), "x", "card")],
    )
    r = run(s, main="main")
    assert r.balances == {"main": D("1000"), "card": D("200")}
    assert r.end_balance == D("1000")

    wrong = dataclasses.replace(
        s, payments=[dataclasses.replace(p, account="main") for p in s.payments])
    assert run(wrong, main="main").end_balance == D("700")


def test_transfer_moves_money_without_changing_total():
    s = Scenario(
        accounts=[Account("a", D("100")), Account("b", D("0"))],
        transfers=[Transfer(date(2026, 1, 5), D("60"), "a", "b")],
    )
    r = run(s, main="a")
    assert r.balances == {"a": D("40"), "b": D("60")}
    assert r.total == D("100")


def test_hole_is_lowest_point_of_main_account():
    s = Scenario(
        accounts=[Account("main", D("1000"))],
        income=[Income(date(2026, 1, 25), D("500"), "main")],
        payments=[Payment(date(2026, 1, 10), D("1200"), "x", "main"),
                  Payment(date(2026, 1, 20), D("100"), "y", "main")],
    )
    r = run(s, main="main")
    assert r.hole == D("-300")
    assert r.min_date == date(2026, 1, 20)
    assert r.end_balance == D("200")


def test_hole_is_zero_when_balance_never_goes_negative():
    s = Scenario(accounts=[Account("main", D("500"))],
                 payments=[Payment(date(2026, 1, 10), D("100"), "x", "main")])
    r = run(s, main="main")
    assert r.hole == D("0")
    assert r.min_balance == D("400")


# --- прожиточный минимум --------------------------------------------------

def _floor_case(**kw) -> Scenario:
    base = dict(
        accounts=[Account("main", D("10000"))],
        income=[Income(date(2026, 1, 11), D("5000"), "main")],
        payments=[Payment(date(2026, 1, 1), D("9000"), "x", "main")],
        living_floor_monthly=D("30000"),
    )
    base.update(kw)
    return Scenario(**base)


def test_floor_gap_uses_days_to_next_income():
    """Нехватка = прожиточный минимум × дней до прихода / 30 − остаток."""
    r = run(_floor_case(), main="main")
    assert r.floor_gap == D("9000.00")      # 30 000 × 10/30 − 1 000
    assert r.floor_gap_date == date(2026, 1, 1)


def test_floor_gap_none_when_floor_unknown():
    """Минимум `?` → «не оценено», а не ноль."""
    r = run(_floor_case(living_floor_monthly=None), main="main")
    assert r.floor_gap is None
    assert r.hole == D("0")                 # разрыв при этом считается


def test_floor_gap_none_without_income_ahead():
    """Нет прихода впереди и нет горизонта → «не оценено», а не «нехватки нет»."""
    s = Scenario(accounts=[Account("main", D("1000"))],
                 payments=[Payment(date(2026, 1, 1), D("100"), "x", "main")],
                 living_floor_monthly=D("30000"))
    assert run(s, main="main").floor_gap is None

    s.income_horizon = date(2026, 2, 1)
    assert run(s, main="main").floor_gap == D("30100.00")   # 30 000 × 31/30 − 900


def test_floor_gap_zero_when_covered():
    """Оценено и покрыто — это ноль, а не «не оценено»."""
    r = run(_floor_case(accounts=[Account("main", D("100000"))]), main="main")
    assert r.floor_gap == D("0")
    assert r.floor_gap_date is None


def test_floor_gap_grows_with_the_floor():
    gaps = [run(_floor_case(living_floor_monthly=f), main="main").floor_gap
            for f in (D("10000"), D("30000"), D("50000"))]
    assert gaps == sorted(gaps)
    assert gaps[0] < gaps[-1]


# --- производные величины -------------------------------------------------

def test_optional_cap_is_balance_minus_reserve():
    s = Scenario(accounts=[Account("main", D("1000"))],
                 payments=[Payment(date(2026, 1, 5), D("400"), "x", "main")])
    r = run(s, main="main")
    assert optional_cap(r, D("250")) == D("350")
    assert optional_cap(r, D("600")) == D("-0")   # резерв больше остатка


def test_cover_cost_yearly_and_daily():
    assert cover_cost(D("-1000"), 10, rate_per_year=D("0.365")) == D("10")
    assert cover_cost(D("-1000"), 10, rate_per_day=D("0.001")) == D("10")
    assert cover_cost(D("1000"), 10, rate_per_day=D("0.001")) == D("10")  # знак не важен
    with pytest.raises(ValueError):
        cover_cost(D("-1000"), 10)


def test_compare_returns_outcomes_in_requested_order():
    base = Scenario(accounts=[Account("main", D("1000"))])
    with_extra = dataclasses.replace(
        base, payments=[Payment(date(2026, 1, 5), D("100"), "x", "main")])
    rows = compare({"без траты": base, "с тратой": with_extra},
                   main="main", reserve=D("0"))
    assert [r.label for r in rows] == ["без траты", "с тратой"]
    assert [r.end_balance for r in rows] == [D("1000"), D("900")]
