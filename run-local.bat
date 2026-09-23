@echo off
rem Double-click to run TC Inventory Planner locally (see run_local.py).
cd /d "%~dp0"
py run_local.py %*
pause
