"""Тесты взаиморасчётов — синтетические фикстуры, без личных данных."""
from __future__ import annotations

from datetime import date
from decimal import Decimal as D
import pytest

from finance_core import (BODY, BOTH, CAPS, CAP_NONE, CAP_SHARE, CAP_SUM,
                          COSTS, CREDITOR, DEBTOR, EXPECTED, IN, INTEREST, LEGAL,
                          OWED_TO_ME, PAID, PAID_LATE, PARTS, PENALTY, PERSON, SKIPPED,
                          STARTER_GROUPS,
                          TRIGGERS, TRIGGER_OVERDUE, TRIGGER_OVERDUE_DAYS,
                          TRIGGER_OVERDUE_SUM, Account, AllocationRule,
                          Assignment, Charge, Counterparty, Deal, Movement,
                          OccurrenceEdit, PartPayment, Payment, PenaltyCap,
                          PenaltyRule, PenaltyStep, Scenario, ScheduleRule,
                          Settlements, TriggerRule, Wallet, accrued_interest,
                          allocate_payment, beneficiary, counterparty_balance,
                          counterparty_role, deal_amount_at, deal_balance,
                          deal_holder_at, funding_wallet, liquidity, occurrences,
                          payment_channel, roll_deals, rule_at, run, validate)

START = date(2026, 1, 1)


def _counterparty(uid: str = "банк", **kw) -> Counterparty:
    base = dict(uid=uid, name="Банк", kind=LEGAL, subtype="банк", groups=("долги",))
    base.update(kw)
    return Counterparty(**base)


def _deal(uid: str = "заём", amount: D | None = D("10000"), **kw) -> Deal:
    base = dict(uid=uid, title="Заём", counterparty="банк", amount=amount)
    base.update(kw)
    return Deal(**base)


# --- контрагент ------------------------------------------------------------

def test_kind_and_subtype_come_from_declared_sets():
    """Тип и подтип — из объявленных наборов, опечатка отклоняется."""
    with pytest.raises(ValueError, match="объявлены: \\['физлицо', 'юрлицо'\\]"):
        validate(Settlements(counterparties=[_counterparty(kind="физ. лицо")]))


def test_subtype_belongs_to_its_kind():
    """Подтипы у физлица и юрлица разные: «банк» физлицу не подтип."""
    with pytest.raises(ValueError, match="родственник"):
        validate(Settlements(counterparties=[_counterparty(kind=PERSON, subtype="банк")]))


def test_groups_are_free_labels_over_a_starter_set():
    """Стартовый набор объявлен, метки свободные: своя группа проходит."""
    assert STARTER_GROUPS == ("семья", "работа", "жильё", "долги")
    book = Settlements(counterparties=[_counterparty(groups=("семья", "соседи"))])
    validate(book)          # свободная метка — не ошибка


def test_group_cannot_be_empty_or_repeated():
    with pytest.raises(ValueError, match="пустая группа"):
        validate(Settlements(counterparties=[_counterparty(groups=("долги", ""))]))
    with pytest.raises(ValueError, match="повторена"):
        validate(Settlements(counterparties=[_counterparty(groups=("долги", "долги"))]))


def test_undeclared_counterparty_lists_declared():
    """Ссылка на необъявленного контрагента — ошибка с перечнем объявленных."""
    book = Settlements(counterparties=[_counterparty()],
                       deals=[_deal(counterparty="нет-такого")])
    with pytest.raises(ValueError, match="объявлены: \\['банк'\\]"):
        validate(book)


def test_uid_is_declared_twice():
    book = Settlements(counterparties=[_counterparty(), _counterparty()])
    with pytest.raises(ValueError, match="объявлен дважды"):
        validate(book)


def test_deal_without_counterparty_does_not_exist():
    """Сделки без контрагента не бывает — без него сделку не собрать."""
    with pytest.raises(TypeError):
        Deal("заём", "Заём")                                    # type: ignore[call-arg]


# --- кошелёк ---------------------------------------------------------------

def test_credit_minus_is_debt_and_debit_minus_is_a_hole():
    """Минус на кредитном — долг, а не дыра; на некредитном — дыра, а не долг."""
    credit = Wallet("кредитка", "Кредитка", "карта", D("-30000"),
                    is_credit=True, limit=D("100000"))
    assert credit.debt == D("30000")
    assert credit.money == D("0")
    debit = Wallet("карта", "Карта", "карта", D("-5000"))
    assert debit.debt == D("0")
    assert debit.money == D("-5000")


def test_credit_plus_is_own_money_and_not_the_free_limit():
    """Плюс на кредитном — свои деньги (переплата), а не свободный лимит."""
    wallet = Wallet("кредитка", "Кредитка", "карта", D("5000"),
                    is_credit=True, limit=D("100000"))
    assert wallet.overpayment == D("5000")
    assert wallet.free_limit == D("100000")
    assert liquidity(Settlements(wallets=[wallet])) == D("5000")


def test_free_limit_is_shown_but_is_not_money():
    """Свободный лимит показывается, но деньгами не считается."""
    wallet = Wallet("кредитка", "Кредитка", "карта", D("-30000"),
                    is_credit=True, limit=D("100000"))
    assert wallet.free_limit == D("70000")
    assert liquidity(Settlements(wallets=[wallet])) == D("0")


def test_blocked_limit_gives_no_free_limit():
    """Лимит, закрытый банком, — не свободный лимит: гасить можно, взять нельзя."""
    blocked = Wallet("кредитка", "Кредитка", "карта", D("-30000"), is_credit=True,
                     limit=D("100000"), available=False)
    assert blocked.debt == D("30000")        # долг остаётся долгом: его гасят
    assert blocked.free_limit == D("0")      # а лимита к использованию нет
    assert blocked.money == D("0")
    assert liquidity(Settlements(wallets=[blocked])) == D("0")

    # Свои деньги на такой карте тоже не достать: с неё можно только гасить.
    trapped = Wallet("кредитка", "Кредитка", "карта", D("5000"), is_credit=True,
                     limit=D("100000"), available=False)
    assert trapped.money == D("0")


def test_unknown_limit_is_not_zero():
    """Лимит неизвестен — «не оценено», а не ноль."""
    wallet = Wallet("кредитка", "Кредитка", "карта", D("-30000"), is_credit=True)
    assert wallet.free_limit is None
    assert Wallet("карта", "Карта", "карта", D("100")).free_limit == D("0")


def test_unavailable_wallet_is_out_of_liquidity():
    """Арестованный или заблокированный показывается, но деньгами не считается."""
    wallet = Wallet("карта", "Карта", "карта", D("40000"), available=False)
    assert wallet.money == D("0")
    assert liquidity(Settlements(wallets=[wallet])) == D("0")


def test_wallet_limit_belongs_to_credit_only():
    with pytest.raises(ValueError, match="только у кредитного"):
        validate(Settlements(wallets=[Wallet("счёт", "Счёт", "счёт", D("100"),
                                             limit=D("1000"))]))


def test_wallet_kinds_are_a_starter_set_that_extends():
    """Тип кошелька: стартовый набор расширяется потребителем, пустого не бывает."""
    validate(Settlements(wallets=[Wallet("брокер", "Брокерский", "брокерский счёт",
                                         D("0"))]))
    with pytest.raises(ValueError, match="не указан тип"):
        validate(Settlements(wallets=[Wallet("пусто", "Пусто", "", D("0"))]))


# --- сделка, движение, остаток ---------------------------------------------

def test_regular_expense_has_nothing_to_close():
    """Сделка без остатка — регулярный расход: не закрывается, досрочке не подлежит."""
    deal = _deal(uid="аренда", amount=None, counterparty="арендодатель")
    assert deal.closing is False
    book = Settlements(
        counterparties=[_counterparty(uid="арендодатель", name="Арендодатель",
                                      subtype="прочее")],
        deals=[deal],
        movements=[Movement(date(2026, 1, 10), D("30000"), "аренда", purpose="жильё")],
    )
    validate(book)
    assert deal_balance(book, "аренда", date(2026, 1, 31)) is None
    assert counterparty_balance(book, "арендодатель", date(2026, 1, 31)) == D("0")


