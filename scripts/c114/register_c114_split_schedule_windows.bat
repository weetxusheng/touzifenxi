@echo off
setlocal EnableExtensions

rem Usage:
rem   register_c114_split_schedule_windows.bat [RUN_TIME] [MAIL_TIME] [RUN_TASK] [MAIL_TASK]
rem Example:
rem   register_c114_split_schedule_windows.bat 10:00 11:20
rem
rem Advanced (optional env vars before call):
rem   set C114_RUN_WEEKDAY_TIME=10:00
rem   set C114_RUN_SAT_TIME=10:30
rem   set C114_RUN_SUN_TIME=
rem   set C114_MAIL_WEEKDAY_TIME=11:20
rem   set C114_MAIL_SAT_TIME=11:40
rem   set C114_MAIL_SUN_TIME=
rem
rem Optional run window (daily interval run):
rem   set C114_RUN_WINDOW_START=09:30
rem   set C114_RUN_WINDOW_END=11:30
rem   set C114_RUN_WINDOW_EVERY_MIN=15

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

set "HAS_ADVANCED="
if defined C114_RUN_WEEKDAY_TIME set "HAS_ADVANCED=1"
if defined C114_RUN_SAT_TIME set "HAS_ADVANCED=1"
if defined C114_RUN_SUN_TIME set "HAS_ADVANCED=1"
if defined C114_MAIL_WEEKDAY_TIME set "HAS_ADVANCED=1"
if defined C114_MAIL_SAT_TIME set "HAS_ADVANCED=1"
if defined C114_MAIL_SUN_TIME set "HAS_ADVANCED=1"

if not defined HAS_ADVANCED (
  echo [INFO] Register split schedule ^(daily default mode^)...
  echo        RUN  : %RUN_TIME% ^| "%RUN_SCRIPT%"
  echo        MAIL : %MAIL_TIME% ^| "%MAIL_SCRIPT%"

  schtasks /create /tn "%RUN_TASK%" /sc DAILY /st %RUN_TIME% /tr "\"%RUN_SCRIPT%\"" /f
  if errorlevel 1 (
    echo [ERROR] Failed creating run task.
    exit /b 1
  )

  schtasks /create /tn "%MAIL_TASK%" /sc DAILY /st %MAIL_TIME% /tr "\"%MAIL_SCRIPT%\"" /f
  if errorlevel 1 (
    echo [ERROR] Failed creating mail task.
    exit /b 1
  )
) else (
  if not defined C114_RUN_WEEKDAY_TIME set "C114_RUN_WEEKDAY_TIME=%RUN_TIME%"
  if not defined C114_RUN_SAT_TIME set "C114_RUN_SAT_TIME=%RUN_TIME%"
  if not defined C114_MAIL_WEEKDAY_TIME set "C114_MAIL_WEEKDAY_TIME=%MAIL_TIME%"
  if not defined C114_MAIL_SAT_TIME set "C114_MAIL_SAT_TIME=%MAIL_TIME%"

  echo [INFO] Register split schedule ^(advanced weekday mode^)...
  call :create_weekly_task "%RUN_TASK%-weekday" "MON,TUE,WED,THU,FRI" "%C114_RUN_WEEKDAY_TIME%" "%RUN_SCRIPT%"
  if errorlevel 1 exit /b 1
  call :create_weekly_task "%RUN_TASK%-sat" "SAT" "%C114_RUN_SAT_TIME%" "%RUN_SCRIPT%"
  if errorlevel 1 exit /b 1
  call :create_weekly_task "%RUN_TASK%-sun" "SUN" "%C114_RUN_SUN_TIME%" "%RUN_SCRIPT%"
  if errorlevel 1 exit /b 1

  call :create_weekly_task "%MAIL_TASK%-weekday" "MON,TUE,WED,THU,FRI" "%C114_MAIL_WEEKDAY_TIME%" "%MAIL_SCRIPT%"
  if errorlevel 1 exit /b 1
  call :create_weekly_task "%MAIL_TASK%-sat" "SAT" "%C114_MAIL_SAT_TIME%" "%MAIL_SCRIPT%"
  if errorlevel 1 exit /b 1
  call :create_weekly_task "%MAIL_TASK%-sun" "SUN" "%C114_MAIL_SUN_TIME%" "%MAIL_SCRIPT%"
  if errorlevel 1 exit /b 1
)

if defined C114_RUN_WINDOW_START if defined C114_RUN_WINDOW_END if defined C114_RUN_WINDOW_EVERY_MIN (
  echo [INFO] Register run window: %C114_RUN_WINDOW_START%-%C114_RUN_WINDOW_END% every %C114_RUN_WINDOW_EVERY_MIN% min
  schtasks /create /tn "%RUN_TASK%-window" /sc DAILY /st %C114_RUN_WINDOW_START% /et %C114_RUN_WINDOW_END% /ri %C114_RUN_WINDOW_EVERY_MIN% /tr "\"%RUN_SCRIPT%\"" /f
  if errorlevel 1 (
    echo [ERROR] Failed creating run window task.
    exit /b 1
  )
)

echo [OK] Tasks created/updated.
echo [HINT] Query tasks:
echo   schtasks /query /fo LIST ^| findstr /I "%RUN_TASK% %MAIL_TASK%"
endlocal & exit /b 0

:create_weekly_task
set "TASK_NAME=%~1"
set "TASK_DAYS=%~2"
set "TASK_TIME=%~3"
set "TASK_SCRIPT=%~4"
if "%TASK_TIME%"=="" (
  echo [INFO] Skip task "%TASK_NAME%" ^(empty time^).
  exit /b 0
)
echo        %TASK_NAME% : %TASK_DAYS% %TASK_TIME%
schtasks /create /tn "%TASK_NAME%" /sc WEEKLY /d %TASK_DAYS% /st %TASK_TIME% /tr "\"%TASK_SCRIPT%\"" /f
if errorlevel 1 (
  echo [ERROR] Failed creating task "%TASK_NAME%".
  exit /b 1
)
exit /b 0
