"""Тесты кассовой стороны — синтетические фикстуры, без личных данных."""
from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal as D

import pytest

from finance_core import (KIND_PAYMENT, KIND_PREPAID, KIND_TRANSFER, Account,
                          Income, Payment, Scenario, Transfer, TransferHint,
                          compare, cover_cost, optional_cap, roll_cash, run)


# --- каскад ---------------------------------------------------------------

def test_events_apply_in_date_order():
    """События применяются по дате, остаток ведётся по каждому кошельку."""
    s = Scenario(
        accounts=[Account("main", D("1000"))],
        income=[Income(date(2026, 3, 20), D("500"), "main")],
        payments=[Payment(date(2026, 3, 5), D("200"), account="main", counterparty="a"),
                  Payment(date(2026, 3, 25), D("300"), account="main", counterparty="b")],
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
        payments=[Payment(date(2026, 3, 5), D("200"), account="main", counterparty="a")],
        transfers=[Transfer(date(2026, 3, 6), D("100"), "main", "other")],
    )
    dates = [x.date for x in run(s, main="main").timeline]
    assert dates == sorted(dates)


def test_unknown_account_gives_clear_error():
    """Опечатка в имени кошелька — понятная ошибка, а не KeyError."""
    s = Scenario(accounts=[Account("main", D("100"))],
                 payments=[Payment(date(2026, 1, 1), D("10"), account="typo", counterparty="x")])
    with pytest.raises(ValueError, match="typo"):
        run(s, main="main")
    with pytest.raises(ValueError, match="основной кошелёк"):
        run(Scenario(accounts=[Account("main", D("100"))]), main="нет-такого")


# --- суммы событий положительные ------------------------------------------

def test_negative_payment_is_an_error_not_money():
    """Отрицательный платёж не начисляет деньги: модель отказывает, а не считает.

    Регресс на класс ошибок «знак вместо направления»: `Payment(amount=-50)`
    с пустого кошелька не списывал, а начислял 50 — в линии шаг +50, и
    необеспеченности не было, потому что «деньги» появились.
    """
    s = Scenario(accounts=[Account("main", D("0"))],
                payments=[Payment(date(2026, 1, 5), D("-50"), account="main", counterparty="x")])
    with pytest.raises(ValueError, match="положительной"):
        run(s, main="main")


def test_zero_payment_is_an_error():
    """Нулевой платёж — не деньги: он ничего не двигает, отдельного смысла нет."""
    s = Scenario(accounts=[Account("main", D("100"))],
                payments=[Payment(date(2026, 1, 5), D("0"), account="main", counterparty="x")])
    with pytest.raises(ValueError, match="положительной"):
        run(s, main="main")


def test_negative_transfer_is_an_error_not_money_moved_backwards():
    """Отрицательный перевод не двигает деньги назад: источник не получает,
    получатель не уходит в минус."""
    s = Scenario(accounts=[Account("a", D("0")), Account("b", D("0"))],
                transfers=[Transfer(date(2026, 1, 5), D("-100"), "a", "b")])
    with pytest.raises(ValueError, match="положительной"):
        run(s, main="a")


def test_zero_transfer_is_an_error():
    s = Scenario(accounts=[Account("a", D("100")), Account("b", D("0"))],
                transfers=[Transfer(date(2026, 1, 5), D("0"), "a", "b")])
    with pytest.raises(ValueError, match="положительной"):
        run(s, main="a")


def test_negative_income_is_an_error_not_money():
    """Отрицательный доход — та же ошибка знака, что и отрицательный платёж."""
    s = Scenario(accounts=[Account("main", D("0"))],
                income=[Income(date(2026, 1, 1), D("-10"), "main")])
    with pytest.raises(ValueError, match="положительной"):
        run(s, main="main")


def test_zero_income_is_an_error():
    """«В этом месяце дохода нет» — пустой список, а не ноль в списке."""
    s = Scenario(accounts=[Account("main", D("0"))],
                income=[Income(date(2026, 1, 1), D("0"), "main")])
    with pytest.raises(ValueError, match="положительной"):
        run(s, main="main")


