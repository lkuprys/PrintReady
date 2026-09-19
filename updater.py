import os
import sys
import re
import json
import time
import zipfile
import urllib.request
import urllib.error
import subprocess
import tempfile
from typing import Optional, Dict, Any, Tuple

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSize
from PySide6.QtGui import QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QWidget, QFrame, QSizePolicy, QSpacerItem, QApplication
)

from qfluentwidgets import (
    PrimaryPushButton, PushButton, ProgressBar, CardWidget,
    SimpleCardWidget, TitleLabel, SubtitleLabel, StrongBodyLabel,
    BodyLabel, CaptionLabel, TextEdit, InfoBar, InfoBarPosition,
    FluentIcon as FIF
)

APP_VERSION = "2.5.6"
DEFAULT_GITHUB_REPO = "lkuprys/PrintReady"

def parse_version_tuple(v_str: str) -> Tuple[int, ...]:
    """Konvertuoja versijos eilutę (pvz., 'v2.5.1' arba '2.6.0') į sveikųjų skaičių tuple."""
    clean = re.sub(r'^[vV]', '', v_str.strip())
    parts = re.findall(r'\d+', clean)
    return tuple(map(int, parts)) if parts else (0,)

def is_newer_version(latest_str: str, current_str: str = APP_VERSION) -> bool:
    """Grąžina True, jei latest_str yra naujesnė versija nei current_str."""
    try:
        t_latest = parse_version_tuple(latest_str)
        t_curr = parse_version_tuple(current_str)
        return t_latest > t_curr
    except Exception:
        return False

# =========================================================================
# 1. Asynchroninė Versijos Tikrinimo Gija (CheckUpdateWorker)
# =========================================================================
class CheckUpdateWorker(QThread):
    update_available = Signal(dict)
    no_update = Signal(str)
    check_error = Signal(str)

    def __init__(self, repo: str = DEFAULT_GITHUB_REPO, current_version: str = APP_VERSION):
        super().__init__()
        self.repo = repo.strip() or DEFAULT_GITHUB_REPO
        self.current_version = current_version

    def run(self):
        url = f"https://api.github.com/repos/{self.repo}/releases/latest"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Podbase-PrintReady-PRO-Updater",
                "Accept": "application/vnd.github.v3+json"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status != 200:
                    self.check_error.emit(f"HTTP Klaida: {response.status}")
                    return
                data = json.loads(response.read().decode('utf-8'))

            tag_name = data.get("tag_name", "")
            release_name = data.get("name") or tag_name
            changelog = data.get("body", "Nėra pateikto aprašymo.")
            html_url = data.get("html_url", "")
            assets = data.get("assets", [])

            # Ieškome tinkamo vykdomojo failo (.exe) arba zip archyvo
            download_url = None
            asset_name = None
            asset_size = 0

            # 1. Pirmumo teisė: tiesioginis vykdomasis failas (.exe)
            for a in assets:
                name_l = a.get("name", "").lower()
                if name_l.endswith(".exe"):
                    download_url = a.get("browser_download_url")
                    asset_name = a.get("name")
                    asset_size = a.get("size", 0)
                    break

            # 2. Antrinis variantas: .zip archyvas (jei nėra tiesioginio .exe)
            if not download_url:
                for a in assets:
                    name_l = a.get("name", "").lower()
                    if name_l.endswith(".zip"):
                        download_url = a.get("browser_download_url")
                        asset_name = a.get("name")
                        asset_size = a.get("size", 0)
                        break

            # Jei assetų nėra, naudojame release zipball arba html_url
            if not download_url:
                download_url = data.get("zipball_url") or html_url
                asset_name = f"PrintReady_{tag_name}.zip"

            if is_newer_version(tag_name, self.current_version):
                update_info = {
                    "version": tag_name,
                    "title": release_name,
                    "changelog": changelog,
                    "url": download_url,
                    "asset_name": asset_name,
                    "asset_size": asset_size,
                    "release_page": html_url
                }
                self.update_available.emit(update_info)
            else:
                self.no_update.emit(self.current_version)

        except urllib.error.HTTPError as e:
            if e.code == 404:
                self.check_error.emit(f"GitHub saugykla arba Releases nerasta: '{self.repo}'")
            else:
                self.check_error.emit(f"GitHub API klaida ({e.code}): {e.reason}")
        except Exception as e:
            self.check_error.emit(f"Nepavyko patikrinti atnaujinimų: {e}")

