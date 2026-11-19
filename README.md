# SnowAutoPatch

Автоматический патч SnowVPN для сторонних подписок. Добавляет преобразование
поддерживаемых Clash, VLESS, Xray JSON и Hysteria2/Salamander в формат встроенного
ядра (mihomo) и снимает привязку к личному кабинету SnowVPN.

Поддерживаются **Windows 10/11 x64** и **Linux x86_64**.

## Windows

Вставьте в обычную **командную строку Windows (`cmd.exe`)**:

```cmd
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/Onarous/SnowAutoPatch/main/install.ps1 | iex"
```

Нужны Windows 10/11 x64, интернет и установленный SnowVPN. Python, Node.js
и Git заранее устанавливать не требуется: установщик скачивает отдельные
среды выполнения с официальных сайтов и проверяет SHA-256 архивов.

## Linux

Нужен **Linux x86_64**, Python 3.8+ и установленный клиент SnowVPN.
Запуск одной командой:

```bash
curl -fsSL https://raw.githubusercontent.com/Onarous/SnowAutoPatch/main/install.sh | bash
```

Установщик сам определит режим:

| Ситуация | Режим | Что происходит |
|---|---|---|
| Клиент в `/opt/SnowVPN` (файлы под root) | системный | Нужен `sudo`: ставится в `/usr/local/lib/snowautopatch`, сервис — в `/etc/systemd/system` |
| Клиент в каталоге пользователя (AppImage, сборка в `~/`) | пользовательский | Root не нужен: ставится в `~/.local/share/snowautopatch`, сервис — `systemd --user` |

Для системной установки:

```bash
curl -fsSL https://raw.githubusercontent.com/Onarous/SnowAutoPatch/main/install.sh | sudo bash
```

> Пока Linux-версия не слита в `main`, замените `main` на имя ветки с
> установщиком — иначе скачается страница «404: Not Found»:
>
> ```bash
> curl -fsSL https://raw.githubusercontent.com/Onarous/SnowAutoPatch/feature/linux-support/install.sh | sudo bash
> ```

Node.js устанавливать не нужно: для проверки синтаксиса используется
`node` из системы, а если его нет — сам Electron клиента (в нём тот же Node,
запускаемый в режиме Node). Если нет и его, установщик скачает приватный
runtime и проверит его SHA-256.

### Указать другой путь к клиенту

```bash
./install.sh --target ~/Applications/SnowVPN
```

Несколько копий клиента:

```bash
./install.sh --target ~/Apps/SnowVPN --target /opt/SnowVPN
```

Список целей позже меняется в конфиге:

* системная установка — `/etc/snowautopatch/config.json`
* пользовательская — `~/.config/snowautopatch/config.json`

Пути должны вести в корень клиента (где лежит `resources/app`) либо в сам
`resources/app`. Конфиг перечитывается при каждой проверке.

### Если клиент запущен

Закройте SnowVPN и запустите заново. Патчер не завершает клиент и не
прерывает VPN-соединение.

## После обновления клиента

Проверка выполняется каждые 10 секунд. После двух наблюдений одинаковых
файлов патч применяется заново — обычно через 10–20 секунд после завершения
записи обновления.

На Linux клиент ставится пакетом (`apt`/`deb`/`rpm`), и обновление перезаписывает
`/opt/SnowVPN` целиком, поэтому патч после такого обновления слетает —
watcher возвращает его автоматически. Новая версия клиента сохраняется:
патчер изменяет проверенные участки исходного кода, а не подменяет весь файл
старой копией.

Если обновление уже запустило приложение до патча, нужен перезапуск клиента.
Поддерживаются распакованные Electron-ресурсы `resources/app`. Незнакомая
структура кода, новый протокол или переход на `app.asar` требуют обновления
рецепта: ошибка записывается в `autopatch.log`. Совместимость со всеми
будущими версиями клиента не гарантируется.

## Проверка, отключение и откат

### Linux

Проверить совместимость без изменения клиента:

```bash
./install.sh --check-only --target /opt/SnowVPN
```

Применить вручную:

```bash
~/.local/share/snowautopatch/Patch-Now.sh
```

Отключить автоматическое патчирование (патч и резервные копии сохраняются):

```bash
~/.local/share/snowautopatch/Disable-AutoPatch.sh
```

Откат (сначала отключите watcher и закройте клиент):

```bash
~/.local/share/snowautopatch/Patch-Now.sh --restore --target /opt/SnowVPN
```

Журнал watcher'а:

```bash
journalctl --user -u snowautopatch.service -f
```

Для системной установки — `journalctl -u snowautopatch.service -f`.

### Windows

Проверить совместимость без изменения клиента:

```powershell
& "$env:LOCALAPPDATA\SnowAutoPatch\runtime\python\python.exe" -X utf8 "$env:LOCALAPPDATA\SnowAutoPatch\autopatch.py" --check
```

Применить вручную: `%LOCALAPPDATA%\SnowAutoPatch\Patch-Now.cmd`.

Отключить фоновую задачу, сохранив патч и резервные копии:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\SnowAutoPatch\Disable-AutoPatch.ps1"
```

Откат:

```powershell
& "$env:LOCALAPPDATA\SnowAutoPatch\runtime\python\python.exe" -X utf8 "$env:LOCALAPPDATA\SnowAutoPatch\autopatch.py" --restore --target "$env:LOCALAPPDATA\Programs\snowvpn-next"
```

## Общее

Оригиналы сохраняются в `backups` перед каждым изменением. Откат проверяет
контрольные суммы и отказывается перезаписывать более позднее обновление.
При записи патча используются временные файлы и атомарная замена;
обнаруженное параллельное обновление отменяет операцию.

## Разработка

В репозитории только автопатчер, преобразователь форматов и синтетические
тесты. Клиент SnowVPN, профили, подписки, пароли, бинарные среды выполнения
и пользовательские резервные копии не публикуются.

```bash
python -m unittest discover -s tests -v
npm ci
npm test
```

Python-тесты требуют Node.js в PATH или установленного приватного runtime;
для JavaScript-тестов нужен Node.js 18 или новее.

Рецепты патча для обеих платформ лежат в `patch_recipes.py`: маркеры в коде
вендора, по которым определяется платформа и вносятся правки. Каждый маркер
обязан встречаться ровно один раз, иначе это чужая версия клиента — патч
прервётся с ошибкой, не изменив ни одного файла.

Для проверки установщика на отдельной копии используйте `--source-directory`,
`--target` и `--check-only`; последний параметр не патчит клиент и не
регистрирует сервис.

## Отличия Linux-клиента от Windows

Рецепты патча различаются, потому что различается сам клиент:

| | Windows | Linux |
|---|---|---|
| Ограничение «только подписки SnowVPN» | Есть (`isOurSubUrl`) | Отсутствует — снимать нечего |
| Ядро | Встроенное | mihomo, конфиг обязателен в Clash-YAML |
| `node_modules` с js-yaml | Есть в комплекте | Отсутствует — js-yaml приезжает с патчем |
| Парсер VLESS-ссылок | Есть в коде клиента | Отсутствует — приезжает с патчем |
| Пользовательский агент подписки | Через `EXTRA_NODES_UA` | `SUB_UA`, переопределяется обёрткой |

## Лицензии

`payload/js-yaml.js` — это [js-yaml](https://github.com/nodeca/js-yaml)
(лицензия MIT, текст в `payload/js-yaml.LICENSE`). Остальной код — MIT.

Проект независим от SnowVPN и не связан с его разработчиками.
