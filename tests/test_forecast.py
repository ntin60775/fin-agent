"""Тесты прогноза — синтетические фикстуры, без личных данных."""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pytest

import finance_core.roll as roll_module
from finance_core import (FAMILY, LEGAL, OWED_TO_ME, PERSON, Counterparty, Deal,
                          ForecastInput, Income, Movement, ObservedBalance,
                          Payment, ScheduleRule, Settlements, Wallet, forecast,
                          forecast_shifts, validate, widest)
from finance_core import WINDOW_INCOME_ENDS, WINDOW_MONTH_CAP
from finance_core.forecast import _roll_of

START = date(2026, 1, 1)


def _counterparty(uid: str = "банк", **kw) -> Counterparty:
    base = dict(uid=uid, name=uid, kind=LEGAL, subtype="банк", groups=("долги",))
    base.update(kw)
    return Counterparty(**base)


def _wallet(uid: str = "карта", balance: D = D("0"), **kw) -> Wallet:
    return Wallet(uid=uid, name=uid, kind="карта", balance=balance, **kw)


def _deal(uid: str = "заём", amount: D | None = D("3000"), **kw) -> Deal:
    base = dict(uid=uid, title=uid, counterparty="банк", amount=amount,
                rate_per_year=D("0"), wallet="карта",
                schedule=ScheduleRule(days=(20,), payment=D("1000")))
    base.update(kw)
    return Deal(**base)


def _book(*deals, counterparties=(), wallets=None, observed=(), movements=()
          ) -> Settlements:
    return Settlements(
        counterparties=[_counterparty(), *counterparties],
        wallets=[_wallet()] if wallets is None else list(wallets),
        deals=list(deals),
        movements=list(movements),
        observed=list(observed),
    )


def _incomes(amount: D, *months: int) -> list[Income]:
    return [Income(date(2026, month, 5), amount, "карта") for month in months]


def _input(book: Settlements, **kw) -> ForecastInput:
    base = dict(start=START, wallets=list(book.wallets), max_months=8)
    base.update(kw)
    return ForecastInput(book=book, **base)


# --- проценты --------------------------------------------------------------

def test_interest_is_the_rolls_total_and_grows_with_the_rate():
    """Рубли процентов за прокат: ставки нет — нет и процентов, ставка выше — дороже."""
    day = _book(_deal(uid="дневной", rate_per_year=None, rate_per_day=D("0.005"),
                      schedule=ScheduleRule(days=(20,), payment=D("1000"))))
    dear = forecast(_input(day, living_floor=D("0"))).interest
    assert dear > 0

    cheap = _book(_deal(uid="дешёвый", rate_per_year=None, rate_per_day=D("0.001"),
                        schedule=ScheduleRule(days=(20,), payment=D("1000"))))
    assert forecast(_input(cheap, living_floor=D("0"))).interest < dear

    free = _book(_deal(uid="бесплатный", schedule=ScheduleRule(days=(20,),
                                                              payment=D("1000"))))
    assert forecast(_input(free, living_floor=D("0"))).interest == D("0")


# --- ступени ---------------------------------------------------------------

def test_step_one_is_the_last_deficit_month_not_the_first_good_one():
    """Ступень 1 — позади последнего месяца с дефицитом, а не первый удачный месяц.

    Январь видит голод первых четырёх чисел (кошелёк 0, приход 5-го), февраль–
    май — с нехваткой до прожиточного минимума, июнь входит в дефицит входом
    позади накопленного минимума: ступень достигнута в июле, а не в январе.
    """
    book = _book()
    # Доход идёт все семь месяцев: июльский приход делает свободными деньги
    # июля — ступень 2, и отчёт кончается на ступенях
    f = forecast(_input(
        book, incomes=_incomes(D("30000"), 1, 2, 3, 4, 5, 6, 7),
        living_floor=D("20000"),
        one_offs=[Payment(date(2026, 2, 10), D("55000"), account="карта",
                          counterparty="банк")]))
    # Тикет 02: прожитое вычтено — дефицит февраля–мая растёт с накоплением;
    # тикет 03: январь получает голод первых чисел, июнь — вход позади
    # минимума (95 000 против 100 666,68)
    assert [d.index for d in f.deficits] == [1, 2, 3, 4, 5, 6]
    assert f.months[0].free > 0     # январь по деньгам удачен, ступени в нём нет
    assert f.step1.month == date(2026, 7, 1)
    assert f.step2.month == date(2026, 7, 1)        # ступень 2 — сверх ступени 1
    assert len(f.months) == 7                       # отчёт кончается на ступенях


