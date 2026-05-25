@echo off
setlocal EnableExtensions

rem Usage:
rem   register_feedcore_schedule_windows.bat [RUN_TIME]
rem Example:
rem   register_feedcore_schedule_windows.bat 09:30
rem
rem 默认 us_news 每天 09:30 跑。
rem 不注册 send 邮件任务 - 邮件由用户主动跑 send_feedcore_latest_brief_email_windows.bat

set "RUN_TIME=%~1"
if "%RUN_TIME%"=="" set "RUN_TIME=09:30"

set "US_TASK=touzifenxi-feedcore-us-news"
set "US_SCRIPT=%~dp0run_feedcore_us_news_windows.bat"

if not exist "%US_SCRIPT%" echo [ERROR] Missing: "%US_SCRIPT%" & exit /b 1

echo [INFO] Register FeedCore us_news daily schedule...
echo        %US_TASK% : %RUN_TIME% ^| "%US_SCRIPT%"

schtasks /create /tn "%US_TASK%" /sc DAILY /st %RUN_TIME% /tr "\"%US_SCRIPT%\"" /f
if errorlevel 1 (
  echo [ERROR] Failed creating us_news task.
  exit /b 1
)

echo.
echo [OK] FeedCore 定时任务已创建。注意:
echo   - 邮件不在定时任务里 - 跑完后请主动执行 send_feedcore_latest_brief_email_windows.bat
echo   - 查询: schtasks /query /fo LIST ^| findstr /I "%US_TASK%"
echo   - 撤销: schtasks /delete /tn "%US_TASK%" /f

endlocal & exit /b 0
