@echo off
setlocal EnableDelayedExpansion

rem Run FeedCore news brief via a Python profile script (Windows scheduled-task friendly).
rem
rem Usage:
rem   run_feedcore_news_brief.bat                 default profile = general_news
rem   run_feedcore_news_brief.bat ai_news         use scripts\feedcore_ai_news.py
rem   run_feedcore_news_brief.bat <profile_name>  use scripts\feedcore_<profile_name>.py
rem
rem Reads MODEL, BASE_URL, and API_KEY from .env (LLM credentials), plus
rem FEEDCORE_REMOTE_FETCHER_URL / FEEDCORE_FETCHER_TOKEN for the remote step1-3 service.

cd /d "%~dp0\.."

set "PROFILE=%~1"
if "%PROFILE%"=="" set "PROFILE=general_news"

set "SHOULD_PAUSE=0"
if "%~1"=="" set "SHOULD_PAUSE=1"

if not exist ".env" (
  echo [ERROR] Missing .env file. Please create .env and set MODEL, BASE_URL, and API_KEY.
  if "!SHOULD_PAUSE!"=="1" pause
  exit /b 1
)

set "PROFILE_SCRIPT=scripts\feedcore_%PROFILE%.py"
if not exist "%PROFILE_SCRIPT%" (
  echo [ERROR] Profile script not found: %PROFILE_SCRIPT%
  if "!SHOULD_PAUSE!"=="1" pause
  exit /b 1
)

python "%PROFILE_SCRIPT%"
if errorlevel 1 (
  echo [ERROR] feedcore brief failed.
  if "!SHOULD_PAUSE!"=="1" pause
  exit /b 1
)

echo [OK] Brief done. See output\reports\feedcore_report\.
if "!SHOULD_PAUSE!"=="1" pause
