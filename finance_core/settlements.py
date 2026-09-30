"""Взаиморасчёты: контрагенты, кошельки, сделки, движения и передачи долга.

Сторона, отвечающая на вопрос «сколько я должен и сколько должны мне». Сделка
несёт условия — сумму, ставку, правило графика — и направление; движения —
факты: когда, сколько, куда и кому уплачено; начисления (`Charge`) — факты
роста долга: решение, штраф, пошлина, издержки растут со своей даты. Ни
расчётный остаток по сделке, ни сальдо по контрагенту не хранятся: и то и
другое считается из условий, движений и начислений
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
from collections.abc import Callable, Iterable, Iterator, Sequence
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

#: Части одного долга — закрытый набор: долг раскладывается по ним, и в них
#: приходят начисления (`Charge.part`) и платежи. Штраф, пошлина, решение суда —
#: основания начисления со свободной меткой (`Charge.basis`), а не части: части
#: различает механика, основания группируют.
BODY = "тело"
INTEREST = "проценты"
PENALTY = "неустойка"
COSTS = "издержки"
PARTS = (BODY, INTEREST, PENALTY, COSTS)

#: Потолок неустойки — закрытый набор форм. «Без потолка» — явная форма, а не
#: отсутствие данных: у маркера один смысл, и его не путать с «правила нет».
CAP_NONE = "без потолка"
CAP_SUM = "сумма"
CAP_SHARE = "доля просроченной суммы"
CAPS = (CAP_NONE, CAP_SUM, CAP_SHARE)

#: Условия триггер-правил — закрытый набор: движок понимает то, что объявлено,
#: и отклоняет чужое текстом, а не молчаливым нулём.
TRIGGER_OVERDUE = "срок прошёл, есть просроченная сумма"
TRIGGER_FULL_UNPAID = "полная неуплата"
TRIGGER_OVERDUE_DAYS = "просрочка не меньше порога"
TRIGGER_OVERDUE_SUM = "сумма просрочки не меньше порога"
TRIGGERS = (TRIGGER_OVERDUE, TRIGGER_FULL_UNPAID, TRIGGER_OVERDUE_DAYS,
            TRIGGER_OVERDUE_SUM)


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
class PenaltyStep:
    """Ступень шкалы неустойки: «от `days` дней просрочки — дневная ставка `rate`».

    Ступень действует включительно: возраст просрочки ровно `days` — берётся эта
    ступень. Возраст дня — число дней со дня после срока (первый день просрочки —
    возраст 1). Порог — целое число дней; ставка — дневная: у неустойки всё по
    дням, второй базы начисления нет.
    """
    days: int
    rate: Decimal


@dataclass
class PenaltyCap:
    """Потолок неустойки — форма с дискриминантом.

    «Без потолка» (`CAP_NONE`) — известное «не ограничено», и значения у него не
    бывает; «сумма» (`CAP_SUM`) и «доля просроченной суммы» (`CAP_SHARE`) несут
    положительное значение. Потолок обязателен у правила неустойки — включая
    «без потолка»: отсутствие данных и отсутствие ограничения — разные вещи.
    """
    kind: str
    value: Decimal | None = None


@dataclass
class PenaltyRule:
    """Правило неустойки: шкала ступеней и потолок — один носитель.

    Правило неустойки целиком: у него одно содержимое, и делить шкалу с потолком
    по разным сущностям значило бы хранить половины врозь. Версии правила
    различаются датой действия (`effective`): правило без даты действует весь
    период и допустимо, только если версия одна.
    """
    steps: tuple[PenaltyStep, ...]
    cap: PenaltyCap
    effective: date | None = None


@dataclass
class AllocationRule:
    """Правило распределения платежа: порядок частей.

    `order` — перестановка `PARTS`: все четыре части, без повторов. Частичный
    порядок не определяет, куда идёт избыток, — поэтому перестановка целиком.
    Версии — по дате действия, как у остальных правил зоны.
    """
    order: tuple[str, ...]
    effective: date | None = None


@dataclass
class TriggerRule:
    """Правило триггера: условие, порог и параметры штрафа, который он породит.

    `condition` — из закрытого набора (`TRIGGERS`); порог (`threshold_days` или
    `threshold_amount`) обязателен для пороговых условий и положителен. Штраф
    задаётся ровно одним из: `charge_amount` (сумма) или `charge_percent` (доля
    исходной просроченной суммы). `part` — часть, в которую идёт штраф, — без
    умолчания: часть штрафа обязана приходить из данных. `basis` — что случилось,
    свободная метка основания производного начисления. `uid` — опознание правила:
    по нему факт-начисление ссылается на правило, и случай опознаётся парой
    «уид правила, дата срабатывания». Исполнение правила (срабатывание, дата,
    идемпотентность) — механика триггеров, форма живёт здесь.
    """
    uid: str
    condition: str
    part: str
    basis: str
    threshold_days: int | None = None
    threshold_amount: Decimal | None = None
    charge_amount: Decimal | None = None
    charge_percent: Decimal | None = None
    effective: date | None = None


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

    `triggers`, `penalties` и `allocations` — правила зоны на сделку, данные,
    а не код движка: триггеры (`TriggerRule`) опознаются уидом — по ним
    проверяется ссылка на правило у факта-начисления (`Charge.trigger`);
    неустойка (`PenaltyRule`) и порядок распределения платежа
    (`AllocationRule`) — формы с валидацией. Движок форм не выдумывает и
    умолчаний не подставляет: правила нет — `None` («не задано»).
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
    penalties: tuple[PenaltyRule, ...] = ()     # шкалы неустойки, версии по дате
    allocations: tuple[AllocationRule, ...] = ()  # порядки распределения, версии
    triggers: tuple[TriggerRule, ...] = ()      # правила триггеров — по уиду правила
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
class PartPayment:
    """Строка разбивки платежа: сколько из движения ушло в часть долга.

    Факт кредитора («куда ушёл платёж»): живёт в `Movement.allocation`; пустая
    разбивка — платёж распределяет правило зоны производно. Разбивка и правило
    не суммируются: записанный факт вытесняет производное.
    """
    part: str
    amount: Decimal


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

    Движение без ссылки гасит просрочку: самые старые исполняемые вхождения,
    чей срок уже прошёл (вхождение в день срока ещё не просрочено), в пределах
    суммы — по возрастанию срока, частичное гашение оставляет остаток. Наступившие
    и будущие вхождения оно не закрывает: досрочка и платёж вне графика не
    отменяют ряд платежей. Возврат вхождения не закрывает — это не уплата.
    Остаток движения сверх просрочек — платёж по сделке, он идёт в части.

    `allocation` — разбивка платежа по частям (`PartPayment`), факт кредитора.
    Пустой кортеж — разбивки нет: платёж распределяет правило зоны
    (`Deal.allocations`) производно. Разбивка вытесняет правило — они не
    суммируются; сумма строк равна сумме движения (валидация).
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
    allocation: tuple[PartPayment, ...] = ()


@dataclass
class Charge:
    """Начисление — факт роста долга: решение суда, штраф, пошлина, издержки.

    Долг растёт не только договорённостью и передачей: событие мира приносит
    сумму, которой раньше не было, и ей нужен носитель. `uid` — уид события:
    запись опознаётся им, поэтому одно событие не записывается дважды.
    `date` — дата события: с неё начисление растёт, и не раньше — запись задним
    числом допустима и меняет расчёт с даты события, как любое поздно
    записанное условие. `part` — часть долга, в которую идёт сумма (`PARTS`):
    штраф, пошлина и решение — основания начисления (`basis`, свободная метка),
    а не новые части. `trigger` — уид правила триггера, чей случай записан этим
    фактом: по нему факт и производное срабатывание опознаются одним случаем —
    «уид правила, дата срабатывания»; запись без правила ссылки не несёт.

    Начислено — не уплачено: долг растёт, а денег это не двигает. Деньги по
    сделке несёт `Movement`, деньги кошелька — `Payment`; «начислено и уплачено»
    одним фактом не записывается.
    """
    uid: str
    date: date
    amount: Decimal
    deal: str
    part: str
    basis: str | None = None
    trigger: str | None = None


@dataclass
class TriggeredCharge:
    """Производное начисление штрафа: случай триггера, увиденный движком.

    Триггер — правило зоны («не уплатил в срок — пойдёт штраф»), применяемое
    движком к фактам книги; начисление по нему — производная величина, как
    проценты: не хранится и повторный прогон даёт то же число (R3-Q11).
    `trigger` — уид правила (Q35): по нему и дате срабатывания случай опознаётся
    и вытесняется фактом. `date` — дата срабатывания: когда условие стало
    истинным (R5-Q29). `part` — часть, в которую идёт штраф, `basis` — что
    случилось (обе — из правила). Потребители (канон, части, прокат) суммируют
    его как начисление; у события мира — свой носитель `Charge` с уидом события.
    """
    trigger: str
    date: date
    amount: Decimal
    part: str
    basis: str


@dataclass
class ObservedBalance:
    """Фактический остаток сделки на дату — наблюдение извне, а не второй источник.

    Введён из выписки или приложения кредитора и служит для сверки: расчётный
    остаток считается (`deal_balance`), и сверка идёт по этому канону — она
    **может сойтись**. Ненулевая разница (наблюдение минус расчёт) — незакрытый
    вопрос: правило начисления движка не совпало с правилом кредитора (иная
    ставка, база начисления, начало долга, комиссия), и это повод править
    условия сделки или записать факт, а не число
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
    остаток и сальдо посчитались бы неверно. `charges` — начисления: факты роста
    долга от события, опознаваемые уидом события. `edits` — правки вхождений из
    журнала зоны: они меняют график, а не условия сделки. `observed` —
    наблюдения извне: фактический остаток с датой, с которым сверяется расчётный.
    """
    counterparties: list[Counterparty] = field(default_factory=list)
    wallets: list[Wallet] = field(default_factory=list)
    deals: list[Deal] = field(default_factory=list)
    movements: list[Movement] = field(default_factory=list)
    charges: list[Charge] = field(default_factory=list)
    assignments: list[Assignment] = field(default_factory=list)
    edits: list[OccurrenceEdit] = field(default_factory=list)
    observed: list[ObservedBalance] = field(default_factory=list)


# --- проверка ссылок -------------------------------------------------------

def _positive(value: Decimal | None) -> bool:
    """Значение задано и положительно: ноль и None — «не задано»."""
    return value is not None and value > 0


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
    _validate_charges(book)
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
        if (d.amount is not None and d.start is None
                and (d.rate_per_year not in (None, Decimal(0))
                     or d.rate_per_day not in (None, Decimal(0)))):
            # Нулевая ставка — не ставка: начисление равно нулю при любой дате,
            # поэтому дата у такой сделки не нужна — как у беспроцентной.
            raise ValueError(
                f"сделка {d.uid!r}: ставка и сумма есть, а начала долга нет — "
                f"без него долг на дату не считается; укажите дату начала "
                f"долга (start). У беспроцентной сделки и у регулярного расхода "
                f"её не спрашивают — начислять нечего")
        if d.schedule is not None:
            _validate_rule(d.uid, d.schedule)
        _validate_deal_rules(d)
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


def _validate_deal_rules(deal: Deal) -> None:
    """Правила зоны на сделку: форма каждой и версии по дате действия.

    Правила приходят снаружи, и движок обязан их понимать: закрытый набор форм,
    каждая — целиком, текст ошибки называет правило и что исправить. Умолчаний
    нет: правила нет — «не задано», а не ноль.
    """
    for rule in deal.penalties:
        if not rule.steps:
            raise ValueError(f"правило неустойки сделки {deal.uid!r}: ступени "
                             f"обязательны — шкалы без ступеней не бывает")
        previous: int | None = None
        for step in rule.steps:
            if step.days < 0:
                raise ValueError(f"правило неустойки сделки {deal.uid!r}: порог "
                                 f"ступени не может быть отрицательным")
            if step.rate < 0:
                raise ValueError(f"правило неустойки сделки {deal.uid!r}: ставка "
                                 f"ступени не может быть отрицательной")
            if previous is not None and step.days <= previous:
                raise ValueError(f"правило неустойки сделки {deal.uid!r}: ступени "
                                 f"должны возрастать, пороги — не отрицательные")
            previous = step.days
        if rule.cap is None:
            raise ValueError(f"правило неустойки сделки {deal.uid!r}: потолок "
                             f"обязателен — включая „без потолка“")
        if rule.cap.kind not in CAPS:
            raise ValueError(f"правило неустойки сделки {deal.uid!r}: потолок "
                             f"{rule.cap.kind!r} не входит в набор форм: "
                             f"{'; '.join(CAPS)}")
        if rule.cap.kind == CAP_NONE:
            if rule.cap.value is not None:
                raise ValueError(f"правило неустойки сделки {deal.uid!r}: у потолка "
                                 f"„без потолка“ не бывает значения")
        elif rule.cap.value is None or rule.cap.value <= 0:
            raise ValueError(f"правило неустойки сделки {deal.uid!r}: у потолка "
                             f"„{rule.cap.kind}“ значение должно быть положительным")
    for rule in deal.allocations:
        if len(rule.order) != len(PARTS) or sorted(rule.order) != sorted(PARTS):
            raise ValueError(f"правило распределения сделки {deal.uid!r}: порядок — "
                             f"перестановка четырёх частей без повторов: "
                             f"{', '.join(PARTS)}")
    seen: set[str] = set()
    for rule in deal.triggers:
        uid = getattr(rule, "uid", None)
        if not uid:
            raise ValueError(f"сделка {deal.uid!r}: триггер-правило без уида — "
                             f"сверять нечем")
        if uid in seen:
            raise ValueError(f"правило триггера сделки {deal.uid!r}: уид {uid!r} "
                             f"уже занят другим правилом триггера")
        seen.add(uid)
        condition = getattr(rule, "condition", None)
        if condition not in TRIGGERS:
            raise ValueError(f"правило триггера сделки {deal.uid!r}: условие "
                             f"{condition!r} не входит в набор условий: "
                             f"{'; '.join(TRIGGERS)}")
        if getattr(rule, "part", None) not in PARTS:
            raise ValueError(f"правило триггера {uid!r}: часть "
                             f"{getattr(rule, 'part', None)!r} не входит в набор "
                             f"частей: {', '.join(PARTS)}")
        if not getattr(rule, "basis", None):
            raise ValueError(f"правило триггера {uid!r}: основание обязательно — "
                             f"по нему записывается, что случилось")
        amount = getattr(rule, "charge_amount", None)
        percent = getattr(rule, "charge_percent", None)
        if _positive(amount) == _positive(percent):
            raise ValueError(f"правило триггера сделки {deal.uid!r}: штраф задаётся "
                             f"ровно одним из — сумма или доля просроченной суммы")
        days = getattr(rule, "threshold_days", None)
        total = getattr(rule, "threshold_amount", None)
        if condition == TRIGGER_OVERDUE_DAYS and not _positive(days):
            raise ValueError(f"правило триггера сделки {deal.uid!r}: у условия "
                             f"„{condition}“ порог должен быть задан и быть "
                             f"положительным")
        if condition == TRIGGER_OVERDUE_SUM and not _positive(total):
            raise ValueError(f"правило триггера сделки {deal.uid!r}: у условия "
                             f"„{condition}“ порог должен быть задан и быть "
                             f"положительным")
    _rule_versions(deal.uid, "неустойки", deal.penalties)
    _rule_versions(deal.uid, "распределения", deal.allocations)
    _rule_versions(deal.uid, "триггера", deal.triggers)


def _rule_versions(deal_uid: str, name: str, rules: Sequence) -> None:
    """Версии правила: датированные идут по возрастанию дат, без даты — одиночная.

    Версия без даты действия действует весь период — она допустима, только
    когда одна: рядом со второй не понять, какая действует, и это ошибка данных,
    а не выбор движка.
    """
    if len(rules) > 1 and any(r.effective is None for r in rules):
        raise ValueError(f"правило {name} сделки {deal_uid!r}: версия без даты "
                         f"действия допустима, только если она одна")
    dated = [r.effective for r in rules if r.effective is not None]
    for left, right in zip(dated, dated[1:]):
        if left >= right:
            raise ValueError(f"правило {name} сделки {deal_uid!r}: даты действий "
                             f"должны возрастать: {left} не раньше {right}")


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
        _validate_allocation_lines(m)
        _declared(m.deal, deals, "движение: сделка")
        _declared(m.wallet, wallets, "движение: финансирующий кошелёк")
        _declared(m.paid_to, counterparties, "движение: кому уплачено")
        _declared(m.channel, counterparties, "движение: канал платежа")
        _declared(m.benefit_for, counterparties, "движение: получатель выгоды")


def _validate_allocation_lines(m: Movement) -> None:
    """Разбивка платежа: положительные строки, объявленные части, сумма сходится.

    Тихой нормализации нет — ни обрезки, ни доводки, ни подстановки части:
    правятся данные зоны, а не движок, и текст называет причину.
    """
    seen: set[str] = set()
    total = Decimal(0)
    for line in m.allocation:
        if line.amount <= 0:
            raise ValueError(f"разбивка платежа: строка части {line.part!r} — "
                             f"сумма должна быть положительной")
        if line.part not in PARTS:
            raise ValueError(f"разбивка платежа: часть {line.part!r} не входит "
                             f"в набор частей: {', '.join(PARTS)}")
        if line.part in seen:
            raise ValueError(f"разбивка платежа: часть {line.part!r} встречается "
                             f"дважды")
        seen.add(line.part)
        total += line.amount
    if seen and total != m.amount:
        raise ValueError(f"разбивка платежа сделки {m.deal!r} от {m.date}: сумма "
                         f"разбивки {total} не равна сумме движения {m.amount}")


def _rule_uids(deal: Deal) -> set[str]:
    """Уиды триггер-правил сделки: правило опознаётся уидом, без него — не правило.

    Форму правила держит зона (её проверяет валидация правил), а здесь нужен
    только уид: по нему факт ссылается на правило, и по нему же опознаётся
    случай — «уид правила, дата срабатывания» (Q35). Правило без уида опознать
    нечем, и это текст ошибки, а не падение.
    """
    uids: set[str] = set()
    for rule in deal.triggers:
        uid = getattr(rule, "uid", None)
        if not uid:
            raise ValueError(f"сделка {deal.uid!r}: триггер-правило без уида — "
                             f"сверять нечем")
        uids.add(uid)
    return uids


def _validate_charges(book: Settlements) -> None:
    """Начисления: уид события, сумма, часть, сделка и случай правила триггера.

    Начислено — не уплачено: запись растёт долг, а денег не двигает — денег у
    неё нет по устройству, их несут `Movement` (по сделке) и `Payment`
    (кошелёк). Части живут у долга «сколько я должен»: у требования и у
    регулярного расхода растить нечего. Уид события обязателен и уникален в
    книге — одно событие, одна запись; случай правила опознаётся тройкой
    «сделка, уид правила, дата срабатывания»: уиды правил уникальны среди
    правил сделки, а не среди правил книги.
    """
    deals = {d.uid: d for d in book.deals}
    seen: set[str] = set()
    cases: set[tuple[str, str, date]] = set()
    for c in book.charges:
        if not c.uid:
            raise ValueError("начисление без уида события — опознать запись нечем")
        if c.amount <= 0:
            raise ValueError(f"начисление {c.uid!r}: сумма должна быть "
                             f"положительной — это рост долга, а не возврат")
        if c.part not in PARTS:
            raise ValueError(f"начисление {c.uid!r}: часть {c.part!r} не входит "
                             f"в набор частей: {', '.join(PARTS)}")
        if c.deal not in deals:
            raise ValueError(f"начисление {c.uid!r}: неизвестная сделка {c.deal!r}; "
                             f"объявлены: {sorted(deals)}")
        deal = deals[c.deal]
        if deal.amount is None:
            raise ValueError(f"начисление {c.uid!r}: у регулярного расхода "
                             f"остатка нет — растить нечего")
        if deal.direction == OWED_TO_ME:
            raise ValueError(f"начисление {c.uid!r}: у требования начисления "
                             f"не поддерживаются — части живут у долга "
                             f"«сколько я должен»")
        if c.uid in seen:
            raise ValueError(f"начисление {c.uid!r}: уид события {c.uid!r} уже "
                             f"записан — одно событие, одна запись")
        seen.add(c.uid)
        if c.trigger is None:
            continue                     # событие мира: правила за ним нет
        if c.trigger not in _rule_uids(deal):
            raise ValueError(f"начисление {c.uid!r}: правило триггера {c.trigger!r} "
                             f"не объявлено среди триггер-правил сделки {c.deal!r}")
        if (c.deal, c.trigger, c.date) in cases:
            raise ValueError(f"начисление {c.uid!r}: случай правила {c.trigger!r} "
                             f"от {c.date} по сделке {c.deal!r} уже записан — "
                             f"одно событие, одна запись")
        cases.add((c.deal, c.trigger, c.date))


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
    отклоняется: иначе оно молча выпало бы из сверки (`_questions` берёт только
    то, у чего `computed` не None), а провалидировать книгу вручную нельзя.
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

def allocate_payment(amount: Decimal, order: Sequence[str],
                     balances: dict[str, Decimal]) -> dict[str, Decimal]:
    """Куда идёт платёж: сколько в каждую часть.

    Части гасятся по `order` в пределах их остатков (`balances` — остатки частей,
    их передаёт вызывающий); части в минус не уходят — остаток части потолок
    доли. Избыток сверх всех частей несёт тело — переплата по сделке: тело может
    уйти в минус. Сумма долей равна `amount` — раскладка не теряет и не выдумывает
    деньги. Очерёдность — про гашение: пополнение (возврат) ей не описывается.
    """
    shares = {part: Decimal(0) for part in order}
    left = amount
    for part in order:
        if left <= 0:
            break
        room = balances.get(part, Decimal(0))
        if room < 0:
            room = Decimal(0)
        take = min(left, room)
        shares[part] += take
        left -= take
    if left > 0:
        shares[BODY] = shares.get(BODY, Decimal(0)) + left
    return shares


def rule_at(rules: Sequence, on: date):
    """Действующая версия правила на дату — последняя с датой действия ≤ `on`.

    Дата действия входит включительно: правило меняется в тот же день, что и
    держатель долга. Версия без даты действует весь период и допустима, только
    когда одна (иначе — ошибка данных, и здесь она тоже текст); правил нет или
    все версии ещё в будущем — `None`: «не задано», а не ноль.
    """
    if not rules:
        return None
    if len(rules) > 1 and any(r.effective is None for r in rules):
        raise ValueError("правило: версия без даты действия допустима, "
                         "только если она одна")
    acting = [r for r in rules if r.effective is not None and r.effective <= on]
    if not acting:
        return rules[0] if rules[0].effective is None else None
    return acting[-1]


def _deal(book: Settlements, uid: str) -> Deal:
    for d in book.deals:
        if d.uid == uid:
            return d
    raise ValueError(f"неизвестная сделка {uid!r}; "
                     f"объявлены: {sorted(d.uid for d in book.deals)}")


def _assignments(book: Settlements, deal_uid: str) -> list[Assignment]:
    return sorted((a for a in book.assignments if a.deal == deal_uid),
                  key=lambda a: a.date)


def _along(deal: Deal, movement: Movement) -> bool:
    """Движение по сделке (в зачёт остатка), а не против неё (возврат)."""
    return ((deal.direction == I_OWE and movement.direction == OUT)
            or (deal.direction == OWED_TO_ME and movement.direction == IN))


def _signed(deal: Deal, movement: Movement) -> Decimal:
    """Движение со знаком для остатка: по сделке — плюс, против неё — минус."""
    return movement.amount if _along(deal, movement) else -movement.amount


def _paid(deal: Deal, movements: Iterable[Movement]) -> Decimal:
    """Сколько уплачено по сделке: по сделке — в остаток, против неё — из остатка."""
    return sum((_signed(deal, m) for m in movements), Decimal(0))


def _charged(book: Settlements, deal_uid: str, on: date) -> Decimal:
    """Сколько начислено фактами по сделке до конца дня `on` включительно.

    Начисление входит со своей даты и раньше неё канон не меняет: запись задним
    числом пересчитывает остаток с даты события, а не с даты записи.
    """
    return sum((c.amount for c in book.charges
                if c.deal == deal_uid and c.date <= on), Decimal(0))


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


def _parts_at(book: Settlements, deal_uid: str, on: date) -> dict[str, Decimal] | None:
    """Раскладка канона по частям на дату — или `None`, если раскладка не известна.

    Тело — версии тела плюс начисления в тело минус платежи в тело; проценты —
    начисленное минус платежи в проценты; неустойка и издержки — свои начисления
    минус свои платежи. Раскладка известна, когда у сделки есть правило
    распределения или у платежей есть явные разбивки; иначе — `None`: пробел,
    а не выдумка (Q12, Q34). Платёж без разбивки и без правила на свою дату
    раскладку делает неопределённой целиком: половинок не бывает.

    Платежи распределяются хронологически по остаткам частей на день платежа
    (`allocate_payment`, правило версии на дату платежа); возврат части не гасит,
    а наполняет — по разбивке, без неё в тело (03-Т5). База процентов — тело
    плюс проценты (правка 2): платежи входят в неё своей долей в тело/проценты,
    начисления в тело — тем же списком приростов, что и дельты передачи
    (04-Т3). Начисление считает та же `accrued_interest`, окно от начала долга.
    Неустойка — своя производная: капает по правилу зоны на потоках просрочки
    (`_penalty_of`), входит в свою часть начисленным и не входит в базу
    процентов; день платежа видит её начисленной до прошлого дня (06-Т3).
    Штрафы триггеров — тоже производные (`trigger_charges`), входят в часть из
    правила с даты срабатывания, как записи-факты, — фактом случай вытесняется,
    двойного входа нет (07-Т5).
    """
    deal = _deal(book, deal_uid)
    if deal.amount is None or deal.direction == OWED_TO_ME:
        return None                # части живут у долга «сколько я должен» (Q23)
    movements = sorted((m for m in book.movements
                        if m.deal == deal_uid and m.date <= on),
                       key=lambda m: m.date)
    if not deal.allocations and not any(m.allocation for m in movements):
        return None                # раскладка не определена — пробел (Q34)
    since = deal.start
    penalty = _penalty_of(book, deal_uid, on)
    charged = {part: Decimal(0) for part in PARTS}
    paid = {part: Decimal(0) for part in PARTS}
    into_base: list[tuple[date, Decimal]] = []   # доли платежей в тело+проценты
    grows = [(a.date, a.delta) for a in book.assignments if a.deal == deal_uid]
    body_before = Decimal(0)       # начисления в тело до начала долга — в базе

    def interest(until: date) -> Decimal:
        """Начисленное к дате: одна функция, база — тело плюс проценты."""
        if since is None or until < since:
            return Decimal(0)      # начисляться нечему: долга ещё нет
        base = (deal_amount_at(book, deal_uid, since)
                - sum((share for when, share in into_base if when < since),
                      Decimal(0))
                + body_before)
        return accrued_interest(deal, base, since, until, into_base, grows)

    for event in sorted([*movements,
                         *(c for c in book.charges if c.deal == deal_uid
                           and c.date <= on),
                         *trigger_charges(book, deal_uid,
                                          _plan_since(deal, on), on)],
                        key=lambda e: e.date):
        if isinstance(event, (Charge, TriggeredCharge)):
            charged[event.part] += event.amount
            if event.part == BODY:
                if since is not None and event.date < since:
                    body_before += event.amount
                else:
                    grows.append((event.date, event.amount))
            continue
        movement = event
        sign = Decimal(1) if _along(deal, movement) else Decimal(-1)
        if movement.allocation:
            shares = {line.part: line.amount for line in movement.allocation}
        elif movement.direction == IN:
            shares = {BODY: movement.amount}    # возврат без разбивки — в тело
        else:
            rule = rule_at(deal.allocations, movement.date)
            if rule is None:
                return None        # платёж без правила — раскладки нет (04-Т5)
            day_before = movement.date - timedelta(days=1)
            balances = {
                BODY: max(deal_amount_at(book, deal_uid, movement.date)
                          + charged[BODY] - paid[BODY], Decimal(0)),
                INTEREST: max(interest(day_before) - paid[INTEREST], Decimal(0)),
                PENALTY: max(charged[PENALTY] + penalty(day_before)
                             - paid[PENALTY], Decimal(0)),
                COSTS: max(charged[COSTS] - paid[COSTS], Decimal(0)),
            }
            shares = allocate_payment(movement.amount, rule.order, balances)
        for part in PARTS:
            paid[part] += sign * shares.get(part, Decimal(0))
        into_base.append((movement.date,
                          sign * (shares.get(BODY, Decimal(0))
                                  + shares.get(INTEREST, Decimal(0)))))

    return {BODY: deal_amount_at(book, deal_uid, on) + charged[BODY] - paid[BODY],
            INTEREST: interest(on) - paid[INTEREST],
            PENALTY: charged[PENALTY] + penalty(on) - paid[PENALTY],
            COSTS: charged[COSTS] - paid[COSTS]}


def deal_parts(book: Settlements, deal_uid: str,
               on: date) -> dict[str, Decimal] | None:
    """Долг на дату по частям: тело, проценты, неустойка, издержки.

    Все четыре ключа всегда, включая нули: кредитор показывает четыре строки, и
    отчёт тем же. Раскладка не известна (нет правила и разбивок) или у сделки
    нет остатка — `None`: пробел, а не ноль; канон при этом отвечает всегда
    (`deal_balance`). Инвариант «канон = сумма частей» держится, когда
    раскладка известна, — по построению, одним путём расчёта.
    """
    return _parts_at(book, deal_uid, on)


def deal_balance(book: Settlements, deal_uid: str, on: date) -> Decimal | None:
    """Долг на дату — канон остатка: сумма частей, а без раскладки — одним числом.

    Одно определение на все пути: по нему идут расчётный остаток, сальдо по
    контрагенту и сверка с выпиской, и он же — состояние проката: прокат
    инициализируется этим каноном на открытие окна, поэтому остаток проката на
    дату окна и расчётный остаток на ту же дату — одно число.

    Когда раскладка известна (правило распределения или разбивки платежей),
    канон — сумма частей `_parts_at`: инвариант «канон = сумма частей» выполняется
    по построению. Без раскладки канон считается прежним путём: тело минус
    движения плюс начисленное, — и даёт те же числа, что раньше: платёж в базе
    процентов идёт целиком (04-Т5).

    Начисление процентов идёт той же функцией `accrued_interest`, что и в
    прокате: от начала долга (`Deal.start`) до конца дня `on`, отрезок внутри
    месяца — пропорционально дням, округление — `model.kopek`. Ставки нет —
    начисления нет; начала долга ещё нет — начисленного тоже нет. У регулярного
    расхода остатка нет — `None`.

    Начисление-факт (`Charge`) входит слагаемым со своей даты: долг растёт от
    случившегося, а не только от договорённости, и раньше своей даты канон не
    меняет. В раскладке начисление в тело растит и базу процентов — со своей
    даты (04-Т3); в безраскладочном пути база прежняя — тело минус движения.
    """
    amount = deal_amount_at(book, deal_uid, on)
    if amount is None:
        return None
    parts = _parts_at(book, deal_uid, on)
    if parts is not None:
        return (parts[BODY] + parts[INTEREST] + parts[PENALTY] + parts[COSTS])
    deal = _deal(book, deal_uid)
    movements = [m for m in book.movements
                 if m.deal == deal_uid and m.date <= on]
    return kopek(amount - _paid(deal, movements)
                 + _accrued(book, deal, movements, on)
                 + _charged(book, deal_uid, on))


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
    """Остаток части «тело» на дату — служебная величина закрытости проката.

    Не канон и не публичная величина: закрытость отвечает на вопрос «есть ли
    что прокатывать», а вхождения гасят тело — начисленное вхождением не
    является, и сделка с съеденным телом не возвращается в прокат, даже если
    проценты за ней ещё числятся (правка 3). Тело здесь — часть из той же
    раскладки (`_parts_at`): версии плюс начисления в тело минус платежи в тело,
    — поэтому начисление в тело меняет и закрытость. Раскладки нет — прежний
    путь «версия минус все движения». «Сколько долгу на эту дату» — другой
    вопрос, на него отвечает канон `deal_balance`, начисленное включающий.
    """
    amount = deal_amount_at(book, deal_uid, on)
    if amount is None:
        return None
    parts = _parts_at(book, deal_uid, on)
    if parts is not None:
        return parts[BODY]
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


def _step_rate(steps: Sequence[PenaltyStep], age: int) -> Decimal | None:
    """Ставка ступени, действующей на возраст просрочки; None — ступени нет.

    Ступени идут по возрастанию порогов (валидация), действует последняя
    с порогом не больше возраста: возраст ровно N — ступень «от N»
    (включительно). Возраст меньше первой ступени не покрывает ни одна —
    за такой день неустойки нет.
    """
    acting: Decimal | None = None
    for step in steps:
        if age < step.days:
            break
        acting = step.rate
    return acting


def accrued_penalty(rule: PenaltyRule, overdues: Sequence[tuple[date, Decimal, Decimal]],
                    since: date, until: date,
                    payments: Sequence[tuple[date, Decimal]] = (),
                    already: Decimal = Decimal(0)) -> Decimal:
    """Начисленная неустойка за отрезок `[since, until]` включительно.

    Одна формула на оба носителя: канон (`_parts_at`) и прокат считают ею же,
    поэтому «долг на дату» и «сколько набежало» сходятся с проекцией по
    построению. Формула — по дням просрочки (Q13): за каждый день база дня —
    остаток потока после платежей этого дня — умножается на ставку ступени,
    действующей на возраст этого потока; возраст дня — число дней со дня после
    срока (первый день неустойки — день после срока, день срока и раньше не
    начисляют). Каждое слагаемое округляется `model.kopek` `HALF_UP` — второго
    пути округления нет.

    `rule` — форма из тикета 02: шкала ступеней и потолок одним носителем.
    `overdues` — потоки просрочек тройками «дата срока — исходная просроченная
    сумма — текущий остаток» (даёт `overdue_amounts`): исходная — база потолка
    «доля просроченной суммы» (Q36), текущий остаток — база дня. Потолок статичен
    и монотонен (Q7, правка 1): «без потолка» не режет, «сумма» и «доля» лишь
    обрезают прирост (день даёт `min(дневное, потолок − накопленное)`) — уже
    набранное погашением просрочки не списывается. Потолок округлён тем же
    `model.kopek` — отдельной функции округления нет.

    `payments` — погашения просрочек отрезка парами «дата — сумма»: гасят
    потоки старыми первыми (Q13), день платежа уменьшает базу до начисления за
    этот день. `already` — набранное до отрезка: потолок считается по
    накопленному сделки, отрезок — его продолжение (прокат зовёт по месяцам).
    Каждое потокодневное слагаемое (остаток потока × ставка дня) округляется
    своей копейкой. Начисление задним числом пересчитывается само: производная
    не хранится и сторно не пишет (R3-Q11).
    """
    if rule is None or until < since or not overdues:
        return Decimal(0)
    streams = sorted(([due, original, remaining]
                      for due, original, remaining in overdues),
                     key=lambda stream: stream[0])
    by_day: dict[date, Decimal] = {}
    for when, amount in payments:
        by_day[when] = by_day.get(when, Decimal(0)) + amount
    if rule.cap.kind == CAP_NONE:
        cap: Decimal | None = None
    elif rule.cap.kind == CAP_SUM:
        cap = kopek(rule.cap.value)
    elif rule.cap.kind == CAP_SHARE:   # доля исходных просроченных сумм (Q36)
        cap = kopek(rule.cap.value * sum((stream[1] for stream in streams),
                                         Decimal(0)))
    else:
        raise ValueError(f"правило неустойки: потолок {rule.cap.kind!r} не входит "
                         f"в набор форм: {'; '.join(CAPS)}")
    total = Decimal(0)
    day = since
    while day <= until:
        left = by_day.get(day, Decimal(0))
        for stream in streams:                     # старые первыми (Q13)
            if left <= 0:
                break
            take = min(left, max(stream[2], Decimal(0)))
            stream[2] -= take
            left -= take
        accrued = Decimal(0)
        for due, _original, remaining in streams:
            if remaining <= 0 or day <= due:
                continue
            rate = _step_rate(rule.steps, (day - due).days)
            if rate:
                accrued += kopek(remaining * rate)
        if cap is not None:
            accrued = min(accrued, max(cap - already - total, Decimal(0)))
        total += accrued
        day += timedelta(days=1)
    return total


def _sans_penalty(book: Settlements, deal_uid: str, on: date) -> Decimal:
    """Остаток долга без производной неустойки — прямой путь канона.

    Обрезка потоков просрочки считается по нему: неустойка растит долг, долг
    обрезает потоки, потоки кормят неустойку — круг производной, который рвётся
    самым внешним слагаемым (обрезка cap-ит поток долгом без самой неустойки).
    Платежи входят в базу процентов целиком (04-Т5): раскладка тут не
    используется, число обрезки не зависит от распределения, — поэтому на
    книгах со ставкой и начислениями в тело оно может отличаться от канона-
    суммы-частей на проценты от начисленного в тело.
    """
    amount = deal_amount_at(book, deal_uid, on)
    if amount is None:
        return Decimal(0)
    deal = _deal(book, deal_uid)
    movements = [m for m in book.movements if m.deal == deal_uid and m.date <= on]
    return kopek(amount - _paid(deal, movements)
                 + _accrued(book, deal, movements, on)
                 + _charged(book, deal_uid, on))


def _plan_since(deal: Deal, on: date) -> date:
    """С какого времени искать вхождения сделки: начала долга и ряда, иначе прошлость."""
    rule = deal.schedule
    marks = [d for d in (deal.start, rule.start if rule else None,
                         rule.first.date if rule and rule.first else None) if d]
    return min(marks) if marks else date(on.year - 20, on.month, 1)


def _penalty_bounds(book: Settlements, deal: Deal, until: date) -> list[date]:
    """Дни, на которых меняются потоки просрочки: рождения, платежи, правки, версии.

    Между соседними днями потоки постоянны, и отрезок считает одна формула.
    Рождение потока — день после срока (там поток появляется); платежи и правки
    меняют остатки и сроки; версии правил — ставку; передачи долга — долг, а с
    ним и обрезку потоков (единственный немонотонный меняетель долга без
    движения). Сюда входит и сам `until`.
    """
    days = {until}
    for occ in occurrences(book, deal.uid, _plan_since(deal, until), until):
        if occ.due is not None and occ.due < until:
            days.add(occ.due + timedelta(days=1))
    for m in book.movements:
        if m.deal == deal.uid and m.date <= until:
            days.add(m.date)
    for a in book.assignments:
        if a.deal == deal.uid and a.date <= until:
            days.add(a.date)
    for edit in book.edits:
        if edit.deal != deal.uid:
            continue
        if edit.planned <= until:
            days.add(edit.planned)
        if edit.moved_to is not None and edit.moved_to <= until:
            days.add(edit.moved_to)
    for version in deal.penalties:
        if version.effective is not None and version.effective <= until:
            days.add(version.effective)
    return sorted(days)


def _penalty_of(book: Settlements, deal_uid: str,
                until: date) -> Callable[[date], Decimal]:
    """Считальщик неустойки: отвечает, сколько начислено к концу дня `day ≤ until`.

    Сегменты между сменами потоков считает `accrued_penalty` (одна формула);
    сегмент, внутри которого обрезка меняет потоки (долг растёт процентами и
    отрезает меньше), проходится по дням. Контрольные суммы сегментов
    запоминаются, поэтому вопрос «сколько на дату» не пересчитывает историю
    целиком заново. Закрытая функция канона: прокат ведёт свою жизнь по тем же
    формулам, но со своим состоянием.
    """
    deal = _deal(book, deal_uid)
    if (not deal.penalties or deal.amount is None
            or deal.direction == OWED_TO_ME):
        return lambda day: Decimal(0)

    bounds = _penalty_bounds(book, deal, until)
    # (начало, конец, правило, потоки на начало, накопленное по дням | None,
    #  накопленное к концу сегмента)
    runs: list[tuple[date, date, PenaltyRule, tuple, dict[date, Decimal] | None,
                     Decimal]] = []
    total = Decimal(0)
    # Последняя граница — сам `until`: за ним виртуальный день, чтобы его день
    # тоже попал в сегмент (последний день неустойки — дата расчёта включительно).
    following = bounds[1:] + [until + timedelta(days=1)]
    for a, nxt in zip(bounds, following):
        b = min(nxt - timedelta(days=1), until)
        rule = rule_at(deal.penalties, a)
        if rule is None:
            continue
        streams = overdue_amounts(book, deal_uid, a)
        if not streams:
            continue
        streams_end = overdue_amounts(book, deal_uid, b)
        if streams == streams_end:             # потоки постоянны — одна формула
            total += accrued_penalty(rule, streams, a, b, already=total)
            runs.append((a, b, rule, streams, None, total))
        else:                                  # обрезка режет внутри — по дням
            daily: dict[date, Decimal] = {}
            day = a
            while day <= b:
                total += accrued_penalty(rule,
                                         overdue_amounts(book, deal_uid, day),
                                         day, day, already=total)
                daily[day] = total
                day += timedelta(days=1)
            runs.append((a, b, rule, streams, daily, total))

    def accrued_to(day: date) -> Decimal:
        if day >= until:
            return total
        answer = Decimal(0)
        for a, b, rule, streams, daily, end_cum in runs:
            if day < a:
                break
            if day >= b:
                answer = end_cum
                continue
            if daily is not None:              # накопленное по дням посчитано
                answer = daily[day]
            else:
                answer = answer + accrued_penalty(rule, streams, a, day,
                                                  already=answer)
            break
        return answer

    return accrued_to


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


def _loose_closings(book: Settlements, deal: Deal,
                    found: list[Occurrence]) -> dict[date, tuple[Decimal, date]]:
    """Гашение просрочек несвязанными движениями: FIFO по сроку.

    Расход по сделке (без ссылки на вхождение) гасит самые старые исполняемые
    вхождения, чей срок уже прошёл: `due` раньше даты движения — вхождение в день
    срока ещё не просрочено (опоздание — это факт после срока). Пропущенные и
    отложенные без даты не гасятся — платить туда некому и некуда; наступившие и
    будущие — тем более. Сумма закрытий в пределах движения: излишек сверх
    просрочек — платёж, а не закрытие графика. Возврат вхождения не закрывает.
    """
    loose = [m for m in book.movements
             if m.deal == deal.uid and m.occurrence is None and _along(deal, m)]
    if not loose:
        return {}
    paid: dict[date, Decimal] = {}
    when: dict[date, date] = {}
    for m in sorted(loose, key=lambda m: m.date):
        left = m.amount
        for occ in sorted(found, key=lambda o: o.due or o.planned):
            if left <= 0:
                break
            if (occ.due is None or occ.due >= m.date or occ.amount is None
                    or occ.status == SKIPPED):
                continue
            done = paid.get(occ.planned, Decimal(0))
            remaining = occ.amount - occ.paid - done
            if remaining <= 0:
                continue
            take = min(left, remaining)
            paid[occ.planned] = done + take
            when[occ.planned] = m.date
            left -= take
    return {planned: (done, when[planned]) for planned, done in paid.items()}


def _occurrence(book: Settlements, deal: Deal, planned: date,
                amount: Decimal | None,
                edits: dict[tuple[str, date], OccurrenceEdit],
                extra: tuple[Decimal, date] | None = None) -> Occurrence:
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
    if extra is not None:
        extra_paid, extra_when = extra
        paid += extra_paid
        if extra_when is not None and (actual is None or extra_when > actual):
            actual = extra_when                   # закрыл не связанный, а FIFO
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
    правки и движения — и возвращает список. Связанные движения (`occurrence`)
    закрывают своё вхождение; несвязанный расход гасит самые старые просроченные
    — FIFO по сроку (`_loose_closings`), наступившие и будущие не трогает.
    """
    deal = _deal(book, deal_uid)
    rule = deal.schedule
    if rule is None:
        return []
    edits = _merged_edits(book)
    entries: list[tuple[date, Decimal | None]] = []
    # Доход сдвигается назад, платёж вперёд: выходной переезжает по стороне денег.
    backward = deal.direction == OWED_TO_ME

    if rule.first is not None:
        first = rule.first.date
        when = planned_date(first.year, first.month, first.day, rule.shift_weekend,
                            backward=backward)
        if since <= when <= until:
            entries.append((when, rule.first.amount))

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
                entries.append((when, rule.payment))

    entries.sort(key=lambda entry: entry[0])
    for left, right in zip(entries, entries[1:]):
        if left[0] == right[0]:
            raise ValueError(f"сделка {deal_uid!r}: два вхождения на {left[0]} — "
                             f"правьте правило графика")
    found = [_occurrence(book, deal, planned, amount, edits)
             for planned, amount in entries]
    closings = _loose_closings(book, deal, found)
    if closings:
        found = [_occurrence(book, deal, planned, amount, edits,
                             closings.get(planned))
                 for planned, amount in entries]
    return found


