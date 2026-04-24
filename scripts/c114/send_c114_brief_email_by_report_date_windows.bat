@echo off
setlocal EnableExtensions

rem Send C114 step6 brief for report date. Usage: this.bat [YYYY-MM-DD]
rem No arg: use T-1 calendar day in Asia/Shanghai (same idea as c114 T-1 brief).

set "REPORT_DATE=%~1"
set "MAIL_TO_LIST=zx944532395@sina.com chenxusheng@cjhxfund.com liuyangcj@cjhxfund.com"

for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"

if not defined PYTHON_BIN (
  if exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" (
    set "PYTHON_BIN=%PROJECT_ROOT%\.venv\Scripts\python.exe"
  ) else (
    set "PYTHON_BIN=python"
  )
)

if exist "%PROJECT_ROOT%\.venv\Scripts\touzifenxi.exe" (
  set "TFX=%PROJECT_ROOT%\.venv\Scripts\touzifenxi.exe"
) else (
  set "TFX=touzifenxi"
)

cd /d "%PROJECT_ROOT%"
set "PYTHONPATH=src"

if "%REPORT_DATE%"=="" (
  for /f "delims=" %%D in ('"%PYTHON_BIN%" -c "from datetime import datetime, timedelta, timezone; s = timezone(timedelta(hours=8)); print((datetime.now(s) - timedelta(days=1)).date().isoformat())"') do set "REPORT_DATE=%%D"
)

call :load_dotenv

echo [C114 mail] report-date=%REPORT_DATE% recipients=%MAIL_TO_LIST%
"%TFX%" send-c114-latest-brief-email --report-date %REPORT_DATE% --to %MAIL_TO_LIST%
set "EXIT_CODE=%ERRORLEVEL%"
echo [C114 mail] exit=%EXIT_CODE%
endlocal & exit /b %EXIT_CODE%

:load_dotenv
if not exist "%PROJECT_ROOT%\.env" exit /b 0
for /f "usebackq tokens=1* delims==" %%A in ("%PROJECT_ROOT%\.env") do (
  if not "%%A"=="" if not "%%A:~0,1"=="#" set "%%A=%%B"
)
exit /b 0
