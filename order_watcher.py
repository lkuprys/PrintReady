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
DEFAULT_OUTPUT = r"C:\Users\kingt\Desktop\Macbook print files\READY"

DATE_REGEX = re.compile(r'(?<!\d)(20\d{2})[-_.](0[1-9]|1[0-2])[-_.](0[1-9]|[12]\d|3[01])(?!\d)')

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
        self.max_workers = max(1, min(16, max_workers))
        self.days_back_limit = max(0, int(days_back_limit))
        self.log_callback = log_callback
        self.on_file_processed_callback = on_file_processed_callback

        self.template_manager = TemplateManager(self.templates_folder)
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.processed_files: Set[str] = set()
        self._log_lock = threading.Lock()

    def log(self, message: str):
        with self._log_lock:
            try:
                enc = sys.stdout.encoding or 'utf-8'
                safe_msg = message.encode(enc, errors='replace').decode(enc)
                print(safe_msg)
            except Exception:
                try:
                    print(message.encode('ascii', errors='ignore').decode('ascii'))
                except Exception:
                    pass
            if self.log_callback:
                try:
                    self.log_callback(message)
                except Exception:
                    pass

    def start(self):
        if self.running:
            return
        
        try:
            os.makedirs(self.output_folder, exist_ok=True)
            os.makedirs(os.path.join(self.output_folder, "BROKAI"), exist_ok=True)
            os.makedirs(self.templates_folder, exist_ok=True)
        except Exception as e:
            self.log(f"Perspėjimas kuriant aplankus: {e}")

        self.running = True
        self.template_manager.reload_templates()
        tmpl_count = len(self.template_manager.get_template_names())

        self.thread = threading.Thread(target=self._watch_loop, daemon=True)
        self.thread.start()
        today = datetime.date.today()
        cutoff = today - datetime.timedelta(days=self.days_back_limit)
        self.log("🚀 Užsakymų ir Brokų fono stebėjimas PALEISTAS!")
        self.log(f"📁 Standartinis Hotfolderis: {self.input_folder}")
        if self.rejects_input_folder:
            self.log(f"🔴 Brokų / Rejects Hotfolderis: {self.rejects_input_folder}")
        self.log(f"📁 Išvestis: {self.output_folder} (Brokai -> {os.path.join(self.output_folder, 'BROKAI')})")
        self.log(f"⚡ Lygiagrečių gijų skaičius: {self.max_workers} | Praleisti jau paruoštus: {'TAIP' if self.skip_existing else 'NE'}")
        self.log(f"📅 Užsakymų senumo filtras: tik nuo {cutoff} iki šiandien ({today}) [Paskutinės {self.days_back_limit} d. + šiandien]")
        self.log(f"📐 Aktyvių šablonų skaičius: {tmpl_count} (Aplankas: '{self.templates_folder}')")

    def stop(self):
        self.running = False
        self.log("⏹ Stebėjimas SUSTABDYTAS.")

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
        """Tikrina, ar naujas failas baigtas kelti/įrašyti į diską (greita ir be dirbtinio laukimo)."""
        try:
            sz = os.path.getsize(file_path)
            if sz <= 0:
                return False
            # Bandom atidaryti skaitymui – jei failas dar kopijuojamas, Windows išmes PermissionError
            with open(file_path, 'rb') as f:
                f.read(1024)
            return True
        except (OSError, PermissionError):
            return False

    def _should_ignore(self, path_or_name: str) -> bool:
        """Tikrina, ar failas ar aplankas turi būti ignoruojamas (pvz. batch sheet)."""
        lower = path_or_name.lower()
        for kw in IGNORE_KEYWORDS:
            if kw in lower:
                return True
        return False

    def get_output_path_for_file(self, file_path: str, is_reject: bool = False, base_input_dir: Optional[str] = None) -> Tuple[str, str]:
        """
        Apskaičiuoja tikslinį .tif išvesties kelią pagal failo vietą įvesties aplanke.
        Grąžina (out_path, rel_path).
        """
        if not base_input_dir:
            norm_fp = os.path.normpath(file_path).lower()
            norm_rej = os.path.normpath(self.rejects_input_folder).lower() if self.rejects_input_folder else ""
            if norm_rej and norm_fp.startswith(norm_rej):
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
        Nuskenuoja tiek standartinį, tiek brokų / rejects įvesties aplankus.
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
        missing_template_files = 0

        sources = []
        if self.input_folder:
            sources.append((self.input_folder, False, "STANDARTINIS"))
        if self.rejects_input_folder and self.rejects_input_folder.lower() != self.input_folder.lower():
            sources.append((self.rejects_input_folder, True, "BROKAS"))

        for folder, is_reject, src_label in sources:
            tag = "🔴 [BROKAI]" if is_reject else "📦 [STANDARTINIS]"
            self.log(f"\n{tag} Skenuojamas aplankas: {folder}")

            if not os.path.exists(folder):
                self.log(f"   ❌ KLAIDA: Kelias nepasiekiamas: '{folder}'")
                self.log(f"   💡 Patikrinkite tinklo ryšį arba pakoreguokite kelią '⚙️ Nustatymai' skiltyje.")
                continue

            folder_file_count = 0
            try:
                for root, dirs, files in os.walk(folder):
                    # Praleidžiame ignoruojamus aplankus ir senesnes nei cutoff_date datas (neiname gilyn)
                    dirs[:] = [
                        d for d in dirs
                        if not self._should_ignore(d)
                        and not self._is_dir_older_than_cutoff(os.path.join(root, d), cutoff_date)
                    ]

                    for file_name in files:
                        if file_name.startswith(('.', '~')) or self._should_ignore(file_name):
                            continue

                        if file_name.lower().endswith(SUPPORTED_EXTENSIONS):
                            file_path = os.path.join(root, file_name)

                            # Tikriname ar failas nėra senesnis už leistiną ribą (šiandien + paskutinės 3 d.)
                            if self._is_file_older_than_cutoff(file_path, cutoff_date):
                                continue

                            # Ieškome atitinkamo šablono
                            tmpl_name, tmpl_path = self.template_manager.find_template_for_path(file_path)
                            if not tmpl_path:
                                # Skenuojame TIK tuos produktus, kurie vadinasi taip kaip šablonai.
                                # Kiti produktai (pvz. marškinėliai, kiti dėklai) visiškai ignoruojami.
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
        self.log(f"   - Standartinių modelių grupių: {std_count}")
        self.log(f"   - Brokų / Rejects grupių: {reject_count}")
        if missing_template_files > 0:
            self.log(f"   ⚠️ Dėmesio: {missing_template_files} failams nerastas atitinkamas šablonas Sablonai aplanke!")

        return result

    def _process_single_order_file(self, file_path: str, is_reject: bool = False, base_input_dir: Optional[str] = None) -> bool:
        """Atskiro failo apdorojimas su esamų failų praleidimu."""
        if self._should_ignore(file_path):
            return False

        tmpl_name, tmpl_path = self.template_manager.find_template_for_path(file_path)
        if not tmpl_path:
            self.log(f"⚠️ Praleistas {os.path.basename(file_path)} - nerastas šablonas!")
            return False

        out_path, rel_path = self.get_output_path_for_file(file_path, is_reject=is_reject, base_input_dir=base_input_dir)

        if self.skip_existing and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            self.processed_files.add(file_path)
            self.log(f"⏩ Jau paruoštas (Praleidžiama): {os.path.basename(file_path)}")
            return True

        tag = "🔴 [BROKAS]" if is_reject else "🎨 [STANDARTINIS]"
        start_t = time.time()
        try:
            success = process_and_crop(
                image_path=file_path,
                template_path=tmpl_path,
                output_path=out_path,
                choke_pixels=self.choke_pixels,
                spot_channel_name=self.spot_channel_name,
                solidity=self.solidity,
                target_dpi=self.target_dpi
            )
            elapsed = time.time() - start_t
            if success:
                self.processed_files.add(file_path)
                if self.delete_original:
                    try:
                        os.remove(file_path)
                    except Exception:
                        pass
                self.log(f"{tag} ✅ IŠSAUGOTA ({elapsed:.2f}s): {os.path.basename(file_path)} -> {out_path}")
                return True
            return False
        except Exception as e:
            self.log(f"❌ KLAIDA apdorojant {file_path}: {e}")
            return False

    def process_selected_groups(
        self,
        selected_groups: List[Dict[str, Any]],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        skip_existing: Optional[bool] = None,
        max_workers: Optional[int] = None
    ) -> int:
        """
        Lygiagrečiai ir itin greitai apdoroja tik vartotojo varnele pažymėtas grupes.
        Jei skip_existing=True, jau konvertuoti failai automatiškai praleidžiami.
        """
        if skip_existing is None:
            skip_existing = self.skip_existing
        if max_workers is None:
            max_workers = self.max_workers

        valid_groups = [g for g in selected_groups if g.get("has_template") and g.get("template_path")]
        skipped_groups = [g for g in selected_groups if not g.get("has_template") or not g.get("template_path")]

        if skipped_groups:
            for sg in skipped_groups:
                self.log(f"⚠️ Praleidžiama grupė '{sg['key']}', nes trūksta šablono ({sg['template_name']})!")

        # Paruošiame užduočių sąrašą
        tasks = []
        for g in valid_groups:
            tmpl_path = g["template_path"]
            tmpl_name = g["template_name"]
            is_reject = g.get("is_reject", False)
            base_folder = g.get("base_folder", self.input_folder)

            for file_path in g["files"]:
                tasks.append({
                    "file_path": file_path,
                    "template_path": tmpl_path,
                    "template_name": tmpl_name,
                    "is_reject": is_reject,
                    "base_folder": base_folder,
                    "group_key": g["key"]
                })

        total_files = len(tasks)
        self.log(f"\n🚀 Pradedama gamyba! Pasirinkta grupių: {len(valid_groups)} (Iš viso failų: {total_files})")
        self.log(f"⚡ Lygiagrečių darbuotojų (Threads): {max_workers} | Praleisti jau paruoštus: {'TAIP' if skip_existing else 'NE'}")

        if total_files == 0:
            if progress_callback:
                progress_callback(0, 0, "Nėra failų gamybai.")
            return 0

        processed_count = 0
        skipped_count = 0
        failed_count = 0
        current_idx = 0
        counter_lock = threading.Lock()

        def _worker_task(item: Dict[str, Any]) -> Tuple[bool, bool, str, str]:
            file_path = item["file_path"]
            tmpl_path = item["template_path"]
            tmpl_name = item["template_name"]
            is_reject = item["is_reject"]
            base_folder = item["base_folder"]
            f_name = os.path.basename(file_path)

            out_path, rel_path = self.get_output_path_for_file(file_path, is_reject=is_reject, base_input_dir=base_folder)

            # Tikriname ar jau egzistuoja
            if skip_existing and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                self.processed_files.add(file_path)
                return True, True, f_name, f"⏩ Jau paruoštas (Praleidžiama): {f_name} -> {os.path.basename(out_path)}"

            tag = "🔴 [BROKAS]" if is_reject else "🎨 [STANDARTINIS]"
            start_t = time.time()
            try:
                success = process_and_crop(
                    image_path=file_path,
                    template_path=tmpl_path,
                    output_path=out_path,
                    choke_pixels=self.choke_pixels,
                    spot_channel_name=self.spot_channel_name,
                    solidity=self.solidity,
                    target_dpi=self.target_dpi
                )
                elapsed = time.time() - start_t
                if success:
                    self.processed_files.add(file_path)
                    if self.delete_original:
                        try:
                            os.remove(file_path)
                        except Exception:
                            pass
                    return True, False, f_name, f"{tag} ✅ IŠSAUGOTA ({elapsed:.2f}s): {f_name} -> {out_path}"
                else:
                    return False, False, f_name, f"❌ Nepavyko išsaugoti: {f_name}"
            except Exception as e:
                return False, False, f_name, f"❌ KLAIDA apdorojant {f_name}: {e}"

        # Lygiagretus vykdymas su ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_file = {executor.submit(_worker_task, item): item for item in tasks}

            for future in as_completed(future_to_file):
                success, was_skipped, f_name, msg = future.result()
                self.log(msg)

                with counter_lock:
                    current_idx += 1
                    if success:
                        if was_skipped:
                            skipped_count += 1
                        else:
                            processed_count += 1
                    else:
                        failed_count += 1

                    if progress_callback:
                        progress_callback(current_idx, total_files, f_name)

        self.log(f"\n🏁 GAMYBA BAIGTA!")
        self.log(f"   - ✅ Naujai sugeneruota: {processed_count}")
        self.log(f"   - ⏩ Praleista (jau buvo paruošti): {skipped_count}")
        if failed_count > 0:
            self.log(f"   - ❌ Nesėkmingi: {failed_count}")

        return processed_count + skipped_count

    def process_all_now(self) -> int:
        """Vienu paspaudimu nuskenuoja visus įvesties aplankus ir apdoroja visus rastus failus."""
        groups = self.scan_available_orders()
        return self.process_selected_groups(groups)

    def _watch_loop(self):
        last_tmpl_reload = time.time()

        while self.running:
            # Kas 45 sek. automatiškai perskaitome šablonų aplanką (jei buvo įkeltas naujas šablonas)
            if time.time() - last_tmpl_reload > 45:
                self.template_manager.reload_templates()
                last_tmpl_reload = time.time()

            today = datetime.date.today()
            cutoff_date = today - datetime.timedelta(days=self.days_back_limit)

            sources = []
            if self.input_folder:
                sources.append((self.input_folder, False))
            if self.rejects_input_folder and self.rejects_input_folder.lower() != self.input_folder.lower():
                sources.append((self.rejects_input_folder, True))

            batch_to_process = []

            for folder, is_reject in sources:
                if not self.running:
                    break
                if folder and os.path.exists(folder):
                    try:
                        for root, dirs, files in os.walk(folder):
                            if not self.running:
                                break
                            # Praleidžiame ignoruojamus aplankus ir senesnes nei cutoff_date datas
                            dirs[:] = [
                                d for d in dirs
                                if not self._should_ignore(d)
                                and not self._is_dir_older_than_cutoff(os.path.join(root, d), cutoff_date)
                            ]

                            for file_name in files:
                                if not self.running:
                                    break
                                if file_name.startswith(('.', '~')) or self._should_ignore(file_name):
                                    continue

                                if file_name.lower().endswith(SUPPORTED_EXTENSIONS):
                                    file_path = os.path.join(root, file_name)

                                    if file_path in self.processed_files:
                                        continue

                                    # Tikriname ar failas nėra senesnis už leistiną ribą
                                    if self._is_file_older_than_cutoff(file_path, cutoff_date):
                                        self.processed_files.add(file_path)
                                        continue

                                    # Tikriname ar jau buvo konvertuotas
                                    if self.skip_existing and self.is_file_already_converted(file_path, is_reject=is_reject, base_input_dir=folder):
                                        self.processed_files.add(file_path)
                                        continue

                                    # PIRMIAUSIA TIKRINAME ŠABLONĄ
                                    tmpl_name, tmpl_path = self.template_manager.find_template_for_path(file_path)
                                    if not tmpl_path:
                                        # Nesusijęs produktas (neturi šablono) – pažymime kaip apdorotą,
                                        # kad nebūtų vėl ir vėl gaištamas laikas kiekvieno ciklo metu!
                                        self.processed_files.add(file_path)
                                        continue

                                    # Tikriname ar failas baigtas įrašyti
                                    if self._is_file_ready(file_path):
                                        out_p, _ = self.get_output_path_for_file(file_path, is_reject=is_reject, base_input_dir=folder)
                                        batch_to_process.append((file_path, tmpl_path, out_p, is_reject, file_name))
                    except Exception as e:
                        self.log(f"Stebėjimo pranešimas ({folder}): {e}")

            # Lygiagretus naujų užsakymų generavimas per gijų baseiną (ThreadPoolExecutor)
            if batch_to_process and self.running:
                def _auto_worker(task):
                    f_path, t_path, o_p, is_rej, f_name = task
                    tag = "🔴 [BROKAS]" if is_rej else "🎨 [STANDARTINIS]"
                    start_t = time.time()
                    try:
                        ok = process_and_crop(
                            image_path=f_path,
                            template_path=t_path,
                            output_path=o_p,
                            choke_pixels=self.choke_pixels,
                            spot_channel_name=self.spot_channel_name,
                            solidity=self.solidity,
                            target_dpi=self.target_dpi
                        )
                        if ok:
                            self.processed_files.add(f_path)
                            elapsed = time.time() - start_t
                            self.log(f"{tag} ✅ Auto-paruoštas ({elapsed:.2f}s): {f_name} -> {o_p}")
                            if self.on_file_processed_callback:
                                try:
                                    self.on_file_processed_callback(f_path, o_p, is_rej)
                                except Exception:
                                    pass
                    except Exception as err:
                        self.log(f"Klaida auto-apdorojant {f_name}: {err}")

                with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    list(executor.map(_auto_worker, batch_to_process))

            time.sleep(3)