# --- просроченная сумма -----------------------------------------------------

def _facts_to(book: Settlements, deal_uid: str, on: date) -> Settlements:
    """Книга на фактах сделки до конца дня `on`: состояние на дату — пересчёт.

    Производная не помнит прошлого — она считается заново на фактах до даты
    вопроса (derived-balances); правки, начисления и передачи передаются как
    есть: их даты сами решают, входят ли они.
    """
    return Settlements(counterparties=book.counterparties, wallets=book.wallets,
                       deals=book.deals,
                       movements=[m for m in book.movements
                                  if m.deal == deal_uid and m.date <= on],
                       charges=book.charges, assignments=book.assignments,
                       edits=book.edits, observed=book.observed)


def overdue_amounts(book: Settlements, deal_uid: str,
                    on: date) -> tuple[tuple[date, Decimal, Decimal], ...]:
    """Потоки просрочек сделки на дату: «дата срока — исходная сумма — остаток».

    Просроченная сумма — состояние, а не хранимое: остатки неисполненных
    вхождений, чей срок уже прошёл (`due` раньше `on` — в день срока платёж ещё
    не просрочен), не больше остатка долга. Дата потока — дата срока (`due`):
    возраст просрочки считает потребитель по правилу «первый день — день после
    срока». Исходная сумма — остаток вхождения на момент начала просрочки,
    обрезанный по остатку долга на ту же дату: одна база для доли штрафа и
    потолка (Q36); текущий остаток — база дня начисления (Q13). Потоки идут
    старыми первыми; две просрочки одной даты живут обе — суммирование корректно,
    а начисление считает по потокам независимо.

    Входят «ожидается» и «перенесён» (у перенесённого срок — перенесённая дата);
    не входят исполненные (остатка нет), пропущенные (правка сняла обязательство)
    и отложенные без даты (платить некуда). У регулярного расхода и требования
    просрочек нет — части живут у долга «сколько я должен» (Q23); пустой ответ —
    «просрочки нет», а не «не оценено».

    Остатки считаются на фактах до `on` включительно: движение задним числом
    пересчитывает состояние на любой дате, сторно не нужно. Сумма потоков
    ограничена долгом **без производной неустойки** (`_sans_penalty`):
    неустойка растит долг, долг обрезает потоки, потоки кормят неустойку —
    круг производной, и обрезка считается по долгу без неё самой. Когда платёж
    вне графика съел долг, обрезаются самые новые потоки — старые (с бо́льшим
    возрастом) сохраняются, как требует хронология (Q13).
    """
    deal = _deal(book, deal_uid)
    if deal.amount is None or deal.direction == OWED_TO_ME:
        return ()
    since = _plan_since(deal, on)
    # Состояние на дату — пересчёт на фактах до `on`: производная не помнит
    # прошлого, она считается заново (derived-balances).
    past = _facts_to(book, deal_uid, on)
    streams: list[tuple[date, Decimal, Decimal]] = []
    for occ in occurrences(past, deal_uid, since, on):
        if occ.status not in (EXPECTED, POSTPONED) or occ.amount is None:
            continue
        due = occ.due
        if due is None or due >= on:
            continue
        remaining = occ.remaining or Decimal(0)
        if remaining <= 0:
            continue
        paid_by_due = Decimal(0)
        for m in book.movements:
            if m.deal == deal_uid and m.occurrence == occ.planned and m.date <= due:
                paid_by_due += m.amount if _along(deal, m) else -m.amount
        original = max(occ.amount - paid_by_due, Decimal(0))
        debt = _sans_penalty(book, deal_uid, due + timedelta(days=1))
        if original > debt:
            original = max(debt, Decimal(0))
        streams.append((due, original, remaining))
    streams.sort(key=lambda stream: stream[0])
    available = _sans_penalty(book, deal_uid, on)
    kept: list[tuple[date, Decimal, Decimal]] = []
    for due, original, remaining in streams:
        if available <= 0:
            break
        take = remaining if remaining <= available else available
        kept.append((due, original, take))
        available -= take
    return tuple(kept)