# =========================================================================
# 2. Asynchroninė Failo Parsiuntimo Gija (DownloadUpdateWorker)
# =========================================================================
class DownloadUpdateWorker(QThread):
    progress = Signal(int, int, float, str)  # cur_bytes, total_bytes, pct, speed_str
    finished = Signal(str)                  # saved_file_path
    error = Signal(str)

    def __init__(self, download_url: str, target_filename: str):
        super().__init__()
        self.download_url = download_url
        self.target_filename = target_filename
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True

    def run(self):
        temp_dir = tempfile.gettempdir()
        temp_file_path = os.path.join(temp_dir, f"PrintReady_Update_{int(time.time())}_{self.target_filename}")

        req = urllib.request.Request(
            self.download_url,
            headers={"User-Agent": "Podbase-PrintReady-PRO-Updater"}
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                total_bytes = int(response.headers.get('Content-Length', 0))
                downloaded = 0
                start_time = time.time()
                last_time = start_time
                last_downloaded = 0

                with open(temp_file_path, 'wb') as f:
                    while True:
                        if self.is_cancelled:
                            f.close()
                            if os.path.exists(temp_file_path):
                                os.remove(temp_file_path)
                            self.error.emit("Atsisiuntimas atšauktas.")
                            return

                        chunk = response.read(64 * 1024)  # 64 KB blokai
                        if not chunk:
                            break

                        f.write(chunk)
                        downloaded += len(chunk)

                        now = time.time()
                        if now - last_time >= 0.2 or downloaded == total_bytes:
                            speed = (downloaded - last_downloaded) / (now - last_time) if now > last_time else 0
                            speed_mb = speed / (1024 * 1024)
                            speed_str = f"{speed_mb:.1f} MB/s" if speed_mb >= 1.0 else f"{speed/1024:.0f} KB/s"
                            pct = (downloaded / total_bytes * 100.0) if total_bytes > 0 else 0.0
                            self.progress.emit(downloaded, total_bytes, pct, speed_str)
                            last_time = now
                            last_downloaded = downloaded

            self.finished.emit(temp_file_path)

        except Exception as e:
            if os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                except Exception:
                    pass
            self.error.emit(f"Klaida siunčiantis atnaujinimą: {e}")

# =========================================================================
# 3. Matomas Konsolės Atnaujinimo Variklis (CMD Console Updater)
# =========================================================================
def apply_update_and_restart(downloaded_file: str, current_exe: Optional[str] = None):
    """
    Saugiai atnaujina programą parodydamas vartotojui atskirą konsolės langą su eiga
    ir paleidžia atnaujintą programą iš naujo.
    Palaiko tiek tiesioginį .exe failą, tiek .zip archyvą.
    """
    if not current_exe:
        if getattr(sys, 'frozen', False):
            current_exe = os.path.abspath(sys.executable)
        else:
            current_exe = os.path.abspath("PrintReady.exe")

    app_dir = os.path.dirname(current_exe)
    current_pid = os.getpid()
    exe_name = os.path.basename(current_exe)
    temp_dir = tempfile.gettempdir()
    bat_path = os.path.join(temp_dir, f"printready_updater_{int(time.time())}.bat")

    if not os.path.exists(downloaded_file):
        print(f"Klaida: Atnaujinimo failas nerastas: {downloaded_file}")
        return

    is_zip = False
    try:
        is_zip = zipfile.is_zipfile(downloaded_file)
    except Exception:
        is_zip = downloaded_file.lower().endswith(".zip")

    if is_zip:
        prep_script = f"""echo [2/3] Isarchyvuojamas atnaujinimo paketas...
set EXTRACT_DIR=%TEMP%\\PrintReady_Extract_%RANDOM%
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; Expand-Archive -LiteralPath '{downloaded_file}' -DestinationPath '%EXTRACT_DIR%' -Force"
if errorlevel 1 (
    echo KLAIDA: Nepavyko isarchyvuoti atnaujinimo paketo!
    pause
    exit /b 1
)

set SRC_EXE=%EXTRACT_DIR%\\PrintReady.exe
if not exist "%SRC_EXE%" (
    for /r "%EXTRACT_DIR%" %%F in (PrintReady.exe) do set SRC_EXE=%%F
)

if not exist "%SRC_EXE%" (
    echo KLAIDA: Isarchyvuotame pakete nerastas PrintReady.exe failas!
    pause
    exit /b 1
)"""
        extra_copy = f"""if exist "%EXTRACT_DIR%\\us_web_coated_swop_v2.icc" (
    copy /y "%EXTRACT_DIR%\\us_web_coated_swop_v2.icc" "{app_dir}\\" >nul 2>&1
)"""
        cleanup_extra = 'if exist "%EXTRACT_DIR%" rd /s /q "%EXTRACT_DIR%" >nul 2>&1'
    else:
        prep_script = f"""echo [2/3] Ruosiamas atnaujintas programos failas...
set SRC_EXE={downloaded_file}"""
        extra_copy = ""
        cleanup_extra = ""

    bat_script = f"""@echo off
chcp 65001 >nul
title PrintReady PRO Atnaujinimas...
color 0B
echo ================================================================
echo   PrintReady PRO Automatinis Atnaujinimas
echo ================================================================
echo.
echo [1/3] Laukiama, kol visi PrintReady procesai pilnai uzsidarys...

:: Uzdarome visus PrintReady procesus ir ju medi
taskkill /F /IM "{exe_name}" /T >nul 2>&1
taskkill /F /PID {current_pid} /T >nul 2>&1
timeout /t 1 /nobreak >nul

:wait_loop
tasklist /fi "imagename eq {exe_name}" 2>nul | find /i "{exe_name}" >nul
if not errorlevel 1 (
    taskkill /F /IM "{exe_name}" /T >nul 2>&1
    timeout /t 1 /nobreak >nul
    goto wait_loop
)

echo.
{prep_script}

echo.
echo Diegiamas naujas PrintReady.exe failas...
set ATTEMPTS=0

:retry_copy
set /a ATTEMPTS+=1

:: 1. Isvalome sena .old jei yra
if exist "{current_exe}.old" del /f /q "{current_exe}.old" >nul 2>&1

:: 2. Pervadiname esama i .old (Windows leidzia pervadinti net uzrakinta faila)
if exist "{current_exe}" move /y "{current_exe}" "{current_exe}.old" >nul 2>&1

:: 3. Kopijuojame nauja faila
copy /y "%SRC_EXE%" "{current_exe}" >nul 2>&1
if errorlevel 1 (
    if %ATTEMPTS% leq 25 (
        echo   - failas vis dar uzimtas sistemoje, kartojama: bandymas %ATTEMPTS% is 25
        timeout /t 1 /nobreak >nul
        goto retry_copy
    )
    echo.
    echo KLAIDA: Nepavyko atnaujinti failo po 25 bandymu!
    echo Tikslinis kelias: "{current_exe}"
    pause
    exit /b 1
)

:: Nukopijuojame papildomus resursus jei buvo isarchyvuoti
{extra_copy}

:: Patikriname ar naujas failas tikrai vietoje ir netuscias
if not exist "{current_exe}" (
    echo.
    echo KLAIDA: Naujas failas nerastas po atnaujinimo: "{current_exe}"
    pause
    exit /b 1
)

for %%F in ("{current_exe}") do set NEW_SZ=%%~zF
if "%NEW_SZ%"=="" set NEW_SZ=0
if %NEW_SZ% leq 1000000 (
    echo.
    echo KLAIDA: Atnaujinto failo dydis per mazas (%NEW_SZ% baitu)!
    pause
    exit /b 1
)

:: Isvalome laikinus failus
if exist "{current_exe}.old" del /f /q "{current_exe}.old" >nul 2>&1
{cleanup_extra}

echo.
echo Atnaujinimas sekmingai idiegtas! Failo dydis: %NEW_SZ% baitu.
echo [3/3] Paleidziama atnaujinta PrintReady PRO programa...
timeout /t 1 /nobreak >nul
cd /d "{app_dir}"
start "" "{current_exe}"
timeout /t 2 /nobreak >nul
exit
"""

    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(bat_script)

    CREATE_NEW_CONSOLE = 0x00000010
    try:
        subprocess.Popen(
            ["cmd.exe", "/c", bat_path],
            creationflags=CREATE_NEW_CONSOLE,
            stdin=subprocess.DEVNULL,
            stdout=None,
            stderr=None,
            close_fds=True
        )
    except Exception:
        try:
            os.system(f'start "" cmd.exe /c "{bat_path}"')
        except Exception as e:
            print(f"Klaida paleidžiant updater skriptą: {e}")

    # IŠKART kietai uždarome Python procesą, kad atlaisvintume visus failų užraktus
    if QApplication.instance():
        try:
            QApplication.instance().quit()
        except Exception:
            pass
    os._exit(0)

# Suderinamumo pseudonimas senam kodui
perform_in_place_update = apply_update_and_restart

# =========================================================================
# 4. Modernus Fluent Atnaujinimo Patvirtinimo Dialogas (UpdateAvailableDialog)
# =========================================================================
class UpdateAvailableDialog(QDialog):
    def __init__(self, update_info: Dict[str, Any], current_version: str = APP_VERSION, parent=None):
        super().__init__(parent=parent)
        self.update_info = update_info
        self.current_version = current_version
        self.should_update = False

        self.setWindowTitle("Rastas Programos Atnaujinimas")
        self.setFixedSize(540, 420)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setStyleSheet("""
            QDialog {
                background-color: #0F172A;
                color: #F8FAFC;
            }
        """)

        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 24, 24, 20)
        main_layout.setSpacing(16)

        # 1. Antraštės kortelė
        h_box = QHBoxLayout()
        h_box.setSpacing(14)

        icon_lbl = QLabel("🚀")
        icon_lbl.setFont(QFont("Segoe UI Emoji", 26))
        h_box.addWidget(icon_lbl)

        t_layout = QVBoxLayout()
        t_layout.setSpacing(2)
        title = TitleLabel("Rastas naujas atnaujinimas!")
        title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: #F8FAFC;")
        t_layout.addWidget(title)

        ver_lbl = StrongBodyLabel(
            f"Dabartinė versija: v{self.current_version}  ➔  Nauja versija: {self.update_info.get('version', '')}"
        )
        ver_lbl.setStyleSheet("color: #38BDF8; font-size: 13px;")
        t_layout.addWidget(ver_lbl)
        h_box.addLayout(t_layout)
        h_box.addStretch(1)
        main_layout.addLayout(h_box)

        # 2. Pakeitimų sąrašo kortelė (Changelog)
        card = CardWidget(self)
        card.setStyleSheet("""
            CardWidget {
                background-color: #1E293B;
                border: 1px solid #334155;
                border-radius: 8px;
            }
        """)
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(16, 14, 16, 14)
        c_layout.setSpacing(8)

        lbl_ch = StrongBodyLabel("Pakeitimų sąrašas (Changelog):")
        lbl_ch.setStyleSheet("color: #F8FAFC; font-weight: bold;")
        c_layout.addWidget(lbl_ch)

        self.txt_changelog = TextEdit(card)
        self.txt_changelog.setReadOnly(True)
        self.txt_changelog.setMarkdown(self.update_info.get("changelog", "Nėra aprašymo."))
        self.txt_changelog.setStyleSheet("""
            TextEdit {
                background-color: #0B1120;
                color: #E2E8F0;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 8px;
                font-size: 12px;
            }
        """)
        c_layout.addWidget(self.txt_changelog)
        main_layout.addWidget(card)

        # 3. Mygtukų juosta
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        sz_bytes = self.update_info.get("asset_size", 0)
        sz_text = f" ({sz_bytes / (1024*1024):.1f} MB)" if sz_bytes > 0 else ""
        
        hint_lbl = CaptionLabel(f"Failas: {self.update_info.get('asset_name', 'PrintReady.zip')}{sz_text}")
        hint_lbl.setStyleSheet("color: #94A3B8;")
        btn_layout.addWidget(hint_lbl)
        btn_layout.addStretch(1)

        self.later_btn = PushButton(FIF.HISTORY, "Priminti vėliau", self)
        self.later_btn.setFixedHeight(36)
        self.later_btn.clicked.connect(self._on_later)
        btn_layout.addWidget(self.later_btn)

        self.update_btn = PrimaryPushButton(FIF.DOWNLOAD, "Taip, atnaujinti dabar", self)
        self.update_btn.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.update_btn.setFixedHeight(36)
        self.update_btn.clicked.connect(self._on_update)
        btn_layout.addWidget(self.update_btn)

        main_layout.addLayout(btn_layout)

    def _on_update(self):
        self.should_update = True
        self.accept()

    def _on_later(self):
        self.should_update = False
        self.reject()

