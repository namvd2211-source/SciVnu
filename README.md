# Research Companion

Research Companion is an AI-assisted academic research and writing workspace for students, lecturers, and researchers. It helps turn a research question, manuscript draft, reviewer comment, dataset note, or collection of papers into a structured academic workflow: plan, search, read, draft, review, revise, and export.

The app is designed for academic writing support, not just generic chat. It focuses on evidence gathering, citation-aware drafting, manuscript revision, and iterative improvement for research papers, theses, reports, and reviewer responses.

## What You Can Do

### Plan a research manuscript
- Describe your topic, objective, target journal, assignment, or research problem.
- Let the manager route the task into an academic workflow.
- Generate an outline with research questions, section goals, and writing direction.
- Set target word count and reference target for long-form outputs.

### Search academic literature
- Search across academic databases from one interface.
- Use filters for publication year, database selection, and deep review mode.
- Supported sources include:
  - Scopus
  - CORE
  - Semantic Scholar
  - OpenAlex
  - arXiv
- Combine search results with uploaded papers and manuscript files.

### Work with your own files
Attach papers, images, spreadsheets, manuscript drafts, reviewer comments, or research notes. The workflow can use these files as context for:
- Literature review
- Manuscript revision
- Evidence extraction
- Section rewriting
- Reviewer response drafting
- Comparing and synthesizing sources

### Draft academic sections
Research Companion can draft or revise common academic paper sections, including:
- Introduction
- Literature Review
- Methods
- Results
- Discussion
- Conclusion
- Abstract
- Reviewer response letters

Drafting is guided by the workflow state, retrieved literature, uploaded files, and target length.

### Review and improve writing
The built-in review steps help identify:
- Weak structure
- Unsupported claims
- Missing evidence
- Overly broad conclusions
- Inconsistent academic tone
- Sections that are too short or too long

The app can then revise the manuscript toward a cleaner final version.

### Export results
Generated outputs are organized in workspace tabs:
- Manager routing
- Outline
- Papers
- Review
- Draft
- Final manuscript

You can export the final result as Markdown or Word.

## How the Workflow Works

1. **Describe the task**  
   Ask for a literature review, paper draft, manuscript revision, reviewer response, comparison, or research plan.

2. **Attach supporting files**  
   Add papers, drafts, spreadsheets, images, or notes if you have them.

3. **Adjust filters if needed**  
   Choose literature databases, year range, and deep review mode.

4. **Send the request**  
   The app routes the request through research and writing agents.

5. **Monitor progress**  
   The execution log shows what the workflow is doing: searching, reading, drafting, reviewing, and finalizing.

6. **Review and export**  
   Inspect outputs in the workspace tabs and download the final manuscript.

## Local Companion and Gemini Quota

Research Companion uses a local desktop companion so you can use your own Gemini CLI OAuth quota instead of relying on a shared project quota.

The desktop companion provides:
- Local Gemini CLI OAuth sign-in
- Multiple Gemini account management
- Account switching when quota or rate limits are reached
- Gemini model selection
- Local web UI at `http://127.0.0.1:8787/`
- Update checking and installer handoff

Academic search API keys are kept on the Cloud Run resource backend, not inside the local desktop companion. Firebase Hosting is not part of the runtime flow; the app is local-first.

## Installation

Download the latest Windows installer from the GitHub Releases page:

- `ResearchCompanionSetup.exe` — standard installer and updater-compatible asset
- `ResearchCompanion_<version>_x64-setup.exe` — versioned installer
- `ResearchCompanion_v<version>_x64_portable.zip` — portable build

After installation:

1. Open `ResearchCompanion.exe`.
2. Click **Connect Gemini**.
3. Complete the Gemini CLI OAuth flow in the browser.
4. Open the local web UI from the companion.
5. Start a research or writing workflow.

## Recommended Use Cases

Research Companion is useful for:
- Drafting a first version of a research manuscript
- Expanding a topic into a structured paper outline
- Finding and screening literature
- Revising a manuscript with uploaded source material
- Preparing responses to reviewer comments
- Comparing papers or research approaches
- Turning notes into an academic report
- Producing a literature-backed discussion section

## Important Notes for Academic Use

Research Companion is an assistant, not a replacement for scholarly judgment.

You should still:
- Verify every citation and source.
- Check whether claims are supported by the cited papers.
- Review the final text for accuracy, originality, and institutional requirements.
- Follow your university, journal, or funder policies on AI-assisted writing.

## For Developers

The repository contains:

```text
backend/      FastAPI backend and workflow core
desktop/      Local companion app
web/          Web interface
config/       Release metadata
scripts/      Build and release scripts
packaging/    Installer and PyInstaller assets
```

Run locally:

```powershell
pip install -r requirements.txt
python -m backend.backend_api
python -m desktop.companion_gui
```

Prepare a Windows release:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\prepare_github_release.ps1
```

This generates the installer, portable zip, release metadata, and release notes in `dist/`.

macOS builds are not currently produced by the Windows release flow. A real macOS release requires a macOS build host, Darwin `cli-proxy-api` binaries, app packaging, codesigning, and notarization.
