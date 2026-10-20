@echo off
setlocal
cd /d "%~dp0"
title DDT Daily Recorder

if exist ".venv\Scripts\python.exe" goto install
py -3.12 --version >nul 2>&1
if errorlevel 1 goto missing_python
py -3.12 -m venv .venv
if errorlevel 1 goto failed

:install
echo Installing / checking dependencies. Internet access is needed on first use.
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto failed

:menu
echo.
echo DDT Daily - Windows local recorder
echo Log in manually and open the game before recording or running.
echo This package does NOT include a ready-made daily task workflow.
echo.
echo [1] Record steps - performs real clicks
echo [2] Run recorded steps
echo [3] Check recording
echo [4] Exit
choice /c 1234 /n /m "Choose 1-4: "
if errorlevel 4 goto done
if errorlevel 3 goto check
if errorlevel 2 goto run
goto record

:record
".venv\Scripts\python.exe" -m ddt_daily record
goto result

:run
".venv\Scripts\python.exe" -m ddt_daily run
goto result

:check
".venv\Scripts\python.exe" -m ddt_daily check
goto result

:result
echo.
pause
goto menu

:missing_python
echo Python 3.12 with the Windows Python launcher is required.
echo Install it from https://www.python.org/downloads/windows/
echo Then double-click start.bat again.
pause
exit /b 1

:failed
echo Setup failed. Read the error above, then try again after fixing it.
pause
exit /b 1

:done
exit /b 0
