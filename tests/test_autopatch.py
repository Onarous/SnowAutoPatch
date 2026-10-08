import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('snowpatch', ROOT / 'autopatch.py')
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)

# Minimal synthetic source with the public patch markers; no vendor files.
MAIN = '''const path=require("path");
const SUB_HOSTS_FALLBACK=[];
function isOurSubUrl(url){return false}
function normalizeSubUrl(url){return url}
async function importSub(url){const settings={};if(!settings.devUnlocked&&!isOurSubUrl(url)){await refreshSubHosts();if(!isOurSubUrl(url)){const e=new Error("Это ссылка не от SnowVPN. Клиент работает только с подписками SnowVPN.");e.needSub=true;throw e}}}
function start(){startSubAutoUpdate();refreshSubHosts();}
function failure(e){return {error:e.message,needSub:!!e.needSub}}
const help="только если это ваш личный кабинет SnowVPN";
async function fetchSub(url,uaOverride){return {text:"",info:{}}}
const EXTRA_NODES_UA="test";
'''
RENDERER = '''function result(r){if(r.ok){done()}else if(r&&r.needSub){$("buySubModal").style.display="flex"}}
var BOT_SUB_URL="https://example.invalid";
if($("mpAdd"))$("mpAdd").onclick=()=>{};
'''
HTML = '''<!doctype html><input placeholder="Вставьте ссылку на подписку (clash)">
  <div class="modal" id="buySubModal">только с нашими подписками</div>
  <div class="modal" id="profModal"></div>
актуальный список серверов от нас
'''


class PatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='snow-autopatch-test-')
        self.root = Path(self.temp.name)
        self.tools = self.root / 'tools'
        shutil.copytree(ROOT / 'payload', self.tools / 'payload')
        private_node = ROOT / 'runtime/node/node.exe'
        if private_node.is_file():
            destination = self.tools / 'runtime/node/node.exe'
            destination.parent.mkdir(parents=True)
            shutil.copy2(private_node, destination)
        patcher.BASE, patcher.STATE = self.tools, self.tools / 'state'
        self.target = self.root / 'client'
        self.app = self.target / 'resources/app'
        self.vendor()

    def tearDown(self):
        patcher.BASE, patcher.STATE = ROOT, ROOT / 'state'
        self.temp.cleanup()

    def vendor(self, extra=''):
        for name, text in zip(patcher.FILES, (MAIN + extra, RENDERER, HTML)):
            file = self.app / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(text.encode())
        (self.app / 'package.json').write_text(json.dumps({'name':'snowvpn-next','main':'main.js','version':'99.0-test'}))
        library = self.app / 'node_modules/js-yaml'
        library.mkdir(parents=True, exist_ok=True)
        (library / 'package.json').write_text('{"name":"js-yaml"}')
        (self.app / patcher.MODULE).unlink(missing_ok=True)

    def contents(self):
        return {name: (self.app / name).read_bytes() if (self.app / name).exists() else None
                for name in (*patcher.FILES, patcher.MODULE)}

    def test_patch_and_idempotency(self):
        self.assertEqual(patcher.patch(self.target)['status'], 'patched')
        self.assertNotIn('isOurSubUrl', (self.app / 'main.js').read_text(encoding='utf-8'))
        before = {name:(self.app / name).stat().st_mtime_ns for name in self.contents()}
        self.assertEqual(patcher.patch(self.target)['status'], 'already_patched')
        self.assertEqual(before, {name:(self.app / name).stat().st_mtime_ns for name in self.contents()})

    def test_read_only_check(self):
        original = self.contents()
        self.assertEqual(patcher.patch(self.target, check=True)['status'], 'ready')
        self.assertEqual(original, self.contents())
        self.assertFalse((self.tools / 'backups').exists())

    def test_update_preserves_new_code_and_version(self):
        patcher.patch(self.target)
        self.vendor('\n// brand new vendor feature\n')
        patcher.patch(self.target)
        self.assertTrue((self.app / 'main.js').read_bytes().endswith(b'// brand new vendor feature\n'))
        self.assertEqual(json.loads((self.app / 'package.json').read_text())['version'], '99.0-test')
        self.assertEqual(len(list((self.tools / 'backups').rglob('manifest.json'))), 2)

    def test_unknown_version_leaves_files(self):
        file = self.app / 'main.js'
        file.write_bytes(file.read_bytes().replace(b'isOurSubUrl', b'newUnknownGate'))
        before = self.contents()
        with self.assertRaises(patcher.UnsupportedVersion):
            patcher.patch(self.target)
        self.assertEqual(before, self.contents())

    def test_restore(self):
        original = self.contents()
        patcher.patch(self.target)
        patcher.restore(self.target)
        self.assertEqual(original, self.contents())

    def test_restore_refuses_updated_vendor(self):
        patcher.patch(self.target)
        with (self.app / 'main.js').open('ab') as file:
            file.write(b'\n// subsequent update\n')
        before = self.contents()
        with self.assertRaises(RuntimeError):
            patcher.restore(self.target)
        self.assertEqual(before, self.contents())

    def test_asar_is_rejected(self):
        target = self.root / 'asar-client'
        (target / 'resources').mkdir(parents=True)
        (target / 'resources/app.asar').write_bytes(b'synthetic')
        with self.assertRaises(patcher.UnsupportedVersion):
            patcher.resolve_app(target)


if __name__ == '__main__':
    unittest.main()
