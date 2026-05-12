@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion

set "SOURCE=%~1"
if "%~1"=="" set "SOURCE=c114"
set "LOG_TAG=!SOURCE!"

rem HTTP 单次请求超时（秒）。默认 300。仅可设为纯数字；启动前执行 set C114_PRT=180 即可覆盖（勿把本说明整行拷进 .env）。
if not defined C114_PRT set "C114_PRT=300"
call :sanitize_c114_prt

rem Repo root = scripts/c114 -> two levels up from this file.
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"
set "LOG_DIR=!PROJECT_ROOT!\logs\c114"
if not exist "!LOG_DIR!" mkdir "!LOG_DIR!"
set "LOG_FILE=!LOG_DIR!\!LOG_TAG!.log"

set "PYTHON_CMD="
if defined PYTHON_BIN if exist "!PYTHON_BIN!" call :probe_python ""!PYTHON_BIN!""
if not defined PYTHON_CMD if exist "!PROJECT_ROOT!\.venv\Scripts\python.exe" call :probe_python ""!PROJECT_ROOT!\.venv\Scripts\python.exe""
if not defined PYTHON_CMD if exist "!PROJECT_ROOT!\venv\Scripts\python.exe" call :probe_python ""!PROJECT_ROOT!\venv\Scripts\python.exe""
if not defined PYTHON_CMD call :probe_python "python"
if not defined PYTHON_CMD call :probe_python "py -3"

cd /d "!PROJECT_ROOT!"
set "PYTHONPATH=!PROJECT_ROOT!\src"
rem Python 重定向到日志文件时默认可能用系统 ANSI 编码；与 UTF-8 混写会乱码。
if not defined PYTHONUTF8 set "PYTHONUTF8=1"
if not defined PYTHONIOENCODING set "PYTHONIOENCODING=utf-8"
echo [!LOG_TAG!] %date% %time% starting daily brief run (no email)...
echo [!LOG_TAG!] PROJECT_ROOT=!PROJECT_ROOT!
echo [!LOG_TAG!] PYTHON_CMD=!PYTHON_CMD!
echo [!LOG_TAG!] per-request timeout=!C114_PRT!s
> "!LOG_FILE!" echo [!LOG_TAG!] %date% %time% starting daily brief run (no email)...
>> "!LOG_FILE!" echo [!LOG_TAG!] PROJECT_ROOT=!PROJECT_ROOT!
>> "!LOG_FILE!" echo [!LOG_TAG!] PYTHON_CMD=!PYTHON_CMD!
>> "!LOG_FILE!" echo [!LOG_TAG!] per-request timeout=!C114_PRT!s

if not defined PYTHON_CMD (
  echo [!LOG_TAG!] ERROR: No runnable Python found. Tried PYTHON_BIN/.venv/venv/python/py -3.
  >> "!LOG_FILE!" echo [!LOG_TAG!] ERROR: No runnable Python found. Tried PYTHON_BIN/.venv/venv/python/py -3.
  endlocal & exit /b 2
)

!PYTHON_CMD! -c "import c114.cli" >nul 2>&1
if errorlevel 1 (
  echo [!LOG_TAG!] ERROR: Python env missing runtime dependency. Run: pip install -e .
  >> "!LOG_FILE!" echo [!LOG_TAG!] ERROR: Python env missing runtime dependency. Run: pip install -e .
  endlocal & exit /b 3
)

!PYTHON_CMD! -c "import cv2" >nul 2>&1
if errorlevel 1 (
  echo [!LOG_TAG!] ERROR: OpenCV ^(cv2^) is missing in current Python env.
  echo [!LOG_TAG!] HINT: run scripts\install_python_deps_windows.bat
  >> "!LOG_FILE!" echo [!LOG_TAG!] ERROR: OpenCV ^(cv2^) is missing in current Python env.
  >> "!LOG_FILE!" echo [!LOG_TAG!] HINT: run scripts\install_python_deps_windows.bat
  endlocal & exit /b 4
)

if exist "!PROJECT_ROOT!\.env" (
  for /f "usebackq tokens=1* delims==" %%A in ("!PROJECT_ROOT!\.env") do (
    if not "%%A"=="" if not "%%A:~0,1"=="#" set "%%A=%%B"
  )
)
call :sanitize_c114_prt

rem Append Python stderr (tracebacks) to logs/c114/<source>.log
!PYTHON_CMD! "!PROJECT_ROOT!\scripts\websearch.py" run --source !SOURCE! --timeout !C114_PRT! 2>> "!LOG_FILE!"
set "EXIT_CODE=%ERRORLEVEL%"

if not "!EXIT_CODE!"=="0" (
  echo [!LOG_TAG!] Python stderr was appended to: !LOG_FILE!
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

:sanitize_c114_prt
rem --timeout 必须为纯数字，否则 argparse 会把多余词当成非法参数。
echo(!C114_PRT!| findstr /r "^[0-9][0-9]*$" >nul || set "C114_PRT=300"
if "!C114_PRT!"=="" set "C114_PRT=300"
exit /b 0
