from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import sys
from pathlib import Path
from typing import Dict, Optional


DEFAULT_PUBLIC_BACKEND_URL = "http://127.0.0.1:8787"
DEFAULT_FRONTEND_ORIGIN = "https://sci-vnucea.web.app"
DEFAULT_REMOTE_RESOURCE_API_BASE_URL = "https://research-backend-213473562655.asia-southeast1.run.app"
DEFAULT_LOCATION = "global"
DEFAULT_GOOGLE_OAUTH_PROJECT_ID = "sci-vnucea"
DEFAULT_GEMINI_CLI_PROJECT_ID = "GOOGLE_ONE"
DEFAULT_PROXY_HOST = "127.0.0.1"
DEFAULT_PROXY_PORT = 8797
DEFAULT_APP_DIRNAME = "ResearchCompanion"
DEFAULT_PROXY_CONFIG_NAME = "cli-proxy-config.yaml"
DEFAULT_PROXY_STATE_NAME = "cli-proxy-runtime.json"
DEFAULT_PROXY_AUTH_DIRNAME = "cli-proxy-auth"
DEFAULT_PROXY_LOG_DIRNAME = "logs"
DEFAULT_PROXY_BIN_DIRNAME = "bin"
DEFAULT_EDITABLE_BACKEND_DIRNAME = "editable-backend"
CLI_PROXY_BINARY_NAME = "cli-proxy-api.exe" if os.name == "nt" else "cli-proxy-api"
EDITABLE_BACKEND_FILES = ("backend_api.py", "backend_core.py")


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def config_search_roots(root: Optional[Path] = None) -> list[Path]:
    root = (root or project_root()).resolve()
    cwd = Path.cwd().resolve()
    candidates = [
        root,
        root.parent,
        cwd,
        cwd.parent,
        bundle_root(),
        bundle_root().parent,
    ]
    ordered: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key not in seen and candidate.exists():
            ordered.append(candidate)
            seen.add(key)
    return ordered


def bundle_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS")).resolve()
    return project_root()


def companion_data_dir(root: Optional[Path] = None) -> Path:
    local_appdata = os.getenv("LOCALAPPDATA", "").strip()
    if local_appdata:
        return Path(local_appdata) / DEFAULT_APP_DIRNAME
    root = root or project_root()
    return root / ".research-companion"


def default_cli_proxy_auth_dir() -> Path:
    try:
        home = Path.home()
    except Exception:
        home = None
    if home:
        return home / ".cli-proxy-api"
    local_appdata = os.getenv("LOCALAPPDATA", "").strip()
    if local_appdata:
        return Path(local_appdata) / DEFAULT_APP_DIRNAME / DEFAULT_PROXY_AUTH_DIRNAME
    return project_root() / ".research-companion" / DEFAULT_PROXY_AUTH_DIRNAME


def legacy_cli_proxy_auth_dir(data_dir: Path) -> Path:
    return data_dir / DEFAULT_PROXY_AUTH_DIRNAME


def load_env_file(env_path: Path) -> Dict[str, str]:
    loaded: Dict[str, str] = {}
    if not env_path.exists():
        return loaded

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        loaded[key.strip()] = value
    return loaded


def apply_env_mapping(values: Dict[str, str]) -> None:
    for key, value in values.items():
        os.environ[key] = value


def load_client_secret_from_file(root: Path) -> Dict[str, str]:
    for search_root in config_search_roots(root):
        for candidate in search_root.glob("client_secret_*.json"):
            try:
                raw = json.loads(candidate.read_text(encoding="utf-8"))
                web = raw.get("web") if isinstance(raw, dict) else None
                if not isinstance(web, dict):
                    continue
                return {
                    "GOOGLE_OAUTH_CLIENT_ID": str(web.get("client_id") or "").strip(),
                    "GOOGLE_OAUTH_CLIENT_SECRET": str(web.get("client_secret") or "").strip(),
                    "GOOGLE_OAUTH_PROJECT_ID": str(web.get("project_id") or "").strip(),
                }
            except Exception:
                continue
    return {}