def test_schedule_rule_days_are_days_of_the_month():
    """День вхождения — день месяца: правило с днём вне 1–31 не собирается."""
    with pytest.raises(ValueError, match="вне 1–31"):
        validate(Settlements(counterparties=[_counterparty()],
                             deals=[_deal(schedule=ScheduleRule(days=(32,)))]))


def test_deal_balance_counts_conditions_and_movements_up_to_the_date():
    """Расчётный остаток считается из условий и движений на дату."""
    book = Settlements(
        counterparties=[_counterparty()],
        deals=[_deal(amount=D("10000"))],
        movements=[Movement(date(2026, 2, 10), D("4000"), "заём")],
    )
    validate(book)
    assert deal_balance(book, "заём", date(2026, 1, 31)) == D("10000")
    assert deal_balance(book, "заём", date(2026, 2, 28)) == D("6000")


def test_movement_against_the_deal_returns_money_to_the_balance():
    """Движение против сделки — возврат: остаток растёт, а не падает."""
    book = Settlements(
        counterparties=[_counterparty()],
        deals=[_deal(amount=D("10000"))],
        movements=[Movement(date(2026, 2, 10), D("4000"), "заём"),
                   Movement(date(2026, 2, 20), D("1000"), "заём",
                            direction=IN, purpose="возврат переплаты")],
    )
    validate(book)
    assert deal_balance(book, "заём", date(2026, 2, 28)) == D("7000")


def test_movement_amount_is_positive():
    """Сумма движения положительная: направление — у движения, а не у знака."""
    book = Settlements(counterparties=[_counterparty()], deals=[_deal()],
                       movements=[Movement(date(2026, 1, 1), D("-100"), "заём")])
    with pytest.raises(ValueError, match="положительной"):
        validate(book)


def test_movement_overrides_the_deal_defaults():
    """Кошелёк, канал и «за кого» — у движения своё, у сделки по умолчанию."""
    deal = _deal(wallet="карта", channel="посредник", benefit_for="родня")
    movement = Movement(date(2026, 1, 5), D("100"), "заём", wallet="наличные")
    assert funding_wallet(deal, movement) == "наличные"
    assert payment_channel(deal, movement) == "посредник"
    assert beneficiary(deal, movement) == "родня"
    assert funding_wallet(deal) == "карта"


# --- начало долга -----------------------------------------------------------

def test_a_rated_deal_without_a_debt_start_falls_with_a_clear_text():
    """Ставка и сумма без начала долга — ошибка: считать долг не от чего."""
    book = Settlements(
        counterparties=[_counterparty()],
        deals=[_deal(start=None, rate_per_year=None, rate_per_day=D("0.001"))],
    )
    with pytest.raises(ValueError, match="начала долга нет"):
        validate(book)


def test_a_free_deal_and_a_regular_expense_never_ask_for_a_debt_start():
    """Ни беспроцентной сделке, ни регулярному расходу начала долга не спрашивают."""
    book = Settlements(
        counterparties=[_counterparty(),
                        _counterparty(uid="арендодатель", name="Арендодатель",
                                      subtype="прочее")],
        deals=[_deal(uid="рассрочка", start=None),
               _deal(uid="аренда", amount=None, counterparty="арендодатель",
                     start=None)],
        movements=[Movement(date(2026, 1, 10), D("3000"), "рассрочка")],
    )
    validate(book)                    # отсутствие начала долга — не ошибка
    # У беспроцентной канон — тело минус движения, у регулярного расхода его нет.
    assert deal_balance(book, "рассрочка", date(2026, 1, 31)) == D("7000")
    assert deal_balance(book, "аренда", date(2026, 1, 31)) is None


def test_a_zero_rate_deal_never_asks_for_a_debt_start():
    """Нулевая ставка — не ставка: дата начала не спрашивается, как у 0 %.

    Начисление равно нулю при любой дате, поэтому сделка «0 % (график)» без
    даты — обычная беспроцентная: проходит `validate`, в пробел не уходит и
    считается «тело минус движения». Ненулевая ставка без даты по-прежнему
    падает (`test_a_rated_deal_without_a_debt_start_falls_with_a_clear_text`).
    """
    book = Settlements(
        counterparties=[_counterparty()],
        wallets=[Wallet("карта", "Карта", "карта", D("0"))],
        deals=[_deal(uid="рассрочка", start=None, rate_per_year=D("0"),
                     wallet="карта",
                     schedule=ScheduleRule(days=(20,), payment=D("1000")))],
        movements=[Movement(date(2026, 1, 10), D("3000"), "рассрочка")],
    )
    validate(book)                    # нулевая ставка без даты — не ошибка
    assert deal_balance(book, "рассрочка", date(2026, 1, 31)) == D("7000")
    assert roll_deals(book, START, D(0), max_months=1).gaps == []


def test_a_rated_regular_expense_and_a_free_claim_never_ask_for_a_debt_start():
    """Ставка без суммы и требование с нулевой ставкой начала не спрашивают.

    Регулярный расход остатка не имеет — закрывать нечего, дата ему не нужна
    даже со ставкой; требование с нулевой ставкой начисления не даёт — как
    обычная беспроцентная сделка.
    """
    book = Settlements(
        counterparties=[_counterparty(),
                        _counterparty(uid="арендодатель", name="Арендодатель",
                                      subtype="прочее")],
        wallets=[Wallet("карта", "Карта", "карта", D("0"))],
        deals=[_deal(uid="аренда", amount=None, counterparty="арендодатель",
                     start=None, rate_per_year=D("0.12"), wallet="карта",
                     schedule=ScheduleRule(days=(5,), payment=D("3000"))),
               _deal(uid="требование", start=None, rate_per_year=D("0"),
                     direction=OWED_TO_ME, wallet="карта",
                     schedule=ScheduleRule(days=(10,), payment=D("1000")))],
    )
    validate(book)                    # ни одна дата не спрашивается
    assert roll_deals(book, START, D(0), max_months=1).gaps == []
    # У нулевой ставки канон тот же, что и «тело минус движения».
    assert deal_balance(book, "требование", date(2026, 1, 31)) == D("10000")


# --- начисление процентов ---------------------------------------------------

def _daily(rate: D = D("0.001")) -> Deal:
    """Сделка с дневной ставкой: база начисления — по дням."""
    return _deal(rate_per_year=None, rate_per_day=rate)


def _yearly(rate: D = D("0.24")) -> Deal:
    """Сделка с годовой ставкой: база начисления — по месяцам."""
    return _deal(rate_per_year=rate, rate_per_day=None)


def test_daily_interest_takes_a_days_payment_before_that_day():
    """Платёж дня уменьшает остаток до начисления за этот день."""
    interest = accrued_interest(_daily(), D("100000"), date(2026, 1, 1),
                                date(2026, 1, 31),
                                [(date(2026, 1, 12), D("50000"))])
    # 11 дней по 100,00 и 20 дней по 50,00: 12-е считается уже от 50 000.
    # Начисли сначала и убавь потом — вышло бы 100,00 за 12-е, то есть 2150.
    assert interest == D("2100.00")


def test_annual_interest_is_taken_from_the_balance_at_the_month_start():
    """Годовая: платежи внутри месяца начисление этого месяца не меняют."""
    interest = accrued_interest(_yearly(), D("120000"), date(2026, 1, 1),
                                date(2026, 1, 31),
                                [(date(2026, 1, 12), D("5000")),
                                 (date(2026, 1, 25), D("5000"))])
    assert interest == D("2400.00")          # 120 000 × 0,24 / 12


def test_daily_interest_covers_only_the_days_of_the_segment():
    """Отрезок внутри месяца — по своим дням."""
    interest = accrued_interest(_daily(), D("100000"),
                                date(2026, 1, 10), date(2026, 1, 19), [])
    assert interest == D("1000.00")          # 10 дней × 100,00


