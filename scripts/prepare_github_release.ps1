[CmdletBinding()]
param(
  [string]$DistRoot = "dist",
  [string]$BuildRoot = "build",
  [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

function Get-FileSha256 {
  param([Parameter(Mandatory = $true)][string]$Path)
  return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Set-Utf8NoBomContent {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Content
  )
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

function New-ReleaseAssetInfo {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Platform,
    [Parameter(Mandatory = $true)][string]$Arch,
    [Parameter(Mandatory = $true)][string]$Kind,
    [string]$Notes = ""
  )
  $item = Get-Item -LiteralPath $Path
  return [ordered]@{
    name = $item.Name
    platform = $Platform
    arch = $Arch
    kind = $Kind
    size = $item.Length
    sha256 = Get-FileSha256 -Path $item.FullName
    notes = $Notes
  }
}

function Get-ChangelogSection {
  param(
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$ChangelogPath
  )
  if (-not (Test-Path -LiteralPath $ChangelogPath)) {
    return ""
  }
  $text = Get-Content -LiteralPath $ChangelogPath -Raw
  $escaped = [regex]::Escape($Version)
  $pattern = "(?ms)^## \[$escaped\].*?\r?\n(?<body>.*?)(?=^## \[|\z)"
  $match = [regex]::Match($text, $pattern)
  if (-not $match.Success) {
    return ""
  }
  return $match.Groups["body"].Value.Trim()
}

function New-ReleaseBody {
  param(
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$TagName,
    [Parameter(Mandatory = $true)][string]$GithubRepo,
    [Parameter(Mandatory = $true)][array]$Assets,
    [Parameter(Mandatory = $true)][string]$ChangelogPath
  )
  $changelog = Get-ChangelogSection -Version $Version -ChangelogPath $ChangelogPath
  if (-not $changelog) {
    if ($GithubRepo) {
      $changelog = "See the [commits](https://github.com/$GithubRepo/commits/$TagName) for details."
    } else {
      $changelog = "See the commits for details."
    }
  }

  $downloadRows = @()
  foreach ($asset in $Assets) {
    $label = switch ($asset.kind) {
      "installer" { "Windows x64 Installer" }
      "portable" { "Windows x64 Portable" }
      "metadata" { "Update Metadata" }
      default { $asset.kind }
    }
    if ($asset.name -eq "ResearchCompanionSetup.exe") {
      $label = "Windows Auto-update Installer"
    }
    $downloadRows += "| $label | ``$($asset.name)`` | $($asset.notes) |"
  }

  return @"
## What's New

$changelog

## Downloads

| Platform | Download | Notes |
|----------|----------|-------|
$($downloadRows -join "`n")

## Verification

- Built the Research Companion PyInstaller onedir bundle.
- Built the NSIS Windows installer.
- Generated versioned installer, portable zip, and latest.json metadata.
"@
}

$releaseConfigPath = Join-Path $root "config\release_config.json"
$releaseConfig = Get-Content -LiteralPath $releaseConfigPath -Raw | ConvertFrom-Json
$version = [string]$releaseConfig.version
$tagPrefix = [string]$releaseConfig.release_tag_prefix
$tagName = if ($tagPrefix) { "$tagPrefix$version" } else { $version }
$assetName = [string]$releaseConfig.release_asset_name
$githubRepo = [string]$releaseConfig.github_repo
$channel = [string]$releaseConfig.release_channel
$releaseNotesFile = [string]$releaseConfig.release_notes_file
$changelogPath = Join-Path -Path $root -ChildPath $releaseNotesFile

$distPath = Join-Path $root $DistRoot
$appDistPath = Join-Path $distPath "ResearchCompanion"
$stableInstallerPath = Join-Path $root "dist\$assetName"
$versionedInstallerName = "ResearchCompanion_${version}_x64-setup.exe"
$versionedInstallerPath = Join-Path $root "dist\$versionedInstallerName"
$portableName = "ResearchCompanion_v${version}_x64_portable.zip"
$portablePath = Join-Path $root "dist\$portableName"
$latestJsonPath = Join-Path $root "dist\latest.json"
$releaseBodyPath = Join-Path $root "dist\release-notes-$tagName.md"

Write-Host "Preparing release $tagName" -ForegroundColor Cyan
if (-not $SkipBuild) {
  Write-Host "Building companion onedir..."
  powershell -ExecutionPolicy Bypass -File .\scripts\build_companion_exe.ps1 -BuildMode onedir -DistRoot $DistRoot -BuildRoot $BuildRoot

  Write-Host "Building NSIS installer..."
  powershell -ExecutionPolicy Bypass -File .\scripts\build_companion_nsis_setup.ps1 -SkipBuild -AppDistDir "$DistRoot\ResearchCompanion"
} else {
  Write-Host "Skipping build and reusing existing dist artifacts..."
}

if (-not (Test-Path -LiteralPath $stableInstallerPath)) {
  throw "Stable installer is missing: $stableInstallerPath"
}
if (-not (Test-Path -LiteralPath $versionedInstallerPath)) {
  throw "Versioned installer is missing: $versionedInstallerPath"
}
if (-not (Test-Path -LiteralPath $appDistPath)) {
  throw "Onedir build is missing: $appDistPath"
}

Write-Host "Creating portable zip..."
if (Test-Path -LiteralPath $portablePath) {
  Remove-Item -LiteralPath $portablePath -Force
}
Compress-Archive -Path (Join-Path $appDistPath "*") -DestinationPath $portablePath -CompressionLevel Optimal

$assets = @(
  (New-ReleaseAssetInfo -Path $stableInstallerPath -Platform "windows" -Arch "x64" -Kind "installer" -Notes "Stable filename used by the in-app updater."),
  (New-ReleaseAssetInfo -Path $versionedInstallerPath -Platform "windows" -Arch "x64" -Kind "installer" -Notes "Recommended download for manual installation."),
  (New-ReleaseAssetInfo -Path $portablePath -Platform "windows" -Arch "x64" -Kind "portable" -Notes "No installer; unzip and run ResearchCompanion.exe.")
)

$latestPayload = [ordered]@{
  version = $version
  tag = $tagName
  channel = $channel
  repo = $githubRepo
  generated_at = (Get-Date).ToUniversalTime().ToString("o")
  assets = $assets
}
Set-Utf8NoBomContent -Path $latestJsonPath -Content ($latestPayload | ConvertTo-Json -Depth 8)
$metadataAsset = New-ReleaseAssetInfo -Path $latestJsonPath -Platform "all" -Arch "all" -Kind "metadata" -Notes "Machine-readable release metadata with hashes."
$allAssets = @($assets + $metadataAsset)

$releaseBody = New-ReleaseBody -Version $version -TagName $tagName -GithubRepo $githubRepo -Assets ([array]$allAssets) -ChangelogPath $changelogPath
Set-Utf8NoBomContent -Path $releaseBodyPath -Content $releaseBody

Write-Host ""
Write-Host "Release artifacts ready:" -ForegroundColor Green
Write-Host (Join-Path $appDistPath "ResearchCompanion.exe")
foreach ($asset in $allAssets) {
  Write-Host (Join-Path $root "dist\$($asset.name)")
}
Write-Host $releaseBodyPath
Write-Host ""
Write-Host "GitHub Release checklist:" -ForegroundColor Yellow
Write-Host "1. Commit code + changelog."
Write-Host "2. Create git tag $tagName."
if ($githubRepo) {
  Write-Host "3. Publish release with:"
  Write-Host "   gh release create $tagName dist/$assetName dist/$versionedInstallerName dist/$portableName dist/latest.json --repo $githubRepo --title $tagName --notes-file dist/release-notes-$tagName.md"
} else {
  Write-Host "3. Set github_repo in config\release_config.json before enabling in-app auto-update."
}
Write-Host "4. Keep $assetName uploaded for existing in-app updater compatibility."
