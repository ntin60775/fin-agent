#!/usr/bin/env python3
"""Проверки перед публикацией в открытый репозиторий.

Правила и определение «личного» — docs/ops/публикация.md, пункт 1.
Запуск:
    python3 .githooks/publication-check.py --all            # вся история (перед публикацией)
    python3 .githooks/publication-check.py --range A..B      # диапазон коммитов
    .githooks/pre-push                                       # автоматически при git push

Блокирует (код возврата 1):
  * шаблоны личного из .forbidden-names.txt (имена кредиторов, реальные суммы) —
    файл в .gitignore: список шаблонов сам был бы личными данными;
  * суммы со знаком валюты (₽, руб) — их в зоне быть не должно;
  * абсолютные пути /home/<пользователь>/.

Показывает «на глаза» (не блокирует): суммы без знака валюты, добавленные в
docs/ и inbox/. Шаблоном они не отличаются от синтетических примеров движка —
эту часть проверяет человек, как и написано в публикация.md.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DOC = "docs/ops/публикация.md"
LOCAL = os.path.join(ROOT, ".forbidden-names.txt")
KB_PREFIXES = ("docs/", "inbox/")

CURRENCY = re.compile(r"\d\s?(?:₽|руб\b|руб\.)")
HOME_PATH = re.compile(r"/home/[A-Za-z0-9._-]+(?:/[^\s`)\]»\"']*)?")
AMOUNT = re.compile(
    r"(?<![\d,.])(?:[−-]?\d{1,3}(?: \d{3})+(?:,\d{2})?|[−-]?\d{1,3},\d{2}|−\d+)(?![\d])"
)
WORD = r"0-9A-Za-zА-Яа-яЁё"


def git(*args, text=True):
    return subprocess.run(["git", *args], capture_output=True, text=text)


def read_patterns(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return [ln.strip() for ln in handle if ln.strip() and not ln.startswith("#")]


def load_patterns():
    """Шаблоны личного — только из локального файла (он не публикуется)."""
    local = read_patterns(LOCAL)
    if not os.path.exists(LOCAL):
        print(f"ВНИМАНИЕ: {os.path.relpath(LOCAL, ROOT)} нет — проверка имён и сумм "
              f"не работает. Заведи файл (одна строка = шаблон) — правила в {DOC} п. 1.")
    return [(item, re.compile(rf"(?<![{WORD}]){re.escape(item)}(?![{WORD}])", re.I))
            for item in local]


def scan(text, patterns):
    return [item for item, rx in patterns if rx.search(text)]


def objects(rev_args):
    """Блобы достижимых коммитов: [(путь, текст)] — по одному на блоб."""
    seen, out = set(), []
    for line in git("rev-list", "--objects", *rev_args).stdout.splitlines():
        sha, _, path = line.partition(" ")
        if not path or sha in seen:
            continue
        seen.add(sha)
        res = git("cat-file", "blob", sha, text=False)
        if res.returncode:
            continue
        out.append((path, res.stdout))
    return out


def messages(rev_args):
    out = git("log", "--no-walk=unsorted", "--format=%H%x00%B%x00", *rev_args).stdout
    chunks = [c for c in out.split("\x00") if c.strip()]
    return [(chunks[i][:8], chunks[i + 1]) for i in range(0, len(chunks) - 1, 2)]


def added_amounts(rev_args):
    """Суммы без валюты, добавленные этим набором коммитов в docs/ и inbox/."""
    out = git("log", "-p", "--format=%h %s", *rev_args, "--", *KB_PREFIXES).stdout
    subject, hits = "", []
    for line in out.splitlines():
        if not line.startswith(("+", "-", "@", "diff", "index", "---", "+++")):
            subject = line.strip()
        elif line.startswith("+") and not line.startswith("+++"):
            for found in AMOUNT.findall(line[1:]):
                hits.append((subject, found, line[1:].strip()[:110]))
    return hits


def check(rev_args, label):
    patterns = load_patterns()
    problems = []
    for path, raw in objects(rev_args):
        text = raw.decode("utf-8", "replace")
        for item in scan(text, patterns):
            problems.append((path, f"шаблон личного «{item}»"))
        if CURRENCY.search(text):
            problems.append((path, "сумма со знаком валюты (₽/руб)"))
        if HOME_PATH.search(text):
            problems.append((path, "абсолютный путь /home/…"))
    for commit, body in messages(rev_args):
        for item in scan(body, patterns):
            problems.append((f"коммит {commit}", f"шаблон личного «{item}»"))
        if CURRENCY.search(body):
            problems.append((f"коммит {commit}", "сумма со знаком валюты (₽/руб)"))
    eyes = added_amounts(rev_args)

    print(f"Публикация · проверки ({DOC} п. 1) · область: {label}")
    if problems:
        print(f"\nБЛОКИРУЮЩЕЕ ({len(problems)}):")
        for where, what in problems:
            print(f"  {where}: {what}")
    if eyes:
        print(f"\nНА ГЛАЗА — {len(eyes)} новых сумм без знака валюты (шаблоном не отличить от синтетики):")
        for subject, found, line in eyes[:15]:
            print(f"  {subject}: {found} ← {line}")
        if len(eyes) > 15:
            print(f"  … ещё {len(eyes) - 15}")
    if problems:
        print(f"\npush остановлен. Правила — {DOC}; свои шаблоны — .forbidden-names.txt")
        return 1
    print("\nчисто: личных данных в этой области нет.")
    return 0


def pre_push():
    status = 0
    for line in sys.stdin:
        parts = line.split()
        if len(parts) != 4:
            continue
        local_ref, local_sha, _remote_ref, remote_sha = parts
        if set(local_sha) == {"0"}:
            continue
        if set(remote_sha) == {"0"}:
            rev_args = [local_sha, "--not", "--remotes"]
            label = f"новая ветка {local_ref} ({local_sha[:8]})"
        else:
            rev_args = [f"{remote_sha}..{local_sha}"]
            label = f"{remote_sha[:8]}..{local_sha[:8]} ({local_ref})"
        status |= check(rev_args, label)
    return status


def main():
    args = sys.argv[1:]
    if args[:1] == ["--all"]:
        return check(["--branches", "--remotes", "--tags"], "вся история")
    if args[:1] == ["--range"] and len(args) > 1:
        return check([args[1]], args[1])
    if args[:1] == ["--pre-push"] or not args:
        return pre_push()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
