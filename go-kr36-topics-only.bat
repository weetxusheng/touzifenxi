@echo off
rem Run from repo root. Safe from PS:  .\go-kr36-topics-only.bat
rem (Avoid typing a full E:\AI\touzifenxi\... path in PowerShell without quotes.
rem  The sequence backslash + "t" can break unquoted invocations in PS.)
setlocal
cd /d "%~dp0"
call "scripts\kr36\run_kr36_topic_focus_download_windows.bat" %*
endlocal
