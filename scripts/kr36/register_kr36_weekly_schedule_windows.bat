@echo off
setlocal EnableExtensions

rem register_kr36_weekly_schedule_windows.bat
rem
rem Schedules:
rem   1) Every Wednesday   run=13:30  mail=15:00  (Sat->Wed window)
rem   2) Every Saturday    run=10:00  mail=11:30  (Wed->Sat window)
rem
rem Task names:
rem   touzifenxi-kr36-wed-run  / touzifenxi-kr36-wed-mail
rem   touzifenxi-kr36-sat-run  / touzifenxi-kr36-sat-mail

set "RUN_SCRIPT=%~dp0run_kr36_daily_brief_no_email_windows.bat"
set "MAIL_SCRIPT=%~dp0send_kr36_daily_brief_email_t1_gate_windows.bat"

if not exist "%RUN_SCRIPT%"  echo [ERROR] Missing: "%RUN_SCRIPT%"  & endlocal & exit /b 1
if not exist "%MAIL_SCRIPT%" echo [ERROR] Missing: "%MAIL_SCRIPT%" & endlocal & exit /b 1

echo [INFO] kr36 weekly schedule registration
echo [INFO]   run  : %RUN_SCRIPT%
echo [INFO]   mail : %MAIL_SCRIPT%

rem --- 1) Weekly Wednesday ---
echo.
echo [INFO] Registering weekly WED tasks...
schtasks /create /tn "touzifenxi-kr36-wed-run" /sc WEEKLY /d WED /st 13:30 /tr "\"%RUN_SCRIPT%\"" /f
if errorlevel 1 echo [ERROR] wed-run failed. & endlocal & exit /b 1
schtasks /create /tn "touzifenxi-kr36-wed-mail" /sc WEEKLY /d WED /st 15:00 /tr "\"%MAIL_SCRIPT%\"" /f
if errorlevel 1 echo [ERROR] wed-mail failed. & endlocal & exit /b 1
echo [OK] WED tasks registered (run=13:30, mail=15:00).

rem --- 2) Weekly Saturday ---
echo.
echo [INFO] Registering weekly SAT tasks...
schtasks /create /tn "touzifenxi-kr36-sat-run" /sc WEEKLY /d SAT /st 10:00 /tr "\"%RUN_SCRIPT%\"" /f
if errorlevel 1 echo [ERROR] sat-run failed. & endlocal & exit /b 1
schtasks /create /tn "touzifenxi-kr36-sat-mail" /sc WEEKLY /d SAT /st 11:30 /tr "\"%MAIL_SCRIPT%\"" /f
if errorlevel 1 echo [ERROR] sat-mail failed. & endlocal & exit /b 1
echo [OK] SAT tasks registered (run=10:00, mail=11:30).

echo.
echo [OK] All 4 tasks registered successfully.
endlocal & exit /b 0
