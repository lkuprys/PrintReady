"""
PrintReady PRO išvaizda (Podbase WORK stilius).

Viskas, kas susiję su spalvomis, šriftais ir bendrais valdikliais, yra čia:
  - PALETTES: šviesi ir tamsi spalvų paletės (tikslios brief'o reikšmės),
  - build_qss(): visos programos stilius (QSS) pagal paletę,
  - valdikliai: Card, Badge, ToggleSwitch, SegmentedControl, UnderlineTabs, IconButton, Toast,
    ThemedDialog ir kt.

Valdikliai stilių gauna per objectName / dinamines savybes, todėl temą galima keisti programai veikiant.
"""
import os
import sys
import tempfile
from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, Signal, QTimer, QRectF, QSize, QPoint, QByteArray
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QIcon, QImage, QPainter, QPixmap, QPen, QPalette,
                           QFontMetrics)
from PySide6.QtWidgets import (QAbstractButton, QApplication, QButtonGroup, QDialog, QFrame, QHBoxLayout,
                               QLabel, QPushButton, QVBoxLayout, QWidget, QSizePolicy, QProgressBar)
from PySide6.QtSvg import QSvgRenderer

# =========================================================================
# Paletės
# =========================================================================
LIGHT: Dict[str, str] = {
    "page": "#F8F8F7", "card": "#FFFFFF", "fill": "#F0F0EE",
    "text": "#171717", "text2": "#5F5F5F", "muted": "#8A8A8A", "disabled": "#B5B5B5",
    "border": "#E7E7E5", "divider": "#EEEEEC", "strong": "#D8D8D5",
    "primary": "#111111", "primary_hover": "#2A2A2A", "on_primary": "#FFFFFF",
    "success": "#198754", "success_bg": "#EAF7EF",
    "warning": "#B7791F", "warning_bg": "#FFF7E6",
    "error": "#D64545", "error_bg": "#FDEEEE",
    "info": "#3478F6", "info_bg": "#EEF4FF",
}

DARK: Dict[str, str] = {
    "page": "#121212", "card": "#1E1E1E", "fill": "#2E2E2D",
    "text": "#F5F5F5", "text2": "#A3A3A0", "muted": "#A3A3A0", "disabled": "#5F5F5F",
    "border": "#2C2C2B", "divider": "#2C2C2B", "strong": "#2E2E2D",
    "primary": "#2D2D2D", "primary_hover": "#2E2E2D", "on_primary": "#F5F5F5",
    # Būsenų spalvos lieka to paties atspalvio, fonai – permatomi jų atspalviai
    "success": "#198754", "success_bg": "rgba(25, 135, 84, 0.16)",
    "warning": "#B7791F", "warning_bg": "rgba(183, 121, 31, 0.16)",
    "error": "#D64545", "error_bg": "rgba(214, 69, 69, 0.16)",
    "info": "#3478F6", "info_bg": "rgba(52, 120, 246, 0.16)",
}

PALETTES = {"light": LIGHT, "dark": DARK}
STATUS_KINDS = ("success", "warning", "error", "info", "neutral")

_current: Dict[str, str] = dict(LIGHT)
_current_name = "light"

FONT_FAMILIES = ["Inter", "Segoe UI", "Helvetica Neue", "Arial"]
MONO_FAMILIES = ["Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Courier New"]


def palette() -> Dict[str, str]:
    return _current


def theme_name() -> str:
    return _current_name


def c(key: str) -> QColor:
    """Spalva iš dabartinės paletės kaip QColor (palaiko ir rgba(...))."""
    return parse_color(_current[key])


def parse_color(value: str) -> QColor:
    v = value.strip()
    if v.startswith("rgba"):
        parts = [p.strip() for p in v[v.index("(") + 1:v.index(")")].split(",")]
        col = QColor(int(parts[0]), int(parts[1]), int(parts[2]))
        col.setAlphaF(float(parts[3]))
        return col
    return QColor(v)


def status_border(kind: str) -> str:
    """Šiek tiek tamsesnis būsenos atspalvis rėmeliui (pranešimai, ženkliukai)."""
    col = parse_color(_current.get(kind, _current["muted"]))
    return f"rgba({col.red()}, {col.green()}, {col.blue()}, 0.28)"


