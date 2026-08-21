@echo off
REM Fish Monitor - Stock Ticker Widget
REM Uses pythonw.exe to hide console window
REM Double-click this file to start

cd /d "%~dp0"

REM Check Python is available
where pythonw.exe >nul 2>nul
if %errorlevel% neq 0 (
    echo Python not found. Please install Python 3.8+ and add to PATH.
    pause
    exit /b 1
)

REM Start with pythonw (no console window)
start "" pythonw.exe fish_monitor.py