def test_annual_interest_fills_a_partial_month_by_days():
    """Неполный месяц у годовой — пропорционально дням месяца."""
    interest = accrued_interest(_yearly(), D("120000"),
                                date(2026, 1, 11), date(2026, 1, 20), [])
    assert interest == D("774.19")           # 2 400 × 10 / 31


def test_segment_boundaries_are_inclusive_on_both_ends():
    """Обе границы отрезка входят в него: день — ровно один день начисления."""
    daily, yearly = _daily(), _yearly()
    assert accrued_interest(daily, D("100000"), date(2026, 1, 15),
                            date(2026, 1, 15), []) == D("100.00")
    assert accrued_interest(daily, D("100000"), date(2026, 1, 15),
                            date(2026, 1, 16), []) == D("200.00")
    assert accrued_interest(yearly, D("120000"), date(2026, 1, 15),
                            date(2026, 1, 15), []) == D("77.42")   # 2 400 / 31
    assert accrued_interest(daily, D("100000"), date(2026, 1, 16),
                            date(2026, 1, 15), []) == D("0")       # пустой отрезок


def test_payments_outside_the_segment_do_not_change_the_accrual():
    """Платежи вне отрезка [since, until] на начисление не влияют.

    Дни вне отрезка циклом не посещаются, а `paid` месяца берётся по границам
    отрезка, — поэтому платёж до `since` и платёж после `until` ничего не
    меняют (тикет 09, Код-3: условие на входе построения `by_day` было этому
    условию избыточно и убрано).
    """
    inside = [(date(2026, 2, 10), D("5000"))]
    outside = [(date(2026, 1, 5), D("7000")),       # до since
               (date(2026, 4, 12), D("9000"))]      # после until
    since, until = date(2026, 2, 1), date(2026, 3, 31)
    daily = accrued_interest(_daily(), D("100000"), since, until, inside)
    yearly = accrued_interest(_yearly(), D("100000"), since, until, inside)
    assert accrued_interest(_daily(), D("100000"), since, until,
                            inside + outside) == daily
    assert accrued_interest(_yearly(), D("100000"), since, until,
                            inside + outside) == yearly
    # Сами числа отрезка: 100 000 × 0,24 / 12 за февраль плюс март на 95 000
    # (после февральского платежа), и дни февраля–мартовские по 100,00/95,00.
    assert yearly == D("3940.00")
    assert daily == D("5734.01")


def test_every_day_is_rounded_on_its_own():
    """Округление — на каждое слагаемое, а не один раз на весь отрезок."""
    interest = accrued_interest(_daily(D("0.0007")), D("1001"),
                                date(2026, 1, 1), date(2026, 1, 10), [])
    # 0,7007 за день округляется до 0,70: десять дней — 7,00, а не 7,01.
    assert interest == D("7.00")


def test_every_month_is_rounded_on_its_own():
    """У годовой слагаемое — месяц, и месяц округляется сам по себе."""
    interest = accrued_interest(_yearly(D("0.001")), D("1000"),
                                date(2026, 1, 1), date(2026, 3, 31), [])
    # 0,0833… округляется до 0,08: три месяца — 0,24, а не 0,25 одним куском.
    assert interest == D("0.24")


def test_annual_interest_runs_on_the_balance_of_the_previous_month():
    """Бегущий остаток: начисленное одного месяца — база следующего."""
    interest = accrued_interest(_yearly(D("0.12")), D("100000"),
                                date(2026, 1, 1), date(2026, 3, 31), [])
    # 1 000,00 + 1 010,00 + 1 020,10 — как в прокате, проценты на проценты.
    assert interest == D("3030.10")


def test_no_balance_means_no_interest():
    """Нет остатка — нет начисления: годовая не уходит в минус."""
    for deal in (_daily(), _yearly()):
        assert accrued_interest(deal, D("0"), date(2026, 1, 1),
                                date(2026, 1, 31), []) == D("0")
        assert accrued_interest(deal, D("-5000"), date(2026, 1, 1),
                                date(2026, 1, 31), []) == D("0")


def test_payments_sharing_one_date_are_summed():
    """Две пары на одну дату — одно начисление от их суммы, а не от последней."""
    interest = accrued_interest(_daily(), D("100000"), date(2026, 1, 1),
                                date(2026, 1, 31),
                                [(date(2026, 1, 12), D("20000")),
                                 (date(2026, 1, 12), D("30000"))])
    # Просроченные вхождения сходятся в один день: 50 000 суммой, значит
    # 11 дней по 100,00 и 20 дней по 50,00, как и одним платежом.
    assert interest == D("2100.00")


def test_annual_payments_of_a_month_cut_the_next_one():
    """Платёж января уменьшает базу февраля: платежи месяца видит следующий."""
    deal = _yearly()
    paid = accrued_interest(deal, D("120000"), date(2026, 1, 1),
                            date(2026, 2, 28), [(date(2026, 1, 15), D("60000"))])
    untouched = accrued_interest(deal, D("120000"), date(2026, 1, 1),
                                 date(2026, 2, 28), [])
    # Январь одинаков в обоих случаях — 2 400,00; февраль разный:
    # (120 000 + 2 400 − 60 000) × 0,24 / 12 против (120 000 + 2 400) × 0,24 / 12.
    assert paid == D("3648.00")
    assert untouched == D("4848.00")
    assert paid < untouched


# --- начисление от события -------------------------------------------------

def _charge(uid: str = "суд", **kw) -> Charge:
    """Начисление по умолчанию: решение суда на 1 500,00 по займу."""
    base = dict(uid=uid, date=date(2026, 3, 10), amount=D("1500"), deal="заём",
                part=PENALTY, basis="решение")
    base.update(kw)
    return Charge(**base)


def _charge_book(*charges: Charge, **deal) -> Settlements:
    """Заём на 10 000,00 без ставки и его начисления: канон = тело плюс начисления."""
    return Settlements(counterparties=[_counterparty()], deals=[_deal(**deal)],
                       charges=list(charges))


def test_parts_are_the_declared_set_of_one_debt():
    """Части долга — закрытый набор, и значения его — термины глоссария."""
    assert PARTS == ("тело", "проценты", "неустойка", "издержки")


def test_charge_grows_the_canon_from_its_own_date():
    """Начисление растёт со своей даты: днём раньше канон ещё прежний."""
    book = _charge_book(_charge(date=date(2026, 3, 10)))
    validate(book)
    assert deal_balance(book, "заём", date(2026, 3, 9)) == D("10000")
    assert deal_balance(book, "заём", date(2026, 3, 10)) == D("11500")


def test_a_backdated_charge_recomputes_from_the_event_date():
    """Запись задним числом меняет расчёт с даты события, а не с даты записи."""
    book = _charge_book(_charge(date=date(2026, 2, 5)))
    validate(book)
    assert deal_balance(book, "заём", date(2026, 2, 4)) == D("10000")
    assert deal_balance(book, "заём", date(2026, 2, 5)) == D("11500")


def test_charge_and_payment_meet_in_one_canon():
    """Начисление и платёж — разные носители одного числа: канон видит оба."""
    book = Settlements(counterparties=[_counterparty()], deals=[_deal()],
                       movements=[Movement(date(2026, 3, 5), D("4000"), "заём")],
                       charges=[_charge(date=date(2026, 3, 10))])
    validate(book)
    assert deal_balance(book, "заём", date(2026, 3, 31)) == D("7500")


def test_every_part_is_accepted_and_basis_does_not_touch_the_calculation():
    """Часть обязательна и из набора; основание — свободная метка, не влияет."""
    for part in PARTS:
        book = _charge_book(_charge(uid=f"начисление-{part}", part=part))
        validate(book)
        assert deal_balance(book, "заём", date(2026, 3, 31)) == D("11500")
    with_basis = _charge_book(_charge(basis="штраф по постановлению"))
    without_basis = _charge_book(_charge(basis=None))
    validate(with_basis)
    validate(without_basis)
    assert with_basis.charges[0].basis == "штраф по постановлению"
    assert (deal_balance(with_basis, "заём", date(2026, 3, 31))
            == deal_balance(without_basis, "заём", date(2026, 3, 31)) == D("11500"))


