import os
import re
from typing import Dict, Optional, List, Tuple

IGNORE_DIR_PATTERNS = (
    r'^\d{4}-\d{2}-\d{2}$',     # Datos (pvz. 2026-09-19)
    r'^\d{1,2}$',               # Trumpi partijų / generacijų numeriai (pvz. 1, 20)
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
        - 4 skaitmenų MacBook modeliams (pvz. '2681' arba 'A2681'):
          Leidžia 'A' prefiksą arba be jo ((?:a)?2681), su griežtomis ribomis (?<![a-z0-9]) ir (?![a-z0-9]).
        - Tekstiniams modeliams (pvz. 'NEO', 'MacBook Air'):
          Reikalauja visų žodžių atitikimo su griežtomis ribomis.
        - NIEKADA neleidžia dalinio skaičiaus sutapimo (pvz. '26815' NEATITIKS '2681').
        """
        k = key.strip().lower()
        m_num = re.fullmatch(r'a?(\d{4})', k)
        if m_num:
            digits = m_num.group(1)
            return rf'(?<![a-z0-9])(?:a)?{digits}(?![a-z0-9])'

        tokens = re.findall(r'[a-z]+|\d+', k)
        if not tokens:
            return rf'(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])'

        pat_parts = [re.escape(t) for t in tokens]
        inner = r'[-_\s]*'.join(pat_parts)
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

    def _is_ignored_dir(self, dir_name: str) -> bool:
        lower = dir_name.lower().strip()
        return any(re.match(p, lower) for p in IGNORE_DIR_PATTERNS)

    def find_template_for_path(self, full_file_path: str) -> Tuple[Optional[str], Optional[str]]:
        r"""
        Ieško tinkamo šablono hierarchine tvarka:
        1. Pirmiausia tiriant patį failo pavadinimą (be plėtinio).
        2. Tiriant tiesioginį tėvinį aplanką (pvz. '2681', 'A2681', 'MacBook Air 13 A2681', 'NEO').
        3. Tiriant aukštesnius tėvinius aplankus (jei failas yra Bid-1/Batch ar kitame sub-aplanke).
        
        Grąžina (template_name, template_full_path) arba (None, None).
        """
        if not self.templates:
            self.reload_templates()
            if not self.templates:
                return None, None

        # Rūšiuojame šablonų raktus pagal ilgį mažėjančia tvarka
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
        if parent_dir_base:
            matched = self._match_segment(parent_dir_base, sorted_keys)
            if matched:
                return matched, self.templates[matched]

        # 3. Prioritetas: aukštesni aplankai (pvz., jei failas yra Bid-1 ar partijos aplanko viduje)
        # Tikriname iki 4 lygių į viršų
        curr_dir = dir_name
        levels_checked = 0
        while curr_dir and os.path.dirname(curr_dir) != curr_dir and levels_checked < 4:
            levels_checked += 1
            curr_dir = os.path.dirname(curr_dir)
            seg = os.path.basename(curr_dir)
            if not seg:
                break

            # Pirmiausia tikriname ar šis aplankas atitinka kurį nors šabloną
            matched = self._match_segment(seg, sorted_keys)
            if matched:
                return matched, self.templates[matched]

            # Jei tai bendrinis sistemos aplankas (data, bid, batch ir kt.), tęsiame aukštyn
            if self._is_ignored_dir(seg):
                continue

        return None, None

    def find_template_for_model(self, model_name: str) -> Optional[str]:
        """Suderinamumo funkcija ieškant tiesiogiai pagal modelio tekstą."""
        t_name, t_path = self.find_template_for_path(model_name)
        return t_path
