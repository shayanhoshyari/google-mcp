"""Gmail operations: read messages, threads, and attachments; create and edit drafts (never send)."""

from __future__ import annotations

import base64
import mimetypes
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from email import message_from_bytes, policy
from email.message import EmailMessage, Message
from email.utils import getaddresses
from html import unescape
from html.parser import HTMLParser
from typing import Any, cast, override
from urllib.parse import quote

from google_mcp import local_files
from google_mcp.auth import account_email
from google_mcp.google_api import GoogleAPIError, multipart_upload, request
from google_mcp.utils import bounded_int, compact, require_text

BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
UPLOAD_URL = "https://gmail.googleapis.com/upload/gmail/v1/users/me"
BODY_LIMIT = 60_000
MAX_DRAFT_BYTES = 25 * 1024 * 1024  # Gmail's sending limit

SCOPE_REASONS = {"ACCESS_TOKEN_SCOPE_INSUFFICIENT", "insufficientPermissions"}
DRAFTS_NOT_AUTHORIZED = "Gmail draft access is not authorized yet; run `google-mcp auth`."
DRAFT_NOT_FOUND = "Draft not found."


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.fragments: list[str] = []
        self.ignored = 0

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "head"}:
            self.ignored += 1
        elif not self.ignored and tag in {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.fragments.append("\n")

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "head"} and self.ignored:
            self.ignored -= 1
        elif not self.ignored and tag in {"p", "div", "li", "tr"}:
            self.fragments.append("\n")

    @override
    def handle_data(self, data: str) -> None:
        if not self.ignored:
            self.fragments.append(data)

    def text(self) -> str:
        raw = unescape("".join(self.fragments)).replace("\r", "")
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r" *\n *", "\n", raw)
        return re.sub(r"\n{3,}", "\n\n", raw).strip()


