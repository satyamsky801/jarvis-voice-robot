@echo off
title JARVIS Desktop Mascot Companion
cd /d "C:\assist\jarvis"
call .venv\Scripts\activate.bat
python voice_robot.py
pause
