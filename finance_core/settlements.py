"""Взаиморасчёты: контрагенты, кошельки, сделки, движения и передачи долга.

Сторона, отвечающая на вопрос «сколько я должен и сколько должны мне». Сделка
несёт условия — сумму, ставку, правило графика — и направление; движения —
факты: когда, сколько, куда и кому уплачено. Ни расчётный остаток по сделке, ни
сальдо по контрагенту не хранятся: и то и другое считается из условий и движений
(`docs/decisions/derived-balances.md`).

Новая форма живёт рядом со старой: строковое имя контрагента в кассовой модели
(`model.Payment.creditor`) продолжает работать до переезда зоны. Личных чисел и
имён здесь нет — всё приходит снаружи.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

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

#: Группы контрагента — свободные метки; стартовый набор обязателен.
STARTER_GROUPS = ("семья", "работа", "жильё", "долги")

#: Типы кошелька: стартовый набор, дальше список расширяется потребителем.
WALLET_KINDS = ("карта", "счёт", "наличные", "электронный кошелёк")

#: Роли контрагента — выводятся из взаиморасчётов, не хранятся.
CREDITOR = "кредитор"
DEBTOR = "должник"
BOTH = "и то и другое"


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
        if self.is_credit and self.balance < 0:
            return -self.balance
        return Decimal(0)

    @property
    def overpayment(self) -> Decimal:
        """Плюс на кредитном — свои деньги (переплата), а не свободный лимит."""
        if self.is_credit and self.balance > 0:
            return self.balance
        return Decimal(0)

    @property
    def free_limit(self) -> Decimal | None:
        """Сколько лимита кошелька можно использовать: показывается, но не деньги.

        У недоступного кошелька свободного лимита нет — ноль, а не число: арест
        или закрытый банком лимит использования не дают. Размер линии при этом
        не теряется — он в `limit`. Лимит неизвестен — None («не оценено»).
        У некредитного кошелька кредитной линии нет вовсе — это ноль.
        """
        if not self.is_credit or not self.available:
            return Decimal(0)
        if self.limit is None:
            return None
        return max(self.limit - self.debt, Decimal(0))

    @property
    def money(self) -> Decimal:
        """Сколько остатка кошелька идёт в ликвидность.

        Недоступный кошелёк не даёт ничего; минус на некредитном остаётся
        минусом — это дыра, а не деньги; свободный лимит кредитного деньгами не
        считается.
        """
        if not self.available:
            return Decimal(0)
        if self.is_credit:
            return self.overpayment
        return self.balance


@dataclass
class ScheduleRule:
    """Правило графика: как рождаются вхождения сделки.

    `days` — дни месяца, когда наступает вхождение; дня, которого нет в месяце,
    не бывает (берётся последний), а выходной сдвигает дату — это правила
    проката (тикет 02). `shift_weekend` — флаг сдвига: платёж сдвигается вперёд;
    приходы считает касса (тикет 03).
    """
    days: tuple[int, ...] = ()
    shift_weekend: bool = True


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
    """
    uid: str
    title: str
    counterparty: str
    direction: str = I_OWE
    amount: Decimal | None = None       # сумма к закрытию; None — регулярный расход
    rate_per_year: Decimal | None = None
    rate_per_day: Decimal | None = None
    schedule: ScheduleRule | None = None
    second_priority: bool = False
    wallet: str | None = None
    channel: str | None = None
    benefit_for: str | None = None
    closure_unit: str | None = None
    kind: str | None = None             # вид сделки — свободная метка для отчётов

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
class Settlements:
    """Взаиморасчёты целиком: контрагенты, кошельки, сделки, движения, передачи.

    Собранную книгу сначала проверяют `validate`, а потом спрашивают производные
    величины: проверка ловит ссылки на необъявленное и расхождения, на которых
    остаток и сальдо посчитались бы неверно.
    """
    counterparties: list[Counterparty] = field(default_factory=list)
    wallets: list[Wallet] = field(default_factory=list)
    deals: list[Deal] = field(default_factory=list)
    movements: list[Movement] = field(default_factory=list)
    assignments: list[Assignment] = field(default_factory=list)


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
        if d.schedule is not None:
            for day in d.schedule.days:
                if not 1 <= day <= 31:
                    raise ValueError(f"сделка {d.uid!r}: день месяца {day} вне 1–31")
        _declared(d.counterparty, counterparties, f"сделка {d.uid!r}: контрагент")
        _declared(d.wallet, wallets, f"сделка {d.uid!r}: кошелёк по умолчанию")
        _declared(d.channel, counterparties, f"сделка {d.uid!r}: канал платежа")
        _declared(d.benefit_for, counterparties, f"сделка {d.uid!r}: получатель выгоды")


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


def _paid(deal: Deal, movements: Iterable[Movement]) -> Decimal:
    """Сколько уплачено по сделке: по сделке — в остаток, против неё — из остатка."""
    total = Decimal(0)
    for m in movements:
        along = ((deal.direction == I_OWE and m.direction == OUT)
                 or (deal.direction == OWED_TO_ME and m.direction == IN))
        total += m.amount if along else -m.amount
    return total


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
    """Расчётный остаток по сделке на дату: условия минус движения до неё.

    Считается, а не хранится, — чтобы его можно было сверить с фактическим
    остатком. У регулярного расхода остатка нет — None.
    """
    amount = deal_amount_at(book, deal_uid, on)
    if amount is None:
        return None
    deal = _deal(book, deal_uid)
    movements = [m for m in book.movements if m.deal == deal_uid and m.date <= on]
    return amount - _paid(deal, movements)


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
