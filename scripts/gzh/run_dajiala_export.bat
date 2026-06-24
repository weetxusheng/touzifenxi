@echo off
setlocal EnableExtensions EnableDelayedExpansion

if "%~1"=="--help" goto usage
if "%~1"=="help" goto usage

call :monorepo_setup
if errorlevel 1 (
  pause
  exit /b 1
)

if not exist "%GZH_ACCOUNTS_FILE%" (
  echo Missing accounts file:
  echo   %GZH_ACCOUNTS_FILE%
  echo Copy example: copy "%GZH_CONFIG%\accounts.example.txt" "%GZH_ACCOUNTS_FILE%"
  pause
  exit /b 1
)

set "EXTRA_MODE="
set "EXTRA_MP="
set "BIZ_EXTRA="
if not "%~1"=="" set "EXTRA_MODE= --mode %~1"
if not "%~2"=="" set "EXTRA_MP= --max-pages %~2"
if not "%~3"=="" set "BIZ_EXTRA= --biz-date %~3"

echo Repo: %REPO_ROOT%
echo Accounts: %GZH_ACCOUNTS_FILE%
echo Output: %GZH_FETCH_OUTPUT_DIR%
echo Audit:  %GZH_EXPORT_AUDIT_DIR%
if not "%~1"=="" (echo Mode override ^(arg^): %~1) else (echo Mode: .env GZH_DAJIALA_FETCH_MODE, default today)
if not "%~2"=="" (echo Max pages override ^(arg^): %~2) else (echo Max pages: .env GZH_DAJIALA_FETCH_MAX_PAGES, default 1)
echo.

%PYEXE% %PYVER% -m gzh_pipeline.cli.fetch ^
  --accounts-file "%GZH_ACCOUNTS_FILE%" ^
  --output-dir "%GZH_FETCH_OUTPUT_DIR%" ^
  --audit-json-root "%GZH_EXPORT_AUDIT_DIR%" ^
  --json-summary%BIZ_EXTRA%%EXTRA_MODE%%EXTRA_MP%

set "EXITCODE=!ERRORLEVEL!"
if not "!EXITCODE!"=="0" (
  echo.
  echo ERROR: exit code=!EXITCODE!
  pause
)
exit /b !EXITCODE!

:usage
echo Usage:
echo   run_dajiala_export.bat
echo     Repo root .env + src\gzh\config\accounts.txt
echo     Output/audit dirs from .env PARSE_INPUT_ROOT / EXPORT_AUDIT_JSON_ROOT, or defaults.
echo.
echo   run_dajiala_export.bat [mode] [max_pages] [biz-date]
echo     Optional: today^|yesterday^|latest^|all, pages, yyyyMMdd folder
echo.
echo Setup:
echo   copy src\gzh\config\accounts.example.txt src\gzh\config\accounts.txt
echo   configure repo root .env
echo.
pause
exit /b 0

:monorepo_setup
set "GZH_SCRIPT_DIR=%~dp0"
if "%GZH_SCRIPT_DIR:~-1%"=="\" set "GZH_SCRIPT_DIR=%GZH_SCRIPT_DIR:~0,-1%"
for %%I in ("%GZH_SCRIPT_DIR%\..\..") do set "REPO_ROOT=%%~fI"
for %%I in ("%REPO_ROOT%\src\gzh") do set "GZH_ROOT=%%~fI"
set "GZH_SRC=%GZH_ROOT%\src"
set "GZH_CONFIG=%GZH_ROOT%\config"
set "GZH_ACCOUNTS_FILE=%GZH_CONFIG%\accounts.txt"
cd /d "%REPO_ROOT%"
if errorlevel 1 (
  echo ERROR: cannot cd to repo root: %REPO_ROOT%
  exit /b 1
)
if not exist "%REPO_ROOT%\.env" (
  echo Missing .env at repo root: %REPO_ROOT%\.env
  exit /b 1
)
set "GZH_FETCH_OUTPUT_DIR=exports"
set "GZH_PARSE_OUTPUT_DIR=parsed_exports"
set "GZH_EXPORT_AUDIT_DIR=audit_traces"
set "GZH_PARSE_AUDIT_DIR=audit_traces"
for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%REPO_ROOT%\.env") do (
  if /i "%%A"=="PARSE_INPUT_ROOT" if not "%%~B"=="" set "GZH_FETCH_OUTPUT_DIR=%%~B"
  if /i "%%A"=="PARSE_OUTPUT_ROOT" if not "%%~B"=="" set "GZH_PARSE_OUTPUT_DIR=%%~B"
  if /i "%%A"=="EXPORT_AUDIT_JSON_ROOT" if not "%%~B"=="" set "GZH_EXPORT_AUDIT_DIR=%%~B"
  if /i "%%A"=="PARSE_AUDIT_JSON_ROOT" if not "%%~B"=="" set "GZH_PARSE_AUDIT_DIR=%%~B"
)
call :strip_trailing_backslash GZH_FETCH_OUTPUT_DIR
call :strip_trailing_backslash GZH_PARSE_OUTPUT_DIR
call :strip_trailing_backslash GZH_EXPORT_AUDIT_DIR
call :strip_trailing_backslash GZH_PARSE_AUDIT_DIR
if not defined GZH_DAJIALA_ACCOUNTS_FILE set "GZH_DAJIALA_ACCOUNTS_FILE=%GZH_ACCOUNTS_FILE%"
set "PYTHONPATH=%GZH_SRC%;%PYTHONPATH%"
set "PYEXE=py"
set "PYVER=-3.13"
%PYEXE% %PYVER% -c "import gzh_pipeline" >nul 2>nul
if errorlevel 1 (
  set "PYVER="
  %PYEXE% -c "import gzh_pipeline" >nul 2>nul
  if errorlevel 1 (
    set "PYEXE=python"
    set "PYVER="
    %PYEXE% -c "import gzh_pipeline" >nul 2>nul
  )
)
if errorlevel 1 (
  echo Installing touzifenxi ^(editable, includes gzh_pipeline^)...
  %PYEXE% %PYVER% -m pip install -e "%REPO_ROOT%[dev]" -q
  if errorlevel 1 (
    echo ERROR: pip install -e failed.
    exit /b 1
  )
)
exit /b 0

:strip_trailing_backslash
rem .env 路径末尾 \ 会使 "--path \"%VAR%\"" 中的 \" 转义引号，导致后续参数被拼进路径（WinError 123）
set "_stv=!%~1!"
:strip_tb_loop
if not "!_stv:~-1!"=="\" (
  set "%~1=!_stv!"
  exit /b 0
)
set "_stv=!_stv:~0,-1!"
goto strip_tb_loop
