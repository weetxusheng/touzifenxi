@echo off
setlocal EnableExtensions

rem Send latest 36Kr T-1 brief after loading .env. Gate: brief mtime not before gate (Shanghai).
rem Same-dir kr36_step_*_brief_*.html must exist (enforced in send-kr36-latest-brief-email).

set "MAIL_TO_LIST=zx944532395@sina.com chenxusheng@cjhxfund.com"
set "T1_GATE_TIME=15:00"

for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"

set "PYTHON_CMD="
if defined PYTHON_BIN if exist "%PYTHON_BIN%" call :probe_python ""%PYTHON_BIN%""
if not defined PYTHON_CMD if exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" call :probe_python ""%PROJECT_ROOT%\.venv\Scripts\python.exe""
if not defined PYTHON_CMD if exist "%PROJECT_ROOT%\venv\Scripts\python.exe" call :probe_python ""%PROJECT_ROOT%\venv\Scripts\python.exe""
if not defined PYTHON_CMD call :probe_python "python"
if not defined PYTHON_CMD call :probe_python "py -3"

cd /d "%PROJECT_ROOT%"
set "PYTHONPATH=src"

call :load_dotenv

if not defined PYTHON_CMD (
  echo [36Kr mail] ERROR: No runnable Python found. Tried PYTHON_BIN/.venv/venv/python/py -3.
  endlocal & exit /b 2
)

echo [36Kr mail] mode=t1-gate gate=%T1_GATE_TIME% recipients=%MAIL_TO_LIST%
%PYTHON_CMD% -m utils.cli send-kr36-latest-brief-email --t1-gate --gate-time %T1_GATE_TIME% --to %MAIL_TO_LIST%
set "EXIT_CODE=%ERRORLEVEL%"

echo [36Kr mail] exit=%EXIT_CODE%
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

