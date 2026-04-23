@echo off
setlocal EnableExtensions
rem ASCII only: UTF-8/Chinese in .bat can break under cmd (wrong code page).
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"
cd /d "%PROJECT_ROOT%"

if not defined PYTHON_BIN (
  if exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" (
    set "PYTHON_BIN=%PROJECT_ROOT%\.venv\Scripts\python.exe"
  ) else (
    set "PYTHON_BIN=python"
  )
)

set "TARGET_DATE=%~1"
if not defined TARGET_DATE (
  for /f "delims=" %%D in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "TARGET_DATE=%%D"
)

if not exist "%PROJECT_ROOT%\config\runtime.local.json" (
  echo [ERROR] Missing config\runtime.local.json. Copy from config\runtime.example.json
  endlocal & exit /b 2
)

if exist "%PROJECT_ROOT%\.env" (
  for /f "usebackq tokens=1* delims==" %%A in ("%PROJECT_ROOT%\.env") do (
    if not "%%A"=="" if not "%%A:~0,1"=="#" set "%%A=%%B"
  )
)

set "PYTHONPATH=%PROJECT_ROOT%\src"
set "PYTHONIOENCODING=utf-8"

set "LOG_DIR=%PROJECT_ROOT%\logs\kr36"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
for /f "delims=" %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "RUN_STAMP=%%I"
set "LOG_FILE=%LOG_DIR%\kr36_topics_only_%RUN_STAMP%.log"
set "KR36_LOG_FILE=%LOG_FILE%"
set "KR36_LOG_ECHO_STDOUT=1"

echo [kr36-topics-only] PROJECT_ROOT=%PROJECT_ROOT%
echo [kr36-topics-only] PYTHON_BIN=%PYTHON_BIN%
echo [kr36-topics-only] DATE=%TARGET_DATE%
echo [kr36-topics-only] LOG_FILE=%LOG_FILE%
echo.
echo [kr36-topics-only] Set sources.kr36 in config\runtime.local.json ^(topic_focus_keywords etc.^)
echo.

"%PYTHON_BIN%" --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python not found. Set PYTHON_BIN or use .venv
  endlocal & exit /b 2
)

echo [kr36-topics-only] running: "%PYTHON_BIN%" -u -m kr36.cli kr36-topics-only --date %TARGET_DATE%
>> "%LOG_FILE%" echo [kr36-topics-only] DATE=%TARGET_DATE% started
>> "%LOG_FILE%" echo [kr36-topics-only] cmd: "%PYTHON_BIN%" -u -m kr36.cli kr36-topics-only --date %TARGET_DATE%
"%PYTHON_BIN%" -u -m kr36.cli kr36-topics-only --date %TARGET_DATE%
set "EXIT_CODE=%ERRORLEVEL%"
>> "%LOG_FILE%" echo [kr36-topics-only] finished exit=%EXIT_CODE%

echo.
if %EXIT_CODE% neq 0 (
  echo [kr36-topics-only] FAILED exit=%EXIT_CODE% log=%LOG_FILE%
) else (
  echo [kr36-topics-only] OK. See run_dir and kr36_topic_downloads\ under that run.
  echo [kr36-topics-only] log=%LOG_FILE%
)

endlocal & exit /b %EXIT_CODE%
