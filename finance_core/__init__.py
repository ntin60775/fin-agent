"""Ядро расчёта личных финансов: касса и взаиморасчёты.

Пять сторон:

- `solver` — касса: хватит ли денег в периоде, где дыра, хватает ли остатка
  прожить до следующего прихода;
- `settlements` — взаиморасчёты: контрагенты, кошельки, сделки и движения,
  остаток по сделке и сальдо по контрагенту — производные величины; правило
  графика порождает вхождения со статусами;
- `roll` — прокат сделок по месяцам: что платится, что копится в копилке и
  когда закрывается последний долг;
- `forecast` — прогноз: когда ступени «выбрался» достигнуты и что этому мешает;
- `actions` — действия и цена варианта: правка сценария как объект, вариант как
  список действий, цена набором измерений, а не одним числом.

Ядро не хранит состояние и не читает markdown: оно считает то, что ему передали,
и возвращает результат. Источник цифр — на стороне вызывающего кода. Деньги —
`Decimal`; зависимостей нет, только стандартная библиотека.
"""
from .actions import (Action, Base, Bridge, Direct, ImpossibleAction, Move,
                      Prepay, Price, Variant, applied, baseline, facts,
                      impossible, price, prices, variants)
from .forecast import (Deficit, Discrepancy, FamilyTransfer, Forecast,
                       ForecastInput, Milestone, Shift, forecast,
                       forecast_shifts, widest)
from .model import Account, Income, Payment, Scenario, Transfer
from .roll import (AVALANCHE, PAYOFF_CLOSED_BEFORE, PAYOFF_NOT_CLOSED, SNOWBALL,
                   STRATEGIES, WINDOW_DEBTS_CLOSED, WINDOW_INCOME_ENDS,
                   WINDOW_MONTH_CAP, WINDOW_REASONS,
                   ConvergenceError, DealMonth, DealRoll, Expectation, Gap,
                   MonthsRoll, ScheduledPayment, UnitMonth, Window,
                   compare_deal_strategies, horizon, roll_deals, roll_months,
                   roll_window)
from .solver import CashMonth, roll_cash
from .settlements import (BOTH, CREDITOR, DEBTOR, DIRECTIONS, EXPECTED, FAMILY,
                          IN, I_OWE, KINDS, LEGAL, MOVEMENT_DIRECTIONS,
                          OCCURRENCE_STATUSES, OUT, OWED_TO_ME, PAID, PAID_LATE,
                          PERSON, POSTPONED, SKIPPED, STARTER_GROUPS, SUBTYPES,
                          WALLET_KINDS, Assignment, Counterparty, Deal,
                          FirstPayment, Movement, Occurrence, OccurrenceEdit,
                          ObservedBalance, ScheduleRule, Settlements, Wallet,
                          accrued_interest, beneficiary, counterparty_balance,
                          counterparty_role, deal_amount_at, deal_balance,
                          deal_holder_at, funding_wallet, liquidity,
                          occurrences, payment_channel, planned_date, validate)
from .solver import (KIND_PAYMENT, KIND_PREPAID, KIND_TRANSFER,
                     UNSECURED_KINDS, Outcome, Result, Step, TransferHint,
                     Unsecured, compare, cover_cost, outcome, run)

__all__ = [
    # касса
    "Account", "Income", "Payment", "Transfer", "Scenario",
    "Step", "Result", "Outcome", "run", "roll_cash",
    "cover_cost", "outcome", "compare",
    "Unsecured", "TransferHint",
    # взаиморасчёты
    "Counterparty", "Wallet", "Deal", "ScheduleRule", "FirstPayment",
    "Movement", "Assignment", "Occurrence", "OccurrenceEdit",
    "ObservedBalance", "Settlements",
    "accrued_interest", "validate", "deal_balance", "deal_amount_at",
    "deal_holder_at", "counterparty_balance", "counterparty_role",
    "funding_wallet", "payment_channel", "beneficiary", "liquidity",
    "occurrences", "planned_date",
    # прокат сделок
    "roll_deals", "roll_months", "compare_deal_strategies", "DealRoll",
    "DealMonth", "ScheduledPayment", "UnitMonth", "Expectation", "Gap",
    "MonthsRoll", "CashMonth", "ConvergenceError", "horizon",
    "roll_window", "Window",
    # прогноз
    "forecast", "forecast_shifts", "widest", "Forecast", "ForecastInput",
    "Milestone", "Deficit", "FamilyTransfer", "Discrepancy", "Shift",
    # действия и цена варианта
    "Move", "Bridge", "Prepay", "Direct", "Action", "Variant", "Base", "Price",
    "ImpossibleAction", "baseline", "impossible", "applied", "price", "prices",
    "facts", "variants",
    # объявленные наборы
    "I_OWE", "OWED_TO_ME", "DIRECTIONS", "OUT", "IN", "MOVEMENT_DIRECTIONS",
    "PERSON", "LEGAL", "KINDS", "SUBTYPES", "STARTER_GROUPS", "WALLET_KINDS",
    "FAMILY", "CREDITOR", "DEBTOR", "BOTH",
    "EXPECTED", "PAID", "PAID_LATE", "SKIPPED", "POSTPONED",
    "OCCURRENCE_STATUSES",
    "KIND_PAYMENT", "KIND_PREPAID", "KIND_TRANSFER", "UNSECURED_KINDS",
    "AVALANCHE", "SNOWBALL", "STRATEGIES",
    "WINDOW_DEBTS_CLOSED", "WINDOW_INCOME_ENDS", "WINDOW_MONTH_CAP",
    "WINDOW_REASONS",
    "PAYOFF_NOT_CLOSED", "PAYOFF_CLOSED_BEFORE",
]
