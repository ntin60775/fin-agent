"""Взаиморасчёты: контрагенты, кошельки, сделки, движения и передачи долга.

Сторона, отвечающая на вопрос «сколько я должен и сколько должны мне». Сделка
несёт условия — сумму, ставку, правило графика — и направление; движения —
факты: когда, сколько, куда и кому уплачено. Ни расчётный остаток по сделке, ни
сальдо по контрагенту не хранятся: и то и другое считается из условий и движений
(`docs/decisions/derived-balances.md`). Начисление процентов — тоже производное:
одно правило на отрезок дат (`accrued_interest`), которым считает и прокат.

Правило графика порождает **вхождения** — плановые платежи с тремя датами и
статусом (`occurrences`). Вхождения тоже считаются: правило плюс правки журнала
(`OccurrenceEdit`) плюс движения, ссылающиеся на вхождение. Правки приходят
снаружи — переносом, пропуском, сменой суммы, — а не правят само правило.

Личных чисел и имён здесь нет — всё приходит снаружи.
"""
from __future__ import annotations

import calendar
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from .model import kopek, wallet_debt, wallet_free_limit, wallet_money

# --- объявленные наборы ----------------------------------------------------

#: Направление сделки: кто кому должен.
I_OWE = "я должен"
OWED_TO_ME = "мне должны"
DIRECTIONS = (I_OWE, OWED_TO_ME)

#: Направление движения: куда ушли деньги из кассы владельца.
OUT = "расход"
IN = "приход"
MOVEMENT_DIRECTIONS = (OUT, IN)

#: Тип контрагента — закрытый список; подтипы зависят от типа.
PERSON = "физлицо"
LEGAL = "юрлицо"
KINDS = (PERSON, LEGAL)
SUBTYPES = {
    LEGAL: ("банк", "МФО", "ПКО", "госорган", "прочее"),
    PERSON: ("родственник", "знакомый", "коллега", "прочее"),
}

#: Группа своих: деньги такому контрагенту — передача внутри семьи, в отчётах
#: отдельной строкой с детализацией, кому и на что.
FAMILY = "семья"

#: Группы контрагента — свободные метки; стартовый набор обязателен.
STARTER_GROUPS = (FAMILY, "работа", "жильё", "долги")

#: Типы кошелька: стартовый набор, дальше список расширяется потребителем.
WALLET_KINDS = ("карта", "счёт", "наличные", "электронный кошелёк")

#: Роли контрагента — выводятся из взаиморасчётов, не хранятся.
CREDITOR = "кредитор"
DEBTOR = "должник"
BOTH = "и то и другое"

#: Статусы вхождения — производные от правок и движений, не хранятся.
EXPECTED = "ожидается"
PAID = "исполнен"
PAID_LATE = "исполнен с опозданием"
SKIPPED = "пропущен"
POSTPONED = "перенесён"
OCCURRENCE_STATUSES = (EXPECTED, PAID, PAID_LATE, SKIPPED, POSTPONED)


# --- сущности --------------------------------------------------------------

@dataclass
class Counterparty:
    """Субъект взаиморасчётов: банк, МФО, коллектор, частное лицо, работодатель.

    Опознаётся уидом — он же имя файла карточки; имя нужно только для показа.
    Тип и подтип берутся из объявленных наборов (`KINDS`, `SUBTYPES`), опечатка
    отклоняется. Группы — свободные метки со стартовым набором
    (`STARTER_GROUPS`); их может быть несколько. Роли здесь нет: «кредитор»,
    «должник» и «и то и другое» выводятся из сделок (`counterparty_role`).
    """
    uid: str
    name: str
    kind: str
    subtype: str
    groups: tuple[str, ...] = ()


@dataclass
class Wallet:
    """Где лежат деньги или кредитная линия.

    `balance` — фактический остаток, подписанный: минус на кредитном — долг
    (`debt`), а не дыра; плюс на кредитном — свои деньги, то есть переплата
    (`overpayment`). `limit` — кредитный лимит; свободный лимит (`free_limit`)
    показывается, но деньгами не считается.

    `available=False` — пользоваться нельзя: арест или лимит, закрытый банком.
    Такой кошелёк показывается отдельно, в ликвидность не входит и свободного
    лимита не даёт. Долг на нём остаётся долгом: его гасят, и это видно.
    """
    uid: str
    name: str
    kind: str
    balance: Decimal
    observed: date | None = None    # дата наблюдения фактического остатка
    available: bool = True
    is_credit: bool = False
    limit: Decimal | None = None    # кредитный лимит; у некредитного не бывает

    @property
    def debt(self) -> Decimal:
        """Минус на кредитном — долг. На некредитном минус — дыра, а не долг."""
        return wallet_debt(self.balance, is_credit=self.is_credit)

    @property
    def overpayment(self) -> Decimal:
        """Плюс на кредитном — свои деньги (переплата), а не свободный лимит."""
        if self.is_credit and self.balance > 0:
            return self.balance
        return Decimal(0)

    @property
    def free_limit(self) -> Decimal | None:
        """Сколько лимита кошелька можно использовать: показывается, но не деньги.

        Правило — `model.wallet_free_limit`: у недоступного кошелька свободного
        лимита нет — ноль, а не число; неизвестный лимит — None («не оценено»);
        у некредитного линии нет вовсе.
        """
        return wallet_free_limit(self.balance, is_credit=self.is_credit,
                                 available=self.available, limit=self.limit)

    @property
    def money(self) -> Decimal:
        """Сколько остатка кошелька идёт в ликвидность.

        Правило — `model.wallet_money`: недоступный кошелёк не даёт ничего; минус
        на некредитном остаётся минусом — это дыра, а не деньги; свободный лимит
        кредитного деньгами не считается.
        """
        return wallet_money(self.balance, is_credit=self.is_credit,
                            available=self.available)


