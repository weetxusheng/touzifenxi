@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem Register email-related env vars from .env into user-level Windows env (setx).
rem Default scope: keys with prefix TOUZIFENXI_EMAIL_
rem Usage:
rem   register_kr36_email_env_windows.bat
rem   register_kr36_email_env_windows.bat "E:\AI\touzifenxi\.env"
rem   register_kr36_email_env_windows.bat --all
rem   register_kr36_email_env_windows.bat --dry-run

for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"

set "DOTENV_PATH="
set "REGISTER_ALL=0"
set "DRY_RUN=0"

:parse_args
if "%~1"=="" goto parsed
if /I "%~1"=="--all" (
  set "REGISTER_ALL=1"
  shift
  goto parse_args
)
if /I "%~1"=="--dry-run" (
  set "DRY_RUN=1"
  shift
  goto parse_args
)
if not defined DOTENV_PATH (
  set "DOTENV_PATH=%~1"
  shift
  goto parse_args
)
echo [ERROR] Unknown argument: %~1
echo [INFO] Usage: %~nx0 [dotenv_path] [--all] [--dry-run]
endlocal & exit /b 2

:parsed
if not defined DOTENV_PATH set "DOTENV_PATH=%PROJECT_ROOT%\.env"

if not exist "%DOTENV_PATH%" (
  echo [ERROR] .env file not found: "%DOTENV_PATH%"
  endlocal & exit /b 1
)

echo [INFO] dotenv: "%DOTENV_PATH%"
if "%REGISTER_ALL%"=="1" (
  echo [INFO] mode: register all keys
) else (
  echo [INFO] mode: register keys prefix TOUZIFENXI_EMAIL_
)
if "%DRY_RUN%"=="1" echo [INFO] dry-run enabled (no setx write)

set /a TOTAL=0
set /a UPDATED=0
set /a SKIPPED=0
set /a FAILED=0

for /f "usebackq tokens=1* delims==" %%A in ("%DOTENV_PATH%") do (
  set "KEY=%%A"
  set "VALUE=%%B"
  if not "!KEY!"=="" if not "!KEY:~0,1!"=="#" (
    set "REGISTER=0"
    if "%REGISTER_ALL%"=="1" (
      set "REGISTER=1"
    ) else (
      if /I "!KEY:~0,17!"=="TOUZIFENXI_EMAIL_" set "REGISTER=1"
    )
    if "!REGISTER!"=="1" (
      set /a TOTAL+=1
      if "%DRY_RUN%"=="1" (
        echo [DRY-RUN] setx !KEY! "<masked>"
        set /a UPDATED+=1
      ) else (
        setx !KEY! "!VALUE!" >nul
        if errorlevel 1 (
          echo [WARN] Failed: !KEY!
          set /a FAILED+=1
        ) else (
          echo [OK] !KEY!
          set /a UPDATED+=1
        )
      )
    ) else (
      set /a SKIPPED+=1
    )
  )
)

echo [DONE] matched=%TOTAL% updated=%UPDATED% failed=%FAILED% skipped=%SKIPPED%
echo [NOTE] setx writes to user environment for new terminals only.
echo [NOTE] Please reopen terminal/app before sending email.
endlocal & exit /b 0

