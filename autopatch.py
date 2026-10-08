"""SnowVPN resource patcher. No whole-file replacement of vendor code."""
from __future__ import annotations

import argparse
import ctypes
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

BASE = Path(__file__).resolve().parent
STATE = BASE / "state"
FILES = ("main.js", "renderer/renderer.js", "renderer/index.html")
MODULE = "subscriptionFormat.js"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class UnsupportedVersion(RuntimeError):
    pass


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


def transform_main(text, wrapper):
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


def transform_renderer(text):
    branch = 'else if(r&&r.needSub){$("buySubModal").style.display="flex"}'
    if "buySubModal" in text:
        text = replace_once(text, branch, "")
        text = section(text, "var BOT_SUB_URL=", 'if($("mpAdd"))$("mpAdd").onclick=', "")
    if "buySubModal" in text or "needSub" in text:
        raise UnsupportedVersion("Unrecognized renderer restriction remains")
    return text


def transform_html(text):
    if 'id="buySubModal"' in text:
        text = section(text, '  <div class="modal" id="buySubModal">', '  <div class="modal" id="profModal">', "")
    text = text.replace('placeholder="Вставьте ссылку на подписку (clash)"', 'placeholder="Ссылка на подписку Clash любого провайдера"')
    text = text.replace("актуальный список серверов от нас", "актуальный список серверов от вашего провайдера")
    if "только с нашими подписками" in text:
        raise UnsupportedVersion("Unrecognized exclusivity notice remains")
    return text


def resolve_app(target):
    target = Path(target).expanduser().resolve()
    app = target if (target / "main.js").is_file() else target / "resources" / "app"
    if not (app / "package.json").exists() and (target / "resources/app.asar").exists():
        raise UnsupportedVersion("This update uses app.asar; the resource layout needs a new patch recipe")
    package = json.loads((app / "package.json").read_text(encoding="utf-8-sig"))
    if package.get("name") != "snowvpn-next" or package.get("main") != "main.js":
        raise UnsupportedVersion("Not a recognized SnowVPN Electron application")
    if not (app / "node_modules/js-yaml/package.json").is_file():
        raise UnsupportedVersion("Bundled js-yaml not found")
    return target, app


def prepare(target):
    target, app = resolve_app(target)
    old = {name: (app / name).read_bytes() for name in FILES}
    old[MODULE] = (app / MODULE).read_bytes() if (app / MODULE).exists() else None
    wrapper = (BASE / "payload/fetch-wrapper.js").read_text(encoding="utf-8").strip()
    new = {
        "main.js": transform_main(old["main.js"].decode("utf-8"), wrapper).encode("utf-8"),
        "renderer/renderer.js": transform_renderer(old["renderer/renderer.js"].decode("utf-8")).encode("utf-8"),
        "renderer/index.html": transform_html(old["renderer/index.html"].decode("utf-8")).encode("utf-8"),
        MODULE: (BASE / "payload" / MODULE).read_bytes(),
    }
    return target, app, old, new


def validate_scripts(new):
    private_node = BASE / "runtime/node/node.exe"
    node = str(private_node) if private_node.is_file() else shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required for syntax verification; nothing was changed")
    with tempfile.TemporaryDirectory(prefix="snowpatch-check-") as directory:
        for name in ("main.js", "renderer/renderer.js", MODULE):
            file = Path(directory) / Path(name).name
            file.write_bytes(new[name])
            result = subprocess.run([node, "--check", str(file)], capture_output=True, text=True,
                                    encoding="utf-8", creationflags=CREATE_NO_WINDOW)
            if result.returncode:
                raise UnsupportedVersion("JavaScript syntax validation failed: " + name)


def target_key(target):
    return digest(str(target).casefold().encode("utf-8"))[:16]


@contextmanager
def target_lock(target):
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
        yield
    finally:
        if locked:
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
        file.close()


def patch(target, check=False):
    target, app, old, new = prepare(target)
    changed = [name for name in new if old[name] != new[name]]
    if not changed:
        return {"target": str(target), "status": "already_patched", "files": []}
    validate_scripts(new)
    if check:
        return {"target": str(target), "status": "ready", "files": changed}
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = BASE / "backups" / target_key(target) / stamp
    manifest = {"target": str(target), "app": str(app), "created": stamp, "files": {}}
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
    return {"target": str(target), "status": "patched", "files": changed, "backup": str(backup), "restart_required_if_running": True}


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
    # Restore main first; the added helper must not disappear while still required.
    for name in sorted(state["files"], key=lambda name: (name != "main.js", name)):
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
    hashes.extend(digest((BASE / "payload" / name).read_bytes()) for name in (MODULE, "fetch-wrapper.js"))
    return tuple(hashes)


def watch():
    mutex = None
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.restype = ctypes.c_void_p
        mutex = kernel.CreateMutexW(None, False, "Local\\SnowVPN-AutoPatch-" + target_key(BASE))
        if ctypes.get_last_error() == 183:
            kernel.CloseHandle(ctypes.c_void_p(mutex))
            return
    seen, done, errors = {}, {}, {}
    stop = STATE / "stop.request"
    log("Watcher started; polls every 10 seconds; does not restart or stop SnowVPN")
    try:
        while not stop.exists():
            try:
                config = json.loads((BASE / "config.json").read_text(encoding="utf-8-sig"))
                targets = config["targets"]
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
        if mutex:
            kernel.CloseHandle(ctypes.c_void_p(mutex))
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
    targets = args.target or json.loads((BASE / "config.json").read_text(encoding="utf-8-sig"))["targets"]
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
