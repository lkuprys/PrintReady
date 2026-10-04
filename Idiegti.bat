@echo off
chcp 65001 >nul
title PrintReady PRO Diegimas
cd /d "%~dp0"
set "DEST=C:\Podbase\PrintReady"

if not exist "PrintReady.exe" (
    echo KLAIDA: paleiskite Idiegti.bat is isarchyvuoto PrintReady aplanko.
    pause
    exit /b 1
)
if not exist "_internal" (
    echo KLAIDA: siame aplanke nera _internal aplanko.
    pause
    exit /b 1
)

taskkill /F /IM PrintReady.exe >nul 2>&1
timeout /t 2 /nobreak >nul

echo Kopijuojama i %DEST%...
if not exist "%DEST%" mkdir "%DEST%"
:: _internal visada svari kopija
robocopy "%CD%\_internal" "%DEST%\_internal" /MIR /R:3 /W:1 /NP /NJH /NJS >nul
if errorlevel 8 (
    echo KLAIDA: nepavyko nukopijuoti _internal.
    pause
    exit /b 1
)
:: Nustatymai ir sablonai niekada neperrasomi
robocopy "%CD%" "%DEST%" /E /R:3 /W:1 /NP /NJH /NJS /XD _internal Sablonai /XF config.json Idiegti.bat >nul
if errorlevel 8 (
    echo KLAIDA: nepavyko nukopijuoti programos failu.
    pause
    exit /b 1
)
if not exist "%DEST%\Sablonai" mkdir "%DEST%\Sablonai"
if exist "Sablonai\*.png" copy /Y "Sablonai\*.png" "%DEST%\Sablonai\" >nul

echo Kuriama nuoroda ant Darbastalio...
powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut([System.IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), 'PrintReady PRO.lnk')); $s.TargetPath = '%DEST%\PrintReady.exe'; $s.WorkingDirectory = '%DEST%'; $s.Description = 'Podbase PrintReady PRO'; $s.Save()"

echo [SĖKMĖ] Įdiegta į %DEST% ir sukurta nuoroda ant Darbastalio!
start "" "%DEST%\PrintReady.exe"
