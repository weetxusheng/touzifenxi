@echo off
setlocal

set "SOURCE=%~1"
if "%SOURCE%"=="" set "SOURCE=c114"
set "LOG_TAG=%SOURCE%"

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
echo [%LOG_TAG%] %date% %time% starting daily brief run (no email)...
echo [%LOG_TAG%] PROJECT_ROOT=%PROJECT_ROOT%
echo [%LOG_TAG%] PYTHON_BIN=%PYTHON_BIN%
> "%LOG_FILE%" echo [%LOG_TAG%] %date% %time% starting daily brief run (no email)...
>> "%LOG_FILE%" echo [%LOG_TAG%] PROJECT_ROOT=%PROJECT_ROOT%
>> "%LOG_FILE%" echo [%LOG_TAG%] PYTHON_BIN=%PYTHON_BIN%

"%PYTHON_BIN%" --version >nul 2>&1
if errorlevel 1 (
  echo [%LOG_TAG%] ERROR: Python not runnable. Set PYTHON_BIN or add python to PATH.
  >> "%LOG_FILE%" echo [%LOG_TAG%] ERROR: Python not runnable. Set PYTHON_BIN or add python to PATH.
  endlocal & exit /b 2
)

"%PYTHON_BIN%" -c "import c114.cli" >nul 2>&1
if errorlevel 1 (
  echo [%LOG_TAG%] ERROR: Python env missing runtime dependency. Run: pip install -e .
  >> "%LOG_FILE%" echo [%LOG_TAG%] ERROR: Python env missing runtime dependency. Run: pip install -e .
  endlocal & exit /b 3
)

if exist "%PROJECT_ROOT%\.env" (
  for /f "usebackq tokens=1* delims==" %%A in ("%PROJECT_ROOT%\.env") do (
    if not "%%A"=="" if not "%%A:~0,1"=="#" set "%%A=%%B"
  )
)

"%PYTHON_BIN%" scripts\websearch.py run --source %SOURCE% --timeout 90
set "EXIT_CODE=%ERRORLEVEL%"

echo [%LOG_TAG%] %date% %time% finished with exit code %EXIT_CODE%.
echo [%LOG_TAG%] detail log: %LOG_FILE%
>> "%LOG_FILE%" echo [%LOG_TAG%] %date% %time% finished with exit code %EXIT_CODE%.

endlocal & exit /b %EXIT_CODE%
