#!/usr/bin/env bash
# Установщик SnowAutoPatch для Linux.
#
# Ставит автопатчер и watcher, который перепатчивает клиент после его
# обновления. На Linux клиент ставится пакетом (apt/deb/rpm), и обновление
# перезаписывает /opt/SnowVPN целиком — патч после такого обновления слетает,
# поэтому watcher и нужен.
#
# Права: ресурсы системного клиента (/opt/SnowVPN) принадлежат root, значит
# патчить их может только root. Если клиент в каталоге пользователя (AppImage,
# сборка в ~/), root не нужен — всё ставится в профиль через systemd --user.
set -euo pipefail

SOURCE_URL="https://codeload.github.com/Onarous/SnowAutoPatch/zip/refs/heads/main"
PYTHON_MIN_MINOR=8
UNIT_NAME="snowautopatch.service"

TARGETS=()
SOURCE_DIR=""
PREFIX=""
MODE=""
CHECK_ONLY=0

usage() {
    cat <<'EOF'
Использование: install.sh [опции]

  --target PATH          Каталог клиента SnowVPN (корень установки, где лежит
                         resources/app, либо сам resources/app). Можно указать
                         несколько раз. По умолчанию ищется /opt/SnowVPN.
  --source-directory DIR  Взять исходники отсюда, а не качать из репозитория.
  --prefix DIR           Куда установить инструменты. По умолчанию зависит от режима.
  --user                 Установить в профиль пользователя (systemd --user, без root).
  --system               Установить системно (systemd system, нужен root).
  --check-only           Только проверка совместимости: файлы клиента не меняются,
                         сервис не ставится.
  -h, --help             Эта справка.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --target) TARGETS+=("${2:?}"); shift 2 ;;
        --source-directory) SOURCE_DIR="${2:?}"; shift 2 ;;
        --prefix) PREFIX="${2:?}"; shift 2 ;;
        --user) MODE="user"; shift ;;
        --system) MODE="system"; shift ;;
        --check-only) CHECK_ONLY=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Неизвестный аргумент: %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

say() { printf '%s\n' "$*"; }
fail() { printf 'Ошибка: %s\n' "$*" >&2; exit 1; }

command -v python3 >/dev/null 2>&1 || fail "Нужен python3 в системе."
command -v tar >/dev/null 2>&1 || fail "Нужен tar."

download() {
    local url="$1" dest="$2"
    if command -v curl >/dev/null 2>&1; then
        # -f: ошибка на HTTP 4xx/5xx, чтобы страница «404: Not Found» не
        # попала в файл молча.
        if ! curl -fsSL --retry 3 -o "$dest" "$url"; then
            fail "Не удалось скачать: $url
Если это main, а установщика там нет — укажите ветку с ним:
  curl -fsSL https://raw.githubusercontent.com/Onarous/SnowAutoPatch/<ВЕТКА>/install.sh | bash"
        fi
    else
        wget -q -O "$dest" "$url" || fail "Не удалось скачать: $url"
    fi
}

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    else
        shasum -a 256 "$1" | cut -d' ' -f1
    fi
}

# ------------------------------------------------------------------ цели

