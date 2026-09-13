"""Типизированная модель кассового сценария.

Все деньги — Decimal (никаких float). Даты — datetime.date.
Модель описывает: счета (карты), доходы, платежи (с указанием,
какой счёт их финансирует), переводы между счетами, прожиточный минимум.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass
class Account:
    """Счёт/карта. balance — стартовый остаток на момент начала сценария."""
    name: str
    balance: Decimal
    is_credit: bool = False  # кредитка: минус на ней — долг, а не дыра


@dataclass
class Income:
    """Приход на счёт в дату."""
    date: date
    amount: Decimal
    account: str


@dataclass
class Payment:
    """Платёж контрагенту, финансируемый конкретным счётом.

    `creditor` — строковое имя (старая форма, живёт до переезда зоны),
    `counterparty` — уид контрагента и `purpose` — назначение (новая форма).
    Платёж ссылается хотя бы на одного из них: платёж без контрагента не бывает.
    Счёт обязателен — правило счёта живёт в модели, а не в комментарии.
    """
    date: date
    amount: Decimal
    creditor: str | None = None
    account: str | None = None
    counterparty: str | None = None
    purpose: str | None = None


@dataclass
class Transfer:
    """Перевод между счетами (например, пополнение карты под автосписание)."""
    date: date
    amount: Decimal
    from_account: str
    to_account: str


@dataclass
class Scenario:
    accounts: list[Account]
    income: list[Income] = field(default_factory=list)
    payments: list[Payment] = field(default_factory=list)
    transfers: list[Transfer] = field(default_factory=list)
    #: Прожиточный минимум в месяц (еда, транспорт, связь, иждивенцы).
    #: None = неизвестен (`?`) → реальный дефицит честно НЕ оценивается.
    living_floor_monthly: Decimal | None = None
    #: Дата ближайшего дохода за окном сценария:
    #: без неё не измерить прожиточный минимум после последнего прихода в окне.
    income_horizon: date | None = None
