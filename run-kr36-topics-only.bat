@echo off
setlocal EnableExtensions
rem Standalone: only kr36-topics-only. Run from any cwd by double-click or:
rem   run-kr36-topics-only.bat
rem   run-kr36-topics-only.bat 2026-04-22
cd /d "%~dp0"
if not exist "config\runtime.local.json" (
  echo [ERROR] Missing config\runtime.local.json
  endlocal & exit /b 2
)
if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (set "PY=python")
set "PYTHONPATH=%CD%\src"
set "PYTHONIOENCODING=utf-8"
if "%~1"=="" (
  for /f "delims=" %%D in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "TARGET_DATE=%%D"
) else set "TARGET_DATE=%~1"
echo [kr36-topics-only] repo=%CD%
echo [kr36-topics-only] date=%TARGET_DATE%
echo.
"%PY%" -u -m kr36.cli kr36-topics-only --date %TARGET_DATE%
set "EC=%ERRORLEVEL%"
echo.
if %EC% neq 0 (echo [kr36-topics-only] failed exit=%EC%) else (echo [kr36-topics-only] done.)
endlocal & exit /b %EC%
