@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion

rem Repo root = scripts -> one level up from this file.
for %%I in ("%~dp0..") do set "PROJECT_ROOT=%%~fI"

set "PYTHON_CMD="
if defined PYTHON_BIN if exist "!PYTHON_BIN!" call :probe_python ""!PYTHON_BIN!""
if not defined PYTHON_CMD if exist "!PROJECT_ROOT!\.venv\Scripts\python.exe" call :probe_python ""!PROJECT_ROOT!\.venv\Scripts\python.exe""
if not defined PYTHON_CMD if exist "!PROJECT_ROOT!\venv\Scripts\python.exe" call :probe_python ""!PROJECT_ROOT!\venv\Scripts\python.exe""
if not defined PYTHON_CMD call :probe_python "python"
if not defined PYTHON_CMD call :probe_python "py -3"

if not defined PYTHON_CMD (
  echo [deps] ERROR: No runnable Python found. Tried PYTHON_BIN/.venv/venv/python/py -3.
  endlocal & exit /b 2
)

cd /d "!PROJECT_ROOT!"
echo [deps] PROJECT_ROOT=!PROJECT_ROOT!
echo [deps] PYTHON_CMD=!PYTHON_CMD!

echo [deps] upgrade pip/setuptools/wheel...
!PYTHON_CMD! -m pip install -U pip setuptools wheel
if errorlevel 1 (
  echo [deps] ERROR: failed while upgrading pip tooling.
  endlocal & exit /b 3
)

echo [deps] install project dependencies (editable + dev extras)...
!PYTHON_CMD! -m pip install -e ".[dev]"
if errorlevel 1 (
  echo [deps] ERROR: failed while installing project dependencies.
  endlocal & exit /b 4
)

echo [deps] install OpenCV runtime...
!PYTHON_CMD! -m pip install -U opencv-python
if errorlevel 1 (
  echo [deps] ERROR: failed while installing opencv-python.
  endlocal & exit /b 5
)

echo [deps] ensure Playwright browser exists (chromium)...
!PYTHON_CMD! -m playwright install chromium
if errorlevel 1 (
  echo [deps] ERROR: failed while installing Playwright chromium.
  endlocal & exit /b 6
)

echo [deps] verify imports: c114.cli / cv2 / playwright...
!PYTHON_CMD! -c "import c114.cli; import cv2; from playwright.sync_api import sync_playwright"
if errorlevel 1 (
  echo [deps] ERROR: import check failed. Re-run with visible traceback:
  echo [deps]       !PYTHON_CMD! -c "import c114.cli; import cv2; from playwright.sync_api import sync_playwright"
  endlocal & exit /b 7
)

echo [deps] SUCCESS: all dependencies are ready in current environment.
endlocal & exit /b 0

:probe_python
if defined PYTHON_CMD exit /b 0
set "_PY_CAND=%~1"
if not defined _PY_CAND exit /b 0
%_PY_CAND% --version >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=%_PY_CAND%"
exit /b 0