def test_step_two_is_the_first_month_with_free_money():
    """Ступень 2 — первый месяц, где свободные деньги положительны."""
    book = _book(_deal())
    # Первые месяцы в минус (15 000 при минимуме 20 000), с мая доход покрывает
    # минимум — положительными деньги становятся только на живых деньгах
    f = forecast(_input(book, incomes=[*_incomes(D("15000"), 1, 2, 3, 4),
                                       *_incomes(D("60000"), 5, 6, 7, 8)],
                        living_floor=D("20000")))
    # Май в дефиците: его вход позади накопленного минимума (тикет 03) —
    # 57 000 против 80 000,01 прожитых январём–апрелем, и до прихода 4 дня
    assert [d.index for d in f.deficits] == [1, 2, 3, 4, 5]
    # Нехватка считает прожитое (тикет 02): январь–апрель минимум окна уже
    # прожит, худший шаг — 20-е, платёж сделки: 10 666,67 − (14 000 − 12 666,67)
    assert f.deficits[0].floor_gap == D("9333.34")
    # Свободные деньги — состояние месяца: нагрузка больше денег — величина отрицательна
    assert f.months[0].free == D("-6666.67")        # 15 000 − 1 000 − 20 666.67
    # ... а бюджет досрочек не бывает отрицательным
    assert f.months[0].prepay_budget == D("0")
    # Накопленный минимум окна: прожитые январь–февраль вычтены из остатка
    assert f.months[1].free == D("-11333.34")       # 28 000 − 39 333.34
    assert f.step1.month == date(2026, 6, 1)        # нехватка держится до мая
    assert f.step2.month == date(2026, 6, 1)        # сверх ступени 1: июнь
    assert f.reached


def test_step_two_is_not_declared_on_lived_through_money():
    """Ступень 2 не достигается на прожитых деньгах: минимум окна накоплен.

    Доход 2 500 при минимуме 3 000: остаток растёт медленнее накопленного
    минимума окна, свободных денег нет ни в одном месяце — «выбрался» на
    фантоме прошлых месяцев больше не объявляется (аудит 2026-09-22, тикет 01).
    """
    f = forecast(_input(_book(), incomes=_incomes(D("2500"), 1, 2, 3, 4),
                        living_floor=D("3000")))
    assert [cm.free for cm in f.months[:3]] == [D("-600"), D("-900"), D("-1500")]
    assert not f.step2.reached


def test_free_money_is_counted_before_prepayments():
    """Свободные деньги считаются до досрочек: они же и есть бюджет досрочек.

    Сделка закрывается досрочкой в первом же месяце: деньги уходят из кассы, а
    свободные деньги месяца остаются до-досрочковыми — иначе один и тот же рубль
    сочтён дважды.
    """
    book = _book(_deal(amount=D("10000")))
    f = forecast(_input(book, incomes=_incomes(D("30000"), 1, 2),
                        living_floor=D("5000"), max_months=2))
    assert f.months[0].free == D("23833.33")        # 30 000 − 5 166.67
    assert f.months[0].balances["карта"] == D("20000.00")   # досрочка ушла из кассы
    assert f.first_priority.month == date(2026, 1, 1)


def test_cushion_is_a_goal_and_is_not_subtracted():
    """Подушка — цель, а не расход: из свободных денег она не вычитается."""
    book = _book()
    base = _input(book, incomes=_incomes(D("30000"), 1, 2, 3, 4),
                  living_floor=D("5000"))
    plain = forecast(base)
    goal = forecast(replace(base, cushion=D("40000")))
    assert [cm.free for cm in goal.months] == [cm.free for cm in plain.months]
    assert goal.cushion.month == date(2026, 2, 1)   # 55 333.33 ≥ 40 000
    far = forecast(replace(base, cushion=D("1000000")))
    assert not far.cushion.reached
    assert "не хватило" in far.cushion.reason


