"""
PrintReady PRO automatinis atnaujinimas per GitHub Releases.

Grandinė:
  1. Tikrinimas: GitHub API releases/latest (paleidus ir kas 30 min.).
  2. Asset: tik PrintReady_vX.Y.Z.zip (pagal šabloną, versija turi sutapti su tag'u).
  3. Atsisiuntimas: curl -f -L (su progresu), atsarginis būdas – urllib su įjungtu SSL.
  4. Patikra: dydis, SHA-256, ZIP vientisumas, ar yra PrintReady/PrintReady.exe ir _internal.
  5. Išarchyvavimas į %TEMP% staging aplanką (programa dar veikia – klaidos atveju nieko nepakeista).
  6. PowerShell skriptas: laukia, kol programa užsidarys, daro atsarginę kopiją, kopijuoja,
     klaidos atveju atstato seną versiją ir VISADA vėl paleidžia programą.
  7. Paleidus: perskaitomas rezultato failas ir parodomas pranešimas.
"""
import os
import re
import sys
import json
import time
import shutil
import hashlib
import zipfile
import threading
import subprocess
import tempfile
import urllib.request
import urllib.error
from typing import Optional, Dict, Any, Tuple, Callable, List

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QApplication

from qfluentwidgets import (
    PrimaryPushButton, PushButton, ProgressBar, CardWidget,
    TitleLabel, StrongBodyLabel, BodyLabel, CaptionLabel, TextEdit,
    InfoBar, InfoBarPosition, MessageBox, FluentIcon as FIF
)

APP_VERSION = "2.5.8"
DEFAULT_GITHUB_REPO = "lkuprys/PrintReady"

APP_NAME = "PrintReady"
EXE_NAME = "PrintReady.exe"
# Release'e imamas TIK šis ZIP (pvz. PrintReady_v2.5.8.zip)
ASSET_PATTERN = re.compile(r"^PrintReady_v(\d+\.\d+\.\d+)\.zip$", re.IGNORECASE)
# ZIP viduje turi būti šis aplankas su exe ir _internal
ZIP_ROOT_DIR = "PrintReady"

# Vietiniai failai/aplankai, kurių atnaujinimas NIEKADA neperrašo
PROTECTED_FILES = ["config.json"]
PROTECTED_DIRS = ["Sablonai"]

CHECK_INTERVAL_MS = 30 * 60 * 1000      # periodinis tikrinimas kas 30 min.
STARTUP_CHECK_DELAY_MS = 5000           # pirmas tikrinimas praėjus 5 s po paleidimo
SNOOZE_SECONDS = 4 * 60 * 60            # „Priminti vėliau“ atideda 4 val.
USER_AGENT = "Podbase-PrintReady-PRO-Updater"


class UpdateError(Exception):
    """Klaida, kurios tekstas rodomas vartotojui."""


# =========================================================================
# Laikinų failų keliai
# =========================================================================
def get_temp_dir() -> str:
    return tempfile.gettempdir()


def get_work_dir() -> str:
    """Aplankas atsisiuntimui ir staging (išvalomas prieš kiekvieną atnaujinimą)."""
    return os.path.join(get_temp_dir(), "PrintReady_update")


def get_result_path() -> str:
    return os.path.join(get_temp_dir(), "printready_update_result.json")


def get_log_path() -> str:
    return os.path.join(get_temp_dir(), "printready_updater.log")


# =========================================================================
# Versijos
# =========================================================================
def parse_version_tuple(v_str: str) -> Tuple[int, ...]:
    """Konvertuoja versijos eilutę (pvz., 'v2.5.1' arba '2.6.0') į sveikųjų skaičių tuple."""
    clean = re.sub(r'^[vV]', '', (v_str or "").strip())
    parts = re.findall(r'\d+', clean)
    return tuple(map(int, parts)) if parts else (0,)


def is_newer_version(latest_str: str, current_str: str = APP_VERSION) -> bool:
    """Grąžina True, jei latest_str yra naujesnė versija nei current_str."""
    try:
        a = list(parse_version_tuple(latest_str))
        b = list(parse_version_tuple(current_str))
        n = max(len(a), len(b))
        a += [0] * (n - len(a))
        b += [0] * (n - len(b))
        return tuple(a) > tuple(b)
    except Exception:
        return False


def normalize_version(v_str: str) -> str:
    return re.sub(r'^[vV]', '', (v_str or "").strip())


# =========================================================================
# Release analizė
# =========================================================================
def select_release_asset(assets: List[Dict[str, Any]], tag_name: str) -> Optional[Dict[str, Any]]:
    """Grąžina tik tikslų PrintReady_vX.Y.Z.zip asset'ą, kurio versija sutampa su tag'u."""
    want = normalize_version(tag_name)
    for a in assets or []:
        m = ASSET_PATTERN.match(a.get("name", "") or "")
        if m and m.group(1) == want and a.get("browser_download_url"):
            return a
    return None


