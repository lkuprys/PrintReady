"""
Spaudos variklio, šablonų atpažinimo ir užsakymų stebėjimo testai (be Qt ir be Windows).

  python -m unittest discover -s tests -v
"""
import os
import sys
import time
import shutil
import datetime
import tempfile
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import numpy as np  # noqa: E402
import tifffile  # noqa: E402
from PIL import Image  # noqa: E402

import crop_engine as CE  # noqa: E402
import order_watcher as OW  # noqa: E402
from template_manager import TemplateManager  # noqa: E402


def parse_8bim(data: bytes) -> dict:
    out, i = {}, 0
    while i + 12 <= len(data) and data[i:i + 4] == b"8BIM":
        rid = int.from_bytes(data[i + 4:i + 6], "big")
        name_len = data[i + 6]
        j = i + 7 + name_len + ((1 + name_len) % 2)
        size = int.from_bytes(data[j:j + 4], "big")
        out[rid] = data[j + 4:j + 4 + size]
        i = j + 4 + size + (size % 2)
    return out


def make_template(path, w=80, h=60, margin=5):
    t = np.zeros((h, w, 4), np.uint8)
    t[margin:h - margin, margin:w - margin, 3] = 255
    Image.fromarray(t).save(path)


def make_image(path, w=160, h=120, mode="RGB"):
    arr = np.random.RandomState(1).randint(0, 255, (h, w, 3), np.uint8)
    img = Image.fromarray(arr)
    if mode == "CMYK":
        img = img.convert("CMYK")
    img.save(path)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tmpl = os.path.join(self.tmp, "2681.png")
        self.img = os.path.join(self.tmp, "img.png")
        make_template(self.tmpl)
        make_image(self.img)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _produce(self, name="out.tif", **kw):
        out = os.path.join(self.tmp, name)
        CE.process_and_crop(self.img, self.tmpl, out, **kw)
        return out

    def test_output_structure_and_300_dpi(self):
        out = self._produce()
        with tifffile.TiffFile(out) as t:
            page = t.pages[0]
            self.assertEqual(page.shape, (60, 80, 6))
            self.assertEqual(page.tags["XResolution"].value, (300, 1))
            self.assertIn(34675, page.tags)  # ICC profilis
            res = parse_8bim(page.tags[34377].value)
            # Photoshop ResolutionInfo: fiksuoto kablelio 16.16 -> 300 DPI
            self.assertEqual(int.from_bytes(res[1005][0:4], "big") / 65536, 300)
            self.assertEqual(int.from_bytes(res[1005][8:12], "big") / 65536, 300)

    def test_photoshop_resolution_follows_setting(self):
        out = self._produce(target_dpi=600)
        with tifffile.TiffFile(out) as t:
            res = parse_8bim(t.pages[0].tags[34377].value)
        self.assertEqual(int.from_bytes(res[1005][0:4], "big") / 65536, 600)

    def test_choke_setting_applies_without_restart(self):
        white = []
        for choke in (1, 5, 1):
            arr = tifffile.imread(self._produce(f"c{choke}_{len(white)}.tif", choke_pixels=choke))
            white.append(int((arr[..., 5] == 0).sum()))
        self.assertLess(white[1], white[0])
        self.assertEqual(white[0], white[2])

    def _partials(self):
        return [f for f in os.listdir(self.tmp) if f.endswith(CE.PARTIAL_SUFFIX)]

    def test_failed_rename_removes_written_temp_file(self):
        # Laikinas failas jau įrašytas, bet pervadinti nepavyksta – neturi likti nei .tif, nei .partial
        out = os.path.join(self.tmp, "fail.tif")
        with mock.patch.object(CE.os, "replace", side_effect=OSError("READY nepasiekiamas")):
            with self.assertRaises(OSError):
                CE.process_and_crop(self.img, self.tmpl, out)
        self.assertEqual(self._partials(), [])
        self.assertFalse(os.path.exists(out))

    def test_failed_write_leaves_no_file(self):
        out = os.path.join(self.tmp, "fail.tif")
        real = CE.tifffile.imwrite

        def half_write(path, *a, **kw):
            with open(path, "wb") as f:
                f.write(b"II*\x00partial")
            raise OSError("disk full")

        with mock.patch.object(CE.tifffile, "imwrite", side_effect=half_write):
            with self.assertRaises(OSError):
                CE.process_and_crop(self.img, self.tmpl, out)
        self.assertEqual(self._partials(), [])
        self.assertFalse(os.path.exists(out))
        self.assertIsNotNone(real)

    def test_locked_file_rename_is_retried(self):
        out = os.path.join(self.tmp, "locked.tif")
        real_replace = os.replace
        calls = []

        def flaky(src, dst):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError("WinError 32: užrakinta")
            return real_replace(src, dst)

        with mock.patch.object(CE.os, "replace", side_effect=flaky), mock.patch.object(CE.time, "sleep"):
            CE.process_and_crop(self.img, self.tmpl, out)
        self.assertTrue(os.path.exists(out))
        self.assertEqual(len(calls), 3)

    def test_cmyk_source_uses_icc_not_naive_conversion(self):
        cmyk_path = os.path.join(self.tmp, "cmyk.tif")
        make_image(cmyk_path, mode="CMYK")
        naive_rgb = os.path.join(self.tmp, "naive.png")
        Image.open(cmyk_path).convert("RGB").save(naive_rgb)  # senasis (netikslus) kelias
        self.img = cmyk_path
        icc_arr = tifffile.imread(self._produce("cmyk.tif"))
        self.img = naive_rgb
        naive_arr = tifffile.imread(self._produce("naive.tif"))
        self.assertEqual(icc_arr.shape, (60, 80, 6))
        self.assertGreater(int(np.abs(icc_arr[..., :4].astype(int) - naive_arr[..., :4].astype(int)).max()), 20)

    def test_unreadable_image_raises_input_error(self):
        bad = os.path.join(self.tmp, "bad.jpg")
        with open(bad, "wb") as f:
            f.write(b"\xff\xd8\xff not a jpeg")
        self.img = bad
        with self.assertRaises(CE.InputImageError):
            self._produce("bad.tif")
        self.assertEqual(self._partials(), [])

    def test_missing_icc_profile_stops_production(self):
        with mock.patch.object(CE, "_SWOP_PROFILE_CACHE", None), \
             mock.patch.object(CE, "_SWOP_ICC_BYTES_CACHE", None), \
             mock.patch.object(CE.os.path, "exists", side_effect=lambda p: not p.endswith(".icc")):
            with self.assertRaises(CE.ColorProfileError):
                CE.process_and_crop(self.img, self.tmpl, os.path.join(self.tmp, "x.tif"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "x.tif")))