def test_cushion_counts_money_at_hand_not_a_sum_of_months():
    """Накопление подушки — остаток, а не поток: суммировать по месяцам нечего.

    Досрочки забирают свободные деньги каждый месяц, поэтому остаток на руках
    стоит на месте: сумма по месяцам давно перевалила бы за цель, которой на
    самом деле нет.
    """
    book = _book(_deal(amount=D("60000")))
    base = _input(book, incomes=_incomes(D("10000"), 1, 2, 3, 4, 5, 6),
                  living_floor=D("0"), max_months=6)
    assert [cm.free for cm in forecast(base).months] == [D("9000.00")]
    assert forecast(replace(base, cushion=D("20000"))).cushion.reached is False
    assert forecast(replace(base, cushion=D("5000"))).cushion.month == date(2026, 1, 1)


def test_unknown_living_floor_leaves_the_steps_unmeasured():
    """Минимум неизвестен — ступени не измерены, а не посчитаны по нулю."""
    book = _book(wallets=[_wallet(balance=D("1000"))])
    f = forecast(_input(book, incomes=_incomes(D("5000"), 1)))
    assert f.living_floor is None
    assert f.months[0].floor_gap is None            # «не оценено», а не ноль
    assert f.months[0].free == D("6000")            # деньги до еды, а не свободные
    assert not f.step1.reached and "неизвестен" in f.step1.reason
    assert not f.step2.reached and "неизвестен" in f.step2.reason
    assert not f.complete


# --- вилка -----------------------------------------------------------------

def test_unknowns_are_taken_one_at_a_time():
    """Неизвестные разбираются по одному: база плюс сдвиг от каждого параметра.

    Каждый вариант считается от базы, а не от предыдущего: комбинированная вилка
    смешала бы причины, и не было бы видно, что задаёт ширину.
    """
    book = _book(_deal())
    base = _input(book, incomes=[*_incomes(D("15000"), 1, 2, 3, 4),
                                 *_incomes(D("60000"), 5, 6, 7, 8)],
                  living_floor=D("20000"))
    shifts = forecast_shifts(base, {"living_floor": D("33000"),
                                    "obligation_reserve": D("100000")})
    assert [s.label for s in shifts] == ["living_floor", "obligation_reserve"]
    # минимум 33 000 держит дефицит до конца окна — ступени в нём не
    # достигнуты; резерв ступень 1 не трогает, но держит бюджет дольше:
    # база развернулась в июне (май позади минимума на входе) — сдвиг +2
    assert (shifts[0].step1_shift, shifts[0].step2_shift) == (None, None)
    assert (shifts[1].step1_shift, shifts[1].step2_shift) == (0, 2)
    assert shifts[0].step1 is None
    assert shifts[1].step2 == date(2026, 8, 1)
    assert widest(shifts).label == "obligation_reserve"

    # сдвиг считается от базы: вариант с одним параметром даёт то же самое
    alone = forecast(replace(base, obligation_reserve=D("100000")))
    assert (shifts[1].step1, shifts[1].step2) == (alone.step1.month, alone.step2.month)


def test_unknown_forecast_parameter_lists_declared():
    """Неизвестный параметр вилки — ошибка с перечнем объявленных."""
    with pytest.raises(ValueError, match="объявлены"):
        forecast_shifts(_input(_book()), {"прожиточный": D("1")})


def test_earlier_shift_widens_the_fork_too():
    """Ширину задаёт размах: параметр расширяет вилку и когда тянет дату назад."""
    book = _book(_deal())
    base = _input(book, incomes=[*_incomes(D("15000"), 1, 2, 3, 4),
                                 *_incomes(D("60000"), 5, 6, 7, 8)],
                  living_floor=D("20000"))
    shifts = forecast_shifts(base, {"living_floor": D("5000"),
                                    "obligation_reserve": D("1000")})
    # минимум 5 000 гасит дефициты с февраля и делает деньги свободными с
    # января (в январе виден голод первых четырёх чисел при пустом кошельке);
    # резерв в 1 000 вилку не двигает — ступени на месте: июнь и июнь
    assert (shifts[0].step1_shift, shifts[0].step2_shift) == (-4, -4)
    assert (shifts[1].step1_shift, shifts[1].step2_shift) == (0, 0)
    assert widest(shifts).label == "living_floor"


# --- требования, передачи, расхождения -------------------------------------