def parse_digest(digest: Optional[str]) -> Optional[str]:
    """GitHub asset 'digest' laukas: 'sha256:<hex>'. Grąžina hex arba None."""
    if not digest or not isinstance(digest, str):
        return None
    m = re.match(r"^sha256:([0-9a-fA-F]{64})$", digest.strip())
    return m.group(1).lower() if m else None


def build_update_info(data: Dict[str, Any]) -> Dict[str, Any]:
    tag_name = data.get("tag_name", "") or ""
    asset = select_release_asset(data.get("assets", []), tag_name)
    return {
        "version": normalize_version(tag_name),
        "tag": tag_name,
        "title": data.get("name") or tag_name,
        "changelog": (data.get("body") or "").strip() or "Pakeitimų aprašymas nepateiktas.",
        "release_page": data.get("html_url", ""),
        "asset_found": asset is not None,
        "url": asset.get("browser_download_url") if asset else None,
        "asset_name": asset.get("name") if asset else None,
        "asset_size": int(asset.get("size") or 0) if asset else 0,
        "sha256": parse_digest(asset.get("digest")) if asset else None,
    }


def fetch_latest_release(repo: str, timeout: int = 15) -> Dict[str, Any]:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    try:
        # urlopen naudoja numatytąjį SSL kontekstą (sertifikatai tikrinami)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError(f"GitHub'e nerasta išleistų versijų saugykloje „{repo}“.")
        if e.code == 403:
            raise UpdateError("GitHub laikinai riboja užklausas (403). Bus bandyta vėliau.")
        raise UpdateError(f"GitHub klaida ({e.code}): {e.reason}")
    except urllib.error.URLError as e:
        raise UpdateError(f"Nepavyko prisijungti prie GitHub: {e.reason}")
    except Exception as e:
        raise UpdateError(f"Nepavyko patikrinti atnaujinimų: {e}")


# =========================================================================
# Atsisiuntimas
# =========================================================================
ProgressCb = Callable[[int, int], None]


def _find_curl() -> Optional[str]:
    if os.name == "nt":
        sys_curl = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "curl.exe")
        if os.path.exists(sys_curl):
            return sys_curl
    return shutil.which("curl")


def _download_with_curl(curl: str, url: str, dest: str, total: int,
                        progress: Optional[ProgressCb], cancel: threading.Event) -> None:
    cmd = [curl, "-f", "-L", "-sS", "--retry", "3", "--retry-delay", "2",
           "--connect-timeout", "20", "-A", USER_AGENT, "-o", dest, url]
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, creationflags=flags)
    while proc.poll() is None:
        if cancel.is_set():
            proc.kill()
            proc.wait()
            raise UpdateError("Atsisiuntimas atšauktas.")
        if progress:
            try:
                progress(os.path.getsize(dest) if os.path.exists(dest) else 0, total)
            except OSError:
                pass
        time.sleep(0.25)
    _, err_b = proc.communicate()
    err = (err_b or b"").decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        raise UpdateError(f"curl klaida (kodas {proc.returncode}): {err or 'nežinoma'}")


def _download_with_urllib(url: str, dest: str, total: int,
                          progress: Optional[ProgressCb], cancel: threading.Event) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        # Numatytasis SSL kontekstas – sertifikatai TIKRINAMI
        with urllib.request.urlopen(req, timeout=30) as resp, open(dest, "wb") as f:
            done = 0
            while True:
                if cancel.is_set():
                    raise UpdateError("Atsisiuntimas atšauktas.")
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    except urllib.error.HTTPError as e:
        raise UpdateError(f"Serveris grąžino klaidą {e.code}: {e.reason}")
    except UpdateError:
        raise
    except Exception as e:
        raise UpdateError(f"Atsisiuntimo klaida: {e}")


def download_file(url: str, dest: str, total: int, progress: Optional[ProgressCb] = None,
                  cancel: Optional[threading.Event] = None) -> None:
    cancel = cancel or threading.Event()
    curl = _find_curl()
    curl_err = None
    if curl:
        try:
            _download_with_curl(curl, url, dest, total, progress, cancel)
            return
        except UpdateError as e:
            if cancel.is_set():
                raise
            curl_err = str(e)
    try:
        _download_with_urllib(url, dest, total, progress, cancel)
    except UpdateError as e:
        if curl_err:
            raise UpdateError(f"{e}\n(curl: {curl_err})")
        raise


