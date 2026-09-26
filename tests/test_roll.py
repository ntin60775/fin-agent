"""Тесты проката сделок — синтетические фикстуры, без личных данных."""
from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

from finance_core import (EXPECTED, LEGAL, OWED_TO_ME, PAID, PAID_LATE,
                          PAYOFF_CLOSED_BEFORE, PAYOFF_NOT_CLOSED, POSTPONED,
                          SKIPPED, AVALANCHE, Assignment, Counterparty, Deal,
                          FirstPayment, Movement, OccurrenceEdit, ScheduleRule,
                          Settlements, Wallet, accrued_interest,
                          compare_deal_strategies, deal_balance, occurrences,
                          roll_deals, validate)
from finance_core import (WINDOW_DEBTS_CLOSED, WINDOW_INCOME_ENDS,
                          WINDOW_MONTH_CAP, roll_window)
from finance_core.roll import _DealsRoll

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
          assignments=()) -> Settlements:
    if wallets is None:
        wallets = [Wallet("карта", "Карта", "карта", D("0"))]
    return Settlements(
        counterparties=[_counterparty(), *counterparties],
        wallets=list(wallets),
        deals=list(deals),
        movements=list(movements),
        edits=list(edits),
        assignments=list(assignments),
    )


def _rule(days=(20,), **kw) -> ScheduleRule:
    return ScheduleRule(days=days, **kw)


# --- формы правила графика -------------------------------------------------

def test_fixed_payment_with_a_count_gives_that_many_occurrences():
    """Фиксированная сумма с числом платежей: сколько раз — столько вхождений."""
    deal = _deal(amount=D("12000"), schedule=_rule(start=START, payment=D("1000"),
                                                   count=12, shift_weekend=False))
    book = _book(deal)
    validate(book)
    found = occurrences(book, "заём", START, date(2027, 12, 31))
    assert [o.planned for o in found] == [date(2026, month, 20)
                                          for month in range(1, 13)]
    assert all(o.amount == D("1000") for o in found)


def test_percent_rule_leaves_the_amount_to_the_roll():
    """Минималка процентом от остатка: сумма вхождения известна только в прокате."""
    deal = _deal(amount=D("100000"), schedule=_rule(percent=D("0.05")))
    book = _book(deal)
    validate(book)
    assert [o.amount for o in occurrences(book, "заём", START, date(2026, 1, 31))] == [None]
    roll = roll_deals(book, START, D(0), max_months=1)
    assert roll.months[0].payments[0].amount == D("5000.00")


def test_several_days_give_several_occurrences_in_a_month():
    """Несколько дней в месяце: сколько дней, столько вхождений."""
    deal = _deal(amount=D("24000"), schedule=_rule(days=(5, 20), payment=D("1000")))
    book = _book(deal)
    validate(book)
    assert [o.planned for o in occurrences(book, "заём", START, date(2026, 1, 31))] == [
        date(2026, 1, 5), date(2026, 1, 20)]


def test_separate_first_payment_stands_before_the_row():
    """Отдельный первый платёж: своя дата и сумма, в число платежей не входит."""
    deal = _deal(amount=D("10000"),
                 schedule=_rule(start=date(2026, 2, 1), payment=D("1000"),
                                first=FirstPayment(date(2026, 1, 5), D("3000"))))
    book = _book(deal)
    validate(book)
    assert [(o.planned, o.amount)
            for o in occurrences(book, "заём", START, date(2026, 3, 31))] == [
        (date(2026, 1, 5), D("3000")),
        (date(2026, 2, 20), D("1000")),
        (date(2026, 3, 20), D("1000")),
    ]


# --- правки вхождений ------------------------------------------------------

def test_edits_move_skip_and_resize_an_occurrence():
    """Поверх правила вхождение правится: перенести, пропустить, сменить сумму."""
    deal = _deal(amount=D("3000"), schedule=_rule(payment=D("1000")))
    book = _book(deal, edits=[
        OccurrenceEdit("заём", date(2026, 1, 20), postponed=True,
                       moved_to=date(2026, 2, 5)),
        OccurrenceEdit("заём", date(2026, 2, 20), skipped=True),
        OccurrenceEdit("заём", date(2026, 3, 20), amount=D("1500")),
    ])
    validate(book)
    jan, feb, mar = occurrences(book, "заём", START, date(2026, 4, 30))[:3]
    assert jan.status == POSTPONED and jan.due == date(2026, 2, 5)
    assert feb.status == SKIPPED
    assert mar.amount == D("1500") and mar.status == EXPECTED


def test_later_edit_wins_field_by_field():
    """Поздняя правка побеждает по своим полям: переговорили — действует новая."""
    deal = _deal(amount=D("1000"), schedule=_rule(payment=D("1000")))
    book = _book(deal, edits=[
        OccurrenceEdit("заём", date(2026, 1, 20), postponed=True,
                       moved_to=date(2026, 2, 1)),
        OccurrenceEdit("заём", date(2026, 1, 20), postponed=True,
                       moved_to=date(2026, 3, 1)),
    ])
    validate(book)
    assert occurrences(book, "заём", START, date(2026, 3, 31))[0].due == date(2026, 3, 1)


def test_occurrence_edits_are_received_not_stored():
    """Правки приходят снаружи и применяются к порождённым: движок их не хранит."""
    deal = _deal(amount=D("1000"), schedule=_rule(payment=D("1000")))
    book = _book(deal, edits=[OccurrenceEdit("заём", date(2026, 1, 20), postponed=True,
                                             moved_to=date(2026, 3, 10))])
    validate(book)
    assert occurrences(book, "заём", START, date(2026, 3, 31))[0].due == date(2026, 3, 10)

    book.edits.clear()                      # правку убрали — вхождение снова на месте
    assert occurrences(book, "заём", START, date(2026, 3, 31))[0].due == date(2026, 1, 20)
    assert book.deals[0].schedule.days == (20,)     # правило правка не тронула


# --- три даты и статус -----------------------------------------------------

def test_occurrence_has_three_dates_and_a_status():
    """У вхождения три даты — плановая, перенесённая, фактическая — и статус."""
    deal = _deal(amount=D("2000"), schedule=_rule(payment=D("1000")))
    book = _book(deal, movements=[Movement(date(2026, 1, 25), D("1000"), "заём",
                                           occurrence=date(2026, 1, 20))])
    validate(book)
    occ = occurrences(book, "заём", START, date(2026, 1, 31))[0]
    assert (occ.planned, occ.moved, occ.actual) == (date(2026, 1, 20), None,
                                                    date(2026, 1, 25))
    assert occ.status == PAID_LATE
    assert occ.remaining == D("0") and not occ.payable


def test_all_five_statuses_come_from_edits_and_movements():
    """Статус производен: ожидается, исполнен, с опозданием, пропущен, перенесён."""
    deal = _deal(amount=D("5000"), schedule=_rule(days=(5, 6, 7, 8, 9),
                                                  payment=D("1000")))
    book = _book(deal,
                 movements=[Movement(date(2026, 1, 5), D("1000"), "заём",
                                     occurrence=date(2026, 1, 5)),
                            Movement(date(2026, 1, 8), D("1000"), "заём",
                                     occurrence=date(2026, 1, 7))],
                 edits=[OccurrenceEdit("заём", date(2026, 1, 8), skipped=True),
                        OccurrenceEdit("заём", date(2026, 1, 9), postponed=True,
                                       moved_to=date(2026, 2, 2))])
    validate(book)
    assert [(o.planned, o.status)
            for o in occurrences(book, "заём", START, date(2026, 1, 31))] == [
        (date(2026, 1, 5), PAID),
        (date(2026, 1, 6), EXPECTED),
        (date(2026, 1, 7), PAID_LATE),
        (date(2026, 1, 8), SKIPPED),
        (date(2026, 1, 9), POSTPONED),
    ]


# --- прокат читает вхождения, а не правило ---------------------------------

def test_skipped_occurrence_does_not_reduce_the_balance():
    """Пропущенное не уменьшает остаток: долг просто закрывается позже."""
    deal = _deal(amount=D("2000"), schedule=_rule(payment=D("1000")))
    book = _book(deal, edits=[OccurrenceEdit("заём", date(2026, 1, 20), skipped=True)])
    validate(book)
    roll = roll_deals(book, START, D(0), max_months=4)
    assert roll.months[0].paid == D("0")
    assert roll.months[0].total == D("2000")
    assert roll.months[1].paid == D("1000")
    assert roll.freedom == date(2026, 3, 1)