def test_requirements_are_a_separate_line_and_do_not_enter_liquidity():
    """Требование идёт отдельной строкой и в кассу не подмешивается."""
    book = _book(_deal(uid="должны", amount=D("20000"), direction=OWED_TO_ME,
                       schedule=ScheduleRule(days=(1,), payment=D("20000"))),
                 wallets=[_wallet(balance=D("-5000"))])
    f = forecast(_input(book, living_floor=D("3000"), max_months=3))
    assert [(e.deal, e.date, e.amount) for e in f.expectations] == [
        ("должны", date(2026, 1, 1), D("20000")),
        # 1 февраля — воскресенье: доход сдвигается назад, к пятнице, и попадает
        # в январское окно отчёта — деньги приходят в январе, а не в феврале.
        ("должны", date(2026, 1, 30), D("20000"))]
    assert f.months[0].hole == D("5000")            # дыра требованием не уменьшена
    assert "должны" not in f.months[0].balances
    assert f.deficits[0].covers_hole is True        # требование закрыло бы дыру


def test_requirement_that_comes_too_late_does_not_cover_the_hole():
    """Требование видно отдельной строкой и тогда, когда дыру оно не закрывает."""
    book = _book(_deal(uid="должны", amount=D("20000"), direction=OWED_TO_ME,
                       schedule=ScheduleRule(days=(15,), payment=D("20000"))),
                 wallets=[_wallet(balance=D("-5000"))])
    f = forecast(_input(book, living_floor=D("3000"), max_months=3))
    assert f.deficits[0].covers_hole is False       # к дате дыры ещё не пришло


def test_family_transfer_is_a_separate_line_with_detail():
    """Передача внутри семьи — отдельной строкой: кому и на что."""
    family = _counterparty(uid="родня", name="Родня", kind=PERSON,
                           subtype="родственник", groups=(FAMILY,))
    rent = _counterparty(uid="аренда", name="Аренда", subtype="прочее",
                         groups=("жильё",))
    book = _book(counterparties=[family, rent],
                 wallets=[_wallet(balance=D("20000"))])
    f = forecast(_input(
        book, living_floor=D("0"), max_months=2,
        one_offs=[Payment(date(2026, 1, 10), D("5000"), account="карта",
                          counterparty="родня", purpose="еда"),
                  Payment(date(2026, 1, 20), D("2000"), account="карта",
                          counterparty="аренда", purpose="квартира")]))
    assert [(t.counterparty, t.purpose, t.amount) for t in f.family] == [
        ("родня", "еда", D("5000"))]
    assert f.family_total == D("5000")


def test_family_transfer_by_schedule_is_shown_too():
    """Передача по графику — тоже передача: деньги уходят члену семьи, и это строка.

    Разовый платёж называет, на что ушли деньги; у сделки назначения не бывает —
    строка показывает то, что у формы есть.
    """
    family = _counterparty(uid="родня", name="Родня", kind=PERSON,
                           subtype="родственник", groups=(FAMILY,))
    support = _deal(uid="помощь", counterparty="родня", amount=None,
                    schedule=ScheduleRule(days=(10,), payment=D("5000")))
    book = _book(support, counterparties=[family],
                 wallets=[_wallet(balance=D("20000"))])
    f = forecast(_input(book, living_floor=D("0"), max_months=2,
                        one_offs=[Payment(date(2026, 1, 5), D("3000"),
                                          account="карта", counterparty="родня",
                                          purpose="еда")]))
    assert [(t.date, t.counterparty, t.purpose, t.amount) for t in f.family] == [
        (date(2026, 1, 5), "родня", "еда", D("3000")),
        (date(2026, 1, 12), "родня", None, D("5000"))]   # 10-е — суббота, платёж вперёд
    assert f.family_total == D("8000")


def test_unexplained_discrepancy_is_an_open_question():
    """Необъяснённое расхождение факта с расчётом — открытый вопрос прогноза."""
    book = _book(_deal(amount=D("10000")),
                 observed=[ObservedBalance("заём", date(2026, 1, 31), D("8000"))])
    validate(book)                                  # само расхождение — не ошибка
    f = forecast(_input(book, living_floor=D("0"), max_months=2))
    assert [(q.deal, q.observed, q.computed, q.difference) for q in f.questions] == [
        ("заём", D("8000"), D("10000"), D("-2000"))]
    assert not f.complete                           # прогноз не выдан за полный


