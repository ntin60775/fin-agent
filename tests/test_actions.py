"""Тесты действий и цены варианта — синтетические фикстуры, без личных данных."""
from __future__ import annotations

import copy
from datetime import date
from decimal import Decimal as D

import pytest

from finance_core import (LEGAL, Bridge, Counterparty, Deal, Direct,
                          ForecastInput, ImpossibleAction, Income, Movement,
                          OccurrenceEdit, Payment, Prepay, ScheduleRule,
                          Settlements, Move, Variant, Wallet, applied, baseline,
                          deal_balance, facts, forecast, horizon, impossible,
                          occurrences, price, prices, roll_deals, roll_months,
                          validate, variants)

START = date(2026, 1, 1)


def _wallet(uid: str = "карта", balance: D = D("0"), **kw) -> Wallet:
    base = dict(uid=uid, name=uid, kind="карта", balance=balance, available=True,
                is_credit=False)
    base.update(kw)
    return Wallet(**base)


def _deal(uid: str = "заём", amount: D | None = D("20000"), wallet: str = "карта",
          **kw) -> Deal:
    base = dict(uid=uid, title=uid, counterparty="банк", amount=amount,
                rate_per_day=D("0.005"), wallet=wallet,
                schedule=ScheduleRule(days=(20,), payment=D("3000")))
    base.update(kw)
    return Deal(**base)


def _book(*deals, wallets=None, counterparties=(), movements=(), edits=()) -> Settlements:
    return Settlements(
        counterparties=[Counterparty(uid="банк", name="Банк", kind=LEGAL,
                                     subtype="банк", groups=("долги",)),
                        Counterparty(uid="арендодатель", name="Арендодатель",
                                     kind=LEGAL, subtype="прочее"),
                        *counterparties],
        wallets=list(wallets) if wallets is not None else [_wallet()],
        deals=list(deals), movements=list(movements), edits=list(edits))


def _inp(book: Settlements, wallets=None, **kw) -> ForecastInput:
    base = dict(book=book, start=START,
                wallets=list(wallets) if wallets is not None else list(book.wallets),
                living_floor=D("0"), max_months=6)
    base.update(kw)
    return ForecastInput(**base)


def _hole_input() -> tuple[ForecastInput, Base]:
    """Сценарий с дырой: кошелёк в минусе, рядом деньги и кредитка."""
    wallets = [_wallet("карта", D("-5000")), _wallet("наличные", D("2000")),
               _wallet("кредитка", D("0"), is_credit=True, limit=D("100000"))]
    book = _book(_deal(), _deal(uid="аренда", amount=None, wallet="наличные",
                                schedule=ScheduleRule(days=(5,), payment=D("1500"))),
                 wallets=wallets)
    validate(book)
    inp = _inp(book, wallets,
               incomes=[_income(m) for m in range(1, 7)])
    return inp, baseline(inp)


def _income(month: int, account: str = "карта") -> Income:
    """Приход 25-го числа месяца — деньги, до которых дыра не дотянулась."""
    return Income(date(2026, month, 25), D("20000"), account)


# --- действия --------------------------------------------------------------

def test_action_is_an_object_and_all_four_kinds_are_supported():
    """Действие — объект: перенос, отложение, мост, досрочка, согласие владельца."""
    wallets = [_wallet("карта", D("-1000")),
               _wallet("кредитка", D("0"), is_credit=True, limit=D("100000"))]
    book = _book(_deal(uid="заём", amount=D("10000")),
                 _deal(uid="аренда", amount=None, wallet="кредитка",
                       schedule=ScheduleRule(days=(5,), payment=D("1000"))),
                 wallets=wallets)
    inp = _inp(book, wallets, incomes=[_income(1)])
    base = baseline(inp)
    variant = Variant("всё сразу", (
        Move(deal="заём", planned=date(2026, 1, 20), to=date(2026, 1, 25)),
        Prepay(deal="заём", date=date(2026, 1, 25), amount=D("1000")),
        Bridge(counterparty="банк", wallet="кредитка", date=START,
               amount=D("2000"), days=10, rate_per_day=D("0.001")),
        Direct(),
    ))
    moved = applied(base, variant)

    assert moved.book.edits == [OccurrenceEdit(deal="заём", planned=date(2026, 1, 20),
                                               postponed=True,
                                               moved_to=date(2026, 1, 25))]
    assert [m.deal for m in moved.book.movements] == ["заём"]
    assert [d.uid for d in moved.book.deals] == ["заём", "аренда",
                                                 "мост-2026-01-01-кредитка"]
    assert [p.amount for p in moved.one_offs] == [D("1000")]
    assert [i.amount for i in moved.incomes] == [D("20000"), D("2000")]
    assert moved.consent_to_second is True