def test_postponed_occurrence_is_not_doubled():
    """Перенесённое не удваивается: платится один раз — в новый месяц."""
    deal = _deal(amount=D("1000"), schedule=_rule(payment=D("1000")))
    book = _book(deal, edits=[OccurrenceEdit("заём", date(2026, 1, 20), postponed=True,
                                             moved_to=date(2026, 2, 10))])
    validate(book)
    roll = roll_deals(book, START, D(0), max_months=3)
    assert roll.months[0].paid == D("0")
    assert [(p.date, p.amount) for p in roll.months[1].payments] == [
        (date(2026, 2, 10), D("1000"))]
    assert roll.total_paid == D("1000")


def test_occurrence_paid_early_is_not_counted_twice():
    """Исполненное заранее не считается дважды: деньги уже в остатке."""
    deal = _deal(amount=D("2000"), schedule=_rule(payment=D("1000")))
    book = _book(deal, movements=[Movement(date(2026, 1, 5), D("1000"), "заём",
                                           occurrence=date(2026, 2, 20))])
    validate(book)
    assert occurrences(book, "заём", START, date(2026, 3, 31))[1].status == PAID
    roll = roll_deals(book, START, D(0), max_months=3)
    assert roll.total_paid == D("1000")             # остаток долга, а не два платежа
    assert roll.freedom == date(2026, 1, 1)


def test_movement_without_a_link_does_not_settle_an_occurrence():
    """Движение без ссылки вхождение не закрывает: это досрочка, ряд идёт своим чередом."""
    deal = _deal(amount=D("2000"), schedule=_rule(payment=D("1000")))
    loose = _book(deal, movements=[Movement(date(2026, 1, 5), D("1000"), "заём")])
    validate(loose)
    assert occurrences(loose, "заём", START, date(2026, 1, 31))[0].status == EXPECTED
    january = roll_deals(loose, START, D(0), max_months=3)
    assert january.months[0].paid == D("1000")      # досрочка платежа не отменяет

    linked = _book(deal, movements=[Movement(date(2026, 1, 5), D("1000"), "заём",
                                             occurrence=date(2026, 1, 20))])
    validate(linked)
    settled = roll_deals(linked, START, D(0), max_months=3)
    assert settled.months[0].paid == D("0")         # вхождение закрыто движением
    assert settled.months[1].paid == D("1000")      # и остаток — это февральское вхождение


# --- календарь -------------------------------------------------------------

def test_day_missing_in_month_is_the_last_day():
    """Дня, которого нет в месяце, не бывает — берётся последний день месяца."""
    deal = _deal(amount=D("1000"), schedule=_rule(days=(31,), payment=D("1000")))
    book = _book(deal)
    validate(book)
    assert [o.planned for o in occurrences(book, "заём", date(2026, 4, 1),
                                           date(2026, 4, 30))] == [date(2026, 4, 30)]


def test_weekend_shifts_the_payment_forward_and_holidays_are_not_counted():
    """Выходной сдвигает платёж вперёд; праздник выходным не считается."""
    deal = _deal(amount=D("2000"), schedule=_rule(days=(1, 3), payment=D("1000")))
    book = _book(deal)
    validate(book)
    # 1 января — праздник, но четверг: календаря праздников у движка нет.
    # 3 января — суббота: платёж переезжает на понедельник, 5-е.
    assert [o.planned for o in occurrences(book, "заём", START, date(2026, 1, 31))] == [
        date(2026, 1, 1), date(2026, 1, 5)]

    off = _book(_deal(uid="без-сдвига", schedule=_rule(days=(3,), payment=D("1000"),
                                                       shift_weekend=False)))
    validate(off)
    assert [o.planned for o in occurrences(off, "без-сдвига", START,
                                           date(2026, 1, 31))] == [date(2026, 1, 3)]


def test_weekend_shifts_the_income_backward():
    """Выходной переезжает по стороне денег: у требования — назад, к пятнице.

    Сторона платежа проверена рядом:
    `test_weekend_shifts_the_payment_forward_and_holidays_are_not_counted`.
    """
    claim = _deal(uid="требование", amount=D("2000"), direction=OWED_TO_ME,
                  schedule=_rule(days=(3,), payment=D("1000")))
    book = _book(claim)
    validate(book)
    # 3 января — суббота: доход приходит в пятницу, 2-го, а не в понедельник.
    assert [o.planned for o in occurrences(book, "требование", START,
                                           date(2026, 1, 31))] == [date(2026, 1, 2)]


def test_overdue_occurrence_is_paid_in_the_first_month_not_in_the_past():
    """Просроченное вхождение платится в первый месяц проката, а не датой в прошлом."""
    deal = _deal(amount=D("3000"), schedule=_rule(start=START, payment=D("1000")))
    book = _book(deal)
    validate(book)
    roll = roll_deals(book, date(2026, 4, 1), D(0), max_months=6)
    april = roll.months[0]
    assert april.month == date(2026, 4, 1)
    assert april.paid == D("3000")                  # январь, февраль, март — долг
    assert [p.date for p in april.payments] == [date(2026, 4, 20)] * 3
    assert roll.freedom == date(2026, 4, 1)


def test_first_payment_before_the_start_is_still_rolled():
    """Отдельный первый платёж — часть графика: окно вхождений его не теряет."""
    deal = _deal(amount=D("3000"),
                 schedule=_rule(start=date(2026, 2, 1), payment=D("1000"),
                                first=FirstPayment(date(2026, 1, 5), D("1000"))))
    book = _book(deal)
    validate(book)
    roll = roll_deals(book, date(2026, 3, 1), D(0), max_months=6)
    assert roll.months[0].paid == D("3000")
    assert roll.freedom == date(2026, 3, 1)


def test_days_that_collapse_in_a_short_month_are_rejected():
    """30-е и 31-е в феврале — одна дата: вхождения задвоятся, опознать нечем."""
    with pytest.raises(ValueError, match="схлопываются"):
        validate(_book(_deal(schedule=_rule(days=(30, 31), payment=D("1000")))))


def test_days_that_shift_onto_one_date_are_rejected():
    """Два дня, съехавших на один понедельник, — тоже задвоение."""
    # 3 и 4 января 2026 — суббота и воскресенье: оба сдвигаются на 5-е.
    book = _book(_deal(schedule=_rule(days=(3, 4), payment=D("1000"))))
    validate(book)              # по дням правило проходит: 3 и 4 не совпадают
    with pytest.raises(ValueError, match="два вхождения на"):
        occurrences(book, "заём", START, date(2026, 1, 31))


def test_payment_count_follows_the_calendar_not_the_tuple_order():
    """Число платежей отсчитывается по календарю: (20, 5) — это 5-е, потом 20-е."""
    deal = _deal(amount=D("2000"), schedule=_rule(days=(20, 5), start=START,
                                                  payment=D("1000"), count=1))
    book = _book(deal)
    validate(book)
    assert [o.planned for o in occurrences(book, "заём", START,
                                           date(2026, 2, 28))] == [date(2026, 1, 5)]


# --- регулярный расход -----------------------------------------------------

def test_regular_expense_pays_but_never_closes_or_is_prepaid():
    """Сделка без остатка: платёж входит в нагрузку, но на дату выхода не влияет."""
    rent = _deal(uid="аренда", amount=None, counterparty="арендодатель",
                 schedule=_rule(days=(5,), payment=D("500")))
    loan = _deal(uid="заём", amount=D("1000"), schedule=_rule(payment=D("1000")))
    book = _book(rent, loan,
                 counterparties=[_counterparty(uid="арендодатель", name="Арендодатель",
                                               subtype="прочее")])
    validate(book)
    roll = roll_deals(book, START, D("5000"), max_months=6)
    assert roll.freedom == date(2026, 1, 1)         # заём закрылся, аренда срок не держит
    assert roll.months[0].paid == D("1500")         # 500 аренды + 1000 займа
    assert [p.amount for p in roll.months[0].payments if p.deal == "аренда"] == [D("500")]
    assert roll.months[0].prepaid == D("0")         # свободные деньги её не досрочили


def test_scheduled_payment_knows_whether_it_is_debt():
    """Признак — из модели: сделка с остатком платит долг, регулярный расход — нет."""
    rent = _deal(uid="аренда", amount=None, counterparty="арендодатель",
                 schedule=_rule(days=(5,), payment=D("500")))
    loan = _deal(uid="заём", amount=D("1000"), schedule=_rule(payment=D("500")))
    book = _book(rent, loan,
                 counterparties=[_counterparty(uid="арендодатель", name="Арендодатель",
                                               subtype="прочее")])
    by_deal = {p.deal: p for p in roll_deals(book, START, D("0"),
                                             max_months=1).months[0].payments}
    assert by_deal["заём"].debt is True
    assert by_deal["аренда"].debt is False


