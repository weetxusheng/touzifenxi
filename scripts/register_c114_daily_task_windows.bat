@echo off
setlocal EnableExtensions

rem Usage:
rem   register_c114_daily_task_windows.bat [HH:mm] [TaskName] [ScriptPath]
rem Example:
rem   register_c114_daily_task_windows.bat
rem   register_c114_daily_task_windows.bat 09:15
rem   register_c114_daily_task_windows.bat 09:15 "touzifenxi-c114-daily-brief"

set "DEFAULT_TIME=08:30"
set "DEFAULT_TASK_NAME=touzifenxi-c114-daily-brief"
rem Default: run_c114_daily_brief_windows.bat next to this script (any drive/path).
for %%I in ("%~dp0run_c114_daily_brief_windows.bat") do set "DEFAULT_SCRIPT_PATH=%%~fI"

set "RUN_TIME=%~1"
if "%RUN_TIME%"=="" set "RUN_TIME=%DEFAULT_TIME%"

set "TASK_NAME=%~2"
if "%TASK_NAME%"=="" set "TASK_NAME=%DEFAULT_TASK_NAME%"

set "SCRIPT_PATH=%~3"
if "%SCRIPT_PATH%"=="" set "SCRIPT_PATH=%DEFAULT_SCRIPT_PATH%"

if not exist "%SCRIPT_PATH%" (
  echo [ERROR] Script not found: "%SCRIPT_PATH%"
  exit /b 1
)

echo [INFO] Registering scheduled task...
echo        Task Name : %TASK_NAME%
echo        Time      : %RUN_TIME%
echo        Script    : %SCRIPT_PATH%

schtasks /create ^
  /tn "%TASK_NAME%" ^
  /sc DAILY ^
  /st %RUN_TIME% ^
  /tr "\"%SCRIPT_PATH%\"" ^
  /f

if errorlevel 1 (
  echo [ERROR] Failed to create scheduled task.
  exit /b 1
)

echo [OK] Scheduled task created/updated successfully.
echo [TIP] Check it with:
echo       schtasks /query /tn "%TASK_NAME%" /v /fo LIST

endlocal & exit /b 0