def test_a_charge_on_the_month_border_changes_only_its_own_day():
    """Первое и последнее число: канон меняется ровно в день записи."""
    book = _charge_book(_charge(date=date(2026, 3, 1)),
                        _charge(uid="суд-позже", date=date(2026, 3, 31)))
    validate(book)
    assert deal_balance(book, "заём", date(2026, 2, 28)) == D("10000")
    assert deal_balance(book, "заём", date(2026, 3, 1)) == D("11500")
    assert deal_balance(book, "заём", date(2026, 3, 30)) == D("11500")
    assert deal_balance(book, "заём", date(2026, 3, 31)) == D("13000")


def test_a_book_without_charges_counts_as_before():
    """Пустой список начислений — канон прежний: новая запись ничего не меняет."""
    book = _charge_book()
    validate(book)
    assert deal_balance(book, "заём", START) == D("10000")


def test_charge_validation_names_the_reason():
    """Уид события, сумма, часть и сделка — каждая со своим текстом."""
    with pytest.raises(ValueError, match="начисление без уида события"):
        validate(_charge_book(_charge(uid="")))
    with pytest.raises(ValueError, match="сумма должна быть положительной"):
        validate(_charge_book(_charge(amount=D("0"))))
    with pytest.raises(ValueError, match="не входит в набор частей"):
        validate(_charge_book(_charge(part="проценты по займу")))
    with pytest.raises(ValueError, match="неизвестная сделка 'чужой'"):
        validate(_charge_book(_charge(deal="чужой")))


def test_charge_has_nothing_to_grow_at_a_regular_expense_and_a_claim():
    """Части живут у долга «сколько я должен»: расходу и требованию — отказ."""
    expense = Settlements(counterparties=[_counterparty()],
                          deals=[_deal(uid="аренда", amount=None)],
                          charges=[_charge(deal="аренда")])
    with pytest.raises(ValueError, match="у регулярного расхода остатка нет"):
        validate(expense)
    with pytest.raises(ValueError, match="у требования начисления не поддерживаются"):
        validate(_charge_book(_charge(), direction=OWED_TO_ME))


def test_one_event_is_one_record():
    """Уид события уникален: дубль падает, два уида — два начисления."""
    duplicate = _charge_book(_charge(uid="суд"),
                             _charge(uid="суд", date=date(2026, 4, 1)))
    with pytest.raises(ValueError, match="уид события 'суд' уже записан"):
        validate(duplicate)
    # Второе решение по тому же делу — другой уид: обе записи считаются.
    two = _charge_book(_charge(uid="суд"), _charge(uid="суд-2"))
    validate(two)
    assert deal_balance(two, "заём", date(2026, 3, 31)) == D("13000")


def _trigger_rule(uid: str = "правило", **kw) -> TriggerRule:
    """Триггер-правило по умолчанию: штраф 500,00 в неустойку, порогов нет."""
    base = dict(uid=uid, condition=TRIGGER_OVERDUE, part=PENALTY,
                basis="штраф", charge_amount=D("500"))
    base.update(kw)
    return TriggerRule(**base)


def test_a_charge_reference_to_a_rule_must_be_declared():
    """Ссылка на правило триггера сверяется с правилами сделки, а не выдумывается."""
    rule = _trigger_rule()
    validate(_charge_book(_charge(trigger="правило"), triggers=(rule,)))
    with pytest.raises(ValueError, match="правило триггера 'нет-такого' не объявлено"):
        validate(_charge_book(_charge(trigger="нет-такого"), triggers=(rule,)))
    # Зона передала уиды вместо правил: отказ текстом, а не падением.
    with pytest.raises(ValueError, match="триггер-правило без уида"):
        validate(_charge_book(_charge(trigger="правило"), triggers=("правило",)))


def test_one_trigger_case_is_one_record():
    """Случай правила — (сделка, уид правила, дата): второй факт — дубль."""
    rule = _trigger_rule()
    twice = _charge_book(_charge(uid="суд", trigger="правило"),
                         _charge(uid="суд-2", trigger="правило"), triggers=(rule,))
    with pytest.raises(ValueError, match="случай правила 'правило' от 2026-03-10"):
        validate(twice)
    other_day = _charge_book(_charge(uid="суд", trigger="правило"),
                             _charge(uid="суд-2", trigger="правило",
                                     date=date(2026, 3, 11)), triggers=(rule,))
    validate(other_day)
    assert deal_balance(other_day, "заём", date(2026, 3, 31)) == D("13000")
    # Уиды правил уникальны среди правил сделки, а не книги: одноимённое
    # правило другой сделки — другой случай, и обе записи легальны.
    two_deals = Settlements(
        counterparties=[_counterparty()],
        deals=[_deal(triggers=(rule,)),
               _deal(uid="второй", amount=D("2000"), triggers=(rule,))],
        charges=[_charge(uid="суд", trigger="правило"),
                 _charge(uid="суд-2", deal="второй", trigger="правило")])
    validate(two_deals)
    assert deal_balance(two_deals, "второй", date(2026, 3, 31)) == D("3500")


# --- сальдо и роль ---------------------------------------------------------

def _two_way_book() -> Settlements:
    """Банк: я должен 10 000 по заёму и он мне 4 000 по встречной сделке."""
    return Settlements(
        counterparties=[_counterparty()],
        deals=[_deal(amount=D("10000")),
               _deal(uid="встречная", amount=D("4000"), direction=OWED_TO_ME)],
    )


def test_saldo_is_reported_without_offsetting_the_deals():
    """Сальдо — отчётная величина: зачёт не производится, остатки остаются своими."""
    book = _two_way_book()
    assert counterparty_balance(book, "банк", START) == D("6000")
    assert deal_balance(book, "заём", START) == D("10000")
    assert deal_balance(book, "встречная", START) == D("4000")


def test_saldo_is_computed_from_movements_not_stored():
    """Сальдо считается из движений: заплатил — сальдо изменилось."""
    book = _two_way_book()
    book.movements.append(Movement(date(2026, 1, 20), D("2500"), "заём"))
    validate(book)
    assert counterparty_balance(book, "банк", date(2026, 1, 31)) == D("3500")


def test_saldo_includes_accrued_interest():
    """Сальдо включает начисленное: «сколько я должен» — один ответ, и ответ кредитора."""
    book = Settlements(
        counterparties=[_counterparty()],
        deals=[_deal(amount=D("10000"), start=date(2025, 7, 1),
                     rate_per_year=D("0.24"), rate_per_day=None)],
        movements=[Movement(date(2026, 1, 20), D("1000"), "заём")],
    )
    validate(book)
    on = date(2026, 1, 31)
    assert deal_balance(book, "заём", on) == D("10486.86")
    assert counterparty_balance(book, "банк", on) == D("10486.86")
    # «тело минус движения» — 9 000: сальдо больше ровно на начисленное
    assert counterparty_balance(book, "банк", on) - D("9000") == D("1486.86")


def test_role_is_derived_from_both_directions():
    """Роль выводится из суммы по сделкам в обе стороны и нигде не хранится."""
    book = _two_way_book()
    assert counterparty_role(book, "банк", START) == BOTH

    only_debt = Settlements(counterparties=[_counterparty()], deals=[_deal()])
    assert counterparty_role(only_debt, "банк", START) == CREDITOR

    only_claim = Settlements(counterparties=[_counterparty()],
                             deals=[_deal(direction=OWED_TO_ME)])
    assert counterparty_role(only_claim, "банк", START) == DEBTOR

    settled = Settlements(counterparties=[_counterparty()],
                          deals=[_deal(amount=D("1000"))],
                          movements=[Movement(date(2026, 1, 5), D("1000"), "заём")])
    assert counterparty_role(settled, "банк", date(2026, 1, 31)) is None