@dataclass
class FirstPayment:
    """Отдельный первый платёж: своя дата и своя сумма.

    Стоит перед рядом регулярных вхождений и в их число не входит: «отдельный» —
    значит не из ряда. Дата проходит те же оговорки, что и остальные, — кламп на
    конец месяца и сдвиг с выходного по флагу правила.
    """
    date: date
    amount: Decimal


@dataclass
class ScheduleRule:
    """Правило графика: как рождаются вхождения сделки.

    Закрытый набор форм:

    - **фиксированная сумма с числом платежей** — `payment` и `count`: сколько
      платить и сколько раз; после `count` вхождений график кончается;
    - **обязательный платёж процентом от остатка** — `percent`: доля остатка, которую
      платят в месяц; сумма вхождения становится известна только в прокате;
    - **несколько дней в месяце** — `days`: сколько дней, столько вхождений;
    - **отдельный первый платёж** — `first`: своя дата и своя сумма перед рядом.

    `days` — дни месяца, когда наступает вхождение; дня, которого нет в месяце,
    не бывает (берётся последний), а выходной сдвигает дату по флагу
    `shift_weekend`: платёж — вперёд, доход — назад (сторону задаёт направление
    сделки, `occurrences`); праздники не учитываются. `start` — с какого месяца
    идёт ряд: без него не сосчитать `count`.

    Правило порождает вхождения, а правят их поверх — переносом, пропуском,
    сменой суммы (`OccurrenceEdit`). Само правило при этом не меняется.
    """
    days: tuple[int, ...] = ()
    shift_weekend: bool = True
    start: date | None = None
    payment: Decimal | None = None      # фиксированная сумма платежа
    count: int | None = None            # число платежей
    percent: Decimal | None = None      # обязательный платёж: доля остатка на месяц
    first: FirstPayment | None = None   # отдельный первый платёж


@dataclass
class Deal:
    """Договорённость с контрагентом, порождающая взаиморасчёты.

    Несёт условия — сумму, ставку, правило графика — и направление. Сделка без
    суммы (`amount=None`) — регулярный расход: её платёж входит в обязательную
    нагрузку, но закрывать нечего, поэтому она не закрывается и досрочке не
    подлежит. Сделки без контрагента не бывает.

    `counterparty` — кому должен (текущий держатель), `wallet` и `channel` —
    кошелёк и канал платежа по умолчанию, `benefit_for` — за кого (получатель
    выгоды). Движение любое из трёх переопределяет. `second_priority` —
    обязательный график или второй приоритет; `closure_unit` — единица
    закрытия, которой помечены сделки, гасящиеся вместе. Остаток не хранится:
    его считает `deal_balance`.

    `start` — **начало долга**: дата, с которой долг существует, и отсюда идёт
    начисление (`deal_balance`, прокат). Обязательно для сделки со ставкой и
    суммой: без даты «долг на дату» считать не от чего. У беспроцентной сделки
    и у регулярного расхода его не спрашивают — начислять нечего. Это другой
    вопрос, чем `ScheduleRule.start` («начало ряда платежей»): ряд платежей
    начинается, когда начинаются платежи, долг существует раньше — и после их
    конца. Передача долга начала не двигает (меняется держатель и версия тела,
    а не дата долга), а дата позже начала окна — не ошибка: долг возникает
    внутри расчёта, и до неё начисленного нет.

    `prepay=False` — сделка досрочке не подлежит: свободные деньги её не
    трогают, обязательный платёж идёт как идёт. Так помечают револьверную
    кредитку: закрывать её досрочкой бессмысленно, лимит освобождается
    минимальным платежом и снова тратится. Признак — про конкретную сделку, а не
    про стратегию: стратегия говорит, чью ставку гасить первой.
    """
    uid: str
    title: str
    counterparty: str
    direction: str = I_OWE
    amount: Decimal | None = None       # сумма к закрытию; None — регулярный расход
    start: date | None = None           # начало долга — отсюда идёт начисление
    rate_per_year: Decimal | None = None
    rate_per_day: Decimal | None = None
    schedule: ScheduleRule | None = None
    second_priority: bool = False
    wallet: str | None = None
    channel: str | None = None
    benefit_for: str | None = None
    closure_unit: str | None = None
    kind: str | None = None             # вид сделки — свободная метка для отчётов
    prepay: bool = True                 # разрешена ли досрочка из свободных денег

    @property
    def closing(self) -> bool:
        """Сделка закрывается (остаток есть) или это регулярный расход."""
        return self.amount is not None


@dataclass
class Movement:
    """Факт по сделке: когда, сколько, куда и кому уплачено.

    Сумма положительная — направление несёт само движение, а не знак.
    `direction` — «расход» (деньги ушли из кассы владельца) или «приход».
    Движение по сделке уменьшает остаток, движение против неё (возврат) —
    увеличивает. `paid_to` — кому уплачено на тот момент: после передачи долга
    прежние движения по-прежнему указывают на прежнего держателя. `wallet`,
    `channel` и `benefit_for` переопределяют значения сделки, `purpose` —
    назначение (на что ушли деньги).

    `occurrence` — какое вхождение движение закрывает, по его плановой дате:
    плановая не меняется, поэтому и годится в опознание. Вхождение считается
    исполненным по такому движению — фактическая дата вхождения и есть его дата.
    Движение без ссылки вхождение не закрывает: это досрочка или платёж вне
    графика — деньги ушли, а ряд платежей идёт своим чередом.
    """
    date: date
    amount: Decimal
    deal: str
    direction: str = OUT
    paid_to: str | None = None
    wallet: str | None = None
    channel: str | None = None
    benefit_for: str | None = None
    purpose: str | None = None
    occurrence: date | None = None


