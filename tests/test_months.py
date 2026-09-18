"""Тесты проката месяцев — синтетические фикстуры, без личных данных."""
from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

from finance_core import (KIND_PREPAID, LEGAL, Account, ConvergenceError, Counterparty,
                            Deal, Income, Payment, Scenario, Settlements,
                            TransferHint, Wallet, roll_cash, roll_deals,
                            roll_months, run)

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


def _book(*deals, counterparties=(), wallets=None, movements=(), edits=()) -> Settlements:
    from finance_core import LEGAL
    if wallets is None:
        wallets = [_wallet()]
    return Settlements(
        counterparties=[Counterparty(uid="банк", name="Банк", kind=LEGAL,
                                     subtype="банк", groups=("долги",)),
                        *counterparties],
        wallets=list(wallets),
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
    assert months[0].hole == D("0")


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


def test_roll_cash_does_not_pay_without_money():
    """Платёж, которому не хватило кошелька, не проходит и в прокате месяцев.

    Правило одно на оба пути: иначе ложь осталась бы ровно там, где зона смотрит.
    """
    s = Scenario(
        accounts=[Account("деньги", D("0")), Account("пустой", D("0"))],
        income=[Income(date(2026, 1, 1), D("500"), "деньги")],
        payments=[Payment(date(2026, 1, 10), D("500"), account="пустой",
                          counterparty="c")],
    )
    months = roll_cash(s, START, max_months=1, main="деньги")
    assert months[0].balances == {"деньги": D("500"), "пустой": D("0")}
    assert months[0].hole == D("0")                 # деньги есть — дыры нет
    assert months[0].unsecured_total == D("500")
    assert months[0].unsecured[0].hint == TransferHint("деньги", D("500"))
    assert months[0].free == D("0")                 # эти деньги уже обещаны платежу


def test_roll_cash_budget_counts_all_wallets():
    """Бюджет досрочек считается по всем кошелькам, а не по кошельку сделки."""
    s = Scenario(accounts=[Account("пустой", D("0")), Account("деньги", D("300"))])
    months = roll_cash(s, START, max_months=1, main="деньги")
    assert months[0].free == D("300")               # деньги лежат не там, где платит сделка


def test_roll_cash_free_money_excludes_what_did_not_pass():
    """Свободные деньги не считают обещанное: непрошедшее обязательство не отменено."""
    s = Scenario(
        accounts=[Account("деньги", D("500")), Account("пустой", D("0"))],
        payments=[Payment(date(2026, 1, 10), D("300"), account="пустой",
                          counterparty="c")],
    )
    months = roll_cash(s, START, max_months=1, main="деньги")
    assert months[0].unsecured_total == D("300")
    assert months[0].free == D("200")               # 500 на руках, 300 из них обещаны


def test_roll_cash_hole_is_a_shortage_across_wallets():
    """Дыра — нехватка суммарно по кошелькам; минус приходит снаружи."""
    s = Scenario(accounts=[Account("main", D("-100")), Account("второй", D("0"))])
    months = roll_cash(s, START, max_months=1, main="main")
    assert months[0].hole == D("100")
    assert months[0].hole_date == START
    assert months[0].unsecured == []


def test_roll_cash_prepayment_takes_what_the_wallet_has():
    """Досрочка проходит в границах кошелька, а не целиком."""
    s = Scenario(
        accounts=[Account("main", D("100"))],
        payments=[Payment(date(2026, 4, 30), D("400"), account="main",
                          counterparty="c", prepaid=True)],
    )
    months = roll_cash(s, date(2026, 4, 1), max_months=1, main="main")
    assert months[0].balances["main"] == D("0")     # ушло ровно сто
    assert months[0].unsecured_total == D("300")
    assert months[0].unsecured[0].kind == KIND_PREPAID


def test_both_paths_agree_on_the_guard():
    """Касса и прокат месяцев считают предохранитель одинаково."""
    s = Scenario(
        accounts=[Account("деньги", D("100")), Account("пустой", D("0"))],
        income=[Income(date(2026, 1, 5), D("500"), "деньги")],
        payments=[Payment(date(2026, 1, 10), D("400"), account="пустой",
                          counterparty="c"),
                  Payment(date(2026, 1, 20), D("200"), account="деньги",
                          counterparty="c")],
    )
    r = run(s, main="деньги")
    months = roll_cash(s, START, max_months=1, main="деньги")
    assert months[0].balances == r.balances
    assert months[0].hole == r.hole
    assert months[0].unsecured_total == r.unsecured_total


def test_both_paths_agree_on_the_credit_limit():
    """Касса и прокат месяцев держат правило лимита одинаково.

    Долговой платёж лимита не получает, жизненный получает — в обоих путях
    одно и то же число.
    """
    s = Scenario(
        accounts=[Account("card", D("0"), is_credit=True, limit=D("1000"))],
        payments=[Payment(date(2026, 1, 10), D("400"), account="card",
                          counterparty="c"),
                  Payment(date(2026, 1, 11), D("300"), account="card",
                          counterparty="c", debt=False)],
    )
    r = run(s, main="card")
    months = roll_cash(s, START, max_months=1, main="card")
    assert months[0].balances == r.balances == {"card": D("-300")}
    assert months[0].unsecured_total == r.unsecured_total == D("400")


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
    assert months[0].hole == D("0")


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


def test_roll_cash_obligation_reserve_reduces_free():
    """Резерв обязательств вычитается из свободных денег: без него рвётся начало месяца."""
    s = Scenario(
        accounts=[Account("main", D("1000"))],
        income=[Income(date(2026, 4, 5), D("500"), "main")],
        payments=[Payment(date(2026, 4, 10), D("200"), account="main",
                          counterparty="c")],
        living_floor_monthly=D("800"),
        obligation_reserve=D("300"),
    )
    months = roll_cash(s, date(2026, 4, 1), max_months=1, main="main")
    # 1300 - 800 (минимум) - 300 (резерв) = 200
    assert months[0].free == D("200")


def test_roll_cash_unknown_floor_gives_none_gap():
    """Неизвестный прожиточный минимум — floor_gap = None, а не ноль."""
    s = Scenario(
        accounts=[Account("main", D("1000"))],
        income=[Income(date(2026, 1, 5), D("500"), "main")],
    )
    months = roll_cash(s, START, max_months=1, main="main")
    assert months[0].floor_gap is None
    assert months[0].floor_gap_date is None


def test_roll_cash_free_money_can_be_negative():
    """Свободные деньги — состояние месяца: без денег величина отрицательна.

    Отрицательные свободные деньги — нехватка, а не бюджет: бюджет досрочек
    (`prepay_budget`) не бывает отрицательным.
    """
    s = Scenario(
        accounts=[Account("main", D("0"))],
        payments=[Payment(date(2026, 1, 10), D("500"), account="main",
                          counterparty="c")],
    )
    months = roll_cash(s, START, max_months=1, main="main")
    assert months[0].free == D("-500")              # деньги обещаны платежу
    assert months[0].prepay_budget == D("0")


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


def test_roll_months_converges_without_living_floor():
    """Прокат месяцев сходится, когда прожиточный минимум неизвестен, а денег не хватает.

    Дефект: отрицательные свободные деньги уходили в бюджет досрочек, уменьшали
    пул месяца — обязательные платежи не проходили целиком, и расчёт уходил в
    `ConvergenceError`. Нехватка бюджетом не становится: он не бывает отрицательным.
    """
    deal = _deal(amount=D("3000"), rate_per_year=None, rate_per_day=D("0.00005"),
                 schedule=_rule(start=START, payment=D("1000")))
    book = _book(deal)
    wallets = [_wallet("main", D("0"))]
    result = roll_months(book, START, wallets, [], [],
                         living_floor=None, max_months=4)
    assert result.iterations >= 1
    # Свободные деньги — состояние месяца: 0 − 1 000, платёж не прошёл
    assert result.cash_months[0].free == D("-1000")
    # Бюджет досрочек не бывает отрицательным — нехватка его не уменьшает
    assert all(cm.prepay_budget == D("0") for cm in result.cash_months)


def test_roll_months_assumed_after_hole():
    """Месяцы после дыры помечаются как посчитанные на допущении.

    Дыра приходит снаружи: движок её не создаёт, поэтому нехватка стоит на старте.
    """
    deal = _deal(amount=D("1000"), schedule=_rule(start=START, payment=D("100")))
    book = _book(deal)
    wallets = [_wallet("main", D("-100"))]
    result = roll_months(
        book, START, wallets, [], [],
        living_floor=D("0"), max_months=3,
    )
    assert result.cash_months[0].hole == D("100")
    # All months from the first hole onward are assumed
    assert result.assumed == [1, 2, 3]


def test_roll_months_marks_the_forecast_as_conditional():
    """Прокат месяцев показывает, что прогноз долгов держится на переводе.

    Кошелёк сделки пуст, деньги лежат на другом: долговая сторона считает, что
    платежи прошли, а касса их не пропускает. Прогноз верен только при условии
    перевода — и обе стороны этого условия видны числом, а не молчанием.
    """
    deal = _deal(amount=D("300"), schedule=_rule(start=START, payment=D("100")))
    book = _book(deal)
    wallets = [_wallet("main", D("0")), _wallet("деньги", D("0"))]
    incomes = [Income(date(2026, 1, 1), D("300"), "деньги")]
    result = roll_months(book, START, wallets, incomes, [],
                         living_floor=D("0"), max_months=3)
    assert result.deal_roll.total_paid == D("300")      # долговая сторона: заплачено
    assert result.unsecured_total == D("300")           # касса: не прошло ничего
    assert result.cash_months[0].unsecured[0].hint.source == "деньги"


def test_roll_months_credit_card_pays_rent_but_not_the_loan():
    """Кредитка платит регулярный расход и не платит заём: признак — из сделки.

    Лимит закрыт под погашение кредитов, а аренда — жизненный расход: она
    проходит. Заём не проходит целиком и виден необеспеченностью, а не дырой.
    """
    loan = _deal(uid="заём", amount=D("1000"), wallet="кредитка",
                 schedule=_rule(start=START, payment=D("500")))
    rent = _deal(uid="аренда", amount=None, counterparty="арендодатель",
                 wallet="кредитка", schedule=_rule(start=START, payment=D("300")))
    wallets = [_wallet("кредитка", D("0"), is_credit=True, limit=D("10000"))]
    book = _book(loan, rent,
                 counterparties=[Counterparty(uid="арендодатель",
                                              name="Арендодатель", kind=LEGAL,
                                              subtype="прочее")],
                 wallets=wallets)
    result = roll_months(book, START, wallets, [], [], living_floor=D("0"),
                         max_months=1)
    assert result.cash_months[0].balances["кредитка"] == D("-300")
    assert result.cash_months[0].hole == D("0")         # деньги есть — лимит закрыт
    [u] = result.cash_months[0].unsecured
    assert (u.amount, u.short) == (D("500"), D("500"))


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