def test_a_move_sets_the_moved_date_and_a_postponement_is_the_same_without_a_date():
    """Перенос ставит перенесённую дату; отложение — тот же перенос без даты."""
    inp = _inp(_book(_deal()), wallets=[_wallet("карта", D("50000"))])
    base = baseline(inp)

    moved = applied(base, Variant("перенос", (
        Move(deal="заём", planned=date(2026, 1, 20), to=date(2026, 1, 27)),)))
    [occ] = occurrences(moved.book, "заём", date(2026, 1, 20), date(2026, 1, 20))
    assert (occ.status, occ.moved, occ.due) == ("перенесён", date(2026, 1, 27),
                                                date(2026, 1, 27))
    roll = roll_deals(moved.book, START, D("0"), max_months=1)
    assert [p.date for p in roll.months[0].payments] == [date(2026, 1, 27)]

    postponed = applied(base, Variant("отложение", (
        Move(deal="заём", planned=date(2026, 1, 20)),)))
    [occ] = occurrences(postponed.book, "заём", date(2026, 1, 20), date(2026, 1, 20))
    assert (occ.status, occ.moved, occ.due, occ.payable) == ("перенесён", None,
                                                             None, False)
    assert roll_deals(postponed.book, START, D("0"),
                      max_months=1).months[0].payments == []


def test_prepayments_are_checked_together_not_one_by_one():
    """Две досрочки вместе могут обещать больше, чем есть: и проверка по сумме."""
    wallets = [_wallet("карта", D("10000"))]
    inp = _inp(_book(_deal(uid="заём", amount=D("10000")), wallets=wallets),
               wallets=wallets)
    base = baseline(inp)
    # Свободные деньги месяца: 10 000 остатка минус платёж 3 000 по графику.
    assert base.forecast.months[0].free == D("7000")

    over_deal = Variant("две досрочки по одной сделке", (
        Prepay(deal="заём", date=date(2026, 1, 15), amount=D("6000")),
        Prepay(deal="заём", date=date(2026, 1, 25), amount=D("6000")),
    ))
    assert "вместе больше остатка" in impossible(base, over_deal)
    with pytest.raises(ImpossibleAction, match="вместе больше остатка"):
        applied(base, over_deal)

    over_month = Variant("две досрочки одного месяца", (
        Prepay(deal="заём", date=date(2026, 1, 15), amount=D("4000")),
        Prepay(deal="заём", date=date(2026, 1, 25), amount=D("4000")),
    ))
    assert "вместе больше свободных денег" in impossible(base, over_month)

    fits = Variant("обе по силам", (
        Prepay(deal="заём", date=date(2026, 1, 15), amount=D("3000")),
        Prepay(deal="заём", date=date(2026, 1, 25), amount=D("4000")),
    ))
    assert impossible(base, fits) is None


def test_two_bridges_from_one_wallet_are_checked_against_its_limit_together():
    """Два моста с одного кошелька вместе не больше его доступного лимита."""
    wallets = [_wallet("карта", D("-1000")),
               _wallet("кредитка", D("0"), is_credit=True, limit=D("10000"))]
    inp = _inp(_book(wallets=wallets), wallets=wallets)
    base = baseline(inp)

    def bridge(day: int, amount: D) -> Bridge:
        return Bridge(counterparty="банк", wallet="кредитка",
                      date=date(2026, 1, day), amount=amount, days=10,
                      rate_per_day=D("0.001"))

    one = Variant("один мост", (bridge(1, D("6000")),))
    assert impossible(base, one) is None
    two = Variant("два моста", (bridge(1, D("6000")), bridge(5, D("6000"))))
    assert "вместе больше доступного лимита" in impossible(base, two)
    with pytest.raises(ImpossibleAction, match="вместе больше доступного лимита"):
        applied(base, two)


