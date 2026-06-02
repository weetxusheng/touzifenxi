@echo off
setlocal EnableExtensions

rem Usage:
rem   register_c114_split_schedule_windows.bat [RUN_TIME] [MAIL_TIME] [RUN_TASK] [MAIL_TASK]
rem Example:
rem   register_c114_split_schedule_windows.bat 17:40 19:20
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
if "%RUN_TIME%"=="" set "RUN_TIME=16:10"
set "MAIL_TIME=%~2"
if "%MAIL_TIME%"=="" set "MAIL_TIME=16:30"
set "RUN_TASK=%~3"
if "%RUN_TASK%"=="" set "RUN_TASK=touzifenxi-c114-daily-run"
set "MAIL_TASK=%~4"
if "%MAIL_TASK%"=="" set "MAIL_TASK=touzifenxi-c114-daily-mail"

set "RUN_SCRIPT=%~dp0run_c114_daily_brief_no_email_windows.bat"
set "MAIL_SCRIPT=%~dp0send_c114_daily_brief_email_t1_gate_windows.bat"
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"
call :load_local_schedule_auth

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

  call :create_daily_task "%RUN_TASK%" "%RUN_TIME%" "%RUN_SCRIPT%"
  if errorlevel 1 (
    echo [ERROR] Failed creating run task.
    exit /b 1
  )

  call :create_daily_task "%MAIL_TASK%" "%MAIL_TIME%" "%MAIL_SCRIPT%"
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
  call :create_daily_window_task "%RUN_TASK%-window" "%C114_RUN_WINDOW_START%" "%C114_RUN_WINDOW_END%" "%C114_RUN_WINDOW_EVERY_MIN%" "%RUN_SCRIPT%"
  if errorlevel 1 (
    echo [ERROR] Failed creating run window task.
    exit /b 1
  )
)

echo [OK] Tasks created/updated.
echo [HINT] Query tasks:
echo   schtasks /query /fo LIST ^| findstr /I "%RUN_TASK% %MAIL_TASK%"
endlocal & exit /b 0

:load_local_schedule_auth
for %%F in ("%PROJECT_ROOT%\.env" "%PROJECT_ROOT%\.env.local") do (
  if exist "%%~F" (
    for /f "usebackq tokens=1* delims==" %%A in ("%%~F") do (
      if not "%%A"=="" if not "%%A:~0,1"=="#" set "%%A=%%B"
    )
  )
)
exit /b 0

:create_daily_task
set "_TASK_NAME=%~1"
set "_TASK_TIME=%~2"
set "_TASK_SCRIPT=%~3"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $time=[datetime]::ParseExact($env:_TASK_TIME,'H:mm',[Globalization.CultureInfo]::InvariantCulture); $action=New-ScheduledTaskAction -Execute $env:_TASK_SCRIPT -WorkingDirectory $env:PROJECT_ROOT; $trigger=New-ScheduledTaskTrigger -Daily -At $time; $params=@{TaskName=$env:_TASK_NAME; Action=$action; Trigger=$trigger; Force=$true}; if ($env:TOUZIFENXI_SCHEDULE_USER -and $env:TOUZIFENXI_SCHEDULE_PASSWORD) { $params.User=$env:TOUZIFENXI_SCHEDULE_USER; $params.Password=$env:TOUZIFENXI_SCHEDULE_PASSWORD }; Register-ScheduledTask @params | Out-Null"
exit /b %ERRORLEVEL%

:create_daily_window_task
set "_TASK_NAME=%~1"
set "_TASK_START=%~2"
set "_TASK_END=%~3"
set "_TASK_EVERY_MIN=%~4"
set "_TASK_SCRIPT=%~5"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $start=[datetime]::ParseExact($env:_TASK_START,'H:mm',[Globalization.CultureInfo]::InvariantCulture); $end=[datetime]::ParseExact($env:_TASK_END,'H:mm',[Globalization.CultureInfo]::InvariantCulture); $duration=$end-$start; if ($duration.TotalMinutes -le 0) { throw 'Run window end must be after start.' }; $action=New-ScheduledTaskAction -Execute $env:_TASK_SCRIPT -WorkingDirectory $env:PROJECT_ROOT; $trigger=New-ScheduledTaskTrigger -Daily -At $start; $trigger.Repetition.Interval=(New-TimeSpan -Minutes ([int]$env:_TASK_EVERY_MIN)); $trigger.Repetition.Duration=$duration; $params=@{TaskName=$env:_TASK_NAME; Action=$action; Trigger=$trigger; Force=$true}; if ($env:TOUZIFENXI_SCHEDULE_USER -and $env:TOUZIFENXI_SCHEDULE_PASSWORD) { $params.User=$env:TOUZIFENXI_SCHEDULE_USER; $params.Password=$env:TOUZIFENXI_SCHEDULE_PASSWORD }; Register-ScheduledTask @params | Out-Null"
exit /b %ERRORLEVEL%

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
set "_TASK_NAME=%TASK_NAME%"
set "_TASK_DAYS=%TASK_DAYS%"
set "_TASK_TIME=%TASK_TIME%"
set "_TASK_SCRIPT=%TASK_SCRIPT%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $map=@{MON='Monday';TUE='Tuesday';WED='Wednesday';THU='Thursday';FRI='Friday';SAT='Saturday';SUN='Sunday'}; $days=($env:_TASK_DAYS -split ',') | ForEach-Object { $map[$_.Trim().ToUpperInvariant()] }; $time=[datetime]::ParseExact($env:_TASK_TIME,'H:mm',[Globalization.CultureInfo]::InvariantCulture); $action=New-ScheduledTaskAction -Execute $env:_TASK_SCRIPT -WorkingDirectory $env:PROJECT_ROOT; $trigger=New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $time; $params=@{TaskName=$env:_TASK_NAME; Action=$action; Trigger=$trigger; Force=$true}; if ($env:TOUZIFENXI_SCHEDULE_USER -and $env:TOUZIFENXI_SCHEDULE_PASSWORD) { $params.User=$env:TOUZIFENXI_SCHEDULE_USER; $params.Password=$env:TOUZIFENXI_SCHEDULE_PASSWORD }; Register-ScheduledTask @params | Out-Null"
if errorlevel 1 (
  echo [ERROR] Failed creating task "%TASK_NAME%".
  exit /b 1
)
exit /b 0