# =========================================================================
# Patikra ir išarchyvavimas
# =========================================================================
def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_zip(path: str, expected_size: int = 0, expected_sha256: Optional[str] = None) -> None:
    """Patikrina atsisiųstą ZIP. Bet kokia problema – UpdateError su aiškiu tekstu."""
    if not os.path.isfile(path):
        raise UpdateError("Atsisiųstas failas nerastas.")
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(4)
    if head[:2] != b"PK":
        raise UpdateError("Atsisiųstas failas nėra ZIP archyvas (greičiausiai gautas klaidos puslapis).")
    if expected_size and size != expected_size:
        raise UpdateError(f"Atsisiųsto failo dydis neteisingas: {size} B, turi būti {expected_size} B.")
    if expected_sha256 and sha256_of(path) != expected_sha256.lower():
        raise UpdateError("Atsisiųsto failo kontrolinė suma (SHA-256) nesutampa – failas sugadintas.")
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
            if bad:
                raise UpdateError(f"ZIP archyvas sugadintas (failas: {bad}).")
            names = set(n.replace("\\", "/") for n in zf.namelist())
    except zipfile.BadZipFile:
        raise UpdateError("ZIP archyvas sugadintas arba neužbaigtas.")
    if f"{ZIP_ROOT_DIR}/{EXE_NAME}" not in names:
        raise UpdateError(f"ZIP archyve nėra {ZIP_ROOT_DIR}/{EXE_NAME}.")
    if not any(n.startswith(f"{ZIP_ROOT_DIR}/_internal/") for n in names):
        raise UpdateError(f"ZIP archyve nėra {ZIP_ROOT_DIR}/_internal aplanko.")


def extract_to_staging(zip_path: str, staging_dir: str) -> str:
    """Išarchyvuoja ZIP į staging ir grąžina kelią iki naujo programos aplanko."""
    if os.path.exists(staging_dir):
        shutil.rmtree(staging_dir, ignore_errors=True)
    os.makedirs(staging_dir, exist_ok=True)
    root = os.path.realpath(staging_dir)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.infolist():
                target = os.path.realpath(os.path.join(staging_dir, member.filename))
                if not (target == root or target.startswith(root + os.sep)):
                    raise UpdateError(f"ZIP archyve neleistinas kelias: {member.filename}")
            zf.extractall(staging_dir)
    except UpdateError:
        raise
    except Exception as e:
        raise UpdateError(f"Nepavyko išarchyvuoti atnaujinimo: {e}")
    app_dir = os.path.join(staging_dir, ZIP_ROOT_DIR)
    if not os.path.isfile(os.path.join(app_dir, EXE_NAME)):
        raise UpdateError(f"Išarchyvavus nerastas {EXE_NAME}.")
    if not os.path.isdir(os.path.join(app_dir, "_internal")):
        raise UpdateError("Išarchyvavus nerastas _internal aplankas.")
    return app_dir


# =========================================================================
# PowerShell atnaujinimo skriptas
# =========================================================================
def ps_quote(value: str) -> str:
    """Saugi PowerShell eilutė viengubose kabutėse ($ ir ` nėra interpretuojami)."""
    return "'" + str(value).replace("'", "''") + "'"


def _ps_array(items: List[str]) -> str:
    return "@(" + ", ".join(ps_quote(i) for i in items) + ")"


