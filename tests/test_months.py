"""Тесты проката месяцев — синтетические фикстуры, без личных данных."""
from __future__ import annotations

import time
from datetime import date
from decimal import Decimal as D

import pytest

import finance_core.roll as roll_module
from finance_core import (KIND_PREPAID, LEGAL, AVALANCHE, Account,
                            ConvergenceError, Counterparty,
                            Deal, ForecastInput, Income, Payment, Scenario,
                            Settlements,
                            TransferHint, Wallet, roll_cash, roll_deals,
                            roll_months, run)
from finance_core import WINDOW_DEBTS_CLOSED
from finance_core.roll import _DealsRoll
from finance_core.solver import _CashRoll

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
    """Дыра: кошелёк начинается с отрицательного остатка — дыра видна без событий.

    Дата — `START`, потому что окно известно: так держится общее правило даты
    стартовой дыры, вторая его половина — None в `run()`
    (`test_hole_comes_from_the_outside`).
    """
    s = Scenario(
        accounts=[Account("main", D("-100"))],
    )
    months = roll_cash(s, START, max_months=1, main="main")
    assert months[0].hole == D("100")
    assert months[0].hole_date == START


def test_roll_cash_start_hole_of_a_later_month_is_dated_first_of_month():
    """Правило даты: у стартовой дыры месяца ≥ 2 дата — 1-е число, а не `start`.

    Окно известно, «когда началась» отвечает `window_start` месяца: минус
    перешёл остатками, события нового месяца дыру не создавали.
    """
    s = Scenario(accounts=[Account("main", D("-100"))])
    months = roll_cash(s, START, max_months=2, main="main")
    assert months[1].hole == D("100")
    assert months[1].hole_date == date(2026, 2, 1)


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


def test_roll_cash_floor_gap_subtracts_the_consumed_minimum():
    """Нехватка до минимума считает прожитое: остаток события его не терял.

    Воспроизведение аудита 2026-09-22 (тикет 02): минимум 3 000/мес, приход
    3 000 первого, платёж 1 500 пятнадцатого, следующий приход первого. На 15-е
    прожито 14 дней = 1 400: на руках 3 000 − 1 400 − 1 500 = 100 против
    требования 1 700 — нехватка 1 600, а не 200 от сырого остатка. Прожитое
    второго месяца накоплено тем же счётчиком, что и свободные деньги: в
    феврале прожит январский минимум 3 100 — худшая точка февраля теперь его
    вход, где остаток позади минимума (тикет 03: вход окна участвует в оценке).
    """
    s = Scenario(
        accounts=[Account("main", D("0"))],
        income=[Income(date(2026, m, 1), D("3000"), "main") for m in (1, 2, 3)],
        payments=[Payment(date(2026, 1, 15), D("1500"), account="main",
                          counterparty="c")],
        living_floor_monthly=D("3000"),
    )
    months = roll_cash(s, START, max_months=2, main="main")
    # 3 000 × 17/30 − (3 000 − 1 400 − 1 500) = 1 700 − 100
    assert months[0].floor_gap == D("1600.00")
    assert months[0].floor_gap_date == date(2026, 1, 15)
    # вход февраля позади минимума: 0 − (1 500 − 3 100) — январский минимум
    # остаток не терял; шаг того же дня мягче (2 800 − (4 500 − 3 100) = 1 400)
    assert months[1].floor_gap == D("1600.00")
    assert months[1].floor_gap_date == date(2026, 2, 1)


def test_roll_cash_floor_gap_at_the_window_start_counts_nothing():
    """Шаг в точке отсчёта прожитого ещё не имеет — нехватка считается как раньше."""
    s = Scenario(
        accounts=[Account("main", D("0"))],
        income=[Income(date(2026, m, 1), D("3000"), "main") for m in (1, 2)],
        living_floor_monthly=D("3000"),
    )
    months = roll_cash(s, START, max_months=1, main="main")
    # 3 000 × 31/30 − 3 000 = 3 100 − 3 000: прожитого в самой точке окна нет
    assert months[0].floor_gap == D("100.00")
    assert months[0].floor_gap_date == date(2026, 1, 1)


