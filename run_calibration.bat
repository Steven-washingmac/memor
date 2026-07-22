@echo off
title TTAG Calibration
cd /d "C:\Users\王应浩\OneDrive\桌面"

echo ===================================
echo   TTAG Auto Calibration
echo ===================================
echo Server mode: listening on 0.0.0.0:20226
echo Base station should connect to 192.168.3.187:20226
echo.

set /p DEVICE="Device ID (e.g. 230030): "
set /p START_T="Start temp [5]: "
set /p END_T="End temp [50]: "
set /p STEP="Step [0.2]: "

if "%START_T%"=="" set START_T=5
if "%END_T%"=="" set END_T=50
if "%STEP%"=="" set STEP=0.2

echo.
echo Device=%DEVICE%  %START_T% -^> %END_T% C  step=%STEP%
echo.
pause

python -u ttag_calibration.py --device %DEVICE% --start %START_T% --end %END_T% --step %STEP%

echo.
echo Done. Press any key to close...
pause >nul
