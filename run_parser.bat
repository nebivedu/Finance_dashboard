@echo off
echo ========================================
echo OTP Bank PDF Parser
echo ========================================
echo.
echo This will parse all PDF files in the 'pdf' folder
echo and create/update the finance database.
echo.
echo Press any key to continue...
pause > nul

cd /d "%~dp0"
python otp_parser.py

echo.
echo ========================================
echo Done!
echo ========================================
echo.
echo Press any key to close this window...
pause > nul
