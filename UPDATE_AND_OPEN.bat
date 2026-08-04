@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" scripts\run_dashboard.py --open
    goto :finished
)

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    py -3 scripts\run_dashboard.py --open
    goto :finished
)

where python >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    python scripts\run_dashboard.py --open
    goto :finished
)

echo Python was not found. Install Python 3, then run this launcher again.
pause
exit /b 1

:finished
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
    echo.
    echo Pricing Dashboard stopped with exit code %EXIT_CODE%.
    pause
)
exit /b %EXIT_CODE%
