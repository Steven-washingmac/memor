@echo off
chcp 65001 >nul
cd /d "%~dp0"
python ttag_dual_verify.py
pause
