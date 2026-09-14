"""Тесты проката месяцев — синтетические фикстуры, без личных данных."""
from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

from finance_core import (Account, ConvergenceError, Counterparty, Deal,
                            Income, Payment, Scenario, Settlements, Wallet,
                            roll_cash, roll_deals, roll_months)

START = date(2026, 1, 1)


def _wallet(uid: str = "main", balance: D = D("0"), **kw) -> Wallet:
    base = dict(uid=uid, name=uid, kind="карта", balance=balance,
                available=True, is_credit=False)
    base.update(kw)
    return Wallet(**base)


def _deal(uid: str = "заём", amount: D | None = D("1000"), **kw) -> Deal:
    from finance_core import ScheduleRule
    base = dict(uid=uid, title="Заём", counterparty="банк", amount=amount,
                rate_per_year=D("0"), wallet="main")
    base.update(kw)
    return Deal(**base)


def _book(*deals, counterparties=(), movements=(), edits=()) -> Settlements:
    from finance_core import LEGAL
    return Settlements(
        counterparties=[Counterparty(uid="банк", name="Банк", kind=LEGAL,
                                     subtype="банк", groups=("долги",)),
                        *counterparties],
        deals=list(deals),
        movements=list(movements),
        edits=list(edits),
    )


def _rule(days=(20,), **kw):
    from finance_core import ScheduleRule
    return ScheduleRule(days=days, **kw)


# --- roll_cash -------------------------------------------------------------

def test_roll_cash_one_month():
    """Касса за месяц: доход, платёж, остаток, свободные деньги."""
    s = Scenario(
        accounts=[Account("main", D("1000"))],
        income=[Income(date(2026, 1, 5), D("500"), "main")],
        payments=[Payment(date(2026, 1, 10), D("200"), account="main",
                          counterparty="c")],
    )
    months = roll_cash(s, START, max_months=1, main="main")
    assert len(months) == 1
    assert months[0].balances["main"] == D("1300")
    assert months[0].free == D("1300")
    assert months[0].hole is None


def test_roll_cash_multi_month_balances_carry():
    """Остатки переносятся из месяца в месяц."""
    s = Scenario(
        accounts=[Account("main", D("0"))],
        income=[Income(date(2026, 1, 5), D("500"), "main"),
                Income(date(2026, 2, 5), D("500"), "main")],
        payments=[Payment(date(2026, 1, 10), D("200"), account="main",
                          counterparty="c")],
    )
    months = roll_cash(s, START, max_months=2, main="main")
    assert months[0].balances["main"] == D("300")
    assert months[1].balances["main"] == D("800")


def test_roll_cash_income_on_weekend_not_shifted():
    """Доход на выходной не сдвигается кассой — это ответственность вызывающего."""
    # 2026-01-10 is Saturday — income stays on Saturday if caller passed it
    s = Scenario(
        accounts=[Account("main", D("0"))],
        income=[Income(date(2026, 1, 10), D("500"), "main")],
    )
    months = roll_cash(s, START, max_months=1, main="main")
    # Income on Saturday is processed as-is
    assert months[0].balances["main"] == D("500")


def test_roll_cash_unavailable_wallet_cannot_pay():
    """Платёж с недоступного кошелька — ошибка."""
    s = Scenario(
        accounts=[Account("main", D("0"), available=False)],
        payments=[Payment(date(2026, 1, 10), D("200"), account="main",
                          counterparty="c")],
    )
    with pytest.raises(ValueError, match="недоступен"):
        roll_cash(s, START, max_months=1)


def test_roll_cash_hole_on_non_credit():
    """Дыра: уход некредитного счёта в минус."""
    s = Scenario(
        accounts=[Account("main", D("100"))],
        payments=[Payment(date(2026, 1, 10), D("200"), account="main",
                          counterparty="c")],
    )
    months = roll_cash(s, START, max_months=1, main="main")
    assert months[0].hole == D("100")
    assert months[0].hole_date == date(2026, 1, 10)


def test_roll_cash_hole_on_starting_negative_balance():
    """Дыра: счёт начинается с отрицательного остатка — дыра видна без событий."""
    s = Scenario(
        accounts=[Account("main", D("-100"))],
    )
    months = roll_cash(s, START, max_months=1, main="main")
    assert months[0].hole == D("100")
    assert months[0].hole_date == START


def test_roll_cash_credit_negative_is_not_hole():
    """Минус на кредитном — долг, а не дыра."""
    s = Scenario(
        accounts=[Account("card", D("-100"), is_credit=True)],
        payments=[Payment(date(2026, 1, 10), D("200"), account="card",
                          counterparty="c")],
    )
    months = roll_cash(s, START, max_months=1, main="card")
    assert months[0].hole is None


def test_roll_cash_living_floor_reduces_free():
    """Свободные деньги = остаток минус прожиточный минимум."""
    # April has 30 days → pro-rata = full month
    s = Scenario(
        accounts=[Account("main", D("1000"))],
        income=[Income(date(2026, 4, 5), D("500"), "main")],
        payments=[Payment(date(2026, 4, 10), D("200"), account="main",
                          counterparty="c")],
        living_floor_monthly=D("800"),
    )
    months = roll_cash(s, date(2026, 4, 1), max_months=1, main="main")
    # 1000 + 500 - 200 = 1300; free = 1300 - 800 = 500
    assert months[0].free == D("500")