def test_role_turns_over_with_an_overpayment():
    """Переплата играет в обратную сторону: свои деньги у чужого — его долг."""
    book = Settlements(counterparties=[_counterparty()],
                       deals=[_deal(amount=D("10000"))],
                       movements=[Movement(date(2026, 1, 5), D("12000"), "заём")])
    validate(book)
    on = date(2026, 1, 31)
    assert deal_balance(book, "заём", on) == D("-2000")
    assert counterparty_balance(book, "банк", on) == D("-2000")
    assert counterparty_role(book, "банк", on) == DEBTOR

    # Зеркально: переплатили мне — теперь должен я.
    mirror = Settlements(
        counterparties=[_counterparty()],
        deals=[_deal(amount=D("4000"), direction=OWED_TO_ME)],
        movements=[Movement(date(2026, 1, 5), D("6000"), "заём", direction=IN)],
    )
    validate(mirror)
    assert counterparty_balance(mirror, "банк", on) == D("2000")
    assert counterparty_role(mirror, "банк", on) == CREDITOR


# --- передача долга --------------------------------------------------------

def _assigned_book(**kw) -> Settlements:
    """Долг 100 000 у банка передан коллектору, сумма выросла на 20 000."""
    base = dict(
        counterparties=[_counterparty(uid="банк", name="Банк"),
                        _counterparty(uid="коллектор", name="Коллектор", subtype="ПКО")],
        deals=[_deal(uid="заём", counterparty="коллектор", amount=D("120000"))],
        assignments=[Assignment(date(2026, 3, 1), "заём", from_holder="банк",
                                to_holder="коллектор", amount=D("100000"),
                                delta=D("20000"))],
    )
    base.update(kw)
    return Settlements(**base)                      # type: ignore[arg-type]


def test_assignment_creates_a_conditions_version():
    """Передача долга создаёт версию условий: прежние сохраняются, новые действуют."""
    book = _assigned_book()
    validate(book)
    before, after = date(2026, 2, 1), date(2026, 3, 2)
    assert deal_holder_at(book, "заём", before) == "банк"
    assert deal_amount_at(book, "заём", before) == D("100000")
    assert deal_holder_at(book, "заём", after) == "коллектор"
    assert deal_amount_at(book, "заём", after) == D("120000")
    assert counterparty_balance(book, "банк", before) == D("100000")
    assert counterparty_balance(book, "банк", after) == D("0")
    assert counterparty_balance(book, "коллектор", after) == D("120000")


def test_movement_remembers_who_was_paid_at_the_time():
    """Движение помнит, кому уплачено: платёж остаётся за прежним держателем.

    Поле движок читает: ссылка на необъявленного контрагента — ошибка.
    """
    book = _assigned_book(movements=[
        Movement(date(2026, 2, 10), D("40000"), "заём", paid_to="банк"),
    ])
    validate(book)
    assert deal_balance(book, "заём", date(2026, 3, 2)) == D("80000")

    book.movements[0].paid_to = "нет-такого"
    with pytest.raises(ValueError, match="кому уплачено"):
        validate(book)


def test_transfer_date_starts_the_new_version():
    """В саму дату передачи действуют уже новая версия и новый держатель."""
    book = _assigned_book(movements=[
        Movement(date(2026, 3, 1), D("40000"), "заём", paid_to="коллектор"),
    ])
    validate(book)
    assert deal_holder_at(book, "заём", date(2026, 3, 1)) == "коллектор"
    assert deal_amount_at(book, "заём", date(2026, 3, 1)) == D("120000")
    assert deal_balance(book, "заём", date(2026, 3, 1)) == D("80000")


def test_an_assignment_neither_moves_the_debt_start_nor_resets_the_accrual():
    """Передача долга начала не двигает, а начисленное через неё не обнуляется.

    Начало долга — дата возникновения, а не держателя: у одного долга не бывает
    двух дат начала, и проценты, накопленные до передачи, остаются накопленными.
    """
    book = _assigned_book(
        deals=[_deal(uid="заём", counterparty="коллектор", amount=D("120000"),
                     start=date(2025, 6, 1), rate_per_year=D("0.24"),
                     rate_per_day=None)])
    validate(book)
    assert book.deals[0].start == date(2025, 6, 1)
    # До передачи: тело версии 100 000 плюс начисленное с начала долга.
    assert deal_balance(book, "заём", date(2026, 2, 28)) == D("119509.25")
    # После: тело версии 120 000 плюс начисленное с начала долга, в которое
    # дельта передачи (01.03) входит базой марта: 19 509,25 + 2 790,19.
    # Перезапуска от 01.03.2026 нет — с него началось бы 120 000 × 0,24 / 12.
    assert deal_balance(book, "заём", date(2026, 3, 31)) == D("142299.44")


def _transfer_book(assign: date, movements: list[Movement]) -> Settlements:
    """Долг 100 000 (после передачи — 120 000) под 24 % с начала 01.06.2025."""
    return _assigned_book(
        deals=[_deal(uid="заём", counterparty="коллектор", amount=D("120000"),
                     start=date(2025, 6, 1), rate_per_year=D("0.24"),
                     rate_per_day=None, wallet="карта",
                     schedule=ScheduleRule(days=(20,), payment=D("1000")))],
        wallets=[Wallet("карта", "Карта", "карта", D("0"))],
        assignments=[Assignment(assign, "заём", from_holder="банк",
                                to_holder="коллектор", amount=D("100000"),
                                delta=D("20000"))],
        movements=movements)


def test_an_assignment_before_the_window_enters_the_accrual_base():
    """Дельта передачи до окна входит в базу с даты передачи, а не с открытия.

    Тело на конец декабря уже 120 000, но проценты с сентября идут на тело с
    дельтой: канон на открытии и основание проката — одно число.
    """
    book = _transfer_book(
        date(2025, 9, 1),
        [Movement(date(2026, 1, 20), D("1000"), "заём",
                  occurrence=date(2026, 1, 20))])
    validate(book)
    assert deal_balance(book, "заём", date(2025, 12, 31)) == D("136517.21")
    roll = roll_deals(book, START, D(0), max_months=1)
    assert roll.months[0].balances["заём"] == D("138247.55")
    assert deal_balance(book, "заём", date(2026, 1, 31)) == \
        roll.months[0].balances["заём"]


def test_an_assignment_inside_the_window_enters_the_accrual_of_its_month():
    """Передача внутри окна: дельта входит в базу своего месяца и в остаток.

    Март считается на теле с дельтой, а не на прежнем, — и прокат, и канон
    дают одно и то же число.
    """
    book = _transfer_book(
        date(2026, 3, 1),
        [Movement(date(2026, month, 20), D("1000"), "заём",
                  occurrence=date(2026, month, 20)) for month in (1, 2, 3)])
    validate(book)
    roll = roll_deals(book, START, D(0), max_months=3)
    assert roll.months[2].balances["заём"] == D("139239.04")
    assert deal_balance(book, "заём", date(2026, 3, 31)) == \
        roll.months[2].balances["заём"]
    # До передачи — прежнее тело: оба числа сходятся и в месяцы до неё.
    assert roll.months[0].balances["заём"] == \
        deal_balance(book, "заём", date(2026, 1, 31))
    assert roll.months[1].balances["заём"] == \
        deal_balance(book, "заём", date(2026, 2, 28))


