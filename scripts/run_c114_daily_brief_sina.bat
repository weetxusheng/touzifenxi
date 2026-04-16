@echo off
setlocal

for %%I in ("%~dp0..") do set "PROJECT_ROOT=%%~fI"
if not defined PYTHON_BIN set "PYTHON_BIN=python"

cd /d "%PROJECT_ROOT%"
set "PYTHONPATH=src"

"%PYTHON_BIN%" -m touzifenxi.cli run-c114-daily-brief --to zx944532395@sina.com
set "EXIT_CODE=%ERRORLEVEL%"

endlocal & exit /b %EXIT_CODE%