def test_agreed_check_is_not_a_question():
    """Сошедшаяся сверка вопросом не является: сверка ищет причину, а не запрещает факт."""
    book = _book(_deal(amount=D("10000")),
                 observed=[ObservedBalance("заём", date(2026, 1, 31), D("10000"))])
    f = forecast(_input(book, incomes=_incomes(D("5000"), 1, 2),
                        income_horizon=date(2026, 4, 5),
                        living_floor=D("0"), max_months=2))
    assert f.questions == []
    assert f.complete


def test_non_convergence_makes_the_forecast_incomplete(monkeypatch):
    """Признак несходимости делает прогноз неполным — в ряд с stalled/assumed.

    Тот же вход: без признака прогноз полон, с признаком — неполон, а сам
    расчёт не роняется исключением и отчёт собирается целиком.
    """
    book = _book(_deal(amount=D("10000")),
                 observed=[ObservedBalance("заём", date(2026, 1, 31), D("10000"))])
    inp = _input(book, incomes=_incomes(D("5000"), 1, 2),
                 income_horizon=date(2026, 4, 5),
                 living_floor=D("0"), max_months=2)
    assert forecast(inp).complete               # признака нет — прогноз полон

    monkeypatch.setattr(roll_module, "_MONTH_PASSES", 1)
    f = forecast(inp)
    assert 1 in f.unconverged                   # признак несходимости в результате
    assert not f.converged
    assert f.months                             # отчёт собран, расчёт не упал
    assert not f.complete                       # и прогноз с признаком неполон


def test_unassessed_deficit_is_not_called_absent():
    """«Не оценено» — не «дефицита нет»: нечем судить — ступень не объявляется."""
    book = _book(wallets=[_wallet(balance=D("1000"))])
    f = forecast(_input(book, living_floor=D("5000"), max_months=4))
    assert [cm.floor_gap for cm in f.months] == [None] * 4   # прихода впереди нет
    assert f.deficits == []
    assert not f.step1.reached
    assert "не оценена" in f.step1.reason
    assert not f.step2.reached
    assert not f.complete


def test_observation_on_a_regular_expense_is_an_error():
    """У регулярного расхода остатка нет — сверять нечего, а не открытый вопрос."""
    book = _book(_deal(uid="аренда", amount=None,
                       schedule=ScheduleRule(days=(5,), payment=D("100"))),
                 observed=[ObservedBalance("аренда", date(2026, 1, 31), D("0"))])
    with pytest.raises(ValueError, match="остатка нет"):
        validate(book)


def test_report_window_bounds_the_lines_it_shows():
    """Строка за окном отчёта не показывается: прокат такого платежа не видел."""
    family = _counterparty(uid="родня", name="Родня", kind=PERSON,
                           subtype="родственник", groups=(FAMILY,))
    wallet = _wallet(balance=D("-5000"))
    book = _book(counterparties=[family], wallets=[wallet])
    f = forecast(_input(book, living_floor=D("3000"), max_months=3,
                        one_offs=[Payment(date(2027, 6, 10), D("777"),
                                          account="карта", counterparty="родня",
                                          purpose="еда")]))
    assert len(f.months) == 3
    assert not f.reached                            # ступени не достигнуты — отчёт весь
    assert f.family == []


# --- даты, допущения, конец отчёта -----------------------------------------

def test_second_priority_date_needs_consent():
    """Дата второго приоритета считается при согласии направлять свободный остаток."""
    claim = _deal(uid="взыскание", amount=D("5000"), second_priority=True,
                  schedule=None)
    book = _book(_deal(uid="график"), claim)
    base = _input(book, incomes=_incomes(D("30000"), 1, 2, 3), living_floor=D("0"))
    without = forecast(base)
    assert not without.second_priority.reached
    assert "согласи" in without.second_priority.reason

    agreed = forecast(replace(base, consent_to_second=True))
    assert agreed.second_priority.month == date(2026, 1, 1)
    assert agreed.first_priority.month == without.first_priority.month


def test_second_priority_closed_before_the_start_is_closed():
    """Закрытый до проката второй приоритет — закрыт, а не «не закрылся»."""
    claim = _deal(uid="взыскание", amount=D("5000"), second_priority=True,
                  schedule=None)
    book = _book(_deal(uid="график"), claim,
                 movements=[Movement(date(2025, 12, 20), D("5000"), "взыскание")])
    f = forecast(_input(book, incomes=_incomes(D("5000"), 1), living_floor=D("0"),
                        max_months=3, consent_to_second=True))
    assert f.second_priority.month == date(2026, 1, 1)
    assert "закрыт" in f.second_priority.reason


