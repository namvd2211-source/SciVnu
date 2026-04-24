# Research Companion

Web research-writing workflow with a local desktop companion for user-owned Gemini quota.

## Release Metadata
- `config/release_config.json` is the single source of truth for desktop release metadata.
- Update these fields before publishing a GitHub Release:
  - `version`
  - `github_repo`
  - `release_asset_name`
- `CHANGELOG.md` is the release-notes source for companion releases.

## Main Pieces
- `backend/`: FastAPI backend and workflow core.
- `desktop/`: local companion UI, runtime bootstrap, and embedded desktop UI assets.
- `scripts/`: build, release, and local helper scripts.
- `packaging/`: PyInstaller spec, NSIS installer script, and installer assets.
- `config/`: shared release metadata and config helpers.
- `web/`: hosted web UI.

## Companion Features
- Local-first web UI at `http://127.0.0.1:8787/` with `/api/live` detection for hosted webapp handoff.
- Gemini CLI OAuth through the bundled `cli-proxy-api` sidecar; the companion no longer needs legacy Google OAuth client-secret files.
- Multiple Gemini auth files can be installed, disabled, deleted, and switched with the `Use` action in the desktop companion.
- When a new Gemini account is connected, it becomes the only active account; other accounts are left installed but inactive.
- CLI proxy requests can rotate to another installed Gemini account after quota or rate-limit failures.
- The local web UI includes a persisted Gemini model selector and sends the selected model with chat/workflow requests.
- Model fallback can try all available Gemini models across tiers instead of only lower-tier models.
- `Connect Gemini` can be clicked again to cancel a stuck browser callback wait and open a fresh OAuth tab.

## Repo Layout
```text
backend/
  backend_api.py
  backend_core.py
desktop/
  companion_gui.py
  local_companion_runtime.py
  ui/
config/
  release_config.json
  release_config.py
scripts/
  build_companion_exe.ps1
  build_companion_nsis_setup.ps1
  prepare_github_release.ps1
  set_release_version.ps1
packaging/
  ResearchCompanion.spec
  ResearchCompanionSetup.nsi
  installer_assets/
web/
vendor/
```

## Local Development
1. Install Python dependencies:
```powershell
pip install -r requirements.txt
```
2. Run the backend locally:
```powershell
python -m backend.backend_api
```
3. Run the local companion UI:
```powershell
python -m desktop.companion_gui
```

## Companion Build
- Build `onedir` desktop app:
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_companion_exe.ps1 -BuildMode onedir
```
- If the default `dist\ResearchCompanion` folder is locked, you can build to a clean alternate root without creating `dist_rebuild*` folders:
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_companion_exe.ps1 -BuildMode onedir -DistRoot out -BuildRoot out_build
```
- Build NSIS setup installer:
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_companion_nsis_setup.ps1
```
- Prepare a release build and print the GitHub Release checklist:
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\prepare_github_release.ps1
```
- Bump the shared app version:
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\set_release_version.ps1 -Version 1.0.1
```
- The build script now cleans old `build_rebuild*` and `dist_rebuild*` folders automatically unless you pass `-KeepLegacyArtifacts`.

## Desktop Versioning And Updates
- The companion now reads its version and updater settings from `config/release_config.json`.
- In-app update checks stay inactive until `github_repo` is set to a real `owner/repo`.
- The recommended GitHub Release flow is:
  1. Update `CHANGELOG.md`.
  2. Bump `version` in `config/release_config.json`.
  3. Build the release with `scripts/prepare_github_release.ps1`.
  4. Create tag `v<version>`.
  5. Publish a GitHub Release and upload `ResearchCompanionSetup.exe`.
- Keep the installer asset name stable so the in-app updater can find it.

## Notes
- The desktop companion bundles `cli-proxy-api.exe` and installs per-user.
- The installer is built with NSIS and includes uninstall support.
- Current workflow/auth flow is centered on local companion + CLI proxy OAuth, not backend project quota fallback.
- Companion builds now materialize editable local backend sources to `%LOCALAPPDATA%\\ResearchCompanion\\editable-backend`.
- After that first run, you can edit `backend_api.py` and `backend_core.py` inside the editable backend folder there without rebuilding `ResearchCompanion.exe`.
- If you need to refresh those editable source files from a newer build, set `RESEARCH_COMPANION_REFRESH_EDITABLE_BACKEND=1` for one launch or delete that folder before starting the companion.
