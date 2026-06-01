@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion

set "PROFILE=us_news"
set "LOG_TAG=feedcore-%PROFILE%"

rem Repo root = scripts/feedcore -> two levels up.
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"
set "LOG_DIR=!PROJECT_ROOT!\logs\feedcore"
if not exist "!LOG_DIR!" mkdir "!LOG_DIR!"
set "LOG_FILE=!LOG_DIR!\feedcore_!PROFILE!.log"

set "PYTHON_CMD="
if defined PYTHON_BIN if exist "!PYTHON_BIN!" call :probe_python ""!PYTHON_BIN!""
if not defined PYTHON_CMD if exist "!PROJECT_ROOT!\.venv\Scripts\python.exe" call :probe_python ""!PROJECT_ROOT!\.venv\Scripts\python.exe""
if not defined PYTHON_CMD if exist "!PROJECT_ROOT!\venv\Scripts\python.exe" call :probe_python ""!PROJECT_ROOT!\venv\Scripts\python.exe""
if not defined PYTHON_CMD call :probe_python "python"
if not defined PYTHON_CMD call :probe_python "py -3"

cd /d "!PROJECT_ROOT!"
set "PYTHONPATH=!PROJECT_ROOT!\src"
if not defined PYTHONUTF8 set "PYTHONUTF8=1"
if not defined PYTHONIOENCODING set "PYTHONIOENCODING=utf-8"

echo [!LOG_TAG!] %date% %time% starting us_news profile...
echo [!LOG_TAG!] PROJECT_ROOT=!PROJECT_ROOT!
echo [!LOG_TAG!] PYTHON_CMD=!PYTHON_CMD!
> "!LOG_FILE!" echo [!LOG_TAG!] %date% %time% starting us_news profile...
>> "!LOG_FILE!" echo [!LOG_TAG!] PROJECT_ROOT=!PROJECT_ROOT!
>> "!LOG_FILE!" echo [!LOG_TAG!] PYTHON_CMD=!PYTHON_CMD!

if not defined PYTHON_CMD (
  echo [!LOG_TAG!] ERROR: No runnable Python found. Tried PYTHON_BIN/.venv/venv/python/py -3.
  >> "!LOG_FILE!" echo [!LOG_TAG!] ERROR: No runnable Python found.
  endlocal & exit /b 2
)

!PYTHON_CMD! -c "import feedcore.cli" >nul 2>&1
if errorlevel 1 (
  echo [!LOG_TAG!] ERROR: Python env missing feedcore module. Run: pip install -e .
  >> "!LOG_FILE!" echo [!LOG_TAG!] ERROR: missing feedcore module
  endlocal & exit /b 3
)

if exist "!PROJECT_ROOT!\.env" (
  for /f "usebackq tokens=1* delims==" %%A in ("!PROJECT_ROOT!\.env") do (
    if not "%%A"=="" if not "%%A:~0,1"=="#" set "%%A=%%B"
  )
)

!PYTHON_CMD! "!PROJECT_ROOT!\scripts\feedcore_us_news.py" 2>> "!LOG_FILE!"
set "EXIT_CODE=%ERRORLEVEL%"

if not "!EXIT_CODE!"=="0" (
  echo [!LOG_TAG!] Python stderr appended to: !LOG_FILE!
)

echo [!LOG_TAG!] %date% %time% finished with exit code !EXIT_CODE!.
echo [!LOG_TAG!] detail log: !LOG_FILE!
>> "!LOG_FILE!" echo [!LOG_TAG!] %date% %time% finished with exit code !EXIT_CODE!.

endlocal & exit /b %EXIT_CODE%

:probe_python
if defined PYTHON_CMD exit /b 0
set "_PY_CAND=%~1"
if not defined _PY_CAND exit /b 0
%_PY_CAND% --version >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=%_PY_CAND%"
exit /b 0
