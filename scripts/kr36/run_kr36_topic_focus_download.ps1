#Requires -Version 5.0
# Kr36: kr36-topics-only. Run from repo root, e.g.:
#   .\scripts\kr36\run_kr36_topic_focus_download.ps1
#   .\scripts\kr36\run_kr36_topic_focus_download.ps1 2026-04-22
# Uses $PSScriptRoot so you never need to paste a long path (PS path/tab issues with touzifenxi).

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location -LiteralPath $ProjectRoot
$dateArg = if ($args.Count -ge 1 -and $args[0]) { $args[0] } else { (Get-Date -Format 'yyyy-MM-dd') }
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'config\runtime.local.json'))) {
    Write-Error "Missing config\runtime.local.json (copy from runtime.example.json)"
    exit 2
}
$venvPy = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$py = if (Test-Path -LiteralPath $venvPy) { $venvPy } else { 'python' }
$env:PYTHONPATH = (Join-Path $ProjectRoot 'src')
$env:PYTHONIOENCODING = 'utf-8'
$logDir = Join-Path $ProjectRoot 'logs\kr36'
if (-not (Test-Path -LiteralPath $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$runStamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$logFile = Join-Path $logDir "kr36_topics_only_$runStamp.log"
$env:KR36_LOG_FILE = $logFile
$env:KR36_LOG_ECHO_STDOUT = '1'
if (Test-Path (Join-Path $ProjectRoot '.env')) {
    # UTF-8 if file was saved that way; override if needed
    Get-Content (Join-Path $ProjectRoot '.env') -Encoding utf8 | ForEach-Object {
        if ($_ -match '^\s*#' -or $_ -notmatch '=') { return }
        $pair = $_.Split('=', 2)
        if ($pair.Count -eq 2) { Set-Item -Path "env:$($pair[0].Trim())" -Value $pair[1] }
    }
}
Write-Host "[kr36-topics-only] PROJECT_ROOT=$ProjectRoot"
Write-Host "[kr36-topics-only] python=$py DATE=$dateArg"
Add-Content -Path $logFile -Value "[kr36-topics-only] start DATE=$dateArg"
$proc = Start-Process -FilePath $py -ArgumentList @('-u', '-m', 'kr36.cli', 'kr36-topics-only', '--date', $dateArg) -WorkingDirectory $ProjectRoot -NoNewWindow -PassThru -Wait
$code = if ($null -ne $proc.ExitCode) { $proc.ExitCode } else { 1 }
Add-Content -Path $logFile -Value "[kr36-topics-only] exit=$code"
exit $code
