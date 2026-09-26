"""Тесты кассовой стороны — синтетические фикстуры, без личных данных."""
from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal as D

import pytest

from finance_core import (KIND_PAYMENT, KIND_PREPAID, KIND_TRANSFER, Account,
                          Income, Payment, Scenario, Transfer, TransferHint,
                          compare, cover_cost, roll_cash, run)


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

    Даты у такой дыры нет: события её не создавали, а в `run()` окна нет —
    так держится общее правило даты стартовой дыры: окно есть → `window_start`
    месяца, окна нет → None (другая половина правила —
    `test_roll_cash_hole_on_starting_negative_balance`).
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


def test_floor_gap_counts_the_consumed_minimum():
    """Прожитое с начала линии вычтено из остатка: остаток события его не терял.

    То же воспроизведение аудита 2026-09-22 (тикет 02), но путь `run()`: окна
    нет, точка отсчёта — первое событие линии и накопления нет. На 15-е прожито
    14 дней = 1 400, на руках 3 000 − 1 400 − 1 500 = 100 против требования
    1 700 — нехватка 1 600, а не 200 от сырого остатка.
    """
    s = Scenario(accounts=[Account("main", D("0"))],
                 income=[Income(date(2026, 1, 1), D("3000"), "main"),
                         Income(date(2026, 2, 1), D("3000"), "main")],
                 payments=[Payment(date(2026, 1, 15), D("1500"), account="main",
                                   counterparty="c")],
                 living_floor_monthly=D("3000"))
    r = run(s, main="main")
    assert r.floor_gap == D("1600.00")      # 1 700 − (3 000 − 1 400 − 1 500)
    assert r.floor_gap_date == date(2026, 1, 15)


def test_the_living_floor_rounds_up_so_free_money_never_overstates():
    """Сторона округления минимума выбрана: вверх — свободные деньги занижаются.

    `F × дней / 30` с дробью меньше половины копейки: вверх (`ROUND_CEILING`)
    даёт 233,34 и 1 033,34, половина вверх (`HALF_UP`) дала бы 233,33 и
    1 033,33 — владелец увидел бы на копейку больше свободных, чем есть.
    Сторона записана словами рядом с формулой свободных денег в
    `finance_core/README.md` и `CONTEXT.md`.
    """
    s = Scenario(
        accounts=[Account("main", D("3000"))],
        income=[Income(date(2026, 1, 1), D("500"), "main"),
                Income(date(2026, 1, 7), D("1000"), "main")],
        payments=[Payment(date(2026, 1, 4), D("200"), account="main",
                          counterparty="x")],
        living_floor_monthly=D("1000"),
    )
    r = run(s, main="main")
    months = roll_cash(s, date(2026, 1, 1), max_months=1, main="main")
    # Линия: отрезок 01.01–07.01 включительно — 7 дней, 1 000 × 7/30 =
    # 233,333… → вверх до 233,34: 4 300 − 233,34.
    assert r.free == D("4066.66")
    # Январь целиком: 1 000 × 31/30 = 1 033,333… → 1 033,34: 4 300 − 1 033,34.
    assert months[0].free == D("3266.66")


def test_the_floor_requirement_and_consumed_round_up_by_a_kopek():
    """Требование до прихода и прожитое округляются вверх, а не половина вверх.

    Точка 05.01 при приходе 15.01: требование 10 000 × 10/30 = 3 333,333…,
    прожитое с 01.01 (10 000 × 4/30 = 1 333,333…) вычтено из ликвидности
    800. Половина вверх дала бы 3 333,33 − (800 − 1 333,33) = 3 866,66,
    вверх — 3 866,68: копейка вверх в обеих слагаемых.
    """
    s = Scenario(
        accounts=[Account("main", D("10000"))],
        payments=[Payment(date(2026, 1, 1), D("9000"), account="main",
                          counterparty="x"),
                  Payment(date(2026, 1, 5), D("200"), account="main",
                          counterparty="y")],
        income=[Income(date(2026, 1, 15), D("5000"), "main")],
        living_floor_monthly=D("10000"),
    )
    r = run(s, main="main")
    assert r.floor_gap == D("3866.68")
    assert r.floor_gap_date == date(2026, 1, 5)


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


