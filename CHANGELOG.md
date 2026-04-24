# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and versions are intended to map to GitHub Releases.

## [Unreleased]

## [1.0.5] - 2026-04-25
### Added
- Release preparation now generates versioned Windows installer, portable zip, `latest.json`, and structured GitHub release notes.
- Added a macOS build scaffold documenting the Darwin-only packaging requirements.

### Changed
- Release documentation now distinguishes the stable updater installer from versioned manual-download assets.
- CLI proxy requests now read the current local proxy API key at request time to avoid stale auth after runtime refreshes.

### Fixed
- Chat now reports expired or invalid Gemini CLI OAuth as a clear sign-in error instead of a generic Internal Server Error.
- Web chat now shows structured backend error details instead of raw JSON response bodies.

## [1.0.4] - 2026-04-25
### Added
- Desktop companion can mark one Gemini auth file as the active account when multiple accounts are installed.
- Local web UI now exposes a persisted Gemini model selector.
- CLI proxy requests can rotate to another Gemini account when a quota/rate-limit response is encountered.
- Connect Gemini can cancel a stuck browser callback wait and open a fresh OAuth tab when clicked again.

### Changed
- Desktop companion now labels the sign-in flow as Gemini CLI OAuth instead of Google OAuth.
- Desktop runtime no longer loads legacy Google OAuth client-secret files for companion sign-in.
- Gemini model fallback now tries all available models across model tiers instead of only lower-tier fallbacks.
- Newly connected Gemini accounts become the only active account immediately after OAuth completes.

## [1.0.3] - 2026-04-25
### Fixed
- Release metadata now loads correctly from PowerShell-written JSON files, preventing the companion from showing `0.0.0-dev`.

## [1.0.2] - 2026-04-24
### Added
- Local companion web UI served from `http://127.0.0.1:8787/` with same-origin API access.
- Lightweight `/api/live` liveness endpoint for reliable hosted webapp companion detection.
- Update Now flow that stops the local proxy, downloads the installer, launches it, and closes the companion.

### Changed
- Desktop companion now opens the local web UI by default and binds the backend to loopback.
- Hosted webapp detection now probes liveness separately from auth/proxy health.
- Packaged companion now includes the production web assets.
- Editable backend runtime refreshes backend files when packaged/source files change.

## [1.0.1] - 2026-04-24
### Added
- Centralized release metadata in `release_config.json`.
- Desktop companion version display, update check, and installer handoff flow.
- Shared release workflow scripts and documentation for GitHub Releases.

### Changed
- Configured the companion updater to check releases from `namvd2211-source/SciVnu`.
- Reorganized the repository into `backend/`, `desktop/`, `config/`, `scripts/`, and `packaging/`.
- Companion build and NSIS installer now read a single app version source.
- Repository ignore rules now exclude local and generated build artifacts more cleanly.

## [1.0.0] - 2026-04-24
### Added
- Initial companion-based local Gemini OAuth workflow.
- Editable backend runtime for post-install backend fixes without rebuilding the desktop executable.
- NSIS-based Windows installer packaging.
