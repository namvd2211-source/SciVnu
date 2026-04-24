from __future__ import annotations

import json
import os
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Literal, Optional, Tuple, TypedDict

import requests

# Core research workflow and helper utilities shared by the web backend and
# local companion runtime.


# -----------------------------
# Internal configuration
# -----------------------------


def load_local_env(env_path: str = ".env") -> None:
    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            if key and key not in os.environ:
                os.environ[key] = value


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


load_local_env()

SCOPUS_API_KEY = os.getenv("SCOPUS_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
CORE_API_KEY = os.getenv("CORE_API_KEY", "")
UNPAYWALL_EMAIL = os.getenv("UNPAYWALL_EMAIL", "namvd@vnu.edu.vn")
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
GOOGLE_CLOUD_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
GOOGLE_OAUTH_PROJECT_ID = os.getenv("GOOGLE_OAUTH_PROJECT_ID", "")
CLI_PROXY_API_BASE_URL = os.getenv("CLI_PROXY_API_BASE_URL", "http://127.0.0.1:8797/v1").rstrip("/")
CLI_PROXY_API_KEY = os.getenv("CLI_PROXY_API_KEY", "rc-local-default")
AUTH_STORE_DIR = os.getenv("AUTH_STORE_DIR", "/tmp/research-auth" if os.getenv("K_SERVICE") else ".auth")
REMOTE_RESOURCE_API_BASE_URL = os.getenv("REMOTE_RESOURCE_API_BASE_URL", "").strip().rstrip("/")
RESOURCE_API_MODE = os.getenv("RESOURCE_API_MODE", "").strip().lower()

DEFAULT_LLM_PROVIDER: Literal["Gemini"] = "Gemini"
GEMINI_MODEL_OPTIONS = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemma-3-27b-it",
    "gemma-3-12b-it",
    "gemma-3-4b-it",
]
DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
SCOPUS_MAX_RESULTS = 25
CORE_MAX_RESULTS = env_int("CORE_MAX_RESULTS", 15)
OPENALEX_MAX_RESULTS = env_int("OPENALEX_MAX_RESULTS", 15)
ARXIV_MAX_RESULTS = env_int("ARXIV_MAX_RESULTS", 15)
TOP_SEARCH_RESULTS = env_int("TOP_SEARCH_RESULTS", 12)
SCOPUS_MAX_SERVICE_COUNT = 25
GOOGLE_GEMINI_OAUTH_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cloud-platform",
]


# -----------------------------
# Data models
# -----------------------------


class PaperMeta(TypedDict, total=False):
    title: str
    authors: str
    year: int
    doi: str
    abstract: str
    core_id: str
    pdf_url: str
    full_text: str
    source_db: str
    relevance_score: float
    search_rank: int
    landing_url: str
    access_date: str


class PaperText(TypedDict, total=False):
    doi: str
    title: str
    year: int
    authors: str
    source: Literal["pdf", "core", "unpaywall", "abstract", "none"]
    text: str


class Quote(TypedDict, total=False):
    doi: str
    title: str
    authors: str
    year: int
    citation: str
    quote: str


class State(TypedDict, total=False):
    topic: str
    user_prompt: str
    language: Literal["English", "Vietnamese"]
    workflow_language: Literal["English", "Vietnamese"]
    output_language: Literal["English", "Vietnamese"]
    chat_history: List[Dict[str, str]]
    search_filters: Dict[str, Any]
    attachment_context: str
    source_manuscript: str
    research_query: str
    manager_guidance: Dict[str, Any]
    plan: str
    metadata_list: List[PaperMeta]
    full_texts: List[PaperText]
    quotations: List[Quote]
    final_draft: str
    reviewed_draft: str
    final_formatted: str


@dataclass
class WorkflowHooks:
    on_status: Optional[Callable[[Dict[str, str]], None]] = None
    on_log: Optional[Callable[[List[str], str], None]] = None
    on_manager: Optional[Callable[[str], None]] = None
    on_outline: Optional[Callable[[str], None]] = None
    on_metadata: Optional[Callable[[List["PaperMeta"]], None]] = None
    on_reader_data: Optional[Callable[[List["PaperText"], List["Quote"]], None]] = None
    on_draft: Optional[Callable[[str], None]] = None
    on_formatted: Optional[Callable[[str], None]] = None
    should_stop: Optional[Callable[[], None]] = None


# -----------------------------
# UI helpers
# -----------------------------


NODE_ORDER = ["Planner", "Researcher", "Reader", "Writer", "Reviewer", "Editor", "Translator"]


def init_statuses() -> Dict[str, str]:
    return {n: "Pending" for n in NODE_ORDER}


def set_node_status(statuses: Dict[str, str], node: str, status: str) -> None:
    statuses[node] = status


def today_access_date() -> str:
    return time.strftime("%Y-%m-%d")


# -----------------------------
# Text normalization + quote verification
# -----------------------------


def normalize_for_match(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def quote_is_verified(quote: str, source_text: str) -> bool:
    q = normalize_for_match(quote)
    t = normalize_for_match(source_text)
    if not q or not t:
        return False
    return q in t


def parse_year(value: Any) -> int:
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    match = re.search(r"\b(19|20)\d{2}\b", text)
    return int(match.group(0)) if match else 0


def normalize_doi(value: str) -> str:
    doi = str(value or "").strip()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    return doi.strip()


def ensure_auth_store_dir() -> str:
    os.makedirs(AUTH_STORE_DIR, exist_ok=True)
    return AUTH_STORE_DIR


def safe_slug(value: str, fallback: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(value or "").strip()).strip("._-")
    return text or fallback


def write_json_file(path: str, data: Dict[str, Any]) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_json_file(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("Auth file must contain a JSON object.")
    return data


def google_oauth_userinfo(access_token: str) -> Dict[str, Any]:
    response = requests.get(
        "https://openidconnect.googleapis.com/v1/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else {}


def google_auth_file_save(token_info: Dict[str, Any], email: str) -> str:
    filename = f"gemini-{safe_slug(email, 'google_user')}.json"
    path = os.path.join(ensure_auth_store_dir(), filename)
    payload = {
        "provider": "gemini",
        "auth_type": "google_oauth",
        "email": email,
        "token_info": token_info,
    }
    write_json_file(path, payload)
    return path


def google_credentials_from_auth_file(path: str) -> Optional[Any]:
    if not path or not os.path.exists(path):
        return None
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        raw = read_json_file(path)
        token_info = raw.get("token_info") if isinstance(raw.get("token_info"), dict) else raw
        credentials = Credentials.from_authorized_user_info(token_info, scopes=GOOGLE_GEMINI_OAUTH_SCOPES)
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            updated = {
                "provider": "gemini",
                "auth_type": "google_oauth",
                "email": raw.get("email", ""),
                "token_info": json.loads(credentials.to_json()),
            }
            write_json_file(path, updated)
        return credentials
    except Exception:
        return None


def ensure_google_credentials(credentials: Any, *, auth_file_path: str = "") -> Any:
    if credentials is None:
        return None
    if isinstance(credentials, str):
        return credentials.strip()
    try:
        from google.auth.transport.requests import Request

        if not getattr(credentials, "token", None) or getattr(credentials, "expired", False):
            credentials.refresh(Request())
            token_info = json.loads(credentials.to_json())
            if auth_file_path:
                raw = read_json_file(auth_file_path)
                updated = {
                    "provider": "gemini",
                    "auth_type": "google_oauth",
                    "email": raw.get("email", ""),
                    "token_info": token_info,
                }
                write_json_file(auth_file_path, updated)
        return credentials
    except Exception:
        return credentials


def resolve_vertex_runtime_credentials(
    user_credentials: Any,
    *,
    project: str = "",
    auth_file_path: str = "",
) -> Tuple[Any, str, str]:
    requested_project = str(project or GOOGLE_CLOUD_PROJECT or GOOGLE_OAUTH_PROJECT_ID or "").strip()
    resolved_user_credentials = ensure_google_credentials(user_credentials, auth_file_path=auth_file_path)
    return resolved_user_credentials, requested_project, "user_oauth"


def cliproxy_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {CLI_PROXY_API_KEY or 'sk-dummy'}",
        "Content-Type": "application/json",
    }


def cliproxy_models_url() -> str:
    return f"{CLI_PROXY_API_BASE_URL}/models"


def cliproxy_chat_completions_url() -> str:
    return f"{CLI_PROXY_API_BASE_URL}/chat/completions"


def cliproxy_logs_dir() -> Path:
    auth_dir = os.getenv("RESEARCH_COMPANION_CLI_PROXY_AUTH_DIR", "").strip()
    if auth_dir:
        return Path(auth_dir) / "logs"
    for auth_path in cliproxy_auth_dirs():
        logs_dir = auth_path / "logs"
        if logs_dir.exists():
            return logs_dir
    home = Path.home()
    if home:
        return home / ".cli-proxy-api" / "logs"
    return Path(os.getenv("LOCALAPPDATA", "")) / "ResearchCompanion" / "cli-proxy-auth" / "logs"


def cliproxy_auth_dirs() -> List[Path]:
    candidates: List[Path] = []
    configured = os.getenv("RESEARCH_COMPANION_CLI_PROXY_AUTH_DIR", "").strip()
    if configured:
        candidates.append(Path(configured))
    legacy_configured = os.getenv("RESEARCH_COMPANION_CLI_PROXY_AUTH_DIR_LEGACY", "").strip()
    home = Path.home()
    if home:
        candidates.append(home / ".cli-proxy-api")
    if legacy_configured:
        candidates.append(Path(legacy_configured))
    local_appdata = os.getenv("LOCALAPPDATA", "").strip()
    if local_appdata:
        candidates.append(Path(local_appdata) / "ResearchCompanion" / "cli-proxy-auth")

    ordered: List[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path.resolve()) if path.exists() else str(path)
        if key not in seen:
            ordered.append(path)
            seen.add(key)
    return ordered


def cliproxy_auth_dir() -> Path:
    dirs = cliproxy_auth_dirs()
    for path in dirs:
        if path.exists():
            return path
    if dirs:
        return dirs[0]
    home = Path.home()
    if home:
        return home / ".cli-proxy-api"
    return Path(os.getenv("LOCALAPPDATA", "")) / "ResearchCompanion" / "cli-proxy-auth"


def read_recent_lines(path: Path, max_lines: int = 120) -> List[str]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            return list(deque(handle, maxlen=max_lines))
    except Exception:
        return []


def cliproxy_error_hint() -> str:
    logs_dir = cliproxy_logs_dir()
    hints: List[str] = []

    main_log = logs_dir / "main.log"
    for line in reversed(read_recent_lines(main_log, max_lines=200)):
        text = str(line).strip()
        if "Failed to complete user setup:" in text:
            hints.append(text.split("Failed to complete user setup:", 1)[1].strip())
            break
        if "full client load complete - 0 clients" in text:
            hints.append("CLI proxy has 0 loaded clients after login.")
            break

    try:
        error_files = sorted(logs_dir.glob("error-v1-chat-completions-*"), key=lambda item: item.stat().st_mtime, reverse=True)
    except Exception:
        error_files = []
    for error_file in error_files[:2]:
        body = "".join(read_recent_lines(error_file, max_lines=120))
        match = re.search(r'"message"\s*:\s*"([^"]+)"', body)
        if match:
            hints.append(match.group(1).strip())
            break
        if "unknown provider for model" in body:
            hints.append("Local CLI proxy does not have a Gemini provider loaded for the requested model.")
            break

    deduped: List[str] = []
    seen: set[str] = set()
    for hint in hints:
        cleaned = normalize_for_match(hint)
        if cleaned and cleaned not in seen:
            deduped.append(hint)
            seen.add(cleaned)
    return " ".join(deduped[:2]).strip()


def cliproxy_auth_entries() -> List[Tuple[Path, Dict[str, Any]]]:
    entries: List[Tuple[Path, Dict[str, Any]]] = []
    seen_paths: set[str] = set()
    for auth_dir in cliproxy_auth_dirs():
        if not auth_dir.exists():
            continue
        for path in auth_dir.glob("*.json"):
            try:
                resolved = str(path.resolve())
            except Exception:
                resolved = str(path)
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            try:
                payload = read_json_file(str(path))
            except Exception:
                continue
            auth_type = str(payload.get("type") or payload.get("provider") or "").strip().lower()
            if auth_type not in {"gemini", "gemini-cli"}:
                continue
            if bool(payload.get("disabled")):
                continue
            token_info = payload.get("token") if isinstance(payload.get("token"), dict) else {}
            if not token_info:
                continue
            entries.append((path, payload))
    entries.sort(
        key=lambda item: (
            not bool(item[1].get("checked", True)),
            -(item[0].stat().st_mtime if item[0].exists() else 0),
            item[0].name,
        )
    )
    return entries


def refresh_cliproxy_auth_tokens() -> bool:
    for path, payload in cliproxy_auth_entries():
        token_info = payload.get("token") if isinstance(payload.get("token"), dict) else {}
        if not token_info:
            continue
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials

            credentials = Credentials(
                token=str(token_info.get("access_token") or token_info.get("token") or "").strip() or None,
                refresh_token=str(token_info.get("refresh_token") or "").strip() or None,
                token_uri=str(token_info.get("token_uri") or "https://oauth2.googleapis.com/token").strip(),
                client_id=str(token_info.get("client_id") or "").strip() or None,
                client_secret=str(token_info.get("client_secret") or "").strip() or None,
                scopes=token_info.get("scopes") if isinstance(token_info.get("scopes"), list) else GOOGLE_GEMINI_OAUTH_SCOPES,
            )
            if not credentials.refresh_token:
                continue
            credentials.refresh(Request())
            refreshed = json.loads(credentials.to_json())
            if isinstance(refreshed, dict):
                for field in ("refresh_token", "client_id", "client_secret", "token_uri", "scopes"):
                    if refreshed.get(field) in {None, "", []} and token_info.get(field) not in {None, "", []}:
                        refreshed[field] = token_info.get(field)
                payload["token"] = refreshed
                write_json_file(str(path), payload)
                return True
        except Exception:
            continue
    return False


def is_cliproxy_auth_error(message: str) -> bool:
    lowered = str(message or "").lower()
    return any(
        token in lowered
        for token in [
            "401",
            "unauthenticated",
            "invalid authentication credentials",
            "expected oauth 2 access token",
            "login cookie",
            "access token",
            "sign in again",
        ]
    )


def fetch_cliproxy_models(timeout: float = 4.0) -> List[str]:
    response = requests.get(
        cliproxy_models_url(),
        headers=cliproxy_headers(),
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    models: List[str] = []
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or "").strip()
            if model_id:
                models.append(model_id)
    filtered = [model for model in models if model.startswith("gemini-") or model.startswith("gemma-")]
    return filtered or models


def cliproxy_reachable(timeout: float = 2.0) -> bool:
    try:
        response = requests.get(
            cliproxy_models_url(),
            headers=cliproxy_headers(),
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        return isinstance(payload, dict) and "data" in payload
    except Exception:
        return False


def cliproxy_available(timeout: float = 2.0) -> bool:
    return cliproxy_reachable(timeout=timeout)


VIETNAMESE_SURNAMES = {
    "nguyen", "tran", "le", "pham", "hoang", "huynh", "phan", "vu", "vo", "dang",
    "bui", "do", "ho", "ngo", "duong", "ly", "truong", "dinh",
}


def split_author_names(authors: str) -> List[str]:
    text = str(authors or "").strip()
    if not text:
        return []
    if ";" in text:
        return [part.strip() for part in text.split(";") if part.strip()]
    if " and " in text:
        return [part.strip() for part in text.split(" and ") if part.strip()]
    return [text]


def split_name_parts(name: str) -> Tuple[str, List[str]]:
    cleaned = normalize_for_match(name).strip(" ,.")
    if not cleaned:
        return "", []
    if "," in cleaned:
        surname, given = [part.strip() for part in cleaned.split(",", 1)]
        given_parts = [part for part in re.split(r"[\s-]+", given) if part]
        return surname, given_parts

    parts = [part for part in re.split(r"\s+", cleaned) if part]
    if len(parts) == 1:
        return parts[0], []
    if parts[0].lower() in VIETNAMESE_SURNAMES:
        return parts[0], parts[1:]
    return parts[-1], parts[:-1]


def first_author_surname(authors: str) -> str:
    names = split_author_names(authors)
    if not names:
        return ""
    surname, _ = split_name_parts(names[0])
    return surname


def citation_from_meta(authors: str, year: int) -> str:
    names = split_author_names(authors)
    if not names or not year:
        return ""
    if len(names) == 1:
        surname, _ = split_name_parts(names[0])
        return f"({surname}, {year})" if surname else ""
    if len(names) == 2:
        first_surname, _ = split_name_parts(names[0])
        second_surname, _ = split_name_parts(names[1])
        return f"({first_surname} & {second_surname}, {year})" if first_surname and second_surname else ""
    first_surname, _ = split_name_parts(names[0])
    return f"({first_surname} et al., {year})" if first_surname else ""


def is_metadata_usable(meta: PaperMeta) -> bool:
    authors = str(meta.get("authors") or "").strip()
    year = int(meta.get("year") or 0)
    doi = normalize_doi(meta.get("doi", ""))
    landing_url = str(meta.get("landing_url") or "").strip()
    return bool(authors and year and (doi or landing_url))


def apa_name(name: str) -> str:
    surname, given_parts = split_name_parts(name)
    if not surname:
        return ""
    initials = " ".join(f"{part[0].upper()}." for part in given_parts if part)
    return f"{surname}, {initials}".strip()


def apa_authors(authors: str) -> str:
    names = [apa_name(name) for name in split_author_names(authors)]
    names = [name for name in names if name]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]}, & {names[1]}"
    return ", ".join(names[:-1]) + f", & {names[-1]}"


def reference_suffix(meta: PaperMeta) -> str:
    doi = normalize_doi(meta.get("doi", ""))
    if doi:
        return f"https://doi.org/{doi}"

    landing_url = str(meta.get("landing_url") or "").strip()
    if landing_url:
        access_date = str(meta.get("access_date") or today_access_date())
        return f"URL: {landing_url} (Accessed on: {access_date})"

    return ""


def apa_reference(meta: PaperMeta) -> str:
    authors = apa_authors(meta.get("authors", ""))
    year = meta.get("year") or "n.d."
    title = str(meta.get("title") or "").strip()
    suffix = reference_suffix(meta)
    pieces = [piece for piece in [authors, f"({year}).", title + "." if title else "", suffix] if piece]
    return " ".join(pieces).replace("..", ".")


def citation_signature(meta: PaperMeta) -> str:
    surname = normalize_for_match(first_author_surname(meta.get("authors", ""))).lower()
    year = str(meta.get("year") or "").strip()
    return f"{surname}|{year}" if surname and year else ""


def extract_citation_signatures(text: str) -> List[str]:
    signatures: List[str] = []
    seen: set[str] = set()
    for block in re.findall(r"\(([^()]{3,120})\)", text):
        for part in block.split(";"):
            match = re.search(r"([A-Za-zÀ-ỹ'`-]+)(?:\s+et\s+al\.)?(?:\s*&\s+[A-Za-zÀ-ỹ'`-]+)?\s*,\s*(\d{4})", part)
            if not match:
                continue
            surname = normalize_for_match(match.group(1)).lower()
            year = match.group(2)
            signature = f"{surname}|{year}"
            if signature not in seen:
                seen.add(signature)
                signatures.append(signature)
    return signatures


def select_reference_metadata(*, text: str, metadata: List[PaperMeta], limit: int) -> List[PaperMeta]:
    usable_metadata = [meta for meta in metadata if is_metadata_usable(meta)]
    if not usable_metadata or limit <= 0:
        return []

    signature_to_meta: Dict[str, PaperMeta] = {}
    for meta in usable_metadata:
        signature = citation_signature(meta)
        if signature and signature not in signature_to_meta:
            signature_to_meta[signature] = meta

    selected: List[PaperMeta] = []
    used_keys: set[str] = set()

    for signature in extract_citation_signatures(text):
        meta = signature_to_meta.get(signature)
        if not meta:
            continue
        identity = paper_identity_key(meta)
        if identity in used_keys:
            continue
        used_keys.add(identity)
        selected.append(meta)
        if len(selected) >= limit:
            return selected

    for meta in usable_metadata:
        identity = paper_identity_key(meta)
        if identity in used_keys:
            continue
        used_keys.add(identity)
        selected.append(meta)
        if len(selected) >= limit:
            break
    return selected


def build_reference_section(metadata: List[PaperMeta], *, text: str = "", limit: int = 0) -> str:
    selected_metadata = select_reference_metadata(
        text=text,
        metadata=metadata,
        limit=(limit or len(metadata)),
    )
    refs = [apa_reference(meta) for meta in selected_metadata if is_metadata_usable(meta)]
    refs = [ref for ref in refs if ref]
    refs = refs[: limit or len(refs)]
    if not refs:
        return ""
    return "## References\n\n" + "\n".join([f"- {ref}" for ref in refs])


def limit_quote_pool(quotations: List[Quote], reference_target: int, *, max_quotes_per_source: int = 3) -> List[Quote]:
    if reference_target <= 0:
        return []
    chosen_citations: List[str] = []
    per_source_counts: Dict[str, int] = {}
    out: List[Quote] = []
    for quote in quotations:
        citation = str(quote.get("citation") or "").strip()
        if not citation:
            continue
        if citation not in chosen_citations:
            if len(chosen_citations) >= reference_target:
                continue
            chosen_citations.append(citation)
        count = per_source_counts.get(citation, 0)
        if count >= max_quotes_per_source:
            continue
        per_source_counts[citation] = count + 1
        out.append(quote)
    return out


def merge_source_names(*values: str) -> str:
    return ", ".join(dict.fromkeys([value for value in values if value]))


def titles_look_related(left: str, right: str) -> bool:
    left_norm = normalize_for_match(left).lower()
    right_norm = normalize_for_match(right).lower()
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm or left_norm in right_norm or right_norm in left_norm:
        return True
    left_terms = keyword_set(left_norm)
    right_terms = keyword_set(right_norm)
    if not left_terms or not right_terms:
        return False
    overlap = len(left_terms & right_terms)
    return overlap / max(1, min(len(left_terms), len(right_terms))) >= 0.6


def extract_core_authors(authors_data: Any) -> str:
    if not isinstance(authors_data, list):
        return ""

    names: List[str] = []
    for author in authors_data:
        if isinstance(author, dict):
            name = str(author.get("name") or author.get("displayName") or "").strip()
        else:
            name = str(author or "").strip()
        if name:
            names.append(name)

    return "; ".join(names)


def paper_identity_key(meta: PaperMeta) -> str:
    doi = normalize_for_match(meta.get("doi", "")).lower()
    if doi:
        return f"doi:{doi}"

    title = normalize_for_match(meta.get("title", "")).lower()
    if title:
        return f"title:{title}"

    core_id = normalize_for_match(meta.get("core_id", "")).lower()
    if core_id:
        return f"core:{core_id}"

    return f"fallback:{len(title)}:{meta.get('year', 0)}"


def merge_metadata_lists(*lists: List[PaperMeta]) -> List[PaperMeta]:
    merged: Dict[str, PaperMeta] = {}

    for items in lists:
        for item in items:
            key = paper_identity_key(item)
            existing = merged.get(key)
            if not existing:
                merged[key] = dict(item)
                continue

            existing["source_db"] = merge_source_names(existing.get("source_db", ""), item.get("source_db", ""))

            for field in ["title", "authors", "doi", "abstract", "core_id", "pdf_url", "full_text", "landing_url", "access_date"]:
                if not existing.get(field) and item.get(field):
                    existing[field] = item[field]  # type: ignore[index]

            if not existing.get("year") and item.get("year"):
                existing["year"] = int(item["year"])

    return list(merged.values())


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into",
    "is", "it", "of", "on", "or", "that", "the", "their", "this", "to", "using",
    "with", "within",
}


