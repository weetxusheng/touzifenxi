@echo off
chcp 65001 >nul
setlocal EnableExtensions

rem Send T-1 C114 brief when task runs. Requires .env SMTP.
rem Same-dir c114_step_6_brief_YYYYMMDD.html must exist (enforced in send-c114-latest-brief-email).
rem Gate: step6 .md mtime not before gate-time today (Shanghai).

set "MAIL_TO_LIST=chenxusheng@cjhxfund.com liuyangcj@cjhxfund.com zx944532395@sina.com 944532395@qq.com"
set "T1_GATE_TIME=15:08"

for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"
set "LOG_DIR=%PROJECT_ROOT%\logs\c114"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "(Get-Date).ToString('yyyyMMdd_HHmmss')"`) do set "RUN_STAMP=%%I"
set "LOG_FILE=%LOG_DIR%\c114_mail_%RUN_STAMP%.log"

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
  echo [C114 mail] ERROR: No runnable Python found. Tried PYTHON_BIN/.venv/venv/python/py -3.
  >> "%LOG_FILE%" echo [C114 mail] ERROR: No runnable Python found. Tried PYTHON_BIN/.venv/venv/python/py -3.
  endlocal & exit /b 2
)

echo [C114 mail] mode=t1-gate gate=%T1_GATE_TIME% recipients=%MAIL_TO_LIST%
echo [C114 mail] log=%LOG_FILE%
> "%LOG_FILE%" echo [C114 mail] mode=t1-gate gate=%T1_GATE_TIME% recipients=%MAIL_TO_LIST%
>> "%LOG_FILE%" echo [C114 mail] python=%PYTHON_CMD%
%PYTHON_CMD% -m utils.cli send-c114-latest-brief-email --t1-gate --gate-time %T1_GATE_TIME% --to %MAIL_TO_LIST% >> "%LOG_FILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"

echo [C114 mail] exit=%EXIT_CODE%
>> "%LOG_FILE%" echo [C114 mail] exit=%EXIT_CODE%
if "%EXIT_CODE%"=="0" (
  echo [C114 mail] status=SUCCESS
  >> "%LOG_FILE%" echo [C114 mail] status=SUCCESS
) else (
  echo [C114 mail] status=FAILED
  >> "%LOG_FILE%" echo [C114 mail] status=FAILED
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
