import os
import sys
import zipfile
import hashlib
from updater import APP_VERSION

def compute_sha256(file_path: str) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def package_release():
    base_dir = os.path.abspath(os.path.dirname(__file__))
    dist_dir = os.path.join(base_dir, "dist")
    exe_path = os.path.join(dist_dir, "PrintReady.exe")

    if not os.path.exists(exe_path):
        print(f"❌ KLAIDA: Nerastas sukompiliuotas failas: {exe_path}")
        print("💡 Pirmiausia paleiskite PyInstaller kompiliavimą (pvz., paleiskite build_exe.bat).")
        sys.exit(1)

    zip_name = f"PrintReady_v{APP_VERSION}.zip"
    zip_path = os.path.join(dist_dir, zip_name)

    print("================================================================")
    print(f"📦 Pakuojamas PrintReady PRO v{APP_VERSION} GitHub Release paketas...")
    print("================================================================")

    # Sukuriame ZIP archyvą
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. Pagrindinis .exe
        print(f" -> Pridedamas {os.path.basename(exe_path)}...")
        zf.write(exe_path, "PrintReady.exe")

        # 2. ICC Profilis
        icc_path = os.path.join(base_dir, "us_web_coated_swop_v2.icc")
        if os.path.exists(icc_path):
            print(f" -> Pridedamas {os.path.basename(icc_path)}...")
            zf.write(icc_path, "us_web_coated_swop_v2.icc")

        # 3. Šablonų aplankas (Sablonai)
        tmpl_dir = os.path.join(base_dir, "Sablonai")
        if os.path.exists(tmpl_dir):
            for root, dirs, files in os.walk(tmpl_dir):
                for file in files:
                    full_p = os.path.join(root, file)
                    rel_p = os.path.relpath(full_p, base_dir)
                    print(f" -> Pridedamas {rel_p}...")
                    zf.write(full_p, rel_p)

        # 4. Dokumentacija ir licenzija
        for doc in ("README.md", "LICENSE"):
            doc_p = os.path.join(base_dir, doc)
            if os.path.exists(doc_p):
                print(f" -> Pridedamas {doc}...")
                zf.write(doc_p, doc)

    zip_size_mb = os.path.getsize(zip_path) / (1024 * 1024)
    exe_size_mb = os.path.getsize(exe_path) / (1024 * 1024)
    zip_sha = compute_sha256(zip_path)

    print("\n================================================================")
    print("✅ RELEASE PAKETAS SĖKMINGAI SUFORMUOTAS!")
    print(f"📁 ZIP Archyvas: {zip_path} ({zip_size_mb:.2f} MB)")
    print(f"📁 Standalone EXE: {exe_path} ({exe_size_mb:.2f} MB)")
    print(f"🔒 SHA256 (ZIP): {zip_sha}")
    print("================================================================")
    print("🚀 Galite įkelti šiuos failus į GitHub Releases:")
    print(f"   1. {zip_name} (Rekomenduojama)")
    print("   2. PrintReady.exe")
    print("================================================================")

if __name__ == "__main__":
    package_release()
