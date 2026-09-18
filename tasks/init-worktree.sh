#!/usr/bin/env bash
# init-worktree.sh — подготовить ворктри fin-agent к работе.
#
# Зачем: ворктри не наследует `.omp/plugins/` — каталог в .gitignore, поэтому в свежем
# дереве нет ни плагина `ontoship`, ни его навыков и команд (ADR plugin-delivery §4).
# Заодно собирается индекс KB: без него `gitmark search` (команда `/kb`) падает с
# «Индекс не найден — запусти gitmark index».
#
# Использование (из основного дерева или из ворктри):
#   tasks/init-worktree.sh /path/to/worktree
#   tasks/init-worktree.sh            # текущий каталог
#
# Идемпотентен: повторный запуск ничего не ломает.
set -euo pipefail

TARGET="${1:-.}"
TARGET="$(cd "$TARGET" && pwd)"

if ! git -C "$TARGET" rev-parse --git-dir >/dev/null 2>&1; then
	echo "не git-репозиторий: $TARGET" >&2
	exit 2
fi

PLUGIN="ontoship@sot-omp-marketplace"
PKG="$TARGET/.omp/plugins/node_modules/ontoship"
ENGINE="$PKG/skills/kb-search/gitmark.py"

echo "ворктри: $TARGET"

# 1. Плагины проекта: ворктри их не наследует (ADR plugin-delivery §4).
if [ -e "$PKG" ]; then
	(cd "$TARGET" && omp plugin upgrade "$PLUGIN" --scope=project)
else
	(cd "$TARGET" && omp plugin install "$PLUGIN" --scope=project)
fi

# 2. Индекс KB ворктри: движок берём из только что поставленного плагина.
if [ ! -f "$ENGINE" ]; then
	echo "движок KB не найден после установки: $ENGINE" >&2
	exit 2
fi
(cd "$TARGET" && python3 "$ENGINE" index)

echo "✓ ворктри готов: $TARGET"
