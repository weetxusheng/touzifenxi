@echo off
setlocal EnableExtensions

rem Register two daily tasks: send 36Kr brief by report date (loads .env).
rem Default times: 10:00 and 11:20. Usage:
rem   register_kr36_brief_email_split_schedule_windows.bat [TIME1] [TIME2] [TASK1_NAME] [TASK2_NAME]

set "TIME1=%~1"
if "%TIME1%"=="" set "TIME1=10:00"
set "TIME2=%~2"
if "%TIME2%"=="" set "TIME2=11:20"
set "TASK1=%~3"
if "%TASK1%"=="" set "TASK1=touzifenxi-kr36-brief-email-1000"
set "TASK2=%~4"
if "%TASK2%"=="" set "TASK2=touzifenxi-kr36-brief-email-1120"

set "MAIL_SCRIPT=%~dp0send_kr36_brief_email_by_report_date_windows.bat"

if not exist "%MAIL_SCRIPT%" echo [ERROR] Missing: "%MAIL_SCRIPT%" & exit /b 1

echo [INFO] Register two daily 36Kr mail tasks (send_kr36_brief_email_by_report_date_windows)...
echo        %TIME1% ^| "%TASK1%" ^| "%MAIL_SCRIPT%"
echo        %TIME2% ^| "%TASK2%" ^| "%MAIL_SCRIPT%"

schtasks /create /tn "%TASK1%" /sc DAILY /st %TIME1% /tr "\"%MAIL_SCRIPT%\"" /f
if errorlevel 1 echo [ERROR] Failed creating task %TASK1%. & exit /b 1

schtasks /create /tn "%TASK2%" /sc DAILY /st %TIME2% /tr "\"%MAIL_SCRIPT%\"" /f
if errorlevel 1 echo [ERROR] Failed creating task %TASK2%. & exit /b 1

echo [OK] Tasks created/updated.
echo schtasks /query /tn "%TASK1%" /v /fo LIST
echo schtasks /query /tn "%TASK2%" /v /fo LIST
endlocal & exit /b 0