def test_negative_amounts_rejected_in_roll_cash_too():
    """Проверка одна на оба пути: `run()` и `roll_cash()` не разъезжаются."""
    s = Scenario(accounts=[Account("main", D("0"))],
                payments=[Payment(date(2026, 1, 5), D("-50"), account="main", counterparty="x")])
    with pytest.raises(ValueError, match="положительной"):
        roll_cash(s, start=date(2026, 1, 1))
    s = Scenario(accounts=[Account("a", D("0")), Account("b", D("0"))],
                transfers=[Transfer(date(2026, 1, 5), D("-100"), "a", "b")])
    with pytest.raises(ValueError, match="положительной"):
        roll_cash(s, start=date(2026, 1, 1))
    s = Scenario(accounts=[Account("main", D("0"))],
                income=[Income(date(2026, 1, 1), D("0"), "main")])
    with pytest.raises(ValueError, match="положительной"):
        roll_cash(s, start=date(2026, 1, 1))


def test_scenario_without_accounts_is_an_error():
    """Сценарий без кошельков — ошибка с понятным текстом, а не StopIteration из кассы."""
    s = Scenario(accounts=[],
                 payments=[Payment(date(2026, 1, 5), D("50"), account="main", counterparty="x")])
    with pytest.raises(ValueError, match="не объявлено ни одного"):
        roll_cash(s, start=date(2026, 1, 1))
    with pytest.raises(ValueError, match="не объявлено ни одного"):
        run(s, main="main")


