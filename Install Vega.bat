@echo off
cd /d "%~dp0"
echo Installing what Vega needs. This takes a few minutes...
python -m pip install --upgrade -r requirements-vega.txt
python -c "import sys; print(sys.executable)" > python_path.txt
echo.
echo Done. Now double-click "Start Vega.bat".
pause