def test_prepayment_is_a_debt_payment():
    """Досрочка долговая всегда: её движок направляет на долг."""
    loan = _deal(uid="заём", amount=D("1000"), schedule=_rule(payment=D("500")))
    book = _book(loan)
    payments = roll_deals(book, START, D("300"), max_months=1).months[0].payments
    early = [p for p in payments if p.planned is None]
    assert early and all(p.debt for p in early)


# --- копилка ---------------------------------------------------------------

def test_closure_unit_is_a_piggy_bank():
    """Копилка: платежи копятся в котёл и падают в остаток участника, цель не растёт, закрытие разом."""
    first = _deal(uid="первый", amount=D("2000"), rate_per_year=D("0.5"),
                  closure_unit="копилка", schedule=_rule(payment=D("1000")))
    second = _deal(uid="второй", amount=D("1000"), closure_unit="копилка",
                   schedule=_rule(payment=D("1000")))
    book = _book(first, second)
    validate(book)
    roll = roll_deals(book, START, D(0), max_months=6)

    assert roll.months[0].units["копилка"].target == D("3000")
    assert roll.months[0].units["копилка"].pot == D("2000")
    # Остаток участника — канон: упал платежами (2 000 − 1 000) и начислился
    # (2 000 × 0,5 / 12 = 83,33) — то же число, что расчётный остаток.
    assert roll.months[0].balances["первый"] == D("1083.33")
    assert roll.months[1].units["копилка"].closed
    assert all(m.units["копилка"].target == D("3000") for m in roll.months)
    assert roll.months[1].balances["первый"] == D("0")      # закрылись разом
    assert roll.months[1].balances["второй"] == D("0")
    # Проценты идут в остаток участника, а не в котёл: котёл ровно на
    # платежах (2 000), цель — ровно на телах (3 000), ни то ни другое
    # процентами не выросло.
    assert roll.total_interest == D("128.47")               # 83,33 + 45,14
    assert {p.unit for p in roll.months[0].payments} == {"копилка"}   # деньги идут в котёл


def test_unit_is_incomplete_when_a_member_is_a_gap():
    """Копилка закрывается вся целиком: неполная единица в прокат не идёт."""
    first = _deal(uid="первый", amount=D("2000"), rate_per_year=None,
                  closure_unit="копилка", schedule=_rule(payment=D("1000")))
    second = _deal(uid="второй", amount=D("1000"), closure_unit="копилка",
                   schedule=_rule(payment=D("1000")))
    book = _book(first, second)
    validate(book)
    roll = roll_deals(book, START, D(0))
    reasons = {g.deal: g.reason for g in roll.gaps}
    assert sorted(reasons) == ["второй", "первый"]
    assert "ставка" in reasons["первый"]            # у кого пробел — своя причина
    assert "единица закрытия" in reasons["второй"]  # а чистого тянет за собой единица
    assert roll.months[0].total == D("0")


def test_unit_pot_on_opening_is_what_was_paid():
    """Котёл на открытие окна — уплаченное по участнику, а не цель минус канон.

    Участник с историей платежей и ставкой: канон включает проценты, а в
    копилку проценты не копятся — туда идут деньги. Поэтому котёл считается от
    тела на открытие окна, той же датой, что и остаток участника.
    """
    deal = _deal(uid="участник", amount=D("20000"), start=date(2025, 6, 1),
                 rate_per_year=D("0.24"), rate_per_day=None,
                 closure_unit="копилка", schedule=_rule(payment=D("1000")))
    book = _book(deal, movements=[Movement(date(2025, 12, 15), D("5000"),
                                           "участник")])
    validate(book)
    # Канон на конец декабря: 20 000 − 5 000 + проценты 2 973,70.
    assert deal_balance(book, "участник", date(2025, 12, 31)) == D("17973.70")
    roll = roll_deals(book, START, D(0), max_months=1)
    unit = roll.months[0].units["копилка"]
    assert unit.target == D("20000")
    assert unit.pot == D("6000")            # 5 000 уплачено до окна + 1 000 января
    assert unit.remaining == D("14000")


def test_unit_pot_takes_an_in_window_movement_in_its_own_month():
    """Движение внутри окна входит в котёл своим месяцем — и ровно один раз."""
    deal = _deal(uid="участник", amount=D("20000"), start=date(2025, 6, 1),
                 rate_per_year=D("0.24"), rate_per_day=None,
                 closure_unit="копилка", schedule=_rule(payment=D("1000")))
    book = _book(deal,
                 movements=[Movement(date(2025, 12, 15), D("5000"), "участник"),
                            Movement(date(2026, 1, 10), D("500"), "участник")])
    validate(book)
    roll = roll_deals(book, START, D(0), max_months=1)
    unit = roll.months[0].units["копилка"]
    assert unit.pot == D("6500")            # 5 000 + 500 января + 1 000 платежа
    assert unit.remaining == D("13500")


def test_pot_member_balance_falls_with_pot_payments_and_equals_the_canon():
    """Остаток участника копилки падает платежами и равен расчётному остатку.

    Платежи проката записываются движениями по сделке — так их записывает зона,
    и на них уже не прокат считает, а канон остатка (тот же путь, что у обычной
    сделки в тесте канона). После записи остаток участника на конец месяца и
    расчётный остаток — одно число: обязательные платежи, досрочка в котёл
    (числится за первым участником) и начисление проводятся одинаково.
    """
    first = _deal(uid="первый", amount=D("20000"), start=date(2025, 6, 1),
                  rate_per_year=D("0.24"), rate_per_day=None,
                  closure_unit="копилка", schedule=_rule(payment=D("1000")))
    second = _deal(uid="второй", amount=D("10000"), start=date(2025, 6, 1),
                   rate_per_year=D("0.24"), rate_per_day=None,
                   closure_unit="копилка", schedule=_rule(payment=D("500")))
    book = _book(first, second,
                 movements=[Movement(date(2025, 12, 15), D("5000"), "первый")])
    validate(book)
    opening = deal_balance(book, "первый", date(2025, 12, 31))
    # Бюджет досрочек уходит в котёл — единица не набирается и не закрывается.
    roll = roll_deals(book, START, D("2000"), max_months=3)

    assert roll.months[0].balances["первый"] < opening    # остаток падает
    assert not roll.months[2].units["копилка"].closed     # единица открыта
    for index, month in enumerate(roll.months):
        for payment in month.payments:
            book.movements.append(Movement(payment.date, payment.amount,
                                           payment.deal,
                                           occurrence=payment.planned))
        on = (date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31))[index]
        assert month.balances["первый"] == \
            deal_balance(book, "первый", on)
        assert month.balances["второй"] == \
            deal_balance(book, "второй", on)


def test_pot_member_balance_and_accrual_are_kopeks_like_the_roll():
    """Остаток и начисление участника копилки округляются тем же кодом, что прокат.

    `model.kopek` — копейка, половина вверх (`HALF_UP`): 10 000 × 0,1249 / 12 =
    104,0833… идёт в остаток 104,08 (округление вверх дало бы 104,09), а ровно
    половина копейки — 10 000 × 0,12495 / 12 = 104,125 — вверх до 104,13.
    """
    def _roll(rate: D) -> tuple[D, D]:
        member = _deal(uid="участник", amount=D("10000"),
                       closure_unit="копилка", rate_per_year=rate,
                       rate_per_day=None, schedule=_rule(payment=D("1000")))
        book = _book(member)
        validate(book)
        roll = roll_deals(book, START, D(0), max_months=1)
        return (roll.months[0].interest,
                roll.months[0].balances["участник"])

    interest, balance = _roll(D("0.1249"))
    assert interest == D("104.08")                # третья цифра 3 — вниз
    assert balance == D("9104.08")                # 10 000 + 104,08 − 1 000
    assert balance == balance.quantize(D("0.01"))  # остаток в целых копейках
    assert _roll(D("0.12495"))[0] == D("104.13")  # ровно половина — вверх