# =========================================================================
# 5. Progreso Dialogo Langas (DownloadProgressDialog)
# =========================================================================
class DownloadProgressDialog(QDialog):
    def __init__(self, update_info: Dict[str, Any], parent=None):
        super().__init__(parent=parent)
        self.update_info = update_info
        self.downloaded_file: Optional[str] = None

        self.setWindowTitle("Atsisiunčiamas Atnaujinimas...")
        self.setFixedSize(480, 220)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint | Qt.WindowStaysOnTopHint)
        self.setStyleSheet("""
            QDialog {
                background-color: #0F172A;
                color: #F8FAFC;
            }
        """)

        self._init_ui()
        self._start_download()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(14)

        self.title_lbl = TitleLabel("Atsisiunčiama nauja versija...")
        self.title_lbl.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        self.title_lbl.setStyleSheet("color: #F8FAFC;")
        layout.addWidget(self.title_lbl)

        self.status_lbl = BodyLabel("Jungiamasi prie GitHub...")
        self.status_lbl.setStyleSheet("color: #94A3B8;")
        layout.addWidget(self.status_lbl)

        self.prog_bar = ProgressBar(self)
        self.prog_bar.setValue(0)
        self.prog_bar.setFixedHeight(10)
        layout.addWidget(self.prog_bar)

        self.detail_lbl = CaptionLabel("0 MB / 0 MB (0%) • 0 KB/s")
        self.detail_lbl.setStyleSheet("color: #38BDF8; font-weight: bold;")
        layout.addWidget(self.detail_lbl)

        layout.addStretch(1)

        b_row = QHBoxLayout()
        b_row.addStretch(1)
        self.cancel_btn = PushButton(FIF.CLOSE, "Atšaukti", self)
        self.cancel_btn.clicked.connect(self._on_cancel)
        b_row.addWidget(self.cancel_btn)
        layout.addLayout(b_row)

    def _start_download(self):
        url = self.update_info.get("url")
        fname = self.update_info.get("asset_name", "PrintReady.zip")
        self.worker = DownloadUpdateWorker(url, fname)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_progress(self, cur: int, total: int, pct: float, speed_str: str):
        cur_mb = cur / (1024 * 1024)
        total_mb = total / (1024 * 1024) if total > 0 else 0
        self.prog_bar.setValue(int(pct))
        self.status_lbl.setText(f"Atsisiunčiama: {self.update_info.get('version', '')}")
        self.detail_lbl.setText(f"{cur_mb:.1f} MB / {total_mb:.1f} MB ({pct:.1f}%) • {speed_str}")

    def _on_finished(self, file_path: str):
        self.downloaded_file = file_path
        self.prog_bar.setValue(100)
        self.title_lbl.setText("✅ Atsisiuntimas baigtas!")
        self.status_lbl.setText("Programa bus atnaujinta ir paleista iš naujo per kelias sekundes...")
        self.detail_lbl.setText("Diegiamas atnaujinimas...")
        self.cancel_btn.setEnabled(False)

        # Palaukus 1.2 s, paleidžiame saugų atnaujinimo procesą
        QTimer.singleShot(1200, lambda: apply_update_and_restart(file_path))

    def _on_error(self, err_msg: str):
        self.title_lbl.setText("❌ Klaida atsisiunčiant")
        self.status_lbl.setText(err_msg)
        self.detail_lbl.setText("")
        self.cancel_btn.setText("Uždaryti")

    def _on_cancel(self):
        if hasattr(self, 'worker') and self.worker.isRunning():
            self.worker.cancel()
        self.reject()

