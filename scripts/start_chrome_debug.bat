@echo off
REM 以调试模式启动 Chrome，允许 DrissionPage 直接连接复用你的 session
REM 运行一次后保持 Chrome 开着，每次跑爬虫前确保此 Chrome 在运行即可

set "CHROME="

REM 1. 优先从 PATH 中自动定位 chrome
for /f "delims=" %%I in ('where chrome 2^>nul') do (
  if not defined CHROME set "CHROME=%%I"
)

REM 2. 回退到标准安装位置（64 位）
if not defined CHROME (
  if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" (
    set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
  )
)

REM 3. 回退到标准安装位置（32 位兼容路径）
if not defined CHROME (
  if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" (
    set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
  )
)

REM 4. 尝试从注册表读取安装路径
if not defined CHROME (
  for /f "tokens=2*" %%A in ('reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" /ve 2^>nul') do (
    if not defined CHROME set "CHROME=%%B"
  )
)
if not defined CHROME (
  for /f "tokens=2*" %%A in ('reg query "HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" /ve 2^>nul') do (
    if not defined CHROME set "CHROME=%%B"
  )
)

if not defined CHROME (
  echo [ERROR] 未找到 Chrome，请确认已安装或将 chrome.exe 所在目录加入 PATH。
  exit /b 1
)

echo [INFO] 使用 Chrome: %CHROME%
"%CHROME%" --remote-debugging-port=9222 --user-data-dir="%LOCALAPPDATA%\Google\Chrome\User Data" https://36kr.com

echo Chrome 已以调试模式启动（端口 9222）
