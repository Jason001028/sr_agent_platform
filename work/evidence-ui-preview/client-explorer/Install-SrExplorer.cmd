@echo off
setlocal
title SR Explorer Setup
echo Install the directory opener for this Windows user.
echo Company default: /DiskArray to W:\. No drive selection is needed.
echo Chrome prompt exemption: http://10.10.81.135:80 for sr-explorer only.
echo Keep both installer files together in the extracted folder.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-SrExplorer.ps1"
if errorlevel 1 (
  echo.
  echo Installation failed. Read the message above before closing this window.
  pause
  exit /b 1
) else (
  echo.
  echo Open chrome://policy, reload policies and verify AutoLaunchProtocolsFromOrigins.
  echo Then return to http://10.10.81.135/viewer and click the folder icon.
  timeout /t 3 /nobreak >nul
)
