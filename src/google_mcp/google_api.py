"""Authenticated request helper for Google REST APIs."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any, BinaryIO

import requests

from google_mcp import auth

REQUEST_TIMEOUT = 30
TRANSFER_TIMEOUT = 120
CHUNK_SIZE = 1024 * 1024


class GoogleAPIError(RuntimeError):
    """A Google API request failed."""

    def __init__(
        self, message: str, *, status: int | None = None, reasons: tuple[str, ...] = (), detail: str | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.reasons = reasons
        self.detail = detail or message


def _error_reasons(body: Any) -> tuple[str, ...]:
    """Collect Google's machine-readable reasons from an error body."""
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return ()
    reasons: list[str] = []
    for key in ("errors", "details"):
        values = error.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            reason = item.get("reason") if isinstance(item, dict) else None
            if isinstance(reason, str) and reason not in reasons:
                reasons.append(reason)
    return tuple(reasons)


def _error_message(response: requests.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = {}
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        message = error.get("message")
        reasons = error.get("errors")
        reason = (
            reasons[0].get("reason") if isinstance(reasons, list) and reasons and isinstance(reasons[0], dict) else None
        )
        detail = str(message or reason or "Google API request failed")
        if reason and message and reason not in message:
            detail += f" ({reason})"
        return detail
    return response.reason or "Google API request failed"


def _api_error(response: requests.Response) -> GoogleAPIError:
    try:
        body = response.json()
    except ValueError:
        body = {}
    detail = _error_message(response)
    return GoogleAPIError(
        f"Google API returned HTTP {response.status_code}: {detail}",
        status=response.status_code,
        reasons=_error_reasons(body),
        detail=detail,
    )


def request(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Issue an authenticated request and return a JSON object."""
    for attempt in range(2):
        token = auth.access_token(force_refresh=attempt == 1)
        try:
            response = requests.request(
                method,
                url,
                params=params,
                json=json_body,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise GoogleAPIError(f"Could not contact Google API: {exc}") from exc
        if response.status_code == 401 and attempt == 0:
            continue
        if not response.ok:
            raise _api_error(response)
        if response.status_code == 204 or not response.content:
            return {}
        try:
            value = response.json()
        except ValueError as exc:
            raise GoogleAPIError("Google API returned an invalid JSON response") from exc
        if not isinstance(value, dict):
            raise GoogleAPIError("Google API returned an unexpected response")
        return value
    raise GoogleAPIError("Google API authorization failed after token refresh")


def download(url: str, destination: BinaryIO, *, params: dict[str, Any] | None = None) -> int:
    """Stream a GET response body into an open binary file and return the byte count."""
    for attempt in range(2):
        token = auth.access_token(force_refresh=attempt == 1)
        try:
            with requests.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                stream=True,
                timeout=TRANSFER_TIMEOUT,
            ) as response:
                if response.status_code == 401 and attempt == 0:
                    continue
                if not response.ok:
                    raise _api_error(response)
                written = 0
                for chunk in response.iter_content(CHUNK_SIZE):
                    destination.write(chunk)
                    written += len(chunk)
                return written
        except requests.RequestException as exc:
            raise GoogleAPIError(f"Could not contact Google API: {exc}") from exc
    raise GoogleAPIError("Google API authorization failed after token refresh")


def resumable_upload(
    method: str,
    url: str,
    source: Path,
    *,
    params: dict[str, Any] | None = None,
    metadata: dict[str, Any],
    content_type: str,
) -> dict[str, Any]:
    """Start a resumable upload session, then PUT the whole file in one request, streamed from disk.

    A 401 restarts the whole upload once with a fresh access token.
    """
    for attempt in range(2):
        token = auth.access_token(force_refresh=attempt == 1)
        try:
            with source.open("rb") as handle:
                size = os.fstat(handle.fileno()).st_size
                session = requests.request(
                    method,
                    url,
                    params=params,
                    json=metadata,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-Upload-Content-Type": content_type,
                        "X-Upload-Content-Length": str(size),
                    },
                    timeout=REQUEST_TIMEOUT,
                )
                if session.status_code == 401 and attempt == 0:
                    continue
                if not session.ok:
                    raise _api_error(session)
                location = session.headers.get("Location")
                if not location:
                    raise GoogleAPIError("Google API did not return an upload session URL")
                response = requests.put(
                    location,
                    data=handle,
                    headers={"Content-Type": content_type, "Content-Length": str(size)},
                    timeout=TRANSFER_TIMEOUT,
                )
        except requests.RequestException as exc:
            raise GoogleAPIError(f"Could not contact Google API: {exc}") from exc
        if response.status_code == 401 and attempt == 0:
            continue
        if not response.ok:
            raise _api_error(response)
        try:
            value = response.json()
        except ValueError as exc:
            raise GoogleAPIError("Google API returned an invalid JSON response") from exc
        if not isinstance(value, dict):
            raise GoogleAPIError("Google API returned an unexpected response")
        return value
    raise GoogleAPIError("Google API authorization failed after token refresh")


def multipart_upload(
    method: str,
    url: str,
    *,
    metadata: dict[str, Any],
    media: bytes,
    media_type: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send JSON metadata and media in one multipart/related request (`uploadType=multipart`)."""
    boundary = f"google_mcp_{secrets.token_hex(16)}"
    body = b"".join(
        [
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode(),
            json.dumps(metadata).encode(),
            f"\r\n--{boundary}\r\nContent-Type: {media_type}\r\n\r\n".encode(),
            media,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    for attempt in range(2):
        token = auth.access_token(force_refresh=attempt == 1)
        try:
            response = requests.request(
                method,
                url,
                params={**(params or {}), "uploadType": "multipart"},
                data=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    "Content-Type": f"multipart/related; boundary={boundary}",
                },
                timeout=TRANSFER_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise GoogleAPIError(f"Could not contact Google API: {exc}") from exc
        if response.status_code == 401 and attempt == 0:
            continue
        if not response.ok:
            raise _api_error(response)
        try:
            value = response.json()
        except ValueError as exc:
            raise GoogleAPIError("Google API returned an invalid JSON response") from exc
        if not isinstance(value, dict):
            raise GoogleAPIError("Google API returned an unexpected response")
        return value
    raise GoogleAPIError("Google API authorization failed after token refresh")