def test_roll_cash_floor_gap_sees_the_starving_start_of_the_window():
    """Вход окна участвует в оценке: голод до первого прихода виден (тикет 03).

    Воспроизведение аудита 2026-09-22: кошелёк 0, приход 25-го числа, минимум
    3 000 — месяц обязан показывать нехватку первых 24 чисел, а не ноль
    «оценено и покрыто» и не None.
    """
    s = Scenario(
        accounts=[Account("main", D("0"))],
        income=[Income(date(2026, 1, 25), D("3000"), "main")],
        living_floor_monthly=D("3000"),
    )
    months = roll_cash(s, START, max_months=1, main="main")
    # 3 000 × 24/30 − 0 = 2 400: до прихода 24 дня, денег нет
    assert months[0].floor_gap == D("2400.00")
    assert months[0].floor_gap_date == date(2026, 1, 1)


def test_roll_cash_floor_gap_a_month_without_events_gets_a_number():
    """Месяц без событий получает оценку по входу, а не None (тикет 03).

    В феврале нет ни одного события, но мартовский приход впереди — вход
    оценивается, и февраль получает число. После последнего прихода судить
    нечем: апрель остаётся «не оценено», а не «нехватки нет».
    """
    s = Scenario(
        accounts=[Account("main", D("0"))],
        income=[Income(date(2026, m, 5), D("3000"), "main") for m in (1, 3)],
        living_floor_monthly=D("3000"),
    )
    months = roll_cash(s, START, max_months=4, main="main")
    # февраль: вход 3 000 минус прожитый январь 3 100, до прихода 5 марта
    # 32 дня — 3 200 − (3 000 − 3 100) = 3 300
    assert months[1].floor_gap == D("3300.00")
    assert months[1].floor_gap_date == date(2026, 2, 1)
    # апрель: прихода впереди не видно — «не оценено», а не ноль
    assert months[3].floor_gap is None
    assert months[3].floor_gap_date is None


def test_roll_cash_floor_gap_counts_money_on_another_wallet():
    """Деньги на другом кошельке — ликвидность: фантомной нехватки нет (тикет 04).

    Раньше основной показывал 23 000 нехватки, пока free месяца был 100 000:
    свободные деньги видели чужие деньги, а floor — нет.
    """
    s = Scenario(
        accounts=[Account("main", D("1000")), Account("other", D("100000"))],
        income=[Income(date(2026, 1, 25), D("30000"), "main")],
        living_floor_monthly=D("30000"),
    )
    m = roll_cash(s, START, max_months=1, main="main")[0]
    assert m.floor_gap == D("0")       # 101 000 против требования 24 000
    assert m.floor_gap_date is None
    assert m.free > D("0")             # «свободны, но голодаем» больше нет


def test_roll_cash_floor_gap_ignores_unavailable_wallet_money():
    """Недоступный кошелёк в базис не входит: арестованные деньги не кушают."""
    s = Scenario(
        accounts=[Account("main", D("1000")),
                  Account("other", D("100000"), available=False)],
        income=[Income(date(2026, 1, 25), D("30000"), "main")],
        living_floor_monthly=D("30000"),
    )
    m = roll_cash(s, START, max_months=1, main="main")[0]
    # 30 000 × 24/30 − 1 000: чужие закрытые деньги базис не делают
    assert m.floor_gap == D("23000.00")
    assert m.floor_gap_date == date(2026, 1, 1)


def test_roll_cash_floor_gap_free_credit_limit_is_not_money():
    """Свободный лимит кредитки — не деньги и в базисе floor не деньги."""
    s = Scenario(
        accounts=[Account("main", D("1000")),
                  Account("credit", D("0"), is_credit=True, limit=D("100000"))],
        income=[Income(date(2026, 1, 25), D("30000"), "main")],
        living_floor_monthly=D("30000"),
    )
    m = roll_cash(s, START, max_months=1, main="main")[0]
    assert m.floor_gap == D("23000.00")


