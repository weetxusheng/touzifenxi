@echo off
chcp 65001 >nul
setlocal EnableExtensions

rem 手动触发：找 output/reports/feedcore_report/ 下最新一份 brief.html 发邮件。
rem 默认收件人见下方 MAIL_TO_LIST；也可调用时覆盖：
rem   send_feedcore_latest_brief_email_windows.bat a@x.com b@y.com
rem   send_feedcore_latest_brief_email_windows.bat "" "FeedCore 政经简报 2026-05-20"  (指定主题)

set "MAIL_TO_LIST=zx944532395@sina.com"
if not "%~1"=="" if not "%~1"=="""" set "MAIL_TO_LIST=%~1"
set "MAIL_SUBJECT=%~2"

for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"
set "LOG_DIR=%PROJECT_ROOT%\logs\feedcore"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "(Get-Date).ToString('yyyyMMdd_HHmmss')"`) do set "RUN_STAMP=%%I"
set "LOG_FILE=%LOG_DIR%\feedcore_mail_%RUN_STAMP%.log"

set "PYTHON_CMD="
if defined PYTHON_BIN if exist "%PYTHON_BIN%" call :probe_python ""%PYTHON_BIN%""
if not defined PYTHON_CMD if exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" call :probe_python ""%PROJECT_ROOT%\.venv\Scripts\python.exe""
if not defined PYTHON_CMD if exist "%PROJECT_ROOT%\venv\Scripts\python.exe" call :probe_python ""%PROJECT_ROOT%\venv\Scripts\python.exe""
if not defined PYTHON_CMD call :probe_python "python"
if not defined PYTHON_CMD call :probe_python "py -3"

cd /d "%PROJECT_ROOT%"
set "PYTHONPATH=%PROJECT_ROOT%\src;%PYTHONPATH%"
if not defined PYTHONUTF8 set "PYTHONUTF8=1"
if not defined PYTHONIOENCODING set "PYTHONIOENCODING=utf-8"

call :load_dotenv

if not defined PYTHON_CMD (
  echo [feedcore mail] ERROR: No runnable Python found.
  >> "%LOG_FILE%" echo [feedcore mail] ERROR: No runnable Python found.
  endlocal & exit /b 2
)

echo [feedcore mail] recipients=%MAIL_TO_LIST%
echo [feedcore mail] log=%LOG_FILE%
> "%LOG_FILE%" echo [feedcore mail] recipients=%MAIL_TO_LIST%
>> "%LOG_FILE%" echo [feedcore mail] python=%PYTHON_CMD%

if "%MAIL_SUBJECT%"=="" (
  %PYTHON_CMD% "%PROJECT_ROOT%\scripts\feedcore_send_brief_email.py" --to %MAIL_TO_LIST% >> "%LOG_FILE%" 2>&1
) else (
  %PYTHON_CMD% "%PROJECT_ROOT%\scripts\feedcore_send_brief_email.py" --to %MAIL_TO_LIST% --subject "%MAIL_SUBJECT%" >> "%LOG_FILE%" 2>&1
)
set "EXIT_CODE=%ERRORLEVEL%"

echo [feedcore mail] exit=%EXIT_CODE%
>> "%LOG_FILE%" echo [feedcore mail] exit=%EXIT_CODE%
if "%EXIT_CODE%"=="0" (
  echo [feedcore mail] status=SUCCESS
  >> "%LOG_FILE%" echo [feedcore mail] status=SUCCESS
) else (
  echo [feedcore mail] status=FAILED
  >> "%LOG_FILE%" echo [feedcore mail] status=FAILED
)
endlocal & exit /b %EXIT_CODE%

:probe_python
if defined PYTHON_CMD exit /b 0
set "_PY_CAND=%~1"
if not defined _PY_CAND exit /b 0
%_PY_CAND% --version >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=%_PY_CAND%"
exit /b 0

:load_dotenv
if not exist "%PROJECT_ROOT%\.env" exit /b 0
for /f "usebackq tokens=1* delims==" %%A in ("%PROJECT_ROOT%\.env") do (
  if not "%%A"=="" if not "%%A:~0,1"=="#" set "%%A=%%B"
)
exit /b 0
