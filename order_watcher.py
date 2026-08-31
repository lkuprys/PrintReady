import os
import re
import time
import threading
from typing import Callable, Optional, Set, List, Dict, Any
from template_manager import TemplateManager
from crop_engine import process_and_crop

SUPPORTED_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp', '.tif', '.tiff')
IGNORE_KEYWORDS = ('batch sheet', 'bach sheet', 'batchsheet', 'bachsheet', 'batch_sheet', 'bach_sheet')

DEFAULT_STD_INPUT = r"\\192.168.1.143\podbase-hotfolder\BENDRAS_PODBASE_HOTFOLDER"
DEFAULT_REJECTS_INPUT = r"\\192.168.1.143\podbase-rejects\BENDRAS_PODBASE_HOTFOLDER"
DEFAULT_OUTPUT = r"C:\Users\kingt\Desktop\Macbook print files\READY"

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
        log_callback: Optional[Callable[[str], None]] = None
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
        self.log_callback = log_callback

        self.template_manager = TemplateManager(self.templates_folder)
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.processed_files: Set[str] = set()

    def log(self, message: str):
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
        self.thread = threading.Thread(target=self._watch_loop, daemon=True)
        self.thread.start()
        self.log("🚀 Užsakymų ir Brokų stebėjimas PALEISTAS!")
        self.log(f"📁 Standartinis Hotfolderis: {self.input_folder}")
        if self.rejects_input_folder:
            self.log(f"🔴 Brokų / Rejects Hotfolderis: {self.rejects_input_folder}")
        self.log(f"📁 Išvestis: {self.output_folder} (Brokai -> {os.path.join(self.output_folder, 'BROKAI')})")
        self.log(f"📁 Šablonai: {self.templates_folder}")

    def stop(self):
        self.running = False
        self.log("⏹ Stebėjimas SUSTABDYTAS.")

    def _is_file_ready(self, file_path: str, wait_time: float = 0.5) -> bool:
        """Tikrina, ar failas baigtas kelti/įrašyti į diską."""
        try:
            initial_size = os.path.getsize(file_path)
            time.sleep(wait_time)
            current_size = os.path.getsize(file_path)
            return initial_size == current_size and initial_size > 0
        except OSError:
            return False

    def _should_ignore(self, path_or_name: str) -> bool:
        """Tikrina, ar failas ar aplankas turi būti ignoruojamas (pvz. batch sheet)."""
        lower = path_or_name.lower()
        for kw in IGNORE_KEYWORDS:
            if kw in lower:
                return True
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

        # 1. Datos nustatymas (ieškome YYYY-MM-DD arba naudojame aplanko vardą)
        date_match = re.search(r'\d{4}-\d{2}-\d{2}', rel_path)
        if date_match:
            date_val = date_match.group(0)
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
        Grąžina rasto sąrašo grupes su šablonų informacija ir būsenomis.
        """
        self.log("🔍 Skenuojami užsakymų aplankai...")
        self.template_manager.reload_templates()
        tmpl_count = len(self.template_manager.get_template_names())
        self.log(f"📐 Aktyvių šablonų skaičius: {tmpl_count} (Aplankas: '{self.templates_folder}')")
        if tmpl_count == 0:
            self.log(f"⚠️ DĖMESIO: Šablonų aplanke '{self.templates_folder}' nerasta jokių .PNG failų!")

        groups: Dict[str, Dict[str, Any]] = {}
        total_found_files = 0
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
                    dirs[:] = [d for d in dirs if not self._should_ignore(d)]

                    for file_name in files:
                        if file_name.startswith(('.', '~')) or self._should_ignore(file_name):
                            continue

                        if file_name.lower().endswith(SUPPORTED_EXTENSIONS):
                            file_path = os.path.join(root, file_name)
                            folder_file_count += 1
                            total_found_files += 1

                            # Ieškome atitinkamo šablono
                            tmpl_name, tmpl_path = self.template_manager.find_template_for_path(file_path)

                            rel_path = os.path.relpath(file_path, folder)
                            date_val, gen_val, model_val = self._parse_hierarchy_parts(rel_path, is_reject, tmpl_name)

                            prefix = "[BROKAS] " if is_reject else ""
                            group_key = f"{prefix}{date_val} / {gen_val} / {model_val}"

                            has_tmpl = (tmpl_path is not None)
                            if not has_tmpl:
                                missing_template_files += 1

                            if group_key not in groups:
                                groups[group_key] = {
                                    "key": group_key,
                                    "date": date_val,
                                    "generation": gen_val,
                                    "model": model_val,
                                    "template_name": tmpl_name if tmpl_name else "NĖRA ŠABLONO",
                                    "template_path": tmpl_path,
                                    "has_template": has_tmpl,
                                    "is_reject": is_reject,
                                    "source_label": src_label,
                                    "base_folder": folder,
                                    "files": []
                                }

                            groups[group_key]["files"].append(file_path)

                self.log(f"   ✅ Rasta palaikomų failų: {folder_file_count}")
            except Exception as e:
                self.log(f"   ❌ Klaida skenuojant {folder}: {e}")

        result = list(groups.values())
        reject_count = sum(1 for g in result if g.get("is_reject"))
        std_count = len(result) - reject_count

        self.log(f"\n📊 Nuskenavimo suvestinė:")
        self.log(f"   - Iš viso rasta failų: {total_found_files}")
        self.log(f"   - Standartinių modelių grupių: {std_count}")
        self.log(f"   - Brokų / Rejects grupių: {reject_count}")
        if missing_template_files > 0:
            self.log(f"   ⚠️ Dėmesio: {missing_template_files} failams nerastas atitinkamas šablonas Sablonai aplanke!")

        return result

    def process_selected_groups(
        self,
        selected_groups: List[Dict[str, Any]],
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> int:
        """Apdoroja tik vartotojo varnele pažymėtas grupes."""
        valid_groups = [g for g in selected_groups if g.get("has_template") and g.get("template_path")]
        skipped_groups = [g for g in selected_groups if not g.get("has_template") or not g.get("template_path")]

        if skipped_groups:
            for sg in skipped_groups:
                self.log(f"⚠️ Praleidžiama grupė '{sg['key']}', nes trūksta šablono ({sg['template_name']})!")

        total_files = sum(len(g["files"]) for g in valid_groups)
        self.log(f"🚀 Pradedama gamyba! Pasirinkta grupių: {len(valid_groups)} (Iš viso failų: {total_files})")
        processed_count = 0
        current_idx = 0

        for g in valid_groups:
            tmpl_path = g["template_path"]
            tmpl_name = g["template_name"]
            is_reject = g.get("is_reject", False)
            base_folder = g.get("base_folder", self.input_folder)
            
            tag = "🔴 [BROKAS]" if is_reject else "📦"
            dest_hint = "-> READY/BROKAI" if is_reject else "-> READY"
            self.log(f"\n{tag} Modelio grupė: {g['key']} (Šablonas: {tmpl_name}.png {dest_hint})")

            for file_path in g["files"]:
                current_idx += 1
                if progress_callback:
                    progress_callback(current_idx, total_files, os.path.basename(file_path))
                if self._process_single_order_file(file_path, is_reject=is_reject, base_input_dir=base_folder):
                    processed_count += 1

        if progress_callback:
            progress_callback(total_files, total_files, "Baigta!")

        self.log(f"\n🏁 GAMYBA BAIGTA! Sėkmingai sugeneruota {processed_count} iš {total_files} failų.")
        return processed_count

    def process_all_now(self) -> int:
        """Vienu paspaudimu nuskenuoja visus įvesties aplankus ir apdoroja visus rastus failus."""
        groups = self.scan_available_orders()
        return self.process_selected_groups(groups)

    def _process_single_order_file(self, file_path: str, is_reject: bool = False, base_input_dir: Optional[str] = None) -> bool:
        if self._should_ignore(file_path):
            return False

        template_name, template_path = self.template_manager.find_template_for_path(file_path)
        if not template_path:
            self.log(f"⚠️ Praleistas {os.path.basename(file_path)} - nerastas šablonas!")
            return False

        if not self._is_file_ready(file_path):
            return False

        # Nustatome bazinį įvesties aplanką ir ar tai brokas
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

        # Jei brokas -> išsaugome į READY/BROKAI/rel_dir, kitaip tiesiai į READY/rel_dir
        if is_reject:
            out_dir = os.path.join(self.output_folder, "BROKAI", rel_dir)
            log_tag = "🔴 [BROKAS]"
        else:
            out_dir = os.path.join(self.output_folder, rel_dir)
            log_tag = "🎨 [STANDARTINIS]"

        out_path = os.path.join(out_dir, out_file_name)

        self.log(f"{log_tag} Spauda: {rel_path} -> Šablonas: '{template_name}.png'")
        start_time = time.time()
        try:
            success = process_and_crop(
                image_path=file_path,
                template_path=template_path,
                output_path=out_path,
                choke_pixels=self.choke_pixels,
                spot_channel_name=self.spot_channel_name,
                solidity=self.solidity,
                target_dpi=self.target_dpi
            )
            elapsed = time.time() - start_time
            if success:
                self.log(f"   ✅ IŠSAUGOTA ({elapsed:.2f}s)! -> {out_path}")
                self.processed_files.add(file_path)
                if self.delete_original:
                    try:
                        os.remove(file_path)
                    except Exception as e:
                        self.log(f"Pastaba: Nepavyko ištrinti įvesties failo: {e}")
                return True
        except Exception as e:
            self.log(f"   ❌ KLAIDA apdorojant {file_path}: {e}")
            return False

    def _watch_loop(self):
        while self.running:
            sources = []
            if self.input_folder:
                sources.append((self.input_folder, False))
            if self.rejects_input_folder and self.rejects_input_folder.lower() != self.input_folder.lower():
                sources.append((self.rejects_input_folder, True))

            for folder, is_reject in sources:
                if not self.running:
                    break
                if folder and os.path.exists(folder):
                    try:
                        for root, dirs, files in os.walk(folder):
                            if not self.running:
                                break
                            dirs[:] = [d for d in dirs if not self._should_ignore(d)]

                            for file_name in files:
                                if not self.running:
                                    break
                                if file_name.startswith(('.', '~')) or self._should_ignore(file_name):
                                    continue

                                if file_name.lower().endswith(SUPPORTED_EXTENSIONS):
                                    file_path = os.path.join(root, file_name)
                                    if file_path not in self.processed_files:
                                        self._process_single_order_file(file_path, is_reject=is_reject, base_input_dir=folder)
                    except Exception as e:
                        self.log(f"Stebėjimo pranešimas ({folder}): {e}")

            time.sleep(3)