@dataclass
class ObservedBalance:
    """Фактический остаток сделки на дату — наблюдение извне, а не второй источник.

    Введён из выписки или приложения кредитора и служит для сверки: расчётный
    остаток считается (`deal_balance`), а расхождение — незакрытый вопрос, а не
    повод править число. Правится не число, а данные
    (`docs/decisions/derived-balances.md`).
    """
    deal: str
    on: date
    amount: Decimal


@dataclass
class Assignment:
    """Передача долга (цессия): от кого к кому, когда, сколько было и что стало.

    `amount` — сумма к закрытию на момент передачи, `delta` — насколько она
    изменилась (может быть и отрицательной). Запись хранит историю: прежние
    условия не теряются, а вместе с новой суммой задают версию условий,
    действующую с даты передачи (`deal_amount_at`, `deal_holder_at`). Сумма
    сделки и последняя версия — одно число: расхождение ловит `validate`.
    """
    date: date
    deal: str
    from_holder: str
    to_holder: str
    amount: Decimal
    delta: Decimal = Decimal(0)


@dataclass
class OccurrenceEdit:
    """Правка вхождения поверх правила: перенести, пропустить, сменить сумму.

    Запись журнала зоны: движок её не хранит, а получает вместе с книгой.
    Вхождение опознаётся парой «сделка + плановая дата» — плановая не меняется,
    поэтому и годится в опознание. Несколько правок одного вхождения
    применяются по порядку: поздняя побеждает по тем полям, которые задаёт, —
    переговорили о новой дате, значит действует новая.

    `postponed` — перенесено; отложение, то есть перенос без даты, — тот же
    перенос с `moved_to=None`: дата ещё не назначена, платить пока некуда.
    Перенесённая дата берётся как назначено: сдвиг по выходным — дело правила
    графика, а не договорённости. `skipped` — пропущено: платить не будут,
    остаток от этого не уменьшается.
    """
    deal: str
    planned: date
    postponed: bool = False
    moved_to: date | None = None
    skipped: bool = False
    amount: Decimal | None = None


@dataclass
class Occurrence:
    """Плановый платёж (вхождение): три даты и статус.

    Плановая дата — по графику, не меняется; перенесённая — когда должен после
    договорённости; фактическая — когда заплатили. Статус — производная от
    правок и движений: «ожидается», «исполнен», «исполнен с опозданием»,
    «пропущен», «перенесён». Ни то, ни другое движок не хранит: вхождения
    считаются, а не лежат.

    `amount` — сколько платить; None у обязательного платежа процентом от остатка: сумма
    зависит от остатка и становится известна только в прокате. `paid` — сколько
    уже закрыто движениями, ссылающимися на это вхождение (`Movement.occurrence`).
    """
    deal: str
    planned: date
    amount: Decimal | None
    moved: date | None = None
    actual: date | None = None
    paid: Decimal = Decimal(0)
    status: str = EXPECTED

    @property
    def due(self) -> date | None:
        """Когда платёж должен состояться: перенесённая дата, иначе плановая.

        Отложенное вхождение без назначенной даты — None: платить некуда, пока
        договорённость не даст дату.
        """
        if self.moved is not None:
            return self.moved
        return None if self.status == POSTPONED else self.planned

    @property
    def remaining(self) -> Decimal | None:
        """Сколько по вхождению ещё не уплачено; None — сумма не определена."""
        if self.amount is None:
            return None
        return max(self.amount - self.paid, Decimal(0))

    @property
    def payable(self) -> bool:
        """Платёж ещё предстоит: вхождение не исполнено и не пропущено."""
        return self.status in (EXPECTED, POSTPONED) and self.due is not None


@dataclass
class Settlements:
    """Взаиморасчёты целиком: контрагенты, кошельки, сделки, движения, передачи.

    Собранную книгу сначала проверяют `validate`, а потом спрашивают производные
    величины: проверка ловит ссылки на необъявленное и расхождения, на которых
    остаток и сальдо посчитались бы неверно. `edits` — правки вхождений из
    журнала зоны: они меняют график, а не условия сделки. `observed` —
    наблюдения извне: фактический остаток с датой, с которым сверяется расчётный.
    """
    counterparties: list[Counterparty] = field(default_factory=list)
    wallets: list[Wallet] = field(default_factory=list)
    deals: list[Deal] = field(default_factory=list)
    movements: list[Movement] = field(default_factory=list)
    assignments: list[Assignment] = field(default_factory=list)
    edits: list[OccurrenceEdit] = field(default_factory=list)
    observed: list[ObservedBalance] = field(default_factory=list)


# --- проверка ссылок -------------------------------------------------------

def _unique(uids: Iterable[str], what: str) -> None:
    seen: set[str] = set()
    for uid in uids:
        if uid in seen:
            raise ValueError(f"{what}: уид {uid!r} объявлен дважды")
        seen.add(uid)


def _declared(uid: str | None, known: set[str], what: str) -> None:
    """Ссылка на необъявленное — ошибка с перечнем объявленных."""
    if uid is not None and uid not in known:
        raise ValueError(f"{what}: неизвестный уид {uid!r}; объявлены: {sorted(known)}")


def validate(book: Settlements) -> None:
    """Проверить объявленные наборы и ссылки: явная ошибка вместо KeyError."""
    _unique((c.uid for c in book.counterparties), "контрагент")
    _unique((w.uid for w in book.wallets), "кошелёк")
    _unique((d.uid for d in book.deals), "сделка")

    _validate_counterparties(book)
    _validate_wallets(book)
    _validate_deals(book)
    _validate_movements(book)
    _validate_assignments(book)
    _validate_edits(book)
    _validate_observed(book)


