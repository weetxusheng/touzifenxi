@echo off
setlocal
chcp 65001 >nul

for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"
if not defined PYTHON_BIN (
  if exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" (
    set "PYTHON_BIN=%PROJECT_ROOT%\.venv\Scripts\python.exe"
  ) else (
    set "PYTHON_BIN=python"
  )
)

cd /d "%PROJECT_ROOT%"
set "PYTHONPATH=src"
echo [kr36-cookie] starting manual cookie refresh...
"%PYTHON_BIN%" scripts\kr36\refresh_kr36_cookies_manual.py --url https://36kr.com/topics/ --second-url https://36kr.com/activity --wait-seconds 180
set "EXIT_CODE=%ERRORLEVEL%"
echo [kr36-cookie] finished with exit code %EXIT_CODE%.
endlocal & exit /b %EXIT_CODE%

