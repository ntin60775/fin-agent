"""Ядро расчёта личных финансов: касса, долги и взаиморасчёты.

Четыре стороны:

- `solver` — касса: хватит ли денег в периоде, где разрыв, хватает ли остатка
  прожить до следующего прихода;
- `debt` — старый прокат долгов: плоская модель, живёт до переезда зоны;
- `settlements` — взаиморасчёты: контрагенты, кошельки, сделки и движения,
  остаток по сделке и сальдо по контрагенту — производные величины; правило
  графика порождает вхождения со статусами;
- `roll` — прокат сделок по месяцам: что платится, что копится в копилке и
  когда закрывается последний долг.

Ядро не хранит состояние и не читает markdown: оно считает то, что ему передали,
и возвращает результат. Источник цифр — на стороне вызывающего кода. Деньги —
`Decimal`; зависимостей нет, только стандартная библиотека.
"""
from .debt import (Debt, MonthSnapshot, Plan, compare_strategies,
                   roll_forward)
from .forecast import (Deficit, Discrepancy, FamilyTransfer, Forecast,
                       ForecastInput, Milestone, Shift, forecast,
                       forecast_shifts, widest)
from .model import Account, Income, Payment, Scenario, Transfer
from .roll import (AVALANCHE, SNOWBALL, STRATEGIES, ConvergenceError,
                   DealMonth, DealRoll, Expectation, Gap, MonthsRoll,
                   ScheduledPayment, UnitMonth, compare_deal_strategies,
                   roll_deals, roll_months)
from .solver import CashMonth, roll_cash
from .settlements import (BOTH, CREDITOR, DEBTOR, DIRECTIONS, EXPECTED, FAMILY,
                          IN, I_OWE, KINDS, LEGAL, MOVEMENT_DIRECTIONS,
                          OCCURRENCE_STATUSES, OUT, OWED_TO_ME, PAID, PAID_LATE,
                          PERSON, POSTPONED, SKIPPED, STARTER_GROUPS, SUBTYPES,
                          WALLET_KINDS, Assignment, Counterparty, Deal,
                          FirstPayment, Movement, Occurrence, OccurrenceEdit,
                          ObservedBalance, ScheduleRule, Settlements, Wallet,
                          beneficiary, counterparty_balance, counterparty_role,
                          deal_amount_at, deal_balance, deal_holder_at,
                          funding_wallet, liquidity, occurrences,
                          payment_channel, planned_date, validate)
from .solver import (KIND_PAYMENT, KIND_PREPAID, KIND_TRANSFER,
                     UNSECURED_KINDS, Outcome, Result, Step, TransferHint,
                     Unsecured, compare, cover_cost, optional_cap, outcome, run)

__all__ = [
    # касса
    "Account", "Income", "Payment", "Transfer", "Scenario",
    "Step", "Result", "Outcome", "run", "roll_cash", "optional_cap",
    "cover_cost", "outcome", "compare",
    "Unsecured", "TransferHint",
    # старый прокат долгов
    "Debt", "MonthSnapshot", "Plan", "roll_forward", "compare_strategies",
    # взаиморасчёты
    "Counterparty", "Wallet", "Deal", "ScheduleRule", "FirstPayment",
    "Movement", "Assignment", "Occurrence", "OccurrenceEdit",
    "ObservedBalance", "Settlements",
    "validate", "deal_balance", "deal_amount_at", "deal_holder_at",
    "counterparty_balance", "counterparty_role", "funding_wallet",
    "payment_channel", "beneficiary", "liquidity", "occurrences", "planned_date",
    # прокат сделок
    "roll_deals", "roll_months", "compare_deal_strategies", "DealRoll",
    "DealMonth", "ScheduledPayment", "UnitMonth", "Expectation", "Gap",
    "MonthsRoll", "CashMonth", "ConvergenceError",
    # прогноз
    "forecast", "forecast_shifts", "widest", "Forecast", "ForecastInput",
    "Milestone", "Deficit", "FamilyTransfer", "Discrepancy", "Shift",
    # объявленные наборы
    "I_OWE", "OWED_TO_ME", "DIRECTIONS", "OUT", "IN", "MOVEMENT_DIRECTIONS",
    "PERSON", "LEGAL", "KINDS", "SUBTYPES", "STARTER_GROUPS", "WALLET_KINDS",
    "FAMILY", "CREDITOR", "DEBTOR", "BOTH",
    "EXPECTED", "PAID", "PAID_LATE", "SKIPPED", "POSTPONED",
    "OCCURRENCE_STATUSES",
    "KIND_PAYMENT", "KIND_PREPAID", "KIND_TRANSFER", "UNSECURED_KINDS",
    "AVALANCHE", "SNOWBALL", "STRATEGIES",
]
