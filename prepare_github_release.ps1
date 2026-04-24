[CmdletBinding()]
param(
  [string]$DistRoot = "dist",
  [string]$BuildRoot = "build"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$releaseConfig = Get-Content -LiteralPath (Join-Path $root "release_config.json") -Raw | ConvertFrom-Json
$version = [string]$releaseConfig.version
$tagPrefix = [string]$releaseConfig.release_tag_prefix
$tagName = if ($tagPrefix) { "$tagPrefix$version" } else { $version }
$assetName = [string]$releaseConfig.release_asset_name
$githubRepo = [string]$releaseConfig.github_repo

Write-Host "Preparing release $tagName" -ForegroundColor Cyan
Write-Host "Building companion onedir..."
powershell -ExecutionPolicy Bypass -File .\build_companion_exe.ps1 -BuildMode onedir -DistRoot $DistRoot -BuildRoot $BuildRoot

Write-Host "Building NSIS installer..."
powershell -ExecutionPolicy Bypass -File .\build_companion_nsis_setup.ps1 -SkipBuild -AppDistDir "$DistRoot\ResearchCompanion"

Write-Host ""
Write-Host "Release artifacts ready:" -ForegroundColor Green
Write-Host (Join-Path $root "$DistRoot\ResearchCompanion\ResearchCompanion.exe")
Write-Host (Join-Path $root "dist\$assetName")
Write-Host ""
Write-Host "GitHub Release checklist:" -ForegroundColor Yellow
Write-Host "1. Commit code + changelog."
Write-Host "2. Create git tag $tagName."
if ($githubRepo) {
  Write-Host "3. Publish release in https://github.com/$githubRepo/releases/new"
} else {
  Write-Host "3. Set github_repo in release_config.json before enabling in-app auto-update."
}
Write-Host "4. Upload dist\$assetName to the release."
Write-Host "5. Keep the asset name stable so the in-app updater can find it."
