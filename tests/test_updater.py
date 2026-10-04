"""
Atnaujintuvo testai be Windows ir be Qt.

  python -m unittest discover -s tests -v

PowerShell testams reikia `pwsh` (Linux versija tinka). Kelias: PATH arba aplinkos kintamasis PWSH.
"""
import os
import sys
import json
import time
import shutil
import hashlib
import zipfile
import tempfile
import threading
import subprocess
import unittest
import http.server
import socketserver

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import fake_qt  # noqa: E402
fake_qt.install()
import updater as U  # noqa: E402


def make_zip(path, files):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)


GOOD_FILES = {
    "PrintReady/PrintReady.exe": b"MZ" + b"x" * 5000,
    "PrintReady/_internal/base_library.zip": b"lib",
    "PrintReady/README.md": b"readme",
}


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


class VersionTests(unittest.TestCase):
    def test_compare(self):
        self.assertTrue(U.is_newer_version("v2.5.10", "2.5.9"))
        self.assertTrue(U.is_newer_version("2.6.0", "2.5.99"))
        self.assertTrue(U.is_newer_version("3.0.0", "2.9.9"))
        self.assertFalse(U.is_newer_version("v2.5.7", "2.5.7"))
        self.assertFalse(U.is_newer_version("2.5.6", "2.5.7"))
        self.assertFalse(U.is_newer_version("2.5", "2.5.0"))
        self.assertTrue(U.is_newer_version("2.5.0.1", "2.5"))
        self.assertFalse(U.is_newer_version("", "2.5.7"))


class AssetTests(unittest.TestCase):
    ASSETS = [
        {"name": "PrintReady.exe", "browser_download_url": "u/exe", "size": 1},
        {"name": "PrintReady_v2.5.7.zip", "browser_download_url": "u/old", "size": 2},
        {"name": "Source code.zip", "browser_download_url": "u/src", "size": 3},
        {"name": "PrintReady_v2.5.8.zip", "browser_download_url": "u/new", "size": 4,
         "digest": "sha256:" + "a" * 64},
    ]

    def test_exact_zip_selected(self):
        a = U.select_release_asset(self.ASSETS, "v2.5.8")
        self.assertEqual(a["browser_download_url"], "u/new")

    def test_no_matching_zip(self):
        self.assertIsNone(U.select_release_asset(self.ASSETS[:3], "v2.5.8"))
        self.assertIsNone(U.select_release_asset([{"name": "PrintReady_v2.5.8.zip.part",
                                                   "browser_download_url": "x"}], "v2.5.8"))

    def test_build_info(self):
        info = U.build_update_info({"tag_name": "v2.5.8", "assets": self.ASSETS, "body": "- a"})
        self.assertTrue(info["asset_found"])
        self.assertEqual(info["version"], "2.5.8")
        self.assertEqual(info["asset_size"], 4)
        self.assertEqual(info["sha256"], "a" * 64)
        info2 = U.build_update_info({"tag_name": "v2.5.9", "assets": self.ASSETS, "zipball_url": "zz"})
        self.assertFalse(info2["asset_found"])
        self.assertIsNone(info2["url"])

    def test_digest(self):
        self.assertIsNone(U.parse_digest(None))
        self.assertIsNone(U.parse_digest("md5:abc"))
        self.assertEqual(U.parse_digest("sha256:" + "AB" * 32), "ab" * 32)


class ZipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _zip(self, files, name="p.zip"):
        p = os.path.join(self.tmp, name)
        make_zip(p, files)
        return p

    def test_good(self):
        p = self._zip(GOOD_FILES)
        U.verify_zip(p, os.path.getsize(p), sha(p))
        app = U.extract_to_staging(p, os.path.join(self.tmp, "staging"))
        self.assertTrue(os.path.isfile(os.path.join(app, "PrintReady.exe")))

    def test_html_error_page(self):
        p = os.path.join(self.tmp, "err.zip")
        with open(p, "w") as f:
            f.write("<!DOCTYPE html><html>Not Found</html>")
        with self.assertRaises(U.UpdateError) as c:
            U.verify_zip(p)
        self.assertIn("nėra ZIP", str(c.exception))

    def test_missing_exe(self):
        p = self._zip({"PrintReady/_internal/a": b"1"})
        with self.assertRaises(U.UpdateError) as c:
            U.verify_zip(p)
        self.assertIn("PrintReady.exe", str(c.exception))

    def test_missing_internal(self):
        p = self._zip({"PrintReady/PrintReady.exe": b"1"})
        with self.assertRaises(U.UpdateError) as c:
            U.verify_zip(p)
        self.assertIn("_internal", str(c.exception))

    def test_wrong_size(self):
        p = self._zip(GOOD_FILES)
        with self.assertRaises(U.UpdateError) as c:
            U.verify_zip(p, os.path.getsize(p) + 1)
        self.assertIn("dydis", str(c.exception))

    def test_wrong_hash(self):
        p = self._zip(GOOD_FILES)
        with self.assertRaises(U.UpdateError) as c:
            U.verify_zip(p, os.path.getsize(p), "0" * 64)
        self.assertIn("SHA-256", str(c.exception))

    def test_truncated(self):
        p = self._zip(GOOD_FILES)
        data = open(p, "rb").read()
        with open(p, "wb") as f:
            f.write(data[: len(data) // 2])
        with self.assertRaises(U.UpdateError):
            U.verify_zip(p)

    def test_zip_slip(self):
        p = self._zip(dict(GOOD_FILES, **{"../../evil.txt": b"x"}))
        with self.assertRaises(U.UpdateError):
            U.extract_to_staging(p, os.path.join(self.tmp, "staging"))


class QuoteAndResultTests(unittest.TestCase):
    def test_ps_quote(self):
        self.assertEqual(U.ps_quote("a'b"), "'a''b'")
        self.assertEqual(U.ps_quote("C:\\$x `y ąčę"), "'C:\\$x `y ąčę'")

    def test_script_has_no_placeholders(self):
        s = U.build_update_script("C:\\A", "C:\\B", "C:\\W", 1, "2.5.8", "2.5.7", "l", "r")
        self.assertNotIn("__", s.replace("$__", ""))

    def test_script_written_with_bom(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "s.ps1")
            U.write_update_script(p, "Write-Output 'ą'")
            self.assertTrue(open(p, "rb").read().startswith(b"\xef\xbb\xbf"))
        finally:
            shutil.rmtree(d)

    def test_read_result(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "r.json")
            with open(p, "w", encoding="utf-8-sig") as f:
                json.dump({"ok": True, "version": "2.5.8"}, f)
            self.assertEqual(U.read_update_result(p)["version"], "2.5.8")
            self.assertFalse(os.path.exists(p))
            self.assertIsNone(U.read_update_result(p))
            with open(p, "w") as f:
                f.write("{sugadinta")
            self.assertFalse(U.read_update_result(p)["ok"])
        finally:
            shutil.rmtree(d)


class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class DownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp()
        with open(os.path.join(cls.root, "ok.bin"), "wb") as f:
            f.write(os.urandom(300_000))
        handler = lambda *a, **k: _Handler(*a, directory=cls.root, **k)
        cls.httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_ok(self):
        dest = os.path.join(self.root, "out.bin")
        seen = []
        U.download_file(f"http://127.0.0.1:{self.port}/ok.bin", dest, 300_000, lambda d, t: seen.append(d))
        self.assertEqual(os.path.getsize(dest), 300_000)

    def test_404_is_error_not_file(self):
        dest = os.path.join(self.root, "out404.bin")
        with self.assertRaises(U.UpdateError):
            U.download_file(f"http://127.0.0.1:{self.port}/nera.zip", dest, 0)


# -------------------------------------------------------------------------
# PowerShell skripto testai
# -------------------------------------------------------------------------
PWSH = os.environ.get("PWSH") or shutil.which("pwsh")

FAKE_ROBOCOPY = r'''#!/usr/bin/env python3
import os, sys, shutil
cnt_file = os.environ.get("ROBO_COUNTER")
n = 1
if cnt_file:
    n = int(open(cnt_file).read()) + 1 if os.path.exists(cnt_file) else 1
    open(cnt_file, "w").write(str(n))
if os.environ.get("ROBO_FAIL_ON") == str(n):
    print("ERROR 5 (0x00000005) Access is denied.")
    sys.exit(16)
args = sys.argv[1:]
src, dst, rest = args[0], args[1], args[2:]
xd, xf, mode = set(), set(), None
for a in rest:
    if a.startswith("/"):
        mode = a.upper() if a.upper() in ("/XD", "/XF") else None
    elif mode == "/XD":
        xd.add(a)
    elif mode == "/XF":
        xf.add(a)
for root, dirs, files in os.walk(src):
    dirs[:] = [d for d in dirs if d not in xd]
    rel = os.path.relpath(root, src)
    out = os.path.join(dst, rel) if rel != "." else dst
    os.makedirs(out, exist_ok=True)
    for f in files:
        if f in xf:
            continue
        shutil.copy2(os.path.join(root, f), os.path.join(out, f))
sys.exit(1)
'''


def fake_exe(label, marker):
    return f"#!/bin/sh\necho {label} > '{marker}'\n"


@unittest.skipUnless(PWSH, "pwsh nerastas")
class PowerShellScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.base = os.path.join(self.tmp, "Darbas ąčę O'Brien $HOME")
        self.app = os.path.join(self.base, "Programa ąčęėįšųūž O'Neil $x `y")
        self.work = os.path.join(self.base, "PrintReady_update")
        self.new = os.path.join(self.work, "staging", "PrintReady")
        self.marker = os.path.join(self.tmp, "launched.txt")
        self.log = os.path.join(self.tmp, "upd ą.log")
        self.result = os.path.join(self.tmp, "result.json")
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        rc = os.path.join(self.bin, "robocopy.exe")
        open(rc, "w").write(FAKE_ROBOCOPY)
        os.chmod(rc, 0o755)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _w(self, path, text, exe=False):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        if exe:
            os.chmod(path, 0o755)

    def _setup(self, with_internal=True):
        self._w(os.path.join(self.app, "PrintReady.exe"), fake_exe("old", self.marker), True)
        if with_internal:
            self._w(os.path.join(self.app, "_internal", "old.txt"), "old")
        self._w(os.path.join(self.app, "config.json"), '{"mano": 1}')
        self._w(os.path.join(self.app, "Sablonai", "mano.png"), "png")
        self._w(os.path.join(self.new, "PrintReady.exe"), fake_exe("new", self.marker), True)
        self._w(os.path.join(self.new, "_internal", "new.txt"), "new")
        self._w(os.path.join(self.new, "config.json"), '{"default": 1}')
        self._w(os.path.join(self.new, "Sablonai", "README.md"), "r")
        self._w(os.path.join(self.new, "README.md"), "naujas")

    def _run(self, fail_on=None):
        sleeper = subprocess.Popen(["sleep", "1"])
        script = os.path.join(self.tmp, "upd.ps1")
        text = U.build_update_script(self.app, self.new, self.work, sleeper.pid, "2.5.8", "2.5.7",
                                     self.log, self.result)
        U.write_update_script(script, text)
        env = dict(os.environ, PATH=self.bin + os.pathsep + os.environ["PATH"],
                   ROBO_COUNTER=os.path.join(self.tmp, "cnt"))
        if fail_on:
            env["ROBO_FAIL_ON"] = str(fail_on)
        p = subprocess.run([PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script],
                           env=env, capture_output=True, text=True, timeout=120)
        sleeper.wait()
        for _ in range(50):
            if os.path.exists(self.marker):
                break
            time.sleep(0.1)
        return p

    def _read(self, *parts):
        return open(os.path.join(self.app, *parts), encoding="utf-8").read()

    def test_syntax(self):
        script = os.path.join(self.tmp, "s.ps1")
        U.write_update_script(script, U.build_update_script(self.app, self.new, self.work, 1, "1", "0",
                                                            self.log, self.result))
        cmd = ("$e=$null; $t=$null; [System.Management.Automation.Language.Parser]::ParseFile("
               + U.ps_quote(script) + ", [ref]$t, [ref]$e) | Out-Null; "
               "if ($e.Count) { $e | ForEach-Object { $_.ToString() }; exit 1 }")
        p = subprocess.run([PWSH, "-NoProfile", "-Command", cmd], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_quote_roundtrip(self):
        s = "C:\\Users\\Jonas O'Brien\\$HOME `n ąčęėįšųūž \"x\""
        script = os.path.join(self.tmp, "q.ps1")
        U.write_update_script(script, "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
                                      "Write-Output " + U.ps_quote(s))
        p = subprocess.run([PWSH, "-NoProfile", "-File", script], capture_output=True)
        self.assertEqual(p.stdout.decode("utf-8").strip(), s)

    def test_success(self):
        self._setup()
        p = self._run()
        res = json.load(open(self.result, encoding="utf-8-sig"))
        self.assertTrue(res["ok"], open(self.log, encoding="utf-8-sig").read() + p.stderr)
        self.assertIn("new", self._read("PrintReady.exe"))
        self.assertTrue(os.path.exists(os.path.join(self.app, "_internal", "new.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.app, "_internal", "old.txt")), "_internal ne švarus")
        self.assertEqual(self._read("config.json"), '{"mano": 1}')
        self.assertTrue(os.path.exists(os.path.join(self.app, "Sablonai", "mano.png")))
        self.assertFalse(os.path.exists(os.path.join(self.app, "Sablonai", "README.md")))
        self.assertEqual(self._read("README.md"), "naujas")
        self.assertFalse(os.path.exists(os.path.join(self.app, "PrintReady.exe.old")))
        self.assertFalse(os.path.exists(os.path.join(self.app, "_internal.old")))
        self.assertFalse(os.path.exists(self.work))
        self.assertEqual(open(self.marker).read().strip(), "new")

    def test_failure_rolls_back_and_restarts(self):
        self._setup()
        self._run(fail_on=2)  # _internal nukopijuotas, šakninis kopijavimas nepavyksta
        res = json.load(open(self.result, encoding="utf-8-sig"))
        self.assertFalse(res["ok"])
        self.assertIn("robocopy", res["message"])
        self.assertIn("old", self._read("PrintReady.exe"))
        self.assertTrue(os.path.exists(os.path.join(self.app, "_internal", "old.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.app, "_internal", "new.txt")))
        self.assertEqual(self._read("config.json"), '{"mano": 1}')
        self.assertFalse(os.path.exists(self.work))
        self.assertEqual(open(self.marker).read().strip(), "old")
        self.assertIn("Sena versija atstatyta", open(self.log, encoding="utf-8-sig").read())

    def test_first_update_from_single_file_version(self):
        self._setup(with_internal=False)
        p = self._run()
        res = json.load(open(self.result, encoding="utf-8-sig"))
        self.assertTrue(res["ok"], p.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.app, "_internal", "new.txt")))
        self.assertEqual(open(self.marker).read().strip(), "new")

    def test_failure_from_single_file_version(self):
        self._setup(with_internal=False)
        self._run(fail_on=1)
        res = json.load(open(self.result, encoding="utf-8-sig"))
        self.assertFalse(res["ok"])
        self.assertFalse(os.path.exists(os.path.join(self.app, "_internal")))
        self.assertIn("old", self._read("PrintReady.exe"))
        self.assertEqual(open(self.marker).read().strip(), "old")

    def test_missing_staging_does_not_touch_app(self):
        self._setup()
        shutil.rmtree(os.path.join(self.new, "_internal"))
        self._run()
        res = json.load(open(self.result, encoding="utf-8-sig"))
        self.assertFalse(res["ok"])
        self.assertIn("old", self._read("PrintReady.exe"))
        self.assertTrue(os.path.exists(os.path.join(self.app, "_internal", "old.txt")))
        self.assertEqual(open(self.marker).read().strip(), "old")


if __name__ == "__main__":
    unittest.main()
