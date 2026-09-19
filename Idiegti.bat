@echo off
chcp 65001 >nul
title PrintReady PRO Diegimas
set "DEST=C:\Podbase\PrintReady"

echo Kuriama %DEST%...
if not exist "%DEST%" mkdir "%DEST%"
if not exist "%DEST%\Sablonai" mkdir "%DEST%\Sablonai"

echo Kopijuojamas failas...
copy /Y "PrintReady.exe" "%DEST%\" >nul
if exist "Sablonai\*.png" copy /Y "Sablonai\*.png" "%DEST%\Sablonai\" >nul

echo Kuriama nuoroda ant Darbastalio...
powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut([System.IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), 'PrintReady PRO.lnk')); $s.TargetPath = '%DEST%\PrintReady.exe'; $s.WorkingDirectory = '%DEST%'; $s.Description = 'Podbase PrintReady PRO'; $s.Save()"

echo [SĖKMĖ] Įdiegta į %DEST% ir sukurta nuoroda ant Darbastalio!
start "" "%DEST%\PrintReady.exe"
