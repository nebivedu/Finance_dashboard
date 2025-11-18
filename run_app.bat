@echo off
echo ========================================
echo Finance Dashboard - Starting Server
echo ========================================
echo.
echo The web interface will open at:
echo http://127.0.0.1:5000
echo.
echo Press Ctrl+C to stop the server
echo ========================================
echo.

cd /d "%~dp0"
python app.py

pause