def keyword_set(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return {token for token in tokens if len(token) >= 3 and token not in STOPWORDS}


def ordered_keywords(text: str) -> List[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    seen: Dict[str, bool] = {}
    out: List[str] = []
    for token in tokens:
        if len(token) < 3 or token in STOPWORDS or token in seen:
            continue
        seen[token] = True
        out.append(token)
    return out


def heuristic_relevance_score(topic: str, meta: PaperMeta) -> float:
    topic_terms = keyword_set(topic)
    title_terms = keyword_set(meta.get("title", ""))
    abstract_terms = keyword_set(meta.get("abstract", ""))

    title_overlap = len(topic_terms & title_terms)
    abstract_overlap = len(topic_terms & abstract_terms)
    source_count = len([s for s in meta.get("source_db", "").split(",") if s.strip()])

    score = (title_overlap * 8.0) + (abstract_overlap * 3.0)
    score += min(source_count, 3) * 1.5

    if meta.get("doi"):
        score += 2.0
    if meta.get("full_text"):
        score += 4.0
    elif meta.get("pdf_url"):
        score += 3.0
    elif meta.get("abstract"):
        score += 1.0

    year = int(meta.get("year") or 0)
    current_year = time.gmtime().tm_year
    if year >= current_year - 3:
        score += 2.0
    elif year >= current_year - 7:
        score += 1.0

    return score


def extract_json_payload(raw_text: str) -> str:
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def rerank_prompt(topic: str, candidates: List[Dict[str, Any]]) -> str:
    payload = json.dumps(candidates, ensure_ascii=False, indent=2)
    return f"""You are ranking academic search results for relevance.

Topic:
{topic}

Candidates:
{payload}

Instructions:
- Rank the papers from most relevant to least relevant for the topic.
- Favor papers whose title and abstract clearly match the topic.
- Prefer papers with DOI, full text, or multiple trusted source databases when relevance is otherwise similar.
- Return ONLY valid JSON with this exact schema:
{{"ranked_ids": ["P3", "P1", "P2"]}}
"""


def rerank_metadata_with_llm(
    *,
    topic: str,
    metadata_list: List[PaperMeta],
    llm_cfg: LLMConfig,
    should_stop: Optional[Callable[[], None]] = None,
) -> List[PaperMeta]:
    if not metadata_list:
        return []

    candidate_pool = sorted(
        metadata_list,
        key=lambda m: (
            float(m.get("relevance_score") or 0.0),
            int(m.get("year") or 0),
            normalize_for_match(m.get("title", "")).lower(),
        ),
        reverse=True,
    )[: min(len(metadata_list), 24)]

    candidates: List[Dict[str, Any]] = []
    id_to_meta: Dict[str, PaperMeta] = {}
    for idx, meta in enumerate(candidate_pool, start=1):
        candidate_id = f"P{idx}"
        id_to_meta[candidate_id] = meta
        candidates.append(
            {
                "id": candidate_id,
                "title": meta.get("title", ""),
                "authors": meta.get("authors", ""),
                "year": meta.get("year", 0),
                "doi": bool(meta.get("doi")),
                "has_full_text": bool(meta.get("full_text") or meta.get("pdf_url")),
                "source_db": meta.get("source_db", ""),
                "abstract": (meta.get("abstract", "") or "")[:700],
                "heuristic_score": round(float(meta.get("relevance_score") or 0.0), 2),
            }
        )

    if should_stop:
        should_stop()
    raw = llm_complete_text(cfg=llm_cfg, prompt=rerank_prompt(topic, candidates), should_stop=should_stop)
    parsed = json.loads(extract_json_payload(raw))
    ranked_ids = parsed.get("ranked_ids", []) if isinstance(parsed, dict) else []

    ranked: List[PaperMeta] = []
    seen: set[str] = set()
    for candidate_id in ranked_ids:
        if candidate_id in id_to_meta and candidate_id not in seen:
            ranked.append(id_to_meta[candidate_id])
            seen.add(candidate_id)

    for candidate in candidate_pool:
        key = paper_identity_key(candidate)
        if key not in {paper_identity_key(item) for item in ranked}:
            ranked.append(candidate)

    return ranked


def rank_metadata_list(
    *,
    topic: str,
    metadata_list: List[PaperMeta],
    llm_cfg: LLMConfig,
    top_k: int,
    should_stop: Optional[Callable[[], None]] = None,
) -> List[PaperMeta]:
    if not metadata_list:
        return []

    scored = [dict(item, relevance_score=heuristic_relevance_score(topic, item)) for item in metadata_list]
    if should_stop:
        should_stop()

    try:
        ranked = rerank_metadata_with_llm(topic=topic, metadata_list=scored, llm_cfg=llm_cfg, should_stop=should_stop)
    except Exception:
        if should_stop:
            should_stop()
        ranked = sorted(
            scored,
            key=lambda m: (
                float(m.get("relevance_score") or 0.0),
                int(m.get("year") or 0),
                normalize_for_match(m.get("title", "")).lower(),
            ),
            reverse=True,
        )

    trimmed = ranked[:top_k]
    for idx, meta in enumerate(trimmed, start=1):
        meta["search_rank"] = idx
    return trimmed


# -----------------------------
# External APIs
# -----------------------------


def scopus_query_variants(topic: str) -> List[str]:
    topic = str(topic or "").strip()
    if not topic:
        return []

    keywords = ordered_keywords(topic)
    variants: List[str] = [f"TITLE-ABS-KEY({topic})"]

    bigrams: List[str] = []
    for index in range(max(0, len(keywords) - 1)):
        bigram = f"{keywords[index]} {keywords[index + 1]}".strip()
        if bigram and bigram not in bigrams:
            bigrams.append(bigram)

    if len(keywords) >= 4:
        start_phrase = f"{keywords[0]} {keywords[1]}".strip()
        end_phrase = f"{keywords[-2]} {keywords[-1]}".strip()
        variants.append(
            f'TITLE-ABS-KEY(("{start_phrase}" OR {keywords[0]}) AND ("{end_phrase}" OR {keywords[-1]}))'
        )

    if bigrams:
        phrase_clause = " OR ".join([f'"{phrase}"' for phrase in bigrams[:3]])
        variants.append(f"TITLE-ABS-KEY({phrase_clause})")

    if keywords:
        focused_keywords = keywords[: min(len(keywords), 6)]
        focused_pairs = [f'"{focused_keywords[i]} {focused_keywords[i + 1]}"' for i in range(0, len(focused_keywords) - 1, 2)]
        if focused_pairs:
            variants.append(
                f"TITLE-ABS-KEY(({ ' OR '.join(focused_pairs[:2]) }) AND ({' OR '.join(focused_keywords[: min(3, len(focused_keywords))])}))"
            )

    return [query for query in dict.fromkeys([item.strip() for item in variants if item.strip()])]


def scopus_search(
    *,
    api_key: str,
    topic: str,
    max_results: int,
    timeout_s: int = 30,
) -> List[PaperMeta]:
    """Search Scopus for basic metadata.

    Note: This uses Elsevier Scopus Search API. Field mapping can vary by endpoint.
    """

    url = "https://api.elsevier.com/content/search/scopus"
    headers = {
        "X-ELS-APIKey": api_key,
        "Accept": "application/json",
    }
    out: List[PaperMeta] = []
    seen_keys: set[str] = set()
    queries = scopus_query_variants(topic)

    for query in queries:
        params = {
            "query": query,
            "count": str(max_results),
            "start": "0",
            "view": "STANDARD",
        }

        resp = requests.get(url, headers=headers, params=params, timeout=timeout_s)
        resp.raise_for_status()
        data = resp.json()
        entries = data.get("search-results", {}).get("entry", [])

        for e in entries:
            if not isinstance(e, dict):
                continue

            title = str(e.get("dc:title") or "").strip()
            year_str = str(e.get("prism:coverDate") or "").strip()
            year = int(year_str[:4]) if year_str[:4].isdigit() else 0

            doi = normalize_doi(str(e.get("prism:doi") or "").strip())
            links = e.get("link") or []
            landing_url = ""
            if isinstance(links, list):
                for link in links:
                    if not isinstance(link, dict):
                        continue
                    if link.get("@ref") == "scopus":
                        landing_url = str(link.get("@href") or "").strip()
                        break

            authors = ""
            if isinstance(e.get("author"), list) and e.get("author"):
                names = []
                for a in e.get("author"):
                    name = a.get("authname") or a.get("ce:indexed-name") or ""
                    if name:
                        names.append(name)
                authors = "; ".join(names)
            else:
                authors = str(e.get("dc:creator") or "").strip()

            abstract = str(e.get("dc:description") or "").strip()
            if not any([title, doi, authors, abstract]):
                continue

            item: PaperMeta = {
                "title": title,
                "authors": authors,
                "year": year,
                "doi": doi,
                "abstract": abstract,
                "source_db": "Scopus",
                "landing_url": f"https://doi.org/{doi}" if doi else landing_url,
                "access_date": today_access_date(),
            }
            item_key = paper_identity_key(item)
            if item_key in seen_keys:
                continue
            seen_keys.add(item_key)
            out.append(item)
            if len(out) >= max_results:
                return out

    return out


def core_search(
    *,
    api_key: str,
    topic: str,
    max_results: int,
    timeout_s: int = 30,
) -> List[PaperMeta]:
    """Search CORE for open-access metadata and, when available, full text."""

    url = "https://api.core.ac.uk/v3/search/works/"
    headers = {"Accept": "application/json"}

    keywords = ordered_keywords(topic)
    fallback_queries = [
        topic,
        " ".join(keywords[:6]),
        " ".join(keywords[:4]),
    ]
    deduped_queries = [query for query in dict.fromkeys([q.strip() for q in fallback_queries if q.strip()])]

    last_error: Optional[Exception] = None
    data: Dict[str, Any] = {}
    for query in deduped_queries:
        params = {
            "q": query,
            "limit": str(max_results),
            "offset": "0",
            "api_key": api_key.strip(),
        }
        for attempt in range(3):
            try:
                resp = requests.get(url, headers=headers, params=params, timeout=timeout_s)
                resp.raise_for_status()
                data = resp.json()
                last_error = None
                break
            except requests.HTTPError as e:
                last_error = e
                status_code = getattr(e.response, "status_code", None)
                if status_code in {500, 502, 503, 504} and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                break
            except Exception as e:  # noqa: BLE001
                last_error = e
                break
        if not last_error:
            break

    if last_error:
        raise last_error

    results = data.get("results", [])
    out: List[PaperMeta] = []
    for item in results:
        if not isinstance(item, dict):
            continue

        out.append(
            {
                "title": str(item.get("title") or "").strip(),
                "authors": extract_core_authors(item.get("authors")),
                "year": parse_year(item.get("yearPublished") or item.get("publishedDate") or item.get("year")),
                "doi": normalize_doi(str(item.get("doi") or "").strip()),
                "abstract": str(item.get("abstract") or "").strip(),
                "core_id": str(item.get("id") or "").strip(),
                "pdf_url": str(item.get("downloadUrl") or "").strip(),
                "full_text": str(item.get("fullText") or "").strip(),
                "source_db": "CORE",
                "landing_url": str((item.get("sourceFulltextUrls") or [""])[0] or item.get("downloadUrl") or "").strip(),
                "access_date": today_access_date(),
            }
        )

    return out


def openalex_authors(authorships: Any) -> str:
    if not isinstance(authorships, list):
        return ""
    names: List[str] = []
    for authorship in authorships:
        if not isinstance(authorship, dict):
            continue
        author = authorship.get("author") or {}
        name = str(author.get("display_name") or authorship.get("raw_author_name") or "").strip()
        if name:
            names.append(name)
    return "; ".join(names)


def openalex_search(
    *,
    topic: str,
    max_results: int,
    timeout_s: int = 30,
) -> List[PaperMeta]:
    resp = requests.get(
        "https://api.openalex.org/works",
        params={"search": topic, "per-page": str(max_results)},
        timeout=timeout_s,
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])

    out: List[PaperMeta] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        doi = normalize_doi(str(item.get("doi") or item.get("ids", {}).get("doi") or ""))
        primary_location = item.get("primary_location") or {}
        landing_url = str(primary_location.get("landing_page_url") or item.get("id") or "").strip()
        out.append(
            {
                "title": str(item.get("display_name") or item.get("title") or "").strip(),
                "authors": openalex_authors(item.get("authorships")),
                "year": int(item.get("publication_year") or 0),
                "doi": doi,
                "abstract": "",
                "pdf_url": str(primary_location.get("pdf_url") or "").strip(),
                "source_db": "OpenAlex",
                "landing_url": landing_url,
                "access_date": today_access_date(),
            }
    )
    return out


def arxiv_search(
    *,
    topic: str,
    max_results: int,
    timeout_s: int = 30,
) -> List[PaperMeta]:
    query_text = str(topic or "").strip()
    if not query_text:
        return []

    keywords = ordered_keywords(query_text)
    fallback_queries = [
        query_text,
        " ".join(keywords[:6]),
        " ".join(keywords[:4]),
    ]
    deduped_queries = [query for query in dict.fromkeys([q.strip() for q in fallback_queries if q.strip()])]
    phrase_pairs = [
        " ".join(keywords[idx : idx + 2]).strip()
        for idx in range(0, min(len(keywords) - 1, 6), 2)
        if len(keywords[idx : idx + 2]) == 2
    ]
    search_expressions: List[str] = []

    def add_search_expression(expression: str) -> None:
        normalized = str(expression or "").strip()
        if normalized and normalized not in search_expressions:
            search_expressions.append(normalized)

    if phrase_pairs:
        add_search_expression(" AND ".join([f'all:\"{phrase}\"' for phrase in phrase_pairs[:2]]))
        add_search_expression(" OR ".join([f'all:\"{phrase}\"' for phrase in phrase_pairs[:2]]))
        for phrase in phrase_pairs[:3]:
            add_search_expression(f'all:\"{phrase}\"')
    if keywords:
        add_search_expression(" AND ".join([f"all:{token}" for token in keywords[: min(len(keywords), 4)]]))
    for query in deduped_queries:
        add_search_expression(f"all:{query}")

    namespace = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }
    out: List[PaperMeta] = []
    seen_keys: set[str] = set()
    headers = {"User-Agent": "ResearchCompanion/1.0 (academic workflow; contact: local-companion)"}

    for query in search_expressions:
        resp = requests.get(
            "https://export.arxiv.org/api/query",
            params={
                "search_query": query,
                "start": "0",
                "max_results": str(max_results),
                "sortBy": "relevance",
                "sortOrder": "descending",
            },
            headers=headers,
            timeout=timeout_s,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        entries = root.findall("atom:entry", namespace)

        for entry in entries:
            title = re.sub(r"\s+", " ", str(entry.findtext("atom:title", default="", namespaces=namespace) or "")).strip()
            abstract = re.sub(r"\s+", " ", str(entry.findtext("atom:summary", default="", namespaces=namespace) or "")).strip()
            published = str(entry.findtext("atom:published", default="", namespaces=namespace) or "").strip()
            year = parse_year(published[:4] if published else "")
            authors = "; ".join(
                [
                    str(author.findtext("atom:name", default="", namespaces=namespace) or "").strip()
                    for author in entry.findall("atom:author", namespace)
                    if str(author.findtext("atom:name", default="", namespaces=namespace) or "").strip()
                ]
            )
            entry_id = str(entry.findtext("atom:id", default="", namespaces=namespace) or "").strip()
            doi = ""
            pdf_url = ""
            landing_url = entry_id

            for link in entry.findall("atom:link", namespace):
                href = str(link.attrib.get("href") or "").strip()
                title_attr = str(link.attrib.get("title") or "").strip().lower()
                rel_attr = str(link.attrib.get("rel") or "").strip().lower()
                type_attr = str(link.attrib.get("type") or "").strip().lower()
                if title_attr == "pdf" or href.endswith(".pdf") or type_attr == "application/pdf":
                    pdf_url = href
                elif rel_attr == "alternate" and href:
                    landing_url = href

            doi_node = entry.find("arxiv:doi", namespace)
            if doi_node is not None and str(doi_node.text or "").strip():
                doi = normalize_doi(str(doi_node.text or "").strip())
            elif entry_id:
                match = re.search(r"arxiv\.org\/abs\/([^v\?]+)", entry_id, flags=re.IGNORECASE)
                if match:
                    doi = ""

            item: PaperMeta = {
                "title": title,
                "authors": authors,
                "year": year,
                "doi": doi,
                "abstract": abstract,
                "pdf_url": pdf_url,
                "source_db": "arXiv",
                "landing_url": landing_url or entry_id,
                "access_date": today_access_date(),
            }
            if not any([title, authors, abstract, landing_url]):
                continue
            item_key = paper_identity_key(item)
            if item_key in seen_keys:
                continue
            seen_keys.add(item_key)
            out.append(item)
            if len(out) >= max_results:
                return out

    return out


def crossref_lookup_by_title(*, title: str, timeout_s: int = 30) -> PaperMeta:
    if not title.strip():
        return {}

    resp = requests.get(
        "https://api.crossref.org/works",
        params={"query.title": title, "rows": "1"},
        timeout=timeout_s,
    )
    resp.raise_for_status()
    items = resp.json().get("message", {}).get("items", [])
    if not items:
        return {}

    item = items[0]
    authors = "; ".join(
        [
            " ".join([str(author.get("given") or "").strip(), str(author.get("family") or "").strip()]).strip()
            for author in item.get("author", [])
            if isinstance(author, dict)
        ]
    )
    doi = normalize_doi(str(item.get("DOI") or ""))
    landing_url = str(item.get("resource", {}).get("primary", {}).get("URL") or item.get("URL") or "").strip()
    title_value = ""
    if isinstance(item.get("title"), list) and item.get("title"):
        title_value = str(item.get("title")[0]).strip()

    return {
        "title": title_value,
        "authors": authors,
        "year": parse_year(item.get("issued", {}).get("date-parts", [[0]])[0][0] if item.get("issued") else 0),
        "doi": doi,
        "source_db": "Crossref",
        "landing_url": landing_url,
        "access_date": today_access_date(),
    }


def enrich_paper_metadata(meta: PaperMeta) -> PaperMeta:
    enriched = dict(meta)
    title = str(enriched.get("title") or "").strip()
    if not title:
        return enriched

    openalex_item: PaperMeta = {}
    crossref_item: PaperMeta = {}

    if not enriched.get("authors") or not normalize_doi(enriched.get("doi", "")) or not enriched.get("landing_url"):
        try:
            openalex_results = openalex_search(topic=title, max_results=1)
            if openalex_results:
                candidate = openalex_results[0]
                if titles_look_related(title, candidate.get("title", "")):
                    openalex_item = candidate
        except Exception:
            openalex_item = {}

        try:
            candidate = crossref_lookup_by_title(title=title)
            if candidate and titles_look_related(title, candidate.get("title", "")):
                crossref_item = candidate
        except Exception:
            crossref_item = {}

    for candidate in [openalex_item, crossref_item]:
        if not candidate:
            continue
        enriched["source_db"] = merge_source_names(enriched.get("source_db", ""), candidate.get("source_db", ""))
        for field in ["authors", "doi", "landing_url", "access_date"]:
            if not enriched.get(field) and candidate.get(field):
                enriched[field] = candidate[field]  # type: ignore[index]
        if not enriched.get("year") and candidate.get("year"):
            enriched["year"] = int(candidate["year"])

    doi = normalize_doi(enriched.get("doi", ""))
    if doi and not enriched.get("landing_url"):
        enriched["landing_url"] = f"https://doi.org/{doi}"
        enriched["access_date"] = today_access_date()
    if doi:
        enriched["doi"] = doi

    return enriched


def unpaywall_lookup(*, doi: str, email: str, timeout_s: int = 30) -> Dict[str, Any]:
    doi = doi.strip()
    url = f"https://api.unpaywall.org/v2/{doi}"
    resp = requests.get(url, params={"email": email}, timeout=timeout_s)
    resp.raise_for_status()
    return resp.json()


def use_remote_resource_api() -> bool:
    return RESOURCE_API_MODE == "cloud" and bool(REMOTE_RESOURCE_API_BASE_URL)


def remote_resource_search(
    *,
    source: Literal["scopus", "core", "openalex", "arxiv"],
    topic: str,
    max_results: int,
    timeout_s: int = 60,
) -> List[PaperMeta]:
    if not use_remote_resource_api():
        raise RuntimeError("Remote resource API is not configured.")

    response = requests.post(
        f"{REMOTE_RESOURCE_API_BASE_URL}/api/resource-search",
        json={
            "source": source,
            "topic": topic,
            "max_results": max_results,
        },
        timeout=timeout_s,
    )
    response.raise_for_status()
    payload = response.json()
    items = payload.get("items") if isinstance(payload, dict) else []
    return items if isinstance(items, list) else []


def remote_unpaywall_lookup(
    *,
    doi: str,
    timeout_s: int = 60,
) -> Dict[str, Any]:
    if not use_remote_resource_api():
        raise RuntimeError("Remote resource API is not configured.")

    response = requests.post(
        f"{REMOTE_RESOURCE_API_BASE_URL}/api/resource-unpaywall",
        json={"doi": str(doi or "").strip()},
        timeout=timeout_s,
    )
    response.raise_for_status()
    payload = response.json()
    item = payload.get("item") if isinstance(payload, dict) else {}
    return item if isinstance(item, dict) else {}


def download_pdf_bytes(*, url: str, timeout_s: int = 60, max_mb: int = 30) -> bytes:
    resp = requests.get(url, stream=True, timeout=timeout_s)
    resp.raise_for_status()

    chunks: List[bytes] = []
    size = 0
    max_bytes = max_mb * 1024 * 1024
    for chunk in resp.iter_content(chunk_size=1024 * 256):
        if not chunk:
            continue
        size += len(chunk)
        if size > max_bytes:
            raise ValueError(f"PDF too large (> {max_mb}MB)")
        chunks.append(chunk)
    return b"".join(chunks)


def extract_text_from_pdf(pdf_bytes: bytes, *, max_chars: int = 5000) -> str:
    try:
        import fitz  # PyMuPDF
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("PyMuPDF not installed. Install pymupdf.") from e

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    parts: List[str] = []
    for i in range(min(doc.page_count, 30)):
        page = doc.load_page(i)
        parts.append(page.get_text("text"))
        if sum(len(p) for p in parts) >= max_chars * 3:
            break

    text = normalize_for_match("\n".join(parts))
    if len(text) <= max_chars:
        return text

    # Heuristic: keep start + end slices (often abstract/intro + conclusions)
    head = text[: int(max_chars * 0.7)]
    tail = text[-int(max_chars * 0.3) :]
    return normalize_for_match(head + "\n…\n" + tail)[:max_chars]


# -----------------------------
# LLM adapters (minimal)
# -----------------------------


@dataclass
class LLMConfig:
    provider: Literal["Gemini"]
    model: str
    auth_mode: Literal["google_oauth", "auth_file", "cli_proxy_oauth"] = "google_oauth"
    credentials: Any = None
    project: str = ""
    location: str = "global"
    auth_file_path: str = ""
    fallback_models: List[str] = field(default_factory=list)
    available_models: List[str] = field(default_factory=list)


def fetch_gemini_models(
    *,
    auth_mode: Literal["google_oauth", "auth_file", "cli_proxy_oauth"] = "google_oauth",
    credentials: Any = None,
    project: str = "",
    location: str = "global",
) -> List[str]:
    if auth_mode == "cli_proxy_oauth":
        return fetch_cliproxy_models()

    out: List[str] = []
    credentials, resolved_project, _credential_source = resolve_vertex_runtime_credentials(
        credentials,
        project=project,
    )
    if credentials is None or not resolved_project.strip():
        return GEMINI_MODEL_OPTIONS
    from google import genai

    client = genai.Client(
        vertexai=True,
        credentials=credentials,
        project=resolved_project,
        location=location or "global",
    )
    items = client.models.list()
    for item in items:
        name = str(getattr(item, "name", "") or "").strip()
        methods = getattr(item, "supported_actions", None) or getattr(item, "supported_generation_methods", None) or []
        normalized = name.removeprefix("models/")
        if not normalized or "generateContent" not in methods:
            continue
        if any(token in normalized for token in ["tts", "embedding", "image", "aqa"]):
            continue
        if not (normalized.startswith("gemini-") or normalized.startswith("gemma-")):
            continue
        out.append(normalized)

    preferred = [model for model in GEMINI_MODEL_OPTIONS if model in out]
    remaining = sorted([model for model in out if model not in preferred], reverse=True)
    return preferred + remaining or GEMINI_MODEL_OPTIONS


def get_provider_models(
    provider: Literal["Gemini"],
    *,
    gemini_auth_mode: Literal["google_oauth", "auth_file", "cli_proxy_oauth"] = "google_oauth",
    google_credentials: Any = None,
) -> Tuple[List[str], str]:
    try:
        if gemini_auth_mode == "cli_proxy_oauth":
            models = fetch_cliproxy_models()
            if models:
                return models, "Loaded from local CLIProxyAPI (Gemini CLI OAuth)"
            return GEMINI_MODEL_OPTIONS, "Connected to local CLIProxyAPI. Using built-in Gemini model list until the local catalog is available."
        resolve_vertex_runtime_credentials(
            google_credentials,
            project=GOOGLE_CLOUD_PROJECT,
        )
        source_note = (
            "Loaded from Vertex AI Models API via Google OAuth"
            if gemini_auth_mode == "google_oauth"
            else "Loaded from Vertex AI Models API via auth file"
        )
        return (
            fetch_gemini_models(
                auth_mode=gemini_auth_mode,
                credentials=google_credentials,
                project=GOOGLE_CLOUD_PROJECT,
                location=GOOGLE_CLOUD_LOCATION,
            ),
            source_note,
        )
    except Exception as e:
        if gemini_auth_mode == "cli_proxy_oauth":
            return GEMINI_MODEL_OPTIONS, f"CLIProxyAPI unavailable, using built-in model list: {e}"
        if gemini_auth_mode == "google_oauth":
            return GEMINI_MODEL_OPTIONS, "Using built-in Gemini model list for Google OAuth. The live models endpoint is not available in this OAuth context."
        return GEMINI_MODEL_OPTIONS, f"Using fallback list: {e}"


def model_options_for_provider(
    provider: Literal["Gemini"],
    *,
    gemini_auth_mode: Literal["google_oauth", "auth_file", "cli_proxy_oauth"] = "google_oauth",
    google_credentials: Any = None,
) -> List[str]:
    return get_provider_models(
        provider,
        gemini_auth_mode=gemini_auth_mode,
        google_credentials=google_credentials,
    )[0]


def default_model_for_provider(
    provider: Literal["Gemini"],
    *,
    gemini_auth_mode: Literal["google_oauth", "auth_file", "cli_proxy_oauth"] = "google_oauth",
    google_credentials: Any = None,
) -> str:
    options = model_options_for_provider(
        provider,
        gemini_auth_mode=gemini_auth_mode,
        google_credentials=google_credentials,
    )
    return DEFAULT_GEMINI_MODEL if DEFAULT_GEMINI_MODEL in options else options[0]


def model_family(model: str) -> str:
    parts = model.split("-")
    return "-".join(parts[:2]) if len(parts) >= 2 else model


def compatible_fallback_models(model: str, available_models: List[str]) -> List[str]:
    available = [item for item in available_models if item != model]
    family = model_family(model)
    same_family = [item for item in available if model_family(item) == family]
    cross_family = [item for item in available if item not in same_family]
    return same_family + cross_family


def build_llm_config(
    provider: Literal["Gemini"],
    model: str,
    available_models: List[str],
    *,
    gemini_auth_mode: Literal["google_oauth", "auth_file", "cli_proxy_oauth"] = "google_oauth",
    google_credentials: Any = None,
    auth_file_path: str = "",
) -> LLMConfig:
    fallback_models = compatible_fallback_models(model, available_models)
    return LLMConfig(
        provider="Gemini",
        model=model,
        auth_mode=gemini_auth_mode,
        credentials=google_credentials,
        project=GOOGLE_CLOUD_PROJECT,
        location=GOOGLE_CLOUD_LOCATION,
        auth_file_path=auth_file_path,
        fallback_models=fallback_models,
        available_models=list(dict.fromkeys([item for item in available_models if str(item or "").strip()])),
    )


NODE_MODEL_SERIES: Dict[str, str] = {
    "WorkflowChat": "flash-lite",
    "Manager": "flash",
    "Planner": "flash",
    "Researcher": "flash-lite",
    "Reader": "flash-lite",
    "Writer": "flash",
    "Reviewer": "pro",
    "Editor": "flash",
    "Translator": "pro",
}

SERIES_MODEL_CHAINS: Dict[str, List[List[str]]] = {
    "pro": [
        ["gemini-2.5-pro"],
        ["gemini-2.5-flash"],
        ["gemini-2.5-flash-lite", "gemini-2.0-flash-lite"],
    ],
    "flash": [
        ["gemini-2.5-flash"],
        ["gemini-2.5-flash-lite", "gemini-2.0-flash-lite"],
    ],
    "flash-lite": [
        ["gemini-2.5-flash-lite", "gemini-2.0-flash-lite"],
    ],
}


def ordered_models_for_series(series: str, available_models: List[str]) -> List[str]:
    available = [str(item or "").strip() for item in available_models if str(item or "").strip()]
    if not available:
        available = list(GEMINI_MODEL_OPTIONS)
    ordered: List[str] = []
    for family in SERIES_MODEL_CHAINS.get(series, SERIES_MODEL_CHAINS["flash"]):
        for candidate in family:
            if candidate in available and candidate not in ordered:
                ordered.append(candidate)
    fallback = [candidate for candidate in available if candidate.startswith("gemini-")]
    for candidate in fallback or available:
        if candidate not in ordered:
            ordered.append(candidate)
    return ordered


def llm_config_for_role(cfg: LLMConfig, role: str) -> LLMConfig:
    available_models = list(cfg.available_models or [cfg.model] + list(cfg.fallback_models))
    ordered_models = ordered_models_for_series(NODE_MODEL_SERIES.get(role, "flash"), available_models)
    primary_model = ordered_models[0] if ordered_models else cfg.model
    fallback_models = [item for item in ordered_models[1:] if item != primary_model]
    return LLMConfig(
        provider=cfg.provider,
        model=primary_model,
        auth_mode=cfg.auth_mode,
        credentials=cfg.credentials,
        project=cfg.project,
        location=cfg.location,
        auth_file_path=cfg.auth_file_path,
        fallback_models=fallback_models,
        available_models=available_models,
    )


def is_retryable_llm_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        token in message
        for token in ["429", "503", "resource_exhausted", "quota", "unavailable", "temporarily", "timeout", "connection reset"]
    )