def _validate_counterparties(book: Settlements) -> None:
    for c in book.counterparties:
        if not c.uid:
            raise ValueError("контрагент без уида")
        if not c.name:
            raise ValueError(f"контрагент {c.uid!r}: имя нужно для показа")
        if c.kind not in KINDS:
            raise ValueError(f"контрагент {c.uid!r}: неизвестный тип {c.kind!r}; "
                             f"объявлены: {list(KINDS)}")
        allowed = SUBTYPES[c.kind]
        if c.subtype not in allowed:
            raise ValueError(f"контрагент {c.uid!r}: подтип {c.subtype!r} не объявлен "
                             f"для типа {c.kind!r}; объявлены: {list(allowed)}")
        seen: set[str] = set()
        for group in c.groups:
            if not group:
                raise ValueError(f"контрагент {c.uid!r}: пустая группа")
            if group in seen:
                raise ValueError(f"контрагент {c.uid!r}: группа {group!r} повторена")
            seen.add(group)


def _validate_wallets(book: Settlements) -> None:
    for w in book.wallets:
        if not w.uid:
            raise ValueError("кошелёк без уида")
        if not w.name:
            raise ValueError(f"кошелёк {w.uid!r}: название нужно для показа")
        if not w.kind:
            raise ValueError(f"кошелёк {w.uid!r}: не указан тип; "
                             f"стартовый набор: {list(WALLET_KINDS)}")
        if w.limit is not None and not w.is_credit:
            raise ValueError(f"кошелёк {w.uid!r}: лимит бывает только у кредитного")
        if w.limit is not None and w.limit < 0:
            raise ValueError(f"кошелёк {w.uid!r}: лимит не может быть отрицательным")


def _validate_deals(book: Settlements) -> None:
    counterparties = {c.uid for c in book.counterparties}
    wallets = {w.uid for w in book.wallets}
    for d in book.deals:
        if not d.uid:
            raise ValueError("сделка без уида")
        if not d.title:
            raise ValueError(f"сделка {d.uid!r}: название нужно для показа")
        if d.direction not in DIRECTIONS:
            raise ValueError(f"сделка {d.uid!r}: неизвестное направление "
                             f"{d.direction!r}; объявлены: {list(DIRECTIONS)}")
        if d.amount is not None and d.amount < 0:
            raise ValueError(f"сделка {d.uid!r}: сумма к закрытию не может быть "
                             f"отрицательной")
        if d.rate_per_year is not None and d.rate_per_day is not None:
            raise ValueError(f"сделка {d.uid!r}: ставка либо годовая, либо дневная")
        if ((d.rate_per_year is not None or d.rate_per_day is not None)
                and d.amount is not None and d.start is None):
            raise ValueError(
                f"сделка {d.uid!r}: ставка и сумма есть, а начала долга нет — "
                f"без него долг на дату не считается; укажите дату начала "
                f"долга (start). У беспроцентной сделки и у регулярного расхода "
                f"её не спрашивают — начислять нечего")
        if d.schedule is not None:
            _validate_rule(d.uid, d.schedule)
        if d.closure_unit is not None and d.amount is None:
            raise ValueError(f"сделка {d.uid!r}: у регулярного расхода нет единицы "
                             f"закрытия — закрывать нечего")
        if d.closure_unit is not None and d.direction == OWED_TO_ME:
            raise ValueError(f"сделка {d.uid!r}: требование в единице закрытия — "
                             f"копилка собирает то, что гасится вместе, а требование "
                             f"приходит, а не гасится")
        _declared(d.counterparty, counterparties, f"сделка {d.uid!r}: контрагент")
        _declared(d.wallet, wallets, f"сделка {d.uid!r}: кошелёк по умолчанию")
        _declared(d.channel, counterparties, f"сделка {d.uid!r}: канал платежа")
        _declared(d.benefit_for, counterparties, f"сделка {d.uid!r}: получатель выгоды")


def _validate_rule(uid: str, rule: ScheduleRule) -> None:
    """Правило графика: закрытый набор форм, и каждая — целиком."""
    seen: set[int] = set()
    for day in rule.days:
        if not 1 <= day <= 31:
            raise ValueError(f"сделка {uid!r}: день месяца {day} вне 1–31")
        if day in seen:
            raise ValueError(f"сделка {uid!r}: день месяца {day} повторён — "
                             f"вхождение задвоится")
        seen.add(day)
    # Дня, которого нет в месяце, не бывает: короткий месяц схлопывает 30-е и
    # 31-е в одно вхождение, и опознать их станет нечем.
    short = [min(day, 28) for day in rule.days]
    if len(set(short)) != len(short):
        raise ValueError(f"сделка {uid!r}: дни месяца {list(rule.days)} схлопываются "
                         f"в коротком месяце — вхождения задвоятся")
    if not rule.days and rule.first is None:
        raise ValueError(f"сделка {uid!r}: правило графика пустое — ни дней месяца, "
                         f"ни первого платежа")
    if rule.payment is not None and rule.payment <= 0:
        raise ValueError(f"сделка {uid!r}: сумма платежа должна быть положительной")
    if rule.count is not None:
        if rule.count < 1:
            raise ValueError(f"сделка {uid!r}: число платежей должно быть "
                             f"положительным")
        if rule.start is None:
            raise ValueError(f"сделка {uid!r}: без даты начала не сосчитать платежи")
    if rule.percent is not None and not Decimal(0) < rule.percent <= Decimal(1):
        raise ValueError(f"сделка {uid!r}: процент от остатка — доля от 0 до 1")
    if rule.count is not None and rule.percent is not None:
        raise ValueError(f"сделка {uid!r}: график либо числом платежей, либо "
                         f"процентом от остатка — не тем и другим сразу")
    if rule.first is not None and rule.first.amount <= 0:
        raise ValueError(f"сделка {uid!r}: первый платёж должен быть положительным")


