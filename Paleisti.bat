@echo off
title MacbookAutoCrop
cd /d "%~dp0"
if exist "dist\MacbookAutoCrop.exe" (
    start "" "dist\MacbookAutoCrop.exe"
) else if exist "MacbookAutoCrop.exe" (
    start "" "MacbookAutoCrop.exe"
) else (
    py main_gui.py
)