def _read_json(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, payload: Dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


def _reserve_port(preferred: int) -> int:
    for port in range(preferred, preferred + 30):
        if _is_port_open(DEFAULT_PROXY_HOST, port):
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((DEFAULT_PROXY_HOST, port))
            except OSError:
                continue
            return port
    return preferred


def _packaged_cli_proxy_binary_candidates(root: Path) -> list[Path]:
    bundle = bundle_root()
    return [
        root / "packaging" / DEFAULT_PROXY_BIN_DIRNAME / CLI_PROXY_BINARY_NAME,
        bundle / CLI_PROXY_BINARY_NAME,
        bundle / DEFAULT_PROXY_BIN_DIRNAME / CLI_PROXY_BINARY_NAME,
    ]


def _materialize_app_owned_binary(root: Path, data_dir: Path) -> str:
    packaged_candidates = _packaged_cli_proxy_binary_candidates(root)
    source_path: Optional[Path] = None
    for candidate in packaged_candidates:
        if candidate.exists():
            source_path = candidate.resolve()
            break
    if source_path is None:
        return ""

    target_dir = data_dir / DEFAULT_PROXY_BIN_DIRNAME
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / CLI_PROXY_BINARY_NAME

    copy_required = True
    if target_path.exists():
        try:
            copy_required = (
                source_path.stat().st_size != target_path.stat().st_size
                or int(source_path.stat().st_mtime) != int(target_path.stat().st_mtime)
            )
        except Exception:
            copy_required = True
    if copy_required:
        try:
            shutil.copy2(source_path, target_path)
        except PermissionError:
            if not target_path.exists():
                raise
    return str(target_path.resolve())


def _discover_cli_proxy_binary(root: Path, data_dir: Path) -> str:
    env_candidate = os.getenv("RESEARCH_COMPANION_CLI_PROXY_BINARY", "").strip()
    materialized_path = _materialize_app_owned_binary(root, data_dir)
    candidates = [
        env_candidate,
        materialized_path,
        str(data_dir / DEFAULT_PROXY_BIN_DIRNAME / CLI_PROXY_BINARY_NAME),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(Path(candidate).resolve())
    return ""


def editable_backend_dir(data_dir: Path) -> Path:
    configured = os.getenv("RESEARCH_COMPANION_EDITABLE_BACKEND_DIR", "").strip()
    if configured:
        return Path(configured)
    return data_dir / DEFAULT_EDITABLE_BACKEND_DIRNAME


def _packaged_backend_source_candidates(root: Path, filename: str) -> list[Path]:
    bundle = bundle_root()
    return [
        root / "backend" / filename,
        bundle / "backend_runtime" / filename,
        bundle / filename,
    ]


def _materialize_editable_backend(root: Path, data_dir: Path) -> str:
    target_dir = editable_backend_dir(data_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    force_refresh = os.getenv("RESEARCH_COMPANION_REFRESH_EDITABLE_BACKEND", "").strip().lower() in {"1", "true", "yes", "on"}
    for filename in EDITABLE_BACKEND_FILES:
        source_path: Optional[Path] = None
        for candidate in _packaged_backend_source_candidates(root, filename):
            if candidate.exists():
                source_path = candidate.resolve()
                break
        if source_path is None:
            continue
        target_path = target_dir / filename
        if target_path.exists() and not force_refresh:
            continue
        try:
            shutil.copy2(source_path, target_path)
        except PermissionError:
            if not target_path.exists():
                raise
        except Exception:
            continue
    return str(target_dir.resolve())


def _auth_files_exist(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        return any(path.glob("*.json")) or any(path.glob("*.json.disabled"))
    except Exception:
        return False


def _migrate_legacy_auth_dir(target_auth_dir: Path, legacy_auth_dir: Path) -> None:
    if not legacy_auth_dir.exists() or target_auth_dir.resolve() == legacy_auth_dir.resolve():
        return

    target_auth_dir.mkdir(parents=True, exist_ok=True)
    if _auth_files_exist(target_auth_dir):
        return
    if not _auth_files_exist(legacy_auth_dir):
        return

    for pattern in ("*.json", "*.json.disabled"):
        for source in legacy_auth_dir.glob(pattern):
            destination = target_auth_dir / source.name
            if destination.exists():
                continue
            try:
                shutil.copy2(source, destination)
            except Exception:
                continue


def _build_proxy_state(data_dir: Path, existing: Dict[str, str]) -> Dict[str, str]:
    preferred_port_raw = os.getenv("RESEARCH_COMPANION_CLI_PROXY_PORT", "").strip()
    preferred_port = int(preferred_port_raw) if preferred_port_raw.isdigit() else 0
    stored_port_raw = str(existing.get("port") or "").strip()
    stored_port = int(stored_port_raw) if stored_port_raw.isdigit() else 0
    if preferred_port:
        port = preferred_port
    elif stored_port:
        port = stored_port
    else:
        port = _reserve_port(DEFAULT_PROXY_PORT)
    api_key = str(existing.get("api_key") or "").strip() or f"rc-local-{secrets.token_urlsafe(24)}"
    management_key = str(existing.get("management_key") or "").strip() or f"rc-mgt-{secrets.token_urlsafe(24)}"

    auth_root = default_cli_proxy_auth_dir().resolve()
    auth_dir = str(auth_root)
    logs_dir = str((auth_root / DEFAULT_PROXY_LOG_DIRNAME).resolve())
    config_path = str((data_dir / DEFAULT_PROXY_CONFIG_NAME).resolve())
    state_path = str((data_dir / DEFAULT_PROXY_STATE_NAME).resolve())
    legacy_auth_dir = str(legacy_cli_proxy_auth_dir(data_dir).resolve())

    return {
        "host": DEFAULT_PROXY_HOST,
        "port": str(port),
        "api_key": api_key,
        "management_key": management_key,
        "auth_dir": auth_dir,
        "legacy_auth_dir": legacy_auth_dir,
        "logs_dir": logs_dir,
        "config_path": config_path,
        "state_path": state_path,
    }


def _write_proxy_config(runtime: Dict[str, str]) -> None:
    config_path = Path(runtime["config_path"])
    auth_dir = Path(runtime["auth_dir"])
    logs_dir = Path(runtime["logs_dir"])
    config_path.parent.mkdir(parents=True, exist_ok=True)
    auth_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    auth_dir_yaml = auth_dir.as_posix()

    # Keep the generated config intentionally small and app-owned.
    config_text = "\n".join(
        [
            "# Research Companion generated config",
            f"port: {runtime['port']}",
            f'auth-dir: "{auth_dir_yaml}"',
            "api-keys:",
            f'  - "{runtime["api_key"]}"',
            "debug: false",
            "usage-statistics-enabled: false",
            "logging-to-file: true",
            "logs-max-total-size-mb: 80",
            "request-retry: 0",
            "max-retry-interval: 0",
            "",
            "# Quota exceeded behavior",
            "quota-exceeded:",
            "  switch-project: true",
            "  switch-preview-model: false",
            "",
            "# Enable Management API for OAuth flows",
            "remote-management:",
            "  allow-remote: true",
            f'  secret-key: "{runtime["management_key"]}"',
            "  disable-control-panel: true",
            "  restrict-management-to-localhost: false",
            "",
        ]
    )
    config_path.write_text(config_text, encoding="utf-8")


def prepare_local_proxy_runtime(root: Optional[Path] = None) -> Dict[str, str]:
    root = root or project_root()
    data_dir = companion_data_dir(root)
    data_dir.mkdir(parents=True, exist_ok=True)

    state_path = data_dir / DEFAULT_PROXY_STATE_NAME
    existing = _read_json(state_path)
    runtime = _build_proxy_state(data_dir, existing)
    runtime["binary_path"] = _discover_cli_proxy_binary(root, data_dir)
    runtime["base_url"] = f"http://{runtime['host']}:{runtime['port']}/v1"
    runtime["management_base_url"] = f"http://{runtime['host']}:{runtime['port']}/v0/management"
    runtime["config_owner"] = "research_companion"
    runtime["writable_path"] = str(data_dir.resolve())
    runtime["editable_backend_dir"] = _materialize_editable_backend(root, data_dir)
    _migrate_legacy_auth_dir(Path(runtime["auth_dir"]), Path(runtime["legacy_auth_dir"]))

    _write_json(state_path, runtime)
    _write_proxy_config(runtime)
    return runtime


def configure_local_companion_env(root: Path | None = None) -> Dict[str, str]:
    root = root or project_root()

    for search_root in reversed(config_search_roots(root)):
        apply_env_mapping(load_env_file(search_root / ".env"))

    proxy_runtime = prepare_local_proxy_runtime(root)

    os.environ["PORT"] = "8787"
    os.environ["PUBLIC_BACKEND_URL"] = DEFAULT_PUBLIC_BACKEND_URL
    os.environ["FRONTEND_ORIGIN"] = DEFAULT_FRONTEND_ORIGIN
    os.environ["OAUTH_STORE_BACKEND"] = "file"
    os.environ["AUTH_STORE_DIR"] = str(root / ".auth-local")
    os.environ["RESOURCE_API_MODE"] = "cloud"
    os.environ["REMOTE_RESOURCE_API_BASE_URL"] = os.getenv("REMOTE_RESOURCE_API_BASE_URL", DEFAULT_REMOTE_RESOURCE_API_BASE_URL).strip()
    os.environ["CLI_PROXY_API_BASE_URL"] = proxy_runtime["base_url"]
    os.environ["CLI_PROXY_API_KEY"] = proxy_runtime["api_key"]
    os.environ["RESEARCH_COMPANION_CLI_PROXY_MANAGEMENT_BASE_URL"] = proxy_runtime["management_base_url"]
    os.environ["RESEARCH_COMPANION_CLI_PROXY_MANAGEMENT_KEY"] = proxy_runtime["management_key"]
    os.environ["RESEARCH_COMPANION_CLI_PROXY_CONFIG_PATH"] = proxy_runtime["config_path"]
    os.environ["RESEARCH_COMPANION_CLI_PROXY_BINARY"] = proxy_runtime["binary_path"]
    os.environ["RESEARCH_COMPANION_CLI_PROXY_AUTH_DIR"] = proxy_runtime["auth_dir"]
    os.environ["RESEARCH_COMPANION_CLI_PROXY_AUTH_DIR_LEGACY"] = proxy_runtime["legacy_auth_dir"]
    os.environ["RESEARCH_COMPANION_CLI_PROXY_WRITABLE_PATH"] = proxy_runtime["writable_path"]
    os.environ["RESEARCH_COMPANION_CLI_PROXY_PORT"] = proxy_runtime["port"]
    os.environ["RESEARCH_COMPANION_CLI_PROXY_OWNED"] = "true"
    os.environ["RESEARCH_COMPANION_EDITABLE_BACKEND_DIR"] = proxy_runtime["editable_backend_dir"]
    if not os.getenv("GOOGLE_CLOUD_PROJECT"):
        os.environ["GOOGLE_CLOUD_PROJECT"] = ""
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", DEFAULT_LOCATION)
    os.environ.pop("SCOPUS_API_KEY", None)
    os.environ.pop("CORE_API_KEY", None)
    os.environ.pop("UNPAYWALL_EMAIL", None)
    os.environ.pop("GEMINI_API_KEY", None)

    if not os.getenv("GOOGLE_OAUTH_CLIENT_ID") or not os.getenv("GOOGLE_OAUTH_CLIENT_SECRET"):
        apply_env_mapping(load_client_secret_from_file(root))
    if not os.getenv("GOOGLE_OAUTH_PROJECT_ID"):
        os.environ["GOOGLE_OAUTH_PROJECT_ID"] = DEFAULT_GOOGLE_OAUTH_PROJECT_ID
    if not os.getenv("GEMINI_CLI_PROJECT_ID"):
        os.environ["GEMINI_CLI_PROJECT_ID"] = DEFAULT_GEMINI_CLI_PROJECT_ID

    return {
        "PORT": os.getenv("PORT", "8787"),
        "PUBLIC_BACKEND_URL": os.getenv("PUBLIC_BACKEND_URL", DEFAULT_PUBLIC_BACKEND_URL),
        "FRONTEND_ORIGIN": os.getenv("FRONTEND_ORIGIN", DEFAULT_FRONTEND_ORIGIN),
        "REMOTE_RESOURCE_API_BASE_URL": os.getenv("REMOTE_RESOURCE_API_BASE_URL", DEFAULT_REMOTE_RESOURCE_API_BASE_URL),
        "GOOGLE_CLOUD_PROJECT": os.getenv("GOOGLE_CLOUD_PROJECT", ""),
        "GOOGLE_CLOUD_LOCATION": os.getenv("GOOGLE_CLOUD_LOCATION", DEFAULT_LOCATION),
        "GOOGLE_OAUTH_PROJECT_ID": os.getenv("GOOGLE_OAUTH_PROJECT_ID", ""),
        "GEMINI_CLI_PROJECT_ID": os.getenv("GEMINI_CLI_PROJECT_ID", DEFAULT_GEMINI_CLI_PROJECT_ID),
        "GOOGLE_OAUTH_CLIENT_ID_PRESENT": "yes" if os.getenv("GOOGLE_OAUTH_CLIENT_ID") else "no",
        "GOOGLE_OAUTH_CLIENT_SECRET_PRESENT": "yes" if os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") else "no",
        "CLI_PROXY_BINARY_PATH": proxy_runtime["binary_path"],
        "CLI_PROXY_CONFIG_PATH": proxy_runtime["config_path"],
        "CLI_PROXY_AUTH_DIR": proxy_runtime["auth_dir"],
        "CLI_PROXY_AUTH_DIR_LEGACY": proxy_runtime["legacy_auth_dir"],
        "CLI_PROXY_WRITABLE_PATH": proxy_runtime["writable_path"],
        "CLI_PROXY_PORT": proxy_runtime["port"],
        "CLI_PROXY_API_KEY_PRESENT": "yes" if proxy_runtime["api_key"] else "no",
        "CLI_PROXY_MANAGEMENT_BASE_URL": proxy_runtime["management_base_url"],
        "CLI_PROXY_MANAGEMENT_KEY_PRESENT": "yes" if proxy_runtime["management_key"] else "no",
        "CLI_PROXY_CONFIG_OWNER": proxy_runtime["config_owner"],
        "CLI_PROXY_DATA_DIR": str(companion_data_dir(root)),
        "EDITABLE_BACKEND_DIR": proxy_runtime["editable_backend_dir"],
    }
