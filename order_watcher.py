import os
import re
import sys
import time
import datetime
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional, Set, List, Dict, Any, Tuple

from template_manager import TemplateManager
from crop_engine import process_and_crop

SUPPORTED_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp', '.tif', '.tiff')
IGNORE_KEYWORDS = ('batch sheet', 'bach sheet', 'batchsheet', 'bachsheet', 'batch_sheet', 'bach_sheet')

DEFAULT_STD_INPUT = r"\\192.168.1.143\podbase-hotfolder\BENDRAS_PODBASE_HOTFOLDER"
DEFAULT_REJECTS_INPUT = r"\\192.168.1.143\podbase-rejects\BENDRAS_PODBASE_HOTFOLDER"
DEFAULT_OUTPUT = os.path.join(os.path.expanduser("~"), "Desktop", "Macbook print files", "READY")

DATE_REGEX = re.compile(r'(?<!\d)(20\d{2})[-_.](0[1-9]|1[0-2])[-_.](0[1-9]|[12]\d|3[01])(?!\d)')

WATCH_INTERVAL_SECONDS = 3.0
TEMPLATE_RELOAD_SECONDS = 45.0
# Failas laikomas įkeltu, kai jo dydis ir laikas nesikeičia bent tiek sekundžių
FILE_STABLE_SECONDS = 2.0
# Nepavykusio failo pakartotiniai bandymai fone (sek.). Išnaudojus – laukiama, kol failas pasikeis.
RETRY_DELAYS_SECONDS = (30, 120, 600)

# Išvesties failai, kurie šiuo metu gaminami (bendra visoms OrderWatcher kopijoms ir gijoms),
# kad tas pats .tif niekada nebūtų rašomas dviem gijomis vienu metu
_INFLIGHT: Set[str] = set()
_INFLIGHT_LOCK = threading.Lock()


def _inflight_key(out_path: str) -> str:
    return os.path.normcase(os.path.abspath(out_path))


def _claim_output(out_path: str) -> bool:
    key = _inflight_key(out_path)
    with _INFLIGHT_LOCK:
        if key in _INFLIGHT:
            return False
        _INFLIGHT.add(key)
        return True


def _release_output(out_path: str):
    with _INFLIGHT_LOCK:
        _INFLIGHT.discard(_inflight_key(out_path))


def parse_date_from_string(text: str) -> Optional[datetime.date]:
    """Ištraukia datą (YYYY-MM-DD, YYYY_MM_DD, YYYY.MM.DD) iš teksto ar kelio."""
    m = DATE_REGEX.search(text)
    if m:
        try:
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return datetime.date(year, month, day)
        except ValueError:
            pass
    return None


def clean_path(p: Optional[str]) -> str:
    if not p:
        return ""
    return os.path.normpath(p.strip().strip('"').strip("'"))


def _file_signature(path: str) -> Optional[Tuple[int, float]]:
    try:
        st = os.stat(path)
        return st.st_size, st.st_mtime
    except OSError:
        return None


# Gamybos rezultatai
DONE, SKIPPED, BUSY, FAILED = "done", "skipped", "busy", "failed"


