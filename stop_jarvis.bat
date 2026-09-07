@echo off
chcp 65001 >nul
title Stop J.A.R.V.I.S. Voice Robot
color 0C
echo ======================================================================
echo           S T O P P I N G   J . A . R . V . I . S .
echo ======================================================================
echo Terminating running J.A.R.V.I.S. voice robot processes...

powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name = 'python.exe' or name = 'pythonw.exe'\" | Where-Object { $_.CommandLine -match 'voice_robot.py' } | ForEach-Object { Write-Host 'Stopped PID:' $_.ProcessId; Stop-Process -Id $_.ProcessId -Force }"

echo.
echo J.A.R.V.I.S. voice robot has been stopped.
