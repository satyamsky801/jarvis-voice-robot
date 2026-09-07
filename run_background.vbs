Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\assist\jarvis"

' Terminate any existing voice_robot instances first
WshShell.Run "powershell -NoProfile -Command ""Get-CimInstance Win32_Process -Filter 'name = ''python.exe'' or name = ''pythonw.exe''' | Where-Object { $_.CommandLine -match 'voice_robot.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }""", 0, True

' Launch JARVIS in silent background mode using pythonw.exe
If CreateObject("Scripting.FileSystemObject").FileExists("C:\assist\jarvis\.venv\Scripts\pythonw.exe") Then
    WshShell.Run "C:\assist\jarvis\.venv\Scripts\pythonw.exe C:\assist\jarvis\voice_robot.py", 0, False
Else
    WshShell.Run "pythonw C:\assist\jarvis\voice_robot.py", 0, False
End If