def _validate_movements(book: Settlements) -> None:
    counterparties = {c.uid for c in book.counterparties}
    wallets = {w.uid for w in book.wallets}
    deals = {d.uid for d in book.deals}
    for m in book.movements:
        if m.direction not in MOVEMENT_DIRECTIONS:
            raise ValueError(f"движение по сделке {m.deal!r}: неизвестное направление "
                             f"{m.direction!r}; объявлены: {list(MOVEMENT_DIRECTIONS)}")
        if m.amount <= 0:
            raise ValueError(f"движение по сделке {m.deal!r}: сумма должна быть "
                             f"положительной, а направление — у движения")
        _declared(m.deal, deals, "движение: сделка")
        _declared(m.wallet, wallets, "движение: финансирующий кошелёк")
        _declared(m.paid_to, counterparties, "движение: кому уплачено")
        _declared(m.channel, counterparties, "движение: канал платежа")
        _declared(m.benefit_for, counterparties, "движение: получатель выгоды")


def _validate_assignments(book: Settlements) -> None:
    counterparties = {c.uid for c in book.counterparties}
    deals = {d.uid: d for d in book.deals}
    last: dict[str, Assignment] = {}
    seen: set[tuple[str, date]] = set()
    for a in book.assignments:
        if a.amount < 0:
            raise ValueError(f"передача долга по сделке {a.deal!r}: сумма на момент "
                             f"передачи не может быть отрицательной")
        _declared(a.deal, set(deals), "передача долга: сделка")
        _declared(a.from_holder, counterparties, "передача долга: прежний держатель")
        _declared(a.to_holder, counterparties, "передача долга: новый держатель")
        if (a.deal, a.date) in seen:
            raise ValueError(f"сделка {a.deal!r}: две передачи долга в одну дату "
                             f"{a.date}: какая из них последняя — не определить")
        seen.add((a.deal, a.date))
        if a.deal in deals and (a.deal not in last or a.date >= last[a.deal].date):
            last[a.deal] = a

    # Последняя передача — действующая версия условий: ни сумма, ни держатель
    # сделки не имеют права разойтись с записью, иначе у одного числа два носителя.
    for uid, a in last.items():
        deal = deals[uid]
        if deal.amount is None:
            raise ValueError(f"сделка {uid!r}: у регулярного расхода передавать "
                             f"нечего — закрывать нечего")
        version = a.amount + a.delta
        if deal.amount != version:
            raise ValueError(
                f"сделка {uid!r}: сумма {deal.amount} расходится с последней "
                f"передачей долга ({version}): действует версия из записи, "
                f"правьте данные, а не число")
        if deal.counterparty != a.to_holder:
            raise ValueError(
                f"сделка {uid!r}: держатель {deal.counterparty!r} расходится с "
                f"последней передачей долга ({a.to_holder!r}): правьте данные, "
                f"а не поле")


def _validate_edits(book: Settlements) -> None:
    deals = {d.uid for d in book.deals}
    for e in book.edits:
        _declared(e.deal, deals, "правка вхождения: сделка")
        if e.moved_to is not None and not e.postponed:
            raise ValueError(f"вхождение {e.deal!r} от {e.planned}: перенесённая "
                             f"дата без переноса")
        if e.amount is not None and e.amount <= 0:
            raise ValueError(f"вхождение {e.deal!r} от {e.planned}: сумма должна "
                             f"быть положительной")

    # Правки одного вхождения сливаются по порядку — судим по тому, что вышло.
    for (uid, planned), e in _merged_edits(book).items():
        if uid in deals and not occurrences(book, uid, planned, planned):
            raise ValueError(f"вхождение {uid!r} от {planned}: такого вхождения по "
                             f"графику нет — правьте данные")
        if e.postponed and e.skipped:
            raise ValueError(f"вхождение {uid!r} от {planned}: и перенесено, и "
                             f"пропущено — правьте данные")
        if e.skipped and any(m.deal == uid and m.occurrence == planned
                             for m in book.movements):
            raise ValueError(f"вхождение {uid!r} от {planned}: пропущено, а движение "
                             f"по нему есть — правьте данные")


def _validate_observed(book: Settlements) -> None:
    """Наблюдения извне: ссылка на объявленную сделку, сумма не отрицательная.

    У регулярного расхода остатка нет — сверять нечего, и наблюдение по нему
    отклоняется: иначе оно навсегда осталось бы открытым вопросом прогноза.
    Расхождение же факта с расчётом ошибкой не объявляется: проверка падает на
    необъяснённом расхождении, а не на самом расхождении — его видно открытым
    вопросом прогноза (`docs/decisions/derived-balances.md`).
    """
    deals = {d.uid: d for d in book.deals}
    for o in book.observed:
        _declared(o.deal, set(deals), "фактический остаток: сделка")
        if o.amount < 0:
            raise ValueError(f"фактический остаток по сделке {o.deal!r} на {o.on}: "
                             f"сумма не может быть отрицательной")
        if o.deal in deals and deals[o.deal].amount is None:
            raise ValueError(f"фактический остаток по сделке {o.deal!r}: у "
                             f"регулярного расхода остатка нет — сверять нечего")


# --- производные величины --------------------------------------------------

def _deal(book: Settlements, uid: str) -> Deal:
    for d in book.deals:
        if d.uid == uid:
            return d
    raise ValueError(f"неизвестная сделка {uid!r}; "
                     f"объявлены: {sorted(d.uid for d in book.deals)}")


def _assignments(book: Settlements, deal_uid: str) -> list[Assignment]:
    return sorted((a for a in book.assignments if a.deal == deal_uid),
                  key=lambda a: a.date)


