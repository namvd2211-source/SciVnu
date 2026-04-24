[CmdletBinding()]
param(
  [switch]$SkipBuild,
  [string]$AppDistDir = "dist\\ResearchCompanion"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

$releaseConfigPath = Join-Path $root "config\release_config.json"
if (-not (Test-Path -LiteralPath $releaseConfigPath)) {
  throw "Missing release configuration: $releaseConfigPath"
}
$releaseConfig = Get-Content -LiteralPath $releaseConfigPath -Raw | ConvertFrom-Json
$appVersion = [string]$releaseConfig.version

$makensisCandidates = @(
  (Join-Path $env:LOCALAPPDATA "tauri\NSIS\makensis.exe"),
  (Join-Path $env:LOCALAPPDATA "tauri\NSIS\Bin\makensis.exe"),
  "C:\Program Files (x86)\NSIS\makensis.exe",
  "C:\Program Files\NSIS\makensis.exe"
)

$makensis = $makensisCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $makensis) {
  throw "makensis.exe was not found. Install NSIS or use the Tauri-bundled NSIS first."
}

$distAppDir = Join-Path $root $AppDistDir
$distAppDirResolved = [System.IO.Path]::GetFullPath($distAppDir)
$distExe = Join-Path $distAppDir "ResearchCompanion.exe"
$distProxy = Join-Path $distAppDir "_internal\bin\cli-proxy-api.exe"
$nsisScript = Join-Path $root "packaging\ResearchCompanionSetup.nsi"
$distSetup = Join-Path $root "dist\ResearchCompanionSetup.exe"

if (-not $SkipBuild) {
  powershell -ExecutionPolicy Bypass -File .\scripts\build_companion_exe.ps1 -BuildMode onedir
}

if (-not (Test-Path -LiteralPath $distExe)) {
  throw "Onedir build is missing: $distExe"
}
if (-not (Test-Path -LiteralPath $distProxy)) {
  throw "Packaged CLI proxy binary is missing: $distProxy"
}
if (-not (Test-Path -LiteralPath $nsisScript)) {
  throw "NSIS script is missing: $nsisScript"
}

if (Test-Path -LiteralPath $distSetup) {
  Remove-Item -LiteralPath $distSetup -Force
}

& $makensis /V2 "/DAPP_SOURCE_DIR=$distAppDirResolved" "/DAPP_VERSION=$appVersion" $nsisScript

if (-not (Test-Path -LiteralPath $distSetup)) {
  throw "NSIS installer build did not produce: $distSetup"
}

Write-Host ""
Write-Host "NSIS setup completed (version $appVersion):" -ForegroundColor Green
Write-Host $distSetup
