#!/usr/bin/env bash
# Отключить автоматическое патчирование, сохранив сам патч и резервные копии.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_NAME="snowautopatch.service"

# Просим watcher'а завершиться: он выйдет на следующей проверке (до 10 секунд).
mkdir -p "$HERE/state"
printf 'stop\n' > "$HERE/state/stop.request"

# Определяем, в каком режиме установлен сервис: системном или пользовательском.
if systemctl --user status "$UNIT_NAME" >/dev/null 2>&1; then
    systemctl --user disable --now "$UNIT_NAME" 2>/dev/null || true
elif systemctl status "$UNIT_NAME" >/dev/null 2>&1; then
    systemctl disable --now "$UNIT_NAME" 2>/dev/null || true
fi

printf 'Автоматическое патчирование отключено. Патч и резервные копии сохранены.\n'
printf 'Чтобы включить обратно: sudo systemctl enable --now %s (или systemctl --user ...).\n' "$UNIT_NAME"
