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
                       ForecastInput, Milestone, Shift, ALLOCATION_NOT_SET,
                       SCALE_NOT_SET, forecast, forecast_shifts, widest)
from .model import Account, Income, Payment, Scenario, Transfer
from .roll import (AVALANCHE, PAYOFF_CLOSED_BEFORE, PAYOFF_NOT_CLOSED, SNOWBALL,
                   STRATEGIES, WINDOW_DEBTS_CLOSED, WINDOW_INCOME_ENDS,
                   WINDOW_MONTH_CAP, WINDOW_REASONS,
                   ConvergenceError, DealMonth, DealRoll, Expectation, Gap,
                   MonthsRoll, ScheduledPayment, UnitMonth, Window,
                   compare_deal_strategies, horizon, roll_deals, roll_months,
                   roll_window)
from .solver import CashMonth, roll_cash
from .settlements import (BODY, BOTH, CAPS, CAP_NONE, CAP_SHARE, CAP_SUM,
                          COSTS, CREDITOR, DEBTOR, DIRECTIONS, EXPECTED, FAMILY,
                          IN, INTEREST, I_OWE, KINDS, LEGAL,
                          MOVEMENT_DIRECTIONS, OCCURRENCE_STATUSES, OUT,
                          OWED_TO_ME, PAID, PAID_LATE, PARTS, PENALTY, PERSON,
                          POSTPONED, SKIPPED, STARTER_GROUPS, SUBTYPES,
                          TRIGGERS, TRIGGER_FULL_UNPAID, TRIGGER_OVERDUE,
                          TRIGGER_OVERDUE_DAYS, TRIGGER_OVERDUE_SUM,
                          WALLET_KINDS, AllocationRule, Assignment, Charge,
                          DebtGrowth, PartPayment, Restructure, TriggeredCharge,
                          Counterparty, Deal, FirstPayment, Movement, Occurrence,
                          OccurrenceEdit, ObservedBalance, PenaltyCap,
                          PenaltyRule, PenaltyStep, ScheduleRule, Settlements,
                          TriggerRule, Wallet, accrued_interest,
                          accrued_penalty, allocate_payment, beneficiary,
                          counterparty_balance, counterparty_role,
                          deal_amount_at, deal_balance, deal_growth,
                          deal_holder_at, deal_parts, funding_wallet, liquidity,
                          occurrences, overdue_amount, overdue_amounts,
                          payment_channel, planned_date, rule_at,
                          trigger_charges, validate)
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
    "Movement", "PartPayment", "Charge", "Assignment", "Restructure",
    "Occurrence", "OccurrenceEdit", "ObservedBalance", "Settlements",
    "allocate_payment",
    "PenaltyRule", "PenaltyStep", "PenaltyCap", "AllocationRule", "TriggerRule",
    "TriggeredCharge", "DebtGrowth", "rule_at",
    "accrued_interest", "accrued_penalty", "validate", "deal_balance",
    "deal_parts", "deal_growth", "deal_amount_at",
    "overdue_amount", "overdue_amounts", "trigger_charges",
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
    "SCALE_NOT_SET", "ALLOCATION_NOT_SET",
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
    "BODY", "INTEREST", "PENALTY", "COSTS", "PARTS",
    "CAP_NONE", "CAP_SUM", "CAP_SHARE", "CAPS",
    "TRIGGER_OVERDUE", "TRIGGER_FULL_UNPAID", "TRIGGER_OVERDUE_DAYS",
    "TRIGGER_OVERDUE_SUM", "TRIGGERS",
    "KIND_PAYMENT", "KIND_PREPAID", "KIND_TRANSFER", "UNSECURED_KINDS",
    "AVALANCHE", "SNOWBALL", "STRATEGIES",
    "WINDOW_DEBTS_CLOSED", "WINDOW_INCOME_ENDS", "WINDOW_MONTH_CAP",
    "WINDOW_REASONS",
    "PAYOFF_NOT_CLOSED", "PAYOFF_CLOSED_BEFORE",
]
