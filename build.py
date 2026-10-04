"""
PrintReady PRO kompiliavimas su PyInstaller.

  python build.py             -> dist/PrintReady/PrintReady.exe + _internal (atnaujinimams)
  python build.py --onefile   -> dist_tiltas/PrintReady.exe (vienas failas senoms versijoms atnaujinti)
"""
import os
import sys
import subprocess

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

EXCLUDED = [
    "scipy", "matplotlib", "tkinter",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras", "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic", "PySide6.Qt3DRender", "PySide6.QtBluetooth", "PySide6.QtQuick",
    "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtSensors",
    "PySide6.QtSpatialAudio", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtSerialBus",
    "PySide6.QtSerialPort", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtLocation",
    "PySide6.QtPositioning", "PySide6.QtScxml", "PySide6.QtRemoteObjects",
]


def main() -> int:
    base = os.path.abspath(os.path.dirname(__file__))
    os.chdir(base)
    onefile = "--onefile" in sys.argv[1:]
    sep = os.pathsep

    args = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
        "--onefile" if onefile else "--onedir",
        "--name", "PrintReady",
        "--icon", "assets/app_icon.ico",
        "--splash", "assets/splash_bg.png",
        "--add-data", f"assets{sep}assets",
        "--add-data", f"us_web_coated_swop_v2.icc{sep}.",
        "--collect-all", "qfluentwidgets",
        "--collect-submodules", "tifffile",
        "--collect-submodules", "imagecodecs",
    ]
    for m in EXCLUDED:
        args += ["--exclude-module", m]
    if onefile:
        args += ["--distpath", "dist_tiltas", "--workpath", "build_tiltas"]
    args.append("main.py")

    print(f"[+] Kompiliuojama ({'vienas failas' if onefile else 'aplankas'})...")
    code = subprocess.call(args)
    if code != 0:
        print(f"[-] KLAIDA: PyInstaller baigėsi su kodu {code}")
        return code

    exe = os.path.join(base, "dist_tiltas" if onefile else os.path.join("dist", "PrintReady"), "PrintReady.exe")
    if not os.path.isfile(exe):
        print(f"[-] KLAIDA: nerastas {exe}")
        return 1
    print(f"[OK] Sukurta: {exe}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