def test_a_variant_is_a_list_applied_whole_and_the_scenario_is_not_rewritten():
    """Вариант применяется целиком; сценарий под вариант не переписывается."""
    wallets = [_wallet("карта", D("50000"))]
    inp = _inp(_book(_deal(uid="заём", amount=D("10000")),
                     _deal(uid="долг", amount=D("5000"),
                           schedule=ScheduleRule(days=(10,), payment=D("1000"))),
                     wallets=wallets),
               wallets=wallets)
    before = copy.deepcopy(inp)
    base = baseline(inp)

    variant = Variant("перенос и досрочка", (
        Move(deal="заём", planned=date(2026, 1, 20), to=date(2026, 2, 20)),
        Prepay(deal="долг", date=date(2026, 1, 15), amount=D("1000")),
    ))
    moved = applied(base, variant)

    assert len(moved.book.edits) == 1 and len(moved.book.movements) == 1
    assert len(moved.one_offs) == 1
    price(base, variant)                      # цена считается по тому же списку
    assert inp == before                      # база не тронута ни одним действием


# --- цена ------------------------------------------------------------------

def test_the_price_is_a_set_of_measurements_not_one_number():
    """Цена — набор измерений: проценты, просрочка, касса, выход, свободные деньги."""
    inp = _inp(_book(_deal()), wallets=[_wallet("карта", D("50000"))],
               incomes=[_income(m) for m in range(1, 7)])
    base = baseline(inp)

    rows = price(base, Variant("перенос на неделю", (
        Move(deal="заём", planned=date(2026, 1, 20), to=date(2026, 1, 27)),)))

    assert rows.label == "перенос на неделю"
    assert rows.delay_days == 7                      # перенесли на неделю вперёд
    assert rows.interest > 0                         # дневная ставка: перенос стоит
    assert rows.hole_before == D("0") and rows.hole_after == D("0")
    assert rows.hole_months_before == 0 and rows.hole_months_after == 0
    assert rows.free_change == D("0")                # внутри месяца деньги те же
    assert (rows.first_priority, rows.step1) == (0, 0)


def test_a_postponement_without_a_date_does_not_measure_delay():
    """Отложенный платёж дней просрочки не даёт: даты нет — ноль соврал бы."""
    inp = _inp(_book(_deal()), wallets=[_wallet("карта", D("50000"))])
    base = baseline(inp)

    rows = price(base, Variant("отложение", (
        Move(deal="заём", planned=date(2026, 1, 20)),)))

    assert rows.delay_days is None


def test_a_bridge_is_a_deal_with_terms_and_money_arriving():
    """Мост — новая сделка с условиями и деньги в кассе, а не отдельная сущность."""
    wallets = [_wallet("карта", D("-1000")), _wallet("кредитка", D("0"),
                                                     is_credit=True,
                                                     limit=D("100000"))]
    book = _book(wallets=wallets)
    inp = _inp(book, wallets,
               incomes=[Income(date(2026, 1, 25), D("20000"), "кредитка")])
    base = baseline(inp)

    variant = Variant("мост", (Bridge(counterparty="банк", wallet="кредитка",
                                      date=START, amount=D("5000"), days=30,
                                      rate_per_day=D("0.002")),))
    moved = applied(base, variant)

    [deal] = [d for d in moved.book.deals if d.kind == "мост"]
    assert deal.amount == D("5300.00")               # 5000 + 5000×0,002×30
    assert deal.wallet == "кредитка"
    assert deal.schedule.count == 1
    assert deal.schedule.payment == D("5300.00")
    assert [i.amount for i in moved.incomes] == [D("20000"), D("5000")]
    assert [d.uid for d in facts(base, variant)] == [deal.uid]

    rows = price(base, variant)
    assert rows.interest == D("300.00")              # цена моста — по переданной ставке
    assert rows.free_change == D("-300.00")          # проценты ушли из свободных денег
    assert rows.unsecured_after == D("0")            # возврат кошельку по силам

    rolled = roll_months(moved.book, START, moved.wallets, moved.incomes, [],
                         living_floor=D("0"), max_months=6)
    assert rolled.deal_roll.freedom == date(2026, 1, 1)   # мост закрылся своим сроком


def test_a_variant_that_promises_money_it_does_not_have_shows_it():
    """Вариант может пообещать деньги, которых на кошельке нет: это видно ценой."""
    wallets = [_wallet("карта", D("-1000")), _wallet("кредитка", D("0"),
                                                     is_credit=True,
                                                     limit=D("100000"))]
    inp = _inp(_book(wallets=wallets), wallets=wallets)     # приходов нет
    base = baseline(inp)

    variant = Variant("мост", (Bridge(counterparty="банк", wallet="кредитка",
                                      date=START, amount=D("5000"), days=20,
                                      rate_per_day=D("0.002")),))
    rows = price(base, variant)

    assert rows.unsecured_before == D("0")
    assert rows.unsecured_after == D("5200.00")      # возврат моста кошельку не по силам
    assert rows.free_change > 0                      # деньги пришли, но вернуть нечем


