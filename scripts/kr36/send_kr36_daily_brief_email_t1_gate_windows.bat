@echo off
setlocal EnableExtensions

rem Send latest 36Kr T-1 brief after loading .env. Gate: brief mtime not before gate (Shanghai).
rem Same-dir kr36_step_*_brief_*.html must exist (enforced in send-kr36-latest-brief-email).

set "MAIL_TO_LIST=zx944532395@sina.com 944532395@qq.com"
rem Default behavior: send today's (Asia/Shanghai) brief by report-date.

for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"
set "LOG_DIR=%PROJECT_ROOT%\logs\kr36"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "(Get-Date).ToString('yyyyMMdd_HHmmss')"`) do set "RUN_STAMP=%%I"
set "LOG_FILE=%LOG_DIR%\kr36_mail_%RUN_STAMP%.log"

set "PYTHON_CMD="
if defined PYTHON_BIN if exist "%PYTHON_BIN%" call :probe_python ""%PYTHON_BIN%""
if not defined PYTHON_CMD if exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" call :probe_python ""%PROJECT_ROOT%\.venv\Scripts\python.exe""
if not defined PYTHON_CMD if exist "%PROJECT_ROOT%\venv\Scripts\python.exe" call :probe_python ""%PROJECT_ROOT%\venv\Scripts\python.exe""
if not defined PYTHON_CMD call :probe_python "python"
if not defined PYTHON_CMD call :probe_python "py -3"

cd /d "%PROJECT_ROOT%"
rem Force UTF-8 console output (avoid garbled Chinese logs on new machines).
chcp 65001 >nul
set "PYTHONPATH=%PROJECT_ROOT%\src;%PYTHONPATH%"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

call :load_dotenv

if not defined PYTHON_CMD (
  echo [36Kr mail] ERROR: No runnable Python found. Tried PYTHON_BIN/.venv/venv/python/py -3.
  >> "%LOG_FILE%" echo [36Kr mail] ERROR: No runnable Python found. Tried PYTHON_BIN/.venv/venv/python/py -3.
  endlocal & exit /b 2
)

for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "[System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId((Get-Date), 'China Standard Time').ToString('yyyy-MM-dd')"`) do set "REPORT_DATE=%%I"

echo [36Kr mail] mode=report-date report_date=%REPORT_DATE% recipients=%MAIL_TO_LIST%
echo [36Kr mail] log=%LOG_FILE%
> "%LOG_FILE%" echo [36Kr mail] mode=report-date report_date=%REPORT_DATE% recipients=%MAIL_TO_LIST%
>> "%LOG_FILE%" echo [36Kr mail] python=%PYTHON_CMD%
%PYTHON_CMD% -m utils.cli send-kr36-latest-brief-email --report-date %REPORT_DATE% --to %MAIL_TO_LIST% >> "%LOG_FILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"

echo [36Kr mail] exit=%EXIT_CODE%
>> "%LOG_FILE%" echo [36Kr mail] exit=%EXIT_CODE%
if "%EXIT_CODE%"=="0" (
  echo [36Kr mail] status=SUCCESS
  >> "%LOG_FILE%" echo [36Kr mail] status=SUCCESS
) else (
  echo [36Kr mail] status=FAILED
  >> "%LOG_FILE%" echo [36Kr mail] status=FAILED
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