def overdue_amount(book: Settlements, deal_uid: str, on: date) -> Decimal:
    """Просроченная сумма сделки на дату: сумма потоков после ограничения.

    Ровно `min(сумма просрочек, канон)`: потоки `overdue_amounts` уже обрезаны,
    чтобы неустойка и триггеры считали по тем же числам, что и состояние.
    """
    return sum((stream[2] for stream in overdue_amounts(book, deal_uid, on)),
               Decimal(0))


# --- триггеры «не уплатил в срок» -------------------------------------------

def _linked_paid(book: Settlements, deal: Deal, occ: Occurrence,
                 by: date) -> Decimal:
    """Сколько уплачено по вхождению к концу дня `by`: связанные движения."""
    total = Decimal(0)
    for m in book.movements:
        if m.deal == deal.uid and m.occurrence == occ.planned and m.date <= by:
            total += m.amount if _along(deal, m) else -m.amount
    return total


def _original_overdue(book: Settlements, deal: Deal, occ: Occurrence) -> Decimal:
    """Исходная просроченная сумма вхождения (Q36): остаток на начало просрочки.

    Остаток вхождения на день после срока, обрезанный по остатку долга на ту же
    дату (долг без производных — `_sans_penalty`, там рвётся круг производной).
    Одна база для доли штрафа и порога суммы.
    """
    original = max(occ.amount - _linked_paid(book, deal, occ, occ.due),
                   Decimal(0))
    debt = _sans_penalty(book, deal.uid, occ.due + timedelta(days=1))
    return min(original, max(debt, Decimal(0)))


