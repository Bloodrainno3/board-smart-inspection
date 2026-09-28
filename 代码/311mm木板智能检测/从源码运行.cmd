@echo off
chcp 65001 >nul
cd /d "%~dp0"
"%~dp0..\..\软件\venv\Scripts\python.exe" "%~dp0main.py"
pause
