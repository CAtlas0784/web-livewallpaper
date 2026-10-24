@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv 2>nul
  if errorlevel 1 python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install -r requirements.txt
python -m playwright install chromium
python -m uvicorn api:app --host 127.0.0.1 --port 8000
pause
endlocal
