[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$Version
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if ($Version -notmatch '^\d+\.\d+\.\d+([-.][0-9A-Za-z]+)*$') {
  throw "Version must look like semantic versioning, for example 1.0.1 or 1.0.1-beta.1"
}

$releasePath = Join-Path $root "release_config.json"
$releaseConfig = Get-Content -LiteralPath $releasePath -Raw | ConvertFrom-Json
$releaseConfig.version = $Version
$releaseConfig | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $releasePath -Encoding utf8

$packagePath = Join-Path $root "package.json"
if (Test-Path -LiteralPath $packagePath) {
  $packageJson = Get-Content -LiteralPath $packagePath -Raw | ConvertFrom-Json
  $packageJson.version = $Version
  $packageJson | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $packagePath -Encoding utf8
}

Write-Host "Release version updated to $Version" -ForegroundColor Green
Write-Host "Next steps:"
Write-Host "1. Update CHANGELOG.md"
Write-Host "2. Run .\\prepare_github_release.ps1"
Write-Host "3. Create tag v$Version"
