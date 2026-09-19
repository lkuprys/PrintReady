import os
import re
from typing import Dict, Optional, List, Tuple

IGNORE_DIR_PATTERNS = (
    r'^\d{4}-\d{2}-\d{2}$',     # Datos (pvz. 2026-09-19)
    r'^\d{1,4}$',               # Trumpi skaitiniai aplankai (pvz. 1, 20)
    r'^batch[-_]?\d+$',         # Partijų numeriai (pvz. batch-1)
    r'^bid[-_]?\d+$',           # Bid numeriai (pvz. bid-20)
    r'^ready$',                 # Išvesties aplankas
    r'^brokai$',                # Brokų aplankas
    r'^rejects?$',              # Rejects aplankas
    r'^hotfolders?$',           # Hotfolderio pavadinimas
    r'^bendras.*$',             # Bendras hotfolderis
    r'^podbase.*$',             # Podbase sistemos vardas
    r'^sablonai$',              # Šablonų aplankas
    r'^assets$',                # Resursų aplankas
    r'^dist$',                  # Kompiliavimo aplankas
    r'^build$',                 # Kompiliavimo aplankas
    r'^projects?$',             # Projektų aplankas
    r'^workspace$'              # Darbo aplankas
)

class TemplateManager:
    def __init__(self, templates_dir: str):
        self.templates_dir = os.path.normpath(templates_dir)
        self.templates: Dict[str, str] = {}
        self.reload_templates()

    def set_templates_dir(self, new_dir: str):
        self.templates_dir = os.path.normpath(new_dir)
        self.reload_templates()

    def reload_templates(self):
        """Iš naujo nuskaito visus .png ir .tif šablonus iš šablonų aplanko."""
        self.templates.clear()
        if not os.path.exists(self.templates_dir):
            try:
                os.makedirs(self.templates_dir, exist_ok=True)
            except Exception:
                pass
            return

        try:
            for f in os.listdir(self.templates_dir):
                if f.lower().endswith(('.png', '.tif', '.tiff')):
                    full_path = os.path.join(self.templates_dir, f)
                    base_name = os.path.splitext(f)[0]
                    self.templates[base_name] = full_path
        except Exception:
            pass

    def get_template_names(self) -> List[str]:
        """Grąžina rastų šablonų pavadinimų sąrašą."""
        return sorted(list(self.templates.keys()))

    @staticmethod
    def _build_strict_regex(key: str) -> str:
        """
        Sukuria griežtą reguliariąją išraišką šablonui:
        - Išlaiko visus šablono žodžius ir skaičius (pvz. 'A2681' -> ['a', '2681'], 'Macbook Air 13' -> ['macbook', 'air', '13']).
        - Leidžia tarp dalių bet kokius skirtukus (tarpus, brūkšnelius, pabraukimus).
        - Griežtos ribos: (?<![a-z0-9]) ir (?![a-z0-9]), kad nesutaptų su kitais skaičiais/žodžiais.
        - NIEKADA neleidžia praleisti raidžių (pvz. 'A2681' NEATITIKS '2681').
        """
        k = key.strip().lower()
        tokens = re.findall(r'[a-z]+|\d+', k)
        if not tokens:
            return rf'(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])'
        
        pat_parts = []
        for i, t in enumerate(tokens):
            pat_parts.append(re.escape(t))
            if i < len(tokens) - 1:
                pat_parts.append(r'[-_\s]*')
        inner = ''.join(pat_parts)
        return rf'(?<![a-z0-9]){inner}(?![a-z0-9])'

    def _match_segment(self, text: str, sorted_keys: List[str]) -> Optional[str]:
        """
        Ieško griežto šablono atitikmens nurodytame tekste (faile arba aplanko varde).
        Užtikrina griežtas žodžio ribas, kad kitų produktų numeriai ar pavadinimai nebūtų klaidingai susieti.
        """
        if not text:
            return None

        t_low = text.lower()
        for key in sorted_keys:
            pat = self._build_strict_regex(key)
            if re.search(pat, t_low):
                return key

        return None

    def find_template_for_path(self, full_file_path: str) -> Tuple[Optional[str], Optional[str]]:
        r"""
        Ieško tinkamo šablono hierarchine tvarka:
        1. Pirmiausia tiriant patį failo pavadinimą (be plėtinio).
        2. Tiriant tiesioginį tėvinį aplanką (kur dažniausiai yra modelis, pvz. 'Macbook Air 13 A2681').
        3. Tiriant aukštesnius tėvinius aplankus (ignoruojant datas, 'READY', 'BROKAI', 'Hotfolder' ir kt.).
        
        NIEKADA neatlieka aklo ieškojimo visame absoliučiame kelyje (apsauga nuo 'Projects', 'PrintReady' ir kt.).
        Grąžina (template_name, template_full_path) arba (None, None).
        """
        if not self.templates:
            self.reload_templates()
            if not self.templates:
                return None, None

        # Rūšiuojame šablonų raktus pagal ilgį mažėjančia tvarka (kad 'A2681' turėtų pirmenybę prieš 'A26')
        sorted_keys = sorted(self.templates.keys(), key=lambda k: len(k), reverse=True)

        norm_path = os.path.normpath(full_file_path)
        dir_name, base_file = os.path.split(norm_path)
        file_name_no_ext, _ = os.path.splitext(base_file)

        # 1. Prioritetas: paties failo pavadinimas
        matched = self._match_segment(file_name_no_ext, sorted_keys)
        if matched:
            return matched, self.templates[matched]

        # 2. Prioritetas: tiesioginis tėvinis aplankas
        parent_dir_base = os.path.basename(dir_name)
        if parent_dir_base and not any(re.match(p, parent_dir_base.lower()) for p in IGNORE_DIR_PATTERNS):
            matched = self._match_segment(parent_dir_base, sorted_keys)
            if matched:
                return matched, self.templates[matched]

        # 3. Prioritetas: aukštesni aplankai (pvz., jei failas yra Bid-1/Batch viduje, ieškome modelyje)
        # Ribojame iki daugiausiai 3 lygių į viršų, kad niekada nepasiektų disko šaknies ar nesusijusių aplankų.
        curr_dir = os.path.dirname(dir_name)
        levels_checked = 0
        while curr_dir and os.path.dirname(curr_dir) != curr_dir and levels_checked < 3:
            levels_checked += 1
            seg = os.path.basename(curr_dir)
            if not seg:
                break
            # Ignoruojame bendrinius aplankus (datas, partijas, sistemos aplankus)
            if any(re.match(p, seg.lower()) for p in IGNORE_DIR_PATTERNS):
                curr_dir = os.path.dirname(curr_dir)
                continue

            matched = self._match_segment(seg, sorted_keys)
            if matched:
                return matched, self.templates[matched]

            curr_dir = os.path.dirname(curr_dir)

        return None, None

    def find_template_for_model(self, model_name: str) -> Optional[str]:
        """Suderinamumo funkcija ieškant tiesiogiai pagal modelio tekstą."""
        t_name, t_path = self.find_template_for_path(model_name)
        return t_path