class TemplateManagerTests(unittest.TestCase):
    def test_reload_does_not_create_folder(self):
        base = tempfile.mkdtemp()
        try:
            d = os.path.join(base, "Sab")
            tm = TemplateManager(d)
            tm.set_templates_dir(d + "x")
            self.assertEqual(os.listdir(base), [])
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_matching(self):
        tm = TemplateManager(tempfile.gettempdir())
        tm.templates = {"2681": "a", "NEO": "b"}
        self.assertEqual(tm.find_template_for_path("/h/2026-10-05/1/A2681/bid-1/f.png")[0], "2681")
        self.assertIsNone(tm.find_template_for_path("/h/x/26815/f.png")[0])
        self.assertEqual(tm.find_template_for_path("/h/neo case/f.jpg")[0], "NEO")


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.today = datetime.date.today()
        self.inp = os.path.join(self.tmp, "in")
        self.rej = os.path.join(self.tmp, "rej")
        self.out = os.path.join(self.tmp, "out")
        self.tmpl_dir = os.path.join(self.tmp, "tmpl")
        for d in (self.inp, self.rej, self.tmpl_dir):
            os.makedirs(d)
        make_template(os.path.join(self.tmpl_dir, "2681.png"))
        self.logs = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def watcher(self, **kw):
        return OW.OrderWatcher(input_folder=self.inp, rejects_input_folder=self.rej, output_folder=self.out,
                               templates_folder=self.tmpl_dir, log_callback=self.logs.append, **kw)

    def add_order(self, day, name="order.png", gen="1"):
        d = os.path.join(self.inp, str(day), gen, "A2681")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, name)
        make_image(p)
        return p

    def test_auto_cutoff_today_only(self):
        w = self.watcher(days_back_limit=3, auto_today_only=True)
        self.assertEqual(w.auto_cutoff_date(self.today), self.today)
        w.apply_settings(auto_today_only=False)
        self.assertEqual(w.auto_cutoff_date(self.today), self.today - datetime.timedelta(days=3))

    def test_watch_loop_produces_only_today(self):
        today_file = self.add_order(self.today)
        old_file = self.add_order(self.today - datetime.timedelta(days=1), "old.png", "2")
        w = self.watcher(auto_today_only=True)
        with mock.patch.object(OW, "FILE_STABLE_SECONDS", 0.0), mock.patch.object(OW, "WATCH_INTERVAL_SECONDS", 0.3):
            w.start()
            deadline = time.time() + 10
            out_today, _ = w.get_output_path_for_file(today_file, base_input_dir=self.inp)
            while time.time() < deadline and not os.path.exists(out_today):
                time.sleep(0.1)
            time.sleep(0.8)
            w.stop()
        self.assertTrue(os.path.exists(out_today))
        out_old, _ = w.get_output_path_for_file(old_file, base_input_dir=self.inp)
        self.assertFalse(os.path.exists(out_old))
        # Rankinis skenavimas vakarykštį užsakymą vis tiek rodo
        keys = [g["key"] for g in w.scan_available_orders()]
        self.assertTrue(any(str(self.today - datetime.timedelta(days=1)) in k for k in keys))

    def test_file_must_be_stable_before_processing(self):
        p = self.add_order(self.today)
        w = self.watcher()
        with mock.patch.object(OW, "FILE_STABLE_SECONDS", 0.2):
            self.assertFalse(w._is_file_ready(p))   # pirmą kartą tik užfiksuojama
            with open(p, "ab") as f:                # failas dar keliamas
                f.write(b"x" * 10)
            self.assertFalse(w._is_file_ready(p))
            time.sleep(0.3)
            self.assertTrue(w._is_file_ready(p))

    def test_failed_file_retry_backoff(self):
        p = self.add_order(self.today, "broken.png")
        with open(p, "wb") as f:
            f.write(b"not an image")
        w = self.watcher()
        out, _ = w.get_output_path_for_file(p, base_input_dir=self.inp)
        tmpl = os.path.join(self.tmpl_dir, "2681.png")
        self.assertEqual(w.produce_file(p, tmpl, out, False)[0], OW.FAILED)
        self.assertFalse(w._retry_allowed(p))       # palaukiama prieš kitą bandymą
        with mock.patch.object(OW.time, "time", return_value=time.time() + 10_000):
            self.assertTrue(w._retry_allowed(p))
        for _ in OW.RETRY_DELAYS_SECONDS:
            w.produce_file(p, tmpl, out, False)
        with mock.patch.object(OW.time, "time", return_value=time.time() + 10 ** 9):
            self.assertFalse(w._retry_allowed(p))   # išnaudoti bandymai
        time.sleep(0.05)
        make_image(p)                                # failas pakeistas – bandoma iš karto
        os.utime(p, (time.time() + 5, time.time() + 5))
        self.assertTrue(w._retry_allowed(p))
        self.assertEqual(w.produce_file(p, tmpl, out, False)[0], OW.DONE)

    def test_output_errors_keep_retrying(self):
        # READY nepasiekiamas – failas neturi būti apleistas po kelių bandymų
        p = self.add_order(self.today)
        w = self.watcher()
        out, _ = w.get_output_path_for_file(p, base_input_dir=self.inp)
        tmpl = os.path.join(self.tmpl_dir, "2681.png")
        msgs = []
        with mock.patch.object(OW, "process_and_crop", side_effect=OSError("tinklas nepasiekiamas")):
            for _ in range(10):
                msgs.append(w.produce_file(p, tmpl, out, False, background=True)[1])
        self.assertEqual(sum(1 for m in msgs if m), 1)        # žurnale tik pirmą kartą
        with mock.patch.object(OW.time, "time", return_value=time.time() + OW.OUTPUT_RETRY_SECONDS + 1):
            self.assertTrue(w._retry_allowed(p))
        self.assertEqual(w.produce_file(p, tmpl, out, False)[0], OW.DONE)
        self.assertNotIn(p, w._failures)

    def test_empty_output_folder_is_rejected(self):
        w = self.watcher()
        w.apply_settings(output_folder="")
        self.assertEqual(w.output_folder, self.out)
        w.output_folder = ""
        with self.assertRaises(ValueError):
            w.get_output_path_for_file(os.path.join(self.inp, "a.png"))

    def test_print_setting_change_does_not_redo_finished_files(self):
        w = self.watcher()
        w.processed_files.add("jau_pagamintas.png")
        w.apply_settings(choke_pixels=3, target_dpi=300, spot_channel_name="W")
        self.assertEqual(w.choke_pixels, 3)
        self.assertIn("jau_pagamintas.png", w.processed_files)

    def test_same_output_never_written_concurrently(self):
        p = self.add_order(self.today)
        w1, w2 = self.watcher(), self.watcher()
        out, _ = w1.get_output_path_for_file(p, base_input_dir=self.inp)
        tmpl = os.path.join(self.tmpl_dir, "2681.png")
        active, peak, lock = [0], [0], threading.Lock()
        real = OW.process_and_crop

        def slow(*a, **kw):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.3)
            try:
                return real(*a, **kw)
            finally:
                with lock:
                    active[0] -= 1

        results = []
        with mock.patch.object(OW, "process_and_crop", side_effect=slow):
            ts = [threading.Thread(target=lambda ww=ww: results.append(ww.produce_file(p, tmpl, out, False, skip_existing=False)[0]))
                  for ww in (w1, w2, w1)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
        self.assertEqual(peak[0], 1)
        self.assertEqual(results.count(OW.DONE), 1)
        self.assertEqual(results.count(OW.BUSY), 2)

    def test_production_stats_report_failures(self):
        self.add_order(self.today)
        bad = self.add_order(self.today, "bad.png")
        with open(bad, "wb") as f:
            f.write(b"x")
        w = self.watcher()
        groups = w.scan_available_orders()
        stats = w.process_selected_groups(groups)
        self.assertEqual((stats["produced"], stats["failed"], stats["total"]), (1, 1, 2))
        stats = w.process_selected_groups(groups)
        self.assertEqual(stats["skipped"], 1)

    def test_apply_settings_changes_running_watcher(self):
        w = self.watcher(choke_pixels=1)
        w.processed_files.add("x")
        w.apply_settings(choke_pixels=3, output_folder=os.path.join(self.tmp, "kitas"))
        self.assertEqual(w.choke_pixels, 3)
        self.assertEqual(w.output_folder, os.path.join(self.tmp, "kitas"))
        self.assertEqual(w.processed_files, set())

    def test_reject_prefix_match_is_exact(self):
        w = self.watcher()
        other = self.rej + "2"
        out, _ = w.get_output_path_for_file(os.path.join(self.rej, "a.png"))
        self.assertIn("BROKAI", out)
        out2, _ = w.get_output_path_for_file(os.path.join(other, "a.png"))
        self.assertNotIn("BROKAI", out2)

    def test_output_folder_inside_input_is_not_scanned(self):
        self.add_order(self.today)
        w = OW.OrderWatcher(input_folder=self.inp, rejects_input_folder="", output_folder=os.path.join(self.inp, "READY"),
                            templates_folder=self.tmpl_dir, log_callback=self.logs.append)
        w.process_selected_groups(w.scan_available_orders())
        groups = w.scan_available_orders()
        self.assertEqual(sum(len(g["files"]) for g in groups), 1)


if __name__ == "__main__":
    unittest.main()
