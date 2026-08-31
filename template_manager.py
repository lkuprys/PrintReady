import os
import re
from typing import Dict, Optional, List, Tuple

class TemplateManager:
    def __init__(self, templates_dir: str):
        self.templates_dir = os.path.normpath(templates_dir)
        self.templates: Dict[str, str] = {}
        self.reload_templates()

    def set_templates_dir(self, new_dir: str):
        self.templates_dir = os.path.normpath(new_dir)
        self.reload_templates()

    def reload_templates(self):
        """Iš naujo nuskaito visus .png šablonus iš šablonų aplanko."""
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

    def find_template_for_path(self, full_file_path: str) -> Tuple[Optional[str], Optional[str]]:
        r"""
        Ieško tinkamo šablono pagal visą failo kelią ir failo vardą.
        Palaiko bet kokį aplankų gylį, reject / brokų žymas ir įvairius skyriklius.
        
        Grąžina (template_name, template_full_path) arba (None, None).
        """
        if not self.templates:
            self.reload_templates()
            if not self.templates:
                return None, None

        norm_path = full_file_path.replace('\\', '/').lower()
        file_name = os.path.basename(full_file_path).lower()

        # Rūšiuojame šablonų raktus pagal ilgį mažėjančia tvarka (kad 'A2681' būtų pirma nei 'A26')
        sorted_keys = sorted(self.templates.keys(), key=lambda k: len(k), reverse=True)

        # 1. Tikriname žodžio ribų atitikimą pagal skyriklius (-, _, /, ., tarpai)
        for key in sorted_keys:
            norm_key = key.lower()
            pattern = r'(?:^|[^a-z0-9])' + re.escape(norm_key) + r'(?:$|[^a-z0-9])'
            if re.search(pattern, norm_path):
                return key, self.templates[key]

        # 2. Tikriname tiesioginį dalinį atitikimą
        for key in sorted_keys:
            norm_key = key.lower()
            if norm_key in norm_path:
                return key, self.templates[key]

        # 3. Tikriname be brūkšnelių/pabraukimų (pvz. 'a2681' vs 'a-2681')
        clean_path = re.sub(r'[^a-z0-9]', '', norm_path)
        for key in sorted_keys:
            clean_key = re.sub(r'[^a-z0-9]', '', key.lower())
            if clean_key and len(clean_key) >= 3 and clean_key in clean_path:
                return key, self.templates[key]

        return None, None

    def find_template_for_model(self, model_name: str) -> Optional[str]:
        """Suderinamumo funkcija ieškant tiesiogiai pagal modelio tekstą."""
        t_name, t_path = self.find_template_for_path(model_name)
        return t_path