# =========================================================================
# 6. Pagrindinis Atnaujinimų Valdiklis (UpdaterController)
# =========================================================================
class AutoUpdaterManager:
    """Pagrindinis atnaujinimų valdiklis, integruojamas į MainWindow."""

    def __init__(self, parent_window, repo: str = DEFAULT_GITHUB_REPO, current_version: str = APP_VERSION):
        self.parent = parent_window
        self.repo = repo
        self.current_version = current_version
        self.is_checking = False

    def check_updates_async(self, is_manual: bool = False):
        """Paleidžia atnaujinimų tikrinimą fone."""
        if self.is_checking:
            return
        self.is_checking = True
        self.is_manual = is_manual

        self.worker = CheckUpdateWorker(self.repo, self.current_version)
        self.worker.update_available.connect(self._on_update_available)
        self.worker.no_update.connect(self._on_no_update)
        self.worker.check_error.connect(self._on_check_error)
        self.worker.start()

    def _on_update_available(self, update_info: Dict[str, Any]):
        self.is_checking = False
        dlg = UpdateAvailableDialog(update_info, self.current_version, self.parent)
        if dlg.exec() and dlg.should_update:
            prog_dlg = DownloadProgressDialog(update_info, self.parent)
            prog_dlg.exec()

    def _on_no_update(self, cur_ver: str):
        self.is_checking = False
        if self.is_manual:
            InfoBar.success(
                title="Versija yra naujausia",
                content=f"Naudojate naujausią PrintReady PRO versiją (v{cur_ver}).",
                position=InfoBarPosition.TOP_RIGHT,
                duration=3500,
                parent=self.parent
            )

    def _on_check_error(self, error_msg: str):
        self.is_checking = False
        if self.is_manual:
            InfoBar.warning(
                title="Atnaujinimų patikra",
                content=error_msg,
                position=InfoBarPosition.TOP_RIGHT,
                duration=4000,
                parent=self.parent
            )