def test_an_inside_window_assignment_enters_the_member_balance_and_the_target():
    """Цессия внутри окна проведена и в остаток участника, и в цель единицы.

    Дельта-ревью тикета 06 (N8): остаток проката стоял на теле, канон рос, а
    цель единицы дельты не видела. Теперь остаток участника — канон на каждую
    дату, цель — сумма актуальных тел участников, а денег от цессии в котёл не
    приходит: передача меняет тело, а не платежи.
    """
    member = _deal(uid="участник", amount=D("120000"), counterparty="коллектор",
                   start=date(2025, 6, 1), rate_per_year=D("0.24"),
                   rate_per_day=None, closure_unit="копилка",
                   schedule=_rule(payment=D("1000")))
    book = _book(
        member,
        counterparties=[_counterparty(uid="коллектор", name="Коллектор",
                                      subtype="ПКО")],
        assignments=[Assignment(date(2026, 3, 1), "участник", from_holder="банк",
                                to_holder="коллектор", amount=D("100000"),
                                delta=D("20000"))])
    validate(book)
    roll = roll_deals(book, START, D(0), max_months=3)

    # Цель до передачи — прежнее тело, с марта — тело с дельтой.
    assert [m.units["копилка"].target for m in roll.months] == [
        D("100000"), D("100000"), D("120000")]
    # Деньги в котёл приходят только платежами: дельта в котёл не идёт.
    assert [m.units["копилка"].pot for m in roll.months] == [
        D("1000"), D("2000"), D("3000")]
    # Остаток участника — канон на каждый конец месяца, месяц передачи включительно.
    for index, month in enumerate(roll.months):
        for payment in month.payments:
            book.movements.append(Movement(payment.date, payment.amount,
                                           payment.deal,
                                           occurrence=payment.planned))
        on = (date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31))[index]
        assert month.balances["участник"] == \
            deal_balance(book, "участник", on)


def test_repeating_the_month_step_keeps_the_transfer_delta_once():
    """Повтор шага месяца проводит дельту цессии в цель единицы ровно один раз.

    Драйвер повторяет шаг, пока бюджет месяца не перестанет меняться
    (`entry` → `begin` → `restore` → `begin`), — снимок обязан вернуть и цель:
    без этого дельта передачи внутри окна задвоилась бы (цель 120 000 →
    140 000), единица закрылась бы раньше и все последующие остатки и `short`
    сдвинулись. Повтор того же шага обязан дать те же числа.
    """
    member = _deal(uid="участник", amount=D("120000"), counterparty="коллектор",
                   start=date(2025, 6, 1), rate_per_year=D("0.24"),
                   rate_per_day=None, closure_unit="копилка",
                   schedule=_rule(payment=D("1000")))
    book = _book(
        member,
        counterparties=[_counterparty(uid="коллектор", name="Коллектор",
                                      subtype="ПКО")],
        assignments=[Assignment(date(2026, 1, 15), "участник", from_holder="банк",
                                to_holder="коллектор", amount=D("100000"),
                                delta=D("20000"))])
    validate(book)
    deals = _DealsRoll(book, START, strategy=AVALANCHE,
                       consent_to_second=False, max_months=3)
    snap = deals.entry()
    deals.begin(1, D(0))
    first = (deals.open_units["копилка"].target,
             deals.open_deals["участник"].balance,
             deals.open_units["копилка"].pot,
             deals.total_interest)
    deals.restore(snap)
    deals.begin(1, D(0))
    assert (deals.open_units["копилка"].target,
            deals.open_deals["участник"].balance,
            deals.open_units["копилка"].pot,
            deals.total_interest) == first
    # Цель — тело с одной дельтой (100 000 + 20 000); остаток — канон открытия
    # плюс начисление января плюс дельта минус платёж в котёл:
    # 114 868,56 + 2 697,37 + 20 000 − 1 000. Котёл — деньги, дельта в него не идёт.
    assert first == (D("120000"), D("136565.93"), D("1000"), D("2697.37"))


# --- требования и приоритеты -----------------------------------------------

def test_receivables_are_listed_as_expectations_and_not_rolled():
    """Прокат гасит только то, что должен я; «мне должны» — отдельным ожиданием."""
    claim = _deal(uid="требование", amount=D("3000"), direction=OWED_TO_ME,
                  schedule=_rule(payment=D("1000")))
    loan = _deal(uid="заём", amount=D("3000"), schedule=_rule(payment=D("1000")))
    book = _book(claim, loan)
    validate(book)
    roll = roll_deals(book, START, D(0), max_months=6)
    assert [(e.date, e.amount) for e in roll.expectations] == [
        (date(2026, 1, 20), D("1000")),
        (date(2026, 2, 20), D("1000")),
        (date(2026, 3, 20), D("1000")),
    ]
    assert roll.months[0].paid == D("1000")         # требование в кассу не подмешано
    assert "требование" not in roll.months[0].balances


def test_second_priority_is_offered_not_paid():
    """Второй приоритет сам не платится: показывается остаток и предложение."""
    schedule_deal = _deal(uid="график", amount=D("3000"), schedule=_rule(payment=D("1000")))
    claim = _deal(uid="взыскание", amount=D("5000"), second_priority=True,
                  schedule=None)
    book = _book(schedule_deal, claim)
    validate(book)
    offered = roll_deals(book, START, D("4000"), max_months=6)
    assert offered.months[0].offer == D("2000")     # срез: 4 000 свободных − 2 000 ушедших
    assert offered.months[0].prepaid == D("2000")
    assert offered.months[0].balances["взыскание"] == D("5000")

    agreed = roll_deals(book, START, D("4000"), max_months=6, consent_to_second=True)
    assert agreed.months[0].offer == D("0")
    assert agreed.months[0].balances["взыскание"] == D("3000")
    assert agreed.months[0].paid == D("5000")


def test_offer_is_a_slice_of_free_money_not_the_pool_leftover():
    """Предложение — срез свободных денег, а не свой расчёт по бюджету.

    На третьем месяце обязательный платёж урезан остатком сделки (500 из
    1 000): неоплаченная база освобождает остаток пула до 4 500, но в
    свободные деньги она не входит. Срез — 4 000 переданного бюджета минус
    ушедшие досрочки; остаток пула в него не попадает.
    """
    tight = _deal(uid="узкий", amount=D("2500"),
                  schedule=_rule(payment=D("1000")), prepay=False)
    claim = _deal(uid="взыскание", amount=D("5000"), second_priority=True,
                  schedule=None)
    book = _book(tight, claim)
    validate(book)
    roll = roll_deals(book, START, D("4000"), max_months=6)
    third = roll.months[2]
    assert third.short == D("500")            # урезано: остаток 500 < платежа 1000
    assert third.prepaid == D("0")            # досрочек не было
    assert third.offer == D("4000")           # срез: 4 000 свободных, а не 4 500 пула


def test_second_priority_grows_while_it_waits():
    """Ждать согласия не бесплатно: второй приоритет растёт по ставке сделки."""
    claim = _deal(uid="взыскание", amount=D("12000"), rate_per_year=D("0.12"),
                  second_priority=True)
    book = _book(claim)
    validate(book)
    roll = roll_deals(book, START, D(0), max_months=2)
    assert roll.months[0].balances["взыскание"] == D("12120.00")
    assert roll.months[0].paid == D("0")            # но сам не платится
    assert roll.months[0].offer == D("0")           # свободных денег ноль — предлагать нечего


def test_second_priority_is_not_paid_by_its_own_schedule():
    """Второй приоритет платится не по графику: график у него есть, а платежа нет."""
    scheduled = _deal(uid="график", amount=D("1000"),
                      schedule=_rule(days=(5,), payment=D("1000")))
    claim = _deal(uid="взыскание", amount=D("5000"), second_priority=True,
                  schedule=_rule(payment=D("1000")))
    book = _book(scheduled, claim)
    validate(book)
    roll = roll_deals(book, START, D("5000"), max_months=2)
    assert roll.months[0].paid == D("1000")                 # только обязательный график
    assert roll.months[0].balances["взыскание"] == D("5000")
    assert roll.months[0].offer == D("5000")                # а свободные деньги предложены


def test_second_priority_is_paid_to_the_end_when_agreed():
    """Согласие считается до конца: видно, когда закроется второй приоритет.

    Без согласия прокат кончается свободой первого приоритета — закрывать второй
    некому, и даты его закрытия не существует.
    """
    schedule_deal = _deal(uid="график", amount=D("6000"),
                          schedule=_rule(payment=D("1000")))
    claim = _deal(uid="взыскание", amount=D("3000"), second_priority=True,
                  schedule=None)
    book = _book(schedule_deal, claim)
    validate(book)

    offered = roll_deals(book, START, D("1000"), max_months=24)
    assert offered.freedom == date(2026, 3, 1)
    assert offered.second_freedom is None

    agreed = roll_deals(book, START, D("1000"), max_months=24,
                        consent_to_second=True)
    assert agreed.freedom == date(2026, 3, 1)       # первый приоритет не переехал
    assert agreed.second_freedom == date(2026, 5, 1)
    assert agreed.months[-1].balances["взыскание"] == D("0.00")


def test_unclosed_second_priority_does_not_mark_the_roll_stalled():
    """`stalled` — про первый приоритет: второй закрывается и после его свободы."""
    schedule_deal = _deal(uid="график", amount=D("6000"),
                          schedule=_rule(payment=D("1000")))
    claim = _deal(uid="взыскание", amount=D("3000"), second_priority=True,
                  schedule=None)
    book = _book(schedule_deal, claim)
    validate(book)
    roll = roll_deals(book, START, D("1200"), max_months=3,
                      consent_to_second=True)
    assert roll.freedom == date(2026, 3, 1)         # первый приоритет закрылся
    assert roll.second_freedom is None              # а второй за три месяца не успел
    assert roll.stalled is False