# =========================================================================
# Šriftai
# =========================================================================
def _resource_dir() -> str:
    if hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def load_fonts():
    """Užkrauna programos Inter šriftus (assets/fonts). Jei nėra – lieka Segoe UI."""
    font_dir = os.path.join(_resource_dir(), "assets", "fonts")
    if not os.path.isdir(font_dir):
        return
    for f in sorted(os.listdir(font_dir)):
        if f.lower().endswith((".ttf", ".otf")):
            QFontDatabase.addApplicationFont(os.path.join(font_dir, f))


def font(px: int = 14, weight: int = 400, tabular: bool = False, mono: bool = False) -> QFont:
    f = QFont()
    f.setFamilies(MONO_FAMILIES if mono else FONT_FAMILIES)
    f.setPixelSize(px)
    f.setWeight(QFont.Weight(weight))
    if tabular:
        set_tabular(f)
    return f


def set_tabular(f: QFont) -> QFont:
    """Lygaus pločio skaitmenys (OpenType 'tnum') – skaičiai nešokinėja keičiantis reikšmei."""
    try:
        f.setFeature(QFont.Tag("tnum"), 1)
    except Exception:
        pass
    return f


# =========================================================================
# Piktogramos (paprastos linijinės SVG, spalvinamos pagal temą)
# =========================================================================
_ICON_PATHS = {
    "folder": '<path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4.4l2 2h8.6A1.5 1.5 0 0 1 21 9.5v8A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5z"/>',
    "folder_check": '<path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4.4l2 2h8.6A1.5 1.5 0 0 1 21 9.5v8A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5z"/><path d="m9 13.5 2 2 4-4"/>',
    "folder_alert": '<path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4.4l2 2h8.6A1.5 1.5 0 0 1 21 9.5v8A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5z"/><path d="M12 11v3"/><path d="M12 16.5v.01"/>',
    "shapes": '<rect x="4" y="4" width="7" height="7" rx="1.5"/><circle cx="16.5" cy="7.5" r="3.5"/><path d="M7.5 14 11 20H4z"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
    "refresh": '<path d="M20 12a8 8 0 1 1-2.34-5.66"/><path d="M20 4v4.5h-4.5"/>',
    "play": '<path d="M8 5.5v13l10.5-6.5z"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
    "image": '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><circle cx="9" cy="10" r="1.5"/><path d="m20.5 16-4.5-4.5L6 19.5"/>',
    "download": '<path d="M12 4v11"/><path d="m7.5 10.5 4.5 4.5 4.5-4.5"/><path d="M5 19.5h14"/>',
    "close": '<path d="M6.5 6.5l11 11"/><path d="M17.5 6.5l-11 11"/>',
    "check": '<path d="m5.5 12.5 4 4 9-9"/>',
    "check_circle": '<circle cx="12" cy="12" r="8.5"/><path d="m8.5 12.2 2.4 2.4 4.6-4.8"/>',
    "alert": '<path d="M12 4 21 19.5H3z"/><path d="M12 10v4"/><path d="M12 17v.01"/>',
    "error": '<circle cx="12" cy="12" r="8.5"/><path d="m9.2 9.2 5.6 5.6"/><path d="m14.8 9.2-5.6 5.6"/>',
    "info": '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5"/><path d="M12 8v.01"/>',
    "chevron_up": '<path d="m7 14 5-5 5 5"/>',
    "chevron_down": '<path d="m7 10 5 5 5-5"/>',
    "link": '<path d="M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1"/><path d="M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1"/>',
    "copy": '<rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M15.5 8.5V6a1.5 1.5 0 0 0-1.5-1.5H6A1.5 1.5 0 0 0 4.5 6v8A1.5 1.5 0 0 0 6 15.5h2.5"/>',
    "trash": '<path d="M4.5 7h15"/><path d="M9.5 7V5h5v2"/><path d="M6.5 7l1 12.5h9l1-12.5"/>',
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "sync": '<path d="M4 12a8 8 0 0 1 13.66-5.66L20 8.5"/><path d="M20 4v4.5h-4.5"/><path d="M20 12a8 8 0 0 1-13.66 5.66L4 15.5"/><path d="M4 20v-4.5h4.5"/>',
}


def _svg(name: str, color: str, stroke: float = 1.75, fill: str = "none") -> bytes:
    body = _ICON_PATHS[name]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{fill}" stroke="{color}" '
            f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>').encode()


