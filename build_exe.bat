@echo off
chcp 65001 >nul
title Building PrintReady PRO Standalone Executable and Release Package
echo ================================================================
echo   Building PrintReady PRO Standalone Executable (Fast & Slim)...
echo ================================================================

py -m PyInstaller --noconfirm --onefile --windowed --name "PrintReady" ^
  --icon "assets/app_icon.ico" --splash "assets/splash_bg.png" ^
  --add-data "assets;assets" ^
  --add-data "us_web_coated_swop_v2.icc;." ^
  --collect-all qfluentwidgets ^
  --collect-submodules tifffile ^
  --collect-submodules imagecodecs ^
  --exclude-module scipy ^
  --exclude-module matplotlib ^
  --exclude-module tkinter ^
  --exclude-module PySide6.Qt3DAnimation ^
  --exclude-module PySide6.Qt3DCore ^
  --exclude-module PySide6.Qt3DExtras ^
  --exclude-module PySide6.Qt3DInput ^
  --exclude-module PySide6.Qt3DLogic ^
  --exclude-module PySide6.Qt3DRender ^
  --exclude-module PySide6.QtBluetooth ^
  --exclude-module PySide6.QtQuick ^
  --exclude-module PySide6.QtQml ^
  --exclude-module PySide6.QtMultimedia ^
  --exclude-module PySide6.QtMultimediaWidgets ^
  --exclude-module PySide6.QtSensors ^
  --exclude-module PySide6.QtSpatialAudio ^
  --exclude-module PySide6.QtSql ^
  --exclude-module PySide6.QtTest ^
  --exclude-module PySide6.QtXml ^
  --exclude-module PySide6.QtSerialBus ^
  --exclude-module PySide6.QtSerialPort ^
  --exclude-module PySide6.QtDesigner ^
  --exclude-module PySide6.QtHelp ^
  --exclude-module PySide6.QtLocation ^
  --exclude-module PySide6.QtPositioning ^
  --exclude-module PySide6.QtScxml ^
  --exclude-module PySide6.QtRemoteObjects main.py

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