def test_second_priority_closed_by_a_closure_unit_still_needs_consent():
    """Копилка гасится первым приоритетом: без согласия второй не считается."""
    unit = _deal(uid="долг", amount=D("6000"), closure_unit="копилка")
    claim = _deal(uid="взыскание", amount=D("3000"), second_priority=True,
                  closure_unit="копилка", schedule=None)
    book = _book(unit, claim)
    base = _input(book, incomes=_incomes(D("4000"), *range(1, 13)),
                  living_floor=D("0"), max_months=24)
    without = forecast(base)
    assert not without.second_priority.reached
    assert "согласи" in without.second_priority.reason

    agreed = forecast(replace(base, consent_to_second=True))
    assert agreed.second_priority.month == date(2026, 3, 1)


def test_second_priority_that_is_a_gap_is_not_called_closed():
    """Требование-пробел в прокат не входит: пробел — не закрытие."""
    claim = _deal(uid="взыскание", amount=D("3000"), second_priority=True,
                  rate_per_year=None, schedule=None)
    book = _book(_deal(uid="график"), claim)
    f = forecast(_input(book, incomes=_incomes(D("5000"), 1), living_floor=D("0"),
                        max_months=3, consent_to_second=True))
    assert not f.second_priority.reached
    assert "пробел" in f.second_priority.reason
    assert [g.deal for g in f.gaps] == ["взыскание"]


def test_months_on_assumption_are_marked():
    """Месяцы, посчитанные на допущении, помечены: цифра на допущении — не факт."""
    book = _book(wallets=[_wallet(balance=D("-1000"))])
    f = forecast(_input(book, incomes=_incomes(D("30000"), 1, 2, 3, 4),
                        living_floor=D("5000"), max_months=6))
    assert [(d.index, d.hole) for d in f.deficits] == [(1, D("1000"))]
    assert f.assumed == [1, 2]                      # дыра и месяц за ней — на допущении
    assert len(f.months) == 2                       # дальше ступеней отчёт не идёт


def test_signs_of_the_roll_beyond_the_report_window_follow_one_rule(monkeypatch):
    """Признаки проката за концом отчёта обрезаются одним правилом.

    Прокат длиннее отчёта (отчёт кончается на ступенях), и его списки содержат
    месяцы за его концом — и допущения (`assumed`), и несходимость
    (`unconverged`). Оба показываются по месяцам отчёта тем же правилом:
    дальше `until` отчёт ничего не утверждает. Внутренний предел в один шаг
    задаёт несходимость провокацией, а не патологией сценария.
    """
    deal = _deal(uid="заём", amount=D("3000"), rate_per_year=D("0.12"),
                 schedule=ScheduleRule(days=(20,), payment=D("1000"), start=START))
    book = _book(deal, wallets=[_wallet(balance=D("-1000"))])
    inp = _input(book, incomes=_incomes(D("30000"), 1, 2, 3, 4),
                 living_floor=D("5000"), max_months=6)
    monkeypatch.setattr(roll_module, "_MONTH_PASSES", 1)
    roll = _roll_of(inp)
    f = forecast(inp)
    until = len(f.months)

    assert until < len(roll.cash_months)            # прокат длиннее отчёта
    assert max(roll.assumed) > until                # допущения за его концом
    assert max(roll.unconverged) > until            # несходимость за его концом
    assert f.assumed == [i for i in roll.assumed if i <= until] == [1, 2]
    assert f.unconverged == [i for i in roll.unconverged if i <= until] == [1, 2]
    assert not f.complete                           # признак внутри отчёта делает его неполным


def test_unreached_steps_are_said_plainly():
    """Не достигнуты — сказано прямо, а не показана последняя цифра проката."""
    book = _book(wallets=[_wallet(balance=D("-5000"))])
    f = forecast(_input(book, living_floor=D("3000"), max_months=3))
    assert len(f.months) == 3                       # прокат кончился, а ступени — нет
    assert not f.reached
    assert "дефицит держится" in f.step1.reason
    assert "ступень 1 не достигнута" in f.step2.reason


