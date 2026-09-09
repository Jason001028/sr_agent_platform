@echo off
setlocal
cd /d "%~dp0"
set "CLOUD_REVIEW_PYTHON=C:\Python310\python.exe"
if not exist "%CLOUD_REVIEW_PYTHON%" set "CLOUD_REVIEW_PYTHON=python"
"%CLOUD_REVIEW_PYTHON%" run_cloud_review_web.py
if errorlevel 1 pause
