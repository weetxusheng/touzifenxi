@echo off
setlocal EnableExtensions

rem Usage:
rem   register_c114_split_schedule_windows.bat [RUN_TIME] [MAIL_TIME] [RUN_TASK] [MAIL_TASK]
rem Example:
rem   register_c114_split_schedule_windows.bat 10:00 11:20

set "RUN_TIME=%~1"
if "%RUN_TIME%"=="" set "RUN_TIME=10:00"
set "MAIL_TIME=%~2"
if "%MAIL_TIME%"=="" set "MAIL_TIME=11:20"
set "RUN_TASK=%~3"
if "%RUN_TASK%"=="" set "RUN_TASK=touzifenxi-c114-daily-run"
set "MAIL_TASK=%~4"
if "%MAIL_TASK%"=="" set "MAIL_TASK=touzifenxi-c114-daily-mail"

set "RUN_SCRIPT=%~dp0run_c114_daily_brief_no_email_windows.bat"
set "MAIL_SCRIPT=%~dp0send_c114_daily_brief_email_t1_gate_windows.bat"

if not exist "%RUN_SCRIPT%" echo [ERROR] Missing: "%RUN_SCRIPT%" & exit /b 1
if not exist "%MAIL_SCRIPT%" echo [ERROR] Missing: "%MAIL_SCRIPT%" & exit /b 1

echo [INFO] Register split schedule...
echo        RUN  : %RUN_TIME% ^| "%RUN_SCRIPT%"
echo        MAIL : %MAIL_TIME% ^| "%MAIL_SCRIPT%"

schtasks /create /tn "%RUN_TASK%" /sc DAILY /st %RUN_TIME% /tr "\"%RUN_SCRIPT%\"" /f
if errorlevel 1 echo [ERROR] Failed creating run task. & exit /b 1

schtasks /create /tn "%MAIL_TASK%" /sc DAILY /st %MAIL_TIME% /tr "\"%MAIL_SCRIPT%\"" /f
if errorlevel 1 echo [ERROR] Failed creating mail task. & exit /b 1

echo [OK] Tasks created/updated.
echo schtasks /query /tn "%RUN_TASK%" /v /fo LIST
echo schtasks /query /tn "%MAIL_TASK%" /v /fo LIST
endlocal & exit /b 0