def test_roll_cash_floor_gap_points_stop_before_prepay():
    """Оценка идёт до досрочки: досрочка точкой оценки не становится (тикет 04).

    Досрочка опустошает кошелёк — если бы её результат оценивали, месяц
    показал бы нехватку, которой до досрочки не было.
    """
    s = Scenario(
        accounts=[Account("main", D("40000"))],
        income=[Income(date(2026, 1, 25), D("30000"), "main"),
                Income(date(2026, 2, 25), D("30000"), "main")],
        payments=[Payment(date(2026, 1, 20), D("70000"), account="main",
                          counterparty="x", prepaid=True)],
        living_floor_monthly=D("30000"),
    )
    m = roll_cash(s, START, max_months=1, main="main")[0]
    assert m.balances["main"] == D("0")  # досрочка прошла и опустошила кошелёк
    assert m.floor_gap == D("0")         # оценка — до досрочки
    assert m.floor_gap_date is None


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


def test_roll_cash_free_subtracts_the_minimum_of_the_whole_window():
    """Свободные деньги вычитают минимум всех месяцев окна, а не текущего.

    Аудит 2026-09-22 (воспроизведение тикета 01): остатки не теряют прожитый
    минимум прошлых месяцев, поэтому free месяцев ≥ 2 завышен ровно на его
    сумму. Кошелёк 0, приход 6 000 пятое число январь–март, минимум 3 000:
    к февралю уже прожиты январские 3 100.
    """
    s = Scenario(
        accounts=[Account("main", D("0"))],
        income=[Income(date(2026, m, 5), D("6000"), "main") for m in (1, 2, 3)],
        living_floor_monthly=D("3000"),
    )
    months = roll_cash(s, START, max_months=3, main="main")
    assert months[0].free == D("2900")              # месяц 1 не меняется: 6 000 − 3 100
    assert months[1].free == D("6100")              # 12 000 − 3 100 − 2 800
    assert months[2].free == D("9000")              # 18 000 − 9 000
    # бюджет досрочек из тех же свободных денег — прожитого в нём нет
    assert months[1].prepay_budget == D("6100")


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


def test_month_fuse_raises_when_the_inner_limit_is_exhausted(monkeypatch):
    """Предохранитель месяца: исчерпан внутренний предел — ошибка с причиной.

    Публичный `max_iterations` снесён тикетом 04 — у зоны один предел, месяцы, —
    а предохранитель остался: малый предел месяца (`_MONTH_PASSES`) и та же
    `ConvergenceError`. Шаг месяца ловится драйвером и становится признаком
    результата — см. `test_roll_months_marks_no_convergence`.
    """
    deal = _deal(amount=D("1000"), schedule=_rule(start=START, payment=D("100")))
    book = _book(deal)
    incomes = [Income(date(2026, 1, 1), D("500"), "main")]
    scenario = Scenario(accounts=[Account(name="main", balance=D("0"))],
                        income=list(incomes), living_floor_monthly=D("200"))
    deals = _DealsRoll(book, START, strategy=AVALANCHE,
                       consent_to_second=False, max_months=12)
    cash = _CashRoll(scenario, START, "main")
    monkeypatch.setattr(roll_module, "_MONTH_PASSES", 1)
    with pytest.raises(ConvergenceError, match="не сошёлся"):
        roll_module._converge_month(deals, cash, 1)


