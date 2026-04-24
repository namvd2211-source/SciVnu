# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and versions are intended to map to GitHub Releases.

## [Unreleased]
### Added
- Centralized release metadata in `release_config.json`.
- Desktop companion version display, update check, and installer handoff flow.
- Shared release workflow scripts and documentation for GitHub Releases.

### Changed
- Companion build and NSIS installer now read a single app version source.
- Repository ignore rules now exclude local and generated build artifacts more cleanly.

## [1.0.0] - 2026-04-24
### Added
- Initial companion-based local Gemini OAuth workflow.
- Editable backend runtime for post-install backend fixes without rebuilding the desktop executable.
- NSIS-based Windows installer packaging.
