@echo off
chcp 65001 >nul
title Building PrintReady PRO Standalone Executable and Release Package
echo ================================================================
echo   Building PrintReady PRO Standalone Executable with PyInstaller...
echo ================================================================

py -m PyInstaller --noconfirm --onefile --windowed --name "PrintReady" ^
  --icon "assets/app_icon.ico" --splash "assets/splash_bg.png" ^
  --add-data "assets;assets" ^
  --add-data "us_web_coated_swop_v2.icc;." ^
  --collect-all qfluentwidgets --collect-all PySide6 --collect-all PIL ^
  --collect-all tifffile --collect-all imagecodecs main.py

if errorlevel 1 (
    echo.
    echo ❌ KLAIDA: PyInstaller kompiliavimas nepavyko!
    pause
    exit /b 1
)

echo.
echo ================================================================
echo   Formuojamas GitHub Release ZIP paketas...
echo ================================================================
py package_release.py

echo.
echo ================================================================
echo ✅ Visi darbai baigti! Paruošti failai aplanke: dist/
echo ================================================================
pause
