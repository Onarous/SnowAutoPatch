"""Рецепты патча клиента SnowVPN.

Два независимых рецепта — для Windows-клиента (Electron, resources\\app) и для
Linux-клиента (/opt/SnowVPN, resources/app). Каждый работает по СВОИМ маркерам
в исходном коде вендора и падает с UnsupportedVersion, если структура не та.

Общие принципы (одинаковые для обеих платформ):
  * патчим только проверенные участки исходника, а не подменяем файл целиком —
    иначе автообновление клиента ломает патч, и наоборот;
  * каждый маркер обязан встречаться РОВНО один раз, иначе это чужая версия;
  * после правки синтаксис проверяется Node.js, и только потом файлы пишутся.
"""
from __future__ import annotations

import re


class UnsupportedVersion(RuntimeError):
    pass


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise UnsupportedVersion("Expected one source marker: " + old[:70])
    return text.replace(old, new, 1)


def section(text, start, end, replacement):
    if text.count(start) != 1 or text.count(end) != 1:
        raise UnsupportedVersion("Unknown source section: " + start)
    a = text.index(start)
    b = text.index(end, a)
    return text[:a] + replacement + text[b:]


# ---------------------------------------------------------------------------
# Windows: клиент snowvpn-next на Electron (resources\\app)
# ---------------------------------------------------------------------------

WIN_FILES = ("main.js", "renderer/renderer.js", "renderer/index.html")
WIN_MODULE = "subscriptionFormat.js"


def _win_transform_main(text, wrapper):
    original_gate = 'if(!settings.devUnlocked&&!isOurSubUrl(url)){await refreshSubHosts();if(!isOurSubUrl(url)){const e=new Error("Это ссылка не от SnowVPN. Клиент работает только с подписками SnowVPN.");e.needSub=true;throw e}}'
    new_gate = 'if(!isSubUrl(url))throw new Error("Нужна корректная HTTP(S)-ссылка на подписку Clash.");'
    if "function isOurSubUrl(" in text:
        text = section(text, "const SUB_HOSTS_FALLBACK=", "function normalizeSubUrl(",
                       'function isSubUrl(u){try{const p=new URL(String(u));return["http:","https:"].includes(p.protocol)&&!!p.hostname}catch(_){return false}}')
        text = replace_once(text, original_gate, new_gate)
        text = replace_once(text, "startSubAutoUpdate();refreshSubHosts();", "startSubAutoUpdate();")
        text = replace_once(text, "error:e.message,needSub:!!e.needSub", "error:e.message")
        text = replace_once(text, "только если это ваш личный кабинет SnowVPN", "только если это ваша подписка у доверенного провайдера")
    elif "function isSubUrl(" not in text or new_gate not in text:
        raise UnsupportedVersion("Subscription URL validation is unfamiliar")
    if "function fetchSubRaw(url,uaOverride){" not in text:
        if 'require("./subscriptionFormat")' in text:
            raise UnsupportedVersion("Incomplete subscription-format patch")
        text = replace_once(text, 'const path=require("path");', 'const{normalizeSubscriptionText}=require("./subscriptionFormat");const path=require("path");')
        text = replace_once(text, "function fetchSub(url,uaOverride){", "function fetchSubRaw(url,uaOverride){")
        text = replace_once(text, "const EXTRA_NODES_UA=", wrapper + "const EXTRA_NODES_UA=")
    else:
        if 'require("./subscriptionFormat")' not in text:
            raise UnsupportedVersion("Raw fetch exists without format module")
        text = section(text, "async function fetchSub(url,uaOverride){", "const EXTRA_NODES_UA=", wrapper)
    if any(marker in text for marker in ("isOurSubUrl", "refreshSubHosts", "SUB_HOSTS_", "needSub")):
        raise UnsupportedVersion("Unrecognized provider restriction remains")
    return text


def _win_transform_renderer(text):
    branch = 'else if(r&&r.needSub){$("buySubModal").style.display="flex"}'
    if "buySubModal" in text:
        text = replace_once(text, branch, "")
        text = section(text, "var BOT_SUB_URL=", 'if($("mpAdd"))$("mpAdd").onclick=', "")
    if "buySubModal" in text or "needSub" in text:
        raise UnsupportedVersion("Unrecognized renderer restriction remains")
    return text


def _win_transform_html(text):
    if 'id="buySubModal"' in text:
        text = section(text, '  <div class="modal" id="buySubModal">', '  <div class="modal" id="profModal">', "")
    text = text.replace('placeholder="Вставьте ссылку на подписку (clash)"', 'placeholder="Ссылка на подписку Clash любого провайдера"')
    text = text.replace("актуальный список серверов от нас", "актуальный список серверов от вашего провайдера")
    if "только с нашими подписками" in text:
        raise UnsupportedVersion("Unrecognized exclusivity notice remains")
    return text