def test_roll_months_marks_no_convergence(monkeypatch):
    """Не сошлось — признак в результате, а расчёт не роняется исключением.

    У месяца есть свободные деньги, поэтому его бюджет меняется от прохода к
    проходу; на один проход круг не сходится — месяц попадает в `unconverged`,
    а прокат считается до конца окна.
    """
    deal = _deal(amount=D("1000"), schedule=_rule(start=START, payment=D("100")))
    book = _book(deal)
    wallets = [_wallet("main", D("0"))]
    incomes = [Income(date(2026, 1, 1), D("500"), "main")]
    monkeypatch.setattr(roll_module, "_MONTH_PASSES", 1)
    result = roll_months(
        book, START, wallets, incomes, [],
        living_floor=D("200"), max_months=12,
    )
    assert result.unconverged[0] == 1          # предохранитель сработал с первого месяца
    assert not result.converged                # признак несходимости виден в результате
    assert len(result.cash_months) == 12       # и расчёт досчитан, а не брошен


# --- проход по месяцам (тикет 04) -------------------------------------------

def _income_on(day: int, amount: D, account: str, months: int) -> list[Income]:
    """Доход каждые `months` месяцев начиная с января 2026 (синтетика)."""
    return [Income(date(2026 + i // 12, i % 12 + 1, day), amount, account)
            for i in range(months)]


def test_twenty_year_debt_with_free_money_gives_the_closing_date():
    """20-летний долг со свободными деньгами считается и даёт дату закрытия.

    До тикета 04 тот же сценарий падал `ConvergenceError`: связка гоняла весь
    прокат целиком на каждой итерации, и правка бюджета, распространяясь на
    месяц вперёд за итерацию, упиралась в лимит 100. Длинный график — ровно
    тот случай, ради которого перестроен проход по месяцам.
    """
    deal = _deal(amount=D("600000"), rate_per_year=D("0.12"),
                 schedule=_rule(days=(20,), payment=D("7270"),
                                count=240, start=START))
    book = _book(deal)
    wallets = [_wallet("main", D("0"))]
    incomes = _income_on(5, D("9270"), "main", 240)   # платёж 7 270 + свободные 2 000
    result = roll_months(book, START, wallets, incomes, [],
                         living_floor=D("0"), obligation_reserve=D("1000"),
                         max_months=240)
    assert result.window.months == 240                 # окно 240 месяцев
    assert result.deal_roll.freedom == date(2034, 10, 1)   # дата закрытия есть
    assert result.converged and result.unconverged == []


def test_roll_time_is_linear_in_months():
    """Время линейно по месяцам: 240 укладывается в 0,5 с и не больше двойного 120."""
    deal = _deal(amount=D("600000"), rate_per_year=D("0.12"),
                 schedule=_rule(days=(20,), payment=D("7270"),
                                count=240, start=START))
    book = _book(deal)
    wallets = [_wallet("main", D("0"))]
    incomes = _income_on(5, D("9270"), "main", 240)

    def best(max_months: int) -> float:
        times = []
        for _ in range(3):
            started = time.perf_counter()
            roll_months(book, START, wallets, incomes, [],
                        living_floor=D("0"), obligation_reserve=D("1000"),
                        max_months=max_months)
            times.append(time.perf_counter() - started)
        return min(times)

    at_120, at_240 = best(120), best(240)
    assert at_240 <= 0.5, f"240 месяцев: {at_240:.3f} с"
    assert at_240 <= 2 * at_120, f"t(240)={at_240:.3f} с против t(120)={at_120:.3f} с"


def test_month_budget_comes_from_the_cash_of_the_same_month():
    """Сходимость месяца: непрошедшее уменьшает свободные, бюджет сходится, досрочка уходит из остатка.

    Январь: деньги есть, касса считает свободные, досрочка уходит из остатка —
    и её сумма равна бюджету месяца. Февраль: кошелёк сделки пуст, обязательный
    платёж не проходит, и непрошедшее вычитается из свободных денег — обещанное
    не отменяется.
    """
    deal = _deal(amount=D("1000"), schedule=_rule(start=START, payment=D("100")))
    book = _book(deal)
    wallets = [_wallet("main", D("0")), _wallet("деньги", D("0"))]
    incomes = ([Income(date(2026, 1, 5), D("500"), "main")]
               + [Income(date(2026, m, 5), D("500"), "деньги")
                  for m in (2, 3)])
    result = roll_months(book, START, wallets, incomes, [],
                         living_floor=D("0"), max_months=3)
    assert result.converged

    jan_deal, jan_cash = result.deal_roll.months[0], result.cash_months[0]
    # Бюджет месяца сходится: досрочка равна свободным деньгам кассы того же месяца
    assert jan_deal.prepaid == jan_cash.free == D("400")
    # Досрочка ушла из остатка: касса пуста после неё
    assert jan_cash.balances["main"] == D("0")

    feb = result.cash_months[1]
    # Непрошедшее уменьшает свободные деньги: деньги есть, но не на том кошельке
    assert [u.amount for u in feb.unsecured][:1] == [D("100")]
    assert feb.free == D("500") - D("100") == D("400")


def test_changed_payments_recompute_the_month_cash_from_its_entry(monkeypatch):
    """Бюджет изменил платежи — касса месяца считается заново с его входа.

    Процент от остатка при ставке > 0: база фиксируется на старте проката по
    остатку без процентов, а платёж месяца считается уже после начисления, —
    поэтому с нулевым бюджетом пул режет платёж, а с бюджетом кассы того же
    месяца пропускает целиком. Платежи изменились — и касса обязана начать
    месяц заново с его входных остатков (`cash.restore`), а не наложить новый
    проход поверх старого. Деньги впритык (доход меньше платежа) держат бюджет
    в колебании до внутреннего предела — это и ловит предохранитель.
    """
    deal = _deal(amount=D("1000"), rate_per_year=D("0.12"),
                 schedule=_rule(days=(20,), percent=D("0.05"), start=START))
    book = _book(deal)
    wallets = [_wallet("main", D("0"))]
    incomes = _income_on(5, D("50.40"), "main", 6)

    calls: list[int] = []
    original = _CashRoll.month

    def counting(self, index, payments=()):
        calls.append(index)
        return original(self, index, payments)

    monkeypatch.setattr(_CashRoll, "month", counting)
    result = roll_months(book, START, wallets, incomes, [],
                         living_floor=None, max_months=6)

    # Месяц 1 считался не один раз: платежи изменились — ветка восстановления
    # состояния кассы исполнялась, а не пропускалась.
    assert calls.count(1) > 1
    assert result.cash_months[0].free == D("0.00")   # вход месяца, а не его остаток
    assert result.unconverged == [1]                 # предохранитель сработал без подмены предела
    assert not result.converged


def test_iteration_limit_is_gone_from_the_entry():
    """Предел итераций исчез из публичного входа: зона не может его задать."""
    deal = _deal(amount=D("1000"), schedule=_rule(start=START, payment=D("100")))
    book = _book(deal)
    wallets = [_wallet("main", D("0"))]
    incomes = [Income(date(2026, 1, 1), D("500"), "main")]
    with pytest.raises(TypeError, match="max_iterations"):
        roll_months(book, START, wallets, incomes, [],
                    living_floor=D("0"), max_months=12, max_iterations=1)
    with pytest.raises(TypeError, match="max_iterations"):
        ForecastInput(book=book, start=START, wallets=wallets,
                      max_iterations=1)


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


# --- окно проката ------------------------------------------------------------

def test_roll_months_cash_covers_the_window_not_the_month_cap():
    """Касса катается по окну, а не по пределу месяцев: вопрос кончается на долге."""
    deal = _deal(amount=D("24000"),
                 schedule=_rule(start=START, payment=D("1000"), count=24))
    book = _book(deal)
    wallets = [_wallet("main", D("0"))]
    incomes = [Income(date(2026, month, 5), D("3000"), "main")
               for month in range(1, 13)]
    result = roll_months(book, START, wallets, incomes, [], living_floor=D("0"),
                         max_months=600)
    assert result.window.months == 24
    assert result.window.reason == WINDOW_DEBTS_CLOSED
    assert len(result.cash_months) == 24
