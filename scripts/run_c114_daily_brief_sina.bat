@echo off
setlocal

set "PROJECT_ROOT=E:\AI\touzifenxi"
set "PYTHON_BIN=E:\Programs\Python310\python.exe"

cd /d "%PROJECT_ROOT%"
set "PYTHONPATH=src"

"%PYTHON_BIN%" -m touzifenxi.cli run-c114-daily-brief --to zx944532395@sina.com
set "EXIT_CODE=%ERRORLEVEL%"

endlocal & exit /b %EXIT_CODE%
