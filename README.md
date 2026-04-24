# Research Companion

Web research-writing workflow with a local desktop companion for user-owned Gemini quota.

## Release Metadata
- `release_config.json` is the single source of truth for desktop release metadata.
- Update these fields before publishing a GitHub Release:
  - `version`
  - `github_repo`
  - `release_asset_name`
- `CHANGELOG.md` is the release-notes source for companion releases.

## Main Pieces
- `backend_api.py`: FastAPI backend for workflow execution, uploads, chat-turn routing, exports, quota, and local companion APIs.
- `backend_core.py`: workflow orchestration, search adapters, LLM routing, citation/localization helpers.
- `web/`: hosted web UI.
- `companion_gui.py`: desktop local companion UI.
- `local_companion_runtime.py`: local runtime/bootstrap for CLI proxy and OAuth-owned app data.
- `prepare_github_release.ps1`: release build helper for GitHub Releases.

## Local Development
1. Install Python dependencies:
```powershell
pip install -r requirements.txt
```
2. Run the backend locally:
```powershell
python .\backend_api.py
```
3. Run the local companion UI:
```powershell
python .\companion_gui.py
```

## Companion Build
- Build `onedir` desktop app:
```powershell
powershell -ExecutionPolicy Bypass -File .\build_companion_exe.ps1 -BuildMode onedir
```
- If the default `dist\ResearchCompanion` folder is locked, you can build to a clean alternate root without creating `dist_rebuild*` folders:
```powershell
powershell -ExecutionPolicy Bypass -File .\build_companion_exe.ps1 -BuildMode onedir -DistRoot out -BuildRoot out_build
```
- Build NSIS setup installer:
```powershell
powershell -ExecutionPolicy Bypass -File .\build_companion_nsis_setup.ps1
```
- Prepare a release build and print the GitHub Release checklist:
```powershell
powershell -ExecutionPolicy Bypass -File .\prepare_github_release.ps1
```
- Bump the shared app version:
```powershell
powershell -ExecutionPolicy Bypass -File .\set_release_version.ps1 -Version 1.0.1
```
- The build script now cleans old `build_rebuild*` and `dist_rebuild*` folders automatically unless you pass `-KeepLegacyArtifacts`.

## Desktop Versioning And Updates
- The companion now reads its version and updater settings from `release_config.json`.
- In-app update checks stay inactive until `github_repo` is set to a real `owner/repo`.
- The recommended GitHub Release flow is:
  1. Update `CHANGELOG.md`.
  2. Bump `version` in `release_config.json`.
  3. Build the release with `prepare_github_release.ps1`.
  4. Create tag `v<version>`.
  5. Publish a GitHub Release and upload `ResearchCompanionSetup.exe`.
- Keep the installer asset name stable so the in-app updater can find it.

## Notes
- The desktop companion bundles `cli-proxy-api.exe` and installs per-user.
- The installer is built with NSIS and includes uninstall support.
- Current workflow/auth flow is centered on local companion + CLI proxy OAuth, not backend project quota fallback.
- Companion builds now materialize editable local backend sources to `%LOCALAPPDATA%\\ResearchCompanion\\editable-backend`.
- After that first run, you can edit `backend_api.py` and `backend_core.py` there without rebuilding `ResearchCompanion.exe`.
- If you need to refresh those editable source files from a newer build, set `RESEARCH_COMPANION_REFRESH_EDITABLE_BACKEND=1` for one launch or delete that folder before starting the companion.
