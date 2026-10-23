@echo off
rem Runs the three checks on a model folder, with the project's own Python.
rem Usage:  run_checks.bat                      checks .\models
rem         run_checks.bat var\synthetic_v3     checks a run folder that contains models\
cd /d "%~dp0"
if not "%~1"=="" set C3_RUN_DIR=%~1
echo.
echo === 1. Unit and integration tests ===
".venv\Scripts\python.exe" -m pytest -q
echo.
echo === 2. 20 hand-written cases on the models ===
".venv\Scripts\python.exe" tools\smoke_real_models.py
echo.
echo === 3. Data page ===
".venv\Scripts\python.exe" -m c3.training.public.datacard
pause
