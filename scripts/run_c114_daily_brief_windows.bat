@echo off
setlocal

set "PROJECT_ROOT=E:\AI\touzifenxi"
set "PYTHON_BIN=E:\Programs\Python310\python.exe"

cd /d "%PROJECT_ROOT%"
set "PYTHONPATH=src"
echo [C114] %date% %time% starting daily brief run...

if exist "%PROJECT_ROOT%\.env" (
  for /f "usebackq tokens=1* delims==" %%A in ("%PROJECT_ROOT%\.env") do (
    if not "%%A"=="" if not "%%A:~0,1"=="#" set "%%A=%%B"
  )
)

"%PYTHON_BIN%" -m touzifenxi.cli run-c114-daily-brief --to zx944532395@sina.com
set "EXIT_CODE=%ERRORLEVEL%"
echo [C114] %date% %time% finished with exit code %EXIT_CODE%.

endlocal & exit /b %EXIT_CODE%