# ---------------------------------------------------------------------------
# Linux: клиент snowvpn-next (Electron), установленный как пакет в /opt/SnowVPN
# ---------------------------------------------------------------------------
#
# Отличия Linux-клиента от Windows (проверено на версии 2.8.37):
#   * НЕТ ограничения «только подписки SnowVPN» — гейта isOurSubUrl/refreshSubHosts
#     в коде просто нет, importSub принимает любой URL. Снимать нечего.
#   * ядро — mihomo, конфиг должен быть в Clash-YAML.
#   * в resources/app НЕТ node_modules, поэтому js-yaml приезжает вместе с патчем.
#   * fetchSub(url) — одна функция без аргумента uaOverride; User-Agent шлётся
#     напрямую через req.setHeader('User-Agent', SUB_UA).
#
# Задача патча на Linux: (1) подставить конвертер форматов в fetchSub, чтобы
# сторонние VLESS/Xray/Hysteria2 ссылки приводились к Clash-YAML, (2) снять
# привязку к личному кабинету SnowVPN в диалоге deep-link, (3) убрать рекламный
# блок «Купить подписку» — на Linux он ведёт на витрину без покупки.

LINUX_FILES = ("main.js", "renderer/renderer.js", "renderer/index.html")
LINUX_MODULE = "subscriptionFormat.js"

# Маркеры Linux-клиента. Каждый встречается в вендорском коде ровно один раз.
_LINUX_FETCH_SUB = "function fetchSub(url) {"
_LINUX_USER_AGENT = "req.setHeader('User-Agent', SUB_UA);"
_LINUX_JSYAML_REQUIRE = "const path = require('path');"
_LINUX_LOOKS_DUMMY = "function looksDummy(text) {"
_LINUX_DEEPLINK_TEXT = "добавляйте только если это ваш личный кабинет SnowVPN."
_LINUX_BUY_HTML = ('<div class="preset"><div class="pi" style="background:linear-gradient(135deg,#ffd166,#ff9e4d)">'
                   '<i class="ti ti-shopping-cart"></i></div><div class="pt"><div class="a">Купить подписку</div>'
                   '<div class="b">snowservice · быстрые серверы</div></div>'
                   '<button class="pb" id="buyBtn" style="background:linear-gradient(135deg,#ffd166,#ff9e4d)">Открыть</button></div>')
_LINUX_BUY_JS = "$('buyBtn').onclick = function () { if (api.openExternal) api.openExternal('https://snowservice.github.io/snowvpn/'); };"
_LINUX_SUB_PLACEHOLDER = 'placeholder="Вставьте ссылку на подписку (clash)"'
_LINUX_SUB_PLACEHOLDER_NEW = 'placeholder="Ссылка на подписку Clash любого провайдера"'
_LINUX_SERVERS_TEXT = "актуальный список серверов от нас"
_LINUX_SERVERS_TEXT_NEW = "актуальный список серверов от вашего провайдера"
_LINUX_HOST_ANCHOR = "function hostName(url) { try { return new URL(url).hostname; } catch (_) { return 'subscription'; } }"


def _linux_transform_main(text, wrapper):
    """Подставляет конвертер форматов в fetchSub.

    Идея: исходная fetchSub(url) остаётся как fetchSubRaw(url, uaOverride) — вся
    её работа (заголовки, таймаут, HWID-заголовки, сборка userinfo) сохраняется.
    Поверх неё кладётся обёртка, которая пробует разобрать ответ как Clash-YAML,
    а если это не Clash — перезапрашивает подписку с User-Agent Clash-клиента
    (многие панели отдают по нему YAML вместо base64-списка ссылок).
    """
    patched = "fetchSubRaw" in text
    if not patched and _LINUX_FETCH_SUB not in text:
        raise UnsupportedVersion("Subscription fetch function not found")
    if not patched:
        # Первый патч: переименовываем оригинал и добавляем модуль конвертера.
        text = replace_once(text, _LINUX_JSYAML_REQUIRE,
                            'const{normalizeSubscriptionText}=require("./subscriptionFormat");' + _LINUX_JSYAML_REQUIRE)
        text = replace_once(text, _LINUX_FETCH_SUB, "function fetchSubRaw(url, uaOverride) {")
        text = replace_once(text, _LINUX_USER_AGENT,
                            "if (uaOverride) req.setHeader('User-Agent', uaOverride);\n    else req.setHeader('User-Agent', SUB_UA);")
        # Обёртка вставляется сразу после функции fetchSubRaw: ищем её конец по
        # устойчивому якорю — следующей функции hostName.
        anchor = _LINUX_HOST_ANCHOR
        if text.count(anchor) != 1:
            raise UnsupportedVersion("Subscription fetch boundary not found")
        text = replace_once(text, anchor, wrapper + "\n" + anchor)
    else:
        # Уже пропатчено — обновляем только содержимое обёртки (новый рецепт).
        if 'require("./subscriptionFormat")' not in text:
            raise UnsupportedVersion("Raw fetch exists without format module")
        # Обёртка — ровно одна функция fetchSub(url) между fetchSubRaw и hostName.
        start = text.find("async function fetchSub(url){")
        end = text.find(_LINUX_HOST_ANCHOR)
        if start < 0 or end < 0 or end < start:
            raise UnsupportedVersion("Existing subscription wrapper not found")
        text = text[:start] + wrapper + "\n" + text[end:]
    if "normalizeSubscriptionText" not in text:
        raise UnsupportedVersion("Subscription format module is not wired")
    return text