def test_obligation_reserve_is_taken_off_free_money():
    """Резерв обязательств вычитается из свободных денег: без него рвётся начало месяца."""
    book = _book(wallets=[_wallet(balance=D("1000"))])
    base = _input(book, start=date(2026, 4, 1),
                  incomes=[Income(date(2026, 4, 5), D("500"), "карта")],
                  living_floor=D("800"), max_months=1)
    assert forecast(base).months[0].free == D("700")        # 1000 + 500 − 800
    with_reserve = forecast(replace(base, obligation_reserve=D("300")))
    assert with_reserve.months[0].free == D("400")
    assert with_reserve.obligation_reserve == D("300")


# --- окно проката -----------------------------------------------------------

def test_step_beyond_the_window_names_the_reason():
    """Ступень за концом окна названа «не достигнута за окно» с причиной конца окна."""
    incomes = _incomes(D("1000"), 1, 2, 3)
    one_offs = [Payment(date(2026, month, 10), D("1000"), account="карта",
                        counterparty="банк") for month in (1, 2, 3)]
    f = forecast(_input(_book(), incomes=incomes, one_offs=one_offs,
                        living_floor=D("0"), max_months=3))
    assert len(f.months) == 3                    # окно кончилось пределом месяцев
    assert f.step1.month == date(2026, 1, 1)     # дефицита нет — ступень 1 достигнута
    assert not f.step2.reached                   # свободные деньги нулевые во всех месяцах
    assert "не достигнута за окно" in f.step2.reason
    assert WINDOW_MONTH_CAP in f.step2.reason
    assert f.window.reason == WINDOW_MONTH_CAP


def test_the_payoff_date_beyond_the_window_names_the_reason():
    """Срок с досрочками за окном назван причиной, а не пропущен."""
    debt = _deal(uid="долгий", amount=D("60000"), rate_per_year=D("0"),
                 wallet="карта",
                 schedule=ScheduleRule(days=(20,), start=START,
                                       payment=D("1000"), count=60))
    f = forecast(_input(_book(debt), incomes=_incomes(D("5000"), 1, 2, 3),
                        living_floor=D("0"), income_horizon=date(2026, 3, 31)))
    assert (f.window.months, f.window.reason) == (3, WINDOW_INCOME_ENDS)
    assert f.first_priority.month is None        # долг не закрылся в окне
    assert WINDOW_INCOME_ENDS in f.first_priority.reason


# --- обе даты срока --------------------------------------------------------

def test_forecast_gives_both_payoff_dates_and_they_differ():
    """Срок с досрочками и срок по графику стоят рядом и не перепутаются."""
    debt = _deal("заём", amount=D("3000"))
    f = forecast(_input(_book(debt), wallets=[_wallet(balance=D("5000"))],
                        max_months=12))
    assert f.first_priority.month == date(2026, 1, 1)    # с досрочками раньше
    assert f.payoff_by_graph.month == date(2026, 3, 1)   # по графику позже
    assert f.first_priority.month != f.payoff_by_graph.month


def test_payoff_by_graph_outlives_the_window_that_income_ends():
    """Доходы кончаются раньше долга: по графику дата есть, с досрочками нет."""
    debt = _deal(uid="долгий", amount=D("60000"),
                 schedule=ScheduleRule(days=(20,), payment=D("1000"), count=60))
    f = forecast(_input(_book(debt), max_months=600,
                        incomes=_incomes(D("1000"), 1),
                        income_horizon=date(2026, 12, 31)))
    assert (f.window.months, f.window.reason) == (12, WINDOW_INCOME_ENDS)
    assert f.first_priority.month is None
    assert WINDOW_INCOME_ENDS in f.first_priority.reason
    assert f.payoff_by_graph.month == date(2030, 12, 1)
    assert f.payoff_by_graph.month > f.window.until      # окном не сужается


def test_both_dates_say_not_closed_with_one_phrasing():
    """Один факт «не закрылось» — одна фраза у обеих дат, причина окна рядом."""
    daily = _deal(uid="дневной", amount=D("10000"), rate_per_day=D("0.005"))
    yearly = _deal(uid="годовой", amount=D("10000"), rate_per_year=D("0.4"))
    f = forecast(_input(_book(daily, yearly), wallets=[_wallet()],
                        max_months=600))
    assert f.first_priority.month is None
    assert f.payoff_by_graph.month is None
    assert f.first_priority.reason == (
        "за отведённые месяцы долг не закрылся: кончился предел месяцев")
    assert f.payoff_by_graph.reason == "за отведённые месяцы долг не закрылся"
    assert f.first_priority.reason.startswith(f.payoff_by_graph.reason)