if [ ${#TARGETS[@]} -eq 0 ]; then
    for candidate in /opt/SnowVPN "$HOME/Applications/SnowVPN" "$HOME/opt/SnowVPN"; do
        [ -f "$candidate/resources/app/package.json" ] && TARGETS+=("$candidate")
    done
    [ ${#TARGETS[@]} -gt 0 ] || fail "Клиент SnowVPN не найден. Укажите путь через --target."
fi

# Каталог ресурсов клиента (там main.js и renderer/).
APP_DIRS=()
for target in "${TARGETS[@]}"; do
    if [ -f "$target/package.json" ] && [ -f "$target/main.js" ]; then
        APP_DIRS+=("$target")
    elif [ -f "$target/resources/app/package.json" ]; then
        APP_DIRS+=("$target/resources/app")
    else
        fail "Не найдены ресурсы клиента в: $target"
    fi
done

# Нужен ли root: ресурсы клиента принадлежат root?
NEEDS_ROOT=0
for app in "${APP_DIRS[@]}"; do
    [ -w "$app" ] || NEEDS_ROOT=1
done

if [ -z "$MODE" ]; then
    if [ "$NEEDS_ROOT" -eq 1 ]; then MODE="system"; else MODE="user"; fi
fi

if [ "$MODE" = "system" ]; then
    [ "$(id -u)" -eq 0 ] || fail "Системная установка требует прав root: запустите через sudo, либо используйте --user."
    PREFIX="${PREFIX:-/usr/local/lib/snowautopatch}"
    CONFIG_FILE="/etc/snowautopatch/config.json"
    UNIT_DIR="/etc/systemd/system"
else
    PREFIX="${PREFIX:-${XDG_DATA_HOME:-$HOME/.local/share}/snowautopatch}"
    CONFIG_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/snowautopatch/config.json"
    UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
fi
UNIT_PATH="$UNIT_DIR/$UNIT_NAME"

say "Режим: $MODE"
say "Инструменты: $PREFIX"
say "Конфигурация: $CONFIG_FILE"

# ------------------------------------------------------------------ исходники

STAGING="$(mktemp -d -t snowautopatch-XXXXXX)"
trap 'rm -rf "$STAGING"' EXIT

if [ -n "$SOURCE_DIR" ]; then
    SOURCE="$(cd "$SOURCE_DIR" && pwd)"
else
    say "Скачивание SnowAutoPatch..."
    download "$SOURCE_URL" "$STAGING/source.zip"
    # codeload отдаёт ZIP, а не tar.gz. Распаковываем правильным инструментом;
    # иначе получаем мусор вместо исходников.
    mkdir -p "$STAGING/source"
    if command -v unzip >/dev/null 2>&1; then
        unzip -q "$STAGING/source.zip" -d "$STAGING/source" || fail "Не удалось распаковать архив."
        # Внутри zip лежит каталог «SnowAutoPatch-<ветка>» — поднимаем его содержимое.
        inner="$(find "$STAGING/source" -mindepth 1 -maxdepth 1 -type d | head -1)"
        [ -n "$inner" ] && SOURCE="$inner" || SOURCE="$STAGING/source"
    else
        command -v bsdtar >/dev/null 2>&1 || fail "Нужен unzip или bsdtar для распаковки исходников."
        bsdtar -xf "$STAGING/source.zip" -C "$STAGING/source" || fail "Не удалось распаковать архив."
        inner="$(find "$STAGING/source" -mindepth 1 -maxdepth 1 -type d | head -1)"
        [ -n "$inner" ] && SOURCE="$inner" || SOURCE="$STAGING/source"
    fi
fi
[ -f "$SOURCE/autopatch.py" ] || fail "В исходниках нет autopatch.py (плохой архив? Проверьте ветку с установщиком)."

# ------------------------------------------------------------------ интерпретаторы

python_minor="$(python3 -c 'import sys; print(sys.version_info[1])' 2>/dev/null || echo 0)"
if [ "$python_minor" -lt "$PYTHON_MIN_MINOR" ]; then
    fail "Нужен python3 версии 3.$PYTHON_MIN_MINOR или новее (сейчас 3.$python_minor)."
fi
PYTHON="$(command -v python3)"
say "Python: $PYTHON"

# Node нужен только для проверки синтаксиса JS. Порядок: системный node →
# Electron самого клиента (внутри тот же Node, запускается в режиме Node) →
# приватный runtime из runtimes.json.
find_client_node() {
    for app in "${APP_DIRS[@]}"; do
        client="$(dirname "$(dirname "$app")")/snowvpn"
        [ -x "$client" ] && { printf '%s' "$client"; return 0; }
    done
    return 1
}

NODE=""
if command -v node >/dev/null 2>&1; then
    NODE="$(command -v node)"
    say "Node: $NODE (системный)"
elif CLIENT_NODE="$(find_client_node)"; then
    NODE="$CLIENT_NODE"
    say "Node: $NODE (Electron клиента)"
else
    say "Node.js не найден — скачивается приватный runtime."
    spec="$(python3 - "$SOURCE/runtimes.json" <<'PY'
import json, sys
specs = json.load(open(sys.argv[1], encoding="utf-8"))
node = (specs.get("linux") or {}).get("node") or specs.get("node")
if not node:
    sys.exit("В runtimes.json нет Linux-спецификации node.")
print(node["url"], node["sha256"])
PY
)"
    url="${spec%% *}"
    sha="${spec##* }"
    download "$url" "$STAGING/node.tar.gz"
    actual="$(sha256_of "$STAGING/node.tar.gz")"
    [ "$actual" = "$sha" ] || fail "Контрольная сумма Node.js не совпала: $actual (скачалось не то?)"
    tar -xzf "$STAGING/node.tar.gz" -C "$STAGING"
    NODE="$(find "$STAGING" -type f -name node -path '*/bin/node' | head -1)"
    [ -n "$NODE" ] || fail "В архиве Node.js не найден bin/node."
fi

# ------------------------------------------------------------------ проверка (--check-only)

if [ "$CHECK_ONLY" -eq 1 ]; then
    say "Проверка совместимости (файлы клиента не меняются)..."
    for app in "${APP_DIRS[@]}"; do
        "$PYTHON" -X utf8 "$SOURCE/autopatch.py" --check --target "$app" || fail "Проверка не пройдена."
    done
    say "Проверка пройдена. Ничего не изменено, сервис не установлен."
    exit 0
fi

# ------------------------------------------------------------------ установка

say "Установка файлов..."
mkdir -p "$PREFIX/payload" "$PREFIX/state" "$PREFIX/backups"
install -m 0644 "$SOURCE/autopatch.py" "$PREFIX/autopatch.py"
install -m 0644 "$SOURCE/patch_recipes.py" "$PREFIX/patch_recipes.py"
install -m 0644 "$SOURCE/runtimes.json" "$PREFIX/runtimes.json"
for file in subscriptionFormat.js fetch-wrapper.js fetch-wrapper-linux.js vless.js js-yaml.js js-yaml.LICENSE; do
    [ -f "$SOURCE/payload/$file" ] && install -m 0644 "$SOURCE/payload/$file" "$PREFIX/payload/$file"
done

# Конфигурация с целями. Существующую сохраняем, если цели явно не заданы.
if [ ${#TARGETS[@]} -gt 0 ] || [ ! -f "$CONFIG_FILE" ]; then
    mkdir -p "$(dirname "$CONFIG_FILE")"
    printf '%s\n' "${TARGETS[@]}" | python3 -c '
import json, sys
paths = [line.strip() for line in sys.stdin if line.strip()]
print(json.dumps({"targets": paths}, ensure_ascii=False, indent=2))
' > "$CONFIG_FILE"
fi

# Копия конфига рядом с инструментами: чтобы ручной запуск autopatch.py из
# этого каталога видел те же цели. Приоритет у /etc/snowautopatch/config.json.
install -m 0644 "$CONFIG_FILE" "$PREFIX/config.json"

# ------------------------------------------------------------------ systemd

stop_watcher() {
    if [ "$MODE" = "system" ]; then
        systemctl stop "$UNIT_NAME" 2>/dev/null || true
    else
        systemctl --user stop "$UNIT_NAME" 2>/dev/null || true
    fi
}
stop_watcher

mkdir -p "$UNIT_DIR"
cat > "$UNIT_PATH" <<EOF
[Unit]
Description=SnowVPN AutoPatch (перепатчивание клиента после обновлений)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$PREFIX
ExecStart=$PYTHON -X utf8 $PREFIX/autopatch.py --watch
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF

# Применяем патч сразу — до запуска watcher'а.
for app in "${APP_DIRS[@]}"; do
    "$PYTHON" -X utf8 "$PREFIX/autopatch.py" --target "$app" || fail "Не удалось применить патч к $app."
done

if [ "$MODE" = "system" ]; then
    systemctl daemon-reload
    systemctl enable --now "$UNIT_NAME"
    systemctl --no-pager --lines=0 status "$UNIT_NAME" || true
    JOURNAL_HINT="journalctl -u $UNIT_NAME"
else
    systemctl --user daemon-reload
    systemctl --user enable --now "$UNIT_NAME"
    systemctl --user --no-pager --lines=0 status "$UNIT_NAME" || true
    # Чтобы сервис жил после выхода из сессии, нужен lingering.
    command -v loginctl >/dev/null 2>&1 && loginctl enable-linger "$(id -un)" 2>/dev/null || true
    JOURNAL_HINT="journalctl --user -u $UNIT_NAME"
fi

say ""
say "Установлено. Инструменты и резервные копии: $PREFIX"
say "Журнал: $JOURNAL_HINT"
say "Если SnowVPN уже запущен — закройте его и запустите заново."