def test_payment_hits_its_funding_account():
    """Платёж списывается со своего кошелька, а не с основного.

    Это регресс на класс ошибок «какой картой платим»: если платёж уходит
    не со своего кошелька, результат меняется.
    """
    s = Scenario(
        accounts=[Account("main", D("1000")), Account("card", D("500"))],
        payments=[Payment(date(2026, 1, 10), D("300"), account="card", counterparty="x")],
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


def test_payment_does_not_pass_when_the_wallet_is_empty():
    """Платёж, которому не хватило ёмкости кошелька, не проходит целиком.

    Регресс на класс ошибок «движок сам создаёт дыру»: платёж уходил с
    финансирующего кошелька, уводил его в минус, а деньги простаивали рядом.
    """
    s = Scenario(
        accounts=[Account("main", D("1000"))],
        payments=[Payment(date(2026, 1, 10), D("1200"), account="main", counterparty="x")],
    )
    r = run(s, main="main")
    assert r.balances["main"] == D("1000")          # кошелёк не ушёл в минус
    assert r.min_balance == D("1000")               # остаток остался остатком
    assert r.hole == D("0")                         # деньги есть — дыры нет
    [(u,)] = [r.unsecured]
    assert (u.date, u.amount, u.paid, u.short, u.kind) == (
        date(2026, 1, 10), D("1200"), D("0"), D("200"), KIND_PAYMENT)
    # Не прошёл целиком, а не хватило двухсот: столько и надо перевести.
    assert u.amount - u.paid == D("1200")


def test_hole_is_a_shortage_across_wallets():
    """Дыра — нехватка суммарно по доступным кошелькам: «денег нет нигде».

    Необеспеченный платёж дырой не становится: деньги есть, но не на этом
    кошельке. Смешать их значит потерять причину.
    """
    elsewhere = Scenario(
        accounts=[Account("деньги", D("500")), Account("пустой", D("0"))],
        payments=[Payment(date(2026, 1, 10), D("300"), account="пустой", counterparty="x")],
    )
    r = run(elsewhere, main="деньги")
    assert r.hole == D("0")
    assert r.unsecured_total == D("300")
    assert r.balances == {"деньги": D("500"), "пустой": D("0")}

    nowhere = Scenario(
        accounts=[Account("деньги", D("100")), Account("пустой", D("0"))],
        payments=[Payment(date(2026, 1, 10), D("300"), account="пустой", counterparty="x")],
    )
    assert run(nowhere, main="деньги").hole == D("0")   # деньги на одном есть


def test_hole_comes_from_the_outside():
    """Дыра приходит снаружи — остатком, который уже в минусе.

    Даты у такой дыры нет: события её не создавали.
    """
    r = run(Scenario(accounts=[Account("main", D("-300"))]), main="main")
    assert r.hole == D("300")
    assert r.hole_date is None
    assert r.unsecured == []


def test_money_on_another_wallet_is_not_a_hole():
    """Деньги на другом кошельке — необеспеченность, а не дыра, и лечится переводом."""
    s = Scenario(
        accounts=[Account("деньги", D("0")), Account("пустой", D("0"))],
        income=[Income(date(2026, 1, 1), D("500"), "деньги")],
        payments=[Payment(date(2026, 1, 10), D("500"), account="пустой", counterparty="кредитор")],
        living_floor_monthly=D("0"),
    )
    r = run(s, main="деньги")
    assert r.balances == {"деньги": D("500"), "пустой": D("0")}
    assert r.hole == D("0")
    assert r.unsecured_total == D("500")
    assert r.unsecured[0].hint == TransferHint("деньги", D("500"))


def test_prepayment_takes_what_the_wallet_can_give():
    """Досрочка — исключение по сумме: движок назначает столько, сколько кошелёк может.

    Обязательство платят целиком или не платят вовсе, а досрочку движок выбирает
    сам — и берёт её в границах кошелька.
    """
    s = Scenario(
        accounts=[Account("main", D("100"))],
        payments=[Payment(date(2026, 1, 10), D("400"), account="main", counterparty="x", prepaid=True)],
    )
    r = run(s, main="main")
    assert r.balances["main"] == D("0")             # ушло ровно сто
    [(u,)] = [r.unsecured]
    assert (u.kind, u.amount, u.paid, u.short) == (
        KIND_PREPAID, D("400"), D("100"), D("300"))


def test_prepayment_with_nothing_to_give_pays_nothing():
    """Досрочка на пустом кошельке не платит ничего и в минус не уходит."""
    s = Scenario(
        accounts=[Account("main", D("0"))],
        payments=[Payment(date(2026, 1, 10), D("400"), account="main", counterparty="x", prepaid=True)],
    )
    r = run(s, main="main")
    assert r.balances["main"] == D("0")
    assert r.timeline == []                 # деньги не двигались
    [(u,)] = [r.unsecured]
    assert (u.paid, u.short) == (D("0"), D("400"))


def test_transfer_from_an_empty_wallet_does_not_pass():
    """Перевод с пустого кошелька не проходит — тем же предохранителем."""
    s = Scenario(
        accounts=[Account("деньги", D("500")), Account("пустой", D("0"))],
        transfers=[Transfer(date(2026, 1, 5), D("80"), "пустой", "деньги")],
    )
    r = run(s, main="деньги")
    assert r.balances == {"деньги": D("500"), "пустой": D("0")}
    assert r.total == D("500")                      # деньги не берутся из ниоткуда
    [(u,)] = [r.unsecured]
    assert (u.kind, u.short) == (KIND_TRANSFER, D("80"))
    # Сначала досылают на кошелёк, потом переводят с него.
    assert u.hint == TransferHint("деньги", D("80"))


def test_hint_does_not_promise_more_than_the_source_has():
    """Подсказка говорит, сколько можно перевести: обещать больше нечего."""
    s = Scenario(
        accounts=[Account("малый", D("50")), Account("пустой", D("0"))],
        payments=[Payment(date(2026, 1, 10), D("300"), account="пустой", counterparty="x")],
    )
    [(u,)] = [run(s, main="пустой").unsecured]
    assert u.short == D("300")
    assert u.hint == TransferHint("малый", D("50"))


def test_hint_is_absent_when_there_is_nowhere_to_transfer_from():
    """Переводить неоткуда — подсказки нет: это дыра, а не необеспеченность."""
    s = Scenario(accounts=[Account("пустой", D("0"))],
                 payments=[Payment(date(2026, 1, 10), D("300"), account="пустой", counterparty="x")])
    [(u,)] = [run(s, main="пустой").unsecured]
    assert u.hint is None


# --- ёмкость кошелька: кредитный лимит -------------------------------------

def test_credit_wallet_pays_a_living_expense_up_to_its_free_limit():
    """Кредитка платит жизненный расход остатком и свободным лимитом, а больше — нет."""
    s = Scenario(
        accounts=[Account("card", D("0"), is_credit=True, limit=D("1000"))],
        payments=[Payment(date(2026, 1, 10), D("1000"), account="card", counterparty="x", debt=False),
                  Payment(date(2026, 1, 11), D("100"), account="card", counterparty="y", debt=False)],
    )
    r = run(s, main="card")
    assert r.balances["card"] == D("-1000")         # минус на кредитном — долг
    assert r.unsecured_total == D("100")            # лимита больше нет
    assert r.hole == D("0")                         # долг — не дыра


def test_credit_wallet_with_debt_pays_the_rest_of_its_limit():
    """Ёмкость кредитки с долгом — свободный лимит: долг в нём уже учтён.

    Регресс на двойной учёт долга: сложив остаток (−900) со свободным лимитом
    (100), движок объявил бы, что кошелёк не может ничего.
    """
    s = Scenario(
        accounts=[Account("card", D("-900"), is_credit=True, limit=D("1000"))],
        payments=[Payment(date(2026, 1, 10), D("100"), account="card", counterparty="x", debt=False),
                  Payment(date(2026, 1, 11), D("50"), account="card", counterparty="y", debt=False)],
    )
    r = run(s, main="card")
    assert r.balances["card"] == D("-1000")         # лимит выбран до конца
    assert r.unsecured_total == D("50")             # а больше лимита нет
    assert r.hole == D("0")                         # долг — не дыра


def test_credit_limit_does_not_pay_a_debt():
    """Лимит закрыт под погашение: долговой платёж идёт только своими деньгами.

    Деньги, до которых платёж не дотянулся, видны необеспеченностью с подсказкой
    перевода: они не «отсутствуют нигде», а лежат на другом кошельке.
    """
    s = Scenario(
        accounts=[Account("card", D("0"), is_credit=True, limit=D("1000")),
                  Account("свои", D("400"))],
        payments=[Payment(date(2026, 1, 10), D("500"), account="card", counterparty="x")],
    )
    r = run(s, main="свои")
    assert r.balances["card"] == D("0")             # лимит не тронут
    assert r.hole == D("0")                         # деньги есть, но не на карте
    [u] = r.unsecured
    assert (u.paid, u.short) == (D("0"), D("500"))
    assert u.hint == TransferHint("свои", D("400"))


def test_credit_limit_does_not_fund_a_prepayment():
    """Досрочка долговая всегда: лимитом она не финансируется, только своими."""
    s = Scenario(
        accounts=[Account("card", D("100"), is_credit=True, limit=D("1000"))],
        payments=[Payment(date(2026, 1, 10), D("500"), account="card", counterparty="x", prepaid=True)],
    )
    r = run(s, main="card")
    assert r.balances["card"] == D("0")             # ушло ровно сто — свои деньги
    [u] = r.unsecured
    assert (u.kind, u.paid, u.short) == (KIND_PREPAID, D("100"), D("400"))


def test_prepayment_cannot_be_living():
    """Досрочка всегда долговая: `debt=False` у неё — противоречие, а не выбор."""
    s = Scenario(
        accounts=[Account("main", D("1000"))],
        payments=[Payment(date(2026, 1, 10), D("500"), account="main", counterparty="x", prepaid=True, debt=False)],
    )
    with pytest.raises(ValueError, match="досрочка всегда долговая"):
        run(s, main="main")


def test_transfer_is_not_a_debt_payment():
    """Перевод — не платёж по долгу: свободный лимит к нему применим."""
    s = Scenario(
        accounts=[Account("card", D("0"), is_credit=True, limit=D("1000")),
                  Account("свои", D("0"))],
        transfers=[Transfer(date(2026, 1, 5), D("500"), "card", "свои")],
    )
    r = run(s, main="card")
    assert r.balances == {"card": D("-500"), "свои": D("500")}
    assert r.unsecured == []


def test_unknown_limit_does_not_invent_a_refusal():
    """Лимит кредитного неизвестен — ёмкость не оценена: отказа движок не выдумывает."""
    s = Scenario(
        accounts=[Account("card", D("0"), is_credit=True)],
        payments=[Payment(date(2026, 1, 10), D("500"), account="card", counterparty="x", debt=False)],
    )
    r = run(s, main="card")
    assert r.balances["card"] == D("-500")
    assert r.unsecured == []


def test_unknown_limit_is_no_help_to_a_debt_payment():
    """Долговой платёж лимита не спрашивает: неизвестный его ёмкость не отменяет."""
    s = Scenario(
        accounts=[Account("card", D("100"), is_credit=True)],
        payments=[Payment(date(2026, 1, 10), D("500"), account="card", counterparty="x")],
    )
    r = run(s, main="card")
    assert r.balances["card"] == D("100")           # целиком или никак: ушло ноль
    [u] = r.unsecured
    assert (u.short, r.unsecured_total) == (D("400"), D("500"))


def test_income_comes_before_payment_on_the_same_day():
    """В один день сначала приходит доход, потом уходит платёж — в любом порядке ввода."""
    income = Income(date(2026, 1, 10), D("500"), "main")
    payment = Payment(date(2026, 1, 10), D("500"), account="main", counterparty="x")

    straight = Scenario(accounts=[Account("main", D("0"))],
                        income=[income], payments=[payment])
    swapped = Scenario(accounts=[Account("main", D("0"))],
                       payments=[payment], income=[income])
    for s in (straight, swapped):
        r = run(s, main="main")
        assert r.balances["main"] == D("0")     # доход успел до платежа
        assert r.min_balance == D("0")          # и в минус никто не уходил
        assert r.unsecured == []


def test_transfer_comes_before_payment_on_the_same_day():
    """Перевод в один день с платежом уходит первым: переводят до того, как тратят."""
    s = Scenario(
        accounts=[Account("деньги", D("300")), Account("карта", D("0"))],
        payments=[Payment(date(2026, 1, 5), D("300"), account="карта", counterparty="x")],
        transfers=[Transfer(date(2026, 1, 5), D("300"), "деньги", "карта")],
    )
    r = run(s, main="деньги")
    assert r.balances == {"деньги": D("0"), "карта": D("0")}
    assert r.unsecured == []
    # Порядок дня виден в линии: перевод уходит раньше платежа, который им оплачен.
    assert [(x.account, x.delta) for x in r.timeline] == [
        ("деньги", D("-300")), ("карта", D("300")), ("карта", D("-300"))]


def test_unavailable_wallet_cannot_pay():
    """С арестованной карты не заплатить — ошибка, а не тихий минус."""
    s = Scenario(accounts=[Account("main", D("1000"), available=False)],
                 payments=[Payment(date(2026, 1, 1), D("10"), account="main", counterparty="x")])
    with pytest.raises(ValueError, match="недоступен"):
        run(s, main="main")


def test_hole_is_zero_when_balance_never_goes_negative():
    s = Scenario(accounts=[Account("main", D("500"))],
                 payments=[Payment(date(2026, 1, 10), D("100"), account="main", counterparty="x")])
    r = run(s, main="main")
    assert r.hole == D("0")
    assert r.min_balance == D("400")


# --- прожиточный минимум --------------------------------------------------

def _floor_case(**kw) -> Scenario:
    base = dict(
        accounts=[Account("main", D("10000"))],
        income=[Income(date(2026, 1, 11), D("5000"), "main")],
        payments=[Payment(date(2026, 1, 1), D("9000"), account="main", counterparty="x")],
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
                 payments=[Payment(date(2026, 1, 1), D("100"), account="main", counterparty="x")],
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
                 payments=[Payment(date(2026, 1, 5), D("400"), account="main", counterparty="x")])
    r = run(s, main="main")
    assert optional_cap(r, D("250")) == D("350")
    assert optional_cap(r, D("600")) == D("-0")   # резерв больше остатка


def test_optional_cap_reserves_what_did_not_pass():
    """Потолок частного транша не считает свободными деньги непрошедшего платежа.

    Платёж не отменён: деньги на него уже обещаны, хотя и лежат пока на кошельке.
    """
    s = Scenario(accounts=[Account("main", D("1000"))],
                 payments=[Payment(date(2026, 1, 5), D("1200"), account="main", counterparty="x")])
    r = run(s, main="main")
    assert r.end_balance == D("1000")             # платёж не прошёл — деньги на месте
    assert optional_cap(r, D("0")) == D("-200")   # но они не свободны


def test_cover_cost_yearly_and_daily():
    assert cover_cost(D("-1000"), 10, rate_per_year=D("0.365")) == D("10")
    assert cover_cost(D("-1000"), 10, rate_per_day=D("0.001")) == D("10")
    assert cover_cost(D("1000"), 10, rate_per_day=D("0.001")) == D("10")  # знак не важен
    with pytest.raises(ValueError):
        cover_cost(D("-1000"), 10)


def test_compare_returns_outcomes_in_requested_order():
    base = Scenario(accounts=[Account("main", D("1000"))])
    with_extra = dataclasses.replace(
        base, payments=[Payment(date(2026, 1, 5), D("100"), account="main", counterparty="x")])
    rows = compare({"без траты": base, "с тратой": with_extra},
                   main="main", reserve=D("0"))
    assert [r.label for r in rows] == ["без траты", "с тратой"]
    assert [r.end_balance for r in rows] == [D("1000"), D("900")]