def test_a_prepay_takes_the_months_free_money_and_more_is_impossible():
    """Досрочка берёт из свободного остатка месяца; больше остатка — невозможно."""
    wallets = [_wallet("карта", D("15000"))]
    inp = _inp(_book(_deal(uid="заём", amount=D("10000")),
                     _deal(uid="долг", amount=D("5000"),
                           schedule=ScheduleRule(days=(10,), payment=D("500"))),
                     wallets=wallets),
               wallets=wallets, obligation_reserve=D("8000"))
    base = baseline(inp)
    free = base.forecast.months[0].free
    assert free == D("3500")                        # 15 000 − 3 500 нагрузки − 8 000

    variant = Variant("досрочка", (Prepay(deal="долг", date=date(2026, 1, 15),
                                          amount=D("3500")),))
    moved = applied(base, variant)
    assert [m.amount for m in moved.book.movements] == [D("3500")]
    assert deal_balance(moved.book, "долг", date(2026, 1, 15)) == D("1500")
    [payment] = moved.one_offs
    assert (payment.date, payment.amount, payment.account) == (
        date(2026, 1, 15), D("3500"), "карта")

    too_much = Variant("досрочка сверх", (Prepay(deal="долг",
                                                 date=date(2026, 1, 15),
                                                 amount=D("3501")),))
    assert "больше свободных денег месяца" in impossible(base, too_much)
    with pytest.raises(ImpossibleAction, match="свободных денег"):
        applied(base, too_much)
    with pytest.raises(ImpossibleAction):
        price(base, too_much)


def test_a_prepay_respects_the_closure_unit():
    """Единицу закрытия одной сделкой не закрыть: копилка гасится разом."""
    wallets = [_wallet("карта", D("10000"))]
    first = _deal(uid="первый", amount=D("1000"), closure_unit="копилка",
                  schedule=ScheduleRule(days=(20,), payment=D("500")))
    second = _deal(uid="второй", amount=D("1000"), closure_unit="копилка",
                   schedule=ScheduleRule(days=(20,), payment=D("500")))
    inp = _inp(_book(first, second, wallets=wallets), wallets=wallets)
    base = baseline(inp)

    variant = Variant("досрочка участника", (
        Prepay(deal="первый", date=date(2026, 1, 15), amount=D("1000")),))
    moved = applied(base, variant)
    roll = roll_deals(moved.book, START, D("0"), max_months=1,
                      budgets={1: D("0")})
    [unit] = roll.months[0].units.values()
    assert unit.closed is False                 # одна сделка копилку не закрыла
    assert unit.target == D("1000")             # цель — остаток живого участника
    assert unit.pot == D("500")                 # и он платит по своему графику

    beyond = Variant("больше участника", (
        Prepay(deal="первый", date=date(2026, 1, 15), amount=D("1001")),))
    assert "больше остатка" in impossible(base, beyond)


def test_direct_is_the_owners_consent_for_the_second_priority():
    """Без согласия владельца второй приоритет не считается погашаемым."""
    wallets = [_wallet("карта", D("6000"))]
    inp = _inp(_book(_deal(uid="заём", amount=D("1000"),
                           schedule=ScheduleRule(days=(20,), payment=D("1000"))),
                     _deal(uid="взыскание", amount=D("3000"), second_priority=True),
                     wallets=wallets),
               wallets=wallets)
    base = baseline(inp)

    without = Variant("без согласия", ())
    with_consent = Variant("с согласием", (Direct(),))

    assert applied(base, without).consent_to_second is False
    assert applied(base, with_consent).consent_to_second is True
    assert base.forecast.second_priority.reached is False
    assert forecast(applied(base, with_consent)).second_priority.reached is True


# --- что невозможно --------------------------------------------------------

def test_a_move_beyond_the_horizon_and_a_missing_occurrence_are_impossible():
    """Сдвиг за горизонт и перенос несуществующего вхождения невозможны."""
    inp = _inp(_book(_deal()), wallets=[_wallet("карта", D("50000"))],
               max_months=3)
    base = baseline(inp)
    assert base.horizon == horizon(START, 3) == date(2026, 3, 31)

    beyond = Variant("за горизонт", (Move(deal="заём",
                                          planned=date(2026, 1, 20),
                                          to=date(2026, 4, 1)),))
    assert "за горизонтом проката" in impossible(base, beyond)

    missing = Variant("чужое вхождение", (Move(deal="заём",
                                               planned=date(2026, 1, 21),
                                               to=date(2026, 1, 25)),))
    assert "такого вхождения по графику нет" in impossible(base, missing)