def test_floor_gap_counts_money_on_other_wallet():
    """Ликвидность всех кошельков — базис: деньги на другом кошельке не голодают.

    Платёж опустошает основной, но 100 000 на другом покрывают остаток месяца:
    раньше floor_gap считал только основной и показывал 22 000 нехватки,
    которых не было (тикет 04). `min_balance`/`end_balance` — метрики основного
    кошелька и не изменились.
    """
    s = Scenario(
        accounts=[Account("main", D("1000")), Account("other", D("100000"))],
        income=[Income(date(2026, 1, 3), D("5000"), "main"),
                Income(date(2026, 1, 25), D("30000"), "main")],
        payments=[Payment(date(2026, 1, 5), D("6000"), account="main",
                          counterparty="x")],
        living_floor_monthly=D("30000"),
    )
    r = run(s, main="main")
    assert r.floor_gap == D("0")
    assert r.floor_gap_date is None
    assert r.min_balance == D("0")          # основной так и опустел
    assert r.end_balance == D("30000")


def test_floor_gap_point_is_every_event_of_the_line():
    """Каждое событие линии — точка оценки: провал на другом кошельке виден.

    Событий на основном нет вовсе: без точки на платеже другого кошелька
    оценки не было бы вовсе (None), а голод до прихода реален.
    """
    s = Scenario(
        accounts=[Account("main", D("0")), Account("other", D("1000"))],
        payments=[Payment(date(2026, 1, 20), D("1000"), account="other",
                          counterparty="x")],
        income=[Income(date(2026, 1, 25), D("30000"), "main")],
        living_floor_monthly=D("30000"),
    )
    r = run(s, main="main")
    # 30 000 × 5/30 = 5 000 до прихода 25-го, денег ноль
    assert r.floor_gap == D("5000.00")
    assert r.floor_gap_date == date(2026, 1, 20)


def test_floor_gap_keeps_the_balance_raw_despite_promise():
    """Обещанное из floor_gap не вычитается: остаток сырой (тикет 04).

    Непрошедший платёж виден в `unsecured` — floor не вычитает его из
    ликвидности второй раз.
    """
    s = Scenario(
        accounts=[Account("main", D("1000"))],
        payments=[Payment(date(2026, 1, 3), D("5000"), account="main",
                          counterparty="x")],
        income=[Income(date(2026, 1, 20), D("30000"), "main")],
        living_floor_monthly=D("30000"),
    )
    r = run(s, main="main")
    assert r.unsecured_total == D("5000")   # деньги не прошли, обещанное видно
    # 30 000 × 17/30 = 17 000 против сырых 1 000; вычет обещанного дал бы
    # 21 000 — непрошедшие деньги ещё на кошельке, прожить на них можно
    assert r.floor_gap == D("16000.00")
    assert r.floor_gap_date == date(2026, 1, 3)


# --- производные величины -------------------------------------------------

def test_line_free_money_subtracts_the_reserve_of_the_scenario():
    """Свободные деньги линии вычитают резерв — порог берётся из сценария."""
    s = Scenario(accounts=[Account("main", D("1000"))],
                 payments=[Payment(date(2026, 1, 5), D("400"), account="main", counterparty="x")],
                 obligation_reserve=D("250"))
    r = run(s, main="main")
    assert r.free == D("350")
    tighter = dataclasses.replace(s, obligation_reserve=D("600"))
    assert run(tighter, main="main").free == D("0")   # резерв больше остатка


