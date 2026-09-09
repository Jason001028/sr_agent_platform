@echo off
setlocal EnableExtensions

set "SRC=%~dp0"
set "APPROOT=%LOCALAPPDATA%\CloudReview\app"

echo CloudReview Web update package
echo Source: "%SRC%"
echo Target: "%APPROOT%"
echo.

if not exist "%SRC%cloud_review\webapp.py" (
  echo ERROR: cloud_review\webapp.py was not found.
  echo Run this BAT from the extracted update package folder.
  pause
  exit /b 1
)

if not exist "%APPROOT%" (
  echo Default target was not found:
  echo "%APPROOT%"
  echo.
  set /p APPROOT=Enter CloudReview app folder:
)

if not exist "%APPROOT%" (
  echo ERROR: target folder does not exist.
  pause
  exit /b 1
)

netstat -ano | findstr ":8765" | findstr "LISTENING" >nul
if not errorlevel 1 (
  echo.
  echo ERROR: CloudReview Web service is still using port 8765.
  echo Close the old Web BAT window first, then run this updater again.
  pause
  exit /b 1
)

echo Close CloudReview before continuing.
set /p CONFIRM=Type Y and press Enter to update:
if /I not "%CONFIRM%"=="Y" exit /b 0

robocopy "%SRC%cloud_review" "%APPROOT%\cloud_review" /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP
if errorlevel 8 goto fail

if exist "%SRC%docs" robocopy "%SRC%docs" "%APPROOT%\docs" /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP
if errorlevel 8 goto fail

copy /Y "%SRC%run_cloud_review.py" "%APPROOT%\run_cloud_review.py" >nul
if errorlevel 1 goto fail
copy /Y "%SRC%run_cloud_picker.py" "%APPROOT%\run_cloud_picker.py" >nul
if errorlevel 1 goto fail
copy /Y "%SRC%run_cloud_review_web.py" "%APPROOT%\run_cloud_review_web.py" >nul
if errorlevel 1 goto fail
copy /Y "%SRC%run_cloud_review_web.bat" "%APPROOT%\run_cloud_review_web.bat" >nul
if errorlevel 1 goto fail

echo.
echo Update completed.
echo Start Web version with: "%APPROOT%\run_cloud_review_web.bat"
pause
exit /b 0

:fail
echo.
echo Update failed. Close CloudReview and try again.
pause
exit /b 1
