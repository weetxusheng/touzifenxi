@echo off
setlocal EnableExtensions

rem Send email at scheduled time.
rem Behavior: send T-1 C114 brief when task runs.
rem Use gate 00:00 so only same-day generated T-1 file can pass.

set "MAIL_TO_LIST=zx944532395@sina.com chenxusheng@cjhxfund.com"
set "T1_GATE_TIME=10:54"

for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"

if not defined PYTHON_BIN (
  if exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" (
    set "PYTHON_BIN=%PROJECT_ROOT%\.venv\Scripts\python.exe"
  ) else (
    set "PYTHON_BIN=python"
  )
)

cd /d "%PROJECT_ROOT%"
set "PYTHONPATH=src"

call :load_dotenv

echo [C114 mail] mode=t1-gate gate=%T1_GATE_TIME% recipients=%MAIL_TO_LIST%
"%PYTHON_BIN%" -m touzifenxi.cli send-c114-latest-brief-email --t1-gate --gate-time %T1_GATE_TIME% --to %MAIL_TO_LIST%
set "EXIT_CODE=%ERRORLEVEL%"

echo [C114 mail] exit=%EXIT_CODE%
endlocal & exit /b %EXIT_CODE%

:load_dotenv
if not exist "%PROJECT_ROOT%\.env" exit /b 0
for /f "usebackq tokens=1* delims==" %%A in ("%PROJECT_ROOT%\.env") do (
  if not "%%A"=="" if not "%%A:~0,1"=="#" set "%%A=%%B"
)
exit /b 0