def retry_delay_seconds(exc: Exception) -> float:
    message = str(exc)
    match = re.search(r"retry in\s+([0-9.]+)s", message, flags=re.IGNORECASE)
    if match:
        return max(1.0, float(match.group(1)))
    return 2.0


def is_quota_exhausted_error(value: Any) -> bool:
    message = str(value or "").lower()
    return any(token in message for token in ["429", "resource_exhausted", "quota", "rate limit"])


def rotate_cliproxy_auth_account() -> Optional[str]:
    entries = cliproxy_auth_entries()
    if len(entries) < 2:
        return None
    current_index = 0
    for index, (_path, payload) in enumerate(entries):
        if bool(payload.get("checked", True)):
            current_index = index
            break
    next_index = (current_index + 1) % len(entries)
    if next_index == current_index:
        return None

    selected_path = entries[next_index][0]
    selected_name = selected_path.name
    for path, payload in entries:
        payload["checked"] = path == selected_path
        try:
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            continue
    return selected_name


def decode_proxy_bytes(value: Any) -> str:
    if isinstance(value, bytes):
        for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
            try:
                return value.decode(encoding)
            except Exception:
                continue
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def cliproxy_stream_text(*, model: str, prompt: str, should_stop: Optional[Callable[[], None]] = None) -> Iterator[str]:
    auth_refresh_attempted = False
    rotate_attempts = 0
    max_rotate_attempts = max(0, len(cliproxy_auth_entries()) - 1)
    while True:
        if should_stop:
            should_stop()
        response = requests.post(
            cliproxy_chat_completions_url(),
            headers=cliproxy_headers(),
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": True,
            },
            timeout=300,
            stream=True,
        )
        if response.status_code >= 400:
            detail = decode_proxy_bytes(response.content).strip()
            if response.status_code == 401 and not auth_refresh_attempted and refresh_cliproxy_auth_tokens():
                auth_refresh_attempted = True
                time.sleep(0.5)
                continue
            hint = cliproxy_error_hint()
            if response.status_code == 401 or is_cliproxy_auth_error(detail):
                message = "CLI proxy Gemini OAuth is invalid or expired. Sign in again in ResearchCompanion.exe."
                if detail:
                    message = f"{message} Response: {detail}"
                if hint:
                    message = f"{message} Hint: {hint}"
                raise RuntimeError(message)
            message = f"CLI proxy request failed with HTTP {response.status_code}."
            if detail:
                message = f"{message} Response: {detail}"
            if hint:
                message = f"{message} Hint: {hint}"
            if is_quota_exhausted_error(f"{response.status_code} {detail}") and rotate_attempts < max_rotate_attempts:
                rotated_name = rotate_cliproxy_auth_account()
                if rotated_name:
                    rotate_attempts += 1
                    time.sleep(0.5)
                    continue
            raise RuntimeError(message)

        for raw_line in response.iter_lines(decode_unicode=False):
            if should_stop:
                should_stop()
            if not raw_line:
                continue
            line = decode_proxy_bytes(raw_line).strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            payload = json.loads(data)
            choices = payload.get("choices") if isinstance(payload, dict) else None
            if not isinstance(choices, list) or not choices:
                continue
            delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
            if isinstance(delta, dict):
                content = delta.get("content")
                if isinstance(content, str) and content:
                    yield content
                    continue
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str) and content:
                    yield content
        return


