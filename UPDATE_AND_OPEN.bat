@echo off
setlocal EnableExtensions
title Pricing Dashboard - Trade Builder

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_windows.ps1"
set "DASHBOARD_EXIT=%ERRORLEVEL%"
if not "%DASHBOARD_EXIT%"=="0" (
    echo.
    echo Pricing Dashboard failed. Review the error above.
    pause
    exit /b %DASHBOARD_EXIT%
)

exit /b 0
