@echo off
setlocal
cd /d "%~dp0"
set /p APP_VERSION=<VERSION
echo Starting ASOC PI Readiness build v%APP_VERSION%
echo Application folder: %CD%
if not exist ".venv\Scripts\python.exe" (
  py -m venv .venv || python -m venv .venv
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if not exist ".env" copy ".env.example" ".env" >nul
echo Open http://127.0.0.1:8000 and confirm the header shows v%APP_VERSION%.
".venv\Scripts\python.exe" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
endlocal
