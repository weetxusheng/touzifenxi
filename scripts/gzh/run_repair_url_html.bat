@echo off
setlocal EnableExtensions EnableDelayedExpansion

if "%~1"=="--help" goto usage
if "%~1"=="help" goto usage

call :monorepo_setup
if errorlevel 1 (
  pause
  exit /b 1
)

set "EXTRA="
if not "%~1"=="" set "EXTRA=--biz-date %~1"

echo Repo: %REPO_ROOT%
echo Repairing *.url.html under: %GZH_FETCH_OUTPUT_DIR%
echo.

%PYEXE% %PYVER% -m gzh_pipeline.cli.repair_url_html --exports-root "%GZH_FETCH_OUTPUT_DIR%" %EXTRA%
set "EXITCODE=!ERRORLEVEL!"
if not "!EXITCODE!"=="0" pause
exit /b !EXITCODE!

:usage
echo Usage:
echo   run_repair_url_html.bat [biz-date]
echo     Fix lazy-load images in existing *.url.html under PARSE_INPUT_ROOT / exports.
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
set "_stv=!%~1!"
:strip_tb_loop
if not "!_stv:~-1!"=="\" (
  set "%~1=!_stv!"
  exit /b 0
)
set "_stv=!_stv:~0,-1!"
goto strip_tb_loop
