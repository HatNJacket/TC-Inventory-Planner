@echo off
title TC Shipping Planner - Local Testing
cd /d "%~dp0"
if exist "backend\.venv\Scripts\python.exe" (
  "backend\.venv\Scripts\python.exe" run_dev.py
) else (
  py run_dev.py
)
if errorlevel 1 pause