def test_line_free_money_excludes_what_did_not_pass():
    """Свободные деньги не считают непрошедший платёж свободным.

    Платёж не отменён: деньги на него уже обещаны, хотя и лежат пока на
    кошельке. Прежний потолок частного транша (`optional_cap`) давал здесь
    те же −200, но без вычета прожиточного минимума и по одному кошельку —
    снесён: одно число, один базис.
    """
    s = Scenario(accounts=[Account("main", D("1000"))],
                 payments=[Payment(date(2026, 1, 5), D("1200"), account="main", counterparty="x")])
    r = run(s, main="main")
    assert r.end_balance == D("1000")             # платёж не прошёл — деньги на месте
    assert r.free == D("-200")                    # но они не свободны


def test_free_money_is_one_number_on_the_line_and_in_the_month():
    """«Сколько свободно» — одно число на одном базисе, а не два ответа.

    Два кошелька, заданный прожиточный минимум и резерв: линия
    (`Result.free`, начало — первое событие, конец — последнее) и месяц
    (`CashMonth.free`, начало — `start` окна, конец — конец месяца) на одном
    и том же январе отвечают одинаково. До правки тут же расходились:
    `optional_cap` (основной кошелёк, без вычета минимума) давал 15 000,
    `CashMonth.free` — −3 600; потолок снесён, ответ один.
    """
    s = Scenario(
        accounts=[Account("main", D("12000")), Account("карта", D("1000"))],
        income=[Income(date(2026, 1, 1), D("5000"), "main")],
        payments=[Payment(date(2026, 1, 31), D("2000"), account="карта",
                          counterparty="x")],
        living_floor_monthly=D("18000"),
        obligation_reserve=D("1000"),
    )
    r = run(s, main="main")
    months = roll_cash(s, date(2026, 1, 1), max_months=1, main="main")
    # 18 000 на двух кошельках − 2 000 обещанного непрошедшего платежа
    # − 18 000 × 31/30 (январь целиком, включительно) − 1 000 резерва
    assert months[0].free == D("-3600.00")
    assert r.free == months[0].free
    assert compare({"январь": s}, main="main")[0].free == r.free


def test_line_without_events_has_no_segment_so_the_floor_is_not_subtracted():
    """Событий вовсе нет — отрезка прожитого нет, минимум в линии не вычитается.

    Точка отсчёта линии — первое событие; событий нет — точки нет, и
    движок её не выдумывает («сегодня» он не спрашивает). Месяц при этом
    копит минимум с начала окна как и раньше: расхождение двух путей ровно
    на прожитое января (18 600 = 18 000 × 31/30).
    """
    s = Scenario(accounts=[Account("main", D("10000"))],
                 living_floor_monthly=D("18000"),
                 obligation_reserve=D("500"))
    r = run(s, main="main")
    assert r.floor_gap is None                # оценивать нечего: точек нет
    assert r.free == D("9500")                # 10 000 − 500: минимум не вычтен
    months = roll_cash(s, date(2026, 1, 1), max_months=1, main="main")
    assert months[0].free == D("-9100.00")    # 10 000 − 18 600 − 500
    assert r.free - months[0].free == D("18600.00")   # разница = прожитое января


def test_compare_reads_free_money_with_the_reserve_from_the_scenario():
    """Сравнение читает свободные деньги в конце линии каждого варианта.

    Порог резерва задаётся только в сценарии — аргумента `reserve` у
    `compare()` больше нет, двух источников одного порога не бывает.
    """
    base = Scenario(accounts=[Account("main", D("1000"))],
                    payments=[Payment(date(2026, 1, 5), D("400"), account="main",
                                      counterparty="x")])
    with_reserve = dataclasses.replace(base, obligation_reserve=D("300"))
    rows = compare({"без резерва": base, "с резервом": with_reserve},
                   main="main")
    assert [row.label for row in rows] == ["без резерва", "с резервом"]
    assert [row.free for row in rows] == [D("600"), D("300")]


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
    rows = compare({"без траты": base, "с тратой": with_extra}, main="main")
    assert [r.label for r in rows] == ["без траты", "с тратой"]
    assert [r.end_balance for r in rows] == [D("1000"), D("900")]
