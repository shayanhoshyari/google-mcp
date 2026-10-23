"""Google Drive operations: read everything, write only files the app created."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

from google_mcp import auth, local_files
from google_mcp.google_api import GoogleAPIError, download, request, resumable_upload
from google_mcp.utils import bounded_int, compact, require_text

BASE_URL = "https://www.googleapis.com/drive/v3"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3"

NATIVE_PREFIX = "application/vnd.google-apps."
FOLDER_MIME = NATIVE_PREFIX + "folder"
SHORTCUT_MIME = NATIVE_PREFIX + "shortcut"
DOC_MIME = NATIVE_PREFIX + "document"
SHEET_MIME = NATIVE_PREFIX + "spreadsheet"

MARKER_KEY = "google_mcp"
CREATED_MARKER = "created"
DEFAULT_FOLDER_MARKER = "default_folder"
DEFAULT_FOLDER_NAME = "mcp_default"
DEFAULT_FOLDER_QUERY = (
    f"appProperties has {{ key='{MARKER_KEY}' and value='{DEFAULT_FOLDER_MARKER}' }} and trashed = false"
)

MAX_UPLOAD_BYTES = 100 * 1024 * 1024

FILE_FIELDS = (
    "id,name,mimeType,modifiedTime,size,owners(displayName,emailAddress),parents,webViewLink,appProperties,"
    "shortcutDetails(targetId,targetMimeType),capabilities(canDownload)"
)

# Drive MIME type -> ordered (export MIME type, extension) candidates.
EXPORTS: dict[str, tuple[tuple[str, str], ...]] = {
    DOC_MIME: (("text/markdown", ".md"), ("text/plain", ".txt")),
    SHEET_MIME: (("text/csv", ".csv"),),
    NATIVE_PREFIX + "presentation": (("text/plain", ".txt"),),
    NATIVE_PREFIX + "drawing": (("image/png", ".png"),),
}

# Local extension -> (upload content type, Google-native target type).
CONVERSIONS: dict[str, tuple[str, str]] = {
    ".md": ("text/markdown", DOC_MIME),
    ".txt": ("text/plain", DOC_MIME),
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", DOC_MIME),
    ".csv": ("text/csv", SHEET_MIME),
    ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", SHEET_MIME),
}

SCOPE_REASONS = {"ACCESS_TOKEN_SCOPE_INSUFFICIENT", "insufficientPermissions"}
RATE_LIMIT_REASONS = {"userRateLimitExceeded", "rateLimitExceeded", "dailyLimitExceeded", "sharingRateLimitExceeded"}

NOT_AUTHORIZED = "Drive access is not authorized yet; run `google-mcp auth`."
APP_ONLY = "The app can only modify files it created (`created_by_app`)."
EXPORT_TOO_LARGE = "Export is over Google's 10 MB limit."
NO_DOWNLOAD = "The owner disabled downloading for this file."
NOT_FOUND = "File not found or not visible to this account."
NATIVE_UPDATE_HINT = (
    "Google may not allow replacing the contents of a converted Google Doc/Sheet; "
    "use upload_file to create a new file instead."
)


def _friendly(exc: GoogleAPIError, *, write: bool) -> str | None:
    """Return an actionable message for known Drive errors, or None to keep the original."""
    reasons = set(exc.reasons)
    if exc.status == 403 and (reasons & SCOPE_REASONS or "insufficient authentication scopes" in exc.detail.lower()):
        text = NOT_AUTHORIZED
    elif exc.status == 403 and "exportSizeLimitExceeded" in reasons:
        text = EXPORT_TOO_LARGE
    elif exc.status == 403 and "cannotDownloadFile" in reasons:
        text = NO_DOWNLOAD
    elif exc.status == 403 and ("appNotAuthorizedToFile" in reasons or (write and not reasons & RATE_LIMIT_REASONS)):
        text = APP_ONLY
    elif exc.status == 404:
        text = f"{NOT_FOUND} Writes only work on files this app created." if write else NOT_FOUND
    else:
        return None
    reason = f", {exc.reasons[0]}" if exc.reasons else ""
    return f"{text} (HTTP {exc.status}{reason})"


@contextmanager
def _mapped(*, write: bool = False) -> Iterator[None]:
    """Translate Google API errors into Drive-specific messages."""
    try:
        yield
    except GoogleAPIError as exc:
        message = _friendly(exc, write=write)
        if message is None:
            raise
        raise GoogleAPIError(message, status=exc.status, reasons=exc.reasons, detail=exc.detail) from exc


def _file_url(file_id: str) -> str:
    return f"{BASE_URL}/files/{quote(file_id, safe='')}"


def _file(value: dict[str, Any]) -> dict[str, Any]:
    raw_owners = value.get("owners")
    owners = [
        compact({"display_name": owner.get("displayName"), "email": owner.get("emailAddress")})
        for owner in (cast(list[Any], raw_owners) if isinstance(raw_owners, list) else [])
        if isinstance(owner, dict)
    ]
    properties = value.get("appProperties")
    shortcut = value.get("shortcutDetails")
    try:
        size = int(value["size"]) if "size" in value else None
    except (TypeError, ValueError):
        size = None
    return compact(
        {
            "id": value.get("id"),
            "name": value.get("name"),
            "mime_type": value.get("mimeType"),
            "modified_time": value.get("modifiedTime"),
            "size": size,
            "owners": owners,
            "parents": value.get("parents", []),
            "web_link": value.get("webViewLink"),
            "created_by_app": isinstance(properties, dict) and MARKER_KEY in properties,
            "shortcut_target_id": shortcut.get("targetId") if isinstance(shortcut, dict) else None,
        }
    )


def _metadata(file_id: str) -> dict[str, Any]:
    with _mapped():
        return request("GET", _file_url(file_id), params={"fields": FILE_FIELDS})


def search_files(
    query: str | None = None, max_results: int | None = None, page_token: str | None = None
) -> dict[str, Any]:
    """Search Drive (My Drive and Shared with me) with Drive query syntax, excluding trashed files."""
    limit = bounded_int(max_results, default=25, maximum=100)
    q = f"({query.strip()}) and trashed = false" if query and query.strip() else "trashed = false"
    params: dict[str, Any] = {
        "q": q,
        "corpora": "user",
        "pageSize": limit,
        "fields": f"nextPageToken,files({FILE_FIELDS})",
    }
    if page_token:
        params["pageToken"] = page_token
    with _mapped():
        page = request("GET", f"{BASE_URL}/files", params=params)
    files = page.get("files", [])
    return compact(
        {
            "files": [_file(item) for item in files if isinstance(item, dict)] if isinstance(files, list) else [],
            "next_page_token": page.get("nextPageToken"),
            "query": q,
        }
    )


def get_file(file_id: str) -> dict[str, Any]:
    """Return normalized metadata for one file."""
    return _file(_metadata(require_text(file_id, "file_id")))


def _with_extension(name: str, extension: str) -> str:
    return name if name.lower().endswith(extension) else name + extension


def _fetch(target: Path, url: str, params: dict[str, Any]) -> int:
    """Stream a download into place atomically."""
    return local_files.atomic_write(target, lambda handle: download(url, handle, params=params))


def _export_unsupported(exc: GoogleAPIError) -> bool:
    return exc.status == 400 or "invalidExportFormat" in exc.reasons


def download_file(file_id: str, path: str | None = None, overwrite: bool = False) -> dict[str, Any]:
    """Save a Drive file (exporting Google-native types) to local disk."""
    file_id = require_text(file_id, "file_id")
    if not isinstance(overwrite, bool):
        raise ValueError("overwrite must be a boolean")
    meta = _metadata(file_id)
    mime_type = str(meta.get("mimeType") or "")
    display = str(meta.get("name") or file_id)
    if mime_type == SHORTCUT_MIME:
        details = meta.get("shortcutDetails")
        target_id = details.get("targetId") if isinstance(details, dict) else None
        raise ValueError(
            f"'{display}' is a shortcut; download its target instead (file_id {target_id}). "
            "Look it up with get_file if needed."
        )
    exports = EXPORTS.get(mime_type)
    if exports is None and mime_type.startswith(NATIVE_PREFIX):
        kind = mime_type.removeprefix(NATIVE_PREFIX)
        raise ValueError(f"'{display}' is a Google {kind} and cannot be downloaded")
    capabilities = meta.get("capabilities")
    if isinstance(capabilities, dict) and capabilities.get("canDownload") is False:
        raise ValueError(f"{NO_DOWNLOAD} (canDownload=false)")
    name = local_files.safe_name(meta.get("name"), file_id)

    if exports is None:
        target = local_files.target(path, local_files.cache_dir(file_id), name, overwrite)
        with _mapped():
            size = _fetch(target, _file_url(file_id), {"alt": "media"})
        return {"path": str(target), "size": size, "mime_type": mime_type, "exported": False}

    for index, (export_mime, extension) in enumerate(exports):
        target = local_files.target(path, local_files.cache_dir(file_id), _with_extension(name, extension), overwrite)
        try:
            with _mapped():
                size = _fetch(target, f"{_file_url(file_id)}/export", {"mimeType": export_mime})
        except GoogleAPIError as exc:
            if index + 1 < len(exports) and _export_unsupported(exc):
                continue
            raise
        return {"path": str(target), "size": size, "mime_type": export_mime, "exported": True}
    raise GoogleAPIError("Google did not offer a supported export format")  # unreachable: the last format raises


def _strip_extension(name: str) -> str:
    stem, extension = os.path.splitext(name)
    return stem if stem and extension.lower() in CONVERSIONS else name


def _default_folder_path() -> Path:
    return auth.config_dir() / "drive.json"


def _stored_default_folder() -> str | None:
    try:
        value = json.loads(_default_folder_path().read_text())
    except (OSError, ValueError):
        return None
    folder_id = value.get("default_folder_id") if isinstance(value, dict) else None
    return folder_id if isinstance(folder_id, str) and folder_id else None


def _folder_usable(folder_id: str) -> bool:
    try:
        value = request("GET", _file_url(folder_id), params={"fields": "id,trashed"})
    except GoogleAPIError as exc:
        if exc.status == 404:
            return False
        raise
    return not value.get("trashed")


def _default_folder() -> str:
    """Return the ID of the app-created mcp_default folder, finding or creating it as needed."""
    stored = _stored_default_folder()
    if stored and _folder_usable(stored):
        return stored
    found = request(
        "GET",
        f"{BASE_URL}/files",
        params={"q": DEFAULT_FOLDER_QUERY, "corpora": "user", "pageSize": 1, "fields": "files(id)"},
    )
    files = found.get("files")
    folder_id = files[0].get("id") if isinstance(files, list) and files and isinstance(files[0], dict) else None
    if not isinstance(folder_id, str) or not folder_id:
        created = request(
            "POST",
            f"{BASE_URL}/files",
            params={"fields": "id"},
            json_body={
                "name": DEFAULT_FOLDER_NAME,
                "mimeType": FOLDER_MIME,
                "parents": ["root"],
                "appProperties": {MARKER_KEY: DEFAULT_FOLDER_MARKER},
            },
        )
        folder_id = created.get("id")
        if not isinstance(folder_id, str) or not folder_id:
            raise GoogleAPIError("Google API did not return the new folder's ID")
    if folder_id != stored:
        auth.write_private_json(_default_folder_path(), {"default_folder_id": folder_id})
    return folder_id


def upload_file(
    path: str, folder_id: str | None = None, name: str | None = None, convert: bool = False
) -> dict[str, Any]:
    """Create a new Drive file from a local file, by default in the app-created mcp_default folder."""
    if not isinstance(convert, bool):
        raise ValueError("convert must be a boolean")
    source = local_files.local_file(path, MAX_UPLOAD_BYTES)
    content_type = local_files.content_type(source)
    target_type: str | None = None
    if convert:
        conversion = CONVERSIONS.get(source.suffix.lower())
        if conversion is None:
            raise ValueError(f"convert=true supports only these extensions: {', '.join(sorted(CONVERSIONS))}")
        content_type, target_type = conversion
    drive_name = require_text(name, "name") if name is not None else source.name
    if convert:
        drive_name = _strip_extension(drive_name)
    parent = require_text(folder_id, "folder_id") if folder_id is not None else None
    with _mapped(write=True):
        metadata: dict[str, Any] = {
            "name": drive_name,
            "parents": [parent or _default_folder()],
            "appProperties": {MARKER_KEY: CREATED_MARKER},
        }
        if target_type:
            metadata["mimeType"] = target_type
        created = resumable_upload(
            "POST",
            f"{UPLOAD_URL}/files",
            source,
            params={"uploadType": "resumable", "fields": FILE_FIELDS},
            metadata=metadata,
            content_type=content_type,
        )
    return _file(created)


def update_file(file_id: str, path: str) -> dict[str, Any]:
    """Replace the contents of a file the app created, keeping its ID, link, and revision history."""
    file_id = require_text(file_id, "file_id")
    source = local_files.local_file(path, MAX_UPLOAD_BYTES)
    current = _metadata(file_id)
    if not _file(current).get("created_by_app"):
        raise ValueError(f"{APP_ONLY} '{current.get('name') or file_id}' was not created by this app.")
    native = str(current.get("mimeType") or "").startswith(NATIVE_PREFIX)
    try:
        with _mapped(write=True):
            updated = resumable_upload(
                "PATCH",
                f"{UPLOAD_URL}/files/{quote(file_id, safe='')}",
                source,
                params={"uploadType": "resumable", "fields": FILE_FIELDS},
                metadata={},
                content_type=local_files.content_type(source),
            )
    except GoogleAPIError as exc:
        if native and "google-mcp auth" not in str(exc):
            raise GoogleAPIError(
                f"{exc} {NATIVE_UPDATE_HINT}", status=exc.status, reasons=exc.reasons, detail=exc.detail
            ) from exc
        raise
    return _file(updated)
