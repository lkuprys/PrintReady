@echo off
title PrintReady PRO
cd /d "%~dp0"
if exist "dist\PrintReady\PrintReady.exe" (
    start "" "dist\PrintReady\PrintReady.exe"
) else if exist "PrintReady.exe" (
    start "" "PrintReady.exe"
) else (
    py main.py
)
