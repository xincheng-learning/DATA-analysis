# Manual entry point for the already-authorized isolated browser acceptance services.
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -lt 7) {
    $qaShell = Join-Path $env:ProgramFiles 'PowerShell/7/pwsh.exe'
    & $qaShell -NoProfile -File $PSCommandPath
    return
}
if ($PSVersionTable.PSVersion.ToString() -ne '7.6.5') {
    throw '请使用 PowerShell 7.6.5 运行此脚本。'
}
$qaRoot = Split-Path -Parent $PSScriptRoot
$qaLogs = Join-Path $qaRoot 'output/local-release-2026-11-14'
$qaBackendListener = Get-NetTCPConnection -State Listen -LocalPort 8101 -ErrorAction SilentlyContinue
if ($qaBackendListener) { throw '8101 已有服务。请把这个提示告诉 Codex，不要关闭其他程序。' }
$qaFrontendListener = Get-NetTCPConnection -State Listen -LocalPort 3100 -ErrorAction SilentlyContinue
$qaReuseFrontend = $false
if ($qaFrontendListener) {
    $qaFrontendPid = [int](Get-Content -LiteralPath (Join-Path $qaLogs 'qa-frontend.pid'))
    $qaFrontendProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($qaFrontendListener.OwningProcess)"
    if ($qaFrontendListener.OwningProcess -ne $qaFrontendPid -and $qaFrontendProcess.ParentProcessId -ne $qaFrontendPid) {
        throw '3100 被其他程序使用；未关闭该程序。请把这个提示告诉 Codex。'
    }
    $qaReuseFrontend = $true
}
New-Item -ItemType Directory -Path $qaLogs -Force | Out-Null
$env:PYTHONUTF8 = '1'
$env:CORS_ALLOWED_ORIGINS = 'http://127.0.0.1:3100,http://localhost:3100'
$qaBackend = Start-Process -FilePath (Join-Path $qaRoot '.venv/Scripts/python.exe') -ArgumentList @('-X','utf8','scripts/qa_local_server.py','--directory','.local/qa-release-20261114','--port','8101') -WorkingDirectory $qaRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $qaLogs 'backend.log') -RedirectStandardError (Join-Path $qaLogs 'backend-error.log') -PassThru
$qaBackend.Id | Set-Content (Join-Path $qaLogs 'qa-backend.pid')
$env:NEXT_PUBLIC_API_URL = 'http://127.0.0.1:8101'
$env:NEXT_PUBLIC_APP_MODE = 'local'
$env:NEXT_DIST_DIR = '.local/qa-release-20261114'
if (-not $qaReuseFrontend) {
    $qaFrontend = Start-Process -FilePath (Get-Command node).Source -ArgumentList @('node_modules/next/dist/bin/next','dev','--webpack','-p','3100','-H','127.0.0.1') -WorkingDirectory (Join-Path $qaRoot 'frontend') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $qaLogs 'frontend.log') -RedirectStandardError (Join-Path $qaLogs 'frontend-error.log') -PassThru
    $qaFrontend.Id | Set-Content (Join-Path $qaLogs 'qa-frontend.pid')
}
Write-Host '已启动隔离验收服务：http://127.0.0.1:3100。请回到 Codex 告诉我“已启动”；首屏编译可能需要约一分钟。'