def _signed(deal: Deal, movement: Movement) -> Decimal:
    """Движение со знаком для остатка: по сделке — плюс, против неё — минус."""
    along = ((deal.direction == I_OWE and movement.direction == OUT)
             or (deal.direction == OWED_TO_ME and movement.direction == IN))
    return movement.amount if along else -movement.amount


def _paid(deal: Deal, movements: Iterable[Movement]) -> Decimal:
    """Сколько уплачено по сделке: по сделке — в остаток, против неё — из остатка."""
    return sum((_signed(deal, m) for m in movements), Decimal(0))


def deal_holder_at(book: Settlements, deal_uid: str, on: date) -> str:
    """Кому должны по сделке на дату: до передачи долга — прежний держатель."""
    deal = _deal(book, deal_uid)
    future = [a for a in _assignments(book, deal_uid) if a.date > on]
    return future[0].from_holder if future else deal.counterparty


def deal_amount_at(book: Settlements, deal_uid: str, on: date) -> Decimal | None:
    """Сумма к закрытию по сделке на дату — с учётом версии условий.

    Передача долга создаёт версию: с даты передачи действует сумма на момент
    передачи плюс её изменение; прежняя сохраняется в записи. У регулярного
    расхода суммы нет — None: закрывать нечего.
    """
    deal = _deal(book, deal_uid)
    if deal.amount is None:
        return None
    assignments = _assignments(book, deal_uid)
    past = [a for a in assignments if a.date <= on]
    if past:
        last = past[-1]
        return last.amount + last.delta
    return assignments[0].amount if assignments else deal.amount


def deal_balance(book: Settlements, deal_uid: str, on: date) -> Decimal | None:
    """Долг на дату — канон остатка: тело минус движения плюс начисленное.

    Одно определение на все пути: по нему идут расчётный остаток, сальдо по
    контрагенту и сверка с выпиской, и он же — состояние проката: прокат
    инициализируется этим каноном на открытие окна, поэтому остаток проката на
    дату окна и расчётный остаток на ту же дату — одно число.

    Считается, а не хранится, — чтобы его можно было сверить с фактическим
    остатком. Начисление идёт той же функцией `accrued_interest`, что и в
    прокате: от начала долга (`Deal.start`) до конца дня `on`, отрезок внутри
    месяца — пропорционально дням, база — бегущий остаток, округление —
    `model.kopek`. Ставки нет — начисления нет, и канон сводится к «тело минус
    движения»; начала долга ещё нет (дата позже `on`) — начисленного тоже нет.
    У регулярного расхода остатка нет — `None`.
    """
    amount = deal_amount_at(book, deal_uid, on)
    if amount is None:
        return None
    deal = _deal(book, deal_uid)
    movements = [m for m in book.movements
                 if m.deal == deal_uid and m.date <= on]
    return kopek(amount - _paid(deal, movements)
                 + _accrued(book, deal, movements, on))


def _accrued(book: Settlements, deal: Deal, movements: Sequence[Movement],
             on: date) -> Decimal:
    """Начисленное по сделке с её начала до конца дня `on`.

    `movements` — движения до `on` включительно: до начала долга они входят в
    базу, после — в платежи отрезка (и в базу следующего месяца, как в прокате).
    """
    since = deal.start
    if since is None or on < since:
        return Decimal(0)                # начисляться нечему: долга ещё нет
    base = (deal_amount_at(book, deal.uid, since)
            - _paid(deal, [m for m in movements if m.date < since]))
    payments = [(m.date, _signed(deal, m)) for m in movements
                if since <= m.date <= on]
    # Смены версии тела (передача долга) входят в базу по своей дате: те, что
    # раньше начала долга, уже в базе (тело берётся версией на начало), а
    # поздние идут списком в ту же функцию, что и платежи.
    deltas = [(a.date, a.delta) for a in book.assignments
              if a.deal == deal.uid and since <= a.date <= on]
    return accrued_interest(deal, base, since, on, payments, deltas)


def _principal_left(book: Settlements, deal_uid: str, on: date) -> Decimal | None:
    """Тело долга на дату без начисления — служебная величина закрытости проката.

    Не канон и не публичная величина: закрытость отвечает на вопрос «есть ли
    что прокатывать», а вхождения гасят тело — начисленное вхождением не
    является, и сделка с съеденным телом не возвращается в прокат, даже если
    проценты за ней ещё числятся. «Сколько долгу на эту дату» — другой вопрос,
    на него отвечает канон `deal_balance`, начисленное включающий.
    """
    amount = deal_amount_at(book, deal_uid, on)
    if amount is None:
        return None
    deal = _deal(book, deal_uid)
    return amount - _paid(deal, (m for m in book.movements
                                 if m.deal == deal_uid and m.date <= on))


# --- начисление ------------------------------------------------------------

