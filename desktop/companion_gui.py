from __future__ import annotations

import atexit
import contextlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Optional, cast

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.release_config import current_app_name, current_version, is_newer_version, load_release_config

RELEASE_CONFIG = load_release_config()
APP_TITLE = current_app_name()
LOCAL_WEB_APP_URL = "http://127.0.0.1:8787/"
LOCAL_LIVE_URL = "http://127.0.0.1:8787/api/live"
LOCAL_HEALTH_URL = "http://127.0.0.1:8787/api/health"
BACKEND_READY_TIMEOUT = 15.0
STATE_POLL_INTERVAL = 1.0
MAX_LOG_LINES = 1200
DEFAULT_UPDATE_CHECK_INTERVAL_HOURS = 6.0
UPDATE_INSTALLER_MAX_AGE_SECONDS = 24 * 3600


@lru_cache(maxsize=1)
def _runtime_imports():
    from desktop.local_companion_runtime import bundle_root, project_root
    from desktop.local_companion_runtime import configure_local_companion_env

    return bundle_root, project_root, configure_local_companion_env


def _runtime_paths():
    bundle_root, project_root, _ = _runtime_imports()

    return bundle_root, project_root


def _configure_local_companion_env():
    _, project_root, configure_local_companion_env = _runtime_imports()

    return configure_local_companion_env(project_root())


@lru_cache(maxsize=1)
def _local_requests_session():
    import requests

    session = requests.Session()
    session.trust_env = False
    return session


def _requests_get(url: str, timeout: float):
    return _local_requests_session().get(url, timeout=timeout)


def _requests_request(
    method: str,
    url: str,
    *,
    timeout: float,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
):
    return _local_requests_session().request(
        method=method,
        url=url,
        timeout=timeout,
        headers=headers,
        params=params,
    )


def _open_in_browser(url: str) -> None:
    import webbrowser

    webbrowser.open(url)


def _open_in_file_explorer(path: Path) -> None:
    target = str(path.resolve())
    if os.name == "nt":
        os.startfile(target)  # type: ignore[attr-defined]
        return
    _open_in_browser(path.as_uri())


def _backend_log_path() -> Path:
    local_appdata = os.getenv("LOCALAPPDATA", "").strip()
    if local_appdata:
        target_dir = Path(local_appdata) / "ResearchCompanion"
    else:
        _, project_root = _runtime_paths()
        target_dir = project_root()
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / "backend-startup.log"