def test_roll_cash_unknown_floor_gives_none_gap():
    """Неизвестный прожиточный минимум — floor_gap = None, а не ноль."""
    s = Scenario(
        accounts=[Account("main", D("1000"))],
        income=[Income(date(2026, 1, 5), D("500"), "main")],
    )
    months = roll_cash(s, START, max_months=1, main="main")
    assert months[0].floor_gap is None
    assert months[0].floor_gap_date is None


# --- roll_months -----------------------------------------------------------

def test_roll_months_converges():
    """Связка долги + касса сходится за конечное число шагов."""
    deal = _deal(amount=D("1000"), schedule=_rule(start=START, payment=D("100")))
    book = _book(deal)
    wallets = [_wallet("main", D("0"))]
    incomes = [Income(date(2026, 1, 1), D("500"), "main")]
    result = roll_months(
        book, START, wallets, incomes, [],
        living_floor=D("200"), max_months=12,
    )
    assert result.iterations >= 1
    assert len(result.cash_months) == 12
    # Deal may close earlier than max_months — that's correct
    assert len(result.deal_roll.months) <= 12


def test_roll_months_assumed_after_hole():
    """Месяцы после дыры помечаются как посчитанные на допущении."""
    deal = _deal(amount=D("1000"), schedule=_rule(start=START, payment=D("100")))
    book = _book(deal)
    wallets = [_wallet("main", D("0"))]
    # No income → hole in first month
    result = roll_months(
        book, START, wallets, [], [],
        living_floor=D("0"), max_months=3,
    )
    assert result.cash_months[0].hole is not None
    # All months from the first hole onward are assumed
    assert result.assumed == [1, 2, 3]


def test_roll_months_raises_on_no_convergence():
    """Не сошлось — ошибка с понятной причиной."""
    deal = _deal(amount=D("1000"), schedule=_rule(start=START, payment=D("100")))
    book = _book(deal)
    wallets = [_wallet("main", D("0"))]
    incomes = [Income(date(2026, 1, 1), D("500"), "main")]
    with pytest.raises(ConvergenceError, match="не сошёлся"):
        roll_months(
            book, START, wallets, incomes, [],
            living_floor=D("200"), max_months=12, max_iterations=1,
        )


# --- roll_deals per-month budgets --------------------------------------------

def test_roll_deals_per_month_budgets_affect_free():
    """Бюджет досрочек по месяцам меняет прокат."""
    deal = _deal(amount=D("1000"), schedule=_rule(start=START, payment=D("100")))
    book = _book(deal)
    # Budget 0: free = 100 - 100 = 0 each month
    roll0 = roll_deals(book, START, D("0"), max_months=3)
    assert roll0.months[0].free == D("0")
    # Budget 200 in month 1: more prepayment, less free
    roll1 = roll_deals(book, START, D("0"), max_months=3,
                       budgets={1: D("200")})
    assert roll1.months[0].prepaid == D("200")
    assert roll1.months[0].free == D("0")


# --- досрочка в кассе --------------------------------------------------------

def test_roll_cash_prepaid_comes_after_free():
    """Досрочка не уменьшает свободные деньги: она уходит после них."""
    s = Scenario(
        accounts=[Account("main", D("1000"))],
        payments=[
            Payment(date(2026, 4, 10), D("200"), account="main",
                    counterparty="c"),
            Payment(date(2026, 4, 30), D("300"), account="main",
                    counterparty="c", prepaid=True),
        ],
        living_floor_monthly=D("400"),
    )
    months = roll_cash(s, date(2026, 4, 1), max_months=1, main="main")
    # free = (1000 - 200) - 400 = 400 — досрочка в него не входит
    assert months[0].free == D("400")
    # остаток = 1000 - 200 - 300: досрочка из кассы ушла
    assert months[0].balances["main"] == D("500")


def test_roll_months_prepayment_leaves_the_cash():
    """Досрочка уходит из кассы: остаток сходится с уплаченным по расписанию.

    Регресс на класс ошибок «касса не видит досрочку»: остаток был завышен на
    всё, что ушло сверх графика, и прогноз показывал деньги, которых нет.
    """
    deal = _deal(amount=D("1000"), schedule=_rule(start=START, payment=D("100")))
    book = _book(deal)
    wallets = [_wallet("main", D("0"))]
    incomes = [Income(date(2026, m, 1), D("500"), "main") for m in range(1, 4)]
    result = roll_months(book, START, wallets, incomes, [],
                         living_floor=D("0"), max_months=3)
    prev = D("0")
    for cm in result.cash_months:
        dm = (result.deal_roll.months[cm.index - 1]
              if cm.index <= len(result.deal_roll.months) else None)
        paid = sum((p.amount for p in dm.payments), D("0")) if dm else D("0")
        assert cm.balances["main"] == prev + D("500") - paid
        prev = cm.balances["main"]
    assert result.deal_roll.months[-1].balances["заём"] == D("0")
    # Деньги не берутся из ниоткуда: сколько ушло из кассы, столько дошло до долга
    left = D("1500") - result.cash_months[-1].balances["main"]
    assert left == result.deal_roll.total_paid
