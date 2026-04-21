@echo off
setlocal
chcp 65001 >nul

set "SOURCE=36kr"
set "LOG_TAG=kr36"
set "TIMEOUT=30"
set /a PICK=%random% %% 3
if %PICK%==1 set "TIMEOUT=40"
if %PICK%==2 set "TIMEOUT=50"

rem Repo root = scripts/kr36 -> two levels up from this file.
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"
set "LOG_DIR=%PROJECT_ROOT%\logs\kr36"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "(Get-Date).ToString('yyyyMMdd_HHmmss')"`) do set "RUN_STAMP=%%I"
set "LOG_FILE=%LOG_DIR%\kr36_%RUN_STAMP%.log"
set "KR36_LOG_FILE=%LOG_FILE%"
set "KR36_LOG_ECHO_STDOUT=1"

if not defined PYTHON_BIN (
  if exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" (
    set "PYTHON_BIN=%PROJECT_ROOT%\.venv\Scripts\python.exe"
  ) else (
    set "PYTHON_BIN=python"
  )
)

cd /d "%PROJECT_ROOT%"
set "PYTHONPATH=src"
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "(Get-Date).ToString('yyyy-MM-dd HH:mm:ss')"`) do set "RUN_AT=%%I"
echo [%LOG_TAG%] %RUN_AT% starting daily brief run (no email)...
echo [%LOG_TAG%] PROJECT_ROOT=%PROJECT_ROOT%
echo [%LOG_TAG%] PYTHON_BIN=%PYTHON_BIN%
echo [%LOG_TAG%] TIMEOUT=%TIMEOUT%s
echo [%LOG_TAG%] LOG_FILE=%LOG_FILE%
>> "%LOG_FILE%" echo [%LOG_TAG%] %RUN_AT% starting daily brief run (no email)...
>> "%LOG_FILE%" echo [%LOG_TAG%] PROJECT_ROOT=%PROJECT_ROOT%
>> "%LOG_FILE%" echo [%LOG_TAG%] PYTHON_BIN=%PYTHON_BIN%
>> "%LOG_FILE%" echo [%LOG_TAG%] TIMEOUT=%TIMEOUT%s
>> "%LOG_FILE%" echo [%LOG_TAG%] LOG_FILE=%LOG_FILE%

"%PYTHON_BIN%" --version >nul 2>&1
if errorlevel 1 (
  echo [%LOG_TAG%] ERROR: Python not runnable. Set PYTHON_BIN or add python to PATH.
  >> "%LOG_FILE%" echo [%LOG_TAG%] ERROR: Python not runnable. Set PYTHON_BIN or add python to PATH.
  endlocal & exit /b 2
)

if exist "%PROJECT_ROOT%\.env" (
  for /f "usebackq tokens=1* delims==" %%A in ("%PROJECT_ROOT%\.env") do (
    if not "%%A"=="" if not "%%A:~0,1"=="#" set "%%A=%%B"
  )
)

echo [%LOG_TAG%] running: "%PYTHON_BIN%" scripts\websearch.py run --source %SOURCE% --timeout %TIMEOUT%
>> "%LOG_FILE%" echo [%LOG_TAG%] running: "%PYTHON_BIN%" scripts\websearch.py run --source %SOURCE% --timeout %TIMEOUT%
set "PYTHONIOENCODING=utf-8"
"%PYTHON_BIN%" scripts\websearch.py run --source %SOURCE% --timeout %TIMEOUT%
set "EXIT_CODE=%ERRORLEVEL%"

for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "(Get-Date).ToString('yyyy-MM-dd HH:mm:ss')"`) do set "DONE_AT=%%I"
echo [%LOG_TAG%] %DONE_AT% finished with exit code %EXIT_CODE%.
echo [%LOG_TAG%] detail log: %LOG_FILE%
>> "%LOG_FILE%" echo [%LOG_TAG%] %DONE_AT% finished with exit code %EXIT_CODE%.

endlocal & exit /b %EXIT_CODE%
