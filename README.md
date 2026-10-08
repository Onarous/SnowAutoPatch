# SnowAutoPatch

Автоматический патч SnowVPN для сторонних подписок. Убирает ограничение
на ссылки SnowVPN и добавляет преобразование поддерживаемых Clash, VLESS,
Xray JSON и Hysteria2/Salamander в формат встроенного ядра.

## Установка одной командой

Вставьте в обычную **командную строку Windows (`cmd.exe`)**:

```cmd
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/Onarous/SnowAutoPatch/main/install.ps1 | iex"
```

Нужны Windows 10/11 x64, интернет и установленный SnowVPN. Python, Node.js
и Git заранее устанавливать не требуется: установщик скачивает отдельные
среды выполнения с официальных сайтов и проверяет SHA-256 архивов.
Системные Python/Node.js и PATH не меняются. Администратор для стандартной
установки в профиле пользователя не требуется.

По умолчанию клиент находится в `%LOCALAPPDATA%\Programs\snowvpn-next`,
а автопатчер — в `%LOCALAPPDATA%\SnowAutoPatch`. После установки задача
Windows **SnowVPN AutoPatch** запускается сразу и при каждом входе в Windows.
Codex для её работы не нужен. Повтор той же команды обновляет автопатчер,
сохраняя настройки и резервные копии.

Если SnowVPN уже открыт, выполните **«Выход» через трей и запустите снова**.
Патчер не завершает клиент и не прерывает VPN-соединение.

## Portable и другой путь

В PowerShell укажите одну или несколько папок клиента:

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/Onarous/SnowAutoPatch/main/install.ps1))) -Target 'D:\Apps\SnowVPN'
```

Для нескольких копий используйте `-Target 'D:\Apps\SnowVPN', "$env:LOCALAPPDATA\Programs\snowvpn-next"`.
Изменить список позже можно в `%LOCALAPPDATA%\SnowAutoPatch\config.json`:
он перечитывается при каждой проверке. Пути должны вести к папке клиента,
содержащей `resources\app`, либо непосредственно к `resources\app`.

## После обновления клиента

Проверка выполняется каждые 10 секунд. После двух наблюдений одинаковых
файлов патч применяется вновь — обычно через 10–20 секунд после завершения
записи обновления. Новая версия клиента сохраняется: патчер изменяет
проверенные участки исходного кода, а не подменяет весь файл старой копией.

Если обновление уже запустило приложение до патча, нужен выход через трей
и повторный запуск. Поддерживаются распакованные Electron-ресурсы
`resources\app`. Незнакомая структура кода, новый протокол или переход на
`app.asar` требуют обновления рецепта: ошибка записывается в `autopatch.log`.
Совместимость со всеми будущими версиями клиента не гарантируется.

## Проверка, отключение и откат

Команды ниже выполняются в PowerShell.

Проверить совместимость без изменения клиента:

```powershell
& "$env:LOCALAPPDATA\SnowAutoPatch\runtime\python\python.exe" -X utf8 "$env:LOCALAPPDATA\SnowAutoPatch\autopatch.py" --check
```

Применить вручную: `%LOCALAPPDATA%\SnowAutoPatch\Patch-Now.cmd`.

Отключить фоновую задачу, сохранив патч и резервные копии:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\SnowAutoPatch\Disable-AutoPatch.ps1"
```

Перед откатом отключите задачу и выйдите из SnowVPN через трей:

```powershell
& "$env:LOCALAPPDATA\SnowAutoPatch\runtime\python\python.exe" -X utf8 "$env:LOCALAPPDATA\SnowAutoPatch\autopatch.py" --restore --target "$env:LOCALAPPDATA\Programs\snowvpn-next"
```

Оригиналы сохраняются в `backups` перед каждым изменением. Откат проверяет
контрольные суммы и отказывается перезаписывать более позднее обновление.
При записи патча используются временные файлы и атомарная замена;
обнаруженное параллельное обновление отменяет операцию.

Не удаляйте папку автопатчера, пока задача включена. Программа работает
с обычными правами пользователя. Для клиента в защищённой папке вроде
`Program Files` потребуются соответствующие права и отдельная настройка задачи.

## Разработка

В репозитории только автопатчер, преобразователь форматов и синтетические
тесты. Клиент SnowVPN, профили, подписки, пароли, бинарные среды выполнения
и пользовательские резервные копии не публикуются.

```powershell
python -m unittest discover -s tests -v
npm ci
node --test tests/subscription.test.js
```

Python-тесты требуют Node.js в PATH или установленного частного runtime;
для JavaScript-тестов нужен Node.js 18 или новее.
Для проверки установщика на отдельной копии используйте
`-SourceDirectory`, `-InstallDir`, `-Target` и `-CheckOnly`; последний параметр
не патчит клиент и не регистрирует задачу.

Проект независим от SnowVPN и не связан с его разработчиками.