def accrued_interest(deal: Deal, balance: Decimal, since: date, until: date,
                     payments: Sequence[tuple[date, Decimal]],
                     deltas: Sequence[tuple[date, Decimal]] = ()) -> Decimal:
    """Начисление процентов по сделке за отрезок `[since, until]` включительно.

    Правило начисления одно, и его вызывают оба носителя остатка: прокат —
    месяц целиком, расчётный остаток (`deal_balance`) — от начала долга
    (`Deal.start`) до даты. Поэтому остаток проката и расчётный остаток на одну
    дату сходятся по построению, а не совпадением: считает одна функция, одна
    база и одно округление.
    Доводы — сделка (ставки и база начисления), остаток на начало отрезка,
    границы отрезка и платежи отрезка списком пар «дата — сумма»: раскладка
    платежей по дням — дело функции, не вызывающего. Полный месяц — частный
    случай отрезка.

    `deltas` — смены версии тела (передача долга), тоже списком «дата — сумма»,
    но с обратным знаком смысла: положительная сумма — долг вырос. Это не
    платёж: деньги не уходили, а вот проценты идут на новое тело. День смены
    увеличивает остаток до начисления за этот день, а в годовой базе смена
    входит в базу того же месяца, в котором случилась, — долг изменился, а не
    был уплачен.

    Правила прежние, они жили в месячном цикле проката:

    - **Дневная ставка** считается по дням: платёж дня уменьшает остаток до
      начисления за этот день, поэтому перенос платежа внутри месяца стоит
      денег; день, на котором остаток стал ≤ 0, начисления не даёт.
    - **Годовая** начисляется за месяц от остатка на начало этого месяца:
      платежи внутри месяца начисление этого месяца не меняют, но уменьшают
      остаток следующего. Отрезок внутри месяца — пропорционально дням.
    - **Каждое слагаемое округляется до копейки** — день у дневной базы, месяц
      у годовой, `HALF_UP` (`model.kopek`). Второй раз проценты не округляются:
      прокат берёт число как есть.

    Начисленное месяца ложится в остаток следующего — бегущий остаток, как в
    прокате; отсюда длинный отрезок и полный месяц считаются одним числом.
    """
    if until < since:
        return Decimal(0)
    daily = deal.rate_per_day
    yearly = deal.rate_per_year
    if daily is None and yearly is None:
        return Decimal(0)
    by_day: dict[date, Decimal] = {}
    for when, amount in payments:
        if since <= when <= until:
            by_day[when] = by_day.get(when, Decimal(0)) + amount
    shift: dict[date, Decimal] = {}
    for when, amount in deltas:
        if since <= when <= until:
            shift[when] = shift.get(when, Decimal(0)) + amount

    total = Decimal(0)
    left = balance
    for year, month in _months(since, until):
        days = calendar.monthrange(year, month)[1]
        from_day = max(since, date(year, month, 1))
        till_day = min(until, date(year, month, days))
        month_start = left
        moved = sum((amount for day, amount in shift.items()
                     if from_day <= day <= till_day), Decimal(0))
        accrued = Decimal(0)
        if daily is not None:
            day = from_day
            while day <= till_day:
                left -= by_day.get(day, Decimal(0))
                left += shift.get(day, Decimal(0))
                if left <= 0:
                    break
                accrued += kopek(left * daily)
                day += timedelta(days=1)
        elif yearly is not None and month_start + moved > 0:
            accrued = (month_start + moved) * yearly / 12
            covered = (till_day - from_day).days + 1
            if covered != days:
                accrued = accrued * Decimal(covered) / Decimal(days)
            accrued = kopek(accrued)
        paid = sum((amount for day, amount in by_day.items()
                    if from_day <= day <= till_day), Decimal(0))
        left = month_start + accrued - paid + moved
        total += accrued
    return total


def _sides(book: Settlements, counterparty: str, on: date) -> tuple[Decimal, Decimal]:
    """Две стороны взаиморасчётов на дату: должен я и должны мне."""
    debt_to = Decimal(0)
    owed_to_me = Decimal(0)
    for deal in book.deals:
        if deal_holder_at(book, deal.uid, on) != counterparty:
            continue
        outstanding = deal_balance(book, deal.uid, on)
        if outstanding is None:          # регулярный расход: закрывать нечего
            continue
        if deal.direction == I_OWE:
            debt_to += outstanding
        else:
            owed_to_me += outstanding
    return debt_to, owed_to_me


def counterparty_balance(book: Settlements, counterparty: str, on: date) -> Decimal:
    """Сальдо по контрагенту на дату: сколько я должен минус сколько он мне.

    Отчётная величина: зачёт не производится — остатки по сделкам остаются
    своими, сальдо ничего не меняет и нигде не хранится.
    """
    debt_to, owed_to_me = _sides(book, counterparty, on)
    return debt_to - owed_to_me


def counterparty_role(book: Settlements, counterparty: str, on: date) -> str | None:
    """Роль контрагента на дату — выводится из взаиморасчётов, не хранится.

    «кредитор» — должен я, «должник» — должны мне, «и то и другое» — и так и
    так. Переплата играет в обратную сторону: свои деньги у чужого — это его
    долг передо мной, и наоборот. Взаиморасчётов нет — None: роли не из чего
    взяться.
    """
    debt_to, owed_to_me = _sides(book, counterparty, on)
    i_owe = max(debt_to, Decimal(0)) + max(-owed_to_me, Decimal(0))
    they_owe = max(owed_to_me, Decimal(0)) + max(-debt_to, Decimal(0))
    if i_owe > 0 and they_owe > 0:
        return BOTH
    if i_owe > 0:
        return CREDITOR
    if they_owe > 0:
        return DEBTOR
    return None


def funding_wallet(deal: Deal, movement: Movement | None = None) -> str | None:
    """Финансирующий кошелёк: у движения своё значение, у сделки — по умолчанию."""
    if movement is not None and movement.wallet is not None:
        return movement.wallet
    return deal.wallet


def payment_channel(deal: Deal, movement: Movement | None = None) -> str | None:
    """Канал платежа (через кого): у движения своё, у сделки — по умолчанию."""
    if movement is not None and movement.channel is not None:
        return movement.channel
    return deal.channel


def beneficiary(deal: Deal, movement: Movement | None = None) -> str | None:
    """Получатель выгоды (за кого): у движения своё, у сделки — по умолчанию."""
    if movement is not None and movement.benefit_for is not None:
        return movement.benefit_for
    return deal.benefit_for


def liquidity(book: Settlements) -> Decimal:
    """Ликвидность: деньги доступных кошельков. Свободный лимит не считается."""
    return sum((w.money for w in book.wallets), Decimal(0))


# --- вхождения -------------------------------------------------------------