def icon(name: str, color: Optional[str] = None, size: int = 18) -> QIcon:
    """Linijinė piktograma nurodyta spalva (numatyta – antrinis tekstas #5F5F5F)."""
    col = color or _current["text2"]
    renderer = QSvgRenderer(QByteArray(_svg(name, col)))
    ic = QIcon()
    for scale in (1, 2):
        pm = QPixmap(size * scale, size * scale)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        renderer.render(p)
        p.end()
        pm.setDevicePixelRatio(scale)
        ic.addPixmap(pm)
    return ic


def _icon_file(name: str, color: str, stroke: float = 2.0) -> str:
    """
    PNG failas QSS naudojimui (rodyklės, varnelė). PNG, o ne SVG, nes sukompiliuotoje programoje
    QSS paveikslėliams SVG įskiepio gali nebūti. Piešiama 48 px – Qt sumažina iki reikiamo dydžio.
    """
    d = os.path.join(tempfile.gettempdir(), "printready_ui")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{name}_{color.strip('#')}_{str(stroke).replace('.', '_')}.png")
    if not os.path.exists(path):
        img = QImage(48, 48, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        QSvgRenderer(QByteArray(_svg(name, color, stroke))).render(p)
        p.end()
        img.save(path, "PNG")
    return path.replace("\\", "/")


# =========================================================================
# Visos programos stilius
# =========================================================================
def build_qss(p: Dict[str, str]) -> str:
    chev_up = _icon_file("chevron_up", p["text2"])
    chev_down = _icon_file("chevron_down", p["text2"])
    dark = _current_name == "dark"
    # Pažymėtas langelis: šviesioje temoje juodas su balta varnele, tamsioje – šviesus su tamsia
    check = _icon_file("check", p["card"] if dark else p["on_primary"], 2.5)
    status_rules = []
    for kind in ("success", "warning", "error", "info"):
        border = status_border(kind)
        status_rules.append(f"""
QFrame#Alert[kind="{kind}"] {{ background: {p[kind + '_bg']}; border: 1px solid {border}; border-radius: 12px; }}
QFrame#Toast[kind="{kind}"] {{ background: {p['card'] if dark else p[kind + '_bg']}; border: 1px solid {border}; border-radius: 16px; }}
""")
    return f"""
* {{ outline: none; }}
QWidget {{ color: {p['text']}; font-size: 14px; }}
QMainWindow, QWidget#Page, QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget#PageBody {{ background: {p['page']}; }}
QScrollArea {{ border: none; }}
QWidget#PageBody {{ background: {p['page']}; }}

/* Viršutinė juosta ir skirtukai */
QFrame#TopBar {{ background: {p['card']}; border: none; border-bottom: 1px solid {p['border']}; }}
QFrame#TabStrip {{ background: {p['card']}; border: none; border-bottom: 1px solid {p['border']}; }}
QPushButton#Tab {{ background: transparent; border: none; border-bottom: 2px solid transparent; border-radius: 0px;
    color: {p['text2']}; font-size: 13px; font-weight: 500; padding: 0px 2px; }}
QPushButton#Tab:hover {{ color: {p['text']}; }}
QPushButton#Tab:checked {{ color: {p['text']}; border-bottom: 2px solid {p['text']}; font-weight: 600; }}

/* Kortelės */
QFrame#Card {{ background: {p['card']}; border: 1px solid {p['border']}; border-radius: 16px; }}
QFrame#Divider {{ background: {p['divider']}; border: none; min-height: 1px; max-height: 1px; }}
QFrame#TableHeader {{ background: transparent; border: none; border-bottom: 1px solid {p['border']}; }}
QFrame#TableRow {{ background: transparent; border: none; border-bottom: 1px solid {p['divider']}; }}
QFrame#TableRow:hover {{ background: {p['fill']}; }}
QFrame#StatCard {{ background: {p['card']}; border: 1px solid {p['border']}; border-radius: 16px; }}

/* Tekstas */
QLabel {{ background: transparent; border: none; }}
QLabel[role="title"] {{ font-size: 22px; font-weight: 600; color: {p['text']}; }}
QLabel[role="h2"] {{ font-size: 16px; font-weight: 600; color: {p['text']}; }}
QLabel[role="label"] {{ font-size: 13px; font-weight: 600; color: {p['text']}; }}
QLabel[role="body"] {{ font-size: 14px; color: {p['text']}; }}
QLabel[role="secondary"] {{ font-size: 13px; color: {p['text2']}; }}
QLabel[role="muted"] {{ font-size: 12px; color: {p['muted']}; }}
QLabel[role="section"] {{ font-size: 11px; font-weight: 600; color: {p['muted']}; }}
QLabel[role="th"] {{ font-size: 11px; font-weight: 600; color: {p['muted']}; }}
QLabel[role="cell"] {{ font-size: 13px; color: {p['text']}; }}
QLabel[role="cell2"] {{ font-size: 13px; color: {p['text2']}; }}
QLabel[role="stat"] {{ font-size: 24px; font-weight: 600; color: {p['text']}; }}
QLabel[role="empty"] {{ font-size: 14px; color: {p['muted']}; }}
QLabel:disabled {{ color: {p['disabled']}; }}

/* Mygtukai */
QPushButton {{ background: {p['card']}; color: {p['text']}; border: 1px solid {p['border']}; border-radius: 12px;
    padding: 0px 14px; font-size: 14px; font-weight: 500; }}
QPushButton:hover {{ background: {p['fill']}; }}
QPushButton:pressed {{ background: {p['strong']}; }}
QPushButton:disabled {{ color: {p['disabled']}; background: {p['card']}; border-color: {p['divider']}; }}
QPushButton[kind="primary"] {{ background: {p['primary']}; color: {p['on_primary']}; border: 1px solid {p['primary']}; }}
QPushButton[kind="primary"]:hover {{ background: {p['primary_hover']}; border-color: {p['primary_hover']}; }}
QPushButton[kind="primary"]:disabled {{ background: {p['fill']}; color: {p['disabled']}; border-color: {p['fill']}; }}
QPushButton[kind="success"] {{ background: {p['success']}; color: #FFFFFF; border: 1px solid {p['success']}; }}
QPushButton[kind="danger"] {{ background: {p['error']}; color: #FFFFFF; border: 1px solid {p['error']}; }}
QPushButton[kind="ghost"] {{ background: transparent; border: 1px solid transparent; color: {p['text2']}; padding: 0px 10px; }}
QPushButton[kind="ghost"]:hover {{ background: {p['fill']}; color: {p['text']}; }}
QPushButton[kind="icon"] {{ background: transparent; border: 1px solid transparent; padding: 0px; }}
QPushButton[kind="icon"]:hover {{ background: {p['fill']}; }}
QPushButton[size="small"] {{ font-size: 13px; padding: 0px 12px; }}

/* Segmentuotas pasirinkimas */
QFrame#Segmented {{ background: {p['fill']}; border: none; border-radius: 12px; }}
QPushButton#Segment {{ background: transparent; border: 1px solid transparent; border-radius: 12px; color: {p['text2']};
    font-size: 13px; font-weight: 500; padding: 0px 12px; }}
QPushButton#Segment:hover {{ color: {p['text']}; }}
QPushButton#Segment:checked {{ background: {p['card']}; border: 1px solid {p['border']}; color: {p['text']}; }}

/* Įvesties laukai */
QLineEdit, QSpinBox, QComboBox {{ background: {p['card']}; color: {p['text']}; border: 1px solid {p['border']};
    border-radius: 12px; padding: 0px 12px; min-height: 38px; max-height: 38px; font-size: 14px;
    selection-background-color: {p['primary']}; selection-color: {p['on_primary']}; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border: 1px solid {p['primary'] if _current_name == 'light' else p['text2']}; }}
QLineEdit:disabled, QSpinBox:disabled {{ color: {p['disabled']}; background: {p['fill']}; }}
QSpinBox {{ padding-right: 28px; }}
QSpinBox::up-button, QSpinBox::down-button {{ subcontrol-origin: border; width: 24px; border: none; background: transparent; }}
QSpinBox::up-button {{ subcontrol-position: top right; margin: 4px 4px 0px 0px; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; margin: 0px 4px 4px 0px; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: {p['fill']}; border-radius: 12px; }}
QSpinBox::up-arrow {{ image: url({chev_up}); width: 14px; height: 14px; }}
QSpinBox::down-arrow {{ image: url({chev_down}); width: 14px; height: 14px; }}
QPlainTextEdit, QTextEdit {{ background: {p['card']}; color: {p['text']}; border: 1px solid {p['border']};
    border-radius: 12px; padding: 8px 10px; selection-background-color: {p['primary']}; selection-color: {p['on_primary']}; }}

/* Žymimieji langeliai */
QCheckBox {{ spacing: 8px; font-size: 13px; color: {p['text']}; background: transparent; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border: 1px solid {p['strong']}; border-radius: 6px; background: {p['card']}; }}
QCheckBox::indicator:hover {{ border-color: {p['text2']}; }}
QCheckBox::indicator:checked {{ background: {p['primary'] if _current_name == 'light' else p['text']}; border-color: {p['primary'] if _current_name == 'light' else p['text']}; image: url({check}); }}
QCheckBox::indicator:disabled {{ background: {p['fill']}; border-color: {p['divider']}; }}

/* Progresas */
QProgressBar {{ background: {p['fill']}; border: none; border-radius: 3px; min-height: 6px; max-height: 6px; }}
QProgressBar::chunk {{ background: {p['primary'] if _current_name == 'light' else p['text']}; border-radius: 3px; }}

/* Slinkties juostos */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p['strong']}; border-radius: 3px; min-height: 32px; }}
QScrollBar::handle:vertical:hover {{ background: {p['muted']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p['strong']}; border-radius: 3px; min-width: 32px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; border: none; width: 0px; height: 0px; }}

QToolTip {{ background: {p['card']}; color: {p['text']}; border: 1px solid {p['border']}; padding: 4px 8px; font-size: 12px; }}

/* Dialogai, pranešimai */
QFrame#DialogFrame {{ background: {p['card']}; border: 1px solid {p['border']}; border-radius: 24px; }}
QLabel[role="dialogTitle"] {{ font-size: 18px; font-weight: 600; color: {p['text']}; }}
{''.join(status_rules)}
QLabel[status="success"] {{ color: {p['success']}; }}
QLabel[status="warning"] {{ color: {p['warning']}; }}
QLabel[status="error"] {{ color: {p['error']}; }}
QLabel[status="info"] {{ color: {p['info']}; }}
"""


_theme_listeners: List[Callable[[], None]] = []


def on_theme_changed(fn: Callable[[], None]):
    _theme_listeners.append(fn)


def apply_theme(app: QApplication, name: str = "light"):
    """Pritaiko temą visai programai (galima kviesti ir programai veikiant)."""
    global _current_name
    _current_name = name if name in PALETTES else "light"
    _current.clear()
    _current.update(PALETTES[_current_name])
    pal = app.palette()
    pal.setColor(QPalette.ColorRole.PlaceholderText, c("muted"))
    pal.setColor(QPalette.ColorRole.Window, c("page"))
    pal.setColor(QPalette.ColorRole.Base, c("card"))
    pal.setColor(QPalette.ColorRole.Text, c("text"))
    pal.setColor(QPalette.ColorRole.WindowText, c("text"))
    pal.setColor(QPalette.ColorRole.ButtonText, c("text"))
    pal.setColor(QPalette.ColorRole.Highlight, c("primary"))
    pal.setColor(QPalette.ColorRole.HighlightedText, c("on_primary"))
    app.setPalette(pal)
    app.setStyleSheet(build_qss(_current))
    for fn in list(_theme_listeners):
        try:
            fn()
        except RuntimeError:
            _theme_listeners.remove(fn)  # valdiklis jau ištrintas
    for w in app.allWidgets():
        w.update()


def setup_application(app: QApplication, theme: str = "light"):
    app.setStyle("Fusion")
    load_fonts()
    app.setFont(font(14))
    apply_theme(app, theme)


def repolish(w: QWidget):
    """Po dinaminės savybės pakeitimo iš naujo pritaiko stilių."""
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


# =========================================================================
# Pagalbiniai konstruktoriai
# =========================================================================
def label(text: str = "", role: str = "body", parent: Optional[QWidget] = None, tabular: bool = False,
          wrap: bool = False) -> QLabel:
    lbl = QLabel(text, parent)
    lbl.setProperty("role", role)
    if role in ("section", "th"):
        f = font(11, 600)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
        lbl.setFont(f)
        lbl.setText(text.upper())
    if tabular:
        f = lbl.font()
        set_tabular(f)
        lbl.setFont(f)
    if wrap:
        lbl.setWordWrap(True)
    return lbl


def button(text: str = "", kind: str = "secondary", icon_name: Optional[str] = None, size: str = "default",
           parent: Optional[QWidget] = None, tooltip: str = "") -> QPushButton:
    """kind: primary | secondary | success | danger | ghost. size: small (32) | default (40) | large (44)."""
    b = QPushButton(text, parent)
    if kind != "secondary":
        b.setProperty("kind", kind)
    if size == "small":
        b.setProperty("size", "small")
    b.setFixedHeight({"small": 32, "default": 40, "large": 44}.get(size, 40))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if icon_name:
        _set_button_icon(b, icon_name, kind)
    if tooltip:
        b.setToolTip(tooltip)
    return b


def _set_button_icon(b: QPushButton, icon_name: str, kind: str):
    def refresh():
        col = palette()["on_primary"] if kind in ("primary",) else ("#FFFFFF" if kind in ("success", "danger") else None)
        b.setIcon(icon(icon_name, col, 16))
    b.setIconSize(QSize(16, 16))
    refresh()
    on_theme_changed(refresh)


class IconButton(QPushButton):
    """36 px piktogramos mygtukas (viršutinei juostai), pilka piktograma, šviesus fonas užvedus."""

    def __init__(self, icon_name: str, tooltip: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setProperty("kind", "icon")
        self.setFixedSize(36, 36)
        self.setIconSize(QSize(18, 18))
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._icon_name = icon_name
        self._refresh()
        on_theme_changed(self._refresh)

    def _refresh(self):
        self.setIcon(icon(self._icon_name, None, 18))


class Card(QFrame):
    """Balta kortelė su 1 px rėmeliu ir 16 px kampais."""

    def __init__(self, parent: Optional[QWidget] = None, padding: int = 20, spacing: int = 12):
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(padding, padding, padding, padding)
        self.body.setSpacing(spacing)


def divider(parent: Optional[QWidget] = None) -> QFrame:
    d = QFrame(parent)
    d.setObjectName("Divider")
    d.setFixedHeight(1)
    return d


def section_header(title: str, subtitle: str = "", parent: Optional[QWidget] = None) -> QWidget:
    """Mažas DIDŽIOSIOMIS raidėmis užrašas + nebūtinas paaiškinimas."""
    w = QWidget(parent)
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    lay.addWidget(label(title, "section"))
    if subtitle:
        lay.addWidget(label(subtitle, "secondary", wrap=True))
    return w


# =========================================================================
# Ženkliukas (būsena su tašku)
# =========================================================================
class Badge(QWidget):
    """Mažas apvalus ženkliukas: atspalvinis fonas + spalvotas tekstas + taškas."""

    def __init__(self, text: str = "", kind: str = "neutral", parent: Optional[QWidget] = None, dot: bool = True):
        super().__init__(parent)
        self._text, self._kind, self._dot = text, kind, dot
        self._font = font(12, 500, tabular=True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._update_size()

    def set(self, text: str, kind: Optional[str] = None):
        self._text = text
        if kind:
            self._kind = kind
        self._update_size()
        self.update()

    def text(self) -> str:
        return self._text

    def kind(self) -> str:
        return self._kind

    def _update_size(self):
        fm = QFontMetrics(self._font)
        w = fm.horizontalAdvance(self._text) + 20 + (12 if self._dot else 0)
        self.setFixedSize(w, 24)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = palette()
        if self._kind in ("success", "warning", "error", "info"):
            bg, fg = parse_color(pal[self._kind + "_bg"]), parse_color(pal[self._kind])
            border = parse_color(status_border(self._kind))
        else:
            bg, fg, border = parse_color(pal["fill"]), parse_color(pal["text2"]), parse_color(pal["border"])
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(border, 1))
        p.setBrush(bg)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        x = 10
        if self._dot:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fg)
            p.drawEllipse(QRectF(x, self.height() / 2 - 3, 6, 6))
            x += 12
        p.setPen(fg)
        p.setFont(self._font)
        p.drawText(QRectF(x, 0, self.width() - x - 8, self.height()), Qt.AlignmentFlag.AlignVCenter, self._text)


# =========================================================================
# Jungiklis
# =========================================================================
class ToggleSwitch(QAbstractButton):
    """Paprastas jungiklis be animacijų. Signalas checkedChanged(bool) – kaip ir toggled."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(40, 24)
        self.checkedChanged = self.toggled

    def sizeHint(self):
        return QSize(40, 24)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = palette()
        on = self.isChecked()
        if not self.isEnabled():
            track = parse_color(pal["fill"])
        elif on:
            track = parse_color(pal["primary"] if theme_name() == "light" else pal["success"])
        else:
            track = parse_color(pal["strong"] if theme_name() == "light" else pal["fill"])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, 40, 24), 12, 12)
        p.setBrush(QColor("#FFFFFF") if self.isEnabled() else parse_color(pal["divider"]))
        x = 19 if on else 3
        p.drawEllipse(QRectF(x, 3, 18, 18))


class LabeledSwitch(QWidget):
    """Jungiklis su pavadinimu ir paaiškinimu kairėje."""

    def __init__(self, title: str, description: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(label(title, "label"))
        if description:
            text.addWidget(label(description, "secondary", wrap=True))
        lay.addLayout(text, 1)
        self.switch = ToggleSwitch(self)
        lay.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignVCenter)


# =========================================================================
# Segmentuotas pasirinkimas ir pabraukti skirtukai
# =========================================================================
class SegmentedControl(QFrame):
    changed = Signal(str)

    def __init__(self, options: List[Tuple[str, str]], parent: Optional[QWidget] = None):
        """options: [(raktas, tekstas), ...]"""
        super().__init__(parent)
        self.setObjectName("Segmented")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: Dict[str, QPushButton] = {}
        for key, text in options:
            b = QPushButton(text, self)
            b.setObjectName("Segment")
            b.setCheckable(True)
            b.setFixedHeight(30)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self._group.addButton(b)
            self._buttons[key] = b
            lay.addWidget(b)
        if options:
            self._buttons[options[0][0]].setChecked(True)
        self.setFixedHeight(36)

    def value(self) -> str:
        for k, b in self._buttons.items():
            if b.isChecked():
                return k
        return ""

    def set_value(self, key: str, emit: bool = False):
        if key in self._buttons:
            self._buttons[key].setChecked(True)
            if emit:
                self.changed.emit(key)

    def set_text(self, key: str, text: str):
        if key in self._buttons:
            self._buttons[key].setText(text)


class UnderlineTabs(QFrame):
    """Pagrindiniai skyriai: 13 px tekstas, aktyvus – tamsus su 2 px pabraukimu."""
    changed = Signal(int)

    def __init__(self, titles: List[str], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("TabStrip")
        self.setFixedHeight(45)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(24, 0, 24, 0)
        lay.setSpacing(24)
        self._group = QButtonGroup(self)
        self.buttons: List[QPushButton] = []
        for i, t in enumerate(titles):
            b = QPushButton(t, self)
            b.setObjectName("Tab")
            b.setCheckable(True)
            b.setFixedHeight(44)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, idx=i: self.changed.emit(idx))
            self._group.addButton(b, i)
            self.buttons.append(b)
            lay.addWidget(b)
        lay.addStretch(1)
        if self.buttons:
            self.buttons[0].setChecked(True)

    def set_current(self, idx: int):
        if 0 <= idx < len(self.buttons):
            self.buttons[idx].setChecked(True)


class ThinProgress(QProgressBar):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setTextVisible(False)
        self.setRange(0, 100)
        self.setFixedHeight(6)


# =========================================================================
# Pranešimai (toast) ir įspėjimų kortelės
# =========================================================================
_STATUS_ICON = {"success": "check_circle", "warning": "alert", "error": "error", "info": "info"}


class Alert(QFrame):
    """Šviesi atspalvinė kortelė: piktograma + trumpas tekstas (+ nebūtinas veiksmas)."""

    def __init__(self, kind: str = "info", text: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Alert")
        self._icon = QLabel(self)
        self._text = label(text, "body", wrap=True)
        self._text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(10)
        lay.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self._text, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        lay.addLayout(self.actions)
        self.set(kind, text)
        on_theme_changed(lambda: self.set(self._kind, self._text.text()))

    def set(self, kind: str, text: str):
        self._kind = kind
        self.setProperty("kind", kind)
        self._text.setText(text)
        self._icon.setPixmap(icon(_STATUS_ICON.get(kind, "info"), palette().get(kind, palette()["text2"]), 18).pixmap(18, 18))
        repolish(self)


class Toast(QFrame):
    closed = Signal(object)

    def __init__(self, kind: str, title: str, text: str, parent: QWidget, duration: int = 4000):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setProperty("kind", kind if kind in _STATUS_ICON else "info")
        self.setFixedWidth(380)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 10, 12)
        lay.setSpacing(10)
        ic = QLabel(self)
        ic.setPixmap(icon(_STATUS_ICON.get(kind, "info"), palette().get(kind, palette()["info"]), 18).pixmap(18, 18))
        lay.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        if title:
            col.addWidget(label(title, "label"))
        if text:
            col.addWidget(label(text, "secondary", wrap=True))
        lay.addLayout(col, 1)
        close = QPushButton(self)
        close.setProperty("kind", "icon")
        close.setFixedSize(24, 24)
        close.setIcon(icon("close", None, 14))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(self.dismiss)
        lay.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        if duration and duration > 0:
            QTimer.singleShot(duration, self.dismiss)

    def dismiss(self):
        if self.isVisible():
            self.hide()
            self.closed.emit(self)
            self.deleteLater()


class ToastHost:
    """Pranešimų vieta lango viršuje dešinėje (pranešimai rikiuojami vienas po kitu)."""

    def __init__(self, window: QWidget, top_offset: int = 64):
        self.window = window
        self.top_offset = top_offset
        self.toasts: List[Toast] = []

    def show(self, kind: str, title: str, text: str = "", duration: int = 4000):
        t = Toast(kind, title, text, self.window, duration)
        t.closed.connect(self._remove)
        self.toasts.append(t)
        t.adjustSize()
        t.show()
        t.raise_()
        self.relayout()

    def _remove(self, t):
        if t in self.toasts:
            self.toasts.remove(t)
        self.relayout()

    def relayout(self):
        y = self.top_offset
        for t in self.toasts:
            t.adjustSize()
            t.move(self.window.width() - t.width() - 20, y)
            y += t.height() + 8


def notify(widget: Optional[QWidget], kind: str, title: str, text: str = "", duration: int = 4000):
    """Parodo pranešimą artimiausiame lange, turinčiame ToastHost (toast_host atributą)."""
    w = widget
    while w is not None:
        host = getattr(w, "toast_host", None)
        if host is not None:
            host.show(kind, title, text, duration)
            return
        w = w.parentWidget()
    app = QApplication.instance()
    for top in (app.topLevelWidgets() if app else []):
        host = getattr(top, "toast_host", None)
        if host is not None and top.isVisible():
            host.show(kind, title, text, duration)
            return


# =========================================================================
# Dialogai
# =========================================================================
class ThemedDialog(QDialog):
    """Baltas dialogas su 24 px kampais, pavadinimu ir veiksmais apačioje dešinėje."""

    def __init__(self, title: str, parent: Optional[QWidget] = None, width: int = 520):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle(title)
        self._drag: Optional[QPoint] = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.frame = QFrame(self)
        self.frame.setObjectName("DialogFrame")
        outer.addWidget(self.frame)
        self.body = QVBoxLayout(self.frame)
        self.body.setContentsMargins(24, 22, 24, 20)
        self.body.setSpacing(14)
        self.title_label = label(title, "dialogTitle")
        self.body.addWidget(self.title_label)
        self.content = QVBoxLayout()
        self.content.setSpacing(12)
        self.body.addLayout(self.content, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        self.actions.addStretch(1)
        self.body.addLayout(self.actions)
        self.setFixedWidth(width)

    def add_action(self, b: QPushButton):
        self.actions.addWidget(b)
        return b

    # Rėmelio neturintį dialogą galima tempti už bet kurios vietos
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag = None
        super().mouseReleaseEvent(e)


def message_dialog(parent: Optional[QWidget], title: str, text: str, kind: str = "info",
                   ok_text: str = "Gerai") -> None:
    dlg = ThemedDialog(title, parent)
    dlg.content.addWidget(Alert(kind, text, dlg))
    ok = dlg.add_action(button(ok_text, "primary"))
    ok.clicked.connect(dlg.accept)
    dlg.exec()


def confirm_dialog(parent: Optional[QWidget], title: str, text: str, confirm_text: str,
                   destructive: bool = False, cancel_text: str = "Atšaukti") -> bool:
    dlg = ThemedDialog(title, parent, 460)
    dlg.content.addWidget(label(text, "secondary", wrap=True))
    cancel = dlg.add_action(button(cancel_text))
    ok = dlg.add_action(button(confirm_text, "danger" if destructive else "primary"))
    cancel.clicked.connect(dlg.reject)
    ok.clicked.connect(dlg.accept)
    return dlg.exec() == QDialog.DialogCode.Accepted
