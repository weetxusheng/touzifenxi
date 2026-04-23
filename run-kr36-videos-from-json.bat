@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="" (
  echo Usage: %~nx0 "path\to\kr36_hot_topics_YYYYMMDD.json"
  endlocal & exit /b 1
)
if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (set "PY=python")
set "PYTHONPATH=%CD%\src"
set "PYTHONIOENCODING=utf-8"
echo Running: "%PY%" -u -m kr36.cli kr36-videos-from-json --json "%~1"
"%PY%" -u -m kr36.cli kr36-videos-from-json --json "%~1"
endlocal & exit /b %ERRORLEVEL%