def llm_stream_text(*, cfg: LLMConfig, prompt: str, should_stop: Optional[Callable[[], None]] = None) -> Iterator[str]:
    candidate_models = list(dict.fromkeys([cfg.model] + [model for model in cfg.fallback_models if model != cfg.model]))
    last_error: Optional[Exception] = None

    for candidate_model in candidate_models:
        for attempt in range(3):
            try:
                if should_stop:
                    should_stop()
                if cfg.auth_mode == "cli_proxy_oauth":
                    yield from cliproxy_stream_text(model=candidate_model, prompt=prompt, should_stop=should_stop)
                    return
                try:
                    from google import genai
                except Exception as e:  # noqa: BLE001
                    raise RuntimeError("Gemini SDK not installed. Install google-genai.") from e

                credentials, resolved_project, _credential_source = resolve_vertex_runtime_credentials(
                    cfg.credentials,
                    project=cfg.project,
                    auth_file_path=cfg.auth_file_path,
                )
                if credentials is None or not resolved_project.strip():
                    raise RuntimeError("Vertex AI runtime credentials unavailable.")
                client = genai.Client(
                    vertexai=True,
                    credentials=credentials,
                    project=resolved_project,
                    location=cfg.location or "global",
                )
                for chunk in client.models.generate_content_stream(
                    model=candidate_model,
                    contents=prompt,
                ):
                    if should_stop:
                        should_stop()
                    text = getattr(chunk, "text", None)
                    if text:
                        yield text
                return
            except Exception as e:  # noqa: BLE001
                last_error = e
                if is_cliproxy_auth_error(e):
                    raise RuntimeError(
                        f"LLM authentication failed for local companion Gemini OAuth. Last error: {e}"
                    ) from e
                if is_retryable_llm_error(e) and attempt < 2:
                    time.sleep(retry_delay_seconds(e))
                    continue
                break

    if last_error:
        raise RuntimeError(
            f"LLM request failed after retries. Provider={cfg.provider}, attempted models={candidate_models}. Last error: {last_error}"
        ) from last_error
    raise RuntimeError("Gemini request failed before any model attempt could complete.")


def llm_complete_text(*, cfg: LLMConfig, prompt: str, should_stop: Optional[Callable[[], None]] = None) -> str:
    return "".join(llm_stream_text(cfg=cfg, prompt=prompt, should_stop=should_stop))


# -----------------------------
# Prompts
# -----------------------------


def prompt_requests_revision(prompt: str) -> bool:
    normalized = str(prompt or "").lower()
    return any(
        token in normalized
        for token in [
            "edit",
            "revise",
            "rewrite",
            "improve",
            "modify",
            "correct",
            "fix",
            "polish",
            "chỉnh sửa",
            "viết lại",
            "sửa bài",
            "dựa trên file",
        ]
    )


