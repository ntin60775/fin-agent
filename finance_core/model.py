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
    available: bool = True   # недоступный — в ликвидность не входит
    limit: Decimal | None = None  # кредитный лимит; свободный лимит деньгами не считается


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

    `prepaid` — досрочка: платёж сверх графика. Касса проводит её после
    обязательных платежей месяца, поэтому свободные деньги считаются до неё.
    """
    date: date
    amount: Decimal
    creditor: str | None = None
    account: str | None = None
    counterparty: str | None = None
    purpose: str | None = None
    prepaid: bool = False


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


# --- правило кошелька ------------------------------------------------------
#
# Одно правило на оба носителя — `Account` в кассе и `Wallet` во взаиморасчётах:
# у правила один носитель, иначе два места разойдутся. Функции принимают остаток
# отдельным доводом, а не берут его из объекта: касса ведёт бегущий остаток, и
# ёмкость кошелька меняется вместе с ним.

def wallet_debt(balance: Decimal, *, is_credit: bool) -> Decimal:
    """Минус на кредитном — долг. На некредитном минус — нехватка, а не долг."""
    if is_credit and balance < 0:
        return -balance
    return Decimal(0)


def wallet_money(balance: Decimal, *, is_credit: bool, available: bool) -> Decimal:
    """Сколько остатка кошелька идёт в ликвидность.

    Недоступный кошелёк не даёт ничего; минус на некредитном остаётся минусом —
    это нехватка, а не деньги; свободный лимит кредитного деньгами не считается,
    а плюс на кредитном — свои деньги (переплата), и он считается.
    """
    if not available:
        return Decimal(0)
    if is_credit:
        return balance if balance > 0 else Decimal(0)
    return balance


def wallet_free_limit(balance: Decimal, *, is_credit: bool, available: bool,
                      limit: Decimal | None) -> Decimal | None:
    """Сколько лимита кошелька можно использовать: показывается, но не деньги.

    У недоступного кошелька свободного лимита нет — ноль, а не число: арест или
    закрытый банком лимит использования не дают. Размер линии при этом не
    теряется — он в `limit`. Лимит неизвестен — None («не оценено»).
    У некредитного кошелька кредитной линии нет вовсе — это ноль.
    """
    if not is_credit or not available:
        return Decimal(0)
    if limit is None:
        return None
    return max(limit - wallet_debt(balance, is_credit=is_credit), Decimal(0))


def wallet_capacity(balance: Decimal, *, is_credit: bool, available: bool,
                    limit: Decimal | None) -> Decimal | None:
    """Сколько кошелёк может отдать под платёж: деньги кошелька плюс свободный лимит.

    Деньги — это остаток, а на кредитном переплата (`wallet_money`). Складывать
    со свободным лимитом именно остаток нельзя: у кредитного в минусе долг уже
    вычтен из лимита, и второй раз он вычелся бы остатком — кошелёк с долгом 900
    и лимитом 1000 объявил бы, что не может ничего, хотя сто рублей у него есть.
    Недоступный кошелёк не отдаёт ничего: с арестованной карты не заплатить.
    У кредитного с неизвестным лимитом ёмкость не оценена — None, а не ноль:
    отказ по неизвестному движок не выдумывает, а подставленный ноль запретил бы
    платёж, который на деле проходит. Отрицательной ёмкости не бывает: кошелёк,
    ушедший в минус, не может отдать ничего.
    """
    if not available:
        return Decimal(0)
    free = wallet_free_limit(balance, is_credit=is_credit, available=available,
                             limit=limit)
    if free is None:
        return None
    money = wallet_money(balance, is_credit=is_credit, available=available)
    return max(money + free, Decimal(0))
