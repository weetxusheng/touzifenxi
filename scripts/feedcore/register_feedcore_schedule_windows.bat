@echo off
setlocal EnableExtensions

rem Usage:
rem   register_feedcore_schedule_windows.bat [RUN_TIME] [MAIL_TIME] [RUN_TASK] [MAIL_TASK]
rem Example:
rem   register_feedcore_schedule_windows.bat 17:40 19:20
rem
rem 默认每日 17:40 跑 us_news，19:20 发最新简报邮件。

set "RUN_TIME=%~1"
if "%RUN_TIME%"=="" set "RUN_TIME=17:40"
set "MAIL_TIME=%~2"
if "%MAIL_TIME%"=="" set "MAIL_TIME=19:20"
set "RUN_TASK=%~3"
if "%RUN_TASK%"=="" set "RUN_TASK=touzifenxi-feedcore-us-news"
set "MAIL_TASK=%~4"
if "%MAIL_TASK%"=="" set "MAIL_TASK=touzifenxi-feedcore-daily-mail"

set "RUN_SCRIPT=%~dp0run_feedcore_us_news_windows.bat"
set "MAIL_SCRIPT=%~dp0send_feedcore_latest_brief_email_windows.bat"

if not exist "%RUN_SCRIPT%" echo [ERROR] Missing: "%RUN_SCRIPT%" & exit /b 1
if not exist "%MAIL_SCRIPT%" echo [ERROR] Missing: "%MAIL_SCRIPT%" & exit /b 1

echo [INFO] Register FeedCore split schedule ^(daily^)...
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

echo.
echo [OK] FeedCore 每日任务已创建/更新。
echo [HINT] Query:
echo   schtasks /query /fo LIST ^| findstr /I "%RUN_TASK% %MAIL_TASK%"
echo [HINT] Delete:
echo   schtasks /delete /tn "%RUN_TASK%" /f
echo   schtasks /delete /tn "%MAIL_TASK%" /f

endlocal & exit /b 0
