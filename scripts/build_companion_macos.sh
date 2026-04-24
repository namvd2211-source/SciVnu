#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "$(uname -s)" != "Darwin" ]]; then
  cat >&2 <<'MSG'
Research Companion macOS builds must be produced on macOS.

This Windows/Linux checkout can prepare Windows release assets, but .app/.dmg
artifacts need a Darwin build host, a darwin cli-proxy-api binary, and the
macOS GUI/runtime dependencies for pywebview.
MSG
  exit 1
fi

VERSION="$(python3 - <<'PY'
import json
from pathlib import Path
payload = json.loads(Path('config/release_config.json').read_text(encoding='utf-8-sig'))
print(payload.get('version', '0.0.0-dev'))
PY
)"
ARCH="$(uname -m)"
case "$ARCH" in
  arm64) DIST_ARCH="aarch64" ;;
  x86_64) DIST_ARCH="x64" ;;
  *) DIST_ARCH="$ARCH" ;;
esac

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required." >&2
  exit 1
fi

if [[ ! -x "packaging/bin/cli-proxy-api" && ! -x "vendor/cli-proxy-api/cli-proxy-api" ]]; then
  cat >&2 <<'MSG'
Missing darwin cli-proxy-api binary.
Expected one of:
  packaging/bin/cli-proxy-api
  vendor/cli-proxy-api/cli-proxy-api

Download/build the darwin CLIProxyAPIPlus binary before packaging macOS.
MSG
  exit 1
fi

cat <<MSG
macOS release scaffold is ready, but the final .app/.dmg packaging is not yet automated.

Expected future outputs for version $VERSION on this host:
  dist/ResearchCompanion_${VERSION}_${DIST_ARCH}.dmg
  dist/ResearchCompanion_${DIST_ARCH}.app.tar.gz

Remaining implementation steps:
  1. Add a macOS PyInstaller .app/BUNDLE spec or pyinstaller command.
  2. Bundle the darwin cli-proxy-api binary as bin/cli-proxy-api.
  3. Package the .app into a DMG.
  4. Codesign and notarize before public distribution.
MSG
