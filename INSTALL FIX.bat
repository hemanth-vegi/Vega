@echo off
cd /d "%~dp0"
set PY=python
if exist "..\python_path.txt" set /p PY=<"..\python_path.txt"
"%PY%" install_fix.py
echo.
pause
