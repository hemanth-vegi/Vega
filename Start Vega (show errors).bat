@echo off
cd /d "%~dp0"
echo Starting Vega with a visible log. If she closes, the error is shown below.
set PY=python
if exist python_path.txt set /p PY=<python_path.txt
"%PY%" "%~dp0vega_webview.py"
pause
