@echo off
setlocal

cd /d "%~dp0"

if "%FEEDCORE_FETCHER_TOKEN%"=="" (
  echo [WARN] FEEDCORE_FETCHER_TOKEN is not set. Remote APIs will run without bearer-token protection.
)

python -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

python run_fetcher.py --host 0.0.0.0 --port 3000 --output-dir remote_output