PS_TEMPLATE = r"""# PrintReady PRO atnaujinimo skriptas (sugeneruotas automatiškai)
$ErrorActionPreference = 'Stop'
$AppDir        = __APP_DIR__
$ExeName       = __EXE_NAME__
$NewDir        = __NEW_DIR__
$WorkDir       = __WORK_DIR__
$OldPid        = __PID__
$NewVersion    = __NEW_VERSION__
$OldVersion    = __OLD_VERSION__
$LogPath       = __LOG_PATH__
$ResultPath    = __RESULT_PATH__
$ProtectedFiles = __PROTECTED_FILES__
$ProtectedDirs  = __PROTECTED_DIRS__
$Robocopy      = 'robocopy.exe'

$ExePath      = Join-Path $AppDir $ExeName
$ExeOld       = $ExePath + '.old'
$InternalDir  = Join-Path $AppDir '_internal'
$InternalOld  = $InternalDir + '.old'

function Log([string]$msg) {
    $line = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '  ' + $msg
    try { Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8 } catch {}
}

function Invoke-Robo([string[]]$RoboArgs) {
    Log ('robocopy ' + ($RoboArgs -join ' '))
    $out = & $Robocopy @RoboArgs 2>&1 | Out-String
    $code = $LASTEXITCODE
    if ($out) { Log $out.Trim() }
    if ($code -ge 8) { throw ('robocopy klaida, kodas ' + $code) }
}

function Remove-Safe([string]$p) {
    if (Test-Path -LiteralPath $p) {
        try { Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction Stop }
        catch {
            $alt = $p + '.del' + (Get-Date -Format 'yyyyMMddHHmmss')
            Log ('Nepavyko ištrinti ' + $p + ', pervadinama į ' + $alt)
            Rename-Item -LiteralPath $p -NewName (Split-Path $alt -Leaf) -Force
        }
    }
}

function Test-Unlocked([string]$p) {
    if (-not (Test-Path -LiteralPath $p)) { return $true }
    try {
        $fs = [System.IO.File]::Open($p, 'Open', 'ReadWrite', 'None')
        $fs.Close()
        return $true
    } catch { return $false }
}

$result = [ordered]@{ ok = $false; version = $NewVersion; from_version = $OldVersion; message = ''; log = $LogPath }
$backupDone = $false
$hadInternal = $false

try {
    Log ('===== Atnaujinimas ' + $OldVersion + ' -> ' + $NewVersion + ' =====')
    Log ('Programos aplankas: ' + $AppDir)

    # 1. Laukiame, kol programa užsidarys
    try {
        $p = Get-Process -Id $OldPid -ErrorAction Stop
        Log ('Laukiama, kol užsidarys procesas ' + $OldPid)
        if (-not $p.WaitForExit(60000)) {
            Log 'Procesas neužsidarė per 60 s – uždaroma priverstinai'
            Stop-Process -Id $OldPid -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2
        }
    } catch { Log 'Programos procesas jau užsidaręs' }

    # Uždarome kitus TO PATIES exe kelio procesus (kitų aplankų nelieciame)
    $fullExe = [System.IO.Path]::GetFullPath($ExePath)
    foreach ($proc in @(Get-Process -ErrorAction SilentlyContinue)) {
        try {
            if ($proc.Id -ne $PID -and $proc.Path -and ([System.IO.Path]::GetFullPath($proc.Path) -ieq $fullExe)) {
                Log ('Uždaromas kitas programos procesas ' + $proc.Id)
                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            }
        } catch {}
    }

    # 2. Laukiame, kol exe bus atrakintas (iki 30 s)
    $deadline = (Get-Date).AddSeconds(30)
    while (-not (Test-Unlocked $ExePath)) {
        if ((Get-Date) -gt $deadline) { throw 'Programos failas vis dar užrakintas po 30 s' }
        Start-Sleep -Milliseconds 500
    }

    if (-not (Test-Path -LiteralPath (Join-Path $NewDir $ExeName))) { throw 'Staging aplanke nėra naujo exe' }
    if (-not (Test-Path -LiteralPath (Join-Path $NewDir '_internal'))) { throw 'Staging aplanke nėra _internal' }

    # 3. Atsarginė kopija
    Remove-Safe $ExeOld
    Remove-Safe $InternalOld
    if (Test-Path -LiteralPath $InternalDir) {
        $hadInternal = $true
        Rename-Item -LiteralPath $InternalDir -NewName (Split-Path $InternalOld -Leaf)
    }
    $backupDone = $true
    if (Test-Path -LiteralPath $ExePath) {
        Rename-Item -LiteralPath $ExePath -NewName (Split-Path $ExeOld -Leaf)
    }
    Log 'Atsarginė kopija padaryta'

    # 4. Kopijavimas: švarus _internal, tada šakninis aplankas be vietinių failų
    Invoke-Robo @((Join-Path $NewDir '_internal'), $InternalDir, '/E', '/R:3', '/W:1', '/NP', '/NJH', '/NJS')
    $rootArgs = @($NewDir, $AppDir, '/E', '/R:3', '/W:1', '/NP', '/NJH', '/NJS', '/XD', '_internal') + $ProtectedDirs
    if ($ProtectedFiles.Count -gt 0) { $rootArgs += @('/XF') + $ProtectedFiles }
    Invoke-Robo $rootArgs

    # Vietiniai failai/aplankai: kopijuojami iš paketo TIK jei jų dar nėra
    foreach ($n in @($ProtectedFiles + $ProtectedDirs)) {
        $src = Join-Path $NewDir $n
        $dst = Join-Path $AppDir $n
        if ((Test-Path -LiteralPath $src) -and -not (Test-Path -LiteralPath $dst)) {
            Log ('Naujas vietinis elementas nukopijuotas: ' + $n)
            Copy-Item -LiteralPath $src -Destination $dst -Recurse
        }
    }

    if (-not (Test-Path -LiteralPath $ExePath)) { throw 'Po kopijavimo nerastas naujas exe' }
    if (-not (Test-Path -LiteralPath $InternalDir)) { throw 'Po kopijavimo nerastas _internal' }

    $result.ok = $true
    $result.message = 'Atnaujinta sėkmingai'
    Log 'Atnaujinimas sėkmingas'

    try { Remove-Safe $ExeOld; Remove-Safe $InternalOld } catch { Log ('Nepavyko išvalyti atsarginės kopijos: ' + $_.Exception.Message) }
}
catch {
    $err = $_.Exception.Message
    Log ('KLAIDA: ' + $err)
    $result.message = $err
    if ($backupDone) {
        Log 'Atstatoma sena versija...'
        try {
            if (Test-Path -LiteralPath $ExeOld) {
                Remove-Safe $ExePath
                Rename-Item -LiteralPath $ExeOld -NewName $ExeName
            }
            Remove-Safe $InternalDir
            if ($hadInternal -and (Test-Path -LiteralPath $InternalOld)) {
                Rename-Item -LiteralPath $InternalOld -NewName '_internal'
            }
            Log 'Sena versija atstatyta'
        } catch {
            Log ('ATSTATYMO KLAIDA: ' + $_.Exception.Message)
            $result.message = $err + ' (atstatant: ' + $_.Exception.Message + ')'
        }
    }
}
finally {
    try {
        ($result | ConvertTo-Json) | Set-Content -LiteralPath $ResultPath -Encoding UTF8
    } catch { Log ('Nepavyko įrašyti rezultato: ' + $_.Exception.Message) }

    try { Remove-Item -LiteralPath $WorkDir -Recurse -Force -ErrorAction Stop } catch { Log ('Nepavyko išvalyti laikino aplanko: ' + $_.Exception.Message) }

    # VISADA paleidžiame programą
    $launch = $ExePath
    if (-not (Test-Path -LiteralPath $launch) -and (Test-Path -LiteralPath $ExeOld)) {
        try { Rename-Item -LiteralPath $ExeOld -NewName $ExeName; Log 'Grąžintas senas exe paleidimui' } catch {}
    }
    try {
        Start-Process -FilePath $launch -WorkingDirectory $AppDir
        Log 'Programa paleista'
    } catch {
        Log ('NEPAVYKO PALEISTI PROGRAMOS: ' + $_.Exception.Message)
    }
}
"""


