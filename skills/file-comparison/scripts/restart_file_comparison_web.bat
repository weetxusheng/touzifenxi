@echo off
setlocal EnableExtensions

REM Mirrors restart_file_comparison_web.sh: scripts\ -> skill root -> repo root
pushd "%~dp0"
cd /d ".."
set "SKILL_ROOT=%CD%"
cd /d "..\.."
set "PROJECT_ROOT=%CD%"
popd

set "SCRIPT_DIR=%~dp0"
set "VENV_PYTHON=%PROJECT_ROOT%\.venv\Scripts\python.exe"
set "SCRIPT_PY=%SKILL_ROOT%\scripts\restart_file_comparison_web.py"

set "PYTHON_BIN=%VENV_PYTHON%"
if exist "%PYTHON_BIN%" goto :paths_done

where python >nul 2>&1
if errorlevel 1 (
  echo ERROR: No venv Python at "%VENV_PYTHON%" and no python on PATH.
  exit /b 1
)
set "PYTHON_BIN=python"

:paths_done
echo SCRIPT_DIR=%SCRIPT_DIR%
echo SKILL_ROOT=%SKILL_ROOT%
echo PROJECT_ROOT=%PROJECT_ROOT%
echo VENV_PYTHON=%VENV_PYTHON%
echo PYTHON_BIN=%PYTHON_BIN%
echo SCRIPT_PY=%SCRIPT_PY%
echo.

cd /d "%SKILL_ROOT%"
"%PYTHON_BIN%" scripts\restart_file_comparison_web.py %*
exit /b %ERRORLEVEL%
