"""Типизированная модель кассового сценария.

Все деньги — Decimal (никаких float). Даты — datetime.date.
Модель описывает: кошельки (карты), доходы, платежи (с указанием,
какой кошелёк их финансирует), переводы между кошельками, прожиточный минимум.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, getcontext, localcontext

#: Денежный знак — копейка. Дом один на весь движок: суммы, остатки и
#: округления берут её отсюда, копии константы расходятся молча.
KOPEK = Decimal("0.01")


def kopek(value: Decimal) -> Decimal:
    """Округлить до копейки, не падая на выросшем числе.

    Дом один у денежного знака и у его округления: копии расходятся молча.
    Контекст Decimal по умолчанию несёт 28 значащих цифр. У долга, который не
    закрывается и растёт по ставке, остаток за прокат перерастает их, и
    `quantize` падает `InvalidOperation` вместо честного «не закрылось»: прокат
    обязан дойти до конца окна на любом графике. Знаков берётся столько,
    сколько нужно самому числу, — на обычных суммах ответ не меняется.
    """
    with localcontext() as ctx:
        ctx.prec = max(getcontext().prec, value.adjusted() + 4)
        return value.quantize(KOPEK, ROUND_HALF_UP)


@dataclass
class Account:
    """Кошелёк (счёт, карта). balance — стартовый остаток на момент начала сценария."""
    name: str
    balance: Decimal
    is_credit: bool = False  # кредитка: минус на ней — долг, а не дыра
    available: bool = True   # недоступный — в ликвидность не входит
    limit: Decimal | None = None  # кредитный лимит; свободный лимит деньгами не считается


@dataclass
class Income:
    """Приход на кошелёк в дату."""
    date: date
    amount: Decimal
    account: str


@dataclass
class Payment:
    """Платёж контрагенту, финансируемый конкретным кошельком.

    `counterparty` — уид контрагента, `purpose` — назначение. Платёж без
    контрагента не бывает.

    `prepaid` — досрочка: платёж сверх графика. Касса проводит её после
    обязательных платежей месяца, поэтому свободные деньги считаются до неё.

    `debt` — признак «долговой / жизненный»: долговой платёж кредитным лимитом
    не финансируется — банк закрывает лимит для погашения кредитов. Признак
    берётся из модели: у сделки с остатком платёж долговой, у регулярного
    расхода — жизненный; досрочка всегда долговая. По умолчанию долговой:
    ошибка в сторону запрета даёт пессимистичный прогноз, в сторону
    разрешения — радужный, и о ней владелец узнаёт от банка, а не от движка.
    """
    date: date
    amount: Decimal
    account: str | None = None
    counterparty: str | None = None
    purpose: str | None = None
    prepaid: bool = False
    debt: bool = True


@dataclass
class Transfer:
    """Перевод между кошельками (например, пополнение карты под автосписание)."""
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
    #: Резерв обязательств: деньги, оставленные под платежи начала следующего
    #: месяца. Вычитается при расчёте свободных денег — без него рвётся начало
    #: месяца. None = не задан: порог живёт в зоне, а не в движке.
    obligation_reserve: Decimal | None = None
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
                    limit: Decimal | None, debt: bool = True) -> Decimal | None:
    """Сколько кошелёк может отдать под платёж: деньги кошелька плюс свободный лимит.

    Свободный лимит идёт только под жизненный расход (`debt=False`): долговой
    платёж банк лимитом не финансирует. Деньги — это остаток, а на кредитном
    переплата (`wallet_money`). Складывать со свободным лимитом именно остаток
    нельзя: у кредитного в минусе долг уже вычтен из лимита, и второй раз он
    вычелся бы остатком — кошелёк с долгом 900 и лимитом 1000 объявил бы, что не
    может ничего, хотя сто рублей у него есть.
    Недоступный кошелёк не отдаёт ничего: с арестованной карты не заплатить.
    У кредитного с неизвестным лимитом ёмкость не оценена — None, а не ноль:
    отказ по неизвестному движок не выдумывает, а подставленный ноль запретил бы
    платёж, который на деле проходит. Долговой платёж лимита не спрашивает
    вовсе, поэтому неизвестный лимит его ёмкость не отменяет: она равна деньгам
    кошелька. Отрицательной ёмкости не бывает: кошелёк, ушедший в минус, не
    может отдать ничего.
    """
    if not available:
        return Decimal(0)
    money = wallet_money(balance, is_credit=is_credit, available=available)
    if debt:
        return max(money, Decimal(0))
    free = wallet_free_limit(balance, is_credit=is_credit, available=available,
                             limit=limit)
    if free is None:
        return None
    return max(money + free, Decimal(0))