def _fires(book: Settlements, deal: Deal, occ: Occurrence, rule: TriggerRule,
           deal_uid: str) -> date | None:
    """Дата срабатывания правила на вхождении — или None, если не сработало.

    Случай — вхождение (R1-Q4: просроченным становится конкретный платёж);
    дата срабатывания — когда условие стало истинным (R5-Q29): «срок прошёл» —
    дата срока, порог по дням — дата срока плюс порог (включительно), порог по
    сумме — первый день потока (день после срока). Условие оценивается строго
    по фактам до даты срабатывания (07-Т4): платёж задним числом откатывает
    случай, платёж после него — нет, сторно не нужно (R3-Q11). Гашения видны
    все: связанные движения и FIFO несвязанных (их несёт `Occurrence.paid` при
    пересчёте на фактах до даты), долг — обрезкой 05.
    """
    due = occ.due
    if due is None:
        return None
    if rule.condition == TRIGGER_OVERDUE:
        if _linked_paid(book, deal, occ, due) >= occ.amount:
            return None               # в свой срок уплачено — случая нет
        # «есть просроченная сумма»: долг на дату срока жив — поток родился
        # не нулевым (обрезка 05 cap-ит его долгом).
        if _sans_penalty(book, deal_uid, due) <= 0:
            return None
        return due
    if rule.condition == TRIGGER_FULL_UNPAID:
        if _linked_paid(book, deal, occ, due) > 0:
            return None               # частичная уплата — не «полная неуплата»
        return due
    if rule.condition == TRIGGER_OVERDUE_DAYS:
        firing = due + timedelta(days=rule.threshold_days)
        # Состояние вхождения на дату порога — пересчёт на фактах до неё:
        # погашено (связанно или FIFO) до порога — условия не было.
        past = _facts_to(book, deal_uid, firing)
        states = {o.planned: o for o in occurrences(
            past, deal_uid, _plan_since(deal, firing), firing)}
        state = states.get(occ.planned)
        if state is None or (state.remaining or Decimal(0)) <= 0:
            return None
        if _sans_penalty(book, deal_uid, firing) <= 0:
            return None               # долг съеден — потока нет (обрезка 05)
        return firing
    # TRIGGER_OVERDUE_SUM: остаток потока (Q36 — исходная просроченная сумма)
    # достиг порога; остаток после срока не растёт, первый день истинности —
    # день после срока.
    if _original_overdue(book, deal, occ) < rule.threshold_amount:
        return None
    return due + timedelta(days=1)


