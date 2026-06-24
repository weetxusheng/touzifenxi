@echo off
setlocal EnableExtensions EnableDelayedExpansion

if "%~1"=="--help" goto usage
if "%~1"=="help" goto usage

call :monorepo_setup
if errorlevel 1 (
  pause
  exit /b 1
)

set "BIZ=%~1"
set "ACC=%~2"
set "STEM=summary"
set "WAIT_END="

if /i "%~3"=="WAIT" (
  set "WAIT_END=1"
)
if not "!WAIT_END!"=="1" (
  if not "%~3"=="" set "STEM=%~3"
)
if /i "%~4"=="WAIT" set "WAIT_END=1"

set "EXTRA_ARGS="
if /i "!PARSE_SKIP_IF_SAME!"=="1" set "EXTRA_ARGS=--idempotent-skip"
if not "!BIZ!"=="" set "EXTRA_ARGS=!EXTRA_ARGS! --biz-date !BIZ!"
if not "!ACC!"=="" set "EXTRA_ARGS=!EXTRA_ARGS! --account !ACC!"
if not "!STEM!"=="summary" set "EXTRA_ARGS=!EXTRA_ARGS! --output-stem !STEM!"

set "PYTHONUNBUFFERED=1"
if "!GZH_PARSE_PROGRESS!"=="" set "GZH_PARSE_PROGRESS=1"

echo Repo: %REPO_ROOT%
echo Input:  %GZH_FETCH_OUTPUT_DIR%
echo Output: %GZH_PARSE_OUTPUT_DIR%
echo Audit:  %GZH_PARSE_AUDIT_DIR%
if "!BIZ!"=="" if "!ACC!"=="" (
  echo Running parse: biz_date from .env GZH_DAJIALA_FETCH_MODE [today=当天目录, yesterday=前一天目录], all accounts
) else (
  echo Running parse biz_date=!BIZ! account=!ACC! output_stem=!STEM! PARSE_SKIP_IF_SAME=!PARSE_SKIP_IF_SAME!
)
echo.

%PYEXE% %PYVER% -u -m gzh_pipeline.cli.parse ^
  --input-root "%GZH_FETCH_OUTPUT_DIR%" ^
  --output-root "%GZH_PARSE_OUTPUT_DIR%" ^
  --audit-json-root "%GZH_PARSE_AUDIT_DIR%" ^
  %EXTRA_ARGS% --json

set "EXITCODE=!ERRORLEVEL!"
if not "!EXITCODE!"=="0" (
  echo.
  echo ERROR: exit code=!EXITCODE!
  pause
  exit /b !EXITCODE!
)
echo.

:: 生成当日总览页面
if "!BIZ!"=="" (
  :: 如果未指定 biz_date，尝试从 .env 中的 GZH_DAJIALA_FETCH_MODE 获取日期
  set "AUTO_BIZ="
  for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%REPO_ROOT%\.env") do (
    if /i "%%A"=="GZH_DAJIALA_FETCH_MODE" if not "%%~B"=="" set "FETCH_MODE=%%~B"
  )
  
  if "!FETCH_MODE!"=="today" (
    for /f "tokens=2 delims==" %%I in ('wmic OS Get localdatetime /value') do set "TODAY=%%I"
    set "AUTO_BIZ=!TODAY:~0,8!"
  ) else if "!FETCH_MODE!"=="yesterday" (
    :: 计算昨天日期（简化版，假设当前为 2026 年）
    for /f "tokens=2 delims==" %%I in ('wmic OS Get localdatetime /value') do set "NOW=%%I"
    set "YEAR=!NOW:~0,4!"
    set "MONTH=!NOW:~4,2!"
    set "DAY=!NOW:~6,2!"
    set /a "YESTERDAY_DAY=!DAY! - 1"
    if !YESTERDAY_DAY! LSS 10 set "YESTERDAY_DAY=0!YESTERDAY_DAY!"
    set "AUTO_BIZ=!YEAR!!MONTH!!YESTERDAY_DAY!"
  )
  
  if "!AUTO_BIZ!"=="" (
    echo [WARNING] biz_date not specified and cannot auto-detect from GZH_DAJIALA_FETCH_MODE, skipping overview generation
  ) else (
    echo Generating overview page for !AUTO_BIZ! ^(auto-detected from FETCH_MODE=!FETCH_MODE!^)...
    %PYEXE% %PYVER% -u -m gzh_pipeline.cli.overview ^
      --biz-date "!AUTO_BIZ!" ^
      --output-root "%GZH_PARSE_OUTPUT_DIR%" ^
      --audit-json-root "%GZH_PARSE_AUDIT_DIR%"
    if errorlevel 1 (
      echo [WARNING] Overview generation failed with exit code !ERRORLEVEL!
    ) else (
      echo Overview page generated successfully.
    )
  )
) else (
  echo Generating overview page for !BIZ!...
  %PYEXE% %PYVER% -u -m gzh_pipeline.cli.overview ^
    --biz-date "!BIZ!" ^
    --output-root "%GZH_PARSE_OUTPUT_DIR%" ^
    --audit-json-root "%GZH_PARSE_AUDIT_DIR%"
  if errorlevel 1 (
    echo [WARNING] Overview generation failed with exit code !ERRORLEVEL!
  ) else (
    echo Overview page generated successfully.
  )
)

echo.
if "!WAIT_END!"=="1" pause
exit /b 0

:usage
echo Usage:
echo   run_aggregate_parse.bat
echo     Parses HTML under input root ^(.env PARSE_INPUT_ROOT or exports^).
echo.
echo   run_aggregate_parse.bat [biz-date] [account] [output-stem] [WAIT]
echo     Optional biz-date yyyyMMdd, account folder name, output stem, WAIT=pause at end
echo.
echo Env: repo root .env ^(PARSE_* / GZH_* / DEEPSEEK_*^)
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
