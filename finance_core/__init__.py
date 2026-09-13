"""Ядро расчёта личных финансов: касса, долги и взаиморасчёты.

Три стороны:

- `solver` — касса: хватит ли денег в периоде, где разрыв, хватает ли остатка
  прожить до следующего прихода;
- `debt` — долги: когда они закроются и сколько стоят проценты;
- `settlements` — взаиморасчёты: контрагенты, кошельки, сделки и движения,
  остаток по сделке и сальдо по контрагенту — производные величины.

Ядро не хранит состояние и не читает markdown: оно считает то, что ему передали,
и возвращает результат. Источник цифр — на стороне вызывающего кода. Деньги —
`Decimal`; зависимостей нет, только стандартная библиотека.
"""
from .debt import (Debt, MonthSnapshot, Plan, compare_strategies,
                   roll_forward)
from .model import Account, Income, Payment, Scenario, Transfer
from .settlements import (BOTH, CREDITOR, DEBTOR, DIRECTIONS, IN, I_OWE, KINDS,
                          LEGAL, MOVEMENT_DIRECTIONS, OUT, OWED_TO_ME, PERSON,
                          STARTER_GROUPS, SUBTYPES, WALLET_KINDS, Assignment,
                          Counterparty, Deal, Movement, ScheduleRule,
                          Settlements, Wallet, beneficiary,
                          counterparty_balance, counterparty_role,
                          deal_amount_at, deal_balance, deal_holder_at,
                          funding_wallet, liquidity, payment_channel, validate)
from .solver import (Outcome, Result, Step, compare, cover_cost, optional_cap,
                     outcome, run)

__all__ = [
    # касса
    "Account", "Income", "Payment", "Transfer", "Scenario",
    "Step", "Result", "Outcome", "run", "optional_cap", "cover_cost",
    "outcome", "compare",
    # долги
    "Debt", "MonthSnapshot", "Plan", "roll_forward", "compare_strategies",
    # взаиморасчёты
    "Counterparty", "Wallet", "Deal", "ScheduleRule", "Movement", "Assignment",
    "Settlements", "validate", "deal_balance", "deal_amount_at",
    "deal_holder_at", "counterparty_balance", "counterparty_role",
    "funding_wallet", "payment_channel", "beneficiary", "liquidity",
    # объявленные наборы
    "I_OWE", "OWED_TO_ME", "DIRECTIONS", "OUT", "IN", "MOVEMENT_DIRECTIONS",
    "PERSON", "LEGAL", "KINDS", "SUBTYPES", "STARTER_GROUPS", "WALLET_KINDS",
    "CREDITOR", "DEBTOR", "BOTH",
]