def test_a_bridge_is_limited_by_the_wallets_available_limit():
    """Мост берётся в пределах доступного лимита; недоступный лимита не даёт."""
    wallets = [_wallet("карта", D("-1000")),
               _wallet("кредитка", D("0"), is_credit=True, limit=D("5000")),
               _wallet("арест", D("0"), is_credit=True, limit=D("5000"),
                       available=False),
               _wallet("неизвестный", D("0"), is_credit=True)]
    inp = _inp(_book(wallets=wallets), wallets=wallets,
               incomes=[_income(1)])
    base = baseline(inp)

    def bridge(wallet: str, amount: D) -> Variant:
        return Variant("мост", (Bridge(counterparty="банк", wallet=wallet,
                                       date=START, amount=amount, days=10,
                                       rate_per_day=D("0.001")),))

    assert impossible(base, bridge("кредитка", D("5000"))) is None
    assert "больше доступного лимита" in impossible(base, bridge("кредитка",
                                                                 D("5001")))
    assert "больше доступного лимита" in impossible(base, bridge("арест", D("1")))
    assert "лимит кошелька 'неизвестный' неизвестен" in impossible(
        base, bridge("неизвестный", D("1")))


def test_a_bridge_without_a_hole_is_impossible():
    """Мост при отсутствии дыры брать нечего."""
    wallets = [_wallet("карта", D("50000")),
               _wallet("кредитка", D("0"), is_credit=True, limit=D("100000"))]
    inp = _inp(_book(wallets=wallets), wallets=wallets)
    base = baseline(inp)

    assert impossible(base, Variant("мост", (
        Bridge(counterparty="банк", wallet="кредитка", date=START,
               amount=D("1000"), days=10, rate_per_day=D("0.001")),))
    ) == "мост при отсутствии дыры: закрывать нечего"


def test_a_conflict_of_actions_inside_a_variant_is_impossible():
    """Два действия на одно и то же — конфликт, а не порядок применения."""
    inp = _inp(_book(_deal()), wallets=[_wallet("карта", D("50000"))])
    base = baseline(inp)

    twice = Variant("два переноса одного вхождения", (
        Move(deal="заём", planned=date(2026, 1, 20), to=date(2026, 1, 25)),
        Move(deal="заём", planned=date(2026, 1, 20), to=date(2026, 1, 27)),
    ))
    assert "конфликт действий" in impossible(base, twice)


def test_only_the_impossible_is_rejected_and_the_unprofitable_is_priced():
    """Невыгодное не отклоняется: оно показывается ценой, а решение за человеком."""
    wallets = [_wallet("карта", D("-5000")), _wallet("кредитка", D("0"),
                                                     is_credit=True,
                                                     limit=D("100000"))]
    inp = _inp(_book(wallets=wallets), wallets=wallets)
    base = baseline(inp)

    dear = Variant("дорогой мост", (Bridge(counterparty="банк", wallet="кредитка",
                                           date=START, amount=D("5000"), days=30,
                                           rate_per_day=D("0.01")),))
    assert impossible(base, dear) is None            # возможен, хоть и дорог
    rows = price(base, dear)
    assert rows.interest == D("1500.00")             # 5000 × 0,01 × 30
    assert rows.hole_months_after < rows.hole_months_before   # дыра не тянется дальше


# --- перебор ---------------------------------------------------------------

def test_the_engine_enumerates_shifts_in_the_window_and_a_bridge():
    """Движок перебирает узкий набор: сдвиги платежей в окне и мост под дыру."""
    inp, base = _hole_input()

    rows = variants(base, bridge=Bridge(counterparty="банк", wallet="кредитка",
                                        date=START, amount=D("0"), days=14,
                                        rate_per_day=D("0.005")))

    labels = [v.label for v in rows]
    assert f"сдвиг аренда 2026-01-05 → 2026-01-25" in labels   # платёж в окне дыры
    assert f"мост {base.forecast.months[0].hole_date} 4500" in labels
    [bridge_row] = [v for v in rows if "мост" in v.label]
    [action] = bridge_row.actions
    assert (action.date, action.amount) == (base.forecast.months[0].hole_date,
                                            D("4500"))

    without_bridge = variants(base)
    assert all("мост" not in v.label for v in without_bridge)
    assert len(without_bridge) == len(rows) - 1