def test_an_assignment_mid_month_enters_the_whole_months_base():
    """Дельта передачи середины месяца входит в базу месяца целиком.

    Передача 25.03 даёт те же числа, что и 01.03: долг вырос, а не был
    уплачен (решение `deal-balance-canon`, вариант 11), поэтому дельта идёт
    в базу своего месяца по своей дате — без пропорции дням и без перезапуска
    начисления с даты передачи.
    """
    def movements() -> list[Movement]:
        return [Movement(date(2026, month, 20), D("1000"), "заём",
                         occurrence=date(2026, month, 20)) for month in (1, 2, 3)]

    mid = _transfer_book(date(2026, 3, 25), movements())
    first = _transfer_book(date(2026, 3, 1), movements())
    validate(mid)
    validate(first)
    # До марта версии тела ещё нет — обе книги дают одно число.
    assert deal_balance(mid, "заём", date(2026, 2, 28)) == \
        deal_balance(first, "заём", date(2026, 2, 28))
    # Март считается на теле с дельтой: середина месяца не даёт ни меньшей
    # базы (пропорция дням), ни перезапуска начисления с даты передачи.
    assert deal_balance(mid, "заём", date(2026, 3, 31)) == D("139239.04")
    assert deal_balance(first, "заём", date(2026, 3, 31)) == D("139239.04")
    # Прокат того же числа — одна формулировка у обоих носителей.
    roll = roll_deals(mid, START, D(0), max_months=3)
    assert roll.months[2].balances["заём"] == D("139239.04")


def test_holder_and_amount_agree_with_the_last_assignment():
    """Сумма и держатель сделки — одно число и одно имя с записью, а не два."""
    book = _assigned_book()
    book.deals[0].counterparty = "банк"
    with pytest.raises(ValueError, match="держатель"):
        validate(book)


def test_two_transfers_on_one_date_are_ambiguous():
    """Две передачи долга в одну дату: какая последняя — не определить."""
    book = _assigned_book()
    book.assignments.append(Assignment(date(2026, 3, 1), "заём", from_holder="банк",
                                       to_holder="коллектор", amount=D("100000"),
                                       delta=D("20000")))
    with pytest.raises(ValueError, match="в одну дату"):
        validate(book)


def test_assignment_to_undeclared_holder_is_rejected():
    book = _assigned_book()
    book.assignments[0].to_holder = "нет-такого"
    with pytest.raises(ValueError, match="новый держатель"):
        validate(book)


def test_deal_sum_and_the_last_version_are_one_number():
    """Сумма сделки и версия из записи о передаче — одно число, а не два."""
    book = _assigned_book()
    book.deals[0].amount = D("130000")          # а версия говорит 100 000 + 20 000
    with pytest.raises(ValueError, match="расходится с последней"):
        validate(book)


def test_regular_expense_cannot_be_assigned():
    """У регулярного расхода передавать нечего: закрывать нечего."""
    book = Settlements(
        counterparties=[_counterparty(), _counterparty(uid="коллектор",
                                                       name="Коллектор",
                                                       subtype="ПКО")],
        deals=[_deal(uid="аренда", amount=None)],
        assignments=[Assignment(date(2026, 3, 1), "аренда", from_holder="банк",
                                to_holder="коллектор", amount=D("0"))],
    )
    with pytest.raises(ValueError, match="закрывать нечего"):
        validate(book)


# --- касса -----------------------------------------------------------------

def test_payment_references_counterparty_and_purpose():
    """Платёж кассы ссылается на контрагента и несёт назначение — вместо строки."""
    scenario = Scenario(
        accounts=[Account("карта", D("1000"))],
        payments=[Payment(date(2026, 1, 10), D("300"), account="карта",
                          counterparty="родня", purpose="еда")],
    )
    assert run(scenario, main="карта").end_balance == D("700")


def test_payment_without_counterparty_is_rejected():
    """Платёж без контрагента не бывает: нужен уид контрагента."""
    scenario = Scenario(accounts=[Account("карта", D("1000"))],
                        payments=[Payment(date(2026, 1, 10), D("300"), account="карта")])
    with pytest.raises(ValueError, match="без контрагента"):
        run(scenario, main="карта")


def test_family_transfer_is_an_expense_from_the_owners_cash():
    """Передача члену семьи — расход владельца с назначением; чужой кошелёк не считается."""
    book = Settlements(
        counterparties=[_counterparty(uid="родня", name="Член семьи", kind=PERSON,
                                      subtype="родственник", groups=("семья",))],
        wallets=[Wallet("карта", "Карта", "карта", D("50000"))],
        deals=[_deal(uid="передачи", amount=None, counterparty="родня")],
        movements=[Movement(date(2026, 1, 5), D("8000"), "передачи",
                            benefit_for="родня", purpose="еда")],
    )
    validate(book)
    assert deal_balance(book, "передачи", date(2026, 1, 31)) is None
    assert liquidity(book) == D("50000")

    scenario = Scenario(
        accounts=[Account("карта", D("50000"))],
        payments=[Payment(date(2026, 1, 5), D("8000"), account="карта",
                          counterparty="родня", purpose="еда")],
    )
    assert run(scenario, main="карта").end_balance == D("42000")


# --- книга целиком ---------------------------------------------------------

def test_the_model_holds_together():
    """Полная книга: кредитный кошелёк, регулярный расход, второй приоритет, копилка."""
    book = Settlements(
        counterparties=[_counterparty(),
                        _counterparty(uid="мфо", name="МФО", subtype="МФО"),
                        _counterparty(uid="арендодатель", name="Арендодатель",
                                      subtype="прочее")],
        wallets=[Wallet("карта", "Карта", "карта", D("20000")),
                 Wallet("кредитка", "Кредитка", "карта", D("-15000"),
                        is_credit=True, limit=D("60000"))],
        deals=[_deal(amount=D("50000"), start=START, rate_per_year=D("0.2"),
                     wallet="карта", closure_unit="копилка", kind="заём",
                     schedule=ScheduleRule(days=(15,))),
               _deal(uid="взыскание", counterparty="мфо", amount=D("30000"),
                     second_priority=True, closure_unit="копилка", kind="цессия"),
               _deal(uid="аренда", counterparty="арендодатель", amount=None,
                     kind="регулярный расход")],
    )
    validate(book)
    assert liquidity(book) == D("20000")
    assert counterparty_role(book, "мфо", START) == CREDITOR
    # Канон остатка: долг начался 01.01, поэтому на конец этого дня по нему
    # уже день начисления — 50 000 × 0,2 / 12 / 31.
    assert counterparty_balance(book, "банк", START) == D("50026.88")
    assert deal_balance(book, "аренда", START) is None


# --- правила зоны: формы, версии, валидация (02) -----------------------------

def _penalty(*steps, cap=..., effective=None) -> PenaltyRule:
    """Правило неустойки: ступени и потолок; без потолка форма не живёт."""
    if cap is ...:
        cap = PenaltyCap(CAP_NONE)
    return PenaltyRule(steps=tuple(steps), cap=cap, effective=effective)


def _penalty_book(*rules, **deal) -> Settlements:
    return Settlements(counterparties=[_counterparty()],
                       deals=[_deal(penalties=rules, **deal)])


def test_penalty_scale_forms_pass_validation():
    """Шкала с порогом 0 и без, потолок всех трёх форм — всё легально."""
    zero_first = _penalty(PenaltyStep(0, D("0.01")), PenaltyStep(30, D("0.001")))
    later_start = _penalty(PenaltyStep(15, D("0.005")),
                           cap=PenaltyCap(CAP_SUM, D("5000")))
    share = _penalty(PenaltyStep(1, D("0.01")),
                     cap=PenaltyCap(CAP_SHARE, D("0.5")))
    for rule in (zero_first, later_start, share):
        validate(_penalty_book(rule))


