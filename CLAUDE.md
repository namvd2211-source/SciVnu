# CLAUDE.md

This file gives Claude Code project-specific context for working in this repository.

## Product Context

Research Companion is an AI-assisted academic research and writing workspace for students, lecturers, and researchers. The product should be presented and developed as an academic workflow assistant, not as a generic chatbot or only as a technical demo.

The main user-facing value is helping users move from a research task to structured outputs:

1. Plan the academic workflow.
2. Search and screen literature.
3. Read uploaded and retrieved sources.
4. Draft academic sections.
5. Review and revise the manuscript.
6. Export Markdown or Word outputs.

Prioritize features that improve academic writing, evidence use, citation support, manuscript revision, reviewer responses, and research workflow clarity.

## User-Facing Positioning

When editing README, release notes, UI copy, or product descriptions, write for academic users first:

- students writing theses or reports
- lecturers preparing manuscripts
- researchers drafting papers or responses to reviewers
- users who need literature-backed academic writing support

Avoid leading with internal implementation details such as FastAPI, Cloud Run, PyInstaller, folder layout, or release scripts unless the section is explicitly for developers.

## Main User Features

The app should clearly support:

- Research task routing through workflow agents
- Literature search across Scopus, CORE, Semantic Scholar, OpenAlex, and arXiv
- Filters for databases, publication year, and deep review mode
- Uploaded papers, drafts, spreadsheets, images, notes, and reviewer comments as context
- Academic outline generation
- Manuscript drafting and revision
- Reviewer feedback and quality checks
- Final output export as Markdown and Word
- Local Gemini CLI OAuth through the desktop companion
- Multiple Gemini account management and fallback when quota or rate limits are reached

## Architecture Overview

Keep this high-level model in mind:

- `web/` contains the browser UI.
- `desktop/` contains the local companion app and runtime bootstrap.
- `backend/` contains the FastAPI API and workflow core.
- `config/` contains shared release metadata.
- `scripts/` contains build and release automation.
- `packaging/` contains installer and PyInstaller assets.

The local desktop companion serves the local web UI at `http://127.0.0.1:8787/` and connects to the user's Gemini CLI OAuth quota through the bundled CLI proxy.

This project is local-first and should not depend on Firebase Hosting. Cloud Run and Secret Manager may still be used for resource-search endpoints that must keep shared academic database API keys off the desktop.

Cloud resource search keeps academic database API keys on the cloud backend. Do not move Scopus, CORE, Semantic Scholar, or similar shared resource API keys into the local desktop runtime.

## LLM and Workflow Notes

The workflow uses role-specific model routing. Do not assume a selected UI model should force every node to use the same model unless the user explicitly asks for that behavior.

Important roles include:

- Manager
- Planner
- Researcher
- Reader
- Writer
- Reviewer
- Editor
- Translator

Writer behavior is important to user trust. Preserve clear progress logs for long drafting steps and avoid silent spinning when a model returns no text.

## Academic Search Notes

Resource search sources include:

- Scopus: cloud API key
- CORE: cloud API key
- Semantic Scholar: cloud API key, rate-limited below one request per second
- OpenAlex: public metadata search
- arXiv: public preprint search

Semantic Scholar rate limiting is cumulative across endpoints and should remain protected by a process-level lock when requests can run concurrently.

## Release and Build Notes

Windows release artifacts are generated into `dist/`.

Important release artifacts:

- `ResearchCompanionSetup.exe`: stable updater-compatible installer name
- `ResearchCompanion_<version>_x64-setup.exe`: versioned installer
- `ResearchCompanion_v<version>_x64_portable.zip`: portable Windows package
- `latest.json`: release metadata and hashes
- `release-notes-v<version>.md`: GitHub release body

`build/` is a disposable PyInstaller intermediate directory and can be removed after verifying `dist/` contains the intended artifacts.

macOS artifacts are not produced by the Windows release flow. Real macOS releases require a macOS build host, Darwin CLI proxy binary, app packaging, codesigning, and notarization.

## Development Commands

Run local backend:

```powershell
python -m backend.backend_api
```

Run local companion:

```powershell
python -m desktop.companion_gui
```

Compile-check changed Python files:

```powershell
python -m py_compile backend/backend_core.py backend/backend_api.py desktop/local_companion_runtime.py
```

Prepare Windows release artifacts:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\prepare_github_release.ps1
```

## Coding Guidelines

- Keep user-facing copy academic and outcome-oriented.
- Prefer small, direct changes over broad refactors.
- Do not add backward-compatibility shims unless existing installed clients require them.
- Keep `ResearchCompanionSetup.exe` as a release asset for updater compatibility.
- Do not commit or embed cloud resource API keys in source or desktop runtime.
- Preserve local companion behavior that strips shared resource keys from local env.
- When changing UI behavior, test the local web UI when possible.
- When changing backend workflow behavior, run Python compile checks at minimum.

## Documentation Guidelines

README should be product-facing first. Keep developer/build details short and near the end.

CHANGELOG should describe user-visible changes and release-relevant fixes.

Avoid creating extra planning or architecture documents unless the user asks. Use this file as the main Claude/project guidance document.