def _linux_transform_renderer(text):
    if _LINUX_BUY_JS in text:
        text = replace_once(text, _LINUX_BUY_JS, "")
    if "buyBtn" in text:
        raise UnsupportedVersion("Unrecognized purchase button handler remains")
    return text


def _linux_transform_html(text):
    if _LINUX_BUY_HTML in text:
        text = replace_once(text, _LINUX_BUY_HTML, "")
    text = text.replace(_LINUX_SUB_PLACEHOLDER, _LINUX_SUB_PLACEHOLDER_NEW)
    text = text.replace(_LINUX_SERVERS_TEXT, _LINUX_SERVERS_TEXT_NEW)
    if "buyBtn" in text:
        raise UnsupportedVersion("Unrecognized purchase block remains")
    return text


def _linux_transform_deeplink(text):
    """Снимает привязку к личному кабинету SnowVPN в диалоге deep-link."""
    if _LINUX_DEEPLINK_TEXT in text:
        text = replace_once(text, _LINUX_DEEPLINK_TEXT,
                            "добавляйте только если это ваша подписка у доверенного провайдера.")
    return text


PLATFORMS = {
    "windows": {
        "files": WIN_FILES,
        "module": WIN_MODULE,
        # В Windows-клиенте js-yaml лежит в его собственном node_modules, а
        # парсер VLESS есть в его коде. Поэтому достаточно одного модуля.
        "extra_payload": (),
        "main": _win_transform_main,
        "renderer": _win_transform_renderer,
        "html": _win_transform_html,
    },
    "linux": {
        "files": LINUX_FILES,
        "module": LINUX_MODULE,
        # В Linux-клиенте node_modules НЕТ вообще, поэтому js-yaml и разбор
        # VLESS-ссылок приезжают вместе с патчем.
        "extra_payload": ("vless.js", "js-yaml.js"),
        "main": _linux_transform_main,
        "renderer": _linux_transform_renderer,
        "html": _linux_transform_html,
        "main_extra": _linux_transform_deeplink,
    },
}


def detect_platform(text_main):
    """Определяет платформу клиента по его исходному коду.

    Возвращает 'windows' или 'linux'. Порядок проверки важен: Windows-маркеры
    (isOurSubUrl, SUB_HOSTS_FALLBACK) уникальны и не пересекаются с Linux.

    Учитываем и УЖЕ пропатченный клиент: после патча вендорские маркеры
    Windows исчезают, и опознать клиент можно только по следам самого патча
    (require("./subscriptionFormat") + fetchSubRaw). Поэтому сначала смотрим
    на Windows-следы патча, потом на Windows-маркеры вендора, и только потом
    на Linux.
    """
    windows_vendor = ("function isOurSubUrl(" in text_main or "SUB_HOSTS_FALLBACK" in text_main
                      or "EXTRA_NODES_UA" in text_main)
    linux_vendor = ("function fetchSub(url) {" in text_main or "SUB_UA" in text_main
                    or "looksDummy" in text_main)
    if windows_vendor:
        return "windows"
    if linux_vendor:
        return "linux"
    # Оба набора маркеров сняты — клиент уже пропатчен. Платформа определяется
    # по следам патча: в Windows остаётся fetchSubRaw(url,uaOverride) с
    # fetchSub(url,uaOverride), в Linux — fetchSubRaw(url, uaOverride) и
    # единственный fetchSub(url).
    if "fetchSubRaw(url,uaOverride)" in text_main or "EXTRA_NODES_UA" in text_main:
        return "windows"
    if "fetchSubRaw(url, uaOverride)" in text_main or "function hostName(url)" in text_main:
        return "linux"
    raise UnsupportedVersion("Unrecognized SnowVPN client layout")


def transform(platform, main_text, renderer_text, html_text, wrapper):
    """Применяет рецепт платформы к трём файлам клиента."""
    recipe = PLATFORMS[platform]
    result = {
        "main.js": recipe["main"](main_text, wrapper),
        "renderer/renderer.js": recipe["renderer"](renderer_text),
        "renderer/index.html": recipe["html"](html_text),
    }
    if "main_extra" in recipe:
        result["main.js"] = recipe["main_extra"](result["main.js"])
    return result


def payload_for(platform):
    """Список файлов payload, которые нужно положить в каталог клиента.

    Основной модуль конвертера нужен обеим платформам. Linux-клиенту
    дополнительно нужны js-yaml (у него нет node_modules) и разбор VLESS-ссылок
    (в его коде парсера нет).
    """
    recipe = PLATFORMS[platform]
    return (recipe["module"],) + tuple(recipe.get("extra_payload", ()))
