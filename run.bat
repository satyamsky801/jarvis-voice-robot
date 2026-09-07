@echo off
chcp 65001 >nul
title J.A.R.V.I.S. - Autonomous Voice Robot Assistant
color 0B
echo ======================================================================
echo           J . A . R . V . I . S .   V O I C E   R O B O T
echo ======================================================================
echo Checking running instances and initializing audio engine...
echo.

REM Automatically close any duplicate or orphaned voice_robot instances
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name = 'python.exe'\" | Where-Object { $_.CommandLine -match 'voice_robot.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1

cd /d C:\assist\jarvis
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe voice_robot.py
) else (
    echo Error: Virtual environment python not found at C:\assist\jarvis\.venv
    echo Running with system Python...
    python voice_robot.py
)

if %ERRORLEVEL% neq 0 (
    echo.
    echo J.A.R.V.I.S. exited with code %ERRORLEVEL%.
    pause
)