def _merged_edits(book: Settlements) -> dict[tuple[str, date], OccurrenceEdit]:
    """Правки одного вхождения, слитые по порядку: поздняя побеждает."""
    merged: dict[tuple[str, date], OccurrenceEdit] = {}
    for e in book.edits:
        key = (e.deal, e.planned)
        current = merged.get(key)
        if current is None:
            merged[key] = OccurrenceEdit(e.deal, e.planned, e.postponed, e.moved_to,
                                         e.skipped, e.amount)
            continue
        if e.postponed:
            current.postponed = True
        if e.moved_to is not None:
            current.moved_to = e.moved_to
        if e.skipped:
            current.skipped = True
        if e.amount is not None:
            current.amount = e.amount
    return merged


def planned_date(year: int, month: int, day: int, shift_weekend: bool,
                 backward: bool = False) -> date:
    """Дата вхождения по правилу: кламп на конец месяца и сдвиг с выходного.

    Дня, которого нет в месяце, не бывает — берётся последний. Выходной сдвигает
    дату: платёж — вперёд, к понедельнику; доход — назад, к пятнице (`backward`).
    Праздники не учитываются: календаря праздников у движка нет.
    """
    last = calendar.monthrange(year, month)[1]
    when = date(year, month, min(day, last))
    step = -1 if backward else 1
    while shift_weekend and when.weekday() >= 5:
        when += timedelta(days=step)
    return when


def _months(since: date, until: date) -> Iterator[tuple[int, int]]:
    """Месяцы от `since` до `until` включительно — по одному."""
    year, month = since.year, since.month
    while (year, month) <= (until.year, until.month):
        yield year, month
        year, month = year + (month == 12), month % 12 + 1


def _linked(deal: Deal, planned: date,
            movements: Iterable[Movement]) -> tuple[date | None, Decimal]:
    """Факт по вхождению: когда оно закрылось и сколько закрыто.

    Считаются движения, ссылающиеся на вхождение (`Movement.occurrence`):
    по сделке — в зачёт, против неё — из зачёта. Фактическая дата — дата
    закрывающего движения, последнего из связанных.
    """
    when: date | None = None
    total = Decimal(0)
    for m in movements:
        if m.deal != deal.uid or m.occurrence != planned:
            continue
        along = ((deal.direction == I_OWE and m.direction == OUT)
                 or (deal.direction == OWED_TO_ME and m.direction == IN))
        total += m.amount if along else -m.amount
        if when is None or m.date > when:
            when = m.date
    return when, total


def _occurrence(book: Settlements, deal: Deal, planned: date,
                amount: Decimal | None,
                edits: dict[tuple[str, date], OccurrenceEdit]) -> Occurrence:
    """Вхождение на плановую дату: правило, правки, факты — и статус."""
    edit = edits.get((deal.uid, planned))
    moved: date | None = None
    skipped = False
    if edit is not None:
        moved = edit.moved_to if edit.postponed else None
        skipped = edit.skipped
        if edit.amount is not None:
            amount = edit.amount

    actual, paid = _linked(deal, planned, book.movements)
    postponed = edit is not None and edit.postponed
    due = moved if moved is not None else planned
    if skipped:
        status = SKIPPED
    elif paid > 0 and (amount is None or paid >= amount):
        status = PAID if actual is not None and actual <= due else PAID_LATE
    elif postponed:
        status = POSTPONED
    else:
        status = EXPECTED
    return Occurrence(deal.uid, planned, amount, moved, actual, paid, status)


def occurrences(book: Settlements, deal_uid: str, since: date,
                until: date) -> list[Occurrence]:
    """Вхождения сделки в окне плановых дат.

    Порождаются правилом графика и правятся правками из журнала; статус — от
    движений, ссылающихся на вхождение. Окно задаётся **плановыми** датами:
    перенесённая дата может увести вхождение за его пределы — это дело проката,
    он раскладывает вхождения по месяцам, когда платить.

    Вхождения считаются, а не лежат: движок их не хранит, а получает правило,
    правки и движения — и возвращает список.
    """
    deal = _deal(book, deal_uid)
    rule = deal.schedule
    if rule is None:
        return []
    edits = _merged_edits(book)
    found: list[Occurrence] = []
    # Доход сдвигается назад, платёж вперёд: выходной переезжает по стороне денег.
    backward = deal.direction == OWED_TO_ME

    if rule.first is not None:
        first = rule.first.date
        when = planned_date(first.year, first.month, first.day, rule.shift_weekend,
                            backward=backward)
        if since <= when <= until:
            found.append(_occurrence(book, deal, when, rule.first.amount, edits))

    if rule.days:
        # Месяц до и после окна: сдвиг с выходного уводит дату за его край.
        before = since.replace(day=1) - timedelta(days=1)
        after = (until.replace(day=1) + timedelta(days=32)).replace(day=1)
        # Дни идут по календарю, а не в том порядке, как записаны: иначе число
        # платежей отсчитается от чужого вхождения.
        days = sorted(rule.days)
        for year, month in _months(before, after):
            if rule.start is not None and (year, month) < (rule.start.year,
                                                           rule.start.month):
                continue               # ряд идёт с начала, раньше вхождений нет
            for position, day in enumerate(days):
                when = planned_date(year, month, day, rule.shift_weekend,
                                    backward=backward)
                if not since <= when <= until:
                    continue
                if rule.count is not None and rule.start is not None:
                    index = (((year - rule.start.year) * 12 + month - rule.start.month)
                             * len(days) + position + 1)
                    if index > rule.count:
                        continue
                found.append(_occurrence(book, deal, when, rule.payment, edits))

    found.sort(key=lambda o: o.planned)
    for left, right in zip(found, found[1:]):
        if left.planned == right.planned:
            raise ValueError(f"сделка {deal_uid!r}: два вхождения на {left.planned} — "
                             f"правьте правило графика")
    return found