def test_penalty_scale_validation_names_the_reason():
    """Убывающие ступени, отрицательная ставка, чужой потолок — каждый со своим."""
    with pytest.raises(ValueError, match="ступени должны возрастать"):
        validate(_penalty_book(_penalty(PenaltyStep(30, D("0.01")),
                                        PenaltyStep(10, D("0.001")))))
    with pytest.raises(ValueError, match="порог ступени не может быть отрицательным"):
        validate(_penalty_book(_penalty(PenaltyStep(-1, D("0.01")))))
    with pytest.raises(ValueError, match="ставка ступени не может быть отрицательной"):
        validate(_penalty_book(_penalty(PenaltyStep(0, D("-0.01")))))
    with pytest.raises(ValueError, match="потолок обязателен"):
        validate(_penalty_book(_penalty(PenaltyStep(0, D("0.01")), cap=None)))
    with pytest.raises(ValueError, match="не входит в набор форм"):
        validate(_penalty_book(_penalty(PenaltyStep(0, D("0.01")),
                                        cap=PenaltyCap("половина"))))
    with pytest.raises(ValueError, match="„без потолка“ не бывает значения"):
        validate(_penalty_book(_penalty(PenaltyStep(0, D("0.01")),
                                        cap=PenaltyCap(CAP_NONE, D("100")))))
    with pytest.raises(ValueError, match="значение должно быть положительным"):
        validate(_penalty_book(_penalty(PenaltyStep(0, D("0.01")),
                                        cap=PenaltyCap(CAP_SUM, D("0")))))
    with pytest.raises(ValueError, match="ступени обязательны"):
        validate(_penalty_book(_penalty()))


def test_allocation_order_is_a_full_permutation():
    """Порядок распределения — все четыре части без повторов, иначе текст."""
    order = AllocationRule(("издержки", "неустойка", "проценты", "тело"))
    validate(Settlements(counterparties=[_counterparty()],
                         deals=[_deal(allocations=(order,))]))
    doubled = AllocationRule(("тело", "тело", "проценты", "неустойка"))
    with pytest.raises(ValueError, match="перестановка четырёх частей без повторов"):
        validate(Settlements(counterparties=[_counterparty()],
                             deals=[_deal(allocations=(doubled,))]))
    short = AllocationRule(("тело", "проценты", "неустойка"))
    with pytest.raises(ValueError, match="перестановка четырёх частей без повторов"):
        validate(Settlements(counterparties=[_counterparty()],
                             deals=[_deal(allocations=(short,))]))


def _trigger_book(*rules, **deal) -> Settlements:
    return Settlements(counterparties=[_counterparty()],
                       deals=[_deal(triggers=rules, **deal)])


def test_trigger_forms_pass_validation():
    """Триггер со штрафом-суммой и со штрафом-долей, пороговые условия — легальны."""
    by_sum = _trigger_rule(condition=TRIGGER_OVERDUE, charge_amount=D("500"))
    by_share = _trigger_rule(uid="доля", condition=TRIGGER_OVERDUE,
                             charge_amount=None, charge_percent=D("0.1"))
    by_days = _trigger_rule(uid="порог-дней", condition=TRIGGER_OVERDUE_DAYS,
                            threshold_days=30)
    by_amount = _trigger_rule(uid="порог-суммы", condition=TRIGGER_OVERDUE_SUM,
                              threshold_amount=D("1000"))
    dated = [dict(effective=date(2026, 1, 1)), dict(effective=date(2026, 2, 1)),
             dict(effective=date(2026, 3, 1)), dict(effective=date(2026, 4, 1))]
    book_rules = tuple(
        _trigger_rule(uid=r.uid, condition=r.condition, part=r.part, basis=r.basis,
                      threshold_days=r.threshold_days,
                      threshold_amount=r.threshold_amount,
                      charge_amount=r.charge_amount,
                      charge_percent=r.charge_percent, **kw)
        for r, kw in zip((by_sum, by_share, by_days, by_amount), dated))
    validate(_trigger_book(*book_rules))


def test_trigger_validation_names_the_reason():
    """Условие, штраф, порог, уид и основание — каждый отказ называет."""
    with pytest.raises(ValueError, match="не входит в набор условий"):
        validate(_trigger_book(_trigger_rule(condition="звонок коллектора")))
    with pytest.raises(ValueError, match="не входит в набор частей"):
        validate(_trigger_book(_trigger_rule(part="пени")))
    with pytest.raises(ValueError, match="основание обязательно"):
        validate(_trigger_book(_trigger_rule(basis="")))
    with pytest.raises(ValueError, match="штраф задаётся ровно одним из"):
        validate(_trigger_book(_trigger_rule(charge_amount=D("500"),
                                             charge_percent=D("0.1"))))
    with pytest.raises(ValueError, match="штраф задаётся ровно одним из"):
        validate(_trigger_book(_trigger_rule(charge_amount=None)))
    with pytest.raises(ValueError, match="порог должен быть задан и быть "
                                         "положительным"):
        validate(_trigger_book(_trigger_rule(condition=TRIGGER_OVERDUE_DAYS)))
    with pytest.raises(ValueError, match="порог должен быть задан и быть "
                                         "положительным"):
        validate(_trigger_book(_trigger_rule(condition=TRIGGER_OVERDUE_SUM,
                                             threshold_amount=D("-1"))))
    with pytest.raises(ValueError, match="уид 'правило' уже занят"):
        validate(_trigger_book(_trigger_rule(), _trigger_rule()))
    with pytest.raises(ValueError, match="триггер-правило без уида"):
        validate(_trigger_book(_trigger_rule(uid="")))


def test_rule_versions_are_dated_and_ordered():
    """Датированные версии идут по возрастанию; версия без даты — только одна."""
    first = _penalty(PenaltyStep(0, D("0.01")), effective=date(2026, 1, 1))
    second = _penalty(PenaltyStep(0, D("0.005")), effective=date(2026, 3, 1))
    validate(_penalty_book(first, second))
    with pytest.raises(ValueError, match="версия без даты действия"):
        validate(_penalty_book(_penalty(PenaltyStep(0, D("0.01"))), second))
    with pytest.raises(ValueError, match="даты действий должны возрастать"):
        validate(_penalty_book(first, first))


def test_rule_at_picks_the_version_of_its_own_dates():
    """Правило обслуживает свои даты: до — прежнее, с даты — новое, после — новое."""
    old = _penalty(PenaltyStep(0, D("0.01")), effective=date(2026, 1, 1))
    new = _penalty(PenaltyStep(0, D("0.005")), effective=date(2026, 3, 1))
    rules = (old, new)
    assert rule_at(rules, date(2025, 12, 31)) is None
    assert rule_at(rules, date(2026, 1, 1)) is old
    assert rule_at(rules, date(2026, 2, 28)) is old
    assert rule_at(rules, date(2026, 3, 1)) is new
    assert rule_at(rules, date(2027, 1, 1)) is new
    # Правило из будущего легально: до своей даты оно не действует.
    future = _penalty(PenaltyStep(0, D("0.02")), effective=date(2027, 1, 1))
    assert rule_at((future,), date(2026, 6, 1)) is None
    assert rule_at((future,), date(2027, 1, 1)) is future


def test_a_rule_without_a_date_acts_over_the_whole_line():
    """Одиночная версия без даты действует весь период; пустой список — None."""
    timeless = _penalty(PenaltyStep(0, D("0.01")))
    assert rule_at((timeless,), START) is timeless
    assert rule_at((timeless,), date(2030, 1, 1)) is timeless
    assert rule_at((), START) is None
    with pytest.raises(ValueError, match="версия без даты действия"):
        rule_at((timeless, _penalty(PenaltyStep(0, D("0.02")),
                                    effective=date(2026, 1, 1))), START)


def test_no_rule_is_not_the_same_as_no_cap():
    """«Правила нет» и «без потолка» — разные вещи: None против известной формы."""
    assert rule_at((), START) is None                     # правила нет — пробел
    none_cap = _penalty(PenaltyStep(0, D("0.01")))
    validate(_penalty_book(none_cap))                     # «без потолка» — форма
    assert none_cap.cap.kind == CAP_NONE
    assert CAP_NONE in CAPS and len(CAPS) == 3


def test_trigger_thresholds_are_declared_forms():
    """Набор условий — закрытый, и все четыре условия проходят форму."""
    assert len(TRIGGERS) == 4
    assert TRIGGER_OVERDUE in TRIGGERS and TRIGGER_OVERDUE_SUM in TRIGGERS


