@echo off
rem Double-click to run TC Inventory Planner locally (see run_local.py).
rem Installs Python first if this PC doesn't have it.
cd /d "%~dp0"
set "PATH=%LOCALAPPDATA%\Programs\Python\Launcher;%PATH%"
where py >nul 2>nul
if errorlevel 1 (
  echo Python not found - installing Python 3.13 with winget...
  winget install --id Python.Python.3.13 -e --silent --accept-package-agreements --accept-source-agreements
)
where py >nul 2>nul
if errorlevel 1 (
  echo.
  echo Python was installed. Close this window and double-click run-local.bat again.
  pause
  exit /b 1
)
py run_local.py %*
pause