def _decode_bytes(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _decode(data: str | None) -> str:
    if not data:
        return ""
    try:
        return _decode_bytes(data).decode("utf-8", errors="replace")
    except (ValueError, UnicodeError):
        return ""


def _headers(payload: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    values = payload.get("headers", [])
    if not isinstance(values, list):
        return result
    for header in values:
        if isinstance(header, dict) and isinstance(header.get("name"), str) and isinstance(header.get("value"), str):
            result[header["name"].lower()] = header["value"]
    return result


def _parts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten MIME parts, treating an attached message (message/rfc822) as one opaque part."""
    flattened = [payload]
    for child in payload.get("parts", []) if isinstance(payload.get("parts"), list) else []:
        if isinstance(child, dict):
            flattened.extend([child] if child.get("mimeType") == "message/rfc822" else _parts(child))
    return flattened


def _body(payload: dict[str, Any]) -> tuple[str, bool]:
    plain: list[str] = []
    html: list[str] = []
    for part in _parts(payload):
        disposition = str(_headers(part).get("content-disposition", "")).lower()
        if "attachment" in disposition or part.get("filename"):
            continue
        body = part.get("body")
        data = body.get("data") if isinstance(body, dict) else None
        decoded = _decode(data if isinstance(data, str) else None)
        mime_type = part.get("mimeType")
        if decoded and mime_type == "text/plain":
            plain.append(decoded)
        elif decoded and mime_type == "text/html":
            html.append(decoded)
    text = "\n\n".join(value.strip() for value in plain if value.strip())
    if not text and html:
        parser = _HTMLText()
        parser.feed("\n".join(html))
        text = parser.text()
    truncated = len(text) > BODY_LIMIT
    return text[:BODY_LIMIT], truncated


def _is_attachment(part: dict[str, Any]) -> bool:
    """A leaf part with a filename or attachment disposition, or a non-text leaf stored as data (cid images)."""
    mime_type = str(part.get("mimeType") or "")
    if mime_type == "message/rfc822":
        return True
    if mime_type.startswith("multipart/"):
        return False
    headers = _headers(part)
    if part.get("filename") or "attachment" in str(headers.get("content-disposition", "")).lower():
        return True
    body = part.get("body")
    stored = isinstance(body, dict) and "attachmentId" in body
    return mime_type not in {"text/plain", "text/html"} and ("content-id" in headers or stored)


def _attachment_parts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the MIME parts that are attachments, inline images, or attached messages."""
    return [part for part in _parts(payload) if part is not payload and _is_attachment(part)]


def _filename(part: dict[str, Any]) -> str:
    """The part's filename, or a stable fallback for unnamed parts such as cid images."""
    name = part.get("filename")
    if isinstance(name, str) and name:
        return name
    extension = mimetypes.guess_extension(str(part.get("mimeType") or "")) or ""
    return f"attachment-{part.get('partId') or 'part'}{extension}"


def _attachment(part: dict[str, Any]) -> dict[str, Any]:
    headers = _headers(part)
    body = part.get("body")
    disposition = str(headers.get("content-disposition", "")).lower()
    inline = disposition.startswith("inline") or ("content-id" in headers and "attachment" not in disposition)
    return compact(
        {
            "filename": _filename(part),
            "mime_type": part.get("mimeType"),
            "size": body.get("size") if isinstance(body, dict) else None,
            "part_id": part.get("partId"),
            "inline": True if inline else None,
        }
    )


def _internal_date(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, UTC).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OSError):
        return None


def _normalize_metadata(message: dict[str, Any]) -> dict[str, Any]:
    raw_payload = message.get("payload")
    payload = cast(dict[str, Any], raw_payload) if isinstance(raw_payload, dict) else {}
    headers = _headers(payload)
    return compact(
        {
            "id": message.get("id"),
            "thread_id": message.get("threadId"),
            "from": headers.get("from"),
            "to": headers.get("to"),
            "subject": headers.get("subject"),
            "date": headers.get("date"),
            "internal_date": _internal_date(message.get("internalDate")),
            "snippet": message.get("snippet"),
            "label_ids": message.get("labelIds", []),
        }
    )


def _normalize_full(message: dict[str, Any]) -> dict[str, Any]:
    result = _normalize_metadata(message)
    raw_payload = message.get("payload")
    payload = cast(dict[str, Any], raw_payload) if isinstance(raw_payload, dict) else {}
    headers = _headers(payload)
    body, truncated = _body(payload)
    result.update(
        compact(
            {
                "cc": headers.get("cc"),
                "reply_to": headers.get("reply-to"),
                "body": body,
                "body_truncated": truncated,
                "attachments": [_attachment(part) for part in _attachment_parts(payload)] or None,
            }
        )
    )
    return result


def list_emails(
    query: str | None = None, max_results: int | None = None, page_token: str | None = None
) -> dict[str, Any]:
    """List inbox messages or search with Gmail query syntax."""
    limit = bounded_int(max_results, default=10)
    params: dict[str, Any] = {"maxResults": limit, "q": query.strip() if query and query.strip() else "in:inbox"}
    if page_token:
        params["pageToken"] = page_token
    page = request("GET", f"{BASE_URL}/messages", params=params)
    messages = page.get("messages", [])
    results: list[dict[str, Any]] = []
    if isinstance(messages, list):
        for item in messages:
            message_id = item.get("id") if isinstance(item, dict) else None
            if not isinstance(message_id, str):
                continue
            value = request(
                "GET",
                f"{BASE_URL}/messages/{quote(message_id, safe='')}",
                params={"format": "metadata", "metadataHeaders": ["From", "To", "Subject", "Date"]},
            )
            results.append(_normalize_metadata(value))
    return compact(
        {
            "messages": results,
            "next_page_token": page.get("nextPageToken"),
            "result_size_estimate": page.get("resultSizeEstimate"),
            "query": params["q"],
        }
    )


def read_email(message_id: str) -> dict[str, Any]:
    """Read one Gmail message including its decoded textual body."""
    message_id = require_text(message_id, "message_id")
    value = request("GET", f"{BASE_URL}/messages/{quote(message_id, safe='')}", params={"format": "full"})
    return _normalize_full(value)


def read_thread(thread_id: str, max_messages: int | None = None) -> dict[str, Any]:
    """Read messages from one thread in chronological order."""
    thread_id = require_text(thread_id, "thread_id")
    limit = bounded_int(max_messages, default=25)
    value = request("GET", f"{BASE_URL}/threads/{quote(thread_id, safe='')}", params={"format": "full"})
    messages = value.get("messages", [])
    normalized = (
        [_normalize_full(item) for item in messages if isinstance(item, dict)] if isinstance(messages, list) else []
    )
    normalized.sort(key=lambda item: str(item.get("internal_date", "")))
    truncated = len(normalized) > limit
    return {"thread_id": value.get("id", thread_id), "messages": normalized[:limit], "messages_truncated": truncated}


def _message_url(message_id: str) -> str:
    return f"{BASE_URL}/messages/{quote(message_id, safe='')}"


def _part_bytes(message_id: str, part: dict[str, Any]) -> bytes:
    """Return an attachment's bytes, inline or fetched by its current attachmentId; never silently empty."""
    body = part.get("body")
    body = body if isinstance(body, dict) else {}
    data = body.get("data")
    if isinstance(data, str):
        return _decode_bytes(data)
    attachment_id = body.get("attachmentId")
    if not isinstance(attachment_id, str):
        if part.get("parts"):  # Gmail expands attached emails into child parts with no attachmentId
            return _raw_part_bytes(message_id, str(part.get("partId") or ""))
        if not body.get("size"):
            return b""
        raise GoogleAPIError(f"Gmail returned no content for attachment {_filename(part)!r}")
    value = request("GET", f"{_message_url(message_id)}/attachments/{quote(attachment_id, safe='')}")
    fetched = value.get("data")
    if not isinstance(fetched, str):
        raise GoogleAPIError(f"Gmail returned no content for attachment {_filename(part)!r}")
    return _decode_bytes(fetched)


def _raw_part_bytes(message_id: str, part_id: str) -> bytes:
    """Extract one part from the raw message; Gmail part IDs are child indexes, as in Python's email tree."""
    value = request("GET", _message_url(message_id), params={"format": "raw"})
    raw = value.get("raw")
    if not isinstance(raw, str):
        raise GoogleAPIError("Gmail did not return the raw message")
    node = cast(Message[Any, Any], message_from_bytes(_decode_bytes(raw), policy=policy.default))
    try:
        for index in part_id.split("."):
            children = node.get_payload()
            if not isinstance(children, list):
                raise IndexError(index)
            child = children[int(index)]
            if not isinstance(child, Message):
                raise IndexError(index)
            node = cast(Message[Any, Any], child)
    except (IndexError, ValueError) as exc:
        raise GoogleAPIError(f"Could not locate part {part_id!r} in the raw message") from exc
    if node.get_content_type() == "message/rfc822":
        inner = node.get_payload()
        attached = inner[0] if isinstance(inner, list) and inner and isinstance(inner[0], Message) else node
        return attached.as_bytes()
    content = node.get_payload(decode=True)
    return content if isinstance(content, bytes) else b""


def download_attachment(
    message_id: str,
    filename: str,
    part_id: str | None = None,
    path: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Save one attachment of a message or draft to local disk, selected by filename (and part_id if ambiguous)."""
    message_id = require_text(message_id, "message_id")
    filename = require_text(filename, "filename")
    if not isinstance(overwrite, bool):
        raise ValueError("overwrite must be a boolean")
    message = request("GET", _message_url(message_id), params={"format": "full"})
    raw_payload = message.get("payload")
    parts = _attachment_parts(cast(dict[str, Any], raw_payload) if isinstance(raw_payload, dict) else {})
    matches = [part for part in parts if _filename(part) == filename]
    if not matches:
        names = sorted({_filename(part) for part in parts})
        raise ValueError(f"No attachment named {filename!r}; attachments: {', '.join(names) or 'none'}")
    if part_id is not None:
        ids = ", ".join(str(part.get("partId")) for part in matches)
        matches = [part for part in matches if part.get("partId") == part_id]
        if not matches:
            raise ValueError(f"No attachment {filename!r} with part_id {part_id!r}; part_ids: {ids}")
    if len(matches) > 1:
        ids = ", ".join(str(part.get("partId")) for part in matches)
        raise ValueError(f"Several attachments are named {filename!r}; pass part_id (one of {ids})")
    part = matches[0]
    data = _part_bytes(message_id, part)
    name = local_files.safe_name(filename, "attachment")
    destination = local_files.target(path, local_files.cache_dir("gmail", message_id), name, overwrite)
    size = local_files.atomic_write(destination, lambda handle: handle.write(data))
    return {"path": str(destination), "size": size, "mime_type": part.get("mimeType")}


@contextmanager
def _mapped_drafts(*, by_id: bool = False) -> Iterator[None]:
    """Translate Google API errors into draft-specific messages; 404 means a missing draft only for ID lookups."""
    try:
        yield
    except GoogleAPIError as exc:
        if exc.status == 403 and set(exc.reasons) & SCOPE_REASONS:
            message = DRAFTS_NOT_AUTHORIZED
        elif exc.status == 404 and by_id:
            message = DRAFT_NOT_FOUND
        else:
            raise
        raise GoogleAPIError(
            f"{message} (HTTP {exc.status}: {exc.detail})", status=exc.status, reasons=exc.reasons, detail=exc.detail
        ) from exc


@dataclass
class _File:
    filename: str
    mime_type: str
    data: bytes


def _addresses(value: list[str] | None, name: str) -> str | None:
    """Validate a recipient list; None means not supplied, an empty list clears the field."""
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{name} must be a list of email addresses")
    cleaned = [item.strip() for item in value if item.strip()]
    for item in cleaned:
        parsed = getaddresses([item])
        if len(parsed) != 1 or "@" not in parsed[0][1]:
            raise ValueError(f"{name} contains an invalid email address: {item}")
    return ", ".join(cleaned)


def _names(value: list[str] | None, name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{name} must be a list of non-empty strings")
    return value


def _local_attachments(paths: list[str] | None) -> list[_File]:
    """Read files to attach; the same absolute-path rules as Drive uploads."""
    files: list[_File] = []
    for path in _names(paths, "attachments"):
        source = local_files.local_file(path, MAX_DRAFT_BYTES)
        files.append(_File(source.name, local_files.content_type(source), source.read_bytes()))
    return files


def _raw_message(headers: dict[str, str | None], body: str, attachments: list[_File]) -> bytes:
    """Build a plain-text RFC 2822 message with attachments; the header policy rejects CR/LF in values."""
    message = EmailMessage()
    for name in ("From", "Reply-To", "To", "Cc", "Bcc", "Subject", "In-Reply-To", "References"):
        value = headers.get(name)
        if value:
            message[name] = value
    message.set_content(body)
    for item in attachments:
        maintype, _, subtype = item.mime_type.partition("/")
        if item.mime_type == "message/rfc822":
            attached = message_from_bytes(item.data, policy=policy.default)
            message.add_attachment(attached, disposition="attachment", filename=item.filename)
            continue
        if not maintype or not subtype or maintype in {"multipart", "message"}:
            maintype, subtype = "application", "octet-stream"
        message.add_attachment(item.data, maintype=maintype, subtype=subtype, filename=item.filename)
    return message.as_bytes()


def _normalize_draft(draft: dict[str, Any], *, include_body: bool = True) -> dict[str, Any]:
    raw_message = draft.get("message")
    message = cast(dict[str, Any], raw_message) if isinstance(raw_message, dict) else {}
    raw_payload = message.get("payload")
    payload = cast(dict[str, Any], raw_payload) if isinstance(raw_payload, dict) else {}
    headers = _headers(payload)
    attachments = [_attachment(part) for part in _attachment_parts(payload)]
    result = compact(
        {
            "draft_id": draft.get("id"),
            "message_id": message.get("id"),
            "thread_id": message.get("threadId"),
            "from": headers.get("from"),
            "to": headers.get("to"),
            "cc": headers.get("cc"),
            "bcc": headers.get("bcc"),
            "subject": headers.get("subject"),
            "in_reply_to": headers.get("in-reply-to"),
            "snippet": message.get("snippet"),
        }
    )
    if include_body:
        body, truncated = _body(payload)
        result.update({"body": body, "body_truncated": truncated, "attachments": attachments})
    else:
        result["has_attachments"] = bool(attachments)
    return result


def _get_draft(draft_id: str) -> dict[str, Any]:
    with _mapped_drafts(by_id=True):
        return request("GET", f"{BASE_URL}/drafts/{quote(draft_id, safe='')}", params={"format": "full"})


def read_draft(draft_id: str) -> dict[str, Any]:
    """Read one draft: headers, body, and attachment list."""
    return _normalize_draft(_get_draft(require_text(draft_id, "draft_id")))


def list_drafts(
    query: str | None = None, max_results: int | None = None, page_token: str | None = None
) -> dict[str, Any]:
    """List drafts, optionally filtered with Gmail query syntax."""
    params: dict[str, Any] = {"maxResults": bounded_int(max_results, default=10)}
    if query and query.strip():
        params["q"] = query.strip()
    if page_token:
        params["pageToken"] = page_token
    with _mapped_drafts():
        page = request("GET", f"{BASE_URL}/drafts", params=params)
    drafts = page.get("drafts", [])
    results: list[dict[str, Any]] = []
    if isinstance(drafts, list):
        for item in drafts:
            draft_id = item.get("id") if isinstance(item, dict) else None
            if not isinstance(draft_id, str):
                continue
            try:
                draft = _get_draft(draft_id)
            except GoogleAPIError as exc:
                if exc.status == 404:  # deleted between list and get
                    continue
                raise
            results.append(_normalize_draft(draft, include_body=False))
    return compact(
        {
            "drafts": results,
            "next_page_token": page.get("nextPageToken"),
            "result_size_estimate": page.get("resultSizeEstimate"),
        }
    )


def _reply_headers(message_id: str) -> tuple[dict[str, str], str | None]:
    """Return threading headers, default subject/recipient, and thread ID for a reply."""
    original = request(
        "GET",
        _message_url(message_id),
        params={
            "format": "metadata",
            "metadataHeaders": ["Message-ID", "References", "Subject", "From", "To", "Reply-To"],
        },
    )
    raw_payload = original.get("payload")
    headers = _headers(cast(dict[str, Any], raw_payload) if isinstance(raw_payload, dict) else {})
    result: dict[str, str] = {}
    original_id = headers.get("message-id")
    if original_id:
        result["In-Reply-To"] = original_id
        result["References"] = f"{headers.get('references', '')} {original_id}".strip()
    subject = headers.get("subject", "")
    result["Subject"] = subject if re.match(r"(?i)re:", subject) else f"Re: {subject}".strip()
    recipient = headers.get("reply-to") or headers.get("from")
    own = account_email()
    if own and recipient and own.lower() in {address.lower() for _, address in getaddresses([recipient])}:
        recipient = headers.get("to")  # replying to the user's own message: follow up with its recipients
    if recipient:
        result["To"] = recipient
    thread_id = original.get("threadId")
    return result, thread_id if isinstance(thread_id, str) else None


def _save(
    headers: dict[str, str | None],
    body: str,
    attachments: list[_File],
    thread_id: str | None,
    draft_id: str | None = None,
) -> dict[str, Any]:
    """Create a draft, or replace the whole message of an existing one, through the media upload endpoint."""
    total = len(body.encode()) + sum(len(item.data) for item in attachments)
    if total > MAX_DRAFT_BYTES:
        raise ValueError(
            f"Draft would be {total / (1024 * 1024):.1f} MB; Gmail's limit is {MAX_DRAFT_BYTES // (1024 * 1024)} MB"
        )
    metadata: dict[str, Any] = {"message": {"threadId": thread_id} if thread_id else {}}
    url = f"{UPLOAD_URL}/drafts"
    if draft_id is not None:
        metadata["id"] = draft_id
        url += f"/{quote(draft_id, safe='')}"
    with _mapped_drafts(by_id=draft_id is not None):
        saved = multipart_upload(
            "PUT" if draft_id is not None else "POST",
            url,
            metadata=metadata,
            media=_raw_message(headers, body, attachments),
            media_type="message/rfc822",
        )
    saved_id = saved.get("id")
    if not isinstance(saved_id, str):
        raise GoogleAPIError("Gmail did not return a draft ID")
    return _normalize_draft(_get_draft(saved_id))


def create_draft(
    *,
    body: str,
    to: list[str] | None = None,
    subject: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    reply_to_message_id: str | None = None,
    attachments: list[str] | None = None,
) -> dict[str, Any]:
    """Create a plain-text draft with optional local-file attachments, optionally threaded as a reply. Never sends."""
    if not isinstance(body, str):
        raise ValueError("body must be a string")
    files = _local_attachments(attachments)
    headers: dict[str, str | None] = {}
    thread_id = None
    if reply_to_message_id is not None:
        reply, thread_id = _reply_headers(require_text(reply_to_message_id, "reply_to_message_id"))
        headers.update(reply)
    if to:
        headers["To"] = _addresses(to, "to")
    if not headers.get("To"):
        raise ValueError("to is required unless replying to a message with a sender to reply to")
    if subject is not None:
        headers["Subject"] = subject
    headers["Cc"] = _addresses(cc, "cc")
    headers["Bcc"] = _addresses(bcc, "bcc")
    return _save(headers, body, files, thread_id)


def update_draft(
    draft_id: str,
    *,
    to: list[str] | None = None,
    subject: str | None = None,
    body: str | None = None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    attachments: list[str] | None = None,
    remove_attachments: list[str] | None = None,
) -> dict[str, Any]:
    """Change supplied fields of one draft, rewriting it as plain text and keeping its attachments. Never sends."""
    draft_id = require_text(draft_id, "draft_id")
    if all(value is None for value in (to, subject, body, cc, bcc)) and not attachments and not remove_attachments:
        raise ValueError("Supply at least one of to, subject, body, cc, bcc, attachments, remove_attachments")
    if body is not None and not isinstance(body, str):
        raise ValueError("body must be a string")
    added = _local_attachments(attachments)
    removed = set(_names(remove_attachments, "remove_attachments"))
    current = _get_draft(draft_id)
    raw_message = current.get("message")
    message = cast(dict[str, Any], raw_message) if isinstance(raw_message, dict) else {}
    raw_payload = message.get("payload")
    payload = cast(dict[str, Any], raw_payload) if isinstance(raw_payload, dict) else {}
    parts = _attachment_parts(payload)
    unknown = removed - {_filename(part) for part in parts}
    if unknown:
        raise ValueError(f"Draft has no attachment named: {', '.join(sorted(unknown))}")
    existing = _headers(payload)
    if body is None:
        body, truncated = _body(payload)
        if truncated:
            raise ValueError("Draft body is too long to preserve; supply body explicitly")
    # A new file replaces an existing attachment with the same name.
    replaced = removed | {item.filename for item in added}
    message_id = str(message.get("id") or "")
    kept = [
        _File(_filename(part), str(part.get("mimeType") or "application/octet-stream"), _part_bytes(message_id, part))
        for part in parts
        if _filename(part) not in replaced
    ]
    headers: dict[str, str | None] = {
        "From": existing.get("from"),
        "Reply-To": existing.get("reply-to"),
        "To": existing.get("to"),
        "Cc": existing.get("cc"),
        "Bcc": existing.get("bcc"),
        "Subject": existing.get("subject"),
        "In-Reply-To": existing.get("in-reply-to"),
        "References": existing.get("references"),
    }
    for name, value in (("To", _addresses(to, "to")), ("Cc", _addresses(cc, "cc")), ("Bcc", _addresses(bcc, "bcc"))):
        if value is not None:
            headers[name] = value
    if subject is not None:
        headers["Subject"] = subject
    thread_id = message.get("threadId")
    return _save(headers, body, kept + added, thread_id if isinstance(thread_id, str) else None, draft_id)