def test_percent_minimum_counts_from_the_month_start():
    """Минималка процентом считается от остатка на начало месяца, а не от текущего."""
    deal = _deal(amount=D("1000"), schedule=_rule(days=(5, 20), percent=D("0.5")))
    book = _book(deal)
    validate(book)
    jan = roll_deals(book, START, D(0), max_months=1).months[0]
    assert [p.amount for p in jan.payments] == [D("500.00"), D("500.00")]
    assert jan.balances["заём"] == D("0.00")


# --- урезанный обязательный платёж ------------------------------------------

def test_cut_percent_payment_names_the_shortfall():
    """Минималка процентом урезана пулом: недоплата — число в месяце, не молчание."""
    deal = _deal(amount=D("10000"), rate_per_year=D("0.36"),
                 schedule=_rule(percent=D("0.1")))
    book = _book(deal)
    validate(book)
    roll = roll_deals(book, START, D(0), max_months=2)
    jan, feb = roll.months
    assert jan.interest == D("300.00")       # 10 000 × 36 % / 12
    assert jan.paid == D("1000")             # пул: 10 % от остатка на старте проката
    assert jan.short == D("30.00")           # want 1 030 − 1 000
    assert feb.short == D(0)                 # месяц без урезания — ноль


def test_short_is_zero_when_pool_covers_want():
    """Пул покрывает want: урезания нет — short ноль, paid прежний."""
    deal = _deal(amount=D("100000"), schedule=_rule(percent=D("0.05")))
    book = _book(deal)
    validate(book)
    jan = roll_deals(book, START, D(0), max_months=1).months[0]
    assert jan.short == D(0)
    assert jan.paid == D("5000.00")


def test_short_sees_balance_cut_after_prepayment():
    """Досрочка съела остаток: фиксированный платёж урезан по остатку — видно."""
    deal = _deal(amount=D("10000"),
                 schedule=_rule(payment=D("3000"), count=4, start=START))
    book = _book(deal)
    validate(book)
    roll = roll_deals(book, START, D("5000"), max_months=3)
    assert roll.months[0].short == D(0)          # январь: пул покрыл и досрочил
    assert roll.months[1].short == D("1000.00")  # февраль: want 3 000 при остатке 2 000


def test_short_sees_the_piggy_bank_room():
    """Копилка: платёж крупнее свободного котла — урезание по котлу названо числом."""
    first = _deal(uid="первый", amount=D("1000"), closure_unit="копилка",
                  schedule=_rule(payment=D("1500")))
    second = _deal(uid="второй", amount=D("1000"), closure_unit="копилка",
                   schedule=_rule(payment=D("1500")))
    book = _book(first, second)
    validate(book)
    roll = roll_deals(book, START, D(0), max_months=2)
    assert [m.short for m in roll.months] == [D("1000.00")]   # want 1 500 при котле 500


def test_short_never_exceeds_want_on_a_negative_pool():
    """Отрицательный бюджет: не заплачено ровно want — недоплата не больше него."""
    deal = _deal(amount=D("10000"), rate_per_year=D("0.36"),
                 schedule=_rule(percent=D("0.1")))
    book = _book(deal)
    validate(book)
    jan = roll_deals(book, START, D("-2000"), max_months=1).months[0]
    assert jan.paid == D(0)
    assert jan.short == D("1030.00")           # want целиком, а не 1 030 + пул


# --- пробелы ---------------------------------------------------------------

def test_unknown_rate_is_a_gap_not_zero():
    """Неизвестная ставка — пробел, а не ноль: долг в прокат не входит."""
    deal = _deal(uid="без-ставки", amount=D("5000"), rate_per_year=None,
                 schedule=_rule(payment=D("1000")))
    book = _book(deal)
    validate(book)
    roll = roll_deals(book, START, D(0))
    assert [g.deal for g in roll.gaps] == ["без-ставки"]
    assert "ставка" in roll.gaps[0].reason
    assert roll.months[0].total == D("0")


def test_deal_without_a_schedule_is_a_gap():
    """Правила графика нет — платить нечем: сделка перечисляется пробелом."""
    book = _book(_deal(uid="без-графика", amount=D("5000")))
    validate(book)
    roll = roll_deals(book, START, D(0))
    assert "графика" in roll.gaps[0].reason


def test_deal_without_a_wallet_is_a_gap_not_silence():
    """Без финансирующего кошелька платёж не дойдёт до кассы: это пробел,
    а не молчаливое уменьшение остатка без движения денег."""
    book = _book(_deal(uid="без-кошелька", amount=D("5000"), wallet=None,
                       schedule=_rule(payment=D("1000"))))
    validate(book)
    roll = roll_deals(book, START, D(0))
    assert [g.deal for g in roll.gaps] == ["без-кошелька"]
    assert "кошелёк" in roll.gaps[0].reason
    assert roll.months[0].total == D("0")


def test_closed_deal_is_not_a_gap_and_not_rolled():
    """Закрытая сделка прокатывать нечего — и пробелом она не считается."""
    deal = _deal(uid="закрытая", amount=D("1000"), schedule=_rule(payment=D("1000")))
    book = _book(deal, movements=[Movement(date(2026, 1, 5), D("1000"), "закрытая")])
    validate(book)
    roll = roll_deals(book, START, D(0))
    assert roll.gaps == []
    assert roll.months[0].total == D("0")


def test_closed_deal_is_not_a_gap_even_without_a_rate():
    """Погашенная сделка без ставки — не пробел: прокатывать её нечего."""
    paid = _deal(uid="погашенный", amount=D("1000"), rate_per_year=None,
                 schedule=_rule(payment=D("1000")))
    book = _book(paid, movements=[Movement(date(2026, 1, 5), D("1000"), "погашенный")])
    validate(book)
    assert roll_deals(book, START, D(0)).gaps == []


def test_closed_member_does_not_break_its_unit():
    """Погашенный участник копилки не тянет за собой живые сделки."""
    done = _deal(uid="погашенный", amount=D("1000"), rate_per_year=None,
                 closure_unit="копилка", schedule=_rule(payment=D("1000")))
    alive = _deal(uid="живой", amount=D("2000"), closure_unit="копилка",
                  schedule=_rule(payment=D("1000")))
    book = _book(done, alive,
                 movements=[Movement(date(2026, 1, 5), D("1000"), "погашенный")])
    validate(book)
    roll = roll_deals(book, START, D(0), max_months=6)
    assert roll.gaps == []
    assert roll.months[0].units["копилка"].target == D("2000")
    assert roll.freedom == date(2026, 2, 1)


def test_receivable_without_a_schedule_is_a_gap_not_silence():
    """Требование без графика не исчезает молча: это пробел, а не ноль."""
    claim = _deal(uid="требование", amount=D("3000"), direction=OWED_TO_ME,
                  schedule=None)
    book = _book(claim)
    validate(book)
    roll = roll_deals(book, START, D(0))
    assert [g.deal for g in roll.gaps] == ["требование"]
    assert "когда придут" in roll.gaps[0].reason
    assert roll.expectations == []


def test_receivable_with_a_percent_rule_is_a_gap():
    """Сумма требования процентом от остатка не определена — это пробел."""
    claim = _deal(uid="требование", amount=D("3000"), direction=OWED_TO_ME,
                  schedule=_rule(percent=D("0.05")))
    book = _book(claim)
    validate(book)
    roll = roll_deals(book, START, D(0))
    assert "сумма требования" in roll.gaps[0].reason


# --- бюджет и стратегии ----------------------------------------------------

def test_freed_payment_stays_in_the_budget():
    """Освободившийся платёж остаётся в бюджете — эффект снежного кома.

    У малой сделки график конечный: закрылась досрочно — её оставшиеся платежи
    по-прежнему в бюджете и уходят большому долгу, а не исчезают.
    """
    small = _deal(uid="малый", amount=D("1000"), schedule=_rule(days=(5,),
                                                               start=START,
                                                               payment=D("1000"),
                                                               count=3))
    big = _deal(uid="большой", amount=D("10000"), schedule=_rule(payment=D("500")))
    book = _book(small, big)
    validate(book)
    roll = roll_deals(book, START, D("1000"), max_months=12)
    assert roll.months[0].balances["малый"] == D("0")       # закрылся в январе
    assert roll.months[0].paid == D("2500")                 # 1000 + 500 + 1000 досрочки
    assert roll.months[1].paid == D("2500")                 # его платёж остался в бюджете
    assert roll.months[1].balances["большой"] == D("6000")
    assert roll.months[3].paid == D("1500")                 # а график кончился — и платёж тоже


