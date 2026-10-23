@echo off
rem Starts the C3 demo (policy check + C4 stub + mock gateway + web page) with the project's own Python.
rem Usage:  start_demo.bat                     uses .\models
rem         start_demo.bat var\synthetic_v3\models     uses another model folder
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo The virtual environment .venv is missing. Create it with:
  echo   python -m venv .venv ^&^& .venv\Scripts\python -m pip install -e ".[train,dev]"
  pause
  exit /b 1
)
if "%~1"=="" (
  ".venv\Scripts\python.exe" -m c3.demo.launcher
) else (
  ".venv\Scripts\python.exe" -m c3.demo.launcher --models "%~1"
)
pause
