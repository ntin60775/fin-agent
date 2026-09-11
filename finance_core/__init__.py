"""Ядро расчёта личных финансов: касса и долги.

Две независимые стороны:

- `solver` — касса: хватит ли денег в периоде, где разрыв, хватает ли остатка
  прожить до следующего прихода;
- `debt`   — долги: когда они закроются и сколько стоят проценты.

Ядро не хранит состояние и не читает markdown: оно считает то, что ему передали,
и возвращает результат. Источник цифр — на стороне вызывающего кода. Деньги —
`Decimal`; зависимостей нет, только стандартная библиотека.
"""
from .debt import (Debt, MonthSnapshot, Plan, compare_strategies,
                   roll_forward)
from .model import Account, Income, Payment, Scenario, Transfer
from .solver import (Outcome, Result, Step, compare, cover_cost, optional_cap,
                     outcome, run)

__all__ = [
    # касса
    "Account", "Income", "Payment", "Transfer", "Scenario",
    "Step", "Result", "Outcome", "run", "optional_cap", "cover_cost",
    "outcome", "compare",
    # долги
    "Debt", "MonthSnapshot", "Plan", "roll_forward", "compare_strategies",
]