def test_avalanche_is_cheaper_than_snowball():
    """Порядок досрочек: лавина — дорогая ставка первой, комок — мелкий остаток."""
    expensive = _deal(uid="дорогой", amount=D("100000"), rate_per_year=D("0.4"),
                      schedule=_rule(payment=D("3000")))
    cheap = _deal(uid="дешёвый", amount=D("40000"), rate_per_year=D("0.1"),
                  schedule=_rule(payment=D("3000")))
    book = _book(expensive, cheap)
    validate(book)
    rows = compare_deal_strategies(book, START, D("20000"))
    assert [name for name, _ in rows] == ["avalanche", "snowball"]
    avalanche, snowball = rows[0][1], rows[1][1]
    assert avalanche.total_interest < snowball.total_interest
    assert avalanche.start_total == snowball.start_total == D("140000")


def test_avalanche_ranks_the_pot_by_its_dearest_member():
    """Лавина ранжирует копилку по самой дорогой ставке участника, а не нулём.

    Копилка под 40 % и гасится в лавине раньше standalone-сделки под 10 %
    при равном прочем; нулевая «ставка» не отправляет её в конец очереди
    после любой сделки с ненулевой ставкой. Ставка копилки участвует только
    в ранжировании — порядок досрочек месяца это и показывает.
    """
    member = _deal(uid="участник", amount=D("1000"), rate_per_year=D("0.4"),
                   closure_unit="копилка", schedule=_rule(payment=D("100")))
    plain = _deal(uid="одиночная", amount=D("1000"), rate_per_year=D("0.1"),
                  schedule=_rule(payment=D("100")))
    book = _book(member, plain)
    validate(book)
    roll = roll_deals(book, START, D("1500"), max_months=12)
    jan = roll.months[0]
    assert [sp.deal for sp in jan.payments if sp.planned is None] == [
        "участник", "одиночная"]                  # копилка гасится раньше
    assert jan.prepaid == D("1500")               # пул месяца разошёлся целиком
    assert jan.units["копилка"].pot == D("1000")  # цель набрана, копилка закрыта
    assert jan.balances["участник"] == D("0")     # закрытие разом, остатка нет
    assert jan.balances["одиночная"] == D("308.33")  # хвост: 1000 + 8,33 − 100 − 600


def test_prepay_can_be_forbidden_for_one_deal():
    """`prepay=False`: свободные деньги сделку не трогают — она идёт своим графиком.

    Так помечают револьверную кредитку: закрывать её досрочкой бессмысленно,
    лимит освобождает минимальный платёж и снова тратится. Признак — про сделку,
    а не про стратегию: свободные деньги уходят следующей по порядку.
    """
    def _pair(card_prepay: bool):
        card = _deal(uid="револьвер", amount=D("5000"), rate_per_year=D("0.5"),
                     schedule=_rule(payment=D("500")), prepay=card_prepay)
        loan = _deal(uid="заём", amount=D("5000"), rate_per_year=D("0.1"),
                     schedule=_rule(payment=D("500")))
        book = _book(card, loan)
        validate(book)
        return roll_deals(book, START, D("1000"), max_months=12)

    # По стратегии первой гасится дорогая ставка — револьвер.
    allowed = _pair(True).months[0]
    assert allowed.balances["револьвер"] < allowed.balances["заём"]
    # Запрет досрочки снимает его с раздачи: деньги идут займу, револьвер платит
    # только вхождение (5 000 + 208,33 процентов − 500).
    roll = _pair(False)
    assert all(sp.planned is not None for dm in roll.months
               for sp in dm.payments if sp.deal == "револьвер")
    assert roll.months[0].prepaid == D("1000")          # досрочка ушла займу
    assert roll.months[0].balances["револьвер"] == D("4708.33")
    assert roll.months[0].balances["заём"] == D("3541.67")


def test_unit_with_a_forbidden_member_is_not_prepaid():
    """Копилка досрочится целиком — запрет участника запрещает и её.

    Иначе запрет обошли бы через единицу закрытия: досрочка копилки гасит всех
    её участников разом.
    """
    first = _deal(uid="первый", amount=D("600"), schedule=_rule(payment=D("300")),
                  closure_unit="копилка")
    second = _deal(uid="второй", amount=D("600"), schedule=_rule(payment=D("300")),
                   closure_unit="копилка", prepay=False)
    book = _book(first, second)
    validate(book)
    roll = roll_deals(book, START, D("1000"), max_months=6)
    assert all(sp.planned is not None for dm in roll.months for sp in dm.payments)
    assert roll.months[0].prepaid == D("0")
    # Остаток упал ровно на обязательный платёж (600 − 300): досрочки не было —
    # в этом суть теста, а не «остаток стоит».
    assert roll.months[0].balances["первый"] == D("300.00")


def test_unknown_strategy_is_rejected():
    with pytest.raises(ValueError, match="неизвестная стратегия"):
        roll_deals(_book(_deal()), START, D(0), strategy="как-нибудь")


# --- начисление процентов --------------------------------------------------

def test_payment_below_interest_never_closes():
    """Платёж меньше начисляемых процентов — честное «не закроется», а не срок."""
    deal = _deal(amount=D("100000"), rate_per_year=None, rate_per_day=D("0.0052"),
                 schedule=_rule(payment=D("400")))
    book = _book(deal)
    validate(book)
    roll = roll_deals(book, START, D(0), max_months=24)
    assert roll.freedom is None and roll.stalled
    assert roll.months[-1].total > D("100000")


def test_daily_rate_charges_for_a_postponement_inside_the_month():
    """Дневная ставка: перенос платежа внутри месяца стоит дни × дневная ставка."""
    deal = _deal(amount=D("100000"), rate_per_year=None, rate_per_day=D("0.001"),
                 schedule=_rule(days=(12,), payment=D("50000")))
    on_time = roll_deals(_book(deal), START, D(0), max_months=1)
    late = roll_deals(_book(deal, edits=[OccurrenceEdit("заём", date(2026, 1, 12),
                                                        postponed=True,
                                                        moved_to=date(2026, 1, 25))]),
                      START, D(0), max_months=1)
    # 11 дней по 100 000 + 20 дней по 50 000 против 24 дней по 100 000 + 7 по 50 000
    assert on_time.months[0].interest == D("2100.00")
    assert late.months[0].interest == D("2750.00")
    assert late.months[0].interest - on_time.months[0].interest == D("650.00")


def test_annual_rate_ignores_a_postponement_inside_the_month():
    """Годовая ставка начисляется за месяц: перенос внутри месяца её не удорожает."""
    deal = _deal(amount=D("120000"), rate_per_year=D("0.24"),
                 schedule=_rule(days=(12,), payment=D("5000")))
    on_time = roll_deals(_book(deal), START, D(0), max_months=1)
    late = roll_deals(_book(deal, edits=[OccurrenceEdit("заём", date(2026, 1, 12),
                                                        postponed=True,
                                                        moved_to=date(2026, 1, 25))]),
                      START, D(0), max_months=1)
    assert on_time.months[0].interest == D("2400.00")
    assert late.months[0].interest == D("2400.00")


def test_a_full_month_through_the_function_equals_the_roll_number():
    """Полный месяц через функцию начисления — ровно то число, что дал прокат.

    Одинаковый вход — одно число: правило живёт в `settlements`, а прокат его
    вызывает, поэтому считает ту же сумму, что и прежде.
    """
    cases = [
        (_deal(amount=D("100000"), rate_per_year=None, rate_per_day=D("0.001"),
               schedule=_rule(days=(12,), payment=D("50000"))), D("50000")),
        (_deal(amount=D("120000"), rate_per_year=D("0.24"),
               schedule=_rule(days=(12,), payment=D("5000"))), D("5000")),
    ]
    seen = []
    for deal, payment in cases:
        roll = roll_deals(_book(deal), START, D(0), max_months=1)
        accrued = accrued_interest(deal, deal.amount, START, date(2026, 1, 31),
                                   [(date(2026, 1, 12), payment)])
        assert accrued == roll.months[0].interest
        seen.append(roll.months[0].interest)
    # Прежние числа проката: 11 × 100 + 20 × 50 по дневной и 120 000 × 0,24 / 12.
    assert seen == [D("2100.00"), D("2400.00")]


# --- канон остатка ---------------------------------------------------------

