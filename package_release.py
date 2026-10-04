"""
Sudaro GitHub Release ZIP: dist/PrintReady_vX.Y.Z.zip

ZIP struktūra (būtina atnaujintuvui):
  PrintReady/PrintReady.exe
  PrintReady/_internal/...
  PrintReady/Sablonai/README.md
  PrintReady/README.md, LICENSE, Idiegti.bat
"""
import os
import sys
import zipfile
import hashlib

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = "PrintReady"
EXE = "PrintReady.exe"
# Vietiniai failai niekada nededami į paketą
NEVER_PACK = {"config.json"}


def read_app_version(base_dir: str) -> str:
    import re
    with open(os.path.join(base_dir, "updater.py"), encoding="utf-8") as f:
        m = re.search(r'^APP_VERSION\s*=\s*"(\d+\.\d+\.\d+)"', f.read(), re.M)
    if not m:
        raise SystemExit("[-] KLAIDA: updater.py nerasta APP_VERSION")
    return m.group(1)


def compute_sha256(file_path: str) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_package(zip_path: str) -> None:
    """Patikrina ZIP struktūrą taip pat, kaip ją tikrins programa."""
    with zipfile.ZipFile(zip_path) as zf:
        bad = zf.testzip()
        if bad:
            raise SystemExit(f"[-] KLAIDA: sugadintas failas ZIP'e: {bad}")
        names = set(zf.namelist())
    required = [
        f"{ROOT}/{EXE}",
        f"{ROOT}/_internal/us_web_coated_swop_v2.icc",
        f"{ROOT}/_internal/assets/app_icon.ico",
    ]
    for r in required:
        if r not in names:
            raise SystemExit(f"[-] KLAIDA: ZIP'e trūksta {r}")
    for n in names:
        if not n.startswith(ROOT + "/"):
            raise SystemExit(f"[-] KLAIDA: failas ne {ROOT}/ aplanke: {n}")
        if os.path.basename(n) in NEVER_PACK:
            raise SystemExit(f"[-] KLAIDA: vietinis failas pateko į paketą: {n}")
    print("[OK] ZIP struktūra patikrinta")


def package_release() -> str:
    base_dir = os.path.abspath(os.path.dirname(__file__))
    version = read_app_version(base_dir)
    onedir = os.path.join(base_dir, "dist", ROOT)
    if not os.path.isfile(os.path.join(onedir, EXE)) or not os.path.isdir(os.path.join(onedir, "_internal")):
        raise SystemExit("[-] KLAIDA: nerastas dist/PrintReady/PrintReady.exe su _internal. Pirma paleiskite build.py")

    zip_path = os.path.join(base_dir, "dist", f"PrintReady_v{version}.zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)
    print(f"[+] Pakuojama PrintReady v{version}...")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(onedir):
            for file in files:
                if file in NEVER_PACK:
                    continue
                full_p = os.path.join(root, file)
                rel = os.path.relpath(full_p, onedir).replace(os.sep, "/")
                zf.write(full_p, f"{ROOT}/{rel}")
        tmpl_readme = os.path.join(base_dir, "Sablonai", "README.md")
        if os.path.exists(tmpl_readme):
            zf.write(tmpl_readme, f"{ROOT}/Sablonai/README.md")
        for doc in ("README.md", "LICENSE", "Idiegti.bat"):
            p = os.path.join(base_dir, doc)
            if os.path.exists(p):
                zf.write(p, f"{ROOT}/{doc}")

    verify_package(zip_path)
    size_mb = os.path.getsize(zip_path) / (1024 * 1024)
    print(f"[OK] {zip_path} ({size_mb:.1f} MB)")
    print(f"SHA256: {compute_sha256(zip_path)}")
    return zip_path


if __name__ == "__main__":
    package_release()