# --- распределение платежа по частям (03) ------------------------------------

def _order(*parts: str) -> tuple[str, ...]:
    return parts or PARTS


def test_allocate_payment_follows_the_zone_order():
    """Части гасятся по порядку зоны, в пределах их остатков."""
    shares = allocate_payment(D("700"), ("неустойка", "проценты", "тело", "издержки"),
                              {BODY: D("10000"), INTEREST: D("500"),
                               PENALTY: D("300"), COSTS: D("50")})
    assert shares == {PENALTY: D("300"), INTEREST: D("400"),
                      BODY: D("0"), COSTS: D("0")}


def test_allocate_payment_never_drives_parts_but_body_below_zero():
    """Части, кроме тела, в минус не уходят: остаток части — потолок доли."""
    shares = allocate_payment(D("100"), _order(),
                              {BODY: D("0"), INTEREST: D("0"),
                               PENALTY: D("0"), COSTS: D("0")})
    assert shares == {BODY: D("100"), INTEREST: D("0"),
                      PENALTY: D("0"), COSTS: D("0")}     # избыток несёт тело
    overpaid = allocate_payment(D("100"), _order(),
                                {BODY: D("-50"), INTEREST: D("0"),
                                 PENALTY: D("0"), COSTS: D("0")})
    assert overpaid[BODY] == D("100")            # минус телу места не даёт
    assert sum(overpaid.values()) == D("100")


def test_payment_bigger_than_all_parts_goes_to_the_body():
    """Платёж больше суммы частей гасит их все, избыток уходит в тело (R6-Q33)."""
    shares = allocate_payment(D("11200"), _order(),
                              {BODY: D("10000"), INTEREST: D("500"),
                               PENALTY: D("300"), COSTS: D("0")})
    assert shares == {BODY: D("10400"), INTEREST: D("500"),
                      PENALTY: D("300"), COSTS: D("0")}
    assert sum(shares.values()) == D("11200")    # раскладка не теряет деньги


def test_allocation_lines_are_validated_with_a_reason():
    """Разбивка: часть объявлена, строка положительна, без повторов, сумма сходится."""
    def movement(**kw) -> Movement:
        base = dict(date=date(2026, 3, 5), amount=D("1000"), deal="заём")
        base.update(kw)
        return Movement(**base)

    book = Settlements(counterparties=[_counterparty()], deals=[_deal()],
                       movements=[movement(allocation=(PartPayment(BODY, D("1000")),))])
    validate(book)
    with pytest.raises(ValueError, match="сумма разбивки 900 не равна сумме "
                                        "движения 1000"):
        validate(Settlements(counterparties=[_counterparty()], deals=[_deal()],
                             movements=[movement(allocation=(
                                 PartPayment(BODY, D("900")),))]))
    with pytest.raises(ValueError, match="часть 'пени' не входит в набор частей"):
        validate(Settlements(counterparties=[_counterparty()], deals=[_deal()],
                             movements=[movement(allocation=(
                                 PartPayment("пени", D("1000")),))]))
    with pytest.raises(ValueError, match="сумма должна быть положительной"):
        validate(Settlements(counterparties=[_counterparty()], deals=[_deal()],
                             movements=[movement(allocation=(
                                 PartPayment(BODY, D("0")),))]))
    with pytest.raises(ValueError, match="часть 'тело' встречается дважды"):
        validate(Settlements(counterparties=[_counterparty()], deals=[_deal()],
                             movements=[movement(allocation=(
                                 PartPayment(BODY, D("500")),
                                 PartPayment(BODY, D("500")),))]))


def _rule(**kw) -> ScheduleRule:
    base = dict(days=(20,), payment=D("1000"), shift_weekend=False,
                start=date(2026, 1, 1))
    base.update(kw)
    return ScheduleRule(**base)


def _scheduled_book(movements: list[Movement], **deal) -> Settlements:
    return Settlements(counterparties=[_counterparty()],
                       deals=[_deal(amount=D("10000"), schedule=_rule(), **deal)],
                       movements=movements)


def test_a_loose_payment_settles_the_oldest_overdue_first():
    """Несвязанный расход гасит самые старые просрочки FIFO, в пределах суммы."""
    book = _scheduled_book([Movement(date(2026, 2, 25), D("1500"), "заём")])
    validate(book)
    jan, feb, mar = occurrences(book, "заём", START, date(2026, 3, 31))
    assert jan.status == PAID_LATE and jan.paid == D("1000")  # самая старая — вся
    assert feb.status == EXPECTED and feb.paid == D("500")    # вторая — остатком
    assert mar.status == EXPECTED and mar.paid == D("0")      # будущее не тронуто
    assert feb.remaining == D("500")


def test_a_loose_payment_on_the_due_day_settles_nothing():
    """В день срока вхождение ещё не просрочено — несвязанный расход его не гасит."""
    book = _scheduled_book([Movement(date(2026, 1, 20), D("1000"), "заём")])
    validate(book)
    jan, feb = occurrences(book, "заём", START, date(2026, 2, 28))
    assert jan.status == EXPECTED and jan.paid == D("0")
    assert feb.status == EXPECTED


def test_a_loose_payment_beyond_overdue_settles_no_future():
    """Излишек сверх просрочек — платёж, а не закрытие: будущие вхождения целы."""
    book = _scheduled_book([Movement(date(2026, 2, 25), D("5000"), "заём")])
    validate(book)
    jan, feb, mar = occurrences(book, "заём", START, date(2026, 3, 31))
    assert (jan.paid, feb.paid, mar.paid) == (D("1000"), D("1000"), D("0"))
    assert jan.status == PAID_LATE and feb.status == PAID_LATE
    assert mar.status == EXPECTED


def test_skipped_and_postponed_overdue_are_settled_by_their_own_rules():
    """Пропущенное не гасится — платить не будут; перенесённое гасится по новому сроку."""
    skipped = Settlements(
        counterparties=[_counterparty()], deals=[_deal(amount=D("10000"),
                                                       schedule=_rule())],
        edits=[OccurrenceEdit("заём", date(2026, 1, 20), skipped=True)],
        movements=[Movement(date(2026, 1, 25), D("1000"), "заём")])
    validate(skipped)
    jan, feb = occurrences(skipped, "заём", START, date(2026, 2, 28))
    assert jan.status == SKIPPED and jan.paid == D("0")
    assert feb.paid == D("0")                    # 25.01 гасить больше нечего

    moved = Settlements(
        counterparties=[_counterparty()], deals=[_deal(amount=D("10000"),
                                                       schedule=_rule())],
        edits=[OccurrenceEdit("заём", date(2026, 1, 20), postponed=True,
                              moved_to=date(2026, 1, 24))],
        movements=[Movement(date(2026, 1, 25), D("1000"), "заём")])
    validate(moved)
    jan = occurrences(moved, "заём", START, date(2026, 1, 31))[0]
    assert jan.due == date(2026, 1, 24) and jan.status == PAID_LATE


def test_a_return_never_settles_an_occurrence():
    """Возврат — не уплата: вхождения он не закрывает."""
    book = _scheduled_book([Movement(date(2026, 2, 25), D("1000"), "заём",
                                     direction=IN)])
    validate(book)
    jan, feb = occurrences(book, "заём", START, date(2026, 2, 28))
    assert jan.paid == D("0") and feb.paid == D("0")


def test_partial_overdue_settlement_accumulates_across_movements():
    """Два движения по одной просрочке добивают её вместе: остаток сходится."""
    book = _scheduled_book([Movement(date(2026, 1, 25), D("400"), "заём"),
                            Movement(date(2026, 1, 26), D("600"), "заём")])
    validate(book)
    jan = occurrences(book, "заём", START, date(2026, 1, 31))[0]
    assert jan.paid == D("1000") and jan.status == PAID_LATE
    assert jan.actual == date(2026, 1, 26)       # закрыл последний