def test_roll_balance_and_the_canon_are_one_number_at_the_month_end():
    """Остаток проката на конец месяца — то же число, что расчётный остаток.

    Долг 100 000 под 24 %, платёж 5 000 двенадцать раз: до канона числа
    расходились ровно на накопленные проценты (59 763,73 против 40 000),
    потому что расчётный остаток ставку не читал.
    """
    deal = _deal(amount=D("100000"), start=START, rate_per_year=D("0.24"),
                 rate_per_day=None,
                 schedule=_rule(days=(12,), payment=D("5000"), count=12,
                                start=START, shift_weekend=False))
    book = _book(deal)
    validate(book)
    roll = roll_deals(book, START, D(0), max_months=12)
    assert roll.months[11].balances["заём"] == D("59763.73")
    # Платежи проката записываются движениями — так их записывает зона, и на
    # них уже не прокат считает, а канон остатка.
    for month in roll.months:
        for payment in month.payments:
            book.movements.append(Movement(payment.date, payment.amount, "заём",
                                           occurrence=payment.planned))
    assert deal_balance(book, "заём", date(2026, 12, 31)) == D("59763.73")
    assert deal_balance(book, "заём", date(2026, 12, 31)) == \
        roll.months[11].balances["заём"]


def test_the_roll_opens_with_the_canon_and_a_mid_month_date_accrues_by_days():
    """Прокат открывается каноном на начало окна; дата внутри месяца — по дням.

    Долг начался раньше окна (01.06.2025), поэтому в основании проката лежит
    канон с начисленным за прошедшие месяцы. Второй приоритет без согласия не
    платится — прокату остаётся начисление, и оба числа видны насквозь.
    """
    deal = _deal(amount=D("100000"), start=date(2025, 6, 1),
                 rate_per_year=D("0.24"), rate_per_day=None,
                 second_priority=True, schedule=_rule(days=(20,), payment=D("1000")))
    book = _book(deal)
    validate(book)
    roll = roll_deals(book, START, D(0), max_months=1)
    # Основание проката — канон на конец декабря: 100 000 плюс начисленное
    # за семь месяцев (окно открывается 01.01, сам январь прокат начислит).
    assert deal_balance(book, "заём", date(2025, 12, 31)) == D("114868.56")
    # Январь начисляется на этом основании: 114 868,56 × 0,24 / 12.
    assert roll.months[0].interest == D("2297.37")
    # Дата внутри месяца — пропорционально дням: 15 из 31 дня января.
    assert deal_balance(book, "заём", date(2026, 1, 15)) == D("115980.19")
    # Месяц на границе — ровно то число, что дал прокат.
    assert roll.months[0].balances["заём"] == D("117165.93")
    assert deal_balance(book, "заём", date(2026, 1, 31)) == \
        roll.months[0].balances["заём"]


def test_a_debt_start_inside_the_window_accrues_only_from_it():
    """Долг начался внутри окна — до его начала начисленного нет, а не ошибка.

    `Deal.start` (15.04) и `ScheduleRule.start` (01.06) — разные вопросы:
    ряд платежей начинается позже, а долг существует с середины апреля, и до
    этой даты баланс равен телу.
    """
    deal = _deal(amount=D("100000"), start=date(2026, 4, 15),
                 rate_per_year=D("0.24"), rate_per_day=None,
                 schedule=_rule(days=(20,), payment=D("1000"),
                                start=date(2026, 6, 1)))
    book = _book(deal)
    validate(book)
    roll = roll_deals(book, START, D(0), max_months=5)
    # До начала долга баланс равен телу, начисленного нет.
    assert deal_balance(book, "заём", date(2026, 3, 31)) == D("100000")
    assert [m.interest for m in roll.months[:3]] == [D("0"), D("0"), D("0")]
    # Апрель начисляет с 15-го: 16 дней из 30, и это тот же отрезок в каноне.
    assert roll.months[3].interest == D("1066.67")
    assert deal_balance(book, "заём", date(2026, 4, 30)) == \
        roll.months[3].balances["заём"]
    assert deal_balance(book, "заём", date(2026, 5, 31)) == \
        roll.months[4].balances["заём"]


def test_a_payment_before_the_debt_start_stays_in_the_accrual_base():
    """Платёж до начала долга в том же месяце уменьшает базу — как в каноне.

    Долг начался 31-го, а платёж 20-го уже уменьшил тело: проценты за 31-е
    идут на 99 000, а не на 100 000, и прокат берёт ту же базу.
    """
    deal = _deal(amount=D("100000"), start=date(2026, 1, 31),
                 rate_per_year=D("0.24"), rate_per_day=None,
                 schedule=_rule(days=(20,), payment=D("1000")))
    book = _book(deal, movements=[Movement(date(2026, 1, 20), D("1000"), "заём",
                                           occurrence=date(2026, 1, 20))])
    validate(book)
    roll = roll_deals(book, START, D(0), max_months=1)
    assert deal_balance(book, "заём", date(2026, 1, 31)) == D("99063.87")
    assert roll.months[0].balances["заём"] == \
        deal_balance(book, "заём", date(2026, 1, 31))


def test_an_in_window_movement_enters_the_months_accrual():
    """Движение месяца участвует в базе дневной ставки, а не только в остатке.

    Января без вхождений: платёж 5-го уменьшает базу с этого дня, и прокат
    считает те же 4 × 100 плюс 27 × 99, что и канон.
    """
    deal = _deal(amount=D("100000"), start=START, rate_per_year=None,
                 rate_per_day=D("0.001"),
                 schedule=_rule(days=(20,), payment=D("5000"),
                                start=date(2026, 2, 1)))
    book = _book(deal, movements=[Movement(date(2026, 1, 5), D("1000"), "заём")])
    validate(book)
    roll = roll_deals(book, START, D(0), max_months=1)
    assert deal_balance(book, "заём", date(2026, 1, 31)) == D("102073")
    assert roll.months[0].balances["заём"] == \
        deal_balance(book, "заём", date(2026, 1, 31))


# --- месяц целиком ---------------------------------------------------------

def test_month_aggregates_its_payments_and_keeps_their_days():
    """Прокат месячный: платежи месяца сходятся в итог, а даты у них остаются."""
    deal = _deal(amount=D("3000"), schedule=_rule(days=(5, 20), payment=D("500")))
    book = _book(deal)
    validate(book)
    jan = roll_deals(book, START, D(0), max_months=1).months[0]
    assert jan.index == 1 and jan.month == date(2026, 1, 1)
    assert jan.paid == D("1000")
    assert [(p.date, p.amount, p.wallet, p.counterparty)
            for p in jan.payments] == [(date(2026, 1, 5), D("500"), "карта", "банк"),
                                       (date(2026, 1, 20), D("500"), "карта", "банк")]


# --- проверки книги --------------------------------------------------------

def test_edits_are_checked_against_the_book():
    deal = _deal(schedule=_rule(payment=D("1000")))
    with pytest.raises(ValueError, match="неизвестный уид"):
        validate(_book(deal, edits=[OccurrenceEdit("нет-такого", date(2026, 1, 20))]))
    with pytest.raises(ValueError, match="перенесённая дата без переноса"):
        validate(_book(deal, edits=[OccurrenceEdit("заём", date(2026, 1, 20),
                                                   moved_to=date(2026, 2, 1))]))
    with pytest.raises(ValueError, match="и перенесено, и пропущено"):
        validate(_book(deal, edits=[OccurrenceEdit("заём", date(2026, 1, 20),
                                                   postponed=True, skipped=True)]))
    with pytest.raises(ValueError, match="такого вхождения по графику нет"):
        validate(_book(deal, edits=[OccurrenceEdit("заём", date(2026, 1, 21),
                                                   skipped=True)]))
    with pytest.raises(ValueError, match="движение по нему есть"):
        validate(_book(deal, movements=[Movement(date(2026, 1, 20), D("100"), "заём",
                                                 occurrence=date(2026, 1, 20))],
                       edits=[OccurrenceEdit("заём", date(2026, 1, 20), skipped=True)]))


def test_rule_forms_are_checked():
    with pytest.raises(ValueError, match="пустое"):
        validate(_book(_deal(schedule=ScheduleRule())))
    with pytest.raises(ValueError, match="повторён"):
        validate(_book(_deal(schedule=_rule(days=(5, 5), payment=D("100")))))
    with pytest.raises(ValueError, match="не сосчитать платежи"):
        validate(_book(_deal(schedule=_rule(payment=D("100"), count=3))))
    with pytest.raises(ValueError, match="не тем и другим сразу"):
        validate(_book(_deal(schedule=_rule(payment=D("100"), count=3, start=START,
                                            percent=D("0.05")))))
    with pytest.raises(ValueError, match="процент от остатка"):
        validate(_book(_deal(schedule=_rule(percent=D("5")))))
    with pytest.raises(ValueError, match="у регулярного расхода нет единицы"):
        validate(_book(_deal(amount=None, closure_unit="копилка")))
    with pytest.raises(ValueError, match="требование в единице закрытия"):
        validate(_book(_deal(amount=D("100"), direction=OWED_TO_ME,
                             closure_unit="копилка")))


