from __future__ import annotations

import json
import os
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

DEFAULT_RELEASE_CONFIG: Dict[str, Any] = {
    "app_name": "Research Companion",
    "version": "0.0.0-dev",
    "release_channel": "stable",
    "release_tag_prefix": "v",
    "github_repo": "",
    "release_asset_name": "ResearchCompanionSetup.exe",
    "release_notes_file": "CHANGELOG.md",
    "auto_update": {
        "enabled": True,
        "check_on_startup": True,
        "check_interval_hours": 6,
    },
}


def _module_root() -> Path:
    return Path(__file__).resolve().parent


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            return Path(meipass).resolve()
        return Path(sys.executable).resolve().parent
    return _module_root()


def _candidate_paths() -> list[Path]:
    candidates: list[Path] = []
    env_path = os.getenv("RESEARCH_COMPANION_RELEASE_CONFIG", "").strip()
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(_module_root() / "release_config.json")
    candidates.append(_module_root().parent / "release_config.json")
    candidates.append(_runtime_root() / "config" / "release_config.json")
    candidates.append(_runtime_root() / "release_config.json")
    return candidates


def _merge_dicts(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dicts(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


@lru_cache(maxsize=1)
def load_release_config() -> Dict[str, Any]:
    config = dict(DEFAULT_RELEASE_CONFIG)
    for candidate in _candidate_paths():
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if isinstance(payload, dict):
            config = _merge_dicts(config, payload)
            break
    return config


def current_version() -> str:
    return str(load_release_config().get("version") or DEFAULT_RELEASE_CONFIG["version"]).strip()


def current_app_name() -> str:
    return str(load_release_config().get("app_name") or DEFAULT_RELEASE_CONFIG["app_name"]).strip()


def release_tag_name(version: str | None = None) -> str:
    config = load_release_config()
    prefix = str(config.get("release_tag_prefix") or "").strip()
    value = str(version or current_version()).strip()
    return f"{prefix}{value}" if prefix else value


def normalize_version(value: str) -> str:
    text = str(value or "").strip()
    return text[1:] if text.lower().startswith("v") else text


def version_sort_key(value: str) -> tuple[tuple[int, Any], ...]:
    normalized = normalize_version(value)
    tokens = re.findall(r"\d+|[A-Za-z]+", normalized)
    key: list[tuple[int, Any]] = []
    for token in tokens:
        if token.isdigit():
            key.append((0, int(token)))
        else:
            key.append((1, token.lower()))
    return tuple(key)


def is_newer_version(candidate: str, current: str | None = None) -> bool:
    baseline = current_version() if current is None else str(current or "")
    return version_sort_key(candidate) > version_sort_key(baseline)
