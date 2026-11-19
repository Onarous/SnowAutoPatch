"""SnowVPN resource patcher. No whole-file replacement of vendor code.

Works on Windows (Electron client under %LOCALAPPDATA%\\Programs) and on Linux
(packaged client under /opt/SnowVPN). The patch recipe differs per platform —
see patch_recipes.py — but the workflow around it is shared: prepare, verify
syntax, back up, then write atomically.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from contextlib import contextmanager

import patch_recipes
from patch_recipes import UnsupportedVersion

BASE = Path(__file__).resolve().parent
STATE = BASE / "state"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
IS_LINUX = os.name != "nt"

# Где искать config.json с целями. На Linux системная установка кладёт его в
# /etc/snowautopatch, пользовательская — в ~/.config/snowautopatch, а
# копия в самом каталоге инструментов тоже поддерживается (удобно при
# разработке и при сборке из исходников).
CONFIG_CANDIDATES = (
    Path("/etc/snowautopatch/config.json"),
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "snowautopatch/config.json",
    BASE / "config.json",
)


def config_path():
    """Первый существующий config.json или None."""
    for candidate in CONFIG_CANDIDATES:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def load_config():
    """Читает config.json. Возвращает список целей (пустой, если конфига нет)."""
    path = config_path()
    if path is None:
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return []
    targets = data.get("targets")
    return list(targets) if isinstance(targets, list) else []

# Файлы клиента и модуль-конвертер. Списки одинаковые для обеих платформ,
# но рецепты патча — разные.
FILES = patch_recipes.WIN_FILES
MODULE = patch_recipes.WIN_MODULE
PAYLOAD_FILES = ("subscriptionFormat.js", "fetch-wrapper.js", "vless.js", "js-yaml.js")

# Каталоги, где может лежать клиент на Linux. Первый существующий — цель по умолчанию.
LINUX_DEFAULT_TARGETS = ("/opt/SnowVPN",)
WINDOWS_DEFAULT_TARGET = "%LOCALAPPDATA%\\Programs\\snowvpn-next"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".snowpatch-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def resolve_app(target):
    """Находит каталог ресурсов клиента и проверяет, что это SnowVPN.

    Windows: <target>/resources/app либо сам <target> (если это уже resources/app).
    Linux:   /opt/SnowVPN/resources/app — та же схема, но встречается ещё
             вариант с app.asar (Electron-builder по умолчанию), который наша
             схема патча не покрывает.
    """
    target = Path(target).expanduser().resolve()
    app = target if (target / "main.js").is_file() else target / "resources" / "app"
    if not (app / "package.json").exists() and (target / "resources/app.asar").exists():
        raise UnsupportedVersion("This update uses app.asar; the resource layout needs a new patch recipe")
    if not (app / "package.json").exists():
        raise UnsupportedVersion("SnowVPN resources not found in " + str(target))
    package = json.loads((app / "package.json").read_text(encoding="utf-8-sig"))
    if package.get("name") != "snowvpn-next" or package.get("main") != "main.js":
        raise UnsupportedVersion("Not a recognized SnowVPN Electron application")
    return target, app


def prepare(target):
    """Читает файлы клиента и готовит пропатченные версии. Ничего не пишет."""
    target, app = resolve_app(target)
    old = {name: (app / name).read_bytes() for name in FILES}
    old[MODULE] = (app / MODULE).read_bytes() if (app / MODULE).exists() else None

    original_main = old["main.js"].decode("utf-8")
    platform = patch_recipes.detect_platform(original_main)

    wrapper = (BASE / "payload/fetch-wrapper.js").read_text(encoding="utf-8").strip()
    if platform == "linux":
        # Linux-обёртка отличается: в этом клиенте fetchSub(url) шлёт
        # User-Agent напрямую и не умеет его переопределять.
        wrapper = (BASE / "payload/fetch-wrapper-linux.js").read_text(encoding="utf-8").strip()

    new = patch_recipes.transform(
        platform,
        original_main,
        old["renderer/renderer.js"].decode("utf-8"),
        old["renderer/index.html"].decode("utf-8"),
        wrapper,
    )
    result = {name: text.encode("utf-8") for name, text in new.items()}
    for name in patch_recipes.payload_for(platform):
        source = BASE / "payload" / name
        old[name] = (app / name).read_bytes() if (app / name).exists() else None
        result[name] = source.read_bytes()
    return target, app, old, result, platform


def node_command():
    """Ищет Node.js для проверки синтаксиса.

    Порядок: приватный runtime патчера, системный node, и (на Linux) сам
    установленный клиент как ELECTRON_RUN_AS_NODE — у него внутри тот же
    Node, и это последний рубеж, если ничего другого нет.
    """
    private = BASE / "runtime/node/bin/node"
    if private.is_file():
        return [str(private)]
    system = shutil.which("node")
    if system:
        return [system]
    if IS_LINUX:
        for candidate in ("/opt/SnowVPN/snowvpn", "/usr/lib/snowvpn/snowvpn"):
            if Path(candidate).is_file() and os.access(candidate, os.X_OK):
                return [candidate]
    return None


def validate_scripts(new):
    """Проверяет синтаксис JS через node --check. Ничего не меняет."""
    command = node_command()
    if not command:
        raise RuntimeError("Node.js is required for syntax verification; nothing was changed")
    env = dict(os.environ)
    if IS_LINUX and command[0].endswith("snowvpn"):
        # Клиент — это Electron; в режиме Node он не поднимает GUI.
        env["ELECTRON_RUN_AS_NODE"] = "1"
    with tempfile.TemporaryDirectory(prefix="snowpatch-check-") as directory:
        for name in ("main.js", "renderer/renderer.js", MODULE, "vless.js", "js-yaml.js"):
            if name not in new:
                continue
            file = Path(directory) / Path(name).name
            file.write_bytes(new[name])
            result = subprocess.run(command + ["--check", str(file)], capture_output=True, text=True,
                                    encoding="utf-8", creationflags=CREATE_NO_WINDOW, env=env)
            if result.returncode:
                raise UnsupportedVersion("JavaScript syntax validation failed: " + name)


def target_key(target):
    return digest(str(target).casefold().encode("utf-8"))[:16]


@contextmanager
def target_lock(target):
    """Исключительная блокировка на уровне файла.

    Windows: msvcrt.locking. Linux: fcntl.flock (LOCK_EX | LOCK_NB) — если
    файл уже залочен, flock падает с BlockingIOError, и мы сообщаем, что
    другой процесс уже работает с этой целью.
    """
    STATE.mkdir(parents=True, exist_ok=True)
    file = (STATE / (target_key(Path(target).resolve()) + ".lock")).open("a+b")
    locked = False
    try:
        if os.name == "nt":
            import msvcrt
            if file.tell() == 0:
                file.write(b"0")
                file.flush()
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_LOCK, 1)
            locked = True
        else:
            import fcntl
            try:
                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError):
                raise RuntimeError("Another patch process is already working on this installation")
            locked = True
        yield
    finally:
        if locked:
            if os.name == "nt":
                import msvcrt
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(file.fileno(), fcntl.LOCK_UN)
        file.close()


def patch(target, check=False):
    target, app, old, new, platform = prepare(target)
    changed = [name for name in new if old[name] != new[name]]
    if not changed:
        return {"target": str(target), "status": "already_patched", "files": [], "platform": platform}
    validate_scripts(new)
    if check:
        return {"target": str(target), "status": "ready", "files": changed, "platform": platform}
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = BASE / "backups" / target_key(target) / stamp
    manifest = {"target": str(target), "app": str(app), "created": stamp, "platform": platform, "files": {}}
    for name in changed:
        manifest["files"][name] = {
            "existed": old[name] is not None,
            "before": digest(old[name]) if old[name] is not None else None,
            "after": digest(new[name]),
        }
        if old[name] is not None:
            destination = backup / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(old[name])
    backup.mkdir(parents=True, exist_ok=True)
    atomic_write(backup / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    # Validate the entire snapshot immediately before committing; updates may race the watcher.
    for name in old:
        current = (app / name).read_bytes() if (app / name).exists() else None
        if current != old[name]:
            raise RuntimeError("Client changed while preparing the patch; retry after the updater finishes")
    written = []
    try:
        # The helper is committed before main.js requires it.
        for name in sorted(changed, key=lambda name: (name == "main.js", name)):
            current = (app / name).read_bytes() if (app / name).exists() else None
            if current != old[name]:
                raise RuntimeError("Updater changed " + name + " during patching")
            atomic_write(app / name, new[name])
            written.append(name)
    except Exception:
        for name in reversed(written):
            if (app / name).read_bytes() == new[name]:
                if old[name] is None:
                    (app / name).unlink()
                else:
                    atomic_write(app / name, old[name])
        raise
    STATE.mkdir(parents=True, exist_ok=True)
    atomic_write(STATE / (target_key(target) + ".json"), json.dumps({"backup": str(backup), **manifest}, ensure_ascii=False, indent=2).encode("utf-8"))
    return {"target": str(target), "status": "patched", "files": changed, "backup": str(backup),
            "platform": platform, "restart_required_if_running": True}


def restore(target):
    target, app = resolve_app(target)
    state_path = STATE / (target_key(target) + ".json")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    backup = Path(state["backup"])
    for name, item in state["files"].items():
        file = app / name
        if not file.exists() or digest(file.read_bytes()) != item["after"]:
            raise RuntimeError("Client changed since patching; restoration refused: " + name)
        if item["existed"] and digest((backup / name).read_bytes()) != item["before"]:
            raise RuntimeError("Backup hash mismatch: " + name)
    # Восстанавливаем main.js ПОСЛЕДНИМ: пока он на месте, добавленный модуль
    # конвертера ещё нужен клиенту. Удаляем его только после отката main.js.
    for name in sorted(state["files"], key=lambda name: (name == "main.js", name)):
        if state["files"][name]["existed"]:
            atomic_write(app / name, (backup / name).read_bytes())
        else:
            (app / name).unlink()
    state_path.unlink()
    return {"target": str(target), "status": "restored"}


def log(message):
    STATE.mkdir(parents=True, exist_ok=True)
    path = BASE / "autopatch.log"
    if path.exists() and path.stat().st_size > 512 * 1024:
        lines = path.read_text(encoding="utf-8").splitlines()[-300:]
        atomic_write(path, ("\n".join(lines) + "\n").encode("utf-8"))
    with path.open("a", encoding="utf-8") as file:
        file.write(dt.datetime.now().isoformat(timespec="seconds") + " " + message + "\n")


def fingerprint(target):
    _, app = resolve_app(target)
    hashes = [digest((app / name).read_bytes()) for name in FILES]
    hashes.append(digest((app / MODULE).read_bytes()) if (app / MODULE).exists() else "missing")
    # Отпечаток включает и payload самого патчера: если обновился рецепт,
    # watcher должен заметить это и перепатчить клиент.
    hashes.extend(digest((BASE / "payload" / name).read_bytes())
                  for name in ("subscriptionFormat.js", "fetch-wrapper.js",
                               "fetch-wrapper-linux.js", "vless.js", "js-yaml.js"))
    return tuple(hashes)


def default_targets():
    """Цели по умолчанию для текущей платформы."""
    if IS_LINUX:
        found = [path for path in LINUX_DEFAULT_TARGETS if (Path(path) / "resources/app/package.json").is_file()]
        return found
    return [os.path.expandvars(WINDOWS_DEFAULT_TARGET)]


def acquire_single_instance():
    """Гарантирует, что watcher запущен в единственном экземпляре.

    Windows: именованный мьютекс CreateMutexW. Linux: flock на файле в
    state/ — второй экземпляр не сможет захватить блокировку и сразу выйдет.
    """
    if os.name == "nt":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.restype = ctypes.c_void_p
        mutex = kernel.CreateMutexW(None, False, "Local\\SnowVPN-AutoPatch-" + target_key(BASE))
        if ctypes.get_last_error() == 183:
            kernel.CloseHandle(ctypes.c_void_p(mutex))
            return None
        return (kernel, mutex)
    import fcntl
    STATE.mkdir(parents=True, exist_ok=True)
    file = (STATE / "watcher.lock").open("a+b")
    try:
        fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        file.close()
        return None
    return file


def watch():
    instance = acquire_single_instance()
    if instance is None:
        log("Watcher already running; exiting")
        return
    seen, done, errors = {}, {}, {}
    stop = STATE / "stop.request"
    log("Watcher started; polls every 10 seconds; does not restart or stop SnowVPN")
    try:
        while not stop.exists():
            try:
                targets = load_config()
                if not targets:
                    raise RuntimeError("No targets configured (config.json not found or empty)")
            except Exception as error:
                log("Config error: " + str(error))
                time.sleep(10)
                continue
            for target in targets:
                try:
                    current = fingerprint(target)
                    if done.get(target) == current:
                        continue
                    # Two equal observations avoid modifying partially installed update files.
                    if seen.get(target) != current:
                        seen[target] = current
                        continue
                    with target_lock(target):
                        result = patch(target)
                    done[target] = fingerprint(target)
                    errors.pop(target, None)
                    if result["status"] == "patched":
                        log(json.dumps(result, ensure_ascii=False))
                except FileNotFoundError:
                    seen.pop(target, None)
                    done.pop(target, None)
                except Exception as error:
                    message = type(error).__name__ + ": " + str(error)
                    if errors.get(target) != message:
                        log(target + " ERROR " + message)
                        errors[target] = message
            time.sleep(10)
    finally:
        if os.name == "nt":
            import ctypes
            kernel, mutex = instance
            kernel.CloseHandle(ctypes.c_void_p(mutex))
        else:
            instance.close()
        log("Watcher stopped")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", action="append", help="Installation root or resources/app directory")
    parser.add_argument("--check", action="store_true", help="Read-only compatibility and syntax check")
    parser.add_argument("--restore", action="store_true", help="Restore the latest pre-patch backup")
    parser.add_argument("--watch", action="store_true", help="Monitor configured installations until stop.request exists")
    args = parser.parse_args()
    if args.watch:
        watch()
        return 0
    if args.target:
        targets = args.target
    else:
        targets = load_config() or default_targets()
    failures = 0
    for target in targets:
        try:
            if args.check:
                result = patch(target, check=True)
            else:
                with target_lock(target):
                    result = restore(target) if args.restore else patch(target)
            print(json.dumps(result, ensure_ascii=False))
        except Exception as error:
            failures += 1
            print(json.dumps({"target": target, "status": "error", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