# --- окно проката ----------------------------------------------------------

def test_window_ends_when_the_debts_close():
    """Окно кончается последним платежом по долгам: регулярный расход его не продлевает.

    Долг с графиком на 24 месяца и бесконечный регулярный расход: окно — 24
    месяца, а не отведённые 600, и причина названа.
    """
    debt = _deal("заём", amount=D("24000"),
                 schedule=_rule(start=START, payment=D("1000"), count=24))
    expense = _deal("аренда", amount=None,
                    schedule=_rule(days=(10,), payment=D("500")))
    roll = roll_deals(_book(debt, expense), START, D("0"))
    assert roll.window.months == 24
    assert roll.window.reason == WINDOW_DEBTS_CLOSED
    assert roll.window.until == date(2027, 12, 31)
    assert len(roll.months) == 24


def test_window_ends_with_the_visible_income():
    """Доходы кончаются раньше долга: окно кончается концом доходов, причина названа."""
    debt = _deal("долгий", amount=D("60000"),
                 schedule=_rule(start=START, payment=D("1000"), count=60))
    book = _book(debt)
    end_of_income = date(2026, 12, 31)
    roll = roll_deals(book, START, D("0"), income_horizon=end_of_income)
    assert roll.window.months == 12
    assert roll.window.reason == WINDOW_INCOME_ENDS
    assert roll.freedom is None              # срок с досрочками за окном — не выдуман
    assert roll_window(book, START, 600, income_horizon=end_of_income).months == 12


def test_window_hits_the_month_cap_without_a_payment_count():
    """Долг без числа платежей: последнего платежа нет — окно упирается в предел месяцев."""
    debt = _deal("карта", amount=D("10000"),
                 schedule=_rule(start=START, percent=D("0.05")))
    roll = roll_deals(_book(debt), START, D("0"), max_months=600)
    assert roll.window.months == 600
    assert roll.window.reason == WINDOW_MONTH_CAP


def test_a_deal_without_a_payment_count_does_not_end_the_window():
    """Ряд без числа платежей границы по долгам не даёт: окно не режется чужим концом."""
    small = _deal("малый", amount=D("3000"),
                  schedule=_rule(start=START, payment=D("1000"), count=3))
    big = _deal("большой", amount=D("10000"),
                schedule=_rule(start=START, payment=D("500")))
    roll = roll_deals(_book(small, big), START, D("0"), max_months=12)
    assert roll.window.months == 12
    assert roll.window.reason == WINDOW_MONTH_CAP


def test_debts_win_the_tie_with_the_month_cap():
    """Совпали границы — побеждает причина, первая по порядку: вопрос был о долгах."""
    debt = _deal("заём", amount=D("3000"),
                 schedule=_rule(start=START, payment=D("1000"), count=3))
    roll = roll_deals(_book(debt), START, D("0"), max_months=3)
    assert roll.window.months == 3
    assert roll.window.reason == WINDOW_DEBTS_CLOSED


def test_second_priority_extends_the_window_only_with_consent():
    """Второй приоритет продлевает окно до своего закрытия только при согласии."""
    first = _deal("график", amount=D("6000"),
                  schedule=_rule(start=START, payment=D("1000"), count=6))
    second = _deal("взыскание", amount=D("6000"), second_priority=True,
                   schedule=_rule(start=START, payment=D("500"), count=24))
    book = _book(first, second)
    without = roll_deals(book, START, D("0"))
    assert (without.window.months, without.window.reason) == (6, WINDOW_DEBTS_CLOSED)
    agreed = roll_deals(book, START, D("0"), consent_to_second=True)
    assert (agreed.window.months, agreed.window.reason) == (24, WINDOW_DEBTS_CLOSED)


def test_a_postponed_payment_extends_the_window_and_never_shortens_it():
    """Перенесённый платёж окно удлиняет, а перенос раньше — не сокращает.

    График — гарантированный верх: окно обязано дождаться платежа по назначенной
    дате, иначе платёж молча выпал бы из проката. Обратный перенос окна не режет.
    """
    debt = _deal("заём", amount=D("3000"),
                 schedule=_rule(start=START, payment=D("1000"), count=3))
    later = _book(debt, edits=[OccurrenceEdit("заём", date(2026, 3, 20),
                                              postponed=True,
                                              moved_to=date(2026, 5, 20))])
    validate(later)
    assert roll_deals(later, START, D("0")).window.months == 5

    earlier = _book(debt, edits=[OccurrenceEdit("заём", date(2026, 3, 20),
                                                postponed=True,
                                                moved_to=date(2026, 2, 25))])
    validate(earlier)
    assert roll_deals(earlier, START, D("0")).window.months == 3


# --- обе даты срока --------------------------------------------------------

def test_payoff_by_graph_says_not_closed_when_the_payment_covers_interest():
    """Платёж равен процентам: по графику не закрывается, с досрочками месяц."""
    debt = _deal("вечный", amount=D("1000"), rate_per_year=D("0.12"),
                 schedule=_rule(payment=D("10")))
    roll = roll_deals(_book(debt), START, D("100"), max_months=24)
    assert roll.payoff_by_graph is None
    assert roll.payoff_by_graph_reason == PAYOFF_NOT_CLOSED
    assert roll.freedom == date(2026, 10, 1)


def test_payoff_by_graph_comes_after_the_payoff_with_prepayments():
    """Обе даты есть и разные: досрочки закрывают долг раньше графика."""
    debt = _deal("заём", amount=D("3000"), schedule=_rule(payment=D("1000")))
    roll = roll_deals(_book(debt), START, D("2000"))
    assert roll.payoff_by_graph == date(2026, 3, 1)
    assert roll.payoff_by_graph_reason is None
    assert roll.freedom == date(2026, 1, 1)
    assert roll.payoff_by_graph != roll.freedom


def test_payoff_by_graph_of_a_debt_paid_off_before_the_roll():
    """Погашен до проката: первый месяц окна и причина, а не пустая дата."""
    debt = _deal("заём", amount=D("1000"), schedule=_rule(payment=D("1000")))
    closed = Movement(date(2025, 12, 20), D("1000"), "заём")
    roll = roll_deals(_book(debt, movements=[closed]), START, D("0"))
    assert roll.payoff_by_graph == date(2026, 1, 1)
    assert roll.payoff_by_graph_reason == PAYOFF_CLOSED_BEFORE


def test_payoff_reasons_are_the_literal_phrases():
    """Формулировки причин — ровно тексты глоссария, а не синоним смысла."""
    gone = roll_deals(
        _book(_deal("заём", amount=D("1000"), schedule=_rule(payment=D("1000"))),
              movements=[Movement(date(2025, 12, 20), D("1000"), "заём")]),
        START, D("0"))
    assert gone.payoff_by_graph_reason == "закрыт к началу проката: закрывать нечего"
    never = roll_deals(
        _book(_deal("вечный", amount=D("1000"), rate_per_year=D("0.12"),
                    schedule=_rule(payment=D("10")))),
        START, D("0"), max_months=24)
    assert never.payoff_by_graph_reason == "за отведённые месяцы долг не закрылся"


def test_a_gap_is_not_a_payoff_closed_before_the_roll():
    """Пробел — не «закрыт к началу»: не смоделированный долг не закрыт, а не просчитан."""
    book = _book(_deal("пробел", amount=D("1000"), schedule=None))
    roll = roll_deals(book, START, D("0"), max_months=24)
    assert roll.gaps                                 # долг остался пробелом
    assert roll.payoff_by_graph is None
    assert roll.payoff_by_graph_reason == PAYOFF_NOT_CLOSED


def test_the_payoff_with_prepayments_is_never_later_than_the_graph():
    """Срок с досрочками не позже срока по графику: досрочки только приближают."""
    plain = _deal("заём", amount=D("3000"), schedule=_rule(payment=D("1000")))
    second = _deal("второй", amount=D("1000"), second_priority=True,
                   schedule=_rule(payment=D("1000")))
    cases = [
        ("без досрочек", _book(plain), D("0")),
        ("с досрочками", _book(plain), D("2000")),
        ("два долга со вторым приоритетом", _book(plain, second), D("2000")),
    ]
    for name, book, extra in cases:
        roll = roll_deals(book, START, extra, consent_to_second=True)
        assert roll.freedom is not None, name
        assert roll.payoff_by_graph is not None, name
        assert roll.freedom <= roll.payoff_by_graph, name
    helped = roll_deals(_book(plain), START, D("2000"))
    assert helped.freedom < helped.payoff_by_graph   # досрочки приближают срок
