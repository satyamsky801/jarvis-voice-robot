@echo off
chcp 65001 >nul
title J.A.R.V.I.S. Setup & Installation
color 0B
echo ======================================================================
echo           J . A . R . V . I . S .   S E T U P   W I Z A R D
echo ======================================================================
echo.

:: Check Python
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python is not installed or not in system PATH.
    echo Please install Python 3.10+ from python.org and check "Add to PATH".
    pause
    exit /b 1
)

echo [1/3] Creating virtual environment (.venv)...
if not exist .venv (
    python -m venv .venv
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
) else (
    echo (.venv already exists)
)

echo.
echo [2/3] Upgrading pip...
.venv\Scripts\python.exe -m pip install --upgrade pip >nul 2>&1

echo.
echo [3/3] Installing dependencies from requirements.txt...
.venv\Scripts\pip.exe install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)

echo.
echo ======================================================================
echo   SUCCESS! Installation Complete.
echo   You can now launch J.A.R.V.I.S. by double-clicking 'run.bat'.
echo ======================================================================
echo.
pause
