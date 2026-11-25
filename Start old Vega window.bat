@echo off
cd /d "%~dp0"
echo Opening the OLD Vega window (only if Vega 2.0 is not running).
set PY=python
if exist python_path.txt set /p PY=<python_path.txt
"%PY%" "%~dp0vega_app.py"
pause
