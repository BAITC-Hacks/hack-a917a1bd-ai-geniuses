@echo off
setlocal
cd /d "%~dp0"
title OrgLens
if not exist ".venv\Scripts\python.exe" (
  echo Python environment is missing. Follow the setup steps in README.md.
  pause
  exit /b 1
)
if not exist "frontend\node_modules\vite\bin\vite.js" (
  echo Frontend dependencies are missing. Run: npm.cmd --prefix frontend ci
  pause
  exit /b 1
)
call npm.cmd --prefix frontend run build:portable
if errorlevel 1 (
  pause
  exit /b 1
)
echo.
echo Open OrgLens: http://127.0.0.1:8000/
echo Keep this window open. Press Ctrl+C to stop.
echo.
".venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
if errorlevel 1 pause
