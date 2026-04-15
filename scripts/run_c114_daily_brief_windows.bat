@echo off
setlocal

set "PROJECT_ROOT=E:\AI\touzifenxi"
set "PYTHON_BIN=E:\Programs\Python310\python.exe"
set "MAIL_TO_1=zx944532395@sina.com"
set "MAIL_TO_2=chenxusheng@cjhxfund.com"

cd /d "%PROJECT_ROOT%"
set "PYTHONPATH=src"
echo [C114] %date% %time% starting daily brief run...

if exist "%PROJECT_ROOT%\.env" (
  for /f "usebackq tokens=1* delims==" %%A in ("%PROJECT_ROOT%\.env") do (
    if not "%%A"=="" if not "%%A:~0,1"=="#" set "%%A=%%B"
  )
)

set "EXIT_CODE=0"

echo [C114] sending to %MAIL_TO_1% via run-c114-daily-brief...
"%PYTHON_BIN%" -m touzifenxi.cli run-c114-daily-brief --to %MAIL_TO_1%
set "SEND1_EXIT=%ERRORLEVEL%"
if not "%SEND1_EXIT%"=="0" (
  echo [C114] send to %MAIL_TO_1% failed with exit code %SEND1_EXIT%.
  set "EXIT_CODE=1"
) else (
  echo [C114] send to %MAIL_TO_1% succeeded.
)

echo [C114] sending to %MAIL_TO_2% via send-c114-latest-brief-email...
"%PYTHON_BIN%" -m touzifenxi.cli send-c114-latest-brief-email --to %MAIL_TO_2%
set "SEND2_EXIT=%ERRORLEVEL%"
if not "%SEND2_EXIT%"=="0" (
  echo [C114] send to %MAIL_TO_2% failed with exit code %SEND2_EXIT%.
  set "EXIT_CODE=1"
) else (
  echo [C114] send to %MAIL_TO_2% succeeded.
)

echo [C114] %date% %time% finished with exit code %EXIT_CODE% (mail1=%SEND1_EXIT%, mail2=%SEND2_EXIT%).

endlocal & exit /b %EXIT_CODE%
