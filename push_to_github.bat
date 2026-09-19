@echo off
chcp 65001 >nul
title Siunciama i GitHub...
echo ================================================================
echo   Siunciami PrintReady v2.5.3 pakeitimai i GitHub...
echo ================================================================
cd /d "d:\Projects\PrintReady_Workspace"
git push -u origin main
git push origin v2.5.3
echo.
echo ================================================================
echo Jei git push pavyko, dabar galite atidaryti GitHub ir sukurti Release:
echo https://github.com/lkuprys/PrintReady/releases/new
echo.
echo Reikia prisegti sugeneruotus failus is darbalaukio:
echo 1. PrintReady.exe
echo 2. PrintReady_v2.5.3.zip
echo ================================================================
pause
