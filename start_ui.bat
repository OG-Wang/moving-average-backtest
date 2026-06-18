@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting A-share Index MA Backtest GUI...
echo Your browser will open http://127.0.0.1:5050  (close this window to stop)
".venv\Scripts\python.exe" src\app.py
pause
