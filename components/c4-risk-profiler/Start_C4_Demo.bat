@echo off
title C4 server (keep this window open)
cd /d "%~dp0"
echo Starting the C4 Risk Profiler on http://localhost:8000/dashboard/
echo Keep this window open while you present. Close it to stop the server.
echo.
:: open the browser after 4 seconds, when the server is ready
start "" cmd /c "timeout /t 4 /nobreak >nul & start http://localhost:8000/dashboard/"
python -m uvicorn c4.main:app --port 8000
pause
