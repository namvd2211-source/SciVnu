$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

Write-Host "Starting Research Companion local stack..." -ForegroundColor Cyan
Write-Host "Backend: http://127.0.0.1:8787" -ForegroundColor Cyan
Write-Host "Proxy config will be generated under %LOCALAPPDATA%\\ResearchCompanion" -ForegroundColor Cyan

python -u -m desktop.companion_gui --backend
