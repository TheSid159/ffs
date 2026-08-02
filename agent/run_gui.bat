@echo off
cd /d %~dp0
REM "py" is the Windows Python Launcher — python.org's installer always puts
REM it on PATH (in C:\Windows) regardless of whether "Add python.exe to PATH"
REM was checked, so this finds Python even when the plain "python" command
REM can't (a common issue right after installing/reinstalling, until you log
REM off or reboot).
py gui.py
if errorlevel 1 (
    echo.
    echo ============================================================
    echo Could not start the app. If you just installed or reinstalled
    echo Python, try restarting your computer, then double-click this
    echo file again.
    echo ============================================================
    pause
)
