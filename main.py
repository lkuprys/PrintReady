import os
import sys
import json
import time
import subprocess
from typing import List, Dict, Any, Optional

from PySide6.QtCore import Qt, QThread, Signal, QSize, QTimer, QPoint
from PySide6.QtGui import QIcon, QPixmap, QFont, QColor, QPainter, QBrush, QPen
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QFileDialog, QScrollArea, QFrame, QSizePolicy, QSpacerItem, QSplashScreen
)

from qfluentwidgets import (
    FluentWindow, NavigationItemPosition, FluentIcon as FIF,
    PrimaryPushButton, PushButton, ToolButton, LineEdit, SearchLineEdit,
    ProgressBar, InfoBar, InfoBarPosition, CardWidget, SimpleCardWidget,
    HeaderCardWidget, CheckBox, SwitchButton, SubtitleLabel, BodyLabel,
    CaptionLabel, TitleLabel, StrongBodyLabel, TextEdit, setTheme, Theme,
    setThemeColor, SpinBox, DoubleSpinBox, Flyout, FlyoutView, MessageDialog,
    IndeterminateProgressBar
)

from order_watcher import OrderWatcher, DEFAULT_STD_INPUT, DEFAULT_REJECTS_INPUT, DEFAULT_OUTPUT
from template_manager import TemplateManager
from crop_engine import process_and_crop
from updater import APP_VERSION, DEFAULT_GITHUB_REPO, AutoUpdaterManager

