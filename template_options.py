"""
Šablonų papildomos taisyklės (pvz. spaudos failą pasukti 180°).

Taisyklės saugomos šablonų aplanke, faile `sablonu_nustatymai.json`, todėl keliauja kartu su
šablonais, o atnaujinimas jų neperrašo (Sablonai aplankas apsaugotas). Pavyzdys:

    {
      "2681": {"output_rotation": 180},
      "NEO": {"image_rotation": 90, "mirror": true}
    }

Laukai:
  output_rotation – viso spaudos failo (kontūro ir nuotraukos) pasukimas pagal laikrodžio rodyklę: 0/90/180/270
  image_rotation  – tik kliento nuotraukos pasukimas kontūro viduje (kontūras lieka vietoje): 0/90/180/270
  mirror          – spaudos failo veidrodinis atspindys (kairė ↔ dešinė)
"""
import os
import json
import threading
from dataclasses import dataclass, fields
from typing import Dict, Optional, Tuple

OPTIONS_FILE = "sablonu_nustatymai.json"
ROTATIONS = (0, 90, 180, 270)


@dataclass(frozen=True)
class TemplateOptions:
    output_rotation: int = 0
    image_rotation: int = 0
    mirror: bool = False

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "TemplateOptions":
        data = data or {}

        def rot(key: str) -> int:
            try:
                v = int(data.get(key, 0)) % 360
            except (TypeError, ValueError):
                return 0
            return v if v in ROTATIONS else 0

        return cls(output_rotation=rot("output_rotation"), image_rotation=rot("image_rotation"),
                   mirror=bool(data.get("mirror", False)))

    def to_dict(self) -> dict:
        """Tik nuo numatytųjų besiskiriančios reikšmės (failas lieka trumpas ir aiškus)."""
        default = TemplateOptions()
        return {f.name: getattr(self, f.name) for f in fields(self)
                if getattr(self, f.name) != getattr(default, f.name)}

    def is_default(self) -> bool:
        return self == TemplateOptions()

    def labels(self) -> list:
        """Trumpi lietuviški aprašymai sąsajai (ženkliukams)."""
        out = []
        if self.output_rotation:
            out.append(f"Failas pasuktas {self.output_rotation}°")
        if self.image_rotation:
            out.append(f"Nuotrauka pasukta {self.image_rotation}°")
        if self.mirror:
            out.append("Veidrodinis")
        return out


DEFAULT_OPTIONS = TemplateOptions()

_cache: Dict[str, Tuple[float, Dict[str, TemplateOptions]]] = {}
_lock = threading.Lock()


def options_path(templates_dir: str) -> str:
    return os.path.join(templates_dir, OPTIONS_FILE)


def load_all(templates_dir: str) -> Dict[str, TemplateOptions]:
    """Visų šablonų taisyklės iš aplanko (su talpykla pagal failo pakeitimo laiką)."""
    path = options_path(os.path.normpath(templates_dir))
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    with _lock:
        cached = _cache.get(path)
        if cached and cached[0] == mtime:
            return dict(cached[1])
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        result = {str(k): TemplateOptions.from_dict(v) for k, v in raw.items() if isinstance(v, dict)}
    except Exception:
        result = {}
    with _lock:
        _cache[path] = (mtime, result)
    return dict(result)


def get_for_template(template_path: str) -> TemplateOptions:
    """Taisyklės konkrečiam šablono failui (ieškoma to paties aplanko nustatymų faile)."""
    name = os.path.splitext(os.path.basename(template_path))[0]
    return load_all(os.path.dirname(os.path.abspath(template_path))).get(name, DEFAULT_OPTIONS)


def save_for_template(templates_dir: str, name: str, options: TemplateOptions):
    """Įrašo vieno šablono taisykles (kitų šablonų taisyklės išlieka). Įrašoma per laikiną failą."""
    templates_dir = os.path.normpath(templates_dir)
    path = options_path(templates_dir)
    raw: Dict[str, dict] = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            raise ValueError(f"Nepavyko perskaityti {OPTIONS_FILE} – pataisykite arba ištrinkite šį failą.")
    if options.is_default():
        raw.pop(name, None)
    else:
        raw[name] = options.to_dict()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(raw.items())), f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    with _lock:
        _cache.pop(path, None)