def build_update_script(app_dir: str, new_dir: str, work_dir: str, pid: int,
                        new_version: str, old_version: str,
                        log_path: str, result_path: str,
                        exe_name: str = EXE_NAME,
                        protected_files: Optional[List[str]] = None,
                        protected_dirs: Optional[List[str]] = None) -> str:
    repl = {
        "__APP_DIR__": ps_quote(app_dir),
        "__EXE_NAME__": ps_quote(exe_name),
        "__NEW_DIR__": ps_quote(new_dir),
        "__WORK_DIR__": ps_quote(work_dir),
        "__PID__": str(int(pid)),
        "__NEW_VERSION__": ps_quote(new_version),
        "__OLD_VERSION__": ps_quote(old_version),
        "__LOG_PATH__": ps_quote(log_path),
        "__RESULT_PATH__": ps_quote(result_path),
        "__PROTECTED_FILES__": _ps_array(PROTECTED_FILES if protected_files is None else protected_files),
        "__PROTECTED_DIRS__": _ps_array(PROTECTED_DIRS if protected_dirs is None else protected_dirs),
    }
    text = PS_TEMPLATE
    for k, v in repl.items():
        text = text.replace(k, v)
    return text


def write_update_script(path: str, text: str) -> None:
    # utf-8-sig (su BOM), kad Windows PowerShell 5.1 teisingai skaitytų lietuviškas raides
    with open(path, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(text)


def launch_update_script(script_path: str) -> None:
    ps = "powershell.exe"
    if os.name == "nt":
        cand = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                            "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        if os.path.exists(cand):
            ps = cand
    flags = 0
    if os.name == "nt":
        flags = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(
            [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", script_path],
            creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=True, cwd=get_temp_dir()
        )
    except Exception as e:
        raise UpdateError(f"Nepavyko paleisti atnaujinimo skripto: {e}")


def read_update_result(path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Perskaito ir ištrina paskutinio atnaujinimo rezultatą (jei yra)."""
    path = path or get_result_path()
    if not os.path.exists(path):
        return None
    data: Optional[Dict[str, Any]] = None
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception as e:
        data = {"ok": False, "message": f"Nepavyko perskaityti atnaujinimo rezultato: {e}", "log": get_log_path()}
    try:
        os.remove(path)
    except Exception:
        pass
    return data


def get_current_exe() -> Optional[str]:
    if getattr(sys, "frozen", False):
        return os.path.abspath(sys.executable)
    return None


# =========================================================================
# Gijos
# =========================================================================
class CheckUpdateWorker(QThread):
    update_available = Signal(dict)
    no_update = Signal(str)
    check_error = Signal(str)

    def __init__(self, repo: str = DEFAULT_GITHUB_REPO, current_version: str = APP_VERSION):
        super().__init__()
        self.repo = (repo or "").strip() or DEFAULT_GITHUB_REPO
        self.current_version = current_version

    def run(self):
        try:
            data = fetch_latest_release(self.repo)
            info = build_update_info(data)
            if is_newer_version(info["version"], self.current_version):
                self.update_available.emit(info)
            else:
                self.no_update.emit(self.current_version)
        except UpdateError as e:
            self.check_error.emit(str(e))
        except Exception as e:
            self.check_error.emit(f"Nepavyko patikrinti atnaujinimų: {e}")


class PrepareUpdateWorker(QThread):
    """Atsisiunčia, patikrina ir išarchyvuoja atnaujinimą. Programa tuo metu veikia toliau."""
    progress = Signal(object, object, str)   # baitai, viso, greitis
    stage = Signal(str)
    finished_ok = Signal(str)                # staging programos aplankas
    failed = Signal(str)

    def __init__(self, update_info: Dict[str, Any]):
        super().__init__()
        self.info = update_info
        self.cancel_event = threading.Event()
        self._last_t = 0.0
        self._last_b = 0

    def cancel(self):
        self.cancel_event.set()

    def _on_progress(self, done: int, total: int):
        now = time.time()
        if now - self._last_t < 0.25 and done != total:
            return
        dt = now - self._last_t if self._last_t else 0
        speed = (done - self._last_b) / dt if dt > 0 else 0
        speed_str = f"{speed / 1048576:.1f} MB/s" if speed >= 1048576 else f"{speed / 1024:.0f} KB/s"
        self._last_t, self._last_b = now, done
        self.progress.emit(done, total, speed_str)

    def run(self):
        work = get_work_dir()
        try:
            shutil.rmtree(work, ignore_errors=True)
            os.makedirs(work, exist_ok=True)
            zip_path = os.path.join(work, self.info["asset_name"])

            self.stage.emit("Atsisiunčiama...")
            download_file(self.info["url"], zip_path, int(self.info.get("asset_size") or 0),
                          self._on_progress, self.cancel_event)
            if self.cancel_event.is_set():
                raise UpdateError("Atsisiuntimas atšauktas.")

            self.stage.emit("Tikrinamas atsisiųstas failas...")
            verify_zip(zip_path, int(self.info.get("asset_size") or 0), self.info.get("sha256"))

            self.stage.emit("Išarchyvuojama...")
            app_dir = extract_to_staging(zip_path, os.path.join(work, "staging"))
            try:
                os.remove(zip_path)
            except Exception:
                pass
            if self.cancel_event.is_set():
                raise UpdateError("Atnaujinimas atšauktas.")
            self.finished_ok.emit(app_dir)
        except UpdateError as e:
            shutil.rmtree(work, ignore_errors=True)
            self.failed.emit(str(e))
        except Exception as e:
            shutil.rmtree(work, ignore_errors=True)
            self.failed.emit(f"Netikėta klaida ruošiant atnaujinimą: {e}")


# =========================================================================
# Dialogai
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
            f"Dabartinė versija: v{self.current_version}  ➔  Nauja versija: v{self.update_info.get('version', '')}"
        )
        ver_lbl.setStyleSheet("color: #38BDF8; font-size: 13px;")
        t_layout.addWidget(ver_lbl)
        h_box.addLayout(t_layout)
        h_box.addStretch(1)
        main_layout.addLayout(h_box)

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

        lbl_ch = StrongBodyLabel("Kas naujo:")
        lbl_ch.setStyleSheet("color: #F8FAFC; font-weight: bold;")
        c_layout.addWidget(lbl_ch)

        self.txt_changelog = TextEdit(card)
        self.txt_changelog.setReadOnly(True)
        self.txt_changelog.setMarkdown(self.update_info.get("changelog", ""))
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

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)
        sz_bytes = self.update_info.get("asset_size", 0)
        sz_text = f" ({sz_bytes / (1024 * 1024):.1f} MB)" if sz_bytes > 0 else ""
        hint_lbl = CaptionLabel(f"Failas: {self.update_info.get('asset_name') or '-'}{sz_text}")
        hint_lbl.setStyleSheet("color: #94A3B8;")
        btn_layout.addWidget(hint_lbl)
        btn_layout.addStretch(1)

        self.later_btn = PushButton(FIF.HISTORY, "Priminti vėliau", self)
        self.later_btn.setFixedHeight(36)
        self.later_btn.clicked.connect(self._on_later)
        btn_layout.addWidget(self.later_btn)

        self.update_btn = PrimaryPushButton(FIF.DOWNLOAD, "Atnaujinti", self)
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


class DownloadProgressDialog(QDialog):
    """Atsisiunčia ir paruošia atnaujinimą, palaukia, kol baigsis gamyba, ir perkrauna programą."""

    def __init__(self, update_info: Dict[str, Any], manager: "AutoUpdaterManager", parent=None):
        super().__init__(parent=parent)
        self.update_info = update_info
        self.manager = manager
        self.staging_app_dir: Optional[str] = None
        self.worker: Optional[PrepareUpdateWorker] = None
        self._busy_timer: Optional[QTimer] = None
        self._busy_started = 0.0
        self._paused_work = False
        self._done = False

        self.setWindowTitle("Programos atnaujinimas")
        self.setFixedSize(500, 240)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setStyleSheet("""
            QDialog {
                background-color: #0F172A;
                color: #F8FAFC;
            }
        """)
        self._init_ui()
        QTimer.singleShot(0, self._start)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(14)

        self.title_lbl = TitleLabel(f"Atnaujinama į v{self.update_info.get('version', '')}")
        self.title_lbl.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        self.title_lbl.setStyleSheet("color: #F8FAFC;")
        layout.addWidget(self.title_lbl)

        self.status_lbl = BodyLabel("Jungiamasi prie GitHub...")
        self.status_lbl.setStyleSheet("color: #94A3B8;")
        self.status_lbl.setWordWrap(True)
        layout.addWidget(self.status_lbl)

        self.prog_bar = ProgressBar(self)
        self.prog_bar.setValue(0)
        self.prog_bar.setFixedHeight(10)
        layout.addWidget(self.prog_bar)

        self.detail_lbl = CaptionLabel("")
        self.detail_lbl.setStyleSheet("color: #38BDF8; font-weight: bold;")
        self.detail_lbl.setWordWrap(True)
        layout.addWidget(self.detail_lbl)
        layout.addStretch(1)

        b_row = QHBoxLayout()
        b_row.addStretch(1)
        self.cancel_btn = PushButton(FIF.CLOSE, "Atšaukti", self)
        self.cancel_btn.clicked.connect(self._on_cancel)
        b_row.addWidget(self.cancel_btn)
        layout.addLayout(b_row)

    # --- 1. Atsisiuntimas, patikra, išarchyvavimas ---
    def _start(self):
        if not get_current_exe():
            self._fail("Atnaujinti galima tik sukompiliuotą programą (PrintReady.exe). "
                       "Dabar programa paleista iš kodo.")
            return
        self.worker = PrepareUpdateWorker(self.update_info)
        self.worker.progress.connect(self._on_progress)
        self.worker.stage.connect(self.status_lbl.setText)
        self.worker.finished_ok.connect(self._on_prepared)
        self.worker.failed.connect(self._fail)
        self.worker.start()

    def _on_progress(self, cur, total, speed_str: str):
        cur_mb = cur / 1048576
        total_mb = total / 1048576 if total else 0
        pct = (cur / total * 100.0) if total else 0.0
        self.prog_bar.setValue(int(min(pct, 100)))
        self.detail_lbl.setText(f"{cur_mb:.1f} MB / {total_mb:.1f} MB ({pct:.0f}%) • {speed_str}")

    def _on_prepared(self, staging_app_dir: str):
        self.staging_app_dir = staging_app_dir
        self.prog_bar.setValue(100)
        self.detail_lbl.setText("")
        self.status_lbl.setText("Atnaujinimas paruoštas.")
        self._paused_work = True
        self.manager.pause_work()
        self._busy_started = time.time()
        self._busy_timer = QTimer(self)
        self._busy_timer.timeout.connect(self._check_busy)
        self._busy_timer.start(1000)
        self._check_busy()

    # --- 2. Laukiame, kol baigsis vykdoma gamyba ---
    def _check_busy(self):
        reason = self.manager.busy_reason()
        if reason:
            waited = int(time.time() - self._busy_started)
            self.status_lbl.setText(f"Laukiama, kol baigsis: {reason}")
            self.detail_lbl.setText(f"Programa persikraus automatiškai ({waited} s)")
            return
        if self._busy_timer:
            self._busy_timer.stop()
        self._apply()

    # --- 3. Paleidžiame skriptą ir uždarome programą ---
    def _apply(self):
        exe = get_current_exe()
        try:
            if not exe:
                raise UpdateError("Nerastas programos exe kelias.")
            script = os.path.join(get_work_dir(), "printready_update.ps1")
            text = build_update_script(
                app_dir=os.path.dirname(exe),
                new_dir=self.staging_app_dir,
                work_dir=get_work_dir(),
                pid=os.getpid(),
                new_version=self.update_info.get("version", ""),
                old_version=self.manager.current_version,
                log_path=get_log_path(),
                result_path=get_result_path(),
                exe_name=os.path.basename(exe),
            )
            write_update_script(script, text)
            launch_update_script(script)
        except UpdateError as e:
            shutil.rmtree(get_work_dir(), ignore_errors=True)
            self._fail(str(e))
            return
        except Exception as e:
            shutil.rmtree(get_work_dir(), ignore_errors=True)
            self._fail(f"Nepavyko paleisti atnaujinimo: {e}")
            return

        self._done = True
        self.status_lbl.setText("Programa uždaroma ir bus paleista iš naujo...")
        self.cancel_btn.setEnabled(False)
        QTimer.singleShot(500, self._quit_app)

    def _quit_app(self):
        # Atsarginis saugiklis: jei Python neišsijungia per 8 s – uždarome priverstinai
        t = threading.Timer(8.0, lambda: os._exit(0))
        t.daemon = True
        t.start()
        app = QApplication.instance()
        if app:
            app.closeAllWindows()
            app.quit()
        else:
            os._exit(0)

    def _fail(self, msg: str):
        if self._busy_timer:
            self._busy_timer.stop()
        if self._paused_work:
            self._paused_work = False
            self.manager.resume_work()
        self.title_lbl.setText("❌ Atnaujinti nepavyko")
        self.status_lbl.setText(msg)
        self.detail_lbl.setText("Programa veikia toliau su dabartine versija.")
        self.prog_bar.setValue(0)
        self.cancel_btn.setText("Uždaryti")
        self.manager.log(f"❌ Atnaujinimas nepavyko: {msg}")

    def _on_cancel(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(5000)
        if self._busy_timer:
            self._busy_timer.stop()
        if self._paused_work:
            self._paused_work = False
            self.manager.resume_work()
            shutil.rmtree(get_work_dir(), ignore_errors=True)
        self.reject()

    def closeEvent(self, event):
        if self._done:
            event.ignore()
            return
        self._on_cancel()
        super().closeEvent(event)


# =========================================================================
# Pagrindinis atnaujinimų valdiklis
# =========================================================================
class AutoUpdaterManager:
    """Atnaujinimų valdiklis, integruojamas į MainWindow."""

    def __init__(self, parent_window, repo: str = DEFAULT_GITHUB_REPO, current_version: str = APP_VERSION,
                 log: Optional[Callable[[str], None]] = None,
                 auto_enabled: Optional[Callable[[], bool]] = None,
                 repo_getter: Optional[Callable[[], str]] = None,
                 busy_reason: Optional[Callable[[], Optional[str]]] = None,
                 pause_work: Optional[Callable[[], None]] = None,
                 resume_work: Optional[Callable[[], None]] = None):
        self.parent = parent_window
        self.repo = repo
        self.current_version = current_version
        self._log = log
        self._auto_enabled = auto_enabled or (lambda: True)
        self._repo_getter = repo_getter
        self._busy_reason = busy_reason or (lambda: None)
        self._pause_work = pause_work or (lambda: None)
        self._resume_work = resume_work or (lambda: None)
        self.is_checking = False
        self.is_manual = False
        self.dialog_open = False
        self.snoozed: Dict[str, float] = {}
        self.worker: Optional[CheckUpdateWorker] = None
        self.timer: Optional[QTimer] = None

    # --- pagalbinės ---
    def log(self, msg: str):
        if self._log:
            try:
                self._log(msg)
            except Exception:
                pass

    def busy_reason(self) -> Optional[str]:
        try:
            return self._busy_reason()
        except Exception:
            return None

    def pause_work(self):
        try:
            self._pause_work()
        except Exception as e:
            self.log(f"Perspėjimas stabdant darbus atnaujinimui: {e}")

    def resume_work(self):
        try:
            self._resume_work()
        except Exception as e:
            self.log(f"Perspėjimas atnaujinant darbus: {e}")

    # --- paleidimas ---
    def start(self):
        """Pirmas tikrinimas po kelių sekundžių, vėliau – kas 30 min."""
        QTimer.singleShot(STARTUP_CHECK_DELAY_MS, self._auto_check)
        self.timer = QTimer(self.parent)
        self.timer.timeout.connect(self._auto_check)
        self.timer.start(CHECK_INTERVAL_MS)

    def _auto_check(self):
        try:
            if not self._auto_enabled():
                return
        except Exception:
            pass
        self.check_updates_async(is_manual=False)

    def check_updates_async(self, is_manual: bool = False):
        if self.is_checking or self.dialog_open:
            return
        if self._repo_getter and not is_manual:
            try:
                self.repo = (self._repo_getter() or "").strip() or DEFAULT_GITHUB_REPO
            except Exception:
                pass
        self.is_checking = True
        self.is_manual = is_manual
        self.worker = CheckUpdateWorker(self.repo, self.current_version)
        self.worker.update_available.connect(self._on_update_available)
        self.worker.no_update.connect(self._on_no_update)
        self.worker.check_error.connect(self._on_check_error)
        self.worker.start()

    def _on_update_available(self, info: Dict[str, Any]):
        self.is_checking = False
        version = info.get("version", "")
        manual = self.is_manual

        if not info.get("asset_found"):
            msg = (f"Versija v{version} išleista, bet joje nėra failo PrintReady_v{version}.zip – "
                   f"atnaujinti negalima.")
            self.log(f"⚠️ {msg}")
            if manual:
                InfoBar.warning(title="Atnaujinimas negalimas", content=msg,
                                position=InfoBarPosition.TOP_RIGHT, duration=8000, parent=self.parent)
            return

        if not manual and self.snoozed.get(version, 0) > time.time():
            return

        self.log(f"🚀 Rasta nauja versija v{version}")
        self.dialog_open = True
        try:
            dlg = UpdateAvailableDialog(info, self.current_version, self.parent)
            if dlg.exec() and dlg.should_update:
                self.snoozed.pop(version, None)
                prog = DownloadProgressDialog(info, self, self.parent)
                prog.exec()
            else:
                self.snoozed[version] = time.time() + SNOOZE_SECONDS
                self.log(f"⏰ Atnaujinimas į v{version} atidėtas 4 val.")
        finally:
            self.dialog_open = False

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
            self.log(f"⚠️ Atnaujinimų patikra: {error_msg}")
            InfoBar.warning(
                title="Atnaujinimų patikra",
                content=error_msg,
                position=InfoBarPosition.TOP_RIGHT,
                duration=6000,
                parent=self.parent
            )

    # --- paskutinio atnaujinimo rezultatas ---
    def show_last_update_result(self):
        res = read_update_result()
        if not res:
            return
        if res.get("ok"):
            msg = f"Programa atnaujinta į v{res.get('version') or self.current_version}."
            self.log(f"✅ {msg}")
            InfoBar.success(title="Programa atnaujinta", content=msg,
                            position=InfoBarPosition.TOP_RIGHT, duration=8000, parent=self.parent)
        else:
            log_p = res.get("log") or get_log_path()
            msg = (f"Nepavyko atnaujinti į v{res.get('version', '?')}. Programa veikia su sena versija.\n\n"
                   f"Priežastis: {res.get('message') or 'nežinoma'}\n\nŽurnalas: {log_p}")
            self.log(f"❌ {msg}")
            box = MessageBox("Atnaujinti nepavyko", msg, self.parent)
            box.cancelButton.hide()
            box.exec()

