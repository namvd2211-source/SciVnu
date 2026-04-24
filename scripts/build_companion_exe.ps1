[CmdletBinding()]
param(
  [ValidateSet("onefile", "onedir")]
  [string]$BuildMode = "onefile",
  [switch]$KeepLegacyArtifacts,
  [string]$DistRoot = "dist",
  [string]$BuildRoot = "build"
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

$vendorDir = Join-Path $root "vendor\cli-proxy-api"
$vendorBinary = Join-Path $vendorDir "cli-proxy-api.exe"
$vendorSourceMarker = Join-Path $vendorDir "upstream.txt"
$proxyBinaryTargetDir = Join-Path $root "packaging\bin"
$proxyBinaryTarget = Join-Path $proxyBinaryTargetDir "cli-proxy-api.exe"
$distDir = Join-Path $root $DistRoot
$buildDir = Join-Path $root $BuildRoot
$cliProxyRepo = "router-for-me/CLIProxyAPIPlus"
$rootSpecArtifact = Join-Path $root "ResearchCompanion.spec"

function Remove-BuildArtifact {
  param(
    [string]$Path
  )

  if (Test-Path -LiteralPath $Path) {
    try {
      Remove-Item -LiteralPath $Path -Recurse -Force
    }
    catch {
      throw "Could not remove build artifact '$Path'. Close any running ResearchCompanion app or Explorer window using that folder, then run the build again."
    }
  }
}

function Clear-LegacyBuildArtifacts {
  if ($KeepLegacyArtifacts) {
    return
  }

  Get-ChildItem -Path $root -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^(build|dist)_rebuild\d*$' } |
    ForEach-Object {
      Write-Host "Removing legacy build artifact: $($_.FullName)" -ForegroundColor DarkYellow
      Remove-BuildArtifact -Path $_.FullName
    }
}

function Get-CliProxyBinary {
  param(
    [string]$DestinationPath
  )

  $currentSource = ""
  if (Test-Path -LiteralPath $vendorSourceMarker) {
    $currentSource = (Get-Content -LiteralPath $vendorSourceMarker -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
  }

  if ((Test-Path -LiteralPath $DestinationPath) -and $currentSource -eq $cliProxyRepo) {
    Write-Host "Using vendored CLIProxyAPI binary:" -ForegroundColor Cyan
    Write-Host $DestinationPath
    return
  }

  Write-Host "Downloading CLIProxyAPIPlus release for Research Companion..." -ForegroundColor Yellow
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $DestinationPath) | Out-Null

  $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$cliProxyRepo/releases/latest" -Headers @{ "User-Agent" = "ResearchCompanionBuild" }
  $asset = $release.assets | Where-Object { $_.name -match "^CLIProxyAPIPlus_.*_windows_amd64\.zip$" } | Select-Object -First 1
  if (-not $asset) {
    throw "Could not find a Windows amd64 CLIProxyAPIPlus release asset."
  }

  $zipPath = Join-Path ([System.IO.Path]::GetTempPath()) ("cli-proxy-api-" + [System.Guid]::NewGuid().ToString("N") + ".zip")
  $extractDir = Join-Path ([System.IO.Path]::GetTempPath()) ("cli-proxy-api-" + [System.Guid]::NewGuid().ToString("N"))

  try {
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zipPath -Headers @{ "User-Agent" = "ResearchCompanionBuild" }
    Expand-Archive -LiteralPath $zipPath -DestinationPath $extractDir -Force
    $exe = Get-ChildItem -Path $extractDir -Recurse -Filter *.exe | Select-Object -First 1
    if (-not $exe) {
      throw "Downloaded CLIProxyAPI archive did not contain an executable."
    }
    Copy-Item -LiteralPath $exe.FullName -Destination $DestinationPath -Force
    Set-Content -LiteralPath $vendorSourceMarker -Value $cliProxyRepo -Encoding ascii
  }
  finally {
    Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $extractDir -Recurse -Force -ErrorAction SilentlyContinue
  }

  Write-Host "Vendored CLIProxyAPI binary saved to:" -ForegroundColor Green
  Write-Host $DestinationPath
}

Get-CliProxyBinary -DestinationPath $vendorBinary

New-Item -ItemType Directory -Force -Path $proxyBinaryTargetDir | Out-Null
Copy-Item -LiteralPath $vendorBinary -Destination $proxyBinaryTarget -Force

Clear-LegacyBuildArtifacts

if ($BuildMode -eq "onedir") {
  Remove-BuildArtifact -Path (Join-Path $distDir "ResearchCompanion")
  Remove-BuildArtifact -Path (Join-Path $buildDir "ResearchCompanion")
} else {
  Remove-BuildArtifact -Path (Join-Path $distDir "ResearchCompanion.exe")
  Remove-BuildArtifact -Path (Join-Path $buildDir "ResearchCompanion")
}

python -m pip install --upgrade pyinstaller
if ($LASTEXITCODE -ne 0) {
  throw "Failed to install or upgrade pyinstaller."
}
python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
  throw "Failed to install build requirements."
}

$pyInstallerArgs = @(
  "-m", "PyInstaller",
  "--noconfirm",
  "--clean",
  "--windowed",
  "--distpath", $distDir,
  "--workpath", $buildDir,
  "--hidden-import", "backend.backend_api",
  "--hidden-import", "backend.backend_core",
  "--hidden-import", "desktop.local_companion_runtime",
  "--hidden-import", "config.release_config",
  "--hidden-import", "uvicorn",
  "--add-binary", "packaging\bin\cli-proxy-api.exe;bin",
  "--add-data", "desktop\ui;companion_ui",
  "--add-data", "web;web",
  "--add-data", "backend\backend_api.py;backend_runtime",
  "--add-data", "backend\backend_core.py;backend_runtime",
  "--add-data", "config\release_config.json;config",
  "--add-data", "CHANGELOG.md;release_assets",
  "--name", "ResearchCompanion"
)

if ($BuildMode -eq "onedir") {
  $pyInstallerArgs += "--onedir"
} else {
  $pyInstallerArgs += "--onefile"
}

$pyInstallerArgs += "desktop\companion_gui.py"

python @pyInstallerArgs
if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller build failed."
}

if (Test-Path -LiteralPath $rootSpecArtifact) {
  Remove-Item -LiteralPath $rootSpecArtifact -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "Build completed ($BuildMode, version $appVersion):" -ForegroundColor Green
if ($BuildMode -eq "onedir") {
  Write-Host (Join-Path $distDir "ResearchCompanion\\ResearchCompanion.exe")
} else {
  Write-Host (Join-Path $distDir "ResearchCompanion.exe")
}