def _backend_log(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with _backend_log_path().open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")
    except Exception:
        pass


def _can_connect_tcp(host: str, port: int, *, timeout: float = 0.8) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def _default_auth_dir_fallback() -> Path:
    try:
        home = Path.home()
    except Exception:
        home = None
    if home:
        return home / ".cli-proxy-api"
    local_appdata = os.getenv("LOCALAPPDATA", "").strip()
    if local_appdata:
        return Path(local_appdata) / "ResearchCompanion" / "cli-proxy-auth"
    _, project_root = _runtime_paths()
    return project_root() / ".research-companion" / "cli-proxy-auth"


def _confirm_exit_dialog() -> bool:
    import tkinter
    from tkinter import messagebox

    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return bool(
            messagebox.askyesno(
                "Exit Research Companion",
                "The local proxy is still running. Stop it and close the app?",
            )
        )
    finally:
        root.destroy()


def _load_tray_icon_image() -> Any:
    from PIL import Image

    icon_path = REPO_ROOT / "packaging" / "installer_assets" / "icon-preview.png"
    if not icon_path.exists():
        icon_path = REPO_ROOT / "packaging" / "installer_assets" / "icon.ico"
    return Image.open(icon_path)


class CompanionTray:
    def __init__(self, window: Any, controller: "CompanionController") -> None:
        self.window = window
        self.controller = controller
        self.icon: Any = None
        self.ready = False
        self.quit_requested = False

    def start(self) -> None:
        try:
            import pystray
        except Exception as exc:
            self.controller.log(f"System tray unavailable: {exc}")
            return
        try:
            self.icon = pystray.Icon(
                "ResearchCompanion",
                _load_tray_icon_image(),
                APP_TITLE,
                menu=pystray.Menu(
                    pystray.MenuItem("Open Research Companion", self.show_window, default=True),
                    pystray.MenuItem("Open Local Web UI", self.open_web_app),
                    pystray.MenuItem("Quit", self.quit_app),
                ),
            )
            threading.Thread(target=self.icon.run, name="research-companion-tray", daemon=True).start()
            self.ready = True
            self.controller.log("System tray is ready. Closing the window will keep Research Companion running.")
        except Exception as exc:
            self.controller.log(f"System tray failed to start: {exc}")

    def show_window(self, _icon: Any = None, _item: Any = None) -> None:
        with contextlib.suppress(Exception):
            self.window.show()
        with contextlib.suppress(Exception):
            self.window.restore()
        with contextlib.suppress(Exception):
            self.window.bring_to_front()

    def hide_window(self) -> bool:
        if not self.ready:
            return False
        with contextlib.suppress(Exception):
            self.window.hide()
            self.controller.log("Research Companion minimized to system tray.")
            return True
        return False

    def open_web_app(self, _icon: Any = None, _item: Any = None) -> None:
        self.controller.open_web_app()

    def quit_app(self, _icon: Any = None, _item: Any = None) -> None:
        self.quit_requested = True
        self.controller.shutdown()
        with contextlib.suppress(Exception):
            if self.icon:
                self.icon.stop()
        with contextlib.suppress(Exception):
            self.window.destroy()
        os._exit(0)

    def stop(self) -> None:
        with contextlib.suppress(Exception):
            if self.icon:
                self.icon.stop()


def _load_runtime_backend_module(settings: Dict[str, Any]):
    backend_dir_text = str(settings.get("EDITABLE_BACKEND_DIR") or os.getenv("RESEARCH_COMPANION_EDITABLE_BACKEND_DIR") or "").strip()
    if backend_dir_text:
        backend_dir = Path(backend_dir_text)
        backend_path = backend_dir / "backend_api.py"
        if backend_path.exists():
            backend_dir_resolved = str(backend_dir.resolve())
            if backend_dir_resolved not in sys.path:
                sys.path.insert(0, backend_dir_resolved)
            sys.modules.pop("backend_api", None)
            sys.modules.pop("backend_core", None)
            spec = importlib.util.spec_from_file_location("backend_api", backend_path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules["backend_api"] = module
                spec.loader.exec_module(module)
                return module, str(backend_path.resolve())
    from backend import backend_api

    source_path = getattr(backend_api, "__file__", "")
    return backend_api, str(source_path or "")


class CompanionController:
    def __init__(self, *, start_monitor: bool = False) -> None:
        self.release_config = load_release_config()
        self.process: Optional[subprocess.Popen[str]] = None
        self.reader_thread: Optional[threading.Thread] = None
        self.health_thread: Optional[threading.Thread] = None
        self.oauth_thread: Optional[threading.Thread] = None
        self.oauth_cancel_event = threading.Event()
        self.update_thread: Optional[threading.Thread] = None
        self.close_callback: Optional[Callable[[], None]] = None
        self.closing_for_update = False
        self.stop_event = threading.Event()
        self.state_lock = threading.Lock()
        self.started_at = 0.0
        self.next_update_check_at = 0.0
        self.log_seq = 0
        self.last_health_payload: Dict[str, Any] = {}
        self.log_lines: Deque[str] = deque(maxlen=MAX_LOG_LINES)
        update_configured = self._updates_configured()
        self.state: Dict[str, Any] = {
            "status": "Stopped",
            "detail": "Local API: http://127.0.0.1:8787",
            "badge": "OFF",
            "running": False,
            "logs": [],
            "log_seq": 0,
            "oauth_source": "",
            "runtime_identity": "user_oauth",
            "runtime_phase": "stopped",
            "auth_phase": "idle",
            "startup_stage": "idle",
            "last_event": "",
            "events": [],
            "auth_summary": {
                "total": 0,
                "file_backed": 0,
                "runtime_only": 0,
                "expired": 0,
                "disabled": 0,
            },
            "app_name": str(self.release_config.get("app_name") or APP_TITLE),
            "app_version": current_version(),
            "release_channel": str(self.release_config.get("release_channel") or "stable"),
            "update_configured": update_configured,
            "update_status": "idle" if update_configured else "not_configured",
            "update_message": (
                "Automatic updates are ready to use once GitHub Releases are configured."
                if update_configured
                else "Automatic updates are not configured yet. Set github_repo in config/release_config.json."
            ),
            "update_available": False,
            "update_checked_at": "",
            "update_latest_version": "",
            "update_download_url": "",
            "update_release_url": "",
            "update_published_at": "",
            "update_asset_name": str(self.release_config.get("release_asset_name") or "ResearchCompanionSetup.exe"),
            "update_download_percent": 0,
            "update_download_bytes": 0,
            "update_download_total_bytes": 0,
        }
        self._append_log("Companion ready.")
        cleaned_installers = self._cleanup_update_installers()
        if cleaned_installers:
            self._append_log(f"Cleaned {cleaned_installers} old update installer(s) from temp.")
        self._append_log(f"Local web app: {LOCAL_WEB_APP_URL}")
        self._append_log(
            f"{self.state['app_name']} version {self.state['app_version']} ({self.state['release_channel']})"
        )
        if start_monitor:
            self.start_monitor()
        atexit.register(self.shutdown)

    def _updates_configured(self) -> bool:
        github_repo = str(self.release_config.get("github_repo") or "").strip()
        auto_update = self.release_config.get("auto_update") if isinstance(self.release_config.get("auto_update"), dict) else {}
        enabled = bool(auto_update.get("enabled", True))
        return enabled and bool(github_repo)

    def _github_repo_slug(self) -> str:
        return str(self.release_config.get("github_repo") or "").strip().strip("/")

    def _release_notes_path(self) -> Optional[Path]:
        candidates = []
        note_name = str(self.release_config.get("release_notes_file") or "CHANGELOG.md").strip() or "CHANGELOG.md"
        bundle_root, project_root = _runtime_paths()
        candidates.append(bundle_root() / "release_assets" / note_name)
        candidates.append(bundle_root() / note_name)
        candidates.append(project_root() / note_name)
        candidates.append(Path(__file__).resolve().parent / note_name)
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def _github_latest_release_api_url(self) -> str:
        slug = self._github_repo_slug()
        if not slug:
            return ""
        return f"https://api.github.com/repos/{slug}/releases/latest"

    def _format_dt_label(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        for candidate in (text.replace("Z", "+00:00"), text):
            try:
                return datetime.fromisoformat(candidate).astimezone().strftime("%Y-%m-%d %H:%M")
            except Exception:
                continue
        return text

    def _update_check_interval_seconds(self) -> float:
        auto_update = self.release_config.get("auto_update") if isinstance(self.release_config.get("auto_update"), dict) else {}
        raw_hours = auto_update.get("check_interval_hours", DEFAULT_UPDATE_CHECK_INTERVAL_HOURS)
        try:
            hours = max(0.25, float(raw_hours))
        except Exception:
            hours = DEFAULT_UPDATE_CHECK_INTERVAL_HOURS
        return hours * 3600.0

    def _set_next_update_check(self, *, immediate: bool = False) -> None:
        delay = 0.0 if immediate else self._update_check_interval_seconds()
        with self.state_lock:
            self.next_update_check_at = time.time() + delay

    def _select_release_asset(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        assets = payload.get("assets")
        if not isinstance(assets, list):
            return None
        expected_name = str(self.release_config.get("release_asset_name") or "").strip()
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            if str(asset.get("name") or "").strip() == expected_name:
                return asset
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name") or "").strip().lower()
            if name.endswith(".exe"):
                return asset
        return None

    def _update_downloads_dir(self) -> Path:
        target = Path(tempfile.gettempdir()) / "ResearchCompanion" / "updates"
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _cleanup_update_installers(self, *, max_age_seconds: int = UPDATE_INSTALLER_MAX_AGE_SECONDS) -> int:
        target = self._update_downloads_dir()
        cutoff = time.time() - max_age_seconds
        removed = 0
        for item in target.glob("ResearchCompanionSetup-*.exe"):
            try:
                if item.stat().st_mtime > cutoff:
                    continue
                item.unlink()
                removed += 1
            except Exception:
                continue
        return removed

    def _update_state_fields(self, **updates: Any) -> None:
        self._update_state(**updates)

    def _run_update_check(self, *, manual: bool) -> None:
        checked_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        if not self._updates_configured():
            self._update_state_fields(
                update_configured=False,
                update_status="not_configured",
                update_message="Automatic updates are not configured yet. Set github_repo in config/release_config.json.",
                update_checked_at=checked_at if manual else "",
            )
            return

        self._update_state_fields(
            update_configured=True,
            update_status="checking",
            update_message="Checking GitHub Releases for a newer companion build...",
            update_checked_at=checked_at,
        )
        try:
            response = _requests_request(
                "GET",
                self._github_latest_release_api_url(),
                timeout=12.0,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "ResearchCompanionUpdater",
                },
            )
            payload = response.json()
            if not response.ok or not isinstance(payload, dict):
                raise RuntimeError(f"GitHub Releases API returned HTTP {response.status_code}.")

            tag_name = str(payload.get("tag_name") or "").strip()
            latest_version = tag_name[1:] if tag_name.lower().startswith("v") else tag_name
            release_url = str(payload.get("html_url") or "").strip()
            published_at = self._format_dt_label(payload.get("published_at") or payload.get("created_at") or "")
            asset = self._select_release_asset(payload)
            download_url = str(asset.get("browser_download_url") or "").strip() if isinstance(asset, dict) else ""
            asset_name = str(asset.get("name") or "").strip() if isinstance(asset, dict) else str(
                self.release_config.get("release_asset_name") or ""
            )

            if latest_version and is_newer_version(latest_version, current_version()):
                self._update_state_fields(
                    update_status="available",
                    update_message=f"Version {latest_version} is available.",
                    update_available=True,
                    update_latest_version=latest_version,
                    update_download_url=download_url,
                    update_release_url=release_url,
                    update_published_at=published_at,
                    update_asset_name=asset_name,
                )
                self._append_log(f"Update available: {latest_version}")
            else:
                latest_label = latest_version or current_version()
                self._update_state_fields(
                    update_status="up_to_date",
                    update_message=f"You are on the latest version ({latest_label}).",
                    update_available=False,
                    update_latest_version=latest_label,
                    update_download_url=download_url,
                    update_release_url=release_url,
                    update_published_at=published_at,
                    update_asset_name=asset_name,
                )
                if manual:
                    self._append_log(f"No update found. Current version {current_version()} is up to date.")
        except Exception as exc:
            self._update_state_fields(
                update_status="error",
                update_message=f"Update check failed: {exc}",
                update_available=False,
            )
            self._append_log(f"Update check failed: {exc}")
        finally:
            self._set_next_update_check()

    def _start_update_check(self, *, manual: bool) -> Dict[str, Any]:
        if self.update_thread and self.update_thread.is_alive():
            return {"ok": True, "message": "Update check is already running."}
        if not self._updates_configured():
            self._run_update_check(manual=manual)
            return {"ok": False, "message": "Automatic updates are not configured yet."}
        self.update_thread = threading.Thread(target=self._run_update_check, kwargs={"manual": manual}, daemon=True)
        self.update_thread.start()
        return {"ok": True, "message": "Update check started."}

    def _auth_dir_path(self) -> Path:
        settings = _configure_local_companion_env()
        auth_dir = str(settings.get("CLI_PROXY_AUTH_DIR") or "").strip()
        if auth_dir:
            return Path(auth_dir)
        return _default_auth_dir_fallback()

    def _auth_dir_candidates(self) -> list[Path]:
        settings = _configure_local_companion_env()
        candidates = [self._auth_dir_path()]
        legacy_auth_dir = str(settings.get("CLI_PROXY_AUTH_DIR_LEGACY") or "").strip()
        if legacy_auth_dir:
            candidates.append(Path(legacy_auth_dir))
        try:
            candidates.append(Path.home() / ".cli-proxy-api")
        except Exception:
            pass
        ordered: list[Path] = []
        seen: set[str] = set()
        for path in candidates:
            key = str(path.resolve()) if path.exists() else str(path)
            if key not in seen:
                ordered.append(path)
                seen.add(key)
        return ordered

    def _summarize_auth_file(self, path: Path) -> Optional[Dict[str, Any]]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        auth_payload = cast(Dict[str, Any], payload)

        token_raw = auth_payload.get("token")
        token = cast(Dict[str, Any], token_raw) if isinstance(token_raw, dict) else {}
        auth_type = str(auth_payload.get("type") or auth_payload.get("provider") or "").strip().lower()
        if auth_type not in {"gemini", "gemini-cli"}:
            return None

        email = str(auth_payload.get("email") or "").strip() or "unknown"
        project_id = str(auth_payload.get("project_id") or "").strip() or "unknown"
        expiry = str(token.get("expiry") or "").strip()
        expiry_label = ""
        expired = False
        if expiry:
            try:
                expiry_dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                expiry_label = expiry_dt.strftime("%Y-%m-%d %H:%M")
                expired = expiry_dt.timestamp() <= time.time()
            except Exception:
                expiry_label = expiry

        try:
            stat = path.stat()
            size_bytes = stat.st_size
            modified = time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime))
        except Exception:
            size_bytes = 0
            modified = ""

        disabled = path.name.endswith(".disabled") or str(auth_payload.get("disabled") or "").lower() == "true"
        checked = str(auth_payload.get("checked") or "").lower() == "true"
        return {
            "filename": path.name,
            "base_filename": path.name[:-9] if path.name.endswith(".disabled") else path.name,
            "email": email,
            "project_id": project_id,
            "type": auth_type,
            "checked": checked,
            "active": checked and not disabled and not expired,
            "auto": bool(auth_payload.get("auto")),
            "has_refresh_token": bool(str(token.get("refresh_token") or "").strip()),
            "expiry": expiry_label,
            "expired": expired,
            "modified": modified,
            "size_kb": f"{(size_bytes / 1024):.1f} KB" if size_bytes else "0 KB",
            "source": "file",
            "runtime_only": False,
            "disabled": disabled,
            "path": str(path),
            "modified_ts": stat.st_mtime if "stat" in locals() else 0.0,
        }

    def _summarize_management_auth_file(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        provider = str(item.get("provider") or item.get("type") or "").strip().lower()
        if "gemini" not in provider and provider not in {"", "google"}:
            return None
        filename = str(item.get("filename") or item.get("name") or "").strip()
        path_text = str(item.get("path") or "").strip()
        if not filename and path_text:
            filename = Path(path_text).name
        source = str(item.get("source") or "").strip().lower()
        runtime_only = bool(item.get("runtime_only")) or source == "runtime"
        return {
            "filename": filename or ("runtime-gemini-auth" if runtime_only else "gemini-auth"),
            "base_filename": filename or ("runtime-gemini-auth" if runtime_only else "gemini-auth"),
            "email": str(item.get("email") or "").strip() or "unknown",
            "project_id": str(item.get("project_id") or "").strip() or "unknown",
            "type": provider or "gemini",
            "checked": bool(item.get("checked")),
            "auto": bool(item.get("auto")),
            "has_refresh_token": bool(item.get("has_refresh_token", not runtime_only)),
            "expiry": str(item.get("expiry") or item.get("expires_at") or "").strip(),
            "expired": str(item.get("status") or "").strip().lower() in {"expired", "invalid"},
            "modified": str(item.get("updated_at") or item.get("modified") or source or "").strip(),
            "size_kb": "runtime" if runtime_only else "",
            "source": source or ("runtime" if runtime_only else "file"),
            "runtime_only": runtime_only,
            "disabled": bool(item.get("disabled")),
            "path": path_text or "",
            "modified_ts": self._parse_modified_timestamp(item.get("updated_at") or item.get("modified") or ""),
        }

    def _push_event(self, kind: str, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        with self.state_lock:
            events = list(self.state.get("events") or [])
            if events and events[-1].get("message") == message and events[-1].get("kind") == kind:
                return
            events.append({"time": timestamp, "kind": kind, "message": message})
            events = events[-10:]
            self.state["events"] = events
            self.state["last_event"] = message

    def _set_runtime_phase(self, phase: str, *, stage: Optional[str] = None) -> None:
        updates: Dict[str, Any] = {"runtime_phase": phase}
        if stage is not None:
            updates["startup_stage"] = stage
        self._update_state(**updates)

    def _set_auth_phase(self, phase: str) -> None:
        self._update_state(auth_phase=phase)

    def _normalize_auth_filename(self, filename: str) -> str:
        text = str(filename or "").strip()
        if not text:
            return ""
        return text[:-9] if text.endswith(".disabled") else text

    def _parse_modified_timestamp(self, value: Any) -> float:
        text = str(value or "").strip()
        if not text:
            return 0.0
        for candidate in (text.replace("Z", "+00:00"), text):
            try:
                return datetime.fromisoformat(candidate).timestamp()
            except Exception:
                pass
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(text, fmt).timestamp()
            except Exception:
                pass
        return 0.0

    def _auth_entry_logical_key(self, summary: Dict[str, Any]) -> str:
        email = str(summary.get("email") or "unknown").strip().lower()
        base_filename = self._normalize_auth_filename(str(summary.get("base_filename") or summary.get("filename") or ""))
        return f"{email}|{base_filename}".lower()

    def _auth_dir_rank_for_path(self, path_text: str) -> int:
        value = str(path_text or "").strip()
        if not value:
            return 999
        try:
            parent = Path(value).resolve().parent
        except Exception:
            return 999
        for index, candidate in enumerate(self._auth_dir_candidates()):
            try:
                if parent == candidate.resolve():
                    return index
            except Exception:
                continue
        return 999

    def _merge_auth_entries(self, current: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
        current_rank = self._auth_dir_rank_for_path(str(current.get("path") or ""))
        candidate_rank = self._auth_dir_rank_for_path(str(candidate.get("path") or ""))
        current_ts = float(current.get("modified_ts") or 0.0)
        candidate_ts = float(candidate.get("modified_ts") or 0.0)
        current_score = (
            1 if bool(current.get("runtime_only")) else 0,
            1 if bool(current.get("expired")) else 0,
            1 if bool(current.get("disabled")) else 0,
            current_rank,
            -current_ts,
        )
        candidate_score = (
            1 if bool(candidate.get("runtime_only")) else 0,
            1 if bool(candidate.get("expired")) else 0,
            1 if bool(candidate.get("disabled")) else 0,
            candidate_rank,
            -candidate_ts,
        )
        preferred = candidate if candidate_score < current_score else current
        secondary = current if preferred is candidate else candidate
        merged = dict(preferred)
        for key, value in secondary.items():
            if merged.get(key) in {"", "unknown", None} and value not in {"", "unknown", None}:
                merged[key] = value
        return merged

    def _auth_entry_dedupe_key(self, summary: Dict[str, Any]) -> str:
        path_text = str(summary.get("path") or "").strip()
        if path_text:
            try:
                return f"path:{Path(path_text).resolve()}".lower()
            except Exception:
                return f"path:{path_text}".lower()
        email = str(summary.get("email") or "unknown").strip().lower()
        base_filename = self._normalize_auth_filename(str(summary.get("base_filename") or summary.get("filename") or ""))
        return f"entry:{email}|{base_filename}".lower()

    def _auth_entry_signature(self, summary: Dict[str, Any]) -> str:
        return "|".join(
            [
                self._auth_entry_dedupe_key(summary),
                str(summary.get("modified") or "").strip(),
                str(summary.get("expiry") or "").strip(),
                "1" if bool(summary.get("expired")) else "0",
                "1" if bool(summary.get("disabled")) else "0",
            ]
        )

    def _auth_file_paths_for_name(self, filename: str) -> list[Path]:
        base = self._normalize_auth_filename(filename)
        if not base:
            return []
        candidates: list[Path] = []
        seen: set[str] = set()
        for auth_dir in self._auth_dir_candidates():
            for name in (base, f"{base}.disabled"):
                path = auth_dir / name
                if not path.exists():
                    continue
                try:
                    key = str(path.resolve())
                except Exception:
                    key = str(path)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(path)
        return candidates

    def _update_auth_summary(self, entries: list[Dict[str, Any]]) -> None:
        summary = {
            "total": len(entries),
            "file_backed": sum(1 for item in entries if not bool(item.get("runtime_only"))),
            "runtime_only": sum(1 for item in entries if bool(item.get("runtime_only"))),
            "expired": sum(1 for item in entries if bool(item.get("expired"))),
            "disabled": sum(1 for item in entries if bool(item.get("disabled"))),
        }
        self._update_state(auth_summary=summary)

    def _inspect_runtime_auth_health(self, entries: list[Dict[str, Any]]) -> None:
        if not entries:
            self._set_auth_phase("missing")
            return
        if any(bool(item.get("runtime_only")) for item in entries):
            self._set_auth_phase("runtime_only")
            return
        if any(bool(item.get("expired")) for item in entries):
            self._set_auth_phase("expired")
            return
        if any(bool(item.get("disabled")) for item in entries):
            self._set_auth_phase("disabled")
            return
        self._set_auth_phase("ready")

    def _parse_log_event(self, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return
        lower = text.lower()

        if "starting local proxy backend" in lower:
            self._set_runtime_phase("starting", stage="bootstrap")
            self._push_event("runtime", "Local companion bootstrap started.")
        elif "starting local companion on " in lower:
            self._set_runtime_phase("starting", stage="local_api")
            self._push_event("runtime", "Local API process is starting.")
        elif "starting app-owned cli proxy" in lower:
            self._set_runtime_phase("starting", stage="sidecar")
            self._push_event("runtime", "CLI proxy sidecar is starting.")
        elif "api server started successfully" in lower:
            self._set_runtime_phase("starting", stage="management_ready")
            self._push_event("runtime", "CLI proxy management API is reachable.")
        elif "companion ready" in lower:
            self._push_event("runtime", "Desktop companion UI is ready.")
        elif "stopping local proxy backend" in lower:
            self._set_runtime_phase("stopping", stage="shutdown")
            self._push_event("runtime", "Stopping local runtime.")
        elif "gemini cli oauth flow" in lower and "starting" in lower:
            self._set_auth_phase("starting")
            self._push_event("auth", "Gemini CLI OAuth flow requested.")
        elif "management api is unavailable" in lower:
            self._set_auth_phase("management_unavailable")
            self._push_event("auth", "Management API is unavailable for OAuth.")
        elif "requesting gemini auth" in lower:
            self._set_auth_phase("requesting_url")
            self._push_event("auth", "Requested Gemini OAuth URL from management API.")
        elif "opening browser" in lower and "gemini cli oauth" in lower:
            self._set_auth_phase("browser_open")
            self._push_event("auth", "Browser opened for Gemini CLI OAuth.")
        elif "waiting for callback" in lower and "gemini cli oauth" in lower:
            self._set_auth_phase("waiting_for_callback")
            self._push_event("auth", "Waiting for OAuth callback on localhost.")
        elif "still waiting for browser callback" in lower:
            self._set_auth_phase("waiting_for_callback")
        elif "auth file saved for" in lower:
            self._set_auth_phase("ready")
            self._push_event("auth", text)
        elif "runtime-only state" in lower:
            self._set_auth_phase("runtime_only")
            self._push_event("auth", text)
        elif "timed out while waiting for completion" in lower:
            self._set_auth_phase("timeout")
            self._push_event("auth", "OAuth timed out before completion.")
        elif "gemini cli oauth failed" in lower or "failed to start" in lower:
            self._set_auth_phase("error")
            self._push_event("auth", text)

        if "server clients and configuration updated:" in lower:
            self._push_event("sidecar", text)
            marker = "auth entries + "
            if marker in lower:
                try:
                    after = lower.split(marker, 1)[1]
                    count = after.split(" gemini api keys", 1)[0].strip()
                    if count.isdigit() and int(count) > 0:
                        self._set_auth_phase("ready")
                except Exception:
                    pass

    def _auth_snapshot(self) -> Dict[str, Any]:
        self._sync_auth_files_across_candidates()
        auth_dir = self._auth_dir_path()
        entry_map: Dict[str, Dict[str, Any]] = {}
        for auth_path in self._auth_dir_candidates():
            if not auth_path.exists():
                continue
            file_paths: list[Path] = []
            for pattern in ("gemini-*.json", "gemini-*.json.disabled"):
                file_paths.extend(auth_path.glob(pattern))
            for path in sorted(file_paths, key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True):
                summary = self._summarize_auth_file(path)
                if summary:
                    key = self._auth_entry_logical_key(summary)
                    entry_map[key] = self._merge_auth_entries(entry_map[key], summary) if key in entry_map else summary
        settings = _configure_local_companion_env()
        for item in self._management_auth_files(settings):
            summary = self._summarize_management_auth_file(item)
            if not summary:
                continue
            key = self._auth_entry_logical_key(summary)
            entry_map[key] = self._merge_auth_entries(entry_map[key], summary) if key in entry_map else summary
        entries = list(entry_map.values())
        entries.sort(
            key=lambda item: (
                bool(item.get("runtime_only")),
                bool(item.get("disabled")),
                bool(item.get("expired")),
                -float(item.get("modified_ts") or 0.0),
            ),
            reverse=False,
        )
        self._update_auth_summary(entries)
        self._inspect_runtime_auth_health(entries)
        return {
            "auth_dir": str(auth_dir),
            "auth_files": entries,
            "auth_file_count": len(entries),
        }

    def _management_headers(self, settings: Dict[str, Any]) -> Dict[str, str]:
        key = str(settings.get("CLI_PROXY_MANAGEMENT_KEY") or os.getenv("RESEARCH_COMPANION_CLI_PROXY_MANAGEMENT_KEY") or "").strip()
        return {"X-Management-Key": key} if key else {}

    def _management_base_url(self, settings: Dict[str, Any]) -> str:
        return str(
            settings.get("CLI_PROXY_MANAGEMENT_BASE_URL")
            or os.getenv("RESEARCH_COMPANION_CLI_PROXY_MANAGEMENT_BASE_URL")
            or ""
        ).rstrip("/")

    def _management_get(
        self,
        settings: Dict[str, Any],
        path: str,
        *,
        timeout: float = 8.0,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        base_url = self._management_base_url(settings)
        if not base_url:
            raise RuntimeError("CLI proxy management base URL is missing.")
        response = _requests_request(
            "GET",
            f"{base_url}/{path.lstrip('/')}",
            timeout=timeout,
            headers=self._management_headers(settings),
            params=params,
        )
        try:
            payload = response.json()
        except Exception:
            payload = {"raw": response.text}
        if response.status_code >= 400:
            detail = ""
            if isinstance(payload, dict):
                detail = str(payload.get("error") or payload.get("message") or payload.get("detail") or "").strip()
            raise RuntimeError(detail or f"Management API returned HTTP {response.status_code}.")
        return payload if isinstance(payload, dict) else {}

    def _gemini_project_id(self, settings: Dict[str, Any]) -> str:
        return str(
            settings.get("GEMINI_CLI_PROJECT_ID")
            or os.getenv("GEMINI_CLI_PROJECT_ID")
            or ""
        ).strip()

    def _management_auth_files(self, settings: Dict[str, Any]) -> list[Dict[str, Any]]:
        try:
            payload = self._management_get(settings, "auth-files", timeout=6.0)
        except Exception:
            return []
        candidates = payload.get("files")
        if not isinstance(candidates, list):
            candidates = payload.get("auth_files")
        if not isinstance(candidates, list):
            candidates = payload.get("data")
        out: list[Dict[str, Any]] = []
        for item in candidates or []:
            if not isinstance(item, dict):
                continue
            provider = str(item.get("provider") or item.get("type") or "").strip().lower()
            if "gemini" not in provider and provider not in {"", "google"}:
                continue
            out.append(item)
        return out

    def _filesystem_gemini_auth_files(self) -> list[Dict[str, Any]]:
        entry_map: Dict[str, Dict[str, Any]] = {}
        for auth_dir in self._auth_dir_candidates():
            if not auth_dir.exists():
                continue
            file_paths: list[Path] = []
            for pattern in ("gemini-*.json", "gemini-*.json.disabled"):
                file_paths.extend(auth_dir.glob(pattern))
            for path in sorted(file_paths, key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True):
                summary = self._summarize_auth_file(path)
                if not summary:
                    continue
                key = self._auth_entry_logical_key(summary)
                entry_map[key] = self._merge_auth_entries(entry_map[key], summary) if key in entry_map else summary
        return list(entry_map.values())

    def _format_gemini_management_snapshot(self, settings: Dict[str, Any]) -> str:
        items = self._management_auth_files(settings)
        if not items:
            return "management auth-files: none"
        parts: list[str] = []
        for item in items[:5]:
            label = str(item.get("filename") or item.get("name") or "runtime-gemini-auth").strip()
            email = str(item.get("email") or "unknown").strip()
            project_id = str(item.get("project_id") or "unknown").strip()
            source = str(item.get("source") or "unknown").strip()
            runtime_only = "runtime_only" if bool(item.get("runtime_only")) else "file_backed"
            status = str(item.get("status") or "unknown").strip()
            parts.append(
                f"{label} email={email} project={project_id} source={source} mode={runtime_only} status={status}"
            )
        suffix = " ..." if len(items) > 5 else ""
        return f"management auth-files ({len(items)}): " + " | ".join(parts) + suffix

    def _format_gemini_filesystem_snapshot(self) -> str:
        files = self._filesystem_gemini_auth_files()
        if not files:
            return "filesystem gemini auth-files: none"
        parts: list[str] = []
        for item in files[:5]:
            filename = str(item.get("filename") or "gemini-auth").strip()
            email = str(item.get("email") or "unknown").strip()
            project_id = str(item.get("project_id") or "unknown").strip()
            parts.append(f"{filename} email={email} project={project_id}")
        suffix = " ..." if len(files) > 5 else ""
        return f"filesystem gemini auth-files ({len(files)}): " + " | ".join(parts) + suffix

    def _log_gemini_oauth_diagnostics(self, settings: Dict[str, Any], *, reason: str) -> None:
        candidate_dirs = ", ".join(str(path) for path in self._auth_dir_candidates())
        self._append_log(f"Gemini CLI OAuth diagnostic: reason={reason}")
        self._append_log(f"Gemini CLI OAuth diagnostic: auth directories={candidate_dirs}")
        try:
            self._append_log(f"Gemini CLI OAuth diagnostic: {self._format_gemini_filesystem_snapshot()}")
        except Exception as exc:
            self._append_log(f"Gemini CLI OAuth diagnostic: filesystem snapshot failed: {exc}")
        try:
            self._append_log(f"Gemini CLI OAuth diagnostic: {self._format_gemini_management_snapshot(settings)}")
        except Exception as exc:
            self._append_log(f"Gemini CLI OAuth diagnostic: management snapshot failed: {exc}")

    def _sync_auth_files_across_candidates(self) -> int:
        candidates = self._auth_dir_candidates()
        source_files: list[Path] = []
        seen_sources: set[str] = set()

        for auth_dir in candidates:
            if not auth_dir.exists():
                continue
            for pattern in ("gemini-*.json", "gemini-*.json.disabled"):
                for path in auth_dir.glob(pattern):
                    try:
                        resolved = str(path.resolve())
                    except Exception:
                        resolved = str(path)
                    if resolved in seen_sources:
                        continue
                    seen_sources.add(resolved)
                    source_files.append(path)

        copied = 0
        for source in source_files:
            for target_dir in candidates:
                try:
                    if target_dir.resolve() == source.parent.resolve():
                        continue
                except Exception:
                    if str(target_dir) == str(source.parent):
                        continue
                try:
                    target_dir.mkdir(parents=True, exist_ok=True)
                    target = target_dir / source.name
                    import shutil
                    if target.exists():
                        try:
                            source_stat = source.stat()
                            target_stat = target.stat()
                            unchanged = (
                                abs(source_stat.st_mtime - target_stat.st_mtime) < 1
                                and source_stat.st_size == target_stat.st_size
                            )
                            if unchanged:
                                continue
                        except Exception:
                            continue
                    shutil.copy2(source, target)
                    copied += 1
                except Exception:
                    continue
        return copied

    def _wait_for_management_ready(self, settings: Dict[str, Any], *, timeout: float = 25.0) -> bool:
        deadline = time.time() + timeout
        last_error = ""
        while time.time() < deadline:
            try:
                self._management_get(settings, "auth-files", timeout=4.0)
                return True
            except Exception as exc:
                last_error = str(exc)
            time.sleep(0.8)
        if last_error:
            self._append_log(f"Gemini CLI OAuth: management API not ready: {last_error}")
        return False

    def _refresh_gemini_auth_after_success(
        self,
        settings: Dict[str, Any],
        *,
        initial_file_signatures: set[str],
        retries: int = 3,
        delay_seconds: float = 0.5,
    ) -> tuple[bool, str]:
        time.sleep(delay_seconds)
        for attempt in range(retries + 1):
            mirrored = self._sync_auth_files_across_candidates()
            fs_files = self._filesystem_gemini_auth_files()
            current_signatures = {self._auth_entry_signature(item) for item in fs_files}
            if current_signatures and current_signatures != initial_file_signatures:
                new_files = [item for item in fs_files if self._auth_entry_signature(item) not in initial_file_signatures]
                first = new_files[0] if new_files else fs_files[0]
                email = str(first.get("email") or "").strip()
                base_filename = str(first.get("base_filename") or first.get("filename") or "").strip()
                if base_filename:
                    self.set_active_auth_file(base_filename)
                mirror_note = f" Mirrored {mirrored} auth file(s) across companion folders." if mirrored else ""
                return True, f"Auth file saved for {email or 'the account'} and set as active.{mirror_note}"
            management_files = self._management_auth_files(settings)
            for item in management_files:
                runtime_only = bool(item.get("runtime_only"))
                email = str(item.get("email") or "").strip()
                if runtime_only:
                    if attempt >= retries:
                        self._log_gemini_oauth_diagnostics(settings, reason="runtime_only_auth")
                        return False, f"OAuth loaded for {email or 'the account'}, but CLI proxy kept it in runtime-only state and did not save a local auth file."
                    break
                if email and current_signatures:
                    first = fs_files[0]
                    mirror_note = f" Mirrored {mirrored} auth file(s) across companion folders." if mirrored else ""
                    return True, f"Auth file saved for {str(first.get('email') or email).strip() or 'the account'}.{mirror_note}"
                if email:
                    if attempt >= retries:
                        candidate_dirs = ", ".join(str(path) for path in self._auth_dir_candidates())
                        self._log_gemini_oauth_diagnostics(settings, reason="management_has_account_but_no_file")
                        return False, (
                            f"OAuth completed for {email}, but no local Gemini auth file appeared in the auth directories. "
                            f"Checked: {candidate_dirs}"
                        )
                    break
            if attempt < retries:
                time.sleep(delay_seconds)
        candidate_dirs = ", ".join(str(path) for path in self._auth_dir_candidates())
        self._log_gemini_oauth_diagnostics(settings, reason="no_management_account_and_no_file")
        return False, f"OAuth completed, but no local Gemini auth file was found. Checked: {candidate_dirs}"

    def _run_gemini_cli_oauth_flow(self) -> None:
        self.oauth_cancel_event.clear()
        settings = _configure_local_companion_env()
        if not self.is_backend_running():
            result = self.start_backend()
            if not result.get("ok"):
                self._append_log("Gemini CLI OAuth: failed to start local proxy first.")
                return
            self._append_log("Gemini CLI OAuth: waiting for local proxy to become ready...")

        if not self._wait_for_management_ready(settings):
            self._append_log("Gemini CLI OAuth: management API is unavailable. Restarting proxy with the latest config...")
            self.stop_backend()
            time.sleep(1.0)
            result = self.start_backend()
            if not result.get("ok") or not self._wait_for_management_ready(settings):
                self._append_log("Gemini CLI OAuth: management API is still unavailable after restart.")
                return

        initial_file_signatures = {self._auth_entry_signature(item) for item in self._filesystem_gemini_auth_files()}

        try:
            project_id = self._gemini_project_id(settings)
            query_params: Dict[str, Any] = {"is_webui": "true"}
            if project_id:
                query_params["project_id"] = project_id
            payload = self._management_get(
                settings,
                "gemini-cli-auth-url",
                timeout=12.0,
                params=query_params,
            )
            auth_url = str(payload.get("url") or "").strip()
            oauth_state = str(payload.get("state") or "").strip()
            if not auth_url or not oauth_state:
                raise RuntimeError("Management API did not return a valid Gemini OAuth URL.")
            if project_id:
                self._append_log(f"Gemini CLI OAuth: requesting Gemini auth with project_id={project_id}")
            else:
                self._append_log("Gemini CLI OAuth: requesting Gemini auth without explicit project_id")
            self._append_log("Gemini CLI OAuth: opening browser...")
            listener_ready = _can_connect_tcp("127.0.0.1", 8085)
            self._append_log(
                "Gemini CLI OAuth: local callback listener on 127.0.0.1:8085 is "
                + ("ready." if listener_ready else "not reachable yet.")
            )
            self._append_log("Gemini CLI OAuth: waiting for callback on http://localhost:8085/oauth2callback")
            _open_in_browser(auth_url)
        except Exception as exc:
            self._append_log(f"Gemini CLI OAuth failed to start: {exc}")
            return

        deadline = time.time() + 120.0
        last_wait_log_at = 0.0
        last_status = "wait"
        while time.time() < deadline:
            if self.oauth_cancel_event.is_set():
                self._append_log("Gemini CLI OAuth: previous sign-in attempt was cancelled. Starting over is available now.")
                self._set_auth_phase("idle")
                return
            try:
                payload = self._management_get(
                    settings,
                    "get-auth-status",
                    timeout=8.0,
                    params={"state": oauth_state},
                )
            except Exception as exc:
                self._append_log(f"Gemini CLI OAuth status check failed: {exc}")
                time.sleep(1.0)
                continue

            status = str(payload.get("status") or "wait").strip().lower()
            last_status = status
            if status == "ok":
                saved, message = self._refresh_gemini_auth_after_success(
                    settings,
                    initial_file_signatures=initial_file_signatures,
                )
                self._append_log(f"Gemini CLI OAuth: {message}")
                return
            if status in {"error", "failed"}:
                detail = str(payload.get("message") or payload.get("error") or "Unknown error.").strip()
                self._append_log(f"Gemini CLI OAuth failed: {detail}")
                return
            now = time.time()
            if now - last_wait_log_at >= 15.0:
                listener_ready = _can_connect_tcp("127.0.0.1", 8085)
                self._append_log(
                    "Gemini CLI OAuth: still waiting for browser callback "
                    f"(state={oauth_state}, listener={'ready' if listener_ready else 'not reachable'})."
                )
                last_wait_log_at = now
            time.sleep(1.0)

        listener_ready = _can_connect_tcp("127.0.0.1", 8085)
        candidate_dirs = ", ".join(str(path) for path in self._auth_dir_candidates())
        self._append_log(
            "Gemini CLI OAuth timed out while waiting for completion. "
            f"Last status={last_status}. "
            f"Callback listener={'ready' if listener_ready else 'not reachable'}. "
            f"Auth directories: {candidate_dirs}"
        )
        self._append_log(
            "Gemini CLI OAuth diagnostic: sidecar never reported status=ok/error before timeout. "
            "The browser callback may not have reached CLI proxy, or onboarding is still stuck upstream."
        )
        self._log_gemini_oauth_diagnostics(settings, reason="callback_not_completed")

    def start_monitor(self) -> None:
        if self.health_thread and self.health_thread.is_alive():
            return
        self._set_next_update_check(immediate=True)
        self.health_thread = threading.Thread(target=self._health_loop, daemon=True)
        self.health_thread.start()
        auto_update = self.release_config.get("auto_update") if isinstance(self.release_config.get("auto_update"), dict) else {}
        if bool(auto_update.get("check_on_startup", True)):
            self._start_update_check(manual=False)

    def _update_state(self, **updates: Any) -> None:
        with self.state_lock:
            self.state.update(updates)

    def _snapshot(self) -> Dict[str, Any]:
        with self.state_lock:
            snapshot = dict(self.state)
            snapshot["logs"] = list(self.log_lines)
            snapshot["log_seq"] = self.log_seq
            snapshot["events"] = list(self.state.get("events") or [])
            return snapshot

    def log(self, message: str) -> None:
        self._append_log(message)

    def _append_log(self, message: str) -> None:
        text = str(message or "").rstrip()
        if not text:
            return
        self._parse_log_event(text)
        timestamp = time.strftime("%H:%M:%S")
        line = f"[{timestamp}] {text}"
        with self.state_lock:
            self.log_lines.append(line)
            self.log_seq += 1
            self.state["logs"] = list(self.log_lines)
            self.state["log_seq"] = self.log_seq

    def _set_running_ui(self, *, running: bool, status: str, detail: str) -> None:
        runtime_phase = "ready" if running and status == "Running" else "stopped" if not running else None
        self._update_state(
            running=running,
            badge="ON" if running else "OFF",
            status=status,
            detail=detail,
            **({"runtime_phase": runtime_phase} if runtime_phase else {}),
        )

    def _spawn_command(self) -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "--backend"]
        return [sys.executable, "-u", str(Path(__file__).resolve()), "--backend"]

    def is_backend_running(self) -> bool:
        with self.state_lock:
            process = self.process
        return process is not None and process.poll() is None

    def _read_aux_process_output(self, process: subprocess.Popen[str], prefix: str) -> None:
        if not process.stdout:
            return
        for line in process.stdout:
            self._append_log(f"{prefix}: {line.rstrip()}")

    def _read_process_output(self) -> None:
        process = self.process
        if not process or not process.stdout:
            return
        for line in process.stdout:
            self._append_log(line.rstrip("\n"))

    def start_gemini_cli_oauth(self) -> Dict[str, Any]:
        settings = _configure_local_companion_env()
        proxy_binary = settings["CLI_PROXY_BINARY_PATH"]
        if not proxy_binary:
            self._append_log("Gemini CLI OAuth unavailable: CLI proxy binary not found.")
            return {"ok": False, "message": "CLI proxy binary was not found."}
        if self.oauth_thread and self.oauth_thread.is_alive():
            self._append_log("Gemini CLI OAuth is already waiting for browser callback. Cancelling it and opening a fresh sign-in tab...")
            self.oauth_cancel_event.set()
            self.oauth_thread.join(timeout=2.0)
            if self.oauth_thread.is_alive():
                self._append_log("Gemini CLI OAuth is still stopping. Try Connect Gemini again in a moment.")
                return {"ok": False, "message": "Previous Gemini sign-in is still stopping. Try again in a moment."}

        self.oauth_cancel_event.clear()
        self._append_log("Starting Gemini CLI OAuth flow...")
        self.oauth_thread = threading.Thread(target=self._run_gemini_cli_oauth_flow, daemon=True)
        self.oauth_thread.start()
        return {"ok": True}

    def start_backend(self) -> Dict[str, Any]:
        _, project_root = _runtime_paths()
        with self.state_lock:
            if self.process and self.process.poll() is None:
                return {"ok": True, "message": "Proxy is already running."}

        self._append_log("Starting local proxy backend...")
        self._set_runtime_phase("starting", stage="spawn")
        self.started_at = time.time()
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        process = subprocess.Popen(
            self._spawn_command(),
            cwd=str(project_root()),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=env,
            creationflags=creationflags,
        )
        with self.state_lock:
            self.process = process
            self.reader_thread = threading.Thread(target=self._read_process_output, daemon=True)
            self.reader_thread.start()
        self._set_running_ui(
            running=True,
            status="Starting...",
            detail="Waiting for http://127.0.0.1:8787/api/live",
        )
        return {"ok": True}

    def stop_backend(self) -> Dict[str, Any]:
        with self.state_lock:
            process = self.process

        if not process or process.poll() is not None:
            self._set_running_ui(running=False, status="Stopped", detail="Local API: http://127.0.0.1:8787")
            self._set_runtime_phase("stopped", stage="idle")
            return {"ok": True, "message": "Proxy is already stopped."}

        self._append_log("Stopping local proxy backend...")
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            process.wait(timeout=6)
        except Exception:
            self._append_log("Process tree stop timed out. Killing backend process...")
            try:
                process.kill()
                process.wait(timeout=3)
            except Exception:
                pass
        finally:
            with self.state_lock:
                if self.process is process:
                    self.process = None
            self._set_running_ui(running=False, status="Stopped", detail="Local API: http://127.0.0.1:8787")
            self._set_runtime_phase("stopped", stage="idle")
        return {"ok": True}

    def clear_log(self) -> Dict[str, Any]:
        with self.state_lock:
            self.log_lines.clear()
            self.log_seq += 1
            self.state["logs"] = []
            self.state["log_seq"] = self.log_seq
        self._append_log("Log cleared.")
        return {"ok": True}

    def open_web_app(self) -> Dict[str, Any]:
        _open_in_browser(LOCAL_WEB_APP_URL)
        return {"ok": True}

    def check_for_updates(self) -> Dict[str, Any]:
        return self._start_update_check(manual=True)

    def open_release_notes(self) -> Dict[str, Any]:
        snapshot = self._snapshot()
        release_url = str(snapshot.get("update_release_url") or "").strip()
        if release_url:
            _open_in_browser(release_url)
            return {"ok": True}
        notes_path = self._release_notes_path()
        if notes_path:
            _open_in_file_explorer(notes_path)
            return {"ok": True}
        return {"ok": False, "message": "Release notes are not available."}

    def install_update(self) -> Dict[str, Any]:
        snapshot = self._snapshot()
        download_url = str(snapshot.get("update_download_url") or "").strip()
        target_version = str(snapshot.get("update_latest_version") or "").strip()
        asset_name = str(snapshot.get("update_asset_name") or self.release_config.get("release_asset_name") or "").strip()

        if not download_url:
            return {"ok": False, "message": "No downloadable update is available yet."}

        safe_version = "".join(ch for ch in (target_version or "latest") if ch.isalnum() or ch in {".", "-", "_"})
        suffix = Path(asset_name or "ResearchCompanionSetup.exe").suffix or ".exe"
        download_name = f"ResearchCompanionSetup-{safe_version}{suffix}"
        update_downloads_dir = self._update_downloads_dir()
        for stale_installer in update_downloads_dir.glob("ResearchCompanionSetup-*.exe"):
            try:
                stale_installer.unlink()
            except Exception:
                continue
        target_path = update_downloads_dir / download_name

        try:
            self._update_state_fields(
                update_status="stopping_proxy",
                update_message="Stopping local proxy before update...",
            )
            self._append_log("Stopping local proxy before update...")
            self.stop_backend()

            self._update_state_fields(
                update_status="downloading",
                update_message=f"Downloading installer {download_name}... 0%",
                update_download_percent=0,
                update_download_bytes=0,
                update_download_total_bytes=0,
            )
            session = _local_requests_session()
            with session.get(
                download_url,
                timeout=60.0,
                headers={"User-Agent": "ResearchCompanionUpdater"},
                stream=True,
            ) as response:
                if not response.ok:
                    raise RuntimeError(f"Installer download returned HTTP {response.status_code}.")
                total_bytes = int(response.headers.get("content-length") or 0)
                downloaded_bytes = 0
                last_reported_percent = -1
                self._update_state_fields(update_download_total_bytes=total_bytes)
                with target_path.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            handle.write(chunk)
                            downloaded_bytes += len(chunk)
                            percent = int((downloaded_bytes * 100) / total_bytes) if total_bytes else 0
                            if percent != last_reported_percent:
                                last_reported_percent = percent
                                progress_label = f"{percent}%" if total_bytes else f"{downloaded_bytes / (1024 * 1024):.1f} MB"
                                self._update_state_fields(
                                    update_status="downloading",
                                    update_message=f"Downloading installer {download_name}... {progress_label}",
                                    update_download_percent=percent,
                                    update_download_bytes=downloaded_bytes,
                                    update_download_total_bytes=total_bytes,
                                )
            self._update_state_fields(
                update_download_percent=100,
                update_download_bytes=downloaded_bytes,
                update_download_total_bytes=total_bytes or downloaded_bytes,
            )
            self._append_log(f"Downloaded update installer to {target_path}")

            self._update_state_fields(
                update_status="launching_installer",
                update_message="Download complete. Launching update installer...",
            )
            creationflags = 0
            if os.name == "nt":
                creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
            subprocess.Popen([str(target_path)], close_fds=True, creationflags=creationflags)
            self._append_log(f"Launched update installer: {target_path}")

            self._update_state_fields(
                update_status="closing_for_update",
                update_message="Installer launched. Research Companion will close so the update can continue.",
            )
            self._schedule_close_for_update()
            return {"ok": True, "path": str(target_path)}
        except Exception as exc:
            self._update_state_fields(
                update_status="error",
                update_message=f"Update install failed: {exc}",
            )
            self._append_log(f"Update install failed: {exc}")
            return {"ok": False, "message": str(exc)}

    def _schedule_close_for_update(self) -> None:
        self.closing_for_update = True

        def close_later() -> None:
            time.sleep(0.8)
            try:
                self.shutdown()
            finally:
                callback = self.close_callback
                if callback:
                    try:
                        callback()
                        return
                    except Exception:
                        pass
                os._exit(0)

        threading.Thread(target=close_later, daemon=True).start()

    def get_state(self) -> Dict[str, Any]:
        snapshot = self._snapshot()
        snapshot.update(self._auth_snapshot())
        return snapshot

    def open_auth_folder(self) -> Dict[str, Any]:
        auth_dir = self._auth_dir_path()
        for candidate in self._auth_dir_candidates():
            if candidate.exists():
                auth_dir = candidate
                break
        auth_dir.mkdir(parents=True, exist_ok=True)
        _open_in_file_explorer(auth_dir)
        return {"ok": True}

    def refresh_auth_files(self) -> Dict[str, Any]:
        mirrored = self._sync_auth_files_across_candidates()
        snapshot = self._auth_snapshot()
        self._append_log(
            f"Auth files refreshed. Found {snapshot.get('auth_file_count', 0)} Gemini auth entry(s)."
            + (f" Mirrored {mirrored} file(s)." if mirrored else "")
        )
        return {"ok": True, "mirrored": mirrored, "snapshot": snapshot}

    def delete_auth_file(self, filename: str) -> Dict[str, Any]:
        targets = self._auth_file_paths_for_name(filename)
        if not targets:
            return {"ok": False, "message": "Auth file was not found."}
        deleted = 0
        for path in targets:
            try:
                path.unlink(missing_ok=True)
                deleted += 1
            except Exception:
                continue
        self._append_log(f"Deleted auth file set for {self._normalize_auth_filename(filename)} ({deleted} path(s)).")
        return {"ok": True, "deleted": deleted}

    def set_auth_file_disabled(self, filename: str, disabled: bool) -> Dict[str, Any]:
        targets = self._auth_file_paths_for_name(filename)
        if not targets:
            return {"ok": False, "message": "Auth file was not found."}
        updated = 0
        base = self._normalize_auth_filename(filename)
        for path in targets:
            if disabled:
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(payload, dict):
                        payload["checked"] = False
                        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
                except Exception:
                    pass
            target = path.with_name(f"{base}.disabled") if disabled else path.with_name(base)
            if path == target:
                updated += 1
                continue
            try:
                if target.exists():
                    path.unlink(missing_ok=True)
                else:
                    path.rename(target)
                updated += 1
            except Exception:
                continue
        verb = "Disabled" if disabled else "Enabled"
        self._append_log(f"{verb} auth file set for {base} ({updated} path(s)).")
        return {"ok": True, "updated": updated}

    def set_active_auth_file(self, filename: str) -> Dict[str, Any]:
        base = self._normalize_auth_filename(filename)
        targets = [path for path in self._auth_file_paths_for_name(filename) if not path.name.endswith(".disabled")]
        if not base or not targets:
            return {"ok": False, "message": "Auth file was not found."}

        selected_paths: set[str] = set()
        for path in targets:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            auth_payload = cast(Dict[str, Any], payload)
            auth_type = str(auth_payload.get("type") or auth_payload.get("provider") or "").strip().lower()
            token_raw = auth_payload.get("token")
            token = cast(Dict[str, Any], token_raw) if isinstance(token_raw, dict) else {}
            if auth_type in {"gemini", "gemini-cli"} and token and not bool(auth_payload.get("disabled")):
                selected_paths.add(str(path.resolve()))
        if not selected_paths:
            return {"ok": False, "message": "Selectable Gemini auth file was not found."}

        updated = 0
        for auth_dir in self._auth_dir_candidates():
            if not auth_dir.exists():
                continue
            for path in auth_dir.glob("*.json"):
                try:
                    resolved = str(path.resolve())
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not isinstance(payload, dict):
                    continue
                auth_payload = cast(Dict[str, Any], payload)
                auth_type = str(auth_payload.get("type") or auth_payload.get("provider") or "").strip().lower()
                token_raw = auth_payload.get("token")
                token = cast(Dict[str, Any], token_raw) if isinstance(token_raw, dict) else {}
                if auth_type not in {"gemini", "gemini-cli"} or not token or bool(auth_payload.get("disabled")):
                    continue
                auth_payload["checked"] = resolved in selected_paths
                try:
                    path.write_text(json.dumps(auth_payload, indent=2, ensure_ascii=False), encoding="utf-8")
                    updated += 1
                except Exception:
                    continue

        self._append_log(f"Selected active Gemini account: {base} ({updated} auth file(s) updated).")
        return {"ok": True, "updated": updated, "snapshot": self._auth_snapshot()}

    def shutdown(self) -> None:
        self.stop_event.set()
        try:
            self.stop_backend()
        except Exception:
            pass

    def _health_loop(self) -> None:
        while not self.stop_event.is_set():
            if self._updates_configured():
                with self.state_lock:
                    should_check = (
                        time.time() >= self.next_update_check_at
                        and not (self.update_thread and self.update_thread.is_alive())
                    )
                if should_check:
                    self._start_update_check(manual=False)
            try:
                response = _requests_get(LOCAL_LIVE_URL, timeout=1.2)
                payload = response.json()
                if not (response.ok and payload.get("ok") and payload.get("service") == "research-companion"):
                    raise RuntimeError("Liveness endpoint returned non-ok payload.")

                health_payload: Dict[str, Any] = {}
                try:
                    health_response = _requests_get(LOCAL_HEALTH_URL, timeout=1.2)
                    if health_response.ok:
                        health_payload = health_response.json()
                except Exception:
                    health_payload = {}
                self.last_health_payload = health_payload or payload
                auth_phase = str(self.state.get("auth_phase") or "idle")
                runtime = "gemini_cli_oauth" if auth_phase in {"ready", "runtime_only"} else "user_oauth"
                self._update_state(
                    oauth_source=auth_phase,
                    runtime_identity=runtime,
                )
                self._set_running_ui(
                    running=True,
                    status="Running",
                    detail=f"Local Web UI: {LOCAL_WEB_APP_URL} | gemini_auth={auth_phase}",
                )
                self._set_runtime_phase("ready", stage="api_live")
            except Exception:
                with self.state_lock:
                    process = self.process
                running = process is not None and process.poll() is None
                if running and time.time() - self.started_at < BACKEND_READY_TIMEOUT:
                    self._set_running_ui(
                        running=True,
                        status="Starting...",
                        detail="Process is up, waiting for local API...",
                    )
                    self._set_runtime_phase("starting", stage="waiting_live")
                elif running:
                    self._set_running_ui(
                        running=True,
                        status="Running (API unavailable)",
                        detail="Process is alive but liveness probe failed.",
                    )
                    self._set_runtime_phase("degraded", stage="live_unavailable")
                else:
                    self._set_running_ui(
                        running=False,
                        status="Stopped",
                        detail="Local API: http://127.0.0.1:8787",
                    )
                    self._set_runtime_phase("stopped", stage="idle")
                    with self.state_lock:
                        if self.process and self.process.poll() is not None:
                            self.process = None
            self.stop_event.wait(STATE_POLL_INTERVAL)


class WebviewApi:
    def __init__(self, controller: CompanionController) -> None:
        self.controller = controller

    def get_state(self) -> Dict[str, Any]:
        return self.controller.get_state()

    def start_backend(self) -> Dict[str, Any]:
        return self.controller.start_backend()

    def stop_backend(self) -> Dict[str, Any]:
        return self.controller.stop_backend()

    def start_gemini_cli_oauth(self) -> Dict[str, Any]:
        return self.controller.start_gemini_cli_oauth()

    def clear_log(self) -> Dict[str, Any]:
        return self.controller.clear_log()

    def open_web_app(self) -> Dict[str, Any]:
        return self.controller.open_web_app()

    def open_auth_folder(self) -> Dict[str, Any]:
        return self.controller.open_auth_folder()

    def refresh_auth_files(self) -> Dict[str, Any]:
        return self.controller.refresh_auth_files()

    def delete_auth_file(self, filename: str) -> Dict[str, Any]:
        return self.controller.delete_auth_file(filename)

    def set_auth_file_disabled(self, filename: str, disabled: bool) -> Dict[str, Any]:
        return self.controller.set_auth_file_disabled(filename, disabled)

    def set_active_auth_file(self, filename: str) -> Dict[str, Any]:
        return self.controller.set_active_auth_file(filename)

    def check_for_updates(self) -> Dict[str, Any]:
        return self.controller.check_for_updates()

    def install_update(self) -> Dict[str, Any]:
        return self.controller.install_update()

    def open_release_notes(self) -> Dict[str, Any]:
        return self.controller.open_release_notes()


def run_backend_mode() -> None:
    _, project_root = _runtime_paths()
    proxy_process: Optional[subprocess.Popen[str]] = None
    _backend_log("Backend bootstrap starting.")
    try:
        settings = _configure_local_companion_env()
        _backend_log(f"Configured PUBLIC_BACKEND_URL={settings['PUBLIC_BACKEND_URL']}")
        _backend_log(f"CLI proxy auth dir={settings['CLI_PROXY_AUTH_DIR']}")
        _backend_log(f"Editable backend dir={settings.get('EDITABLE_BACKEND_DIR', '')}")
        print(f"Starting local companion on {settings['PUBLIC_BACKEND_URL']}", flush=True)
        print(f"Research Companion proxy auth dir: {settings['CLI_PROXY_AUTH_DIR']}", flush=True)
        if settings.get("EDITABLE_BACKEND_DIR"):
            print(f"Research Companion editable backend dir: {settings['EDITABLE_BACKEND_DIR']}", flush=True)
        _backend_log(f"OAuth redirect URI: http://127.0.0.1:8787/oauth/google/callback")

        proxy_binary = settings["CLI_PROXY_BINARY_PATH"]
        proxy_config = settings["CLI_PROXY_CONFIG_PATH"]
        if proxy_binary:
            proxy_command = [proxy_binary, "--config", proxy_config]
            proxy_env = os.environ.copy()
            proxy_env["WRITABLE_PATH"] = str(Path(proxy_config).resolve().parent)
            print(f"Starting app-owned CLI proxy on http://127.0.0.1:{settings['CLI_PROXY_PORT']}", flush=True)
            _backend_log(f"Launching CLI proxy binary={proxy_binary}")
            _backend_log(f"CLI proxy WRITABLE_PATH={proxy_env['WRITABLE_PATH']}")
            proxy_process = subprocess.Popen(
                proxy_command,
                cwd=str(project_root()),
                stdout=None,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env=proxy_env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            print("CLI proxy binary not found. Restore the packaged cli-proxy-api.exe and try again.", flush=True)
            _backend_log("CLI proxy binary missing.")

        _backend_log("Importing uvicorn and backend_api.")
        import uvicorn
        backend_api, backend_source = _load_runtime_backend_module(settings)
        if backend_source:
            _backend_log(f"Loaded backend app from {backend_source}")
            print(f"Backend source: {backend_source}", flush=True)

        host = os.getenv("HOST", "127.0.0.1")
        port = int(os.getenv("PORT", "8787"))
        _backend_log(f"Starting uvicorn on {host}:{port}.")
        uvicorn.run(
            backend_api.app,
            host=host,
            port=port,
            reload=False,
            access_log=False,
            log_config=None,
        )
    except Exception as exc:
        import traceback

        _backend_log(f"Backend bootstrap failed: {exc}")
        _backend_log(traceback.format_exc())
        raise
    finally:
        _backend_log("Backend shutdown requested.")
        if proxy_process and proxy_process.poll() is None:
            proxy_process.terminate()
            try:
                proxy_process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                proxy_process.kill()
                proxy_process.wait(timeout=2)


def run_gemini_cli_oauth_mode() -> int:
    controller = CompanionController(start_monitor=False)
    try:
        result = controller.start_gemini_cli_oauth()
        if not result.get("ok"):
            print(str(result.get("message") or "Gemini CLI OAuth launch failed."), flush=True)
            return 1
        print("Gemini CLI OAuth launch requested.", flush=True)
        return 0
    finally:
        controller.stop_event.set()


def run_gui_mode() -> None:
    import webview

    bundle_root, _ = _runtime_paths()
    controller = CompanionController(start_monitor=False)
    api = WebviewApi(controller)
    if getattr(sys, "frozen", False):
        ui_path = (bundle_root() / "companion_ui" / "index.html").resolve()
    else:
        ui_path = (REPO_ROOT / "desktop" / "ui" / "index.html").resolve()
    if not ui_path.exists():
        raise FileNotFoundError(f"Missing companion UI asset: {ui_path}")

    window = webview.create_window(
        APP_TITLE,
        url=ui_path.as_uri(),
        js_api=api,
        width=620,
        height=590,
        min_size=(600, 560),
        text_select=True,
    )

    tray = CompanionTray(window, controller)

    def close_window_for_update() -> None:
        tray.stop()
        os._exit(0)

    controller.close_callback = close_window_for_update

    def on_closing() -> bool:
        if tray.quit_requested or controller.closing_for_update:
            tray.stop()
            controller.shutdown()
            return True
        if tray.hide_window():
            return False
        if controller.is_backend_running() and not _confirm_exit_dialog():
            return False
        controller.shutdown()
        return True

    def start_companion() -> None:
        tray.start()
        controller.start_monitor()

    window.events.closing += on_closing
    webview.start(func=start_companion, debug=False)


if __name__ == "__main__":
    if "--backend" in sys.argv:
        run_backend_mode()
    elif "--oauth-login" in sys.argv:
        raise SystemExit(run_gemini_cli_oauth_mode())
    else:
        run_gui_mode()
