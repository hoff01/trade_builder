@echo off
setlocal EnableExtensions
title Pricing Dashboard - Install Bloomberg and Polars

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_windows.ps1" -InstallOnly
set "INSTALL_EXIT=%ERRORLEVEL%"
if not "%INSTALL_EXIT%"=="0" (
    echo.
    echo Trade Builder setup failed. Review the error above.
    pause
    exit /b %INSTALL_EXIT%
)

echo.
echo Trade Builder setup completed successfully.
pause
exit /b 0