def get_app_dir() -> str:
    """Grąžina programos aplanką (kur yra .exe arba .py)."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(".")

def get_resource_path(relative_path: str) -> str:
    """Grąžina teisingą resursų kelią veikiant tiek kaip .py, tiek kaip PyInstaller .exe."""
    if hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS
    else:
        base_path = get_app_dir()
    return os.path.join(base_path, relative_path)

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
        "github_repo": DEFAULT_GITHUB_REPO,
        "auto_check_updates": True
    }

    if os.path.exists(cfg_p):
        try:
            with open(cfg_p, "r", encoding="utf-8") as f:
                data = json.load(f)
                defaults.update(data)
        except Exception:
            pass
    return defaults

def save_config(config_dict: Dict[str, Any]):
    cfg_p = get_config_path()
    try:
        with open(cfg_p, "w", encoding="utf-8") as f:
            json.dump(config_dict, f, indent=4, ensure_ascii=False)
    except Exception:
        pass

# =========================================================================
# 0. Modernus Windows 11 Fluent Loading / Splash Screen
# =========================================================================
class PrintReadySplashScreen(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(480, 320)

        screen = QApplication.primaryScreen().geometry()
        self.move((screen.width() - self.width()) // 2, (screen.height() - self.height()) // 2)

        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)

        card = CardWidget(self)
        card.setObjectName("splashCard")
        card.setStyleSheet("""
            CardWidget#splashCard {
                background-color: #0F172A;
                border: 1px solid #334155;
                border-radius: 12px;
            }
        """)
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(28, 24, 28, 24)
        c_layout.setSpacing(10)
        c_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_path = get_resource_path("printready_icon.png")
        if not os.path.exists(icon_path):
            icon_path = get_resource_path("app_icon.png")

        if os.path.exists(icon_path):
            icon_lbl = QLabel()
            pix = QPixmap(icon_path).scaled(80, 80, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            icon_lbl.setPixmap(pix)
            icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            c_layout.addWidget(icon_lbl)

        title = TitleLabel("PrintReady PRO")
        title.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        title.setStyleSheet("color: #F8FAFC;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        c_layout.addWidget(title)

        sub = CaptionLabel("Podbase UV Spaudos Automatizavimo Sistema • v2.5")
        sub.setStyleSheet("color: #94A3B8;")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        c_layout.addWidget(sub)

        c_layout.addSpacing(10)

        self.prog_bar = IndeterminateProgressBar(self)
        self.prog_bar.setFixedHeight(6)
        self.prog_bar.start()
        c_layout.addWidget(self.prog_bar)

        self.status_lbl = CaptionLabel("Kraunami modelių šablonai ir spalvų profiliai...")
        self.status_lbl.setStyleSheet("color: #38BDF8; font-weight: bold;")
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        c_layout.addWidget(self.status_lbl)

        main_layout.addWidget(card)

    def set_status(self, text: str):
        self.status_lbl.setText(text)
        QApplication.processEvents()

# =========================================================================
# Gijų (Thread) pagalbinės klasės sklandžiam foniniam darbui
# =========================================================================
class ScanWorker(QThread):
    finished = Signal(list)
    log_msg = Signal(str)

    def __init__(self, watcher: OrderWatcher):
        super().__init__()
        self.watcher = watcher

    def run(self):
        try:
            groups = self.watcher.scan_available_orders()
            self.finished.emit(groups)
        except Exception as e:
            self.log_msg.emit(f"❌ Klaida skenuojant: {e}")
            self.finished.emit([])

class ProductionWorker(QThread):
    progress = Signal(int, int, str)
    finished = Signal(int)
    log_msg = Signal(str)

    def __init__(self, watcher: OrderWatcher, groups: List[Dict[str, Any]]):
        super().__init__()
        self.watcher = watcher
        self.groups = groups

    def run(self):
        def on_prog(cur, total, fname):
            self.progress.emit(cur, total, fname)

        try:
            count = self.watcher.process_selected_groups(self.groups, progress_callback=on_prog)
            self.finished.emit(count)
        except Exception as e:
            self.log_msg.emit(f"❌ Gamybos klaida: {e}")
            self.finished.emit(0)

# =========================================================================
# 1. Pagrindinis Užsakymų ir Gamybos Puslapis (OrdersInterface)
# =========================================================================
class OrdersInterface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("ordersInterface")
        self.main_app = parent

        self.scanned_groups: List[Dict[str, Any]] = []
        self.group_cards: Dict[str, SimpleCardWidget] = {}
        self.group_checkboxes: Dict[str, CheckBox] = {}

        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(28, 20, 28, 20)
        main_layout.setSpacing(14)

        # 1. Viršutinė antraštė su Podbase logotipu ir greitaisiais veiksmais
        header_card = SimpleCardWidget(self)
        header_card.setStyleSheet("""
            SimpleCardWidget {
                background-color: #1E293B;
                border: 1px solid #334155;
                border-radius: 10px;
            }
        """)
        h_layout = QHBoxLayout(header_card)
        h_layout.setContentsMargins(18, 12, 18, 12)
        h_layout.setSpacing(14)

        # Podbase logotipas
        logo_path = get_resource_path("podbase_logo_header.png")
        if not os.path.exists(logo_path):
            logo_path = get_resource_path("podbase_logo_darkmode.png")
        if not os.path.exists(logo_path):
            logo_path = get_resource_path("podbase_logo.png")

        if os.path.exists(logo_path):
            logo_lbl = QLabel()
            pix = QPixmap(logo_path)
            scaled_pix = pix.scaledToHeight(38, Qt.TransformationMode.SmoothTransformation)
            logo_lbl.setPixmap(scaled_pix)
            h_layout.addWidget(logo_lbl)

        # Pavadinimai
        t_box = QVBoxLayout()
        t_box.setSpacing(2)
        t_title = TitleLabel("PrintReady PRO")
        t_title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        t_title.setStyleSheet("color: #F8FAFC;")
        t_sub = CaptionLabel("MacBook UV Spaudos Paruošimo Sistema • ColorGATE & Photoshop Ready")
        t_sub.setStyleSheet("color: #94A3B8;")
        t_box.addWidget(t_title)
        t_box.addWidget(t_sub)
        h_layout.addLayout(t_box)

        h_layout.addSpacerItem(QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))

        # Viršutiniai mygtukai
        self.open_ready_btn = PrimaryPushButton(FIF.FOLDER, "Atidaryti READY", self)
        self.open_ready_btn.clicked.connect(self.main_app.open_output_folder)
        h_layout.addWidget(self.open_ready_btn)

        self.open_brokai_btn = PushButton(FIF.DELETE, "📂 BROKAI", self)
        self.open_brokai_btn.setStyleSheet("color: #F87171; font-weight: bold;")
        self.open_brokai_btn.clicked.connect(self.main_app.open_brokai_folder)
        h_layout.addWidget(self.open_brokai_btn)

        self.open_tmpl_btn = PushButton(FIF.LABEL, "Šablonai", self)
        self.open_tmpl_btn.clicked.connect(self.main_app.open_templates_folder)
        h_layout.addWidget(self.open_tmpl_btn)

        main_layout.addWidget(header_card)

        # 2. KPI Statistikos kortelės
        kpi_layout = QHBoxLayout()
        kpi_layout.setSpacing(10)

        card_kpi_style = """
            SimpleCardWidget {
                background-color: #1E293B;
                border: 1px solid #334155;
                border-radius: 8px;
            }
        """

        self.kpi_tmpl = SimpleCardWidget(self)
        self.kpi_tmpl.setStyleSheet(card_kpi_style)
        l1 = QHBoxLayout(self.kpi_tmpl)
        l1.setContentsMargins(14, 10, 14, 10)
        self.kpi_tmpl_lbl = StrongBodyLabel("📐 Šablonai: Kraunama...")
        self.kpi_tmpl_lbl.setStyleSheet("color: #38BDF8; font-weight: bold;")
        l1.addWidget(self.kpi_tmpl_lbl)
        kpi_layout.addWidget(self.kpi_tmpl)

        self.kpi_orders = SimpleCardWidget(self)
        self.kpi_orders.setStyleSheet(card_kpi_style)
        l2 = QHBoxLayout(self.kpi_orders)
        l2.setContentsMargins(14, 10, 14, 10)
        self.kpi_orders_lbl = StrongBodyLabel("📦 Nuskenuota: 0 modelių")
        self.kpi_orders_lbl.setStyleSheet("color: #FBBF24; font-weight: bold;")
        l2.addWidget(self.kpi_orders_lbl)
        kpi_layout.addWidget(self.kpi_orders)

        self.kpi_status = SimpleCardWidget(self)
        self.kpi_status.setStyleSheet(card_kpi_style)
        l3 = QHBoxLayout(self.kpi_status)
        l3.setContentsMargins(14, 10, 14, 10)
        self.kpi_status_lbl = StrongBodyLabel("⚡ Būsena: Paruošta darbui")
        self.kpi_status_lbl.setStyleSheet("color: #34D399; font-weight: bold;")
        l3.addWidget(self.kpi_status_lbl)
        kpi_layout.addWidget(self.kpi_status)

        main_layout.addLayout(kpi_layout)

        # 3. Pagrindinė Užsakymų Valdymo Kortelė
        self.orders_box = CardWidget(self)
        self.orders_box.setStyleSheet("""
            CardWidget {
                background-color: #0F172A;
                border: 1px solid #1E293B;
                border-radius: 10px;
            }
        """)
        box_layout = QVBoxLayout(self.orders_box)
        box_layout.setContentsMargins(16, 14, 16, 14)
        box_layout.setSpacing(12)

        # 3.1 Veiksmų juosta: Skenavimas, Žymėjimas, Gamybos mygtukas
        act_row = QHBoxLayout()
        act_row.setSpacing(8)

        self.scan_btn = PrimaryPushButton(FIF.SEARCH, "SKENUOTI UŽSAKYMUS", self)
        self.scan_btn.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.scan_btn.setFixedHeight(36)
        self.scan_btn.clicked.connect(self._scan_orders)
        act_row.addWidget(self.scan_btn)

        self.select_all_btn = PushButton(FIF.CHECKBOX, "Žymėti Rodomus", self)
        self.select_all_btn.setFixedHeight(36)
        self.select_all_btn.clicked.connect(self._select_all)
        act_row.addWidget(self.select_all_btn)

        self.deselect_all_btn = PushButton("Nuimti Rodomus", self)
        self.deselect_all_btn.setFixedHeight(36)
        self.deselect_all_btn.clicked.connect(self._deselect_all)
        act_row.addWidget(self.deselect_all_btn)

        act_row.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))

        self.produce_btn = PrimaryPushButton(FIF.PLAY, "GAMINTI PAŽYMĖTUS (0 failų)", self)
        self.produce_btn.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self.produce_btn.setFixedHeight(38)
        self.produce_btn.clicked.connect(self._produce_selected)
        act_row.addWidget(self.produce_btn)

        box_layout.addLayout(act_row)

        # 3.2 Paieškos juosta
        search_row = QHBoxLayout()
        search_row.setSpacing(8)

        self.search_entry = SearchLineEdit(self)
        self.search_entry.setPlaceholderText("Greita paieška: įveskite modelį (a2681, 1932, NEO), datą, failo numerį ar 'brokas'...")
        self.search_entry.setFixedHeight(34)
        self.search_entry.setStyleSheet("""
            SearchLineEdit {
                background-color: #1E293B;
                color: #F8FAFC;
                border: 1px solid #334155;
                border-radius: 6px;
                padding-left: 8px;
            }
        """)
        self.search_entry.textChanged.connect(self._filter_groups)
        self.search_entry.clearSignal.connect(self._filter_groups)
        search_row.addWidget(self.search_entry)

        box_layout.addLayout(search_row)

        # 3.3 Progreso juosta (Progress Bar)
        self.prog_box = SimpleCardWidget(self)
        self.prog_box.setStyleSheet("""
            SimpleCardWidget {
                background-color: #1E293B;
                border: 1px solid #334155;
                border-radius: 8px;
            }
        """)
        prog_inner = QVBoxLayout(self.prog_box)
        prog_inner.setContentsMargins(12, 8, 12, 8)
        prog_inner.setSpacing(4)

        self.progress_bar = ProgressBar(self)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(8)
        prog_inner.addWidget(self.progress_bar)

        self.progress_lbl = CaptionLabel("Pradedama gamyba...")
        self.progress_lbl.setStyleSheet("color: #60A5FA; font-weight: bold;")
        prog_inner.addWidget(self.progress_lbl)

        self.prog_box.setVisible(False)
        box_layout.addWidget(self.prog_box)

        # 3.4 Užsakymų sąrašas (Scroll Area)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setStyleSheet("QScrollArea { background-color: transparent; border: none; }")

        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background-color: transparent;")
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(0, 4, 0, 4)
        self.scroll_layout.setSpacing(8)
        self.scroll_layout.addStretch(1)

        self.scroll_area.setWidget(self.scroll_content)
        box_layout.addWidget(self.scroll_area)

        # Tuščio sąrašo pranešimas
        self.empty_lbl = BodyLabel("Spustelėkite '🔍 SKENUOTI UŽSAKYMUS', kad pamatytumėte visus paruoštus modelius (Standartinius ir Brokus).")
        self.empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_lbl.setStyleSheet("color: #94A3B8; font-size: 13px; padding: 20px;")
        self.scroll_layout.insertWidget(0, self.empty_lbl)

        main_layout.addWidget(self.orders_box)

    def update_templates_kpi(self, count: int):
        if count == 0:
            self.kpi_tmpl_lbl.setText("⚠️ 0 šablonų rasta!")
            self.kpi_tmpl_lbl.setStyleSheet("color: #EF4444; font-weight: bold;")
        else:
            self.kpi_tmpl_lbl.setText(f"📐 {count} aktyvių šablonų")
            self.kpi_tmpl_lbl.setStyleSheet("color: #38BDF8; font-weight: bold;")

    def _scan_orders(self):
        self.kpi_status_lbl.setText("🔍 Skenuojama...")
        self.kpi_status_lbl.setStyleSheet("color: #FBBF24; font-weight: bold;")
        self.scan_btn.setEnabled(False)

        watcher = self.main_app.get_watcher_instance()
        self.scan_worker = ScanWorker(watcher)
        self.scan_worker.finished.connect(self._on_scan_finished)
        self.scan_worker.log_msg.connect(self.main_app.log)
        self.scan_worker.start()

    def _on_scan_finished(self, groups: List[Dict[str, Any]]):
        self.scan_btn.setEnabled(True)
        self.scanned_groups = groups
        self.group_cards.clear()
        self.group_checkboxes.clear()

        while self.scroll_layout.count() > 1:
            item = self.scroll_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        total_files = sum(len(g["files"]) for g in groups)
        reject_count = sum(1 for g in groups if g.get("is_reject"))
        reject_files = sum(len(g["files"]) for g in groups if g.get("is_reject"))
        std_count = len(groups) - reject_count
        missing_tmpl_count = sum(1 for g in groups if not g.get("has_template"))

        if reject_count > 0:
            self.kpi_orders_lbl.setText(f"📦 {std_count} std ({total_files-reject_files}f) • 🔴 {reject_count} brokų ({reject_files}f)")
        else:
            self.kpi_orders_lbl.setText(f"📦 Nuskenuota: {len(groups)} modelių ({total_files} failų)")

        self.kpi_status_lbl.setText("⚡ Būsena: Paruošta gamybai")
        self.kpi_status_lbl.setStyleSheet("color: #34D399; font-weight: bold;")

        if not groups:
            self.empty_lbl.setText("Nerasta jokių užsakymų nei standartiniame, nei brokų hotfolderyje.\n(Patikrinkite tinklo kelius ir ar 'Sablonai' aplankas nėra tuščias).")
            self.empty_lbl.setVisible(True)
            self._update_counter()
            return

        self.empty_lbl.setVisible(False)

        for idx, g in enumerate(groups):
            card = SimpleCardWidget(self.scroll_content)
            is_reject = g.get("is_reject", False)
            has_tmpl = g.get("has_template", True)

            # Aiški, tamsi, kontrastinga kortelės išvaizda
            if not has_tmpl:
                card.setStyleSheet("""
                    SimpleCardWidget {
                        background-color: #2D1D16;
                        border: 1px solid #B45309;
                        border-radius: 8px;
                    }
                    SimpleCardWidget:hover {
                        border: 1px solid #F59E0B;
                        background-color: #38241C;
                    }
                """)
            elif is_reject:
                card.setStyleSheet("""
                    SimpleCardWidget {
                        background-color: #26121C;
                        border: 1px solid #881337;
                        border-radius: 8px;
                    }
                    SimpleCardWidget:hover {
                        border: 1px solid #E11D48;
                        background-color: #311724;
                    }
                """)
            else:
                card.setStyleSheet("""
                    SimpleCardWidget {
                        background-color: #1E293B;
                        border: 1px solid #334155;
                        border-radius: 8px;
                    }
                    SimpleCardWidget:hover {
                        border: 1px solid #475569;
                        background-color: #243248;
                    }
                """)

            c_layout = QHBoxLayout(card)
            c_layout.setContentsMargins(14, 8, 14, 8)
            c_layout.setSpacing(12)

            prefix = "🔴 [BROKAS]  " if is_reject else ""
            chk_text = f"{prefix}📅 {g['date']}   ▶   ⚡ {g['generation']}   ▶   💻 {g['model']}"

            chk = CheckBox(chk_text, card)
            chk.setChecked(has_tmpl)
            if not has_tmpl:
                chk.setEnabled(False)
            chk.setStyleSheet("""
                CheckBox {
                    color: #F8FAFC;
                    font-size: 13px;
                    font-weight: 600;
                }
                CheckBox:disabled {
                    color: #94A3B8;
                }
            """)
            chk.stateChanged.connect(self._update_counter)
            c_layout.addWidget(chk)

            c_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))

            # Kontrastingas Badge / Ženkliukas
            badge = QLabel(card)
            if not has_tmpl:
                badge.setText(f"  ⚠️ NĖRA ŠABLONO  |  📁 {len(g['files'])} failai  ")
                badge.setStyleSheet("""
                    background-color: #451A03;
                    color: #FDE68A;
                    border: 1px solid #D97706;
                    border-radius: 6px;
                    font-size: 11px;
                    font-weight: bold;
                    padding: 4px 8px;
                """)
            elif is_reject:
                badge.setText(f"  🔴 BROKAS (-> BROKAI)  |  📁 {len(g['files'])} failai  |  📐 {g['template_name']}.png  ")
                badge.setStyleSheet("""
                    background-color: #4C0519;
                    color: #FECDD3;
                    border: 1px solid #E11D48;
                    border-radius: 6px;
                    font-size: 11px;
                    font-weight: bold;
                    padding: 4px 8px;
                """)
            else:
                badge.setText(f"  📁 {len(g['files'])} failai  |  📐 {g['template_name']}.png  ")
                badge.setStyleSheet("""
                    background-color: #064E3B;
                    color: #A7F3D0;
                    border: 1px solid #059669;
                    border-radius: 6px;
                    font-size: 11px;
                    font-weight: bold;
                    padding: 4px 8px;
                """)
            c_layout.addWidget(badge)

            quick_btn = PushButton(FIF.PLAY, "Gaminti šį", card)
            quick_btn.setFixedHeight(28)
            if not has_tmpl:
                quick_btn.setEnabled(False)
            quick_btn.clicked.connect(lambda _, grp=g: self._produce_single_group(grp))
            c_layout.addWidget(quick_btn)

            self.group_cards[g["key"]] = card
            self.group_checkboxes[g["key"]] = chk
            self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, card)

        self._update_counter()
        
        info_content = f"Rasta {len(groups)} modelių grupių ({total_files} failų, iš jų {reject_files} brokų)."
        if missing_tmpl_count > 0:
            info_content += f"\n⚠️ Dėmesio: {missing_tmpl_count} grupėms trūksta šablonų Sablonai aplanke."

        InfoBar.success(
            title="Skenavimas baigtas",
            content=info_content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP_RIGHT,
            duration=4000,
            parent=self
        )

    def _filter_groups(self):
        query = self.search_entry.text().strip().lower()
        visible_count = 0

        for g in self.scanned_groups:
            card = self.group_cards.get(g["key"])
            if not card:
                continue

            if query:
                match_date = query in g.get("date", "").lower()
                match_gen = query in g.get("generation", "").lower()
                match_model = query in g.get("model", "").lower()
                match_tmpl = query in g.get("template_name", "").lower()
                match_key = query in g.get("key", "").lower()
                match_files = any(query in os.path.basename(f).lower() for f in g.get("files", []))
                match_reject = ("brok" in query or "reject" in query) and g.get("is_reject", False)

                is_match = match_date or match_gen or match_model or match_tmpl or match_key or match_files or match_reject
            else:
                is_match = True

            card.setVisible(is_match)
            if is_match:
                visible_count += 1

        if visible_count == 0 and len(self.scanned_groups) > 0:
            self.empty_lbl.setText(f"Pagal paiešką '{query}' rezultatų nerasta.")
            self.empty_lbl.setVisible(True)
        elif len(self.scanned_groups) > 0:
            self.empty_lbl.setVisible(False)

        self._update_counter()

    def _update_counter(self):
        selected_files = 0
        selected_groups = 0
        for g in self.scanned_groups:
            chk = self.group_checkboxes.get(g["key"])
            if chk and chk.isChecked() and g.get("has_template"):
                selected_groups += 1
                selected_files += len(g["files"])

        self.produce_btn.setText(f"GAMINTI PAŽYMĖTUS ({selected_files} failų)")

    def _select_all(self):
        query = self.search_entry.text().strip().lower()
        for g in self.scanned_groups:
            card = self.group_cards.get(g["key"])
            chk = self.group_checkboxes.get(g["key"])
            if card and chk and card.isVisible() and g.get("has_template"):
                chk.setChecked(True)
        self._update_counter()

    def _deselect_all(self):
        query = self.search_entry.text().strip().lower()
        for g in self.scanned_groups:
            card = self.group_cards.get(g["key"])
            chk = self.group_checkboxes.get(g["key"])
            if card and chk and card.isVisible():
                chk.setChecked(False)
        self._update_counter()

    def _produce_single_group(self, group: Dict[str, Any]):
        self._start_production([group])

    def _produce_selected(self):
        to_produce = []
        for g in self.scanned_groups:
            chk = self.group_checkboxes.get(g["key"])
            if chk and chk.isChecked() and g.get("has_template"):
                to_produce.append(g)

        if not to_produce:
            InfoBar.warning(
                title="Nėra pasirinkimo",
                content="Prašome varnele pažymėti bent vieną modelį su aktyviu šablonu gamybai!",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP_RIGHT,
                duration=3500,
                parent=self
            )
            return

        self._start_production(to_produce)

    def _start_production(self, groups: List[Dict[str, Any]]):
        self.produce_btn.setEnabled(False)
        self.scan_btn.setEnabled(False)
        self.prog_box.setVisible(True)
        self.progress_bar.setValue(0)
        self.progress_lbl.setText("Pradedama gamyba...")
        self.kpi_status_lbl.setText("⏳ Vyksta gamyba...")
        self.kpi_status_lbl.setStyleSheet("color: #FBBF24; font-weight: bold;")

        watcher = self.main_app.get_watcher_instance()
        self.prod_worker = ProductionWorker(watcher, groups)
        self.prod_worker.progress.connect(self._on_progress)
        self.prod_worker.finished.connect(self._on_production_finished)
        self.prod_worker.log_msg.connect(self.main_app.log)
        self.prod_worker.start()

    def _on_progress(self, cur: int, total: int, fname: str):
        pct = int((cur / total) * 100) if total > 0 else 0
        self.progress_bar.setValue(pct)
        self.progress_lbl.setText(f"⏳ Apdorojama {cur} iš {total} ({pct}%): {fname}")

    def _on_production_finished(self, count: int):
        self.produce_btn.setEnabled(True)
        self.scan_btn.setEnabled(True)
        self.progress_bar.setValue(100)
        self.progress_lbl.setText(f"✅ Sėkmingai sugeneruota {count} failų!")
        self.kpi_status_lbl.setText(f"✅ Gamyba baigta ({count} failų)")
        self.kpi_status_lbl.setStyleSheet("color: #34D399; font-weight: bold;")

        InfoBar.success(
            title="Gamyba Baigta! 🚀",
            content=f"Sėkmingai paruošta {count} UV spaudos failų į READY (ir BROKAI) aplankus.",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP_RIGHT,
            duration=4500,
            parent=self
        )

# =========================================================================
# 2. Vieno Failo Apdorojimo Puslapis (SingleFileInterface)
# =========================================================================
class SingleFileInterface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("singleFileInterface")
        self.main_app = parent
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        title = TitleLabel("Vieno Failo Rankinis Apdorojimas")
        title.setStyleSheet("color: #F8FAFC;")
        sub = CaptionLabel("Greitas 1 failo apkirpimas pagal pasirinktą .PNG šabloną su CMYK + Spot W")
        sub.setStyleSheet("color: #94A3B8;")
        layout.addWidget(title)
        layout.addWidget(sub)

        card = CardWidget(self)
        card.setStyleSheet("""
            CardWidget {
                background-color: #1E293B;
                border: 1px solid #334155;
                border-radius: 10px;
            }
        """)
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(22, 20, 22, 20)
        c_layout.setSpacing(16)

        # 1. Kliento nuotrauka
        lbl1 = StrongBodyLabel("1. Kliento Nuotrauka:")
        lbl1.setStyleSheet("color: #F8FAFC;")
        c_layout.addWidget(lbl1)
        h1 = QHBoxLayout()
        self.img_path_entry = LineEdit(card)
        self.img_path_entry.setPlaceholderText("Pasirinkite kliento nuotrauką (PNG, JPG, TIF)...")
        h1.addWidget(self.img_path_entry)
        b1 = PushButton(FIF.PHOTO, "Naršyti...", card)
        b1.clicked.connect(self._select_cust_img)
        h1.addWidget(b1)
        c_layout.addLayout(h1)

        # 2. Šablono PNG
        lbl2 = StrongBodyLabel("2. Modelio Šablonas (.PNG):")
        lbl2.setStyleSheet("color: #F8FAFC;")
        c_layout.addWidget(lbl2)
        h2 = QHBoxLayout()
        self.tmpl_path_entry = LineEdit(card)
        self.tmpl_path_entry.setPlaceholderText("Pasirinkite šablono .PNG failą iš Sablonai aplanko...")
        h2.addWidget(self.tmpl_path_entry)
        b2 = PushButton(FIF.LABEL, "Pasirinkti Šabloną...", card)
        b2.clicked.connect(self._select_tmpl_img)
        h2.addWidget(b2)
        c_layout.addLayout(h2)

        # 3. Išvesties aplankas
        lbl3 = StrongBodyLabel("3. Išsaugoti Į:")
        lbl3.setStyleSheet("color: #F8FAFC;")
        c_layout.addWidget(lbl3)
        h3 = QHBoxLayout()
        self.out_path_entry = LineEdit(card)
        self.out_path_entry.setText(DEFAULT_OUTPUT)
        h3.addWidget(self.out_path_entry)
        b3 = PushButton(FIF.FOLDER, "Pasirinkti...", card)
        b3.clicked.connect(self._select_out_dir)
        h3.addWidget(b3)
        c_layout.addLayout(h3)

        c_layout.addSpacing(10)

        # Vykdymo mygtukas
        self.process_btn = PrimaryPushButton(FIF.PLAY, "Apdoroti ir Išsaugoti Spaudos Failą (.TIF)", card)
        self.process_btn.setFixedHeight(42)
        self.process_btn.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self.process_btn.clicked.connect(self._process_single)
        c_layout.addWidget(self.process_btn)

        layout.addWidget(card)
        layout.addStretch(1)

    def _select_cust_img(self):
        f, _ = QFileDialog.getOpenFileName(self, "Pasirinkite kliento nuotrauką", "", "Nuotraukos (*.png *.jpg *.jpeg *.webp *.tif *.tiff)")
        if f:
            self.img_path_entry.setText(f)

    def _select_tmpl_img(self):
        initial = self.main_app.settings_interface.tmpl_entry.text().strip()
        f, _ = QFileDialog.getOpenFileName(self, "Pasirinkite šablono .PNG failą", initial, "PNG Šablonai (*.png)")
        if f:
            self.tmpl_path_entry.setText(f)

    def _select_out_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Pasirinkite išvesties aplanką", self.out_path_entry.text())
        if d:
            self.out_path_entry.setText(d)

    def _process_single(self):
        img_p = self.img_path_entry.text().strip()
        tmpl_p = self.tmpl_path_entry.text().strip()
        out_d = self.out_path_entry.text().strip()

        if not os.path.exists(img_p):
            InfoBar.error(title="Klaida", content="Kliento nuotraukos failas nerastas!", parent=self)
            return
        if not os.path.exists(tmpl_p):
            InfoBar.error(title="Klaida", content="Šablono failas nerastas!", parent=self)
            return

        os.makedirs(out_d, exist_ok=True)
        base_name = os.path.splitext(os.path.basename(img_p))[0]
        out_file = os.path.join(out_d, f"{base_name}.tif")

        settings = self.main_app.get_current_settings()
        try:
            self.process_btn.setEnabled(False)
            success = process_and_crop(
                image_path=img_p,
                template_path=tmpl_p,
                output_path=out_file,
                choke_pixels=settings["choke"],
                spot_channel_name=settings["spot_name"],
                solidity=settings["solidity"],
                target_dpi=settings["dpi"]
            )
            self.process_btn.setEnabled(True)
            if success:
                InfoBar.success(
                    title="Sėkmingai Apdorota!",
                    content=f"Failas išsaugotas į: {out_file}",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP_RIGHT,
                    duration=4500,
                    parent=self
                )
                self.main_app.log(f"✅ Vieno failo gamyba baigta: {out_file}")
        except Exception as e:
            self.process_btn.setEnabled(True)
            InfoBar.error(title="Klaida", content=str(e), parent=self)
            self.main_app.log(f"❌ Klaida: {e}")

# =========================================================================
# 3. Nustatymų Puslapis (SettingsInterface) su Konfigūracijos Išsaugojimu
# =========================================================================
class SettingsInterface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("settingsInterface")
        self.main_app = parent
        self._init_ui()

    def _init_ui(self):
        cfg = load_saved_config()

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background-color: transparent; border: none; }")

        container = QWidget()
        container.setStyleSheet("background-color: transparent;")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        # Antraštė
        title = TitleLabel("Sistemos ir Spaudos Nustatymai")
        title.setStyleSheet("color: #F8FAFC;")
        sub = CaptionLabel("Hotfolderių keliai (Standartiniai ir Brokai), šablonų aplankas ir UV spaudos parametrai")
        sub.setStyleSheet("color: #94A3B8;")
        layout.addWidget(title)
        layout.addWidget(sub)

        card_style = """
            CardWidget {
                background-color: #1E293B;
                border: 1px solid #334155;
                border-radius: 10px;
            }
        """

        # 1. KORTELĖ: Aplankų keliai
        folder_card = CardWidget(container)
        folder_card.setStyleSheet(card_style)
        fc_layout = QVBoxLayout(folder_card)
        fc_layout.setContentsMargins(20, 18, 20, 18)
        fc_layout.setSpacing(14)

        h_title_row = QHBoxLayout()
        f_icon_lbl = QLabel("📁")
        f_icon_lbl.setFont(QFont("Segoe UI Emoji", 14))
        h_title_row.addWidget(f_icon_lbl)
        f_head = StrongBodyLabel("Aplankų Keliai (Hotfolderiai ir READY)")
        f_head.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        f_head.setStyleSheet("color: #F8FAFC;")
        h_title_row.addWidget(f_head)
        h_title_row.addStretch(1)
        fc_layout.addLayout(h_title_row)

        # 1.1 Standartinis įvesties Hotfolderis
        l_in = BodyLabel("Standartinis Įvesties Hotfolderis:")
        l_in.setStyleSheet("color: #E2E8F0;")
        fc_layout.addWidget(l_in)
        h1 = QHBoxLayout()
        self.in_entry = LineEdit(folder_card)
        self.in_entry.setText(cfg.get("input_folder", DEFAULT_STD_INPUT))
        self.in_entry.textChanged.connect(self._auto_save)
        h1.addWidget(self.in_entry)
        b1 = PushButton(FIF.FOLDER, "Pasirinkti...", folder_card)
        b1.clicked.connect(self._pick_in)
        h1.addWidget(b1)
        fc_layout.addLayout(h1)

        # 1.2 Brokų / Rejects Hotfolderis
        l_rej = BodyLabel("Brokų / Rejects Hotfolderis (krenta brokai):")
        l_rej.setStyleSheet("color: #E2E8F0;")
        fc_layout.addWidget(l_rej)
        h_rej = QHBoxLayout()
        self.rejects_entry = LineEdit(folder_card)
        self.rejects_entry.setText(cfg.get("rejects_input_folder", DEFAULT_REJECTS_INPUT))
        self.rejects_entry.textChanged.connect(self._auto_save)
        h_rej.addWidget(self.rejects_entry)
        b_rej = PushButton(FIF.FOLDER, "Pasirinkti...", folder_card)
        b_rej.clicked.connect(self._pick_rejects)
        h_rej.addWidget(b_rej)
        fc_layout.addLayout(h_rej)

        rej_note = CaptionLabel("💡 Pastaba: Visi failai iš šio brokų aplanko automatiškai išsaugomi į READY\\BROKAI aplanką.")
        rej_note.setStyleSheet("color: #F87171; font-weight: bold;")
        fc_layout.addWidget(rej_note)

        # 1.3 Išvesties READY Aplankas
        l_out = BodyLabel("Išvesties READY Aplankas (Paruošti spaudos TIF failai):")
        l_out.setStyleSheet("color: #E2E8F0;")
        fc_layout.addWidget(l_out)
        h2 = QHBoxLayout()
        self.out_entry = LineEdit(folder_card)
        self.out_entry.setText(cfg.get("output_folder", DEFAULT_OUTPUT))
        self.out_entry.textChanged.connect(self._auto_save)
        h2.addWidget(self.out_entry)
        b2 = PushButton(FIF.FOLDER, "Pasirinkti...", folder_card)
        b2.clicked.connect(self._pick_out)
        h2.addWidget(b2)
        b2_open = PrimaryPushButton(FIF.QUICK_NOTE, "Atidaryti READY", folder_card)
        b2_open.clicked.connect(self.main_app.open_output_folder)
        h2.addWidget(b2_open)
        b2_brokai = PushButton(FIF.DELETE, "Atidaryti BROKAI", folder_card)
        b2_brokai.setStyleSheet("color: #F87171; font-weight: bold;")
        b2_brokai.clicked.connect(self.main_app.open_brokai_folder)
        h2.addWidget(b2_brokai)
        fc_layout.addLayout(h2)

        # 1.4 Šablonų Aplankas
        l_tmpl = BodyLabel("Šablonų Aplankas (.PNG kontūrai):")
        l_tmpl.setStyleSheet("color: #E2E8F0;")
        fc_layout.addWidget(l_tmpl)
        h_tmpl = QHBoxLayout()
        self.tmpl_entry = LineEdit(folder_card)
        self.tmpl_entry.setText(cfg.get("templates_folder", os.path.join(get_app_dir(), "Sablonai")))
        self.tmpl_entry.textChanged.connect(self._on_tmpl_changed)
        h_tmpl.addWidget(self.tmpl_entry)
        b_tmpl = PushButton(FIF.FOLDER, "Pasirinkti...", folder_card)
        b_tmpl.clicked.connect(self._pick_tmpl)
        h_tmpl.addWidget(b_tmpl)
        b_tmpl_open = PushButton(FIF.LABEL, "Atidaryti", folder_card)
        b_tmpl_open.clicked.connect(self.main_app.open_templates_folder)
        h_tmpl.addWidget(b_tmpl_open)
        fc_layout.addLayout(h_tmpl)

        layout.addWidget(folder_card)

        # 2. KORTELĖ: UV Spaudos Parametrai (CMYK + Spot W)
        print_card = CardWidget(container)
        print_card.setStyleSheet(card_style)
        pc_layout = QVBoxLayout(print_card)
        pc_layout.setContentsMargins(20, 18, 20, 18)
        pc_layout.setSpacing(14)

        p_title_row = QHBoxLayout()
        p_icon_lbl = QLabel("⚙️")
        p_icon_lbl.setFont(QFont("Segoe UI Emoji", 14))
        p_title_row.addWidget(p_icon_lbl)
        p_head = StrongBodyLabel("UV Spaudos Parametrai (ColorGATE & Photoshop)")
        p_head.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        p_head.setStyleSheet("color: #F8FAFC;")
        p_title_row.addWidget(p_head)
        p_title_row.addStretch(1)
        pc_layout.addLayout(p_title_row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(12)

        l_c = BodyLabel("Balto rašalo sutraukimas (Choke):")
        l_c.setStyleSheet("color: #E2E8F0;")
        grid.addWidget(l_c, 0, 0)
        self.choke_spin = SpinBox(print_card)
        self.choke_spin.setRange(0, 20)
        self.choke_spin.setValue(int(cfg.get("choke", 1)))
        self.choke_spin.setSuffix(" px")
        self.choke_spin.setFixedWidth(140)
        self.choke_spin.valueChanged.connect(self._auto_save)
        grid.addWidget(self.choke_spin, 0, 1)

        l_d = BodyLabel("Spaudos raiška (DPI):")
        l_d.setStyleSheet("color: #E2E8F0;")
        grid.addWidget(l_d, 0, 2)
        self.dpi_spin = SpinBox(print_card)
        self.dpi_spin.setRange(72, 1200)
        self.dpi_spin.setValue(int(cfg.get("dpi", 300)))
        self.dpi_spin.setSuffix(" DPI")
        self.dpi_spin.setFixedWidth(140)
        self.dpi_spin.valueChanged.connect(self._auto_save)
        grid.addWidget(self.dpi_spin, 0, 3)

        l_s = BodyLabel("Spot Kanalo Pavadinimas:")
        l_s.setStyleSheet("color: #E2E8F0;")
        grid.addWidget(l_s, 1, 0)
        self.spot_name_entry = LineEdit(print_card)
        self.spot_name_entry.setText(cfg.get("spot_name", "W"))
        self.spot_name_entry.setFixedWidth(140)
        self.spot_name_entry.textChanged.connect(self._auto_save)
        grid.addWidget(self.spot_name_entry, 1, 1)

        l_sol = BodyLabel("Spot Kanalo Tankis (Solidity %):")
        l_sol.setStyleSheet("color: #E2E8F0;")
        grid.addWidget(l_sol, 1, 2)
        self.solidity_spin = SpinBox(print_card)
        self.solidity_spin.setRange(1, 100)
        self.solidity_spin.setValue(int(cfg.get("solidity", 5)))
        self.solidity_spin.setSuffix(" %")
        self.solidity_spin.setFixedWidth(140)
        self.solidity_spin.valueChanged.connect(self._auto_save)
        grid.addWidget(self.solidity_spin, 1, 3)

        pc_layout.addLayout(grid)
        layout.addWidget(print_card)

        # 3. KORTELĖ: Fono Stebėjimas
        watcher_card = CardWidget(container)
        watcher_card.setStyleSheet(card_style)
        wc_layout = QHBoxLayout(watcher_card)
        wc_layout.setContentsMargins(20, 18, 20, 18)
        wc_layout.setSpacing(14)

        w_info = QVBoxLayout()
        w_info.setSpacing(4)
        w_t_row = QHBoxLayout()
        w_icon = QLabel("⚡")
        w_icon.setFont(QFont("Segoe UI Emoji", 14))
        w_t_row.addWidget(w_icon)
        w_head = StrongBodyLabel("Automatinis Fono Stebėjimas (Standartiniai + Brokai)")
        w_head.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        w_head.setStyleSheet("color: #F8FAFC;")
        w_t_row.addWidget(w_head)
        w_t_row.addStretch(1)
        w_info.addLayout(w_t_row)

        w_sub = CaptionLabel("Stebi abu hotfolderius realiu laiku. Standartiniai failai keliauja į READY, o brokai automatiškai į READY\\BROKAI.")
        w_sub.setStyleSheet("color: #94A3B8;")
        w_info.addWidget(w_sub)
        wc_layout.addLayout(w_info)

        wc_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))

        self.watch_switch = SwitchButton(watcher_card)
        self.watch_switch.setOnText("AKTYVUS")
        self.watch_switch.setOffText("IŠJUNGTAS")
        self.watch_switch.checkedChanged.connect(self._toggle_watcher)
        wc_layout.addWidget(self.watch_switch)

        layout.addWidget(watcher_card)

        # 4. KORTELĖ: Darbastalio Nuoroda
        sh_card = CardWidget(container)
        sh_card.setStyleSheet(card_style)
        sh_layout = QHBoxLayout(sh_card)
        sh_layout.setContentsMargins(20, 18, 20, 18)
        sh_layout.setSpacing(14)

        sh_info = QVBoxLayout()
        sh_info.setSpacing(4)
        sh_t_row = QHBoxLayout()
        sh_icon = QLabel("📌")
        sh_icon.setFont(QFont("Segoe UI Emoji", 14))
        sh_t_row.addWidget(sh_icon)
        sh_head = StrongBodyLabel("Nuoroda Darbastalyje (Desktop Shortcut)")
        sh_head.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        sh_head.setStyleSheet("color: #F8FAFC;")
        sh_t_row.addWidget(sh_head)
        sh_t_row.addStretch(1)
        sh_info.addLayout(sh_t_row)

        sh_sub = CaptionLabel("Vienu paspaudimu sukuria gražią „PrintReady PRO“ paleidimo nuorodą ant Darbastalio.")
        sh_sub.setStyleSheet("color: #94A3B8;")
        sh_info.addWidget(sh_sub)
        sh_layout.addLayout(sh_info)

        sh_layout.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))

        self.create_sh_btn = PrimaryPushButton(FIF.PIN, "Sukurti Nuorodą", sh_card)
        self.create_sh_btn.clicked.connect(self._create_desktop_shortcut)
        sh_layout.addWidget(self.create_sh_btn)

        layout.addWidget(sh_card)

        # 5. KORTELĖ: Programos Atnaujinimai (GitHub Releases)
        up_card = CardWidget(container)
        up_card.setStyleSheet(card_style)
        up_layout = QVBoxLayout(up_card)
        up_layout.setContentsMargins(20, 18, 20, 18)
        up_layout.setSpacing(14)

        up_t_row = QHBoxLayout()
        up_icon = QLabel("🚀")
        up_icon.setFont(QFont("Segoe UI Emoji", 14))
        up_t_row.addWidget(up_icon)
        up_head = StrongBodyLabel(f"Programos Atnaujinimai (Dabartinė versija: v{APP_VERSION})")
        up_head.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        up_head.setStyleSheet("color: #F8FAFC;")
        up_t_row.addWidget(up_head)
        up_t_row.addStretch(1)
        up_layout.addLayout(up_t_row)

        up_sub = CaptionLabel("Tikrina „GitHub Releases“ ar yra išleista naujesnė programos versija ir leidžia atsinaujinti vienu paspaudimu.")
        up_sub.setStyleSheet("color: #94A3B8;")
        up_layout.addWidget(up_sub)

        # GitHub Repo eilutė
        h_repo = QHBoxLayout()
        l_repo = BodyLabel("GitHub Saugykla (owner/repo):")
        l_repo.setStyleSheet("color: #E2E8F0;")
        h_repo.addWidget(l_repo)
        self.repo_entry = LineEdit(up_card)
        self.repo_entry.setText(cfg.get("github_repo", DEFAULT_GITHUB_REPO))
        self.repo_entry.textChanged.connect(self._auto_save)
        h_repo.addWidget(self.repo_entry)
        up_layout.addLayout(h_repo)

        # Automatinio tikrinimo jungiklis ir Tikrinimo mygtukas
        h_ctrl = QHBoxLayout()
        self.auto_update_switch = SwitchButton(up_card)
        self.auto_update_switch.setOnText("Auto-tikrinimas ĮJUNGTAS")
        self.auto_update_switch.setOffText("Auto-tikrinimas IŠJUNGTAS")
        self.auto_update_switch.setChecked(cfg.get("auto_check_updates", True))
        self.auto_update_switch.checkedChanged.connect(self._auto_save)
        h_ctrl.addWidget(self.auto_update_switch)

        h_ctrl.addStretch(1)

        self.check_now_btn = PrimaryPushButton(FIF.SYNC, "Tikrinti atnaujinimus dabar", up_card)
        self.check_now_btn.clicked.connect(self._manual_check_updates)
        h_ctrl.addWidget(self.check_now_btn)
        up_layout.addLayout(h_ctrl)

        layout.addWidget(up_card)
        layout.addStretch(1)

        scroll.setWidget(container)
        outer_layout.addWidget(scroll)

    def _manual_check_updates(self):
        if self.main_app:
            self.main_app.check_for_updates_manual()

    def _auto_save(self):
        cfg = {
            "input_folder": self.in_entry.text().strip(),
            "rejects_input_folder": self.rejects_entry.text().strip(),
            "output_folder": self.out_entry.text().strip(),
            "templates_folder": self.tmpl_entry.text().strip(),
            "choke": int(self.choke_spin.value()),
            "dpi": int(self.dpi_spin.value()),
            "spot_name": self.spot_name_entry.text().strip() or "W",
            "solidity": int(self.solidity_spin.value()),
            "github_repo": self.repo_entry.text().strip() if hasattr(self, 'repo_entry') else DEFAULT_GITHUB_REPO,
            "auto_check_updates": self.auto_update_switch.isChecked() if hasattr(self, 'auto_update_switch') else True
        }
        save_config(cfg)

    def _on_tmpl_changed(self):
        self._auto_save()
        new_dir = self.tmpl_entry.text().strip()
        self.main_app.template_manager.set_templates_dir(new_dir)
        self.main_app.reload_templates()

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
                    f"$s = $ws.CreateShortcut('{shortcut_path}'); "
                    f"$s.TargetPath = '{current_exe}'; "
                    f"$s.WorkingDirectory = '{current_dir}'; "
                    f"$s.Description = 'Podbase PrintReady PRO'; "
                    f"$s.Save()"
                )
                subprocess.run(["powershell", "-Command", ps_cmd], capture_output=True)

            InfoBar.success(
                title="Nuoroda sukurta!",
                content="„PrintReady PRO“ nuoroda sėkmingai sukurta ant jūsų Darbastalio (Desktop).",
                position=InfoBarPosition.TOP_RIGHT,
                duration=4000,
                parent=self
            )
        except Exception as e:
            InfoBar.error(title="Klaida", content=str(e), parent=self)

    def _pick_in(self):
        d = QFileDialog.getExistingDirectory(self, "Pasirinkite standartinį įvesties hotfolderį", self.in_entry.text())
        if d:
            self.in_entry.setText(d)
            self._auto_save()

    def _pick_rejects(self):
        d = QFileDialog.getExistingDirectory(self, "Pasirinkite brokų / rejects hotfolderį", self.rejects_entry.text())
        if d:
            self.rejects_entry.setText(d)
            self._auto_save()

    def _pick_out(self):
        d = QFileDialog.getExistingDirectory(self, "Pasirinkite išvesties aplanką", self.out_entry.text())
        if d:
            self.out_entry.setText(d)
            self._auto_save()

    def _pick_tmpl(self):
        d = QFileDialog.getExistingDirectory(self, "Pasirinkite šablonų aplanką (Sablonai)", self.tmpl_entry.text())
        if d:
            self.tmpl_entry.setText(d)
            self._on_tmpl_changed()

    def _toggle_watcher(self, checked: bool):
        if checked:
            self.main_app.start_watcher()
        else:
            self.main_app.stop_watcher()

# =========================================================================
# 4. Žurnalo Puslapis (LogsInterface)
# =========================================================================
class LogsInterface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("logsInterface")
        self.main_app = parent
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        h = QHBoxLayout()
        title = TitleLabel("Sistemos Žurnalas (Console Logs)")
        title.setStyleSheet("color: #F8FAFC;")
        h.addWidget(title)
        h.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))

        clear_btn = PushButton(FIF.DELETE, "Išvalyti Žurnalą", self)
        clear_btn.clicked.connect(self._clear_logs)
        h.addWidget(clear_btn)
        layout.addLayout(h)

        self.log_text = TextEdit(self)
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 10))
        self.log_text.setStyleSheet("""
            TextEdit {
                background-color: #0B1120;
                color: #E2E8F0;
                border: 1px solid #1E293B;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        layout.addWidget(self.log_text)

    def append_log(self, message: str):
        self.log_text.append(message)

    def _clear_logs(self):
        self.log_text.clear()

# =========================================================================
# 5. Pagrindinis FluentWindow Langas
# =========================================================================
class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()

        setTheme(Theme.DARK)
        setThemeColor("#2563EB")

        self.setWindowTitle("Podbase • PrintReady PRO")
        self.setWindowIcon(QIcon(get_resource_path("app_icon.ico")))
        self.resize(1180, 880)

        # Užkrauname nustatymus
        cfg = load_saved_config()
        tmpl_dir = cfg.get("templates_folder", os.path.join(get_app_dir(), "Sablonai"))
        os.makedirs(tmpl_dir, exist_ok=True)
        self.template_manager = TemplateManager(tmpl_dir)

        self.watcher: Optional[OrderWatcher] = None

        # Atnaujinimų valdiklis
        self.updater_manager = AutoUpdaterManager(
            self,
            repo=cfg.get("github_repo", DEFAULT_GITHUB_REPO),
            current_version=APP_VERSION
        )

        # Sukuriame puslapius
        self.orders_interface = OrdersInterface(self)
        self.single_interface = SingleFileInterface(self)
        self.settings_interface = SettingsInterface(self)
        self.logs_interface = LogsInterface(self)

        # Registruojame navigacijos elementus
        self._init_navigation()

        # Atnaujiname šablonus
        self.reload_templates()

        self.log("🚀 Podbase PrintReady PRO sistema paleista!")
        self.log(f"📁 Standartinis Hotfolderis: {cfg.get('input_folder', DEFAULT_STD_INPUT)}")
        self.log(f"🔴 Brokų Hotfolderis: {cfg.get('rejects_input_folder', DEFAULT_REJECTS_INPUT)}")
        self.log(f"📁 Išvesties READY Aplankas: {cfg.get('output_folder', DEFAULT_OUTPUT)}")
        self.log(f"📐 Šablonų aplankas: {tmpl_dir}")

        # Automatinis atnaujinimų patikrinimas fone po 3.5 sekundžių
        if cfg.get("auto_check_updates", True):
            QTimer.singleShot(3500, lambda: self.updater_manager.check_updates_async(is_manual=False))

    def check_for_updates_manual(self):
        if hasattr(self, 'settings_interface') and hasattr(self.settings_interface, 'repo_entry'):
            self.updater_manager.repo = self.settings_interface.repo_entry.text().strip() or DEFAULT_GITHUB_REPO
        self.log(f"🔍 Tikrinami atnaujinimai iš GitHub ({self.updater_manager.repo})...")
        self.updater_manager.check_updates_async(is_manual=True)

    def _init_navigation(self):
        self.addSubInterface(self.orders_interface, FIF.APPLICATION, "Užsakymai")
        self.addSubInterface(self.single_interface, FIF.PHOTO, "1 Failo Įrankis")
        self.addSubInterface(self.settings_interface, FIF.SETTING, "Nustatymai")
        self.addSubInterface(self.logs_interface, FIF.DOCUMENT, "Žurnalas", NavigationItemPosition.BOTTOM)

    def log(self, message: str):
        try:
            print(message.encode(sys.stdout.encoding or 'utf-8', errors='replace').decode(sys.stdout.encoding or 'utf-8'))
        except Exception:
            pass
        self.logs_interface.append_log(message)

    def reload_templates(self):
        self.template_manager.reload_templates()
        count = len(self.template_manager.get_template_names())
        self.orders_interface.update_templates_kpi(count)
        self.log(f"📐 Šablonai atnaujinti. Aktyvių šablonų: {count} ({self.template_manager.templates_dir})")

    def open_templates_folder(self):
        tmpl_dir = self.settings_interface.tmpl_entry.text().strip()
        os.makedirs(tmpl_dir, exist_ok=True)
        subprocess.Popen(f'explorer "{tmpl_dir}"')

    def open_output_folder(self):
        out_dir = self.settings_interface.out_entry.text().strip()
        try:
            os.makedirs(out_dir, exist_ok=True)
            subprocess.Popen(f'explorer "{out_dir}"')
        except Exception as e:
            self.log(f"Klaida atidarant išvesties aplanką: {e}")

    def open_brokai_folder(self):
        out_dir = self.settings_interface.out_entry.text().strip()
        brokai_dir = os.path.join(out_dir, "BROKAI")
        try:
            os.makedirs(brokai_dir, exist_ok=True)
            subprocess.Popen(f'explorer "{brokai_dir}"')
        except Exception as e:
            self.log(f"Klaida atidarant BROKAI aplanką: {e}")

    def get_current_settings(self) -> Dict[str, Any]:
        return {
            "input_folder": self.settings_interface.in_entry.text().strip(),
            "rejects_input_folder": self.settings_interface.rejects_entry.text().strip(),
            "output_folder": self.settings_interface.out_entry.text().strip(),
            "templates_folder": self.settings_interface.tmpl_entry.text().strip(),
            "choke": int(self.settings_interface.choke_spin.value()),
            "dpi": int(self.settings_interface.dpi_spin.value()),
            "spot_name": self.settings_interface.spot_name_entry.text().strip() or "W",
            "solidity": int(self.settings_interface.solidity_spin.value())
        }

    def get_watcher_instance(self) -> OrderWatcher:
        s = self.get_current_settings()
        return OrderWatcher(
            input_folder=s["input_folder"],
            rejects_input_folder=s["rejects_input_folder"],
            output_folder=s["output_folder"],
            templates_folder=s["templates_folder"],
            choke_pixels=s["choke"],
            spot_channel_name=s["spot_name"],
            solidity=s["solidity"],
            target_dpi=s["dpi"],
            log_callback=self.log
        )

    def start_watcher(self):
        if self.watcher and self.watcher.running:
            return
        self.watcher = self.get_watcher_instance()
        self.watcher.start()
        self.orders_interface.kpi_status_lbl.setText("🟢 Fono Stebėjimas AKTYVUS")
        self.orders_interface.kpi_status_lbl.setStyleSheet("color: #10B981; font-weight: bold;")
        InfoBar.success(
            title="Fono Stebėjimas Paleistas",
            content="Hotfolderiai (Standartiniai + Brokai) stebimi realiu laiku.",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP_RIGHT,
            duration=3500,
            parent=self
        )

    def stop_watcher(self):
        if self.watcher:
            self.watcher.stop()
            self.watcher = None
        self.orders_interface.kpi_status_lbl.setText("⚡ Būsena: Paruošta darbui")
        self.orders_interface.kpi_status_lbl.setStyleSheet("color: #34D399; font-weight: bold;")
        InfoBar.info(
            title="Stebėjimas Sustabdytas",
            content="Fono stebėjimas išjungtas.",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP_RIGHT,
            duration=3000,
            parent=self
        )

# =========================================================================
# Pagrindinis Paleidimas su Splash Screen
# =========================================================================
if __name__ == "__main__":
    try:
        import pyi_splash
        pyi_splash.update_text("Inicijuojama PrintReady PRO sistema...")
    except Exception:
        pass

    app = QApplication(sys.argv)
    
    splash = PrintReadySplashScreen()
    splash.show()
    app.processEvents()

    try:
        import pyi_splash
        pyi_splash.close()
    except Exception:
        pass

    splash.set_status("Inicijuojami UV spaudos ir ICC spalvų profiliai...")
    app.processEvents()

    main_window = MainWindow()
    
    splash.set_status("Paruošta! Paleidžiama...")
    app.processEvents()

    main_window.show()
    splash.close()

    sys.exit(app.exec())
