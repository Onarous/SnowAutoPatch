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

recipes_spec = importlib.util.spec_from_file_location('snowpatch_recipes', ROOT / 'patch_recipes.py')
recipes = importlib.util.module_from_spec(recipes_spec)
recipes_spec.loader.exec_module(recipes)

# Синтетический Linux-клиент: маркеры взяты из реального клиента 2.8.37,
# но без вендорского кода и без единого байта оригинальных файлов.
LINUX_MAIN = '''const path = require('path');
const fs = require('fs');
const SUB_UA = `Clash/Meta; SnowVPN/v5.1.0 Platform/linux`;
function decodeProfileTitle(v){return v||''}
function parseUserInfo(v){return {}}
function parseDispositionName(v){return ''}
function looksBlocked(text) {
  const t = (text || '').trim().slice(0, 400).toLowerCase();
  return t.includes('<iframe') || t.includes('<!doctype html') || t.includes('<html') || t.includes('403');
}
function looksDummy(text) {
  return /не поддерживается|скачайте.{0,20}happ|snow client/i.test(text || '');
}
function fetchSub(url) {
  return new Promise((resolve, reject) => {
    const req = net.request({ url, redirect: 'follow' });
    const chunks = [];
    req.on('response', (res) => {
      res.on('data', (c) => { chunks.push(Buffer.isBuffer(c) ? c : Buffer.from(c)); });
      res.on('end', () => resolve({ text: Buffer.concat(chunks).toString('utf8'), status: res.statusCode || 0 }));
    });
    req.setHeader('User-Agent', SUB_UA);
    req.setHeader('Accept', '*/*');
    req.end();
  });
}
function hostName(url) { try { return new URL(url).hostname; } catch (_) { return 'subscription'; } }
async function importSub(url, name) {
  const { text, status } = await fetchSub(url);
  if (status && status >= 400) throw new Error(`Сервер вернул ${status}. Проверьте ссылку/доступ`);
  const id = 'sub_' + Date.now().toString(36);
  fs.writeFileSync(profilePath(id), text, 'utf8');
  return id;
}
async function handleDeepLinkArgv(argv) {
  const subUrl = extractDeepLinkUrl(argv);
  const r = { response: 0 };
  const win = { isDestroyed: () => false };
  dialog.showMessageBox(win, {
    type: 'question', noLink: true, defaultId: 0, cancelId: 1,
    buttons: ['Добавить', 'Отмена'],
    title: 'Добавление подписки',
    message: 'Добавить подписку с ' + hostName(subUrl) + '?',
    detail: 'Ссылка открыта из браузера. Она станет активной подпиской — добавляйте только если это ваш личный кабинет SnowVPN.',
  });
}
'''

LINUX_RENDERER = '''function $(id) { return document.getElementById(id); }
$('subAdd').onclick = async function () {
  var url = $('subUrl').value.trim(); if (!url) { toast('Вставьте ссылку на подписку'); return; }
  var r = await api.importSub(url);
};
$('buyBtn').onclick = function () { if (api.openExternal) api.openExternal('https://snowservice.github.io/snowvpn/'); };
'''

LINUX_HTML = '''<!doctype html>
    <section class="scr" data-screen="subs">
      <div class="preset"><div class="pi" style="background:linear-gradient(135deg,#ffd166,#ff9e4d)"><i class="ti ti-shopping-cart"></i></div><div class="pt"><div class="a">Купить подписку</div><div class="b">snowservice · быстрые серверы</div></div><button class="pb" id="buyBtn" style="background:linear-gradient(135deg,#ffd166,#ff9e4d)">Открыть</button></div>
      <div class="search" style="margin:14px 0"><i class="ti ti-link"></i><input id="subUrl" placeholder="Вставьте ссылку на подписку (clash)"></div>
    </section>
    <div class="faqitem" data-q="sub"><div class="faqa">Подписка — это ссылка, по которой приложение получает актуальный список серверов от нас (сервера периодически меняются/добавляются).</div></div>
'''


class LinuxPatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='snow-autopatch-linux-test-')
        self.root = Path(self.temp.name)
        self.tools = self.root / 'tools'
        shutil.copytree(ROOT / 'payload', self.tools / 'payload')
        patcher.BASE, patcher.STATE = self.tools, self.tools / 'state'
        self.target = self.root / 'client'
        self.app = self.target / 'resources/app'
        self.vendor()

    def tearDown(self):
        patcher.BASE, patcher.STATE = ROOT, ROOT / 'state'
        self.temp.cleanup()

    def vendor(self, extra=''):
        for name, text in zip(patcher.FILES, (LINUX_MAIN + extra, LINUX_RENDERER, LINUX_HTML)):
            file = self.app / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(text.encode())
        (self.app / 'package.json').write_text(json.dumps({'name': 'snowvpn-next', 'main': 'main.js', 'version': '2.8.37-test'}))
        (self.app / patcher.MODULE).unlink(missing_ok=True)

    def contents(self):
        return {name: (self.app / name).read_bytes() if (self.app / name).exists() else None
                for name in (*patcher.FILES, patcher.MODULE)}

    def test_platform_detection(self):
        self.assertEqual(recipes.detect_platform(LINUX_MAIN), 'linux')

    def test_patch_applies_and_is_idempotent(self):
        result = patcher.patch(self.target)
        self.assertEqual(result['status'], 'patched')
        self.assertEqual(result['platform'], 'linux')
        main = (self.app / 'main.js').read_text(encoding='utf-8')
        self.assertIn('normalizeSubscriptionText', main)
        self.assertIn('fetchSubRaw', main)
        self.assertNotIn('личный кабинет SnowVPN', main)
        before = {name: (self.app / name).stat().st_mtime_ns for name in self.contents()}
        self.assertEqual(patcher.patch(self.target)['status'], 'already_patched')
        self.assertEqual(before, {name: (self.app / name).stat().st_mtime_ns for name in self.contents()})

    def test_purchase_block_removed(self):
        patcher.patch(self.target)
        html = (self.app / 'renderer/index.html').read_text(encoding='utf-8')
        self.assertNotIn('buyBtn', html)
        self.assertNotIn('Купить подписку', html)
        renderer = (self.app / 'renderer/renderer.js').read_text(encoding='utf-8')
        self.assertNotIn('buyBtn', renderer)

    def test_placeholder_and_wording_updated(self):
        patcher.patch(self.target)
        html = (self.app / 'renderer/index.html').read_text(encoding='utf-8')
        self.assertIn('placeholder="Ссылка на подписку Clash любого провайдера"', html)
        self.assertIn('актуальный список серверов от вашего провайдера', html)
        self.assertNotIn('актуальный список серверов от нас', html)

    def test_read_only_check_changes_nothing(self):
        original = self.contents()
        self.assertEqual(patcher.patch(self.target, check=True)['status'], 'ready')
        self.assertEqual(original, self.contents())
        self.assertFalse((self.tools / 'backups').exists())

    def test_update_preserves_new_vendor_code(self):
        patcher.patch(self.target)
        self.vendor('\n// brand new vendor feature\n')
        patcher.patch(self.target)
        self.assertTrue((self.app / 'main.js').read_bytes().endswith(b'// brand new vendor feature\n'))
        self.assertEqual(len(list((self.tools / 'backups').rglob('manifest.json'))), 2)

    def test_unknown_version_leaves_files(self):
        file = self.app / 'main.js'
        file.write_bytes(file.read_bytes().replace(b'function fetchSub(url) {', b'function fetchSubNew(url) {'))
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
