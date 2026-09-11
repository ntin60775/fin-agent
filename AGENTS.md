# Ядро расчёта личных финансов — точка входа

Это **архитектурная зона**: движок расчёта (`finance_core`) и всё, что нужно для
его разработки. Финансовые данные сюда не попадают — они живут в отдельной
приватной зоне-потребителе.

> **Движок обязан оставаться чистым.** Ни одного личного числа, ни одного имени
> кредитора, ни одного названия банка — ни в коде, ни в тестах, ни в документах,
> ни в примерах докстрингов. Всё личное — в зоне-потребителе.

## Где что лежит

```
.omp/
  skills/    kb-search (движок поиска), kb-curate, dev-flow, grilling,
             domain-modeling, mp-* (гейт-пайплайн)
  commands/  /kb /kb-map /doc /onto-doc /grill /grilling /architecture
             /code-review /to-tickets /handoff /prototype /ship
  rules/     kb-source-of-truth, kb-first, ship-gate, acceptance-rounds
finance_core/  движок: касса (solver) и долги (debt) + README
tests/         синтетические тесты движка — без личных данных
docs/          база знаний (это KB): reference, decisions, plans
CONTEXT.md     доменные термины
AGENTS.md      этот файл — читается первым
```

## С чего начать

- **Устройство движка** → [finance_core/README.md](finance_core/README.md)
- **База знаний** → [docs/README.md](docs/README.md)
- **Модель знаний** (типы, свойства, ссылки) → [docs/ontology.md](docs/ontology.md)
- **Термины** → [CONTEXT.md](CONTEXT.md)
- **Решения** → [docs/decisions/](docs/decisions/)

## Принцип

Markdown + git — источник правды. Производное — индекс `.gitmark/index.db` и
HTML-карта — регенерируется из md и в git не коммитится. `README.md` каждой папки
— её индекс; документ без ссылок (сирота) считается ошибкой.

## Проверка

```bash
python3 -m pytest tests/                          # синтетические тесты движка
python3 .omp/skills/kb-search/gitmark.py lint     # инварианты KB (I1–I7)
python3 .omp/skills/kb-search/gitmark.py index    # индекс поиска
```

## Потребитель

Финансовая зона подключает движок как библиотеку и держит свои сценарии с
реальными цифрами. Изменение движка — это цикл разработки: замысел → контракт
плана → тикеты → код → синтетические тесты → ревью → сценарные тесты потребителя.
Порядок — навык `dev-flow`, правила — `.omp/rules/ship-gate.md`.
