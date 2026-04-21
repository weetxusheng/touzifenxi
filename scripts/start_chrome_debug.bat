@echo off
REM 以调试模式启动 Chrome，允许 DrissionPage 直接连接复用你的 session
REM 运行一次后保持 Chrome 开着，每次跑爬虫前确保此 Chrome 在运行即可

set CHROME="C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist %CHROME% set CHROME="C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"

%CHROME% --remote-debugging-port=9222 --user-data-dir="%LOCALAPPDATA%\Google\Chrome\User Data" https://36kr.com

echo Chrome 已以调试模式启动（端口 9222）
