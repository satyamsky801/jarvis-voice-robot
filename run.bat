@echo off
chcp 65001 >nul
title J.A.R.V.I.S. - Autonomous Voice Robot Assistant
color 0B
echo ======================================================================
echo           J . A . R . V . I . S .   V O I C E   R O B O T
echo ======================================================================
echo Initializing audio devices and voice engine...
echo.

cd /d "%~dp0"

if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe voice_robot.py
) else if exist C:\assist\jarvis\.venv\Scripts\python.exe (
    C:\assist\jarvis\.venv\Scripts\python.exe voice_robot.py
) else (
    echo Virtual environment not found. Running install.bat first...
    call install.bat
    .venv\Scripts\python.exe voice_robot.py
)

if %ERRORLEVEL% neq 0 (
    echo.
    echo J.A.R.V.I.S. exited with code %ERRORLEVEL%.
    pause
)