class OrderWatcher:
    def __init__(
        self,
        input_folder: str = DEFAULT_STD_INPUT,
        output_folder: str = DEFAULT_OUTPUT,
        templates_folder: str = "Sablonai",
        rejects_input_folder: Optional[str] = DEFAULT_REJECTS_INPUT,
        choke_pixels: int = 1,
        spot_channel_name: str = "W",
        solidity: int = 5,
        target_dpi: int = 300,
        delete_original: bool = False,
        skip_existing: bool = True,
        max_workers: int = 4,
        days_back_limit: int = 3,
        auto_today_only: bool = True,
        log_callback: Optional[Callable[[str], None]] = None,
        on_file_processed_callback: Optional[Callable[[str, str, bool], None]] = None
    ):
        self.input_folder = clean_path(input_folder)
        self.rejects_input_folder = clean_path(rejects_input_folder)
        self.output_folder = clean_path(output_folder)
        self.templates_folder = clean_path(templates_folder)
        self.choke_pixels = choke_pixels
        self.spot_channel_name = spot_channel_name
        self.solidity = solidity
        self.target_dpi = target_dpi
        self.delete_original = delete_original
        self.skip_existing = skip_existing
        self.max_workers = max(1, min(16, int(max_workers)))
        self.days_back_limit = max(0, int(days_back_limit))
        self.auto_today_only = bool(auto_today_only)
        self.log_callback = log_callback
        self.on_file_processed_callback = on_file_processed_callback

        self.template_manager = TemplateManager(self.templates_folder)
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self._threads: List[threading.Thread] = []
        self._run_token: Optional[object] = None

        # Fono stebėjimo būsena
        self.processed_files: Set[str] = set()          # jau pagaminti / per seni / jau paruošti
        self._no_template: Set[str] = set()             # neturi šablono (tikrinama vėl pasikeitus šablonams)
        self._failures: Dict[str, Tuple[int, float, Optional[Tuple[int, float]]]] = {}
        self._seen: Dict[str, Tuple[Optional[Tuple[int, float]], float]] = {}
        self._state_lock = threading.Lock()
        self._log_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Pagalbinės
    # ------------------------------------------------------------------
    def log(self, message: str):
        with self._log_lock:
            if self.log_callback:
                # Žurnalą rodo (ir į konsolę išveda) programos langas
                try:
                    self.log_callback(message)
                except Exception:
                    pass
                return
            try:
                enc = sys.stdout.encoding or 'utf-8'
                safe_msg = message.encode(enc, errors='replace').decode(enc)
                print(safe_msg)
            except Exception:
                try:
                    print(message.encode('ascii', errors='ignore').decode('ascii'))
                except Exception:
                    pass

    def apply_settings(self, **settings):
        """
        Pritaiko naujus nustatymus veikiančiam stebėjimui (nereikia jo perjungti).
        Priimami tie patys raktai kaip konstruktoriuje.
        """
        paths_changed = False
        for key in ("input_folder", "rejects_input_folder", "output_folder"):
            if key in settings:
                new = clean_path(settings[key])
                if new != getattr(self, key):
                    setattr(self, key, new)
                    paths_changed = True

        if "templates_folder" in settings:
            new_tmpl = clean_path(settings["templates_folder"])
            if new_tmpl != self.templates_folder:
                self.templates_folder = new_tmpl
                self.template_manager.set_templates_dir(new_tmpl)
                paths_changed = True

        print_changed = False
        for key in ("choke_pixels", "spot_channel_name", "solidity", "target_dpi"):
            if key in settings and settings[key] != getattr(self, key):
                setattr(self, key, settings[key])
                print_changed = True

        if "skip_existing" in settings:
            self.skip_existing = bool(settings["skip_existing"])
        if "max_workers" in settings:
            self.max_workers = max(1, min(16, int(settings["max_workers"])))
        if "days_back_limit" in settings:
            self.days_back_limit = max(0, int(settings["days_back_limit"]))
        if "auto_today_only" in settings:
            new_today = bool(settings["auto_today_only"])
            if new_today != self.auto_today_only:
                self.auto_today_only = new_today
                paths_changed = True  # pasikeitė datos riba – failus reikia peržiūrėti iš naujo
        if "delete_original" in settings:
            self.delete_original = bool(settings["delete_original"])

        if paths_changed or print_changed:
            with self._state_lock:
                self.processed_files.clear()
                self._no_template.clear()
                self._failures.clear()
                self._seen.clear()

    def _get_sources(self) -> List[Tuple[str, bool]]:
        sources = []
        if self.input_folder:
            sources.append((self.input_folder, False))
        if self.rejects_input_folder and os.path.normcase(self.rejects_input_folder) != os.path.normcase(self.input_folder):
            sources.append((self.rejects_input_folder, True))
        return sources

    def auto_cutoff_date(self, today: Optional[datetime.date] = None) -> datetime.date:
        """Seniausia data, kurią fono auto-gamyba dar gamina."""
        today = today or datetime.date.today()
        if self.auto_today_only:
            return today
        return today - datetime.timedelta(days=self.days_back_limit)

    def _is_dir_older_than_cutoff(self, dir_path_or_name: str, cutoff_date: datetime.date) -> bool:
        """
        Tikrina, ar aplankas atstovauja datą, senesnę už leistiną ribą (cutoff_date).
        Jei aplankas turi datą (pvz., '2026-09-12') ir ji senesnė už cutoff_date, grąžina True (praleisti / neiti gilyn).
        Jei aplanko pavadinime ar kelyje datos nėra, grąžina False (tikrinama giliau).
        """
        base = os.path.basename(dir_path_or_name)
        d = parse_date_from_string(base)
        if d:
            return d < cutoff_date
        d_full = parse_date_from_string(dir_path_or_name)
        if d_full:
            return d_full < cutoff_date
        return False

    def _is_file_older_than_cutoff(self, file_path: str, cutoff_date: datetime.date) -> bool:
        """
        Tikrina, ar failas yra senesnis už leistiną ribą.
        Pirmiausia bando ištraukti datą iš failo kelio/pavadinimo.
        Jei kelyje datos nėra (pvz. plokščias brokų aplankas), tikrina failo naujausią laiką (max(mtime, ctime)).
        """
        d = parse_date_from_string(file_path)
        if d:
            return d < cutoff_date
        try:
            mtime = os.path.getmtime(file_path)
            try:
                ctime = os.path.getctime(file_path)
                best_time = max(mtime, ctime)
            except Exception:
                best_time = mtime
            f_date = datetime.date.fromtimestamp(best_time)
            return f_date < cutoff_date
        except Exception:
            return False

    def _is_file_ready(self, file_path: str) -> bool:
        """
        Tikrina, ar failas baigtas kelti: jo dydis ir keitimo laikas turi nesikeisti
        bent FILE_STABLE_SECONDS (tarp dviejų stebėjimo ciklų), o failas turi atsidaryti skaitymui.
        """
        sig = _file_signature(file_path)
        now = time.time()
        if sig is None or sig[0] <= 0:
            with self._state_lock:
                self._seen.pop(file_path, None)
            return False
        with self._state_lock:
            prev = self._seen.get(file_path)
            if prev is None or prev[0] != sig:
                self._seen[file_path] = (sig, now)
                return False
            if now - prev[1] < FILE_STABLE_SECONDS:
                return False
        try:
            with open(file_path, 'rb') as f:
                f.read(1024)
        except OSError:
            return False
        with self._state_lock:
            self._seen.pop(file_path, None)
        return True

    def _should_ignore(self, path_or_name: str) -> bool:
        """Tikrina, ar failas ar aplankas turi būti ignoruojamas (pvz. batch sheet)."""
        lower = path_or_name.lower()
        for kw in IGNORE_KEYWORDS:
            if kw in lower:
                return True
        return False

    def _keep_dir(self, root: str, d: str, cutoff_date: datetime.date) -> bool:
        if self._should_ignore(d):
            return False
        full = os.path.join(root, d)
        # Jei READY aplankas yra įvesties aplanko viduje – į jį neiname (kitaip gamintume iš savo išvesties)
        if self.output_folder and os.path.normcase(os.path.normpath(full)) == os.path.normcase(self.output_folder):
            return False
        return not self._is_dir_older_than_cutoff(full, cutoff_date)

    def _iter_input_files(self, folder: str, cutoff_date: datetime.date, should_continue: Callable[[], bool] = lambda: True):
        """Grąžina palaikomų įvesties failų kelius aplanke, praleidžiant ignoruojamus ir per senus aplankus."""
        for root, dirs, files in os.walk(folder):
            if not should_continue():
                return
            dirs[:] = [d for d in dirs if self._keep_dir(root, d, cutoff_date)]
            for file_name in files:
                if file_name.startswith(('.', '~')) or self._should_ignore(file_name):
                    continue
                if file_name.lower().endswith(SUPPORTED_EXTENSIONS):
                    yield os.path.join(root, file_name)

    def get_output_path_for_file(self, file_path: str, is_reject: bool = False, base_input_dir: Optional[str] = None) -> Tuple[str, str]:
        """
        Apskaičiuoja tikslinį .tif išvesties kelią pagal failo vietą įvesties aplanke.
        Grąžina (out_path, rel_path).
        """
        if not base_input_dir:
            norm_fp = os.path.normcase(os.path.normpath(file_path))
            norm_rej = os.path.normcase(os.path.normpath(self.rejects_input_folder)) if self.rejects_input_folder else ""
            if norm_rej and norm_fp.startswith(norm_rej.rstrip(os.sep) + os.sep):
                base_input_dir = self.rejects_input_folder
                is_reject = True
            else:
                base_input_dir = self.input_folder

        try:
            rel_path = os.path.relpath(file_path, base_input_dir)
        except Exception:
            rel_path = os.path.basename(file_path)

        rel_dir = os.path.dirname(rel_path)
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        out_file_name = f"{base_name}.tif"

        if is_reject:
            out_dir = os.path.join(self.output_folder, "BROKAI", rel_dir)
        else:
            out_dir = os.path.join(self.output_folder, rel_dir)

        out_path = os.path.join(out_dir, out_file_name)
        return out_path, rel_path

    def is_file_already_converted(self, file_path: str, is_reject: bool = False, base_input_dir: Optional[str] = None) -> bool:
        """Tikrina, ar failas jau yra sėkmingai konvertuotas į .TIF išvesties aplanke."""
        try:
            out_path, _ = self.get_output_path_for_file(file_path, is_reject=is_reject, base_input_dir=base_input_dir)
            return os.path.exists(out_path) and os.path.getsize(out_path) > 0
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Nepavykusių failų pakartojimai
    # ------------------------------------------------------------------
    def _record_failure(self, file_path: str):
        sig = _file_signature(file_path)
        with self._state_lock:
            attempts = self._failures.get(file_path, (0, 0.0, None))[0] + 1
            if attempts <= len(RETRY_DELAYS_SECONDS):
                next_try = time.time() + RETRY_DELAYS_SECONDS[attempts - 1]
            else:
                next_try = float("inf")
            self._failures[file_path] = (attempts, next_try, sig)
        if next_try == float("inf"):
            self.log(f"⛔ {os.path.basename(file_path)}: nepavyko {attempts} kartus – fone nebebandoma, "
                     f"kol failas nepasikeis (galima pagaminti rankiniu būdu).")

    def _retry_allowed(self, file_path: str) -> bool:
        with self._state_lock:
            entry = self._failures.get(file_path)
            if entry is None:
                return True
            attempts, next_try, sig = entry
            if _file_signature(file_path) != sig:
                # Failas pakeistas (pvz. įkeltas iš naujo) – bandome iš karto
                self._failures.pop(file_path, None)
                return True
            return time.time() >= next_try

    # ------------------------------------------------------------------
    # Vieno failo gamyba (naudojama ir rankinėje, ir fono gamyboje)
    # ------------------------------------------------------------------
    def produce_file(self, file_path: str, tmpl_path: str, out_path: str, is_reject: bool,
                     skip_existing: Optional[bool] = None, label: str = "IŠSAUGOTA") -> Tuple[str, str]:
        """
        Pagamina vieną failą. Grąžina (rezultatas, pranešimas), kur rezultatas yra
        DONE, SKIPPED (jau buvo paruoštas), BUSY (tą patį failą jau gamina kita gija) arba FAILED.
        """
        if skip_existing is None:
            skip_existing = self.skip_existing
        f_name = os.path.basename(file_path)

        if not _claim_output(out_path):
            return BUSY, f"⏳ Jau gaminamas kitos užduoties: {f_name}"
        try:
            if skip_existing and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                self.processed_files.add(file_path)
                return SKIPPED, f"⏩ Jau paruoštas (Praleidžiama): {f_name} -> {os.path.basename(out_path)}"

            tag = "🔴 [BROKAS]" if is_reject else "🎨 [STANDARTINIS]"
            start_t = time.time()
            try:
                process_and_crop(
                    image_path=file_path,
                    template_path=tmpl_path,
                    output_path=out_path,
                    choke_pixels=self.choke_pixels,
                    spot_channel_name=self.spot_channel_name,
                    solidity=self.solidity,
                    target_dpi=self.target_dpi,
                    warn=self.log
                )
            except Exception as e:
                self._record_failure(file_path)
                return FAILED, f"❌ KLAIDA apdorojant {f_name}: {e}"

            elapsed = time.time() - start_t
            self.processed_files.add(file_path)
            with self._state_lock:
                self._failures.pop(file_path, None)
            if self.delete_original:
                try:
                    os.remove(file_path)
                except Exception:
                    pass
            if self.on_file_processed_callback:
                try:
                    self.on_file_processed_callback(file_path, out_path, is_reject)
                except Exception:
                    pass
            return DONE, f"{tag} ✅ {label} ({elapsed:.2f}s): {f_name} -> {out_path}"
        finally:
            _release_output(out_path)

    # ------------------------------------------------------------------
    # Fono stebėjimas
    # ------------------------------------------------------------------
    def start(self):
        try:
            os.makedirs(self.output_folder, exist_ok=True)
            os.makedirs(os.path.join(self.output_folder, "BROKAI"), exist_ok=True)
            os.makedirs(self.templates_folder, exist_ok=True)
        except Exception as e:
            self.log(f"Perspėjimas kuriant aplankus: {e}")

        if self.running and self.thread is not None and self.thread.is_alive():
            return

        self.running = True
        self.template_manager.reload_templates()
        tmpl_count = len(self.template_manager.get_template_names())

        # Kiekviena gija turi savo žymą: senas ciklas (jei dar baigia darbą) išeis pats,
        # o tie patys failai dvigubai negaminami (_claim_output)
        token = object()
        self._run_token = token
        self.thread = threading.Thread(target=self._watch_loop, args=(token,), daemon=True)
        self._threads = [t for t in self._threads if t.is_alive()] + [self.thread]
        self.thread.start()

        today = datetime.date.today()
        cutoff = self.auto_cutoff_date(today)
        self.log("🚀 Užsakymų ir Brokų fono stebėjimas PALEISTAS!")
        self.log(f"📁 Generacijų aplankas: {self.input_folder}")
        if self.rejects_input_folder:
            self.log(f"🔴 Rejected aplankas: {self.rejects_input_folder}")
        self.log(f"📁 Išvestis: {self.output_folder} (Brokai -> {os.path.join(self.output_folder, 'BROKAI')})")
        self.log(f"⚡ Lygiagrečių gijų skaičius: {self.max_workers} | Praleisti jau paruoštus: {'TAIP' if self.skip_existing else 'NE'}")
        if self.auto_today_only:
            self.log(f"📅 Auto-gamyba: TIK šiandienos ({today}) užsakymai")
        else:
            self.log(f"📅 Auto-gamyba: nuo {cutoff} iki šiandien ({today}) [Paskutinės {self.days_back_limit} d. + šiandien]")
        self.log(f"📐 Aktyvių šablonų skaičius: {tmpl_count} (Aplankas: '{self.templates_folder}')")

    def stop(self):
        self.running = False
        self._run_token = None
        self.log("⏹ Stebėjimas SUSTABDYTAS.")

    def is_active(self) -> bool:
        """Ar kuri nors stebėjimo gija dar dirba (pvz. baigia pradėtus failus po sustabdymo)."""
        self._threads = [t for t in self._threads if t.is_alive()]
        return bool(self._threads)

    def _watch_loop(self, token: object):
        def alive() -> bool:
            return self.running and self._run_token is token

        last_tmpl_reload = time.time()
        last_tmpl_names = tuple(self.template_manager.get_template_names())

        while alive():
            # Kas 45 sek. perskaitome šablonų aplanką (jei buvo įkeltas naujas šablonas)
            if time.time() - last_tmpl_reload > TEMPLATE_RELOAD_SECONDS:
                self.template_manager.reload_templates()
                last_tmpl_reload = time.time()
                names = tuple(self.template_manager.get_template_names())
                if names != last_tmpl_names:
                    last_tmpl_names = names
                    with self._state_lock:
                        self._no_template.clear()

            cutoff_date = self.auto_cutoff_date()
            batch_to_process = []

            for folder, is_reject in self._get_sources():
                if not alive():
                    break
                if not (folder and os.path.exists(folder)):
                    continue
                try:
                    for file_path in self._iter_input_files(folder, cutoff_date, alive):
                        if not alive():
                            break
                        if file_path in self.processed_files or file_path in self._no_template:
                            continue

                        if self._is_file_older_than_cutoff(file_path, cutoff_date):
                            continue

                        if self.skip_existing and self.is_file_already_converted(file_path, is_reject=is_reject, base_input_dir=folder):
                            self.processed_files.add(file_path)
                            continue

                        tmpl_name, tmpl_path = self.template_manager.find_template_for_path(file_path)
                        if not tmpl_path:
                            # Nesusijęs produktas – nebetikriname, kol nepasikeis šablonų sąrašas
                            self._no_template.add(file_path)
                            continue

                        if not self._retry_allowed(file_path):
                            continue

                        if self._is_file_ready(file_path):
                            out_p, _ = self.get_output_path_for_file(file_path, is_reject=is_reject, base_input_dir=folder)
                            batch_to_process.append((file_path, tmpl_path, out_p, is_reject))
                except Exception as e:
                    self.log(f"Stebėjimo pranešimas ({folder}): {e}")

            if batch_to_process and alive():
                def _auto_worker(task):
                    f_path, t_path, o_p, is_rej = task
                    status, msg = self.produce_file(f_path, t_path, o_p, is_rej, label="Auto-paruoštas")
                    if status in (DONE, FAILED):
                        self.log(msg)

                with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    list(executor.map(_auto_worker, batch_to_process))

            # Miegame mažais žingsniais, kad sustabdymas suveiktų greitai
            slept = 0.0
            while slept < WATCH_INTERVAL_SECONDS and alive():
                time.sleep(0.25)
                slept += 0.25

    # ------------------------------------------------------------------
    # Skenavimas ir rankinė gamyba
    # ------------------------------------------------------------------
    def _parse_hierarchy_parts(self, rel_path: str, is_reject: bool, tmpl_name: Optional[str]) -> Tuple[str, str, str]:
        """
        Ištraukia datą, generaciją ir modelį iš santykinio kelio.
        Pritaikyta tiek gilioms hierarchijoms (Data/Gen/Model/Bid/File),
        tiek plokštiems brokų aplankams (Model/File arba File).
        """
        parts = rel_path.replace('\\', '/').split('/')
        file_name = parts[-1] if parts else ""
        dir_parts = parts[:-1]

        # 1. Datos nustatymas (ieškome datos YYYY-MM-DD arba naudojame aplanko vardą)
        d = parse_date_from_string(rel_path)
        if d:
            date_val = d.strftime("%Y-%m-%d")
        elif dir_parts and len(dir_parts[0]) >= 6 and any(c.isdigit() for c in dir_parts[0]):
            date_val = dir_parts[0]
        else:
            date_val = "Brokai" if is_reject else "Užsakymai"

        # 2. Generacijos / partijos nustatymas (ieškome trumpo skaitinio aplanko pvz. /20/, /1/)
        gen_val = "-"
        for p in dir_parts:
            if p.isdigit() and len(p) <= 4:
                gen_val = p
                break
            if p.lower().startswith('bid-') or p.lower().startswith('batch-'):
                gen_val = p
                break

        # 3. Modelio pavadinimas
        if tmpl_name:
            model_val = tmpl_name
        else:
            model_val = "Nežinomas"
            for p in reversed(dir_parts):
                if any(kw in p.lower() for kw in ('macbook', 'case', 'apple', 'pro', 'air', 'neo', '1932', '2681')):
                    model_val = p
                    break
            if model_val == "Nežinomas" and file_name:
                model_val = os.path.splitext(file_name)[0]

        return date_val, gen_val, model_val

    def scan_available_orders(self) -> List[Dict[str, Any]]:
        """
        Nuskenuoja tiek generacijų, tiek rejected įvesties aplankus.
        Filtruoja tik šiandienos ir pastarųjų X dienų (numatyta: 3 d.) užsakymus.
        Grąžina rasto sąrašo grupes su šablonų informacija ir konvertavimo būsenomis.
        """
        today = datetime.date.today()
        cutoff_date = today - datetime.timedelta(days=self.days_back_limit)

        self.log("🔍 Skenuojami užsakymų aplankai...")
        self.log(f"📅 Užsakymų senumo filtras: rodomi tik nuo {cutoff_date} iki šiandien ({today}) [Šiandien + paskutinės {self.days_back_limit} d.]")
        self.template_manager.reload_templates()
        tmpl_count = len(self.template_manager.get_template_names())
        self.log(f"📐 Aktyvių šablonų skaičius: {tmpl_count} (Aplankas: '{self.templates_folder}')")
        if tmpl_count == 0:
            self.log(f"⚠️ DĖMESIO: Šablonų aplanke '{self.templates_folder}' nerasta jokių .PNG failų!")

        groups: Dict[str, Dict[str, Any]] = {}
        total_found_files = 0
        total_already_converted = 0

        for folder, is_reject in self._get_sources():
            src_label = "BROKAS" if is_reject else "STANDARTINIS"
            tag = "🔴 [REJECTED]" if is_reject else "📦 [GENERACIJOS]"
            self.log(f"\n{tag} Skenuojamas aplankas: {folder}")

            if not os.path.exists(folder):
                self.log(f"   ❌ KLAIDA: Kelias nepasiekiamas: '{folder}'")
                self.log(f"   💡 Patikrinkite tinklo ryšį arba pakoreguokite kelią '⚙️ Nustatymai' skiltyje.")
                continue

            folder_file_count = 0
            try:
                for file_path in self._iter_input_files(folder, cutoff_date):
                    if self._is_file_older_than_cutoff(file_path, cutoff_date):
                        continue

                    # Skenuojame TIK tuos produktus, kurie vadinasi taip kaip šablonai
                    tmpl_name, tmpl_path = self.template_manager.find_template_for_path(file_path)
                    if not tmpl_path:
                        continue

                    folder_file_count += 1
                    total_found_files += 1

                    rel_path = os.path.relpath(file_path, folder)
                    date_val, gen_val, model_val = self._parse_hierarchy_parts(rel_path, is_reject, tmpl_name)

                    prefix = "[BROKAS] " if is_reject else ""
                    group_key = f"{prefix}{date_val} / {gen_val} / {model_val}"

                    is_converted = self.is_file_already_converted(file_path, is_reject=is_reject, base_input_dir=folder)
                    if is_converted:
                        total_already_converted += 1

                    if group_key not in groups:
                        groups[group_key] = {
                            "key": group_key,
                            "date": date_val,
                            "generation": gen_val,
                            "model": model_val,
                            "template_name": tmpl_name,
                            "template_path": tmpl_path,
                            "has_template": True,
                            "is_reject": is_reject,
                            "source_label": src_label,
                            "base_folder": folder,
                            "files": [],
                            "converted_files": [],
                            "new_files": []
                        }

                    groups[group_key]["files"].append(file_path)
                    if is_converted:
                        groups[group_key]["converted_files"].append(file_path)
                    else:
                        groups[group_key]["new_files"].append(file_path)

                self.log(f"   ✅ Rasta palaikomų failų: {folder_file_count}")
            except Exception as e:
                self.log(f"   ❌ Klaida skenuojant {folder}: {e}")

        # Nustatome kiekvienos grupės būseną
        for g in groups.values():
            total_g = len(g["files"])
            conv_g = len(g["converted_files"])
            if conv_g == total_g and total_g > 0:
                g["status"] = "ALL_READY"
            elif conv_g > 0:
                g["status"] = "PARTIAL"
            else:
                g["status"] = "NEW"

        result = list(groups.values())
        reject_count = sum(1 for g in result if g.get("is_reject"))
        std_count = len(result) - reject_count
        new_files_total = total_found_files - total_already_converted

        self.log(f"\n📊 Nuskenavimo suvestinė:")
        self.log(f"   - Iš viso rasta failų: {total_found_files}")
        self.log(f"   - ✅ Jau konvertuotų (READY): {total_already_converted}")
        self.log(f"   - 🆕 Naujų gamybai: {new_files_total}")
        self.log(f"   - Generacijų modelių grupių: {std_count}")
        self.log(f"   - Rejected grupių: {reject_count}")

        return result

    def process_selected_groups(
        self,
        selected_groups: List[Dict[str, Any]],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        skip_existing: Optional[bool] = None,
        max_workers: Optional[int] = None
    ) -> Dict[str, int]:
        """
        Lygiagrečiai apdoroja vartotojo pažymėtas grupes.
        Grąžina suvestinę: {"produced", "skipped", "failed", "total"}.
        """
        if skip_existing is None:
            skip_existing = self.skip_existing
        if max_workers is None:
            max_workers = self.max_workers

        valid_groups = [g for g in selected_groups if g.get("has_template") and g.get("template_path")]
        for sg in selected_groups:
            if sg not in valid_groups:
                self.log(f"⚠️ Praleidžiama grupė '{sg['key']}', nes trūksta šablono ({sg.get('template_name')})!")

        tasks = []
        for g in valid_groups:
            for file_path in g["files"]:
                tasks.append((file_path, g["template_path"], g.get("is_reject", False),
                              g.get("base_folder", self.input_folder)))

        total_files = len(tasks)
        stats = {"produced": 0, "skipped": 0, "failed": 0, "total": total_files}
        self.log(f"\n🚀 Pradedama gamyba! Pasirinkta grupių: {len(valid_groups)} (Iš viso failų: {total_files})")
        self.log(f"⚡ Lygiagrečių darbuotojų (Threads): {max_workers} | Praleisti jau paruoštus: {'TAIP' if skip_existing else 'NE'}")

        if total_files == 0:
            if progress_callback:
                progress_callback(0, 0, "Nėra failų gamybai.")
            return stats

        def _worker_task(item) -> Tuple[str, str, str]:
            file_path, tmpl_path, is_reject, base_folder = item
            out_path, _ = self.get_output_path_for_file(file_path, is_reject=is_reject, base_input_dir=base_folder)
            status, msg = self.produce_file(file_path, tmpl_path, out_path, is_reject, skip_existing=skip_existing)
            return status, os.path.basename(file_path), msg

        current_idx = 0
        with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as executor:
            futures = [executor.submit(_worker_task, item) for item in tasks]
            for future in as_completed(futures):
                try:
                    status, f_name, msg = future.result()
                except Exception as e:
                    status, f_name, msg = FAILED, "?", f"❌ Netikėta klaida: {e}"
                self.log(msg)
                current_idx += 1
                if status == DONE:
                    stats["produced"] += 1
                elif status in (SKIPPED, BUSY):
                    # BUSY – tą patį failą tuo metu gamina fono stebėjimas
                    stats["skipped"] += 1
                else:
                    stats["failed"] += 1
                if progress_callback:
                    progress_callback(current_idx, total_files, f_name)

        self.log(f"\n🏁 GAMYBA BAIGTA!")
        self.log(f"   - ✅ Naujai sugeneruota: {stats['produced']}")
        self.log(f"   - ⏩ Praleista (jau buvo paruošti / gaminami fone): {stats['skipped']}")
        if stats["failed"] > 0:
            self.log(f"   - ❌ Nesėkmingi: {stats['failed']}")

        return stats

    def process_all_now(self) -> Dict[str, int]:
        """Vienu paspaudimu nuskenuoja visus įvesties aplankus ir apdoroja visus rastus failus."""
        groups = self.scan_available_orders()
        return self.process_selected_groups(groups)
