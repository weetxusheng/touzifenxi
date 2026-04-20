@echo off
setlocal

rem Repo root = scripts/c114 -> two levels up from this file.
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"
set "LOG_FILE=%PROJECT_ROOT%\log.txt"

if not defined PYTHON_BIN (
  if exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" (
    set "PYTHON_BIN=%PROJECT_ROOT%\.venv\Scripts\python.exe"
  ) else (
    set "PYTHON_BIN=python"
  )
)

cd /d "%PROJECT_ROOT%"
set "PYTHONPATH=src"
echo [C114] %date% %time% starting daily brief run (no email)...
echo [C114] PROJECT_ROOT=%PROJECT_ROOT%
echo [C114] PYTHON_BIN=%PYTHON_BIN%
> "%LOG_FILE%" echo [C114] %date% %time% starting daily brief run (no email)...
>> "%LOG_FILE%" echo [C114] PROJECT_ROOT=%PROJECT_ROOT%
>> "%LOG_FILE%" echo [C114] PYTHON_BIN=%PYTHON_BIN%

"%PYTHON_BIN%" --version >nul 2>&1
if errorlevel 1 (
  echo [C114] ERROR: Python not runnable. Set PYTHON_BIN or add python to PATH.
  >> "%LOG_FILE%" echo [C114] ERROR: Python not runnable. Set PYTHON_BIN or add python to PATH.
  endlocal & exit /b 2
)

"%PYTHON_BIN%" -c "import touzifenxi; from zoneinfo import ZoneInfo; ZoneInfo('Asia/Shanghai')" >nul 2>&1
if errorlevel 1 (
  echo [C114] ERROR: Python env missing project/tzdata dependency. Run: pip install -e . && pip install tzdata
  >> "%LOG_FILE%" echo [C114] ERROR: Python env missing project/tzdata dependency. Run: pip install -e . ^&^& pip install tzdata
  endlocal & exit /b 3
)

if exist "%PROJECT_ROOT%\.env" (
  for /f "usebackq tokens=1* delims==" %%A in ("%PROJECT_ROOT%\.env") do (
    if not "%%A"=="" if not "%%A:~0,1"=="#" set "%%A=%%B"
  )
)

"%PYTHON_BIN%" -m touzifenxi.cli run-c114-daily-brief --no-email
set "EXIT_CODE=%ERRORLEVEL%"

echo [C114] %date% %time% finished with exit code %EXIT_CODE%.
echo [C114] detail log: %LOG_FILE%
>> "%LOG_FILE%" echo [C114] %date% %time% finished with exit code %EXIT_CODE%.

endlocal & exit /b %EXIT_CODE%
