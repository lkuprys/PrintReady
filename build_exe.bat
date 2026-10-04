@echo off
chcp 65001 >nul
title PrintReady PRO kompiliavimas
cd /d "%~dp0"
echo ================================================================
echo   Kompiliuojama PrintReady PRO (aplankas: dist\PrintReady)...
echo ================================================================
py build.py
if errorlevel 1 (
    echo.
    echo KLAIDA: kompiliavimas nepavyko!
    pause
    exit /b 1
)

echo.
echo ================================================================
echo   Formuojamas Release ZIP paketas...
echo ================================================================
py package_release.py
if errorlevel 1 (
    echo.
    echo KLAIDA: ZIP paketo sudaryti nepavyko!
    pause
    exit /b 1
)

echo.
echo ================================================================
echo   Baigta! Failai aplanke dist\
echo   Naujos versijos isleidziamos per GitHub: Actions - "Isleisti nauja versija"
echo ================================================================
pause