def trigger_charges(book: Settlements, deal_uid: str, since: date,
                    until: date) -> tuple[TriggeredCharge, ...]:
    """Случаи триггеров сделки за отрезок `[since, until]` — производные штрафы.

    Правила зоны (`Deal.triggers`), применённые движком к фактам книги (R1-Q3,
    R3-Q11): каждое вхождение оценивается каждым правилом, чья версия действует
    на дату срабатывания (`rule_at`); идемпотентно по построению — повторный
    прогон даёт те же случаи, хранимых начислений нет. Штраф — фиксированная
    сумма (`charge_amount`) или доля исходной просроченной суммы (Q36;
    `charge_percent`), округлённая `model.kopek` `HALF_UP`.

    Запись-факт с тем же опознанием — уид правила и дата срабатывания
    (`Charge.trigger`, Q35) — вытесняет случай: производное не считается.
    Задвоение фактов с одним ключом падает валидацией книги; правка `basis`
    правила связь не ломает — опознание несёт уид, а не текст. Случай опознаётся
    парой «уид, дата», поэтому два вхождения одной даты срабатывания дают один
    штраф (первого сработавшего), а не два.
    """
    deal = _deal(book, deal_uid)
    if not deal.triggers or deal.amount is None or deal.direction == OWED_TO_ME:
        return ()
    recorded = {(c.trigger, c.date) for c in book.charges
                if c.deal == deal_uid and c.trigger is not None}
    cases: list[TriggeredCharge] = []
    derived: set[tuple[str, date]] = set()
    for occ in occurrences(book, deal_uid, _plan_since(deal, until), until):
        if occ.amount is None or occ.status == SKIPPED:
            continue                  # сумма не определена или платить не будут
        for rule in deal.triggers:
            firing = _fires(book, deal, occ, rule, deal_uid)
            if firing is None or not since <= firing <= until:
                continue
            if rule_at(deal.triggers, firing) is not rule:
                continue              # на эту дату действует другая версия
            key = (rule.uid, firing)
            if key in recorded or key in derived:
                continue              # случай записан фактом или уже увиден
            derived.add(key)
            if rule.charge_amount is not None:
                amount = kopek(rule.charge_amount)
            else:
                amount = kopek(rule.charge_percent
                               * _original_overdue(book, deal, occ))
            cases.append(TriggeredCharge(rule.uid, firing, amount, rule.part,
                                         rule.basis))
    cases.sort(key=lambda case: (case.date, case.trigger))
    return tuple(cases)
