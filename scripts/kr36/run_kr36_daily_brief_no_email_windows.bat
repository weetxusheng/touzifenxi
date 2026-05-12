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

set "PYTHON_CMD="
if defined PYTHON_BIN if exist "%PYTHON_BIN%" call :probe_python ""%PYTHON_BIN%""
if not defined PYTHON_CMD if exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" call :probe_python ""%PROJECT_ROOT%\.venv\Scripts\python.exe""
if not defined PYTHON_CMD if exist "%PROJECT_ROOT%\venv\Scripts\python.exe" call :probe_python ""%PROJECT_ROOT%\venv\Scripts\python.exe""
if not defined PYTHON_CMD call :probe_python "python"
if not defined PYTHON_CMD call :probe_python "py -3"

cd /d "%PROJECT_ROOT%"
set "PYTHONPATH=%PROJECT_ROOT%\src"
if not defined PYTHONUTF8 set "PYTHONUTF8=1"
if not defined PYTHONIOENCODING set "PYTHONIOENCODING=utf-8"
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "(Get-Date).ToString('yyyy-MM-dd HH:mm:ss')"`) do set "RUN_AT=%%I"
echo [%LOG_TAG%] %RUN_AT% starting daily brief run (no email)...
echo [%LOG_TAG%] PROJECT_ROOT=%PROJECT_ROOT%
echo [%LOG_TAG%] PYTHON_CMD=%PYTHON_CMD%
echo [%LOG_TAG%] TIMEOUT=%TIMEOUT%s
echo [%LOG_TAG%] LOG_FILE=%LOG_FILE%
>> "%LOG_FILE%" echo [%LOG_TAG%] %RUN_AT% starting daily brief run (no email)...
>> "%LOG_FILE%" echo [%LOG_TAG%] PROJECT_ROOT=%PROJECT_ROOT%
>> "%LOG_FILE%" echo [%LOG_TAG%] PYTHON_CMD=%PYTHON_CMD%
>> "%LOG_FILE%" echo [%LOG_TAG%] TIMEOUT=%TIMEOUT%s
>> "%LOG_FILE%" echo [%LOG_TAG%] LOG_FILE=%LOG_FILE%

if not defined PYTHON_CMD (
  echo [%LOG_TAG%] ERROR: No runnable Python found. Tried PYTHON_BIN/.venv/venv/python/py -3.
  >> "%LOG_FILE%" echo [%LOG_TAG%] ERROR: No runnable Python found. Tried PYTHON_BIN/.venv/venv/python/py -3.
  endlocal & exit /b 2
)

%PYTHON_CMD% -c "import cv2" >nul 2>&1
if errorlevel 1 (
  echo [%LOG_TAG%] ERROR: OpenCV ^(cv2^) is missing in current Python env.
  echo [%LOG_TAG%] HINT: run scripts\install_python_deps_windows.bat
  >> "%LOG_FILE%" echo [%LOG_TAG%] ERROR: OpenCV ^(cv2^) is missing in current Python env.
  >> "%LOG_FILE%" echo [%LOG_TAG%] HINT: run scripts\install_python_deps_windows.bat
  endlocal & exit /b 6
)

rem In kr36 auto-solver flow, Playwright must be truly usable (not only package present).
%PYTHON_CMD% -c "from playwright.sync_api import sync_playwright, Error, TimeoutError" >nul 2>&1
if errorlevel 1 (
  echo [%LOG_TAG%] ERROR: Playwright sync_api import failed in current Python: %PYTHON_CMD%
  echo [%LOG_TAG%] HINT: run this for details:
  echo [%LOG_TAG%]       %PYTHON_CMD% -c "from playwright.sync_api import sync_playwright^)"
  echo [%LOG_TAG%] HINT: then reinstall:
  echo [%LOG_TAG%]       python -m pip install -U playwright ^&^& python -m playwright install chromium
  >> "%LOG_FILE%" echo [%LOG_TAG%] ERROR: Playwright sync_api import failed in current Python: %PYTHON_CMD%
  >> "%LOG_FILE%" echo [%LOG_TAG%] HINT: %PYTHON_CMD% -c "from playwright.sync_api import sync_playwright^)"
  >> "%LOG_FILE%" echo [%LOG_TAG%] HINT: python -m pip install -U playwright ^&^& python -m playwright install chromium
  endlocal & exit /b 7
)

%PYTHON_CMD% -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); b.close(); p.stop()" >nul 2>&1
if errorlevel 1 (
  echo [%LOG_TAG%] ERROR: Playwright launch failed in current Python: %PYTHON_CMD%
  echo [%LOG_TAG%] HINT: set PYTHON_BIN to project venv python and run:
  echo [%LOG_TAG%]       python -m playwright install chromium
  echo [%LOG_TAG%] HINT: run this for detailed error:
  echo [%LOG_TAG%]       %PYTHON_CMD% -c "from playwright.sync_api import sync_playwright; p=sync_playwright^(^).start^(^); b=p.chromium.launch^(headless=True^)"
  >> "%LOG_FILE%" echo [%LOG_TAG%] ERROR: Playwright launch failed in current Python: %PYTHON_CMD%
  >> "%LOG_FILE%" echo [%LOG_TAG%] HINT: python -m playwright install chromium
  endlocal & exit /b 8
)

if exist "%PROJECT_ROOT%\.env" (
  for /f "usebackq tokens=1* delims==" %%A in ("%PROJECT_ROOT%\.env") do (
    if not "%%A"=="" if not "%%A:~0,1"=="#" set "%%A=%%B"
  )
)

echo [%LOG_TAG%] running: %PYTHON_CMD% "%PROJECT_ROOT%\scripts\websearch.py" run --source %SOURCE% --timeout %TIMEOUT%
>> "%LOG_FILE%" echo [%LOG_TAG%] running: %PYTHON_CMD% "%PROJECT_ROOT%\scripts\websearch.py" run --source %SOURCE% --timeout %TIMEOUT%
set "PYTHONIOENCODING=utf-8"
%PYTHON_CMD% "%PROJECT_ROOT%\scripts\websearch.py" run --source %SOURCE% --timeout %TIMEOUT%
set "EXIT_CODE=%ERRORLEVEL%"

for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "(Get-Date).ToString('yyyy-MM-dd HH:mm:ss')"`) do set "DONE_AT=%%I"
echo [%LOG_TAG%] %DONE_AT% finished with exit code %EXIT_CODE%.
echo [%LOG_TAG%] detail log: %LOG_FILE%
>> "%LOG_FILE%" echo [%LOG_TAG%] %DONE_AT% finished with exit code %EXIT_CODE%.

endlocal & exit /b %EXIT_CODE%

:probe_python
if defined PYTHON_CMD exit /b 0
set "_PY_CAND=%~1"
if not defined _PY_CAND exit /b 0
%_PY_CAND% --version >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=%_PY_CAND%"
exit /b 0