def chat_history_block(messages: List[Dict[str, str]], *, limit: int = 8) -> str:
    lines: List[str] = []
    for item in messages[-limit:]:
        role = str(item.get("role") or "user").strip().title()
        content = str(item.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def compact_instruction_text(value: Any, *, limit: int = 700) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def guess_language_label(*values: str) -> Literal["English", "Vietnamese"]:
    combined = " ".join(str(value or "") for value in values)
    lowered = combined.lower()
    if re.search(r"[àáạảãăằắặẳẵâầấậẩẫđèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹ]", lowered):
        return "Vietnamese"
    vietnamese_tokens = [
        "không",
        "được",
        "nghiên cứu",
        "bài báo",
        "bài viết",
        "chỉnh sửa",
        "viết lại",
        "phản biện",
        "tiếng việt",
        "tài liệu",
        "trích dẫn",
        "và cộng sự",
    ]
    if sum(1 for token in vietnamese_tokens if token in lowered) >= 1:
        return "Vietnamese"
    return "English"


def normalize_language_label(value: Any, fallback: Literal["English", "Vietnamese"]) -> Literal["English", "Vietnamese"]:
    text = str(value or "").strip().lower()
    if text in {"vi", "vietnamese", "tiếng việt", "tieng viet"}:
        return "Vietnamese"
    if text in {"en", "english", "tiếng anh", "tieng anh"}:
        return "English"
    return fallback


def infer_requested_output_language(
    *values: str,
    fallback: Literal["English", "Vietnamese"],
) -> Literal["English", "Vietnamese"]:
    combined = " ".join(str(value or "") for value in values).lower()
    if any(token in combined for token in ["tiếng việt", "tieng viet", "vietnamese", "viết bằng tiếng việt", "dịch sang tiếng việt"]):
        return "Vietnamese"
    if any(token in combined for token in ["tiếng anh", "tieng anh", "english", "write in english", "dịch sang tiếng anh"]):
        return "English"
    return fallback


def language_detection_text(*values: str) -> str:
    combined = " ".join(str(value or "") for value in values)
    normalized = unicodedata.normalize("NFKD", combined)
    ascii_folded = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    ascii_folded = ascii_folded.replace("đ", "d").replace("Đ", "D")
    ascii_folded = re.sub(r"\s+", " ", ascii_folded).strip().lower()
    return ascii_folded


def guess_language_label(*values: str) -> Literal["English", "Vietnamese"]:
    combined = " ".join(str(value or "") for value in values)
    lowered = language_detection_text(*values)
    if re.search(r"[ăâđêôơưàáạảãằắặẳẵầấậẩẫèéẹẻẽềếệểễìíịỉĩòóọỏõồốộổỗờớợởỡùúụủũừứựửữỳýỵỷỹ]", combined.lower()):
        return "Vietnamese"
    vietnamese_tokens = [
        "khong",
        "duoc",
        "nghien cuu",
        "bai bao",
        "bai viet",
        "chinh sua",
        "viet lai",
        "phan bien",
        "tieng viet",
        "tai lieu",
        "trich dan",
        "va cong su",
    ]
    if any(token in lowered for token in vietnamese_tokens):
        return "Vietnamese"
    return "English"


def normalize_language_label(value: Any, fallback: Literal["English", "Vietnamese"]) -> Literal["English", "Vietnamese"]:
    text = language_detection_text(str(value or ""))
    if text in {"vi", "vietnamese", "tieng viet"}:
        return "Vietnamese"
    if text in {"en", "english", "tieng anh"}:
        return "English"
    return fallback


def infer_requested_output_language(
    *values: str,
    fallback: Literal["English", "Vietnamese"],
) -> Literal["English", "Vietnamese"]:
    combined = language_detection_text(*values)
    if any(token in combined for token in ["tieng viet", "vietnamese", "viet bang tieng viet", "dich sang tieng viet"]):
        return "Vietnamese"
    if any(token in combined for token in ["tieng anh", "english", "write in english", "dich sang tieng anh"]):
        return "English"
    guessed = guess_language_label(*values)
    return guessed if guessed == "Vietnamese" else fallback


VIETNAMESE_RAW_MARKERS = (
    "ă",
    "â",
    "đ",
    "ê",
    "ô",
    "ơ",
    "ư",
    "à",
    "á",
    "ả",
    "ã",
    "ạ",
    "è",
    "é",
    "ẻ",
    "ẽ",
    "ẹ",
    "ì",
    "í",
    "ỉ",
    "ĩ",
    "ị",
    "ò",
    "ó",
    "ỏ",
    "õ",
    "ọ",
    "ù",
    "ú",
    "ủ",
    "ũ",
    "ụ",
    "ỳ",
    "ý",
    "ỷ",
    "ỹ",
    "ỵ",
)

VIETNAMESE_FOLDED_TOKENS = (
    "tieng viet",
    "viet nam",
    "giao duc",
    "dai hoc",
    "kiem dinh",
    "chat luong",
    "nghien cuu",
    "bai bao",
    "ban thao",
    "phan bien",
    "chinh sua",
    "tai lieu",
    "trich dan",
    "tom tat",
    "va cong su",
)

VIETNAMESE_EXPLICIT_TOKENS = (
    "tieng viet",
    "vietnamese",
    "viet bang tieng viet",
    "tra loi bang tieng viet",
    "xuat bang tieng viet",
    "dich sang tieng viet",
)

ENGLISH_EXPLICIT_TOKENS = (
    "tieng anh",
    "english",
    "write in english",
    "answer in english",
    "respond in english",
    "dich sang tieng anh",
)


def _language_detection_variants(*values: str) -> tuple[str, str]:
    combined = " ".join(str(value or "") for value in values)
    normalized = unicodedata.normalize("NFKC", combined)
    lowered = re.sub(r"\s+", " ", normalized).strip().casefold()
    decomposed = unicodedata.normalize("NFKD", normalized)
    folded = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    folded = folded.replace("đ", "d").replace("Đ", "D")
    folded = re.sub(r"\s+", " ", folded).strip().casefold()
    return lowered, folded


def language_detection_text(*values: str) -> str:
    _, folded = _language_detection_variants(*values)
    return folded


def guess_language_label(*values: str) -> Literal["English", "Vietnamese"]:
    lowered, folded = _language_detection_variants(*values)
    if any(marker in lowered for marker in VIETNAMESE_RAW_MARKERS):
        return "Vietnamese"
    if any(token in folded for token in VIETNAMESE_FOLDED_TOKENS):
        return "Vietnamese"
    return "English"


def normalize_language_label(value: Any, fallback: Literal["English", "Vietnamese"]) -> Literal["English", "Vietnamese"]:
    lowered, folded = _language_detection_variants(str(value or ""))
    if lowered in {"vi", "vietnamese", "tiếng việt"} or folded == "tieng viet":
        return "Vietnamese"
    if lowered in {"en", "english", "tiếng anh"} or folded == "tieng anh":
        return "English"
    return fallback


def infer_requested_output_language(
    *values: str,
    fallback: Literal["English", "Vietnamese"],
) -> Literal["English", "Vietnamese"]:
    lowered, folded = _language_detection_variants(*values)
    if any(token in lowered for token in ("tiếng việt", "vietnamese")) or any(
        token in folded for token in VIETNAMESE_EXPLICIT_TOKENS
    ):
        return "Vietnamese"
    if any(token in lowered for token in ("tiếng anh", "english")) or any(
        token in folded for token in ENGLISH_EXPLICIT_TOKENS
    ):
        return "English"
    guessed = guess_language_label(*values)
    return guessed if guessed == "Vietnamese" else fallback


def clamp_int(value: Any, minimum: int, maximum: int, fallback: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return max(minimum, min(maximum, fallback))
    return max(minimum, min(maximum, parsed))


def infer_revision_targets(
    *,
    user_prompt: str,
    source_manuscript: str,
    fallback_word_count: int,
    fallback_reference_target: int,
) -> Tuple[int, int]:
    manuscript_words = count_words(source_manuscript or "")
    cited_sources = len(extract_citation_signatures(source_manuscript or ""))

    target_words = manuscript_words if manuscript_words >= 600 else fallback_word_count
    target_refs = cited_sources if cited_sources >= 2 else fallback_reference_target

    prompt_lower = str(user_prompt or "").lower()
    if any(token in prompt_lower for token in ["shorten", "condense", "compress", "rút gọn", "ngắn hơn"]):
        target_words = int(target_words * 0.82)
    elif any(token in prompt_lower for token in ["expand", "extend", "elaborate", "mở rộng", "dài hơn", "bổ sung"]):
        target_words = int(target_words * 1.18)

    if any(token in prompt_lower for token in ["more references", "add references", "thêm tài liệu", "bổ sung tài liệu", "thêm trích dẫn"]):
        target_refs = max(target_refs, fallback_reference_target)
    if any(token in prompt_lower for token in ["fewer references", "reduce references", "giảm tài liệu", "ít tài liệu hơn"]):
        target_refs = min(target_refs, fallback_reference_target)

    return (
        clamp_int(target_words, 1000, 20000, fallback_word_count),
        clamp_int(target_refs, 4, 200, fallback_reference_target),
    )


def translation_prompt(
    *,
    source_language: str,
    target_language: str,
    text: str,
    label: str = "text",
) -> str:
    return f"""Translate the {label} below from {source_language} to {target_language}.

Requirements:
- Preserve academic meaning, structure, headings, Markdown, and tables.
- Preserve citation strings and years exactly, including forms like (Nguyen et al., 2021) or (Nguyen & Tran, 2021).
- Preserve quoted text unless translation is clearly needed for the target language.
- Do not add commentary, notes, or code fences.
- Return only the translated text.

TEXT:
{text}
"""


def split_translation_chunks(text: str, *, max_chars: int = 6000) -> List[str]:
    value = str(text or "").strip()
    if not value:
        return []
    if len(value) <= max_chars:
        return [value]

    paragraphs = re.split(r"(\n\s*\n)", value)
    chunks: List[str] = []
    current = ""
    for piece in paragraphs:
        if not piece:
            continue
        candidate = f"{current}{piece}"
        if current and len(candidate) > max_chars:
            chunks.append(current.strip())
            current = piece
            continue
        if not current and len(piece) > max_chars:
            start = 0
            while start < len(piece):
                chunks.append(piece[start : start + max_chars].strip())
                start += max_chars
            current = ""
            continue
        current = candidate
    if current.strip():
        chunks.append(current.strip())
    return chunks


def translate_text_block(
    *,
    cfg: "LLMConfig",
    text: str,
    source_language: Literal["English", "Vietnamese"],
    target_language: Literal["English", "Vietnamese"],
    should_stop: Optional[Callable[[], None]] = None,
    label: str = "text",
    max_chars: int = 6000,
) -> str:
    value = str(text or "").strip()
    if not value or source_language == target_language:
        return value

    translated_parts: List[str] = []
    for chunk in split_translation_chunks(value, max_chars=max_chars):
        if should_stop:
            should_stop()
        translated_parts.append(
            llm_complete_text(
                cfg=cfg,
                prompt=translation_prompt(
                    source_language=source_language,
                    target_language=target_language,
                    text=chunk,
                    label=label,
                ),
                should_stop=should_stop,
            ).strip()
        )
    return "\n\n".join(part for part in translated_parts if part).strip()


def translate_chat_history_messages(
    *,
    cfg: "LLMConfig",
    messages: List[Dict[str, str]],
    source_language: Literal["English", "Vietnamese"],
    target_language: Literal["English", "Vietnamese"],
    should_stop: Optional[Callable[[], None]] = None,
) -> List[Dict[str, str]]:
    if source_language == target_language:
        return list(messages or [])

    translated_messages: List[Dict[str, str]] = []
    for item in messages or []:
        role = str(item.get("role") or "user").strip()
        content = str(item.get("content") or "").strip()
        if not content:
            translated_messages.append({"role": role, "content": ""})
            continue
        translated_messages.append(
            {
                "role": role,
                "content": translate_text_block(
                    cfg=cfg,
                    text=content,
                    source_language=source_language,
                    target_language=target_language,
                    should_stop=should_stop,
                    label=f"{role} message",
                    max_chars=2500,
                ),
            }
        )
    return translated_messages


def localize_in_text_citations(text: str, language: Literal["English", "Vietnamese"]) -> str:
    value = str(text or "")
    if not value or language != "Vietnamese":
        return value

    def replace_block(match: re.Match[str]) -> str:
        block = match.group(1)

        def replace_multi_author(part: str) -> str:
            updated = re.sub(
                r"\b([A-ZÀ-Ỹ][A-Za-zÀ-ỹ'`-]+)\s+et\s+al\.\s*,\s*(\d{4})",
                r"\1 và cộng sự, \2",
                part,
                flags=re.IGNORECASE,
            )
            updated = re.sub(
                r"\b([A-ZÀ-Ỹ][A-Za-zÀ-ỹ'`-]+)\s*&\s+([A-ZÀ-Ỹ][A-Za-zÀ-ỹ'`-]+)\s*,\s*(\d{4})",
                r"\1 và \2, \3",
                updated,
            )
            return updated

        localized_parts = [replace_multi_author(part.strip()) for part in block.split(";")]
        return "(" + "; ".join(localized_parts) + ")"

    return re.sub(r"\(([^()]{3,160})\)", replace_block, value)


def extract_citation_signatures(text: str) -> List[str]:
    signatures: List[str] = []
    seen: set[str] = set()
    pattern = re.compile(
        r"([A-Za-zÀ-ỹ'`-]+)(?:\s+(?:et\s+al\.|và\s+cộng\s+sự))?(?:\s+(?:&|và)\s+[A-Za-zÀ-ỹ'`-]+)?\s*,\s*(\d{4})",
        flags=re.IGNORECASE,
    )
    for block in re.findall(r"\(([^()]{3,120})\)", text):
        for part in block.split(";"):
            match = pattern.search(part)
            if not match:
                continue
            surname = normalize_for_match(match.group(1)).lower()
            year = match.group(2)
            signature = f"{surname}|{year}"
            if signature not in seen:
                seen.add(signature)
                signatures.append(signature)
    return signatures


def localize_in_text_citations(text: str, language: Literal["English", "Vietnamese"]) -> str:
    value = str(text or "")
    if not value or language != "Vietnamese":
        return value

    def replace_block(match: re.Match[str]) -> str:
        block = match.group(1)

        def replace_multi_author(part: str) -> str:
            updated = re.sub(
                r"\b([A-ZÀ-Ỹ][A-Za-zÀ-ỹ'`-]+)\s+et\s+al\.\s*,\s*(\d{4})",
                r"\1 và cộng sự, \2",
                part,
                flags=re.IGNORECASE,
            )
            updated = re.sub(
                r"\b([A-ZÀ-Ỹ][A-Za-zÀ-ỹ'`-]+)\s*(?:&|and|và)\s*([A-ZÀ-Ỹ][A-Za-zÀ-ỹ'`-]+)\s*,\s*(\d{4})",
                r"\1 và \2, \3",
                updated,
                flags=re.IGNORECASE,
            )
            updated = re.sub(
                r"\b([A-ZÀ-Ỹ][A-Za-zÀ-ỹ'`-]+)\s*,\s*[A-ZÀ-Ỹ][A-Za-zÀ-ỹ'`-]+(?:\s*,\s*[A-ZÀ-Ỹ][A-Za-zÀ-ỹ'`-]+)*(?:\s*,?\s*(?:&|and|và)\s*[A-ZÀ-Ỹ][A-Za-zÀ-ỹ'`-]+)?\s*,\s*(\d{4})",
                r"\1 và cộng sự, \2",
                updated,
                flags=re.IGNORECASE,
            )
            return updated

        localized_parts = [replace_multi_author(part.strip()) for part in block.split(";")]
        return "(" + "; ".join(localized_parts) + ")"

    return re.sub(r"\(([^()]{3,160})\)", replace_block, value)


def extract_citation_signatures(text: str) -> List[str]:
    signatures: List[str] = []
    seen: set[str] = set()
    pattern = re.compile(
        r"([A-Za-zÀ-ỹ'`-]+)(?:\s+(?:et\s+al\.|và\s+cộng\s+sự))?(?:\s+(?:&|and|và)\s+[A-Za-zÀ-ỹ'`-]+)?\s*,\s*(\d{4})",
        flags=re.IGNORECASE,
    )
    for block in re.findall(r"\(([^()]{3,120})\)", text):
        for part in block.split(";"):
            match = pattern.search(part)
            if not match:
                continue
            surname = normalize_for_match(match.group(1)).lower()
            year = match.group(2)
            signature = f"{surname}|{year}"
            if signature not in seen:
                seen.add(signature)
                signatures.append(signature)
    return signatures


def localize_in_text_citations(text: str, language: Literal["English", "Vietnamese"]) -> str:
    value = str(text or "")
    if not value or language != "Vietnamese":
        return value

    def replace_block(match: re.Match[str]) -> str:
        block = match.group(1)

        def replace_multi_author(part: str) -> str:
            updated = re.sub(
                r"\b([A-ZÀ-Ỹ][A-Za-zÀ-ỹ'`-]+)\s+et\s+al\.\s*,\s*(\d{4})",
                r"\1 và cộng sự, \2",
                part,
                flags=re.IGNORECASE,
            )
            updated = re.sub(
                r"\b([A-ZÀ-Ỹ][A-Za-zÀ-ỹ'`-]+)\s*(?:&|and|và)\s*([A-ZÀ-Ỹ][A-Za-zÀ-ỹ'`-]+)\s*,\s*(\d{4})",
                r"\1 và \2, \3",
                updated,
                flags=re.IGNORECASE,
            )
            updated = re.sub(
                r"\b([A-ZÀ-Ỹ][A-Za-zÀ-ỹ'`-]+)\s*,\s*[A-ZÀ-Ỹ][A-Za-zÀ-ỹ'`-]+(?:\s*,\s*[A-ZÀ-Ỹ][A-Za-zÀ-ỹ'`-]+)*(?:\s*,?\s*(?:&|and|và)\s*[A-ZÀ-Ỹ][A-Za-zÀ-ỹ'`-]+)?\s*,\s*(\d{4})",
                r"\1 và cộng sự, \2",
                updated,
                flags=re.IGNORECASE,
            )
            return updated

        localized_parts = [replace_multi_author(part.strip()) for part in block.split(";")]
        return "(" + "; ".join(localized_parts) + ")"

    return re.sub(r"\(([^()]{3,160})\)", replace_block, value)


def extract_citation_signatures(text: str) -> List[str]:
    signatures: List[str] = []
    seen: set[str] = set()
    pattern = re.compile(
        r"([A-Za-zÀ-ỹ'`-]+)(?:\s+(?:et\s+al\.|và\s+cộng\s+sự))?(?:\s+(?:&|and|và)\s+[A-Za-zÀ-ỹ'`-]+)?\s*,\s*(\d{4})",
        flags=re.IGNORECASE,
    )
    for block in re.findall(r"\(([^()]{3,120})\)", text):
        for part in block.split(";"):
            match = pattern.search(part)
            if not match:
                continue
            surname = normalize_for_match(match.group(1)).lower()
            year = match.group(2)
            signature = f"{surname}|{year}"
            if signature not in seen:
                seen.add(signature)
                signatures.append(signature)
    return signatures


def localize_in_text_citations(text: str, language: Literal["English", "Vietnamese"]) -> str:
    value = str(text or "")
    if not value or language != "Vietnamese":
        return value

    def replace_block(match: re.Match[str]) -> str:
        block = match.group(1)

        def replace_multi_author(part: str) -> str:
            updated = re.sub(
                r"\b([A-ZÀ-Ỹ][A-Za-zÀ-ỹ'`-]+)\s+et\s+al\.\s*,\s*(\d{4})",
                r"\1 và cộng sự, \2",
                part,
                flags=re.IGNORECASE,
            )
            updated = re.sub(
                r"\b([A-ZÀ-Ỹ][A-Za-zÀ-ỹ'`-]+)\s*(?:&|and|và)\s*([A-ZÀ-Ỹ][A-Za-zÀ-ỹ'`-]+)\s*,\s*(\d{4})",
                r"\1 và \2, \3",
                updated,
                flags=re.IGNORECASE,
            )
            updated = re.sub(
                r"\b([A-ZÀ-Ỹ][A-Za-zÀ-ỹ'`-]+)\s*,\s*[A-ZÀ-Ỹ][A-Za-zÀ-ỹ'`-]+(?:\s*,\s*[A-ZÀ-Ỹ][A-Za-zÀ-ỹ'`-]+)*(?:\s*,?\s*(?:&|and|và)\s*[A-ZÀ-Ỹ][A-Za-zÀ-ỹ'`-]+)?\s*,\s*(\d{4})",
                r"\1 và cộng sự, \2",
                updated,
                flags=re.IGNORECASE,
            )
            return updated

        localized_parts = [replace_multi_author(part.strip()) for part in block.split(";")]
        return "(" + "; ".join(localized_parts) + ")"

    return re.sub(r"\(([^()]{3,160})\)", replace_block, value)


def extract_citation_signatures(text: str) -> List[str]:
    signatures: List[str] = []
    seen: set[str] = set()
    pattern = re.compile(
        r"([A-Za-zÀ-ỹ'`-]+)(?:\s+(?:et\s+al\.|và\s+cộng\s+sự))?(?:\s+(?:&|and|và)\s+[A-Za-zÀ-ỹ'`-]+)?\s*,\s*(\d{4})",
        flags=re.IGNORECASE,
    )
    for block in re.findall(r"\(([^()]{3,120})\)", text):
        for part in block.split(";"):
            match = pattern.search(part)
            if not match:
                continue
            surname = normalize_for_match(match.group(1)).lower()
            year = match.group(2)
            signature = f"{surname}|{year}"
            if signature not in seen:
                seen.add(signature)
                signatures.append(signature)
    return signatures


def _first_json_object(raw_text: str) -> Dict[str, Any]:
    cleaned = extract_json_payload(str(raw_text or ""))
    if not cleaned:
        return {}
    try:
        loaded = json.loads(cleaned)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            return {}
        try:
            loaded = json.loads(match.group(0))
            return loaded if isinstance(loaded, dict) else {}
        except Exception:
            return {}


def _explicit_literature_request(prompt: str) -> bool:
    normalized = str(prompt or "").lower()
    return any(
        token in normalized
        for token in [
            "literature review",
            "related work",
            "add references",
            "update references",
            "find papers",
            "search papers",
            "search literature",
            "search online",
            "bổ sung tài liệu",
            "cập nhật tài liệu",
            "tìm bài báo",
            "tìm tài liệu",
            "tìm thêm nguồn",
        ]
    )


def fallback_manager_guidance(
    *,
    topic: str,
    user_prompt: str,
    chat_history: str,
    research_query: str,
    source_manuscript: str,
    attachment_context: str,
    requested_target_word_count: int = 4000,
    requested_reference_target: int = 24,
) -> Dict[str, Any]:
    revision_mode = prompt_requests_revision(user_prompt) and bool(source_manuscript.strip())
    explicit_lit = _explicit_literature_request(user_prompt)
    source_language = guess_language_label(topic, user_prompt, chat_history[:3000], attachment_context[:3000], source_manuscript[:3000])
    workflow_language: Literal["English", "Vietnamese"] = "English"
    output_language = infer_requested_output_language(
        topic,
        user_prompt,
        chat_history[:3000],
        attachment_context[:3000],
        source_manuscript[:3000],
        fallback=source_language,
    )
    effective_word_count = requested_target_word_count
    effective_reference_target = requested_reference_target
    if revision_mode and source_manuscript.strip():
        effective_word_count, effective_reference_target = infer_revision_targets(
            user_prompt=user_prompt,
            source_manuscript=source_manuscript,
            fallback_word_count=requested_target_word_count,
            fallback_reference_target=requested_reference_target,
        )
    topic_focus = compact_instruction_text(source_manuscript.splitlines()[0] if revision_mode and source_manuscript.strip() else topic, limit=220)
    use_translator = output_language != workflow_language
    editor_brief = f"Polish wording in {workflow_language} while preserving citations, structure, and requested scope."
    translator_brief = (
        f"Translate the final manuscript from {workflow_language} into {output_language}. Use attached glossary, spreadsheet, "
        "and manuscript context to keep terminology consistent. Preserve citation markers, years, structure, and academic tone."
        if use_translator
        else f"No translation is required. Keep the final manuscript in {output_language} unless language drift must be corrected."
    )
    return {
        "task_mode": "revise_manuscript" if revision_mode else "new_paper",
        "source_language": source_language,
        "workflow_language": workflow_language,
        "output_language": output_language,
        "translate_to_english_for_workflow": source_language != workflow_language,
        "use_translator": use_translator,
        "target_word_count": effective_word_count,
        "reference_target": effective_reference_target,
        "topic_focus": topic_focus or compact_instruction_text(topic, limit=220),
        "research_query": compact_instruction_text(research_query or topic, limit=180),
        "use_online_search": True if revision_mode else bool(explicit_lit or not revision_mode),
        "preserve_structure": bool(revision_mode),
        "node_sequence": ["Researcher", "Reader", "Writer"] if revision_mode else ["Planner", "Researcher", "Reader", "Writer"],
        "planner_brief": "Keep the outline tightly aligned with the user's request and any attached manuscript.",
        "researcher_brief": "Search only for sources that are directly relevant to the core academic topic.",
        "reader_brief": "Extract only evidence that is clearly useful for the planned manuscript.",
        "writer_brief": "Write conservatively and stay faithful to the manuscript, prompt, and available evidence.",
        "reviewer_brief": "Critique logic, structure, unsupported claims, and target-length compliance.",
        "editor_brief": editor_brief,
        "translator_brief": translator_brief,
        "notes": "Fallback manager guidance was used because structured manager output was unavailable.",
    }


def resolve_editor_brief(
    *,
    editor_brief: str,
    workflow_language: Literal["English", "Vietnamese"],
    output_language: Literal["English", "Vietnamese"],
    use_translator: bool,
) -> str:
    brief = compact_instruction_text(editor_brief, limit=700)
    normalized_brief = language_detection_text(brief)
    if use_translator:
        handoff_clause = (
            f" Keep all prose in {workflow_language} at this stage. Do not perform the final translation; the Translator node will deliver {output_language}."
        )
        if "translator node" not in normalized_brief and "do not perform the final translation" not in normalized_brief:
            brief = (brief.rstrip(".") + "." + handoff_clause).strip()
    elif brief:
        language_clause = f" Ensure the final manuscript remains in {output_language}."
        if f"remains in {output_language.lower()}" not in normalized_brief and f"in {output_language.lower()}" not in normalized_brief:
            brief = (brief.rstrip(".") + "." + language_clause).strip()
    return compact_instruction_text(brief, limit=700)


def resolve_translator_brief(
    *,
    translator_brief: str,
    workflow_language: Literal["English", "Vietnamese"],
    output_language: Literal["English", "Vietnamese"],
    use_translator: bool,
) -> str:
    brief = compact_instruction_text(translator_brief, limit=700)
    normalized_brief = language_detection_text(brief)
    if use_translator:
        translation_clause = (
            f" Translate the final manuscript from {workflow_language} into {output_language}. "
            "Use attached glossary, spreadsheet, and offline context whenever they define preferred terminology. "
            f"Ensure all final prose is in {output_language}."
        )
        if "translate the final manuscript" not in normalized_brief or "all final prose is in" not in normalized_brief:
            brief = (brief.rstrip(".") + "." + translation_clause).strip()
    elif brief:
        keep_clause = f" No translation is required; keep the manuscript in {output_language}."
        if "no translation is required" not in normalized_brief:
            brief = (brief.rstrip(".") + "." + keep_clause).strip()
    return compact_instruction_text(brief, limit=700)


def normalize_manager_guidance(
    raw: Dict[str, Any],
    *,
    topic: str,
    user_prompt: str,
    chat_history: str,
    research_query: str,
    source_manuscript: str,
    attachment_context: str,
    requested_target_word_count: int = 4000,
    requested_reference_target: int = 24,
) -> Dict[str, Any]:
    fallback = fallback_manager_guidance(
        topic=topic,
        user_prompt=user_prompt,
        chat_history=chat_history,
        research_query=research_query,
        source_manuscript=source_manuscript,
        attachment_context=attachment_context,
        requested_target_word_count=requested_target_word_count,
        requested_reference_target=requested_reference_target,
    )
    if not raw:
        return fallback

    task_mode = str(raw.get("task_mode") or fallback["task_mode"]).strip().lower()
    allowed_modes = {"new_paper", "revise_manuscript", "critique_and_rewrite", "summarize", "compare", "literature_review"}
    if task_mode not in allowed_modes:
        task_mode = fallback["task_mode"]

    def pick_text(key: str, limit: int) -> str:
        return compact_instruction_text(raw.get(key) or fallback.get(key, ""), limit=limit)

    use_online_search_raw = raw.get("use_online_search")
    if isinstance(use_online_search_raw, bool):
        use_online_search = use_online_search_raw
    else:
        use_online_search = bool(fallback["use_online_search"])

    preserve_structure_raw = raw.get("preserve_structure")
    if isinstance(preserve_structure_raw, bool):
        preserve_structure = preserve_structure_raw
    else:
        preserve_structure = bool(fallback["preserve_structure"])

    use_translator_raw = raw.get("use_translator")
    if isinstance(use_translator_raw, bool):
        use_translator = use_translator_raw
    else:
        use_translator = bool(fallback.get("use_translator"))

    raw_sequence = raw.get("node_sequence")
    allowed_nodes = ["Planner", "Researcher", "Reader", "Writer"]
    normalized_sequence: List[str] = []
    if isinstance(raw_sequence, list):
        seen_nodes: set[str] = set()
        for item in raw_sequence:
            node = str(item or "").strip().title()
            if node not in allowed_nodes or node in seen_nodes:
                continue
            seen_nodes.add(node)
            normalized_sequence.append(node)
    if not normalized_sequence:
        normalized_sequence = list(fallback.get("node_sequence", ["Planner", "Researcher", "Reader", "Writer"]))

    source_language = normalize_language_label(raw.get("source_language"), fallback["source_language"])
    workflow_language = normalize_language_label(raw.get("workflow_language"), fallback["workflow_language"])
    output_language = normalize_language_label(raw.get("output_language"), fallback["output_language"])
    output_language = infer_requested_output_language(
        topic,
        user_prompt,
        chat_history[:3000],
        attachment_context[:3000],
        source_manuscript[:3000],
        str(raw.get("output_language") or ""),
        fallback=output_language,
    )
    if workflow_language != "English":
        workflow_language = "English"

    target_word_count = clamp_int(raw.get("target_word_count"), 1000, 20000, fallback["target_word_count"])
    reference_target = clamp_int(raw.get("reference_target"), 4, 200, fallback["reference_target"])
    if task_mode in {"revise_manuscript", "critique_and_rewrite"} and source_manuscript.strip():
        target_word_count, reference_target = infer_revision_targets(
            user_prompt=user_prompt,
            source_manuscript=source_manuscript,
            fallback_word_count=target_word_count,
            fallback_reference_target=reference_target,
        )

    normalized = {
        "task_mode": task_mode,
        "source_language": source_language,
        "workflow_language": workflow_language,
        "output_language": output_language,
        "translate_to_english_for_workflow": source_language != workflow_language,
        "use_translator": use_translator or output_language != workflow_language,
        "target_word_count": target_word_count,
        "reference_target": reference_target,
        "topic_focus": pick_text("topic_focus", 220) or fallback["topic_focus"],
        "research_query": pick_text("research_query", 180) or fallback["research_query"],
        "use_online_search": use_online_search,
        "preserve_structure": preserve_structure,
        "node_sequence": normalized_sequence,
        "planner_brief": pick_text("planner_brief", 600) or fallback["planner_brief"],
        "researcher_brief": pick_text("researcher_brief", 500) or fallback["researcher_brief"],
        "reader_brief": pick_text("reader_brief", 500) or fallback["reader_brief"],
        "writer_brief": pick_text("writer_brief", 700) or fallback["writer_brief"],
        "reviewer_brief": pick_text("reviewer_brief", 700) or fallback["reviewer_brief"],
        "editor_brief": pick_text("editor_brief", 700) or fallback["editor_brief"],
        "translator_brief": pick_text("translator_brief", 700) or fallback["translator_brief"],
        "notes": pick_text("notes", 700) or fallback["notes"],
    }
    normalized["editor_brief"] = resolve_editor_brief(
        editor_brief=str(normalized.get("editor_brief") or fallback["editor_brief"]),
        workflow_language=workflow_language,
        output_language=output_language,
        use_translator=bool(normalized.get("use_translator")),
    )
    normalized["translator_brief"] = resolve_translator_brief(
        translator_brief=str(normalized.get("translator_brief") or fallback["translator_brief"]),
        workflow_language=workflow_language,
        output_language=output_language,
        use_translator=bool(normalized.get("use_translator")),
    )
    if normalized["task_mode"] == "revise_manuscript" and source_manuscript.strip():
        normalized["preserve_structure"] = True
    if normalized["task_mode"] in {"revise_manuscript", "critique_and_rewrite"} and "Researcher" not in normalized["node_sequence"]:
        normalized["node_sequence"] = ["Researcher", "Reader", "Writer"]
    return normalized


def manager_prompt(
    *,
    topic: str,
    language: str,
    target_word_count: int,
    reference_target: int,
    user_prompt: str = "",
    chat_history: str = "",
    attachment_context: str = "",
    source_manuscript: str = "",
    research_query: str = "",
) -> str:
    return f"""You are the management node for an academic multi-agent workflow.

Analyze the user's request and return ONLY valid JSON.

JSON schema:
{{
  "task_mode": "new_paper | revise_manuscript | critique_and_rewrite | summarize | compare | literature_review",
  "source_language": "English | Vietnamese",
  "workflow_language": "English | Vietnamese",
  "output_language": "English | Vietnamese",
  "translate_to_english_for_workflow": true,
  "use_translator": true,
  "target_word_count": 4000,
  "reference_target": 24,
  "topic_focus": "short academic topic description",
  "research_query": "4-12 keyword academic search query with no filenames or workflow text",
  "use_online_search": true,
  "preserve_structure": false,
  "node_sequence": ["Planner", "Researcher", "Reader", "Writer"],
  "planner_brief": "instructions for planner",
  "researcher_brief": "instructions for researcher",
  "reader_brief": "instructions for reader",
  "writer_brief": "instructions for writer",
  "reviewer_brief": "instructions for reviewer",
  "editor_brief": "instructions for editor",
  "translator_brief": "instructions for translator",
  "notes": "brief rationale"
}}

Rules:
- If the user wants to revise an uploaded manuscript or address reviewer comments, set `task_mode` to `revise_manuscript` or `critique_and_rewrite`.
- When revising an uploaded manuscript, set `preserve_structure` to true unless the user explicitly asks to restructure.
- Detect the conversation/material language in `source_language`.
- Infer `output_language` from the user's explicit request in the prompt/chat first. Do not blindly follow the UI language field if the user clearly asked for Vietnamese or English output.
- Use `output_language` for the final manuscript language requested by the user. If the conversation is Vietnamese and there is no contrary instruction, use `Vietnamese`.
- For academic search, planning, drafting, reviewing, and rewriting, prefer `workflow_language = English` whenever the source language is not English.
- If `workflow_language` differs from `source_language`, set `translate_to_english_for_workflow` to true.
- If `output_language` differs from `workflow_language`, set `use_translator` to true.
- When `use_translator` is true, `editor_brief` should focus on polishing only, while `translator_brief` must explicitly tell the Translator node to translate the final manuscript from `workflow_language` into `output_language`.
- If the user attached terminology tables, glossaries, spreadsheets, or translation examples, tell the Translator node to use them as preferred terminology guidance.
- For `revise_manuscript` or `critique_and_rewrite`, choose `target_word_count` and `reference_target` from the actual manuscript task, not from the UI settings. Preserve manuscript scale unless the user explicitly asks to shorten, expand, or change citation density.
- Set `use_online_search` to false if the task can be completed mainly from the attached manuscript and offline files. Set it to true only when literature update/search is genuinely needed.
- `research_query` must be short, academic, and reusable for Scopus/CORE/OpenAlex.
- If `workflow_language` is English, then `topic_focus`, `research_query`, and all node briefs must also be written in English.
- `node_sequence` must be a sensible ordered subset of ["Planner", "Researcher", "Reader", "Writer"].
- For a new paper, the usual route is ["Planner", "Researcher", "Reader", "Writer"].
- For a manuscript revision that needs new supporting citations or evidence, prefer ["Researcher", "Reader", "Writer"].
- For a light manuscript revision with no new literature needed, ["Writer"] is allowed.
- Do not include markdown fences.

Language: {language}
Target total words: {target_word_count}
Reference target: {reference_target}
Original topic field: {topic}
Heuristic research query: {research_query or topic}
User prompt:
{user_prompt or topic}

Recent chat:
{chat_history or "(none)"}

Offline attachment context:
{attachment_context[:10000] if attachment_context else "(none)"}

Source manuscript excerpt:
{source_manuscript[:10000] if source_manuscript else "(none)"}
"""


def planner_prompt(
    topic: str,
    language: str,
    target_word_count: int,
    reference_target: int,
    *,
    user_prompt: str = "",
    chat_history: str = "",
    attachment_context: str = "",
    source_manuscript: str = "",
    manager_guidance: Optional[Dict[str, Any]] = None,
) -> str:
    reference_budget = estimated_reference_words(reference_target)
    body_budget = max(400, target_word_count - reference_budget)
    revision_mode = prompt_requests_revision(user_prompt) and bool(source_manuscript.strip())
    guidance = manager_guidance or {}
    return f"""You are a research planner. Create a concise IMRaD outline for a paper about: {topic}

Language: {language}
Target manuscript length: about {target_word_count} words total
Approximate body-text budget for IMRaD sections: about {body_budget} words
Approximate references budget: about {reference_budget} words
Reference target: about {reference_target} sources
User task request: {user_prompt or topic}

Constraints:
- Output Markdown only.
- Keep it actionable.
- Include approximate word allocation for Introduction, Methods, Results, and Discussion.
- Do NOT cite sources.
- If the user request is to revise an uploaded manuscript, keep the outline aligned with that manuscript instead of inventing a disconnected structure.

Management guidance:
- Task mode: {guidance.get("task_mode", "new_paper")}
- Preserve original structure: {guidance.get("preserve_structure", False)}
- Topic focus: {guidance.get("topic_focus", topic)}
- Planner brief: {guidance.get("planner_brief", "(none)")}

Recent chat context:
{chat_history or "(none)"}

Offline attachment context:
{attachment_context or "(none)"}

Source manuscript to preserve/revise:
{source_manuscript[:12000] if revision_mode else "(none)"}
"""


def quote_extraction_prompt(text: str, authors: str, year: int, doi: str, title: str) -> str:
    return f"""Extract 5-10 short, high-signal verbatim quotations from the SOURCE TEXT below.

For each quotation, return JSON list items with keys: quote, citation.
- citation must be "(FirstAuthor, {year})" if authors present; otherwise "(Unknown, {year})".
- quote must be verbatim from SOURCE TEXT.
- Keep each quote <= 240 characters.

Return ONLY valid JSON array.

Paper:
- title: {title}
- authors: {authors}
- year: {year}
- doi: {doi}

SOURCE TEXT:
{text}
"""


def heuristic_extract_quotes(text: str, *, max_quotes: int = 5) -> List[str]:
    candidates = re.split(r"(?<=[.!?])\s+", normalize_for_match(text))
    quotes: List[str] = []
    for candidate in candidates:
        candidate = candidate.strip()
        if len(candidate) < 80 or len(candidate) > 240:
            continue
        if candidate in quotes:
            continue
        if len(candidate.split()) < 12:
            continue
        quotes.append(candidate)
        if len(quotes) >= max_quotes:
            break
    return quotes


def section_word_targets(total_words: int) -> Dict[str, int]:
    total = max(total_words, 400)
    return {
        "Introduction": int(total * 0.22),
        "Methods": int(total * 0.18),
        "Results": int(total * 0.28),
        "Discussion": total - int(total * 0.22) - int(total * 0.18) - int(total * 0.28),
    }


def dynamic_section_word_target(
    *,
    total_target_words: int,
    current_word_count: int,
    section_name: str,
    remaining_sections: List[str],
) -> int:
    base_targets = section_word_targets(total_target_words)
    remaining_total = max(120, total_target_words - current_word_count)
    remaining_weight = sum(base_targets[name] for name in remaining_sections)
    if remaining_weight <= 0:
        return max(120, remaining_total)
    proportional_target = remaining_total * (base_targets[section_name] / remaining_weight)
    return max(120, int(round(proportional_target)))


def constrained_writer_prompt(
    *,
    language: str,
    plan: str,
    section_name: str,
    section_target_words: int,
    current_total_words: int,
    total_target_words: int,
    existing_draft: str,
    quotes: List[Quote],
    manager_brief: str = "",
) -> str:
    quotes_block = "\n".join([f"- {q.get('quote','')} {q.get('citation','')}" for q in quotes])
    return f"""Write ONLY the {section_name} section of an academic article in {language}.

Target length for this section: about {section_target_words} words.
Current draft length before this section: about {current_total_words} words.
Final target for the whole manuscript body: about {total_target_words} words.

CRITICAL CONSTRAINTS:
- You may ONLY use the QUOTATIONS provided below as evidence.
- Do not introduce any new facts, numbers, methods, datasets, or citations.
- Use the provided in-text citations exactly as given.
- Write in polished academic prose.
- Keep this section balanced so the full manuscript stays close to the final target length.
- Return Markdown for this section only, beginning with `## {section_name}`.
- Management guidance: {manager_brief or "(none)"}

IMRaD OUTLINE:
{plan}

ALREADY WRITTEN SECTIONS:
{existing_draft}

QUOTATIONS (verbatim evidence):
{quotes_block}
"""


def revision_writer_prompt(
    *,
    language: str,
    user_prompt: str,
    plan: str,
    source_manuscript: str,
    attachment_context: str,
    quotes: List[Quote],
    total_target_words: int,
    manager_brief: str = "",
) -> str:
    quotes_block = "\n".join([f"- {q.get('quote','')} {q.get('citation','')}" for q in quotes[:80]])
    return f"""Revise the SOURCE MANUSCRIPT below in {language}.

User request:
{user_prompt}

Target body length: about {total_target_words} words.

Requirements:
- Preserve the original manuscript's core structure, argument, and section logic unless the user explicitly asks to restructure it.
- Use the planner outline only as guidance for refinement, not as a reason to replace the manuscript with a totally new paper.
- Incorporate relevant evidence from the provided quotations where useful.
- Use the offline attachment context if it helps clarify or extend the attached manuscript.
- Do not invent citations that are not already supported by the manuscript or provided quotations.
- Return Markdown body only.
- Do not include a References section.
- Management guidance: {manager_brief or "(none)"}

Planner outline:
{plan}

Offline attachment context:
{attachment_context or "(none)"}

Available quotations:
{quotes_block}

SOURCE MANUSCRIPT:
{source_manuscript}
"""


def editor_prompt(
    language: str,
    draft: str,
    metadata: List[PaperMeta],
    *,
    user_prompt: str = "",
    attachment_context: str = "",
    manager_brief: str = "",
) -> str:
    return f"""Format the DRAFT below into a polished academic manuscript in {language}.

Constraints:
- Keep all in-text citations as provided; do not invent new citations.
- Keep the body substantive and detailed.
- Preserve the approximate overall length.
- Do NOT add or rewrite the References section.
- Output Markdown.
- Respect the user's specific editing request if one is given.

User request:
{user_prompt or "(none)"}

Management guidance:
{manager_brief or "(none)"}

Offline attachment context:
{attachment_context or "(none)"}

DRAFT:
{draft}
"""


def reviewer_prompt(
    *,
    language: str,
    draft: str,
    target_body_words: int,
    reference_target: int,
    manager_brief: str = "",
) -> str:
    current_words = count_words(draft)
    min_words = max(300, int(target_body_words * 0.88))
    max_words = max(min_words + 80, int(target_body_words * 1.08))
    return f"""You are a strict manuscript reviewer. Revise the DRAFT below in {language}.

Current body length: about {current_words} words.
Target body length: about {target_body_words} words.
Allowed body-length range after revision: {min_words}-{max_words} words.
Maximum number of distinct cited sources allowed in the body: {reference_target}.

Requirements:
- Keep the IMRaD structure and academic tone.
- If the draft is too long, compress aggressively while preserving the strongest points.
- If the draft is too short, expand modestly using ONLY claims already supported by citations already present in the draft.
- Do not invent new evidence or new citations.
- Reduce citation spread so the manuscript relies on at most {reference_target} distinct cited sources.
- Return Markdown only.
- Do NOT add a References section.
- Management guidance: {manager_brief or "(none)"}

DRAFT:
{draft}
"""


# -----------------------------
# Workflow runner (LangGraph)
# -----------------------------


def execute_workflow(
    *,
    topic: str,
    language: Literal["English", "Vietnamese"],
    target_word_count: int,
    reference_target: int,
    llm_cfg: LLMConfig,
    statuses: Dict[str, str],
    hooks: Optional[WorkflowHooks] = None,
    user_prompt: str = "",
    chat_history: Optional[List[Dict[str, str]]] = None,
    search_filters: Optional[Dict[str, Any]] = None,
    attachment_context: str = "",
    source_manuscript: str = "",
    research_query: str = "",
) -> Tuple[State, List[str]]:
    logs: List[str] = []
    hooks = hooks or WorkflowHooks()

    def cfg_for(role: str) -> LLMConfig:
        return llm_config_for_role(llm_cfg, role)

    def resolved_target_word_count(state: State) -> int:
        guidance = state.get("manager_guidance", {}) or {}
        return clamp_int(guidance.get("target_word_count"), 1000, 20000, target_word_count)

    def resolved_reference_target(state: State) -> int:
        guidance = state.get("manager_guidance", {}) or {}
        return clamp_int(guidance.get("reference_target"), 4, 200, reference_target)

    def check_stop() -> None:
        if hooks and hooks.should_stop:
            hooks.should_stop()

    def refresh_cards() -> None:
        check_stop()
        if hooks and hooks.on_status:
            hooks.on_status(dict(statuses))

    def add_log(message: str) -> None:
        check_stop()
        timestamp = time.strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        logs.append(line)
        if hooks and hooks.on_log:
            hooks.on_log(list(logs), line)

    def node_manager(state: State) -> Dict[str, Any]:
        check_stop()
        add_log("Manager started: analyzing prompt, chat context, and attachments for node routing.")
        history_text = chat_history_block(state.get("chat_history", []))
        raw = llm_complete_text(
            cfg=cfg_for("Manager"),
            prompt=manager_prompt(
                topic=state.get("topic", ""),
                language=state.get("language", "English"),
                target_word_count=target_word_count,
                reference_target=reference_target,
                user_prompt=state.get("user_prompt", ""),
                chat_history=history_text,
                attachment_context=state.get("attachment_context", ""),
                source_manuscript=state.get("source_manuscript", ""),
                research_query=state.get("research_query", "") or state.get("topic", ""),
            ),
            should_stop=check_stop,
        )
        guidance = normalize_manager_guidance(
            _first_json_object(raw),
            topic=state.get("topic", ""),
            user_prompt=state.get("user_prompt", ""),
            chat_history=history_text,
            research_query=state.get("research_query", "") or state.get("topic", ""),
            source_manuscript=state.get("source_manuscript", ""),
            attachment_context=state.get("attachment_context", ""),
            requested_target_word_count=target_word_count,
            requested_reference_target=reference_target,
        )
        source_language = normalize_language_label(guidance.get("source_language"), guess_language_label(state.get("topic", ""), state.get("user_prompt", ""), history_text))
        workflow_language = normalize_language_label(guidance.get("workflow_language"), "English")
        output_language = infer_requested_output_language(
            state.get("topic", ""),
            state.get("user_prompt", ""),
            history_text,
            state.get("attachment_context", "")[:3000],
            state.get("source_manuscript", "")[:3000],
            str(guidance.get("output_language") or ""),
            fallback=normalize_language_label(guidance.get("output_language"), state.get("language", source_language)),
        )
        translated_topic = state.get("topic", "")
        translated_user_prompt = state.get("user_prompt", "")
        translated_attachment_context = state.get("attachment_context", "")
        translated_source_manuscript = state.get("source_manuscript", "")
        translated_chat_history = list(state.get("chat_history", []))
        translated_research_query = str(guidance.get("research_query", state.get("research_query", "") or state.get("topic", ""))).strip()

        if source_language != workflow_language:
            add_log(f"Manager translation started: converting workflow context from {source_language} to {workflow_language}.")
            translated_topic = translate_text_block(
                cfg=cfg_for("Manager"),
                text=translated_topic,
                source_language=source_language,
                target_language=workflow_language,
                should_stop=check_stop,
                label="workflow topic",
                max_chars=2500,
            ) or translated_topic
            translated_user_prompt = translate_text_block(
                cfg=cfg_for("Manager"),
                text=translated_user_prompt,
                source_language=source_language,
                target_language=workflow_language,
                should_stop=check_stop,
                label="user prompt",
                max_chars=5000,
            ) or translated_user_prompt
            translated_attachment_context = translate_text_block(
                cfg=cfg_for("Manager"),
                text=translated_attachment_context,
                source_language=source_language,
                target_language=workflow_language,
                should_stop=check_stop,
                label="offline attachment context",
                max_chars=5000,
            ) or translated_attachment_context
            translated_source_manuscript = translate_text_block(
                cfg=cfg_for("Manager"),
                text=translated_source_manuscript,
                source_language=source_language,
                target_language=workflow_language,
                should_stop=check_stop,
                label="source manuscript",
                max_chars=5000,
            ) or translated_source_manuscript
            translated_chat_history = translate_chat_history_messages(
                cfg=cfg_for("Manager"),
                messages=translated_chat_history,
                source_language=source_language,
                target_language=workflow_language,
                should_stop=check_stop,
            )
            translated_research_query = translate_text_block(
                cfg=cfg_for("Manager"),
                text=translated_research_query,
                source_language=source_language,
                target_language=workflow_language,
                should_stop=check_stop,
                label="research query",
                max_chars=1000,
            ) or translated_research_query
            add_log("Manager translation done: workflow context normalized for internal processing.")

        guidance["topic_focus"] = compact_instruction_text(
            str(guidance.get("topic_focus") or translated_topic or state.get("topic", "")),
            limit=220,
        )
        guidance["research_query"] = compact_instruction_text(
            str(translated_research_query or guidance.get("research_query") or translated_topic or state.get("topic", "")),
            limit=180,
        )
        guidance["source_language"] = source_language
        guidance["workflow_language"] = workflow_language
        guidance["output_language"] = output_language
        guidance["translate_to_english_for_workflow"] = source_language != workflow_language
        guidance["use_translator"] = bool(guidance.get("use_translator", output_language != workflow_language))
        manager_snapshot = {
            "task_mode": guidance.get("task_mode", "new_paper"),
            "source_language": guidance.get("source_language", source_language),
            "workflow_language": guidance.get("workflow_language", workflow_language),
            "output_language": guidance.get("output_language", output_language),
            "translate_to_english_for_workflow": guidance.get(
                "translate_to_english_for_workflow",
                source_language != workflow_language,
            ),
            "use_translator": guidance.get("use_translator", output_language != workflow_language),
        }
        for key, value in guidance.items():
            if key not in manager_snapshot:
                manager_snapshot[key] = value
        if hooks and hooks.on_manager:
            hooks.on_manager(json.dumps(manager_snapshot, ensure_ascii=False, indent=2))
        add_log(
            "Manager done: "
            f"task_mode={guidance.get('task_mode', 'new_paper')}, "
            f"workflow_language={workflow_language}, "
            f"output_language={output_language}, "
            f"target_word_count={guidance.get('target_word_count', target_word_count)}, "
            f"reference_target={guidance.get('reference_target', reference_target)}, "
            f"use_online_search={guidance.get('use_online_search', True)}, "
            f"research_query={guidance.get('research_query', translated_research_query)!r}."
        )
        return {
            "topic": translated_topic,
            "user_prompt": translated_user_prompt,
            "language": output_language,
            "workflow_language": workflow_language,
            "output_language": output_language,
            "chat_history": translated_chat_history,
            "attachment_context": translated_attachment_context,
            "source_manuscript": translated_source_manuscript,
            "manager_guidance": guidance,
            "research_query": str(guidance.get("research_query") or translated_research_query or state.get("research_query", "") or state.get("topic", "")),
        }

    def node_planner(state: State) -> Dict[str, Any]:
        check_stop()
        set_node_status(statuses, "Planner", "Processing")
        refresh_cards()
        add_log("Planner started: generating IMRaD outline.")
        guidance = state.get("manager_guidance", {}) or {}
        planning_topic = str(guidance.get("topic_focus") or state.get("topic", "")).strip() or state.get("topic", "")
        effective_target_words = resolved_target_word_count(state)
        effective_reference_target = resolved_reference_target(state)

        plan = llm_complete_text(
            cfg=cfg_for("Planner"),
            prompt=planner_prompt(
                planning_topic,
                state.get("workflow_language", state.get("language", "English")),
                effective_target_words,
                effective_reference_target,
                user_prompt=state.get("user_prompt", ""),
                chat_history=chat_history_block(state.get("chat_history", [])),
                attachment_context=state.get("attachment_context", ""),
                source_manuscript=state.get("source_manuscript", ""),
                manager_guidance=guidance,
            ),
            should_stop=check_stop,
        )

        set_node_status(statuses, "Planner", "Done")
        refresh_cards()
        if hooks and hooks.on_outline:
            hooks.on_outline(plan)
        add_log("Planner done: outline generated.")
        return {"plan": plan}

    def node_researcher(state: State) -> Dict[str, Any]:
        check_stop()
        set_node_status(statuses, "Researcher", "Processing")
        refresh_cards()
        guidance = state.get("manager_guidance", {}) or {}
        search_filters_state = state.get("search_filters") if isinstance(state.get("search_filters"), dict) else {}
        database_filters = search_filters_state.get("databases") if isinstance(search_filters_state.get("databases"), dict) else {}
        allow_scopus = bool(database_filters.get("scopus", True))
        allow_core = bool(database_filters.get("core", True))
        allow_openalex = bool(database_filters.get("openalex", True))
        allow_arxiv = bool(database_filters.get("arxiv", True))
        if not any([allow_scopus, allow_core, allow_openalex, allow_arxiv]):
            allow_openalex = True
        deep_review = bool(search_filters_state.get("deep_review") or search_filters_state.get("deepReview"))
        year_min = clamp_int(search_filters_state.get("publish_year_min"), 1900, 2100, 0) if search_filters_state.get("publish_year_min") else 0
        year_max = clamp_int(search_filters_state.get("publish_year_max"), 1900, 2100, 0) if search_filters_state.get("publish_year_max") else 0
        use_online_search = bool(guidance.get("use_online_search", True))
        topic_value = state.get("research_query", "") or state.get("topic", "")
        attachment_only_fallback = bool(state.get("source_manuscript", "").strip() or state.get("attachment_context", "").strip())
        effective_reference_target = resolved_reference_target(state)
        if not use_online_search:
            add_log("Researcher skipped online search: manager determined the task should rely mainly on attached offline materials.")
            set_node_status(statuses, "Researcher", "Done")
            refresh_cards()
            if hooks and hooks.on_metadata:
                hooks.on_metadata([])
            return {"metadata_list": []}

        add_log("Researcher started: searching Scopus, CORE, OpenAlex, and arXiv in parallel.")
        search_pool_per_source = max(15, min(effective_reference_target * 3, 60))
        if deep_review:
            search_pool_per_source = max(search_pool_per_source, 45)
            add_log("Researcher: deep review filter enabled; prioritizing broader retrieval and fuller text coverage.")
        source_results: List[List[PaperMeta]] = []
        errors: List[str] = []
        scopus_limit = SCOPUS_MAX_SERVICE_COUNT
        core_limit = max(CORE_MAX_RESULTS, search_pool_per_source)
        openalex_limit = max(OPENALEX_MAX_RESULTS, search_pool_per_source)
        arxiv_limit = max(ARXIV_MAX_RESULTS, search_pool_per_source)
        use_cloud_resources = use_remote_resource_api()
        if use_cloud_resources:
            add_log("Researcher: cloud resource proxy enabled for literature APIs.")

        future_map: Dict[Any, Tuple[str, int]] = {}
        executor = ThreadPoolExecutor(max_workers=4)
        try:
            if allow_scopus and (SCOPUS_API_KEY.strip() or use_cloud_resources):
                add_log(f"Scopus: queued search (limit={scopus_limit}).")
                if use_cloud_resources:
                    future = executor.submit(
                        remote_resource_search,
                        source="scopus",
                        topic=topic_value,
                        max_results=scopus_limit,
                    )
                else:
                    future = executor.submit(
                        scopus_search,
                        api_key=SCOPUS_API_KEY,
                        topic=topic_value,
                        max_results=scopus_limit,
                    )
                future_map[future] = ("Scopus", scopus_limit)
            elif not allow_scopus:
                add_log("Scopus: skipped by literature search filters.")
            else:
                add_log("Scopus: skipped - API key not configured.")

            if allow_core and (CORE_API_KEY.strip() or use_cloud_resources):
                add_log(f"CORE: queued search (limit={core_limit}).")
                if use_cloud_resources:
                    future = executor.submit(
                        remote_resource_search,
                        source="core",
                        topic=topic_value,
                        max_results=core_limit,
                    )
                else:
                    future = executor.submit(
                        core_search,
                        api_key=CORE_API_KEY,
                        topic=topic_value,
                        max_results=core_limit,
                    )
                future_map[future] = ("CORE", core_limit)
            elif not allow_core:
                add_log("CORE: skipped by literature search filters.")
            else:
                add_log("CORE: skipped - API key not configured.")

            if allow_openalex:
                add_log(f"OpenAlex: queued search (limit={openalex_limit}).")
                if use_cloud_resources:
                    future = executor.submit(
                        remote_resource_search,
                        source="openalex",
                        topic=topic_value,
                        max_results=openalex_limit,
                    )
                else:
                    future = executor.submit(
                        openalex_search,
                        topic=topic_value,
                        max_results=openalex_limit,
                    )
                future_map[future] = ("OpenAlex", openalex_limit)
            else:
                add_log("OpenAlex: skipped by literature search filters.")

            if allow_arxiv:
                add_log(f"arXiv: queued search (limit={arxiv_limit}).")
                if use_cloud_resources:
                    future = executor.submit(
                        remote_resource_search,
                        source="arxiv",
                        topic=topic_value,
                        max_results=arxiv_limit,
                    )
                else:
                    future = executor.submit(
                        arxiv_search,
                        topic=topic_value,
                        max_results=arxiv_limit,
                    )
                future_map[future] = ("arXiv", arxiv_limit)
            else:
                add_log("arXiv: skipped by literature search filters.")

            pending = set(future_map.keys())
            while pending:
                check_stop()
                done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                if not done:
                    continue
                for future in done:
                    check_stop()
                    source_name, source_limit = future_map[future]
                    try:
                        result = future.result()
                        source_results.append(result)
                        add_log(f"{source_name}: fetched {len(result)} candidate papers (limit={source_limit}).")
                    except Exception as e:
                        errors.append(f"{source_name} error: {e}")
                        add_log(f"{source_name}: error while searching - {e}")
        except Exception:
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=False, cancel_futures=False)

        metadata_list = merge_metadata_lists(*source_results)
        check_stop()
        metadata_list = [enrich_paper_metadata(item) for item in metadata_list]
        if year_min or year_max:
            before_year = len(metadata_list)
            metadata_list = [
                item
                for item in metadata_list
                if (
                    clamp_int(item.get("year"), 0, 2100, 0) >= (year_min or 0)
                    and (not year_max or clamp_int(item.get("year"), 0, 2100, 0) <= year_max)
                )
            ]
            add_log(
                f"Researcher: {before_year - len(metadata_list)} papers removed by publish-year filter ({year_min or 'any'}-{year_max or 'any'})."
            )
        before_filter = len(metadata_list)
        metadata_list = [item for item in metadata_list if is_metadata_usable(item)]
        dropped_count = before_filter - len(metadata_list)
        metadata_list = rank_metadata_list(
            topic=topic_value,
            metadata_list=metadata_list,
            llm_cfg=cfg_for("Researcher"),
            top_k=max(effective_reference_target, TOP_SEARCH_RESULTS),
            should_stop=check_stop,
        )
        if not metadata_list and errors and not attachment_only_fallback:
            raise RuntimeError(" ; ".join(errors))
        if not metadata_list and attachment_only_fallback:
            add_log("Researcher: online search returned no usable papers. Proceeding with attached offline materials.")

        set_node_status(statuses, "Researcher", "Done")
        refresh_cards()
        if hooks and hooks.on_metadata:
            hooks.on_metadata(metadata_list)
        add_log(
            f"Researcher done: {len(metadata_list)} ranked papers selected, {dropped_count} dropped for missing author/year/reference metadata. "
            f"Reference target={effective_reference_target}, search pool per source={search_pool_per_source}."
        )
        return {"metadata_list": metadata_list}

    def node_reader(state: State) -> Dict[str, Any]:
        check_stop()
        set_node_status(statuses, "Reader", "Processing")
        refresh_cards()
        add_log("Reader started: collecting full text and extracting quotations.")

        metadata_list = state.get("metadata_list", [])
        full_texts: List[PaperText] = []
        quotations: List[Quote] = []
        unpaywall_attempts = 0
        unpaywall_hits = 0

        for m in metadata_list:
            check_stop()
            doi = (m.get("doi") or "").strip()
            text_source: Literal["pdf", "core", "unpaywall", "abstract", "none"] = "none"
            extracted = ""
            core_full_text = (m.get("full_text") or "").strip()
            core_pdf_url = (m.get("pdf_url") or "").strip()
            citation = citation_from_meta(m.get("authors", ""), int(m.get("year") or 0))

            if not citation:
                continue

            if core_full_text:
                extracted = core_full_text
                text_source = "core"

            if not extracted and core_pdf_url:
                try:
                    pdf_bytes = download_pdf_bytes(url=core_pdf_url)
                    extracted = extract_text_from_pdf(pdf_bytes)
                    text_source = "pdf"
                except Exception:
                    extracted = ""

            if not extracted and doi and (UNPAYWALL_EMAIL.strip() or use_remote_resource_api()):
                unpaywall_attempts += 1
                try:
                    if use_remote_resource_api():
                        upw = remote_unpaywall_lookup(doi=doi)
                    else:
                        upw = unpaywall_lookup(doi=doi, email=UNPAYWALL_EMAIL)
                    pdf_url = upw.get("best_oa_location", {}).get("url_for_pdf") or upw.get("url_for_pdf")
                    if pdf_url:
                        pdf_bytes = download_pdf_bytes(url=pdf_url)
                        extracted = extract_text_from_pdf(pdf_bytes)
                        text_source = "unpaywall"
                        unpaywall_hits += 1
                except Exception:
                    extracted = ""

            if not extracted:
                extracted = (m.get("abstract") or "").strip()
                if extracted:
                    text_source = "abstract"

            full_texts.append(
                {
                    "doi": doi,
                    "title": m.get("title", ""),
                    "year": int(m.get("year") or 0),
                    "authors": m.get("authors", ""),
                    "source": text_source,
                    "text": extracted,
                }
            )

            if extracted:
                paper_quotes: List[Quote] = []
                try:
                    check_stop()
                    raw = llm_complete_text(
                        cfg=cfg_for("Reader"),
                        prompt=quote_extraction_prompt(
                            extracted,
                            m.get("authors", ""),
                            int(m.get("year") or 0),
                            doi,
                            m.get("title", ""),
                        ),
                        should_stop=check_stop,
                    )
                    items = json.loads(raw)
                    if isinstance(items, list):
                        for it in items:
                            check_stop()
                            q = str(it.get("quote", "")).strip()
                            if q and quote_is_verified(q, extracted):
                                paper_quotes.append(
                                    {
                                        "doi": doi,
                                        "title": m.get("title", ""),
                                        "authors": m.get("authors", ""),
                                        "year": int(m.get("year") or 0),
                                        "citation": citation,
                                        "quote": q,
                                    }
                                )
                except Exception:
                    check_stop()
                    pass

                if not paper_quotes:
                    for q in heuristic_extract_quotes(extracted):
                        check_stop()
                        paper_quotes.append(
                            {
                                "doi": doi,
                                "title": m.get("title", ""),
                                "authors": m.get("authors", ""),
                                "year": int(m.get("year") or 0),
                                "citation": citation,
                                "quote": q,
                            }
                        )

                quotations.extend(paper_quotes)

        set_node_status(statuses, "Reader", "Done")
        refresh_cards()
        if hooks and hooks.on_reader_data:
            hooks.on_reader_data(full_texts, quotations)
        source_counts: Dict[str, int] = {}
        for item in full_texts:
            source_name = item.get("source", "none")
            source_counts[source_name] = source_counts.get(source_name, 0) + 1
        add_log(
            f"Reader done: {len(full_texts)} texts prepared, {len(quotations)} verified quotes. "
            f"Sources: {source_counts}. Unpaywall attempts={unpaywall_attempts}, hits={unpaywall_hits}."
        )
        return {"full_texts": full_texts, "quotations": quotations}

    def node_writer(state: State) -> Dict[str, Any]:
        check_stop()
        set_node_status(statuses, "Writer", "Processing")
        refresh_cards()
        effective_target_words = resolved_target_word_count(state)
        effective_reference_target = resolved_reference_target(state)
        body_target_words = max(900, effective_target_words - estimated_reference_words(effective_reference_target))
        revision_mode = prompt_requests_revision(state.get("user_prompt", "")) and bool(state.get("source_manuscript", "").strip())
        guidance = state.get("manager_guidance", {}) or {}
        add_log(
            f"Writer started: {'revising uploaded manuscript' if revision_mode else 'drafting IMRaD sections'} with {len(state.get('quotations', []))} quotations, "
            f"body target about {body_target_words} words within total target {effective_target_words}."
        )

        section_order = ["Introduction", "Methods", "Results", "Discussion"]
        quote_pool = state.get("quotations", [])[: min(max(effective_reference_target * 3, 24), 80)]

        if revision_mode:
            draft_parts: List[str] = []
            for delta in llm_stream_text(
                cfg=cfg_for("Writer"),
                prompt=revision_writer_prompt(
                    language=state.get("workflow_language", state.get("language", "English")),
                    user_prompt=state.get("user_prompt", ""),
                    plan=state.get("plan", ""),
                    source_manuscript=state.get("source_manuscript", ""),
                    attachment_context=state.get("attachment_context", ""),
                    quotes=quote_pool,
                    total_target_words=body_target_words,
                    manager_brief=str(guidance.get("writer_brief", "") or ""),
                ),
                should_stop=check_stop,
            ):
                check_stop()
                draft_parts.append(delta)
                if hooks and hooks.on_draft:
                    hooks.on_draft("".join(draft_parts))
            final_draft = "".join(draft_parts).strip()
        else:
            sections: List[str] = []
            cumulative = ""
            for index, section_name in enumerate(section_order):
                check_stop()
                current_total_words = count_words(cumulative)
                remaining_sections = section_order[index:]
                section_target = dynamic_section_word_target(
                    total_target_words=body_target_words,
                    current_word_count=current_total_words,
                    section_name=section_name,
                    remaining_sections=remaining_sections,
                )
                add_log(
                    f"Writer: generating {section_name} (~{section_target} words). "
                    f"Current body words before section={current_total_words}, body target={body_target_words}."
                )
                section_prompt = constrained_writer_prompt(
                    language=state.get("workflow_language", state.get("language", "English")),
                    plan=state.get("plan", ""),
                    section_name=section_name,
                    section_target_words=section_target,
                    current_total_words=current_total_words,
                    total_target_words=body_target_words,
                    existing_draft=cumulative,
                    quotes=quote_pool,
                    manager_brief=str(guidance.get("writer_brief", "") or ""),
                )
                section_buf: List[str] = []
                for delta in llm_stream_text(cfg=cfg_for("Writer"), prompt=section_prompt, should_stop=check_stop):
                    check_stop()
                    section_buf.append(delta)
                    preview = cumulative + "".join(section_buf)
                    if hooks and hooks.on_draft:
                        hooks.on_draft(preview)

                section_text = "".join(section_buf).strip()
                sections.append(section_text)
                cumulative = "\n\n".join([part for part in sections if part]).strip()

            final_draft = cumulative

        set_node_status(statuses, "Writer", "Done")
        refresh_cards()
        add_log("Writer done: draft generated.")
        return {"final_draft": final_draft}

    initial_state: State = {
        "topic": topic,
        "user_prompt": user_prompt or topic,
        "language": language,
        "workflow_language": language,
        "output_language": language,
        "chat_history": chat_history or [],
        "search_filters": search_filters or {},
        "attachment_context": attachment_context,
        "source_manuscript": source_manuscript,
        "research_query": research_query or topic,
        "manager_guidance": {},
    }
    add_log(f"Workflow queued with provider={llm_cfg.provider}, model={llm_cfg.model}.")
    state: State = dict(initial_state)
    state.update(node_manager(state))

    guidance = state.get("manager_guidance", {}) or {}
    node_sequence = guidance.get("node_sequence") if isinstance(guidance.get("node_sequence"), list) else []
    ordered_sequence = [str(node) for node in node_sequence if str(node) in {"Planner", "Researcher", "Reader", "Writer"}]
    if not ordered_sequence:
        ordered_sequence = ["Planner", "Researcher", "Reader", "Writer"]

    add_log(f"Manager route selected: {' -> '.join(ordered_sequence)}.")

    runnable_nodes = {
        "Planner": node_planner,
        "Researcher": node_researcher,
        "Reader": node_reader,
        "Writer": node_writer,
    }

    for node_name in ["Planner", "Researcher", "Reader", "Writer"]:
        if node_name not in ordered_sequence and statuses.get(node_name) == "Pending":
            statuses[node_name] = "Done"
            refresh_cards()
            add_log(f"{node_name} skipped by Manager for this task.")

    for node_name in ordered_sequence:
        check_stop()
        result = runnable_nodes[node_name](state)
        if isinstance(result, dict):
            state.update(result)

    return state, logs


def count_words(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def estimated_reference_words(reference_target: int) -> int:
    return max(180, reference_target * 18)

