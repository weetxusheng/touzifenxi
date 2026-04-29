@echo off
setlocal EnableExtensions

rem One-time tasks for tomorrow Wed Apr 29 2026
rem   run  : 08:00  run_kr36_daily_brief_no_email_windows.bat
rem   mail : 09:00  send_kr36_daily_brief_email_t1_gate_windows.bat

set "RUN_SCRIPT=%~dp0run_kr36_daily_brief_no_email_windows.bat"
set "MAIL_SCRIPT=%~dp0send_kr36_daily_brief_email_t1_gate_windows.bat"

if not exist "%RUN_SCRIPT%"  echo [ERROR] Missing: "%RUN_SCRIPT%"  & endlocal & exit /b 1
if not exist "%MAIL_SCRIPT%" echo [ERROR] Missing: "%MAIL_SCRIPT%" & endlocal & exit /b 1

schtasks /create /tn "touzifenxi-kr36-wed-morning-run"  /sc ONCE /sd 2026/04/29 /st 08:00 /tr "\"%RUN_SCRIPT%\""  /f
if errorlevel 1 echo [ERROR] run task failed.  & endlocal & exit /b 1

schtasks /create /tn "touzifenxi-kr36-wed-morning-mail" /sc ONCE /sd 2026/04/29 /st 09:00 /tr "\"%MAIL_SCRIPT%\"" /f
if errorlevel 1 echo [ERROR] mail task failed. & endlocal & exit /b 1

echo [OK] Tasks registered: run=08:00 mail=09:00 on 2026/04/29
endlocal & exit /b 0
