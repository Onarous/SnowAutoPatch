#!/usr/bin/env bash
# Применить патч к клиенту прямо сейчас.
# Использование: ./Patch-Now.sh [опции autopatch.py]
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -x "$HERE/runtime/python/bin/python3" ]; then
    PYTHON="$HERE/runtime/python/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="$(command -v python3)"
else
    printf 'Ошибка: не найден python3.\n' >&2
    exit 1
fi

exec "$PYTHON" -X utf8 "$HERE/autopatch.py" "$@"