def test_a_variant_is_priced_and_only_the_feasible_are_enumerated():
    """Цены набора считаются рядом; невозможное в набор не попадает."""
    inp, base = _hole_input()

    rows = variants(base, bridge=Bridge(counterparty="банк", wallet="кредитка",
                                        date=START, amount=D("0"), days=14,
                                        rate_per_day=D("0.005")))
    priced = prices(base, rows)

    assert [p.label for p in priced] == [v.label for v in rows]
    [shift_price] = [p for p in priced if "сдвиг" in p.label]
    assert shift_price.delay_days == 20              # 05.01 → 25.01
    [bridge_price] = [p for p in priced if "мост" in p.label]
    assert bridge_price.delay_days == 0

    no_hole = _inp(_book(wallets=[_wallet("карта", D("5000"))]),
                   wallets=[_wallet("карта", D("5000"))])
    assert variants(baseline(no_hole), bridge=Bridge(
        counterparty="банк", wallet="карта", date=START, amount=D("0"), days=14,
        rate_per_day=D("0.005"))) == []


# --- цена по базе начисления ------------------------------------------------

def test_the_bridge_costs_by_the_passed_rate_and_the_move_by_the_deals_base():
    """У дневной ставки перенос стоит рублей, у годовой — только дней."""
    day_wallets = [_wallet("карта", D("50000"))]
    day_inp = _inp(_book(_deal(uid="дневной", amount=D("10000"),
                               rate_per_day=D("0.005"),
                               schedule=ScheduleRule(days=(20,),
                                                     payment=D("3000"))),
                         wallets=day_wallets),
                   wallets=day_wallets)
    day_base = baseline(day_inp)
    day_move = Variant("перенос", (Move(deal="дневной", planned=date(2026, 1, 20),
                                        to=date(2026, 1, 27)),))
    assert price(day_base, day_move).interest > 0

    year_wallets = [_wallet("карта", D("50000"))]
    year_inp = _inp(_book(_deal(uid="годовой", amount=D("10000"),
                                rate_per_day=None, rate_per_year=D("0.24"),
                                schedule=ScheduleRule(days=(20,),
                                                      payment=D("3000"))),
                          wallets=year_wallets),
                    wallets=year_wallets)
    year_base = baseline(year_inp)
    year_move = Variant("перенос", (Move(deal="годовой", planned=date(2026, 1, 20),
                                         to=date(2026, 1, 27)),))
    rows = price(year_base, year_move)
    assert rows.interest == D("0")                   # внутри месяца годовая не дорожает
    assert rows.delay_days == 7                      # но дни просрочки видны


def test_an_accepted_action_becomes_a_fact_and_the_object_is_not_stored():
    """Принятое действие становится записью журнала; объект-действие не хранится."""
    wallets = [_wallet("карта", D("-1000")),
               _wallet("кредитка", D("0"), is_credit=True, limit=D("100000"))]
    book = _book(_deal(uid="заём", amount=D("10000")), wallets=wallets)
    inp = _inp(book, wallets, incomes=[_income(1)])
    base = baseline(inp)

    variant = Variant("перенос, досрочка и мост", (
        Move(deal="заём", planned=date(2026, 1, 20), to=date(2026, 2, 20)),
        Prepay(deal="заём", date=date(2026, 1, 25), amount=D("1000")),
        Bridge(counterparty="банк", wallet="кредитка", date=date(2026, 1, 10),
               amount=D("2000"), days=10, rate_per_day=D("0.001")),
    ))
    rows = facts(base, variant)

    assert [type(row).__name__ for row in rows] == ["OccurrenceEdit", "Movement",
                                                    "Deal"]
    [edit, movement, deal] = rows
    assert (edit.planned, edit.moved_to) == (date(2026, 1, 20), date(2026, 2, 20))
    assert (movement.date, movement.amount, movement.deal) == (
        date(2026, 1, 25), D("1000"), "заём")
    assert deal.kind == "мост"

    moved = applied(base, variant)
    assert not any(isinstance(row, (Move, Prepay, Bridge))
                   for row in [*moved.book.edits, *moved.book.movements,
                               *moved.book.deals, *moved.one_offs])
