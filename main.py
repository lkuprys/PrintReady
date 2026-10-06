import os
import sys
import json
import datetime
import subprocess
from typing import List, Dict, Any, Optional

from PySide6.QtCore import Qt, QObject, QThread, Signal, QTimer, QRectF
from PySide6.QtGui import QIcon, QPixmap, QPainter, QFontMetrics, QAction, QImage
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QFileDialog, QScrollArea, QFrame, QSizePolicy, QStackedWidget, QSpinBox, QCheckBox, QPlainTextEdit
)

from PIL import Image

import ui_theme as T
from order_watcher import OrderWatcher, DEFAULT_STD_INPUT, DEFAULT_REJECTS_INPUT, DEFAULT_OUTPUT
from template_manager import TemplateManager
from crop_engine import process_and_crop, render_preview
import template_options as TO
from updater import APP_VERSION, DEFAULT_GITHUB_REPO, AutoUpdaterManager, ps_quote


def get_app_dir() -> str:
    """Grąžina programos aplanką (kur yra .exe arba .py)."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_resource_path(relative_path: str) -> str:
    """Grąžina teisingą resursų kelią veikiant tiek kaip .py, tiek kaip PyInstaller .exe."""
    base_path = sys._MEIPASS if hasattr(sys, '_MEIPASS') else get_app_dir()
    p_assets = os.path.join(base_path, "assets", relative_path)
    if os.path.exists(p_assets):
        return p_assets
    p_direct = os.path.join(base_path, relative_path)
    if os.path.exists(p_direct):
        return p_direct
    return p_assets


def get_config_path() -> str:
    return os.path.join(get_app_dir(), "config.json")


def load_saved_config() -> Dict[str, Any]:
    cfg_p = get_config_path()
    default_tmpl = os.path.join(get_app_dir(), "Sablonai")
    if not os.path.exists(default_tmpl) and os.path.exists(r"C:\Podbase\PrintReady\Sablonai"):
        default_tmpl = r"C:\Podbase\PrintReady\Sablonai"

    defaults = {
        "input_folder": DEFAULT_STD_INPUT,
        "rejects_input_folder": DEFAULT_REJECTS_INPUT,
        "output_folder": DEFAULT_OUTPUT,
        "templates_folder": default_tmpl,
        "choke": 1,
        "dpi": 300,
        "spot_name": "W",
        "solidity": 5,
        "skip_existing": True,
        "max_workers": 4,
        "days_back_limit": 3,
        "github_repo": DEFAULT_GITHUB_REPO,
        "auto_check_updates": True,
        "auto_watch_enabled": True,
        "auto_today_only": True,
        "theme": "light"
    }

    if os.path.exists(cfg_p):
        try:
            with open(cfg_p, "r", encoding="utf-8") as f:
                defaults.update(json.load(f))
        except Exception:
            pass
    return defaults


def save_config(config_dict: Dict[str, Any]):
    """Įrašo nustatymus per laikiną failą, kad nutrūkus įrašymui config.json nesugestų."""
    cfg_p = get_config_path()
    tmp_p = cfg_p + ".tmp"
    try:
        with open(tmp_p, "w", encoding="utf-8") as f:
            json.dump(config_dict, f, indent=4, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_p, cfg_p)
    except Exception:
        try:
            os.remove(tmp_p)
        except OSError:
            pass


def open_in_explorer(path: str):
    if sys.platform.startswith("win"):
        subprocess.Popen(f'explorer "{path}"')
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


# =========================================================================
# Gijų saugus žurnalo signalo siuntėjas ir foninės gijos
# =========================================================================
class LogEmitter(QObject):
    log_signal = Signal(str)


class ScanWorker(QThread):
    finished = Signal(list)
    log_msg = Signal(str)

    def __init__(self, watcher: OrderWatcher):
        super().__init__()
        self.watcher = watcher

    def run(self):
        try:
            self.finished.emit(self.watcher.scan_available_orders())
        except Exception as e:
            self.log_msg.emit(f"❌ Klaida skenuojant: {e}")
            self.finished.emit([])


class ProductionWorker(QThread):
    progress = Signal(int, int, str)
    finished = Signal(object)  # {"produced", "skipped", "failed", "total"}
    log_msg = Signal(str)

    def __init__(self, watcher: OrderWatcher, groups: List[Dict[str, Any]], skip_existing: bool = True, max_workers: int = 4):
        super().__init__()
        self.watcher = watcher
        self.groups = groups
        self.skip_existing = skip_existing
        self.max_workers = max_workers

    def run(self):
        try:
            stats = self.watcher.process_selected_groups(
                self.groups,
                progress_callback=lambda cur, total, fname: self.progress.emit(cur, total, fname),
                skip_existing=self.skip_existing,
                max_workers=self.max_workers
            )
            self.finished.emit(stats)
        except Exception as e:
            self.log_msg.emit(f"❌ Gamybos klaida: {e}")
            self.finished.emit({"produced": 0, "skipped": 0, "failed": 0, "total": 0, "error": str(e)})


class SingleFileWorker(QThread):
    finished = Signal(bool, str, str)  # success, out_file, error_msg

    def __init__(self, img_path: str, tmpl_path: str, out_path: str, choke: int, spot: str, solidity: int, dpi: int):
        super().__init__()
        self.img_path = img_path
        self.tmpl_path = tmpl_path
        self.out_path = out_path
        self.choke = choke
        self.spot = spot
        self.solidity = solidity
        self.dpi = dpi

    def run(self):
        try:
            process_and_crop(
                image_path=self.img_path,
                template_path=self.tmpl_path,
                output_path=self.out_path,
                choke_pixels=self.choke,
                spot_channel_name=self.spot,
                solidity=self.solidity,
                target_dpi=self.dpi
            )
            self.finished.emit(True, self.out_path, "")
        except Exception as e:
            self.finished.emit(False, self.out_path, str(e))


# =========================================================================
# Bendri sąsajos elementai
# =========================================================================
class ElidedLabel(QLabel):
    """Ilgas kelias sutrumpinamas per vidurį (…), pilnas rodomas užvedus pelę."""

    def __init__(self, text: str = "", role: str = "secondary", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setProperty("role", role)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(80)
        self._full = ""
        self.setText(text)

    def setText(self, text: str):
        self._full = text or ""
        self.setToolTip(self._full)
        super().setText(self._full)
        self.update()

    def text(self) -> str:
        return self._full

    def minimumSizeHint(self):
        sz = super().minimumSizeHint()
        sz.setWidth(80)
        return sz

    def sizeHint(self):
        sz = super().sizeHint()
        sz.setWidth(min(sz.width(), 600))
        return sz

    def paintEvent(self, _):
        p = QPainter(self)
        p.setPen(self.palette().color(self.foregroundRole()))
        p.setFont(self.font())
        elided = QFontMetrics(self.font()).elidedText(self._full, Qt.TextElideMode.ElideMiddle, self.width())
        p.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)


def page_header(title: str, subtitle: str = "") -> QVBoxLayout:
    col = QVBoxLayout()
    col.setSpacing(2)
    col.addWidget(T.label(title, "title"))
    if subtitle:
        col.addWidget(T.label(subtitle, "secondary"))
    return col


def field_label(text: str) -> QLabel:
    return T.label(text, "label")


def make_spin(lo: int, hi: int, value: int, suffix: str, width: int = 140) -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    # Reikšmė priimama tik baigus įvesti (ne po kiekvieno skaitmens)
    s.setKeyboardTracking(False)
    s.setValue(value)
    s.setSuffix(suffix)
    s.setFixedWidth(width)
    s.setFixedHeight(40)
    f = s.font()
    T.set_tabular(f)
    s.setFont(f)
    return s


def make_line_edit(text: str = "", placeholder: str = "") -> QLineEdit:
    e = QLineEdit(text)
    e.setPlaceholderText(placeholder)
    e.setFixedHeight(40)
    e.setCursorPosition(0)  # ilgas kelias rodomas nuo pradžios
    return e


# =========================================================================
# 1. Užsakymai
# =========================================================================
STATUS_VIEW = {
    "ALL_READY": ("Paruošta", "success"),
    "PARTIAL": ("Dalinai", "warning"),
    "NEW": ("Nauja", "info"),
}

# (antraštė, plotis; 0 = užima likusią vietą)
ORDER_COLUMNS = [("", 28), ("Data", 108), ("Generacija", 96), ("Modelis", 0), ("Šaltinis", 124),
                 ("Paruošta", 92), ("Būsena", 132), ("", 96)]


class StatCard(QFrame):
    def __init__(self, title: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("StatCard")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 12, 18, 12)
        lay.setSpacing(2)
        lay.addWidget(T.label(title, "section"))
        self.value = T.label("—", "stat", tabular=True)
        lay.addWidget(self.value)
        self.hint = T.label("", "muted")
        lay.addWidget(self.hint)

    def set(self, value, hint: str = ""):
        self.value.setText(str(value))
        self.hint.setText(hint)


class OrdersInterface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("ordersInterface")
        self.main_app = parent

        self.scanned_groups: List[Dict[str, Any]] = []
        self.group_cards: Dict[str, QFrame] = {}
        self.group_checkboxes: Dict[str, QCheckBox] = {}
        self.quick_buttons: List[Any] = []
        self.prod_worker: Optional[ProductionWorker] = None
        self.scan_worker: Optional[ScanWorker] = None
        self._has_scanned = False
        self._days_back = 3
        self._today_only = True

        self._init_ui()

    # ------------------------------------------------------------------ UI
    def _init_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 24)
        lay.setSpacing(16)

        # Antraštė
        head = QHBoxLayout()
        head.addLayout(page_header("Užsakymai", "Nuskenuokite aplankus ir pagaminkite spaudos failus."), 1)
        self.status_badge = T.Badge("Paruošta darbui", "neutral")
        head.addWidget(self.status_badge, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)

        # Skaičiai
        stats = QHBoxLayout()
        stats.setSpacing(12)
        self.stat_new = StatCard("Nauji failai")
        self.stat_ready = StatCard("Paruošta")
        self.stat_rejects = StatCard("Rejected failai")
        self.stat_templates = StatCard("Šablonai")
        for s in (self.stat_new, self.stat_ready, self.stat_rejects, self.stat_templates):
            stats.addWidget(s, 1)
        lay.addLayout(stats)

        # Aplankai + automatinė gamyba
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(self._build_sources_card(), 3)
        row.addWidget(self._build_auto_card(), 2)
        lay.addLayout(row)

        # Sąrašas
        lay.addWidget(self._build_list_card(), 1)

        self._refresh_stats()
        self._update_empty_state()

    def _build_sources_card(self) -> QFrame:
        card = T.Card(padding=16, spacing=0)
        card.body.addWidget(T.section_header("Aplankai"))
        card.body.addSpacing(4)
        cfg = load_saved_config()
        self.gen_dir_lbl = self._source_row(card, "Generacijos", cfg.get("input_folder", DEFAULT_STD_INPUT),
                                            lambda: self.main_app.settings_interface._pick_in())
        card.body.addWidget(T.divider())
        self.rej_dir_lbl = self._source_row(card, "Rejected", cfg.get("rejects_input_folder", DEFAULT_REJECTS_INPUT),
                                            lambda: self.main_app.settings_interface._pick_rejects())
        card.body.addWidget(T.divider())
        self.out_dir_lbl = self._source_row(card, "READY", cfg.get("output_folder", DEFAULT_OUTPUT),
                                            lambda: self.main_app.settings_interface._pick_out())
        return card

    def _source_row(self, card: T.Card, title: str, path: str, on_pick) -> ElidedLabel:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 4, 0, 4)
        h.setSpacing(12)
        t = T.label(title, "label")
        t.setFixedWidth(92)
        h.addWidget(t)
        lbl = ElidedLabel(path or "(nenurodyta)")
        h.addWidget(lbl, 1)
        b = T.button("Pakeisti", size="small")
        b.clicked.connect(on_pick)
        h.addWidget(b)
        card.body.addWidget(w)
        return lbl

    def _build_auto_card(self) -> QFrame:
        card = T.Card(padding=16, spacing=8)
        card.body.addWidget(T.section_header("Automatinė gamyba"))
        sw = T.LabeledSwitch("Fono stebėjimas", "")
        self.watch_switch = sw.switch
        self.watch_switch.checkedChanged.connect(self._on_watch_switch_toggled)
        card.body.addWidget(sw)
        self.auto_desc = T.label("", "secondary", wrap=True)
        card.body.addWidget(self.auto_desc)
        card.body.addWidget(T.divider())
        last = QHBoxLayout()
        last.addWidget(T.label("Paskutinis pagamintas", "muted"))
        last.addStretch(1)
        self.last_auto_lbl = T.label("—", "secondary", tabular=True)
        last.addWidget(self.last_auto_lbl)
        card.body.addLayout(last)
        card.body.addStretch(1)
        return card

    def _build_list_card(self) -> QFrame:
        card = T.Card(padding=0, spacing=0)

        # Antraštė su veiksmais
        top = QHBoxLayout()
        top.setContentsMargins(20, 18, 20, 12)
        top.setSpacing(8)
        tcol = QVBoxLayout()
        tcol.setSpacing(2)
        tcol.addWidget(T.label("Užsakymų sąrašas", "h2"))
        self.list_hint = T.label("", "secondary")
        tcol.addWidget(self.list_hint)
        top.addLayout(tcol, 1)
        self.scan_btn = T.button("Skenuoti", icon_name="refresh")
        self.scan_btn.clicked.connect(self._scan_orders)
        top.addWidget(self.scan_btn)
        self.produce_all_btn = T.button("Gaminti visus naujus")
        self.produce_all_btn.setToolTip("Pažymi visus naujus ir dalinai paruoštus užsakymus ir juos pagamina")
        self.produce_all_btn.clicked.connect(self._produce_all_new)
        top.addWidget(self.produce_all_btn)
        self.produce_btn = T.button("Gaminti pažymėtus (0)", "primary", icon_name="play")
        self.produce_btn.clicked.connect(self._produce_selected)
        top.addWidget(self.produce_btn)
        card.body.addLayout(top)

        # Paieška, filtras, pažymėjimo suvestinė
        tools = QHBoxLayout()
        tools.setContentsMargins(20, 0, 20, 12)
        tools.setSpacing(12)
        self.search_entry = make_line_edit("", "Ieškoti: modelis, data, failo pavadinimas…")
        self.search_entry.setFixedWidth(340)
        self._search_action = QAction(self.search_entry)
        self.search_entry.addAction(self._search_action, QLineEdit.ActionPosition.LeadingPosition)
        self.search_entry.setClearButtonEnabled(True)
        self.search_entry.textChanged.connect(self._filter_groups)
        tools.addWidget(self.search_entry)
        self.filter_seg = T.SegmentedControl([("all", "Visi"), ("new", "Nauji"), ("ready", "Paruošti"),
                                              ("rejects", "Rejected")])
        self.filter_seg.changed.connect(lambda _: self._filter_groups())
        tools.addWidget(self.filter_seg)
        tools.addStretch(1)
        self.selection_lbl = T.label("", "secondary", tabular=True)
        tools.addWidget(self.selection_lbl)
        card.body.addLayout(tools)
        self._refresh_search_icon()
        T.on_theme_changed(self._refresh_search_icon)

        # Progresas
        self.prog_box = QWidget()
        pb = QVBoxLayout(self.prog_box)
        pb.setContentsMargins(20, 0, 20, 12)
        pb.setSpacing(6)
        self.progress_bar = T.ThinProgress()
        pb.addWidget(self.progress_bar)
        self.progress_lbl = T.label("", "secondary", tabular=True)
        pb.addWidget(self.progress_lbl)
        self.prog_box.setVisible(False)
        card.body.addWidget(self.prog_box)

        # Lentelės antraštė
        header = QFrame()
        header.setObjectName("TableHeader")
        hh = QHBoxLayout(header)
        hh.setContentsMargins(20, 8, 20, 8)
        hh.setSpacing(12)
        self.select_all_chk = QCheckBox()
        self.select_all_chk.setToolTip("Pažymėti / nuimti visus rodomus")
        self.select_all_chk.clicked.connect(self._toggle_select_visible)
        for i, (title, width) in enumerate(ORDER_COLUMNS):
            if i == 0:
                w = self.select_all_chk
            else:
                w = T.label(title, "th")
            if width:
                w.setFixedWidth(width)
                hh.addWidget(w)
            else:
                hh.addWidget(w, 1)
        card.body.addWidget(header)

        # Eilutės
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("TableBody")
        self.scroll_content.setStyleSheet("QWidget#TableBody { background: transparent; }")
        self.scroll_area.setStyleSheet("QScrollArea { background: transparent; }")
        self.scroll_area.viewport().setAutoFillBackground(False)
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(0, 0, 0, 8)
        self.scroll_layout.setSpacing(0)

        # Tuščia būsena
        self.empty_box = QWidget()
        eb = QVBoxLayout(self.empty_box)
        eb.setContentsMargins(20, 40, 20, 40)
        eb.setSpacing(12)
        self.empty_lbl = T.label("", "empty", wrap=True)
        self.empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        eb.addWidget(self.empty_lbl)
        self.empty_btn = T.button("Skenuoti", icon_name="refresh")
        self.empty_btn.clicked.connect(self._scan_orders)
        eb.addWidget(self.empty_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        self.scroll_layout.addWidget(self.empty_box)
        self.scroll_layout.addStretch(1)
        self.scroll_area.setWidget(self.scroll_content)
        card.body.addWidget(self.scroll_area, 1)
        return card

    def _refresh_search_icon(self):
        self._search_action.setIcon(T.icon("search", T.palette()["muted"], 16))

    # ------------------------------------------------------------------ būsena
    def set_status(self, text: str, kind: str = "neutral"):
        self.status_badge.set(text, kind)

    def set_watch_state(self, on: bool):
        self.watch_switch.blockSignals(True)
        self.watch_switch.setChecked(on)
        self.watch_switch.blockSignals(False)
        self._update_auto_desc()

    def _update_auto_desc(self):
        on = self.watch_switch.isChecked()
        scope = "tik šiandienos" if self._today_only else f"šiandienos ir paskutinių {self._days_back} d."
        if on:
            self.auto_desc.setText(f"Įjungta. Nauji {scope} failai gaminami automatiškai, kai tik baigia kopijuotis.")
        else:
            self.auto_desc.setText("Išjungta. Failus gaminkite rankiniu būdu iš sąrašo apačioje.")

    def set_last_auto(self, file_name: str):
        self.last_auto_lbl.setText(f"{datetime.datetime.now():%H:%M} · {file_name}")

    def update_source_folders(self, generations: str, rejected: str, ready: Optional[str] = None):
        self.gen_dir_lbl.setText(generations or "(nenurodyta)")
        self.rej_dir_lbl.setText(rejected or "(nenurodyta – Rejected neskenuojami)")
        if ready is not None:
            self.out_dir_lbl.setText(ready or "(nenurodyta)")

    def update_templates_kpi(self, count: int):
        self.stat_templates.set(count, "Šablonų aplanke nėra .png failų!" if count == 0 else "aktyvūs")

    def update_date_kpi(self, days_back: int, auto_today_only: bool = True):
        self._days_back = days_back
        self._today_only = auto_today_only
        self.list_hint.setText("Rodomi tik šiandienos užsakymai." if days_back == 0
                               else f"Rodomi šiandienos ir paskutinių {days_back} d. užsakymai.")
        self._update_auto_desc()

    def _refresh_stats(self):
        groups = self.scanned_groups
        if not self._has_scanned:
            for s in (self.stat_new, self.stat_ready, self.stat_rejects):
                s.set("—", "dar nenuskenuota")
            return
        total = sum(len(g["files"]) for g in groups)
        ready = sum(len(g.get("converted_files", [])) for g in groups)
        rej = sum(len(g["files"]) for g in groups if g.get("is_reject"))
        rej_new = sum(len(g["files"]) - len(g.get("converted_files", [])) for g in groups if g.get("is_reject"))
        self.stat_new.set(total - ready, "laukia gamybos")
        self.stat_ready.set(ready, f"iš {total}")
        self.stat_rejects.set(rej, f"{rej_new} nepagaminti" if rej else "nėra")
        counts = {
            "all": len(groups),
            "new": sum(1 for g in groups if g.get("status") in ("NEW", "PARTIAL")),
            "ready": sum(1 for g in groups if g.get("status") == "ALL_READY"),
            "rejects": sum(1 for g in groups if g.get("is_reject")),
        }
        for key, text in (("all", "Visi"), ("new", "Nauji"), ("ready", "Paruošti"), ("rejects", "Rejected")):
            self.filter_seg.set_text(key, f"{text} {counts[key]}")

    def _update_empty_state(self, visible_count: Optional[int] = None):
        if not self._has_scanned:
            self.empty_lbl.setText("Spauskite „Skenuoti“, kad pamatytumėte užsakymus.")
            self.empty_btn.setVisible(True)
            self.empty_box.setVisible(True)
        elif not self.scanned_groups:
            self.empty_lbl.setText("Generacijų ir Rejected aplankuose užsakymų su šablonais nerasta.")
            self.empty_btn.setVisible(True)
            self.empty_box.setVisible(True)
        elif visible_count == 0:
            self.empty_lbl.setText("Pagal paiešką ar filtrą užsakymų nerasta.")
            self.empty_btn.setVisible(False)
            self.empty_box.setVisible(True)
        else:
            self.empty_box.setVisible(False)

    # ------------------------------------------------------------------ skenavimas
    def is_scanning(self) -> bool:
        w = self.scan_worker
        return w is not None and w.isRunning()

    def _scan_orders(self):
        # Kol vyksta skenavimas ar gamyba, naujas skenavimas nepradedamas
        # (veikiančios gijos pakeitimas nulauždavo programą)
        if self.is_scanning() or self.is_producing():
            return
        self.set_status("Skenuojama…", "info")
        self._set_production_controls_enabled(False)

        watcher = self.main_app.get_watcher_instance()
        self.scan_worker = ScanWorker(watcher)
        self.scan_worker.finished.connect(self._on_scan_finished)
        self.scan_worker.log_msg.connect(self.main_app.log)
        self.scan_worker.start()

    def _on_scan_finished(self, groups: List[Dict[str, Any]]):
        try:
            self._show_scanned_groups(groups)
        finally:
            # Mygtukai įjungiami net jei rodant sąrašą įvyko klaida
            self._set_production_controls_enabled(not self.is_producing())

    def _show_scanned_groups(self, groups: List[Dict[str, Any]]):
        self._has_scanned = True
        self.scanned_groups = groups
        self.group_cards.clear()
        self.group_checkboxes.clear()
        self.quick_buttons.clear()

        # Pašaliname senas eilutes (tuščios būsenos blokas ir tarpas lieka)
        for i in reversed(range(self.scroll_layout.count())):
            wdg = self.scroll_layout.itemAt(i).widget()
            if wdg is not None and wdg is not self.empty_box:
                self.scroll_layout.takeAt(i)
                wdg.deleteLater()

        for g in groups:
            row = self._build_row(g)
            self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, row)

        self._refresh_stats()
        self._filter_groups()
        if self.is_producing():
            pass
        elif groups:
            self.set_status("Paruošta gamybai", "neutral")
        else:
            self.set_status("Užsakymų nerasta", "neutral")

        if groups:
            new_files = sum(len(g["files"]) - len(g.get("converted_files", [])) for g in groups)
            missing = sum(1 for g in groups if not g.get("has_template"))
            text = f"Rasta grupių: {len(groups)}. Naujų failų: {new_files}."
            if missing:
                text += f" {missing} grupėms trūksta šablono."
            T.notify(self, "success", "Skenavimas baigtas", text)

    def _build_row(self, g: Dict[str, Any]) -> QFrame:
        row = QFrame()
        row.setObjectName("TableRow")
        row.setFixedHeight(52)
        h = QHBoxLayout(row)
        h.setContentsMargins(20, 0, 20, 0)
        h.setSpacing(12)
        has_tmpl = g.get("has_template", True)

        chk = QCheckBox()
        chk.setChecked(has_tmpl and g.get("status") != "ALL_READY")
        chk.setEnabled(has_tmpl)
        chk.stateChanged.connect(self._update_counter)

        model_lbl = T.label(g.get("model", ""), "cell")
        f = model_lbl.font()
        f.setWeight(f.Weight.DemiBold)
        model_lbl.setFont(f)
        model_lbl.setToolTip(f"Šablonas: {g.get('template_name')}.png")
        model = QWidget()
        ml = QHBoxLayout(model)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(8)
        ml.addWidget(model_lbl)
        rules = TO.get_for_template(g["template_path"]).labels() if g.get("template_path") else []
        if rules:
            rb = T.Badge(" · ".join(rules), "neutral", dot=False)
            rb.setToolTip("Šablono taisyklės (keičiamos skiltyje „Šablonai“)")
            ml.addWidget(rb)
        ml.addStretch(1)

        if g.get("is_reject"):
            source = T.Badge("Rejected", "error")
        else:
            source = T.label("Generacijos", "cell2")

        files_lbl = T.label("", "cell", tabular=True)
        badge = T.Badge("", "info")
        quick_btn = T.button("Gaminti", size="small")
        quick_btn.setEnabled(has_tmpl)
        quick_btn._has_template = has_tmpl
        quick_btn.clicked.connect(lambda _=False, grp=g: self._produce_single_group(grp))

        cells = [chk, T.label(g.get("date", ""), "cell", tabular=True),
                 T.label(g.get("generation", ""), "cell2", tabular=True), model, source, files_lbl, badge, quick_btn]
        for (title, width), w in zip(ORDER_COLUMNS, cells):
            if width:
                holder = QWidget()
                holder.setFixedWidth(width)
                hl = QHBoxLayout(holder)
                hl.setContentsMargins(0, 0, 0, 0)
                hl.addWidget(w)
                hl.addStretch(1)
                h.addWidget(holder)
            else:
                h.addWidget(w, 1)

        row._badge = badge
        row._files_lbl = files_lbl
        row._group_data = g
        self._refresh_row(row, g)
        self.group_cards[g["key"]] = row
        self.group_checkboxes[g["key"]] = chk
        self.quick_buttons.append(quick_btn)
        return row

    def _refresh_row(self, row: QFrame, g: Dict[str, Any]):
        total = len(g["files"])
        conv = len(g.get("converted_files", []))
        row._files_lbl.setText(f"{conv} / {total}")
        if not g.get("has_template", True):
            row._badge.set("Nėra šablono", "error")
        else:
            text, kind = STATUS_VIEW.get(g.get("status", "NEW"), ("Nauja", "info"))
            row._badge.set(text, kind)

    # ------------------------------------------------------------------ filtrai ir žymėjimas
    def _matches(self, g: Dict[str, Any], query: str, flt: str) -> bool:
        if flt == "new" and g.get("status") not in ("NEW", "PARTIAL"):
            return False
        if flt == "ready" and g.get("status") != "ALL_READY":
            return False
        if flt == "rejects" and not g.get("is_reject"):
            return False
        if not query:
            return True
        return (query in g.get("date", "").lower()
                or query in g.get("generation", "").lower()
                or query in g.get("model", "").lower()
                or query in (g.get("template_name") or "").lower()
                or query in g.get("key", "").lower()
                or any(query in os.path.basename(f).lower() for f in g.get("files", []))
                or (("brok" in query or "reject" in query) and g.get("is_reject", False))
                or ("nauj" in query and g.get("status") in ("NEW", "PARTIAL")))

    def _filter_groups(self):
        query = self.search_entry.text().strip().lower()
        flt = self.filter_seg.value()
        visible = 0
        for g in self.scanned_groups:
            row = self.group_cards.get(g["key"])
            if not row:
                continue
            ok = self._matches(g, query, flt)
            row.setVisible(ok)
            visible += int(ok)
        self._update_empty_state(visible)
        self._update_counter()

    def _visible_selectable(self):
        for g in self.scanned_groups:
            row = self.group_cards.get(g["key"])
            chk = self.group_checkboxes.get(g["key"])
            if row and chk and not row.isHidden() and g.get("has_template"):
                yield g, chk

    def _update_counter(self):
        selected_files = 0
        selected_groups = 0
        for g in self.scanned_groups:
            chk = self.group_checkboxes.get(g["key"])
            if chk and chk.isChecked() and g.get("has_template"):
                selected_groups += 1
                selected_files += len(g["files"])
        self.produce_btn.setText(f"Gaminti pažymėtus ({selected_files})")
        self.selection_lbl.setText(f"Pažymėta: {selected_groups} gr. · {selected_files} fail." if self.scanned_groups else "")
        vis = list(self._visible_selectable())
        self.select_all_chk.blockSignals(True)
        self.select_all_chk.setChecked(bool(vis) and all(chk.isChecked() for _, chk in vis))
        self.select_all_chk.blockSignals(False)

    def _toggle_select_visible(self):
        vis = list(self._visible_selectable())
        if vis and all(chk.isChecked() for _, chk in vis):
            self._deselect_all()
        else:
            self._select_all()

    def _select_only_new(self):
        """Pažymi tik tas rodomas grupes, kurios dar nėra pilnai konvertuotos."""
        for g, chk in self._visible_selectable():
            chk.setChecked(g.get("status") in ("NEW", "PARTIAL"))
        self._update_counter()

    def _select_all(self):
        for _, chk in self._visible_selectable():
            chk.setChecked(True)
        self._update_counter()

    def _deselect_all(self):
        for g in self.scanned_groups:
            row = self.group_cards.get(g["key"])
            chk = self.group_checkboxes.get(g["key"])
            if row and chk and not row.isHidden():
                chk.setChecked(False)
        self._update_counter()

    # ------------------------------------------------------------------ gamyba
    def _produce_single_group(self, group: Dict[str, Any]):
        self._start_production([group])

    def _produce_selected(self):
        to_produce = [g for g in self.scanned_groups
                      if self.group_checkboxes.get(g["key"]) and self.group_checkboxes[g["key"]].isChecked()
                      and g.get("has_template")]
        if not to_produce:
            T.notify(self, "warning", "Nieko nepažymėta", "Pažymėkite bent vieną užsakymą su šablonu.")
            return
        self._start_production(to_produce)

    def _produce_all_new(self):
        if not self.scanned_groups:
            T.notify(self, "info", "Sąrašas tuščias", "Pirmiausia nuskenuokite užsakymus.")
            return
        self.filter_seg.set_value("all")
        self.search_entry.blockSignals(True)
        self.search_entry.clear()
        self.search_entry.blockSignals(False)
        self._filter_groups()
        self._select_only_new()
        to_produce = [g for g in self.scanned_groups
                      if self.group_checkboxes.get(g["key"]) and self.group_checkboxes[g["key"]].isChecked()
                      and g.get("has_template")]
        if to_produce:
            self._start_production(to_produce)
        else:
            T.notify(self, "info", "Nėra naujų užsakymų", "Visi rodomi užsakymai jau paruošti.")

    def _on_watch_switch_toggled(self, checked: bool):
        if self.main_app:
            if checked:
                self.main_app.start_watcher()
            else:
                self.main_app.stop_watcher()

    def on_watcher_file_completed(self, file_path: str, out_path: str, is_reject: bool):
        """Atnaujina eilučių būseną realiu laiku, kai failas pagaminamas (fone ar rankiniu būdu)."""
        updated_any = False
        for g in self.scanned_groups:
            if file_path in g.get("files", []):
                g.setdefault("converted_files", [])
                if file_path not in g["converted_files"]:
                    g["converted_files"].append(file_path)
                if file_path in g.get("new_files", []):
                    g["new_files"].remove(file_path)
                total_g = len(g["files"])
                conv_g = len(g["converted_files"])
                if conv_g >= total_g and total_g > 0:
                    g["status"] = "ALL_READY"
                elif conv_g > 0:
                    g["status"] = "PARTIAL"
                row = self.group_cards.get(g["key"])
                if row is not None:
                    self._refresh_row(row, g)
                updated_any = True
        if updated_any:
            self._refresh_stats()
            self._update_counter()

    def is_producing(self) -> bool:
        return self.prod_worker is not None and self.prod_worker.isRunning()

    def _set_production_controls_enabled(self, enabled: bool):
        for b in (self.produce_btn, self.produce_all_btn, self.scan_btn, self.empty_btn, self.select_all_chk):
            b.setEnabled(enabled)
        for b in self.quick_buttons:
            b.setEnabled(enabled and getattr(b, "_has_template", True))

    def _start_production(self, groups: List[Dict[str, Any]]):
        # Vienu metu vykdoma tik viena gamyba (antras paspaudimas anksčiau nulauždavo programą)
        if self.is_producing() or self.is_scanning():
            T.notify(self, "warning", "Gamyba jau vyksta", "Palaukite, kol baigsis dabartinis darbas.")
            return
        self._set_production_controls_enabled(False)
        self.prog_box.setVisible(True)
        self.progress_bar.setValue(0)
        self.progress_lbl.setText("Ruošiamasi gamybai…")
        self.set_status("Gaminama…", "warning")

        watcher = self.main_app.get_watcher_instance()
        settings = self.main_app.get_current_settings()
        self.prod_worker = ProductionWorker(watcher, groups,
                                            skip_existing=settings.get("skip_existing", True),
                                            max_workers=settings.get("max_workers", 4))
        self.prod_worker.progress.connect(self._on_progress)
        self.prod_worker.finished.connect(self._on_production_finished)
        self.prod_worker.log_msg.connect(self.main_app.log)
        self.prod_worker.start()

    def _on_progress(self, cur: int, total: int, fname: str):
        pct = int((cur / total) * 100) if total > 0 else 0
        self.progress_bar.setValue(pct)
        self.progress_lbl.setText(f"Gaminama {cur} iš {total} ({pct} %) · {fname}")

    def _on_production_finished(self, stats: Dict[str, Any]):
        self._set_production_controls_enabled(not self.is_scanning())
        produced = int(stats.get("produced", 0))
        skipped = int(stats.get("skipped", 0))
        failed = int(stats.get("failed", 0))
        summary = f"Pagaminta: {produced} · Jau buvo paruošti: {skipped}"
        if failed:
            summary += f" · Nepavyko: {failed}"
        self.progress_bar.setValue(100)
        self.progress_lbl.setText(summary)

        if stats.get("error"):
            self.set_status("Klaida", "error")
            T.notify(self, "error", "Gamybos klaida", str(stats["error"]), 8000)
        elif failed:
            self.set_status(f"Baigta su klaidomis ({failed})", "warning")
            T.notify(self, "warning", "Gamyba baigta su klaidomis",
                     f"{summary}. Priežastys – skiltyje „Žurnalas“.", 0)
        else:
            self.set_status("Gamyba baigta", "success")
            T.notify(self, "success", "Gamyba baigta", summary)


# =========================================================================
# 2. Vieno failo įrankis
# =========================================================================
class SingleFileInterface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("singleFileInterface")
        self.main_app = parent
        self.worker: Optional[SingleFileWorker] = None
        self._auto_tmpl = True
        self._init_ui()

    def _init_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 24)
        lay.setSpacing(16)
        lay.addLayout(page_header("Vieno failo įrankis",
                                  "Rankinis vienos nuotraukos paruošimas pagal pasirinktą šabloną (pvz. perspausdinimui)."))

        card = T.Card(padding=20, spacing=16)
        card.setMaximumWidth(860)

        self.img_path_entry = make_line_edit("", "Pasirinkite kliento nuotrauką (PNG, JPG, TIF)")
        self.img_path_entry.editingFinished.connect(self._suggest_template)
        card.body.addLayout(self._field("Kliento nuotrauka", self.img_path_entry, "Naršyti…", self._select_cust_img))

        self.tmpl_path_entry = make_line_edit("", "Pasirinkite šablono .png failą")
        self.tmpl_path_entry.textEdited.connect(lambda _: setattr(self, "_auto_tmpl", False))
        card.body.addLayout(self._field("Šablonas", self.tmpl_path_entry, "Pasirinkti…", self._select_tmpl_img))
        self.tmpl_hint = T.label("Parenkamas automatiškai pagal nuotraukos pavadinimą ar aplanką.", "muted")
        card.body.addWidget(self.tmpl_hint)

        self.out_path_entry = make_line_edit(load_saved_config().get("output_folder", DEFAULT_OUTPUT))
        card.body.addLayout(self._field("Išsaugoti į", self.out_path_entry, "Pasirinkti…", self._select_out_dir))

        card.body.addWidget(T.divider())
        bottom = QHBoxLayout()
        self.params_lbl = T.label("", "secondary", tabular=True)
        bottom.addWidget(self.params_lbl, 1)
        self.process_btn = T.button("Sukurti spaudos failą", "primary", icon_name="play", size="large")
        self.process_btn.clicked.connect(self._process_single)
        bottom.addWidget(self.process_btn)
        card.body.addLayout(bottom)
        lay.addWidget(card)

        self.result = T.Alert("success", "")
        self.result.setMaximumWidth(860)
        self.open_result_btn = T.button("Atidaryti aplanką", size="small")
        self.open_result_btn.clicked.connect(self._open_result_folder)
        self.result.actions.addWidget(self.open_result_btn)
        self.result.setVisible(False)
        lay.addWidget(self.result)
        lay.addStretch(1)
        self._last_out = ""

    def _field(self, title: str, entry: QLineEdit, btn_text: str, on_click) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(6)
        col.addWidget(field_label(title))
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(entry, 1)
        b = T.button(btn_text)
        b.clicked.connect(on_click)
        row.addWidget(b)
        col.addLayout(row)
        return col

    def showEvent(self, e):
        super().showEvent(e)
        if self.main_app and hasattr(self.main_app, "get_current_settings"):
            s = self.main_app.get_current_settings()
            self.params_lbl.setText(f"CMYK + W · {s['dpi']} DPI · choke {s['choke']} px · tankis {s['solidity']} %")

    def _select_cust_img(self):
        f, _ = QFileDialog.getOpenFileName(self, "Pasirinkite kliento nuotrauką", "",
                                           "Nuotraukos (*.png *.jpg *.jpeg *.webp *.tif *.tiff)")
        if f:
            self.img_path_entry.setText(f)
            self.img_path_entry.setCursorPosition(0)
            self._suggest_template()

    def _suggest_template(self):
        img = self.img_path_entry.text().strip()
        if not img or not (self._auto_tmpl or not self.tmpl_path_entry.text().strip()):
            return
        name, path = self.main_app.template_manager.find_template_for_path(img)
        if path:
            self.tmpl_path_entry.setText(path)
            self.tmpl_path_entry.setCursorPosition(0)
            self._auto_tmpl = True
            self.tmpl_hint.setText(f"Parinktas šablonas: {name}.png")
        else:
            self.tmpl_hint.setText("Šablonas pagal pavadinimą nerastas – pasirinkite rankiniu būdu.")

    def _select_tmpl_img(self):
        initial = self.main_app.settings_interface.committed("templates_folder")
        f, _ = QFileDialog.getOpenFileName(self, "Pasirinkite šablono .png failą", initial, "PNG šablonai (*.png)")
        if f:
            self.tmpl_path_entry.setText(f)
            self.tmpl_path_entry.setCursorPosition(0)
            self._auto_tmpl = False
            self.tmpl_hint.setText("Šablonas pasirinktas rankiniu būdu.")

    def _select_out_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Pasirinkite išvesties aplanką", self.out_path_entry.text())
        if d:
            self.out_path_entry.setText(d)

    def _process_single(self):
        img_p = self.img_path_entry.text().strip()
        tmpl_p = self.tmpl_path_entry.text().strip()
        out_d = self.out_path_entry.text().strip()

        if not os.path.exists(img_p):
            T.notify(self, "error", "Nuotrauka nerasta", "Patikrinkite kliento nuotraukos kelią.")
            return
        if not os.path.exists(tmpl_p):
            T.notify(self, "error", "Šablonas nerastas", "Pasirinkite šablono .png failą.")
            return
        if self.worker is not None and self.worker.isRunning():
            return
        if not out_d:
            T.notify(self, "error", "Nenurodytas aplankas", "Nurodykite, kur išsaugoti failą.")
            return
        try:
            os.makedirs(out_d, exist_ok=True)
        except OSError as e:
            T.notify(self, "error", "Nepavyko sukurti aplanko", str(e))
            return
        base_name = os.path.splitext(os.path.basename(img_p))[0]
        out_file = os.path.join(out_d, f"{base_name}.tif")
        # Niekada neperrašome paties kliento failo (jei tai .tif tame pačiame aplanke)
        if os.path.normcase(os.path.abspath(out_file)) == os.path.normcase(os.path.abspath(img_p)):
            out_file = os.path.join(out_d, f"{base_name}_print.tif")

        settings = self.main_app.get_current_settings()
        self.process_btn.setEnabled(False)
        self.process_btn.setText("Gaminama…")
        self.result.setVisible(False)

        self.worker = SingleFileWorker(
            img_path=img_p, tmpl_path=tmpl_p, out_path=out_file,
            choke=settings["choke"], spot=settings["spot_name"],
            solidity=settings["solidity"], dpi=settings["dpi"]
        )
        self.worker.finished.connect(self._on_single_file_finished)
        self.worker.start()

    def _on_single_file_finished(self, success: bool, out_file: str, err_msg: str):
        self.process_btn.setEnabled(True)
        self.process_btn.setText("Sukurti spaudos failą")
        if success:
            self._last_out = out_file
            self.result.set("success", f"Failas sukurtas: {out_file}")
            self.open_result_btn.setVisible(True)
            self.main_app.log(f"✅ Vieno failo gamyba baigta: {out_file}")
        else:
            self.result.set("error", f"Nepavyko sukurti failo: {err_msg}")
            self.open_result_btn.setVisible(False)
            self.main_app.log(f"❌ Vieno failo klaida: {err_msg}")
        self.result.setVisible(True)

    def _open_result_folder(self):
        if self._last_out:
            open_in_explorer(os.path.dirname(self._last_out))


# =========================================================================
# 3. Šablonai ir jų taisyklės
# =========================================================================
def pil_to_pixmap(img) -> QPixmap:
    img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    qimg = QImage(data, img.width, img.height, img.width * 4, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimg.copy())


ROTATION_OPTIONS = [("0", "0°"), ("90", "90°"), ("180", "180°"), ("270", "270°")]


class TemplateOptionsDialog(T.ThemedDialog):
    """Vieno šablono taisyklės su gyva peržiūra."""

    def __init__(self, name: str, template_path: str, options: TO.TemplateOptions, parent=None):
        super().__init__(f"Šablonas {name}", parent, 700)
        self.template_path = template_path
        body = QHBoxLayout()
        body.setSpacing(20)

        prev_box = QFrame()
        prev_box.setObjectName("Card")
        prev_box.setFixedSize(292, 292)
        pl = QVBoxLayout(prev_box)
        pl.setContentsMargins(16, 16, 16, 16)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pl.addWidget(self.preview)
        left = QVBoxLayout()
        left.setSpacing(6)
        left.addWidget(prev_box)
        left.addWidget(T.label("Peržiūra: taip atrodys spaudos failas.", "muted"))
        left.addStretch(1)
        body.addLayout(left)

        right = QVBoxLayout()
        right.setSpacing(6)
        right.addWidget(field_label("Spaudos failo pasukimas"))
        right.addWidget(T.label("Pasukamas visas failas – kontūras ir nuotrauka (pagal laikrodžio rodyklę).", "secondary", wrap=True))
        self.out_rot = T.SegmentedControl(ROTATION_OPTIONS)
        self.out_rot.set_value(str(options.output_rotation))
        right.addWidget(self.out_rot, 0, Qt.AlignmentFlag.AlignLeft)
        right.addSpacing(12)
        right.addWidget(field_label("Nuotraukos pasukimas kontūre"))
        right.addWidget(T.label("Pasukama tik kliento nuotrauka, kontūras lieka vietoje.", "secondary", wrap=True))
        self.img_rot = T.SegmentedControl(ROTATION_OPTIONS)
        self.img_rot.set_value(str(options.image_rotation))
        right.addWidget(self.img_rot, 0, Qt.AlignmentFlag.AlignLeft)
        right.addSpacing(12)
        mirror = T.LabeledSwitch("Veidrodinis atspindys", "Kairė ↔ dešinė, pvz. spaudai iš galinės pusės.")
        self.mirror = mirror.switch
        self.mirror.setChecked(options.mirror)
        right.addWidget(mirror)
        right.addStretch(1)
        body.addLayout(right, 1)
        self.content.addLayout(body)
        self.content.addWidget(T.Alert("info", "Taisyklės galioja naujai gaminamiems failams. Jau paruoštus pergaminsite "
                                               "išjungę „Praleisti jau paruoštus“ Nustatymuose."))

        self.reset_btn = self.add_action(T.button("Atkurti numatytuosius"))
        self.reset_btn.clicked.connect(self._reset)
        cancel = self.add_action(T.button("Atšaukti"))
        cancel.clicked.connect(self.reject)
        self.save_btn = self.add_action(T.button("Išsaugoti", "primary"))
        self.save_btn.clicked.connect(self.accept)

        self.out_rot.changed.connect(lambda _: self._update_preview())
        self.img_rot.changed.connect(lambda _: self._update_preview())
        self.mirror.checkedChanged.connect(lambda _: self._update_preview())
        self._update_preview()

    def options(self) -> TO.TemplateOptions:
        return TO.TemplateOptions(output_rotation=int(self.out_rot.value() or 0),
                                  image_rotation=int(self.img_rot.value() or 0),
                                  mirror=self.mirror.isChecked())

    def _reset(self):
        self.out_rot.set_value("0")
        self.img_rot.set_value("0")
        self.mirror.setChecked(False)
        self._update_preview()

    def _update_preview(self):
        try:
            self.preview.setPixmap(pil_to_pixmap(render_preview(self.template_path, self.options(), 256)))
        except Exception as e:
            self.preview.setText(f"Peržiūra negalima: {e}")


TEMPLATE_COLUMNS = [("Kontūras", 72), ("Šablonas", 0), ("Dydis", 140), ("Taisyklės", 300), ("", 112)]


class TemplatesInterface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("templatesInterface")
        self.main_app = parent
        self.rows: Dict[str, QFrame] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 24)
        lay.setSpacing(16)
        head = QHBoxLayout()
        head.addLayout(page_header("Šablonai", "Kiekvienam šablonui galima nustatyti papildomas taisykles, "
                                               "pvz. spaudos failą pasukti 180°."), 1)
        open_btn = T.button("Atidaryti aplanką", icon_name="folder")
        open_btn.clicked.connect(lambda: self.main_app.open_templates_folder())
        head.addWidget(open_btn, 0, Qt.AlignmentFlag.AlignTop)
        refresh_btn = T.button("Atnaujinti", icon_name="refresh")
        refresh_btn.clicked.connect(self.refresh)
        head.addWidget(refresh_btn, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)

        card = T.Card(padding=0, spacing=0)
        header = QFrame()
        header.setObjectName("TableHeader")
        hh = QHBoxLayout(header)
        hh.setContentsMargins(20, 10, 20, 10)
        hh.setSpacing(12)
        for title, width in TEMPLATE_COLUMNS:
            w = T.label(title, "th")
            if width:
                w.setFixedWidth(width)
                hh.addWidget(w)
            else:
                hh.addWidget(w, 1)
        card.body.addWidget(header)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        body = QWidget()
        body.setObjectName("TableBody")
        body.setStyleSheet("QWidget#TableBody { background: transparent; }")
        self.list_layout = QVBoxLayout(body)
        self.list_layout.setContentsMargins(0, 0, 0, 8)
        self.list_layout.setSpacing(0)
        self.empty_lbl = T.label("", "empty", wrap=True)
        self.empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_lbl.setContentsMargins(20, 40, 20, 40)
        self.list_layout.addWidget(self.empty_lbl)
        self.list_layout.addStretch(1)
        scroll.setWidget(body)
        card.body.addWidget(scroll, 1)
        lay.addWidget(card, 1)

    def showEvent(self, e):
        super().showEvent(e)
        self.refresh()

    def templates_dir(self) -> str:
        return self.main_app.template_manager.templates_dir

    def refresh(self):
        for i in reversed(range(self.list_layout.count())):
            wdg = self.list_layout.itemAt(i).widget()
            if wdg is not None and wdg is not self.empty_lbl:
                self.list_layout.takeAt(i)
                wdg.deleteLater()
        self.rows.clear()
        tm = self.main_app.template_manager
        tm.reload_templates()
        names = tm.get_template_names()
        all_opts = TO.load_all(self.templates_dir())
        self.empty_lbl.setText(f"Šablonų aplanke nėra .png šablonų.\n{self.templates_dir()}")
        self.empty_lbl.setVisible(not names)
        for name in names:
            row = self._build_row(name, tm.templates[name], all_opts.get(name, TO.DEFAULT_OPTIONS))
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)
            self.rows[name] = row

    def _build_row(self, name: str, path: str, opts: TO.TemplateOptions) -> QFrame:
        row = QFrame()
        row.setObjectName("TableRow")
        row.setFixedHeight(76)
        h = QHBoxLayout(row)
        h.setContentsMargins(20, 0, 20, 0)
        h.setSpacing(12)
        thumb = QLabel()
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        size_text = "—"
        try:
            thumb.setPixmap(pil_to_pixmap(render_preview(path, opts, 56)))
            with Image.open(path) as im:
                size_text = f"{im.width} × {im.height} px"
        except Exception:
            thumb.setText("—")
        name_box = QVBoxLayout()
        name_box.setContentsMargins(0, 0, 0, 0)
        name_box.setSpacing(2)
        name_box.addStretch(1)
        nl = T.label(name, "cell")
        f = nl.font()
        f.setWeight(f.Weight.DemiBold)
        nl.setFont(f)
        name_box.addWidget(nl)
        name_box.addWidget(T.label(os.path.basename(path), "muted"))
        name_box.addStretch(1)
        name_w = QWidget()
        name_w.setLayout(name_box)
        rules = QWidget()
        rl = QHBoxLayout(rules)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)
        labels = opts.labels()
        if labels:
            for text in labels:
                rl.addWidget(T.Badge(text, "info", dot=False))
        else:
            rl.addWidget(T.label("Numatytosios", "cell2"))
        rl.addStretch(1)
        edit = T.button("Redaguoti", size="small")
        edit.clicked.connect(lambda _=False, n=name, p=path: self.edit(n, p))
        cells = [thumb, name_w, T.label(size_text, "cell2", tabular=True), rules, edit]
        for (title, width), w in zip(TEMPLATE_COLUMNS, cells):
            if width:
                holder = QWidget()
                holder.setFixedWidth(width)
                hl = QHBoxLayout(holder)
                hl.setContentsMargins(0, 0, 0, 0)
                hl.addWidget(w)
                hl.addStretch(1)
                h.addWidget(holder)
            else:
                h.addWidget(w, 1)
        return row

    def edit(self, name: str, path: str):
        current = TO.load_all(self.templates_dir()).get(name, TO.DEFAULT_OPTIONS)
        dlg = TemplateOptionsDialog(name, path, current, self)
        if not dlg.exec():
            return
        new = dlg.options()
        try:
            TO.save_for_template(os.path.dirname(path), name, new)
        except Exception as e:
            T.notify(self, "error", "Nepavyko išsaugoti taisyklių", str(e), 6000)
            return
        self.main_app.log(f"📐 Šablono {name} taisyklės: {', '.join(new.labels()) or 'numatytosios'}")
        T.notify(self, "success", f"Šablonas {name} išsaugotas",
                 ", ".join(new.labels()) or "Taisyklės atkurtos į numatytąsias.")
        self.refresh()


# =========================================================================
# 4. Nustatymai
# =========================================================================
class SettingsInterface(QWidget):
    # Teksto laukai, kurie taikomi tik baigus redaguoti: (nustatymo raktas, lauko atributas)
    TEXT_FIELDS = (
        ("input_folder", "in_entry"),
        ("rejects_input_folder", "rejects_entry"),
        ("output_folder", "out_entry"),
        ("templates_folder", "tmpl_entry"),
        ("spot_name", "spot_name_entry"),
    )
    FIELD_NAMES = {
        "input_folder": "Generacijų aplankas",
        "rejects_input_folder": "Rejected aplankas",
        "output_folder": "READY aplankas",
        "templates_folder": "Šablonų aplankas",
    }

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("settingsInterface")
        self.main_app = parent
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._do_save_config)
        self._committed: Dict[str, str] = {}
        self._init_ui()
        self._committed = {key: getattr(self, attr).text().strip() for key, attr in self.TEXT_FIELDS}

    def _init_ui(self):
        cfg = load_saved_config()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("PageBody")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(24, 20, 24, 32)
        lay.setSpacing(16)
        lay.addLayout(page_header("Nustatymai", "Pakeitimai išsaugomi automatiškai."))

        # --- Aplankai
        folders = self._card(lay, "Aplankai", "Iš kur imami užsakymai ir kur išsaugomi paruošti failai.")
        self.in_entry = make_line_edit(cfg.get("input_folder", DEFAULT_STD_INPUT))
        self._path_field(folders, "Generacijos", self.in_entry, self._pick_in,
                         hint="Įprasti užsakymai. Paruošti failai išsaugomi į READY.")
        self.rejects_entry = make_line_edit(cfg.get("rejects_input_folder", DEFAULT_REJECTS_INPUT))
        self._path_field(folders, "Rejected", self.rejects_entry, self._pick_rejects,
                         hint="Brokai ir perspausdinimai. Failai išsaugomi į READY\\BROKAI. Palikite tuščią, jei nenaudojate.")
        self.out_entry = make_line_edit(cfg.get("output_folder", DEFAULT_OUTPUT))
        self._path_field(folders, "READY", self.out_entry, self._pick_out,
                         open_fn=lambda: self.main_app.open_output_folder())
        self.tmpl_entry = make_line_edit(cfg.get("templates_folder", os.path.join(get_app_dir(), "Sablonai")))
        self._path_field(folders, "Šablonai", self.tmpl_entry, self._pick_tmpl,
                         open_fn=lambda: self.main_app.open_templates_folder(),
                         hint="Modelių .png kontūrai. Failo vardas turi atitikti modelį (pvz. 2681.png).")
        for _, attr in self.TEXT_FIELDS[:4]:
            getattr(self, attr).editingFinished.connect(self._commit_text_fields)

        # --- Automatinė gamyba
        auto = self._card(lay, "Automatinė gamyba")
        sw = T.LabeledSwitch("Fono stebėjimas", "Stebi Generacijų ir Rejected aplankus ir naujus failus gamina automatiškai.")
        self.watch_switch = sw.switch
        self.watch_switch.checkedChanged.connect(self._toggle_watcher)
        auto.body.addWidget(sw)
        auto.body.addWidget(T.divider())
        sw = T.LabeledSwitch("Tik šiandienos užsakymai",
                             "Fone gaminami tik šiandienos failai. Senesnius galima pagaminti rankiniu būdu iš sąrašo.")
        self.today_only_switch = sw.switch
        self.today_only_switch.setChecked(bool(cfg.get("auto_today_only", True)))
        self.today_only_switch.checkedChanged.connect(self._on_days_limit_changed)
        auto.body.addWidget(sw)
        auto.body.addWidget(T.divider())
        sw = T.LabeledSwitch("Praleisti jau paruoštus", "Jei READY aplanke failas jau yra, jis negaminamas iš naujo.")
        self.skip_existing_switch = sw.switch
        self.skip_existing_switch.setChecked(bool(cfg.get("skip_existing", True)))
        self.skip_existing_switch.checkedChanged.connect(self._auto_save)
        auto.body.addWidget(sw)
        auto.body.addWidget(T.divider())
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(6)
        self.days_limit_spin = make_spin(0, 365, int(cfg.get("days_back_limit", 3)), " d.")
        self.days_limit_spin.valueChanged.connect(self._on_days_limit_changed)
        self.workers_spin = make_spin(1, 16, int(cfg.get("max_workers", 4)), " gijos")
        self.workers_spin.valueChanged.connect(self._auto_save)
        self._grid_field(grid, 0, 0, "Sąraše rodyti užsakymus", self.days_limit_spin, "Šiandien + tiek paskutinių dienų.")
        self._grid_field(grid, 0, 1, "Lygiagrečios gijos", self.workers_spin, "Kiek failų gaminama vienu metu.")
        grid.setColumnStretch(2, 1)
        auto.body.addLayout(grid)

        # --- Spaudos parametrai
        prn = self._card(lay, "Spaudos parametrai", "ColorGATE ir Photoshop. Numatyta: 1 px, 300 DPI, W, 5 %.")
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(6)
        self.choke_spin = make_spin(0, 20, int(cfg.get("choke", 1)), " px")
        self.choke_spin.valueChanged.connect(self._auto_save)
        self.dpi_spin = make_spin(72, 1200, int(cfg.get("dpi", 300)), " DPI")
        self.dpi_spin.valueChanged.connect(self._auto_save)
        self.spot_name_entry = make_line_edit(cfg.get("spot_name", "W"))
        self.spot_name_entry.setFixedWidth(140)
        self.spot_name_entry.editingFinished.connect(self._commit_text_fields)
        self.solidity_spin = make_spin(1, 100, int(cfg.get("solidity", 5)), " %")
        self.solidity_spin.valueChanged.connect(self._auto_save)
        self._grid_field(grid, 0, 0, "Balto rašalo sutraukimas (choke)", self.choke_spin)
        self._grid_field(grid, 0, 1, "Raiška", self.dpi_spin)
        self._grid_field(grid, 0, 2, "Spot kanalo pavadinimas", self.spot_name_entry)
        self._grid_field(grid, 0, 3, "Spot kanalo tankis", self.solidity_spin)
        grid.setColumnStretch(4, 1)
        prn.body.addLayout(grid)

        # --- Išvaizda
        look = self._card(lay, "Išvaizda")
        row = QHBoxLayout()
        row.addWidget(T.label("Tema", "label"))
        row.addStretch(1)
        self.theme_seg = T.SegmentedControl([("light", "Šviesi"), ("dark", "Tamsi")])
        self.theme_seg.set_value(cfg.get("theme", "light"))
        self.theme_seg.changed.connect(self._on_theme_changed)
        row.addWidget(self.theme_seg)
        look.body.addLayout(row)

        # --- Atnaujinimai
        upd = self._card(lay, "Atnaujinimai", f"Dabartinė versija: v{APP_VERSION}. Naujos versijos imamos iš GitHub Releases.")
        sw = T.LabeledSwitch("Tikrinti automatiškai", "Paleidus programą ir kas 30 min.")
        self.auto_update_switch = sw.switch
        self.auto_update_switch.setChecked(bool(cfg.get("auto_check_updates", True)))
        self.auto_update_switch.checkedChanged.connect(self._auto_save)
        upd.body.addWidget(sw)
        upd.body.addWidget(T.divider())
        col = QVBoxLayout()
        col.setSpacing(6)
        col.addWidget(field_label("GitHub saugykla (owner/repo)"))
        r = QHBoxLayout()
        r.setSpacing(8)
        self.repo_entry = make_line_edit(cfg.get("github_repo", DEFAULT_GITHUB_REPO))
        self.repo_entry.setMaximumWidth(360)
        self.repo_entry.textChanged.connect(self._auto_save)
        r.addWidget(self.repo_entry)
        self.check_now_btn = T.button("Tikrinti dabar", icon_name="sync")
        self.check_now_btn.clicked.connect(self._manual_check_updates)
        r.addWidget(self.check_now_btn)
        r.addStretch(1)
        col.addLayout(r)
        upd.body.addLayout(col)

        # --- Nuoroda
        sh = self._card(lay, "Nuoroda darbalaukyje")
        row = QHBoxLayout()
        row.addWidget(T.label("Sukuria „PrintReady PRO“ paleidimo nuorodą darbalaukyje.", "secondary"), 1)
        self.create_sh_btn = T.button("Sukurti nuorodą", icon_name="link")
        self.create_sh_btn.clicked.connect(self._create_desktop_shortcut)
        row.addWidget(self.create_sh_btn)
        sh.body.addLayout(row)

        lay.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll)

    # --- išdėstymo pagalbininkai
    def _card(self, lay: QVBoxLayout, title: str, subtitle: str = "") -> T.Card:
        card = T.Card(padding=20, spacing=14)
        card.setMaximumWidth(960)
        card.body.addWidget(T.section_header(title, subtitle))
        lay.addWidget(card)
        return card

    def _path_field(self, card: T.Card, title: str, entry: QLineEdit, pick_fn, open_fn=None, hint: str = ""):
        col = QVBoxLayout()
        col.setSpacing(6)
        col.addWidget(field_label(title))
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(entry, 1)
        b = T.button("Pasirinkti…")
        b.clicked.connect(pick_fn)
        row.addWidget(b)
        if open_fn:
            ob = T.button("Atidaryti", icon_name="folder")
            ob.clicked.connect(open_fn)
            row.addWidget(ob)
        col.addLayout(row)
        if hint:
            col.addWidget(T.label(hint, "muted", wrap=True))
        card.body.addLayout(col)

    @staticmethod
    def _grid_field(grid: QGridLayout, r: int, col: int, title: str, w: QWidget, hint: str = ""):
        box = QVBoxLayout()
        box.setSpacing(6)
        box.addWidget(field_label(title))
        box.addWidget(w)
        if hint:
            box.addWidget(T.label(hint, "muted"))
        grid.addLayout(box, r, col, Qt.AlignmentFlag.AlignTop)

    # --- veiksmai
    def _on_theme_changed(self, name: str):
        T.apply_theme(QApplication.instance(), name)
        cfg = load_saved_config()
        cfg["theme"] = name
        save_config(cfg)

    def _manual_check_updates(self):
        if self.main_app:
            self.main_app.check_for_updates_manual()

    def _auto_save(self):
        # Atidėtas išsaugojimas (debouncing)
        self._save_timer.start()

    def committed(self, key: str) -> str:
        """Paskutinė patvirtinta (baigta redaguoti ir patikrinta) teksto lauko reikšmė."""
        return self._committed.get(key, "")

    @staticmethod
    def _validate_field(key: str, value: str) -> Optional[str]:
        """Grąžina klaidos tekstą arba None. Nepasiekiamas tinklo kelias leidžiamas (tinklas gali būti išjungtas)."""
        if key == "spot_name":
            return None
        if not value:
            return None if key == "rejects_input_folder" else "kelias negali būti tuščias"
        if not (os.path.isabs(value) or value.startswith("\\\\")):
            return "nurodykite pilną kelią (pvz. C:\\... arba \\\\serveris\\...)"
        return None

    def _commit_text_fields(self):
        """Patikrina ir pritaiko teksto laukus (baigus redaguoti arba pasirinkus aplanką)."""
        changed = False
        for key, attr in self.TEXT_FIELDS:
            entry = getattr(self, attr)
            value = entry.text().strip().strip('"').strip("'")
            if key == "spot_name":
                value = value or "W"
            if value == self._committed.get(key):
                continue
            err = self._validate_field(key, value)
            if err:
                entry.blockSignals(True)
                entry.setText(self._committed.get(key, ""))
                entry.blockSignals(False)
                T.notify(self, "warning", self.FIELD_NAMES.get(key, key), f"Nepakeista: {err}.", 5000)
                continue
            if value and key != "spot_name" and not os.path.exists(value) and self.main_app:
                self.main_app.log(f"⚠️ {self.FIELD_NAMES.get(key, key)} šiuo metu nepasiekiamas: {value}")
            self._committed[key] = value
            changed = True
        if changed:
            self._save_timer.stop()
            self._do_save_config()

    def flush_pending_save(self):
        self._commit_text_fields()
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._do_save_config()

    def _do_save_config(self):
        # Pradedame nuo esamo failo, kad neprarastume kitų reikšmių (pvz. auto_watch_enabled)
        cfg = load_saved_config()
        cfg.update({
            "input_folder": self.committed("input_folder"),
            "rejects_input_folder": self.committed("rejects_input_folder"),
            "output_folder": self.committed("output_folder"),
            "templates_folder": self.committed("templates_folder"),
            "choke": int(self.choke_spin.value()),
            "dpi": int(self.dpi_spin.value()),
            "spot_name": self.committed("spot_name") or "W",
            "solidity": int(self.solidity_spin.value()),
            "skip_existing": self.skip_existing_switch.isChecked(),
            "max_workers": int(self.workers_spin.value()),
            "days_back_limit": int(self.days_limit_spin.value()),
            "github_repo": self.repo_entry.text().strip() or DEFAULT_GITHUB_REPO,
            "auto_check_updates": self.auto_update_switch.isChecked(),
            "auto_today_only": self.today_only_switch.isChecked(),
        })
        save_config(cfg)
        if self.main_app and hasattr(self.main_app, 'on_settings_saved'):
            self.main_app.on_settings_saved()

    def _on_days_limit_changed(self):
        self._auto_save()
        if self.main_app and hasattr(self.main_app, 'orders_interface'):
            self.main_app.orders_interface.update_date_kpi(int(self.days_limit_spin.value()),
                                                           self.today_only_switch.isChecked())

    def _create_desktop_shortcut(self):
        try:
            desktop = os.path.join(os.environ.get('USERPROFILE', r'C:\Users\Default'), 'Desktop')
            shortcut_path = os.path.join(desktop, "PrintReady PRO.lnk")
            current_exe = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath("PrintReady.exe")
            current_dir = os.path.dirname(current_exe)
            icon_p = get_resource_path("app_icon.ico")
            try:
                import win32com.client
                shell = win32com.client.Dispatch("WScript.Shell")
                shortcut = shell.CreateShortCut(shortcut_path)
                shortcut.Targetpath = current_exe
                shortcut.WorkingDirectory = current_dir
                shortcut.Description = "Podbase PrintReady PRO - MacBook UV Spaudos Paruošimo Sistema"
                if os.path.exists(icon_p):
                    shortcut.IconLocation = icon_p
                shortcut.save()
            except Exception:
                ps_cmd = (
                    f"$ws = New-Object -ComObject WScript.Shell; "
                    f"$s = $ws.CreateShortcut({ps_quote(shortcut_path)}); "
                    f"$s.TargetPath = {ps_quote(current_exe)}; "
                    f"$s.WorkingDirectory = {ps_quote(current_dir)}; "
                    f"$s.Description = 'Podbase PrintReady PRO'; "
                    f"$s.Save()"
                )
                res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True)
                if res.returncode != 0:
                    raise RuntimeError(res.stderr.decode("utf-8", errors="replace").strip() or "nežinoma klaida")
            T.notify(self, "success", "Nuoroda sukurta", "„PrintReady PRO“ nuoroda yra darbalaukyje.")
        except Exception as e:
            T.notify(self, "error", "Nepavyko sukurti nuorodos", str(e), 6000)

    def _pick_in(self):
        d = QFileDialog.getExistingDirectory(self, "Pasirinkite Generacijų aplanką", self.in_entry.text())
        if d:
            self.in_entry.setText(d)
            self._commit_text_fields()

    def _pick_rejects(self):
        d = QFileDialog.getExistingDirectory(self, "Pasirinkite Rejected aplanką", self.rejects_entry.text())
        if d:
            self.rejects_entry.setText(d)
            self._commit_text_fields()

    def _pick_out(self):
        d = QFileDialog.getExistingDirectory(self, "Pasirinkite READY aplanką", self.out_entry.text())
        if d:
            self.out_entry.setText(d)
            self._commit_text_fields()

    def _pick_tmpl(self):
        d = QFileDialog.getExistingDirectory(self, "Pasirinkite šablonų aplanką", self.tmpl_entry.text())
        if d:
            self.tmpl_entry.setText(d)
            self._commit_text_fields()

    def _toggle_watcher(self, checked: bool):
        if checked:
            self.main_app.start_watcher()
        else:
            self.main_app.stop_watcher()


# =========================================================================
# 5. Žurnalas
# =========================================================================
class LogsInterface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("logsInterface")
        self.main_app = parent
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 24)
        lay.setSpacing(16)
        head = QHBoxLayout()
        head.addLayout(page_header("Žurnalas", "Visi programos veiksmai ir klaidos. Rodoma iki 5000 paskutinių eilučių."), 1)
        copy_btn = T.button("Kopijuoti", icon_name="copy")
        copy_btn.clicked.connect(self._copy_logs)
        head.addWidget(copy_btn, 0, Qt.AlignmentFlag.AlignTop)
        clear_btn = T.button("Išvalyti", icon_name="trash")
        clear_btn.clicked.connect(self._clear_logs)
        head.addWidget(clear_btn, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)

        self.log_text = QPlainTextEdit(self)
        self.log_text.setReadOnly(True)
        self.log_text.setFont(T.font(13, 400, mono=True))
        # Programa veikia visą dieną – laikome tik paskutines eilutes, kad žurnalas nelėtintų lango
        self.log_text.setMaximumBlockCount(5000)
        lay.addWidget(self.log_text, 1)

    def append_log(self, message: str):
        text = message.strip("\n")
        if not text.strip():
            return
        pad = " " * 10
        lines = text.split("\n")
        stamped = [f"{datetime.datetime.now():%H:%M:%S}  {lines[0]}"] + [pad + l for l in lines[1:]]
        self.log_text.appendPlainText("\n".join(stamped))

    def _copy_logs(self):
        QApplication.clipboard().setText(self.log_text.toPlainText())
        T.notify(self, "success", "Nukopijuota", "Žurnalas nukopijuotas į iškarpinę.", 2500)

    def _clear_logs(self):
        self.log_text.clear()


# =========================================================================
# 6. Pagrindinis langas
# =========================================================================
class TopBar(QFrame):
    def __init__(self, window: "MainWindow"):
        super().__init__(window)
        self.setObjectName("TopBar")
        self.setFixedHeight(56)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(24, 0, 16, 0)
        lay.setSpacing(10)
        logo = QLabel()
        pm = QPixmap(get_resource_path("printready_icon.png"))
        if not pm.isNull():
            logo.setPixmap(pm.scaledToHeight(24, Qt.TransformationMode.SmoothTransformation))
        lay.addWidget(logo)
        name = T.label("PrintReady PRO", "body")
        f = name.font()
        f.setPixelSize(15)
        f.setWeight(f.Weight.DemiBold)
        name.setFont(f)
        lay.addWidget(name)
        lay.addWidget(T.label(f"v{APP_VERSION}", "muted"))
        lay.addStretch(1)
        self.auto_badge = T.Badge("Auto-gamyba išjungta", "neutral")
        lay.addWidget(self.auto_badge)
        lay.addSpacing(8)
        self.clock = T.label("", "secondary", tabular=True)
        lay.addWidget(self.clock)
        lay.addSpacing(8)
        self.ready_btn = T.IconButton("folder_check", "Atidaryti READY aplanką")
        self.ready_btn.clicked.connect(window.open_output_folder)
        lay.addWidget(self.ready_btn)
        self.brokai_btn = T.IconButton("folder_alert", "Atidaryti READY\\BROKAI aplanką")
        self.brokai_btn.clicked.connect(window.open_brokai_folder)
        lay.addWidget(self.brokai_btn)
        self.tmpl_btn = T.IconButton("shapes", "Atidaryti šablonų aplanką")
        self.tmpl_btn.clicked.connect(window.open_templates_folder)
        lay.addWidget(self.tmpl_btn)
        self._tick()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(15000)

    def _tick(self):
        self.clock.setText(f"{datetime.datetime.now():%Y-%m-%d  %H:%M}")


class MainWindow(QMainWindow):
    watcher_file_done = Signal(str, str, bool)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PrintReady PRO · Podbase")
        self.setWindowIcon(QIcon(get_resource_path("app_icon.ico")))
        self.setMinimumSize(1100, 740)
        self.resize(1280, 860)

        # Gijų saugus žurnalo siuntėjas
        self.log_emitter = LogEmitter()
        self.watcher_file_done.connect(self._on_watcher_file_done)

        cfg = load_saved_config()
        tmpl_dir = cfg.get("templates_folder", os.path.join(get_app_dir(), "Sablonai"))
        try:
            os.makedirs(tmpl_dir, exist_ok=True)
        except OSError:
            pass
        self.template_manager = TemplateManager(tmpl_dir)

        # Vienas bendras gamybos variklis fonui, skenavimui ir rankinei gamybai
        self.watcher: Optional[OrderWatcher] = None
        self._applied_tmpl_dir = os.path.normpath(tmpl_dir)

        # Atnaujinimų valdiklis
        self._watcher_paused_for_update = False
        self.updater_manager = AutoUpdaterManager(
            self,
            repo=cfg.get("github_repo", DEFAULT_GITHUB_REPO),
            current_version=APP_VERSION,
            log=self.log,
            auto_enabled=lambda: load_saved_config().get("auto_check_updates", True),
            repo_getter=lambda: load_saved_config().get("github_repo", DEFAULT_GITHUB_REPO),
            busy_reason=self._update_busy_reason,
            pause_work=self._pause_work_for_update,
            resume_work=self._resume_work_after_update
        )

        # Langas: viršutinė juosta, skirtukai, puslapiai
        central = QWidget()
        central.setObjectName("Page")
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.top_bar = TopBar(self)
        v.addWidget(self.top_bar)
        self.tabs = T.UnderlineTabs(["Užsakymai", "Vieno failo įrankis", "Šablonai", "Nustatymai", "Žurnalas"])
        v.addWidget(self.tabs)
        self.stack = QStackedWidget()
        v.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.orders_interface = OrdersInterface(self)
        self.single_interface = SingleFileInterface(self)
        self.templates_interface = TemplatesInterface(self)
        self.settings_interface = SettingsInterface(self)
        self.logs_interface = LogsInterface(self)
        self._pages = [self.orders_interface, self.single_interface, self.templates_interface,
                       self.settings_interface, self.logs_interface]
        for p in self._pages:
            self.stack.addWidget(p)
        self.tabs.changed.connect(lambda i: self.stack.setCurrentIndex(i))
        self.log_emitter.log_signal.connect(self._safe_append_log)

        self.toast_host = T.ToastHost(self, top_offset=112)

        self.watcher = self._create_watcher()

        # Atnaujiname šablonus ir rodiklius
        self.reload_templates()
        days_limit = int(cfg.get("days_back_limit", 3))
        self.orders_interface.update_date_kpi(days_limit, bool(cfg.get("auto_today_only", True)))
        self._set_watch_ui(False)

        self.log("🚀 Podbase PrintReady PRO sistema paleista!")
        self.log(f"📁 Generacijų aplankas: {cfg.get('input_folder', DEFAULT_STD_INPUT)}")
        if cfg.get('rejects_input_folder'):
            self.log(f"🔴 Rejected aplankas: {cfg.get('rejects_input_folder')}")
        self.log(f"📁 Išvesties READY Aplankas: {cfg.get('output_folder', DEFAULT_OUTPUT)}")
        self.log(f"⚡ Lygiagretus gamybos variklis: {cfg.get('max_workers', 4)} gijos | Praleisti jau paruoštus: {'TAIP' if cfg.get('skip_existing', True) else 'NE'}")
        self.log(f"📅 Užsakymų sąrašas: šiandien + paskutinės {days_limit} d. | Auto-gamyba: "
                 f"{'tik šiandien' if cfg.get('auto_today_only', True) else f'šiandien + {days_limit} d.'}")
        self.log(f"📐 Šablonų aplankas: {tmpl_dir}")

        # Automatinis fono stebėjimo paleidimas (pagal nutylėjimą: ĮJUNGTA)
        if cfg.get("auto_watch_enabled", True):
            QTimer.singleShot(800, self.start_watcher)

        # Paskutinio atnaujinimo rezultatas (jei programa ką tik atsinaujino)
        QTimer.singleShot(1500, self.updater_manager.show_last_update_result)

        # Atnaujinimų tikrinimas: po kelių sekundžių ir kas 30 min.
        self.updater_manager.start()

    # --- navigacija
    def switchTo(self, page: QWidget):
        idx = self._pages.index(page)
        self.stack.setCurrentIndex(idx)
        self.tabs.set_current(idx)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "toast_host"):
            self.toast_host.relayout()

    def _on_watcher_file_done(self, file_path: str, out_path: str, is_reject: bool):
        if hasattr(self, 'orders_interface'):
            self.orders_interface.on_watcher_file_completed(file_path, out_path, is_reject)
            if self.watcher and self.watcher.running:
                self.orders_interface.set_last_auto(os.path.basename(out_path))

    # --- Atnaujinimui: palaukti, kol baigsis vykdoma gamyba ---
    def _update_busy_reason(self) -> Optional[str]:
        prod = getattr(self.orders_interface, 'prod_worker', None)
        if prod is not None and prod.isRunning():
            return "užsakymų gamyba"
        single = getattr(self.single_interface, 'worker', None)
        if single is not None and single.isRunning():
            return "1 failo apdorojimas"
        if self.watcher and self.watcher.is_active():
            return "fono gamyba (baigiami pradėti failai)"
        return None

    def _pause_work_for_update(self):
        # Stabdome fono stebėjimą nekeičiant išsaugoto nustatymo – pradėti failai pabaigiami
        if self.watcher and self.watcher.running:
            self._watcher_paused_for_update = True
            self.watcher.stop()
            self.log("⏸ Fono stebėjimas pristabdytas atnaujinimui...")

    def _resume_work_after_update(self):
        if self._watcher_paused_for_update:
            self._watcher_paused_for_update = False
            self.get_watcher_instance().start()
            self.log("▶ Fono stebėjimas tęsiamas.")

    def closeEvent(self, event):
        # Neprarandame paskutinio nustatymų pakeitimo (išsaugojimas atidedamas 0,4 s)
        try:
            self.settings_interface.flush_pending_save()
        except Exception:
            pass
        super().closeEvent(event)

    def check_for_updates_manual(self):
        self.updater_manager.repo = self.settings_interface.repo_entry.text().strip() or DEFAULT_GITHUB_REPO
        self.log(f"🔍 Tikrinami atnaujinimai iš GitHub ({self.updater_manager.repo})...")
        self.updater_manager.check_updates_async(is_manual=True)

    # --- žurnalas
    def log(self, message: str):
        try:
            enc = sys.stdout.encoding or 'utf-8'
            print(message.encode(enc, errors='replace').decode(enc))
        except Exception:
            pass
        self.log_emitter.log_signal.emit(message)

    def _safe_append_log(self, message: str):
        self.logs_interface.append_log(message)

    def reload_templates(self):
        self.template_manager.reload_templates()
        count = len(self.template_manager.get_template_names())
        self.orders_interface.update_templates_kpi(count)
        self.log(f"📐 Šablonai atnaujinti. Aktyvių šablonų: {count} ({self.template_manager.templates_dir})")

    # --- aplankų atidarymas
    def _open_dir(self, path: str, what: str):
        try:
            os.makedirs(path, exist_ok=True)
            open_in_explorer(path)
        except Exception as e:
            self.log(f"Klaida atidarant {what}: {e}")
            T.notify(self, "error", f"Nepavyko atidaryti: {what}", str(e))

    def open_templates_folder(self):
        self._open_dir(self.settings_interface.committed("templates_folder"), "šablonų aplanką")

    def open_output_folder(self):
        self._open_dir(self.settings_interface.committed("output_folder"), "READY aplanką")

    def open_brokai_folder(self):
        self._open_dir(os.path.join(self.settings_interface.committed("output_folder"), "BROKAI"), "BROKAI aplanką")

    # --- nustatymai ir variklis
    def get_current_settings(self) -> Dict[str, Any]:
        si = self.settings_interface
        return {
            "input_folder": si.committed("input_folder"),
            "rejects_input_folder": si.committed("rejects_input_folder"),
            "output_folder": si.committed("output_folder"),
            "templates_folder": si.committed("templates_folder"),
            "choke": int(si.choke_spin.value()),
            "dpi": int(si.dpi_spin.value()),
            "spot_name": si.committed("spot_name") or "W",
            "solidity": int(si.solidity_spin.value()),
            "skip_existing": si.skip_existing_switch.isChecked(),
            "max_workers": int(si.workers_spin.value()),
            "days_back_limit": int(si.days_limit_spin.value()),
            "auto_today_only": si.today_only_switch.isChecked(),
        }

    def _watcher_settings(self) -> Dict[str, Any]:
        s = self.get_current_settings()
        return {
            "input_folder": s["input_folder"],
            "rejects_input_folder": s["rejects_input_folder"],
            "output_folder": s["output_folder"],
            "templates_folder": s["templates_folder"],
            "choke_pixels": s["choke"],
            "spot_channel_name": s["spot_name"],
            "solidity": s["solidity"],
            "target_dpi": s["dpi"],
            "skip_existing": s["skip_existing"],
            "max_workers": s["max_workers"],
            "days_back_limit": s["days_back_limit"],
            "auto_today_only": s["auto_today_only"],
        }

    def get_watcher_instance(self) -> OrderWatcher:
        """Grąžina bendrą gamybos variklį su dabartiniais nustatymais."""
        if self.watcher is None:
            self.watcher = self._create_watcher()
        else:
            self.watcher.apply_settings(**self._watcher_settings())
        return self.watcher

    def on_settings_saved(self):
        """Nustatymai išsaugoti – pritaikome juos iškart (ir veikiančiam fono stebėjimui)."""
        s = self.get_current_settings()
        new_tmpl = os.path.normpath(s["templates_folder"]) if s["templates_folder"] else ""
        if new_tmpl and new_tmpl != self._applied_tmpl_dir:
            self._applied_tmpl_dir = new_tmpl
            self.template_manager.set_templates_dir(new_tmpl)
            self.reload_templates()
        if self.watcher is not None:
            self.watcher.apply_settings(**self._watcher_settings())
        self.orders_interface.update_source_folders(s["input_folder"], s["rejects_input_folder"], s["output_folder"])

    def _create_watcher(self) -> OrderWatcher:
        s = self._watcher_settings()
        return OrderWatcher(
            log_callback=self.log,
            on_file_processed_callback=lambda fp, op, rej: self.watcher_file_done.emit(fp, op, rej),
            **s
        )

    def _set_watch_ui(self, on: bool):
        self.orders_interface.set_watch_state(on)
        sw = self.settings_interface.watch_switch
        sw.blockSignals(True)
        sw.setChecked(on)
        sw.blockSignals(False)
        self.top_bar.auto_badge.set("Auto-gamyba įjungta" if on else "Auto-gamyba išjungta",
                                    "success" if on else "neutral")

    def start_watcher(self):
        watcher = self.get_watcher_instance()
        if watcher.running:
            return
        watcher.start()
        self._set_watch_ui(True)
        cfg = load_saved_config()
        cfg["auto_watch_enabled"] = True
        save_config(cfg)
        T.notify(self, "success", "Automatinė gamyba įjungta", "Nauji užsakymai gaminami fone.", 3000)

    def stop_watcher(self):
        if self.watcher and self.watcher.running:
            self.watcher.stop()
        self._set_watch_ui(False)
        cfg = load_saved_config()
        cfg["auto_watch_enabled"] = False
        save_config(cfg)
        T.notify(self, "info", "Automatinė gamyba išjungta", "Failus gaminkite rankiniu būdu.", 3000)


# =========================================================================
# Paleidimas
# =========================================================================
def setup_application(app: QApplication):
    """Šriftai, stilius ir tema pagal nustatymus."""
    T.setup_application(app, load_saved_config().get("theme", "light"))


def main():
    app = QApplication(sys.argv)
    setup_application(app)

    main_window = MainWindow()
    main_window.show()

    # Uždaryti PyInstaller splash screen jei buvo aktyvus
    try:
        import pyi_splash
        pyi_splash.close()
    except Exception:
        pass

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
