@echo off
chcp 65001 >nul
echo ============================================
echo   TTAG Calibration - Build EXE
echo ============================================
echo.

REM 检查 pyinstaller
pip show pyinstaller >nul 2>&1
if %errorlevel% neq 0 (
    echo 正在安装 pyinstaller...
    pip install pyinstaller
    if %errorlevel% neq 0 (
        echo 安装 pyinstaller 失败!
        pause
        exit /b 1
    )
)

echo 正在打包...
pyinstaller --onefile --windowed ^
    --name TTAG_Cal ^
    --add-data "water_bath_control.py;." ^
    --add-data "ttag_monitor.py;." ^
    --add-data "ttag_fitting.py;." ^
    --hidden-import serial ^
    --hidden-import serial.tools.list_ports ^
    --hidden-import openpyxl ^
    --hidden-import numpy ^
    --hidden-import scipy.optimize ^
    --hidden-import scipy.linalg ^
    --clean ^
    ttag_cal_app.py

if %errorlevel% equ 0 (
    echo.
    echo ============================================
    echo   打包成功!
    echo   输出: dist\TTAG_Cal.exe
    echo ============================================
) else (
    echo.
    echo 打包失败，请检查错误信息。
)

pause
