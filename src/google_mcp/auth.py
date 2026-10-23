"""Google installed-app OAuth with PKCE and local token storage."""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import shutil
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any, override

import requests

SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
)
DEFAULT_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
GMAIL_PROFILE_URI = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
REQUEST_TIMEOUT = 30


class AuthError(RuntimeError):
    """Authentication is missing, expired, or invalid."""


def config_dir() -> Path:
    override = os.environ.get("GOOGLE_MCP_CONFIG_DIR")
    return Path(override).expanduser() if override else Path.home() / ".claude" / "google_mcp" / "config"


def client_secrets_path() -> Path:
    return config_dir() / "client_secrets.json"


def state_path() -> Path:
    return config_dir() / "state.json"


def _prepare_config_dir() -> None:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise AuthError(f"Missing {path}. Run: google-mcp configure /path/to/client_secret.json") from exc
    except json.JSONDecodeError as exc:
        raise AuthError(f"Invalid JSON in {path}") from exc
    if not isinstance(value, dict):
        raise AuthError(f"Expected a JSON object in {path}")
    return value


def _client() -> dict[str, str]:
    document = _read_json(client_secrets_path())
    raw = document.get("installed")
    if not isinstance(raw, dict):
        raise AuthError("OAuth credentials must be a Google Desktop app client (the JSON must contain 'installed')")
    client_id = raw.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        raise AuthError("OAuth client JSON is missing installed.client_id")
    result = {
        "client_id": client_id,
        "auth_uri": str(raw.get("auth_uri") or DEFAULT_AUTH_URI),
        "token_uri": str(raw.get("token_uri") or DEFAULT_TOKEN_URI),
    }
    secret = raw.get("client_secret")
    if isinstance(secret, str) and secret:
        result["client_secret"] = secret
    return result


def configure(source: str | Path) -> Path:
    """Validate and copy a downloaded Desktop OAuth client JSON file."""
    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file():
        raise AuthError(f"OAuth client file does not exist: {source_path}")
    try:
        document = json.loads(source_path.read_text())
    except json.JSONDecodeError as exc:
        raise AuthError(f"OAuth client file is not valid JSON: {source_path}") from exc
    installed = document.get("installed") if isinstance(document, dict) else None
    if not isinstance(installed, dict) or not installed.get("client_id"):
        raise AuthError("Choose a Desktop app OAuth client; expected an 'installed.client_id' value")
    _prepare_config_dir()
    destination = client_secrets_path()
    shutil.copyfile(source_path, destination)
    os.chmod(destination, 0o600)
    return destination


def write_private_json(path: Path, value: dict[str, Any]) -> None:
    """Atomically write a JSON file with mode 0600 inside the config directory."""
    _prepare_config_dir()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)


def _atomic_write_state(value: dict[str, Any]) -> None:
    write_private_json(state_path(), value)


def _load_state() -> dict[str, Any]:
    try:
        value = _read_json(state_path())
    except AuthError as exc:
        if not state_path().exists():
            raise AuthError("Google account is not authorized. Run: google-mcp auth") from exc
        raise
    return value


def _token_request(payload: dict[str, str], client: dict[str, str]) -> dict[str, Any]:
    try:
        response = requests.post(client["token_uri"], data=payload, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise AuthError(f"Could not contact Google's OAuth token endpoint: {exc}") from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not response.ok:
        code = body.get("error") if isinstance(body, dict) else None
        detail = body.get("error_description") if isinstance(body, dict) else None
        message = f"Google OAuth failed ({code or response.status_code})"
        if detail:
            message += f": {detail}"
        if code == "invalid_grant":
            message += ". The authorization may have expired; run: google-mcp auth"
        raise AuthError(message)
    if not isinstance(body, dict) or not body.get("access_token"):
        raise AuthError("Google OAuth response did not contain an access token")
    return body


class _CallbackServer(http.server.ThreadingHTTPServer):
    result: dict[str, str]
    expected_state: str


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    server: _CallbackServer

    def do_GET(self) -> None:  # noqa: N802
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        state = query.get("state", [""])[0]
        if not secrets.compare_digest(state, self.server.expected_state):
            self.server.result = {"error": "OAuth callback state did not match"}
            self._reply(400, "Authorization failed: the callback state did not match.")
            return
        error = query.get("error", [""])[0]
        if error:
            self.server.result = {"error": f"Google returned: {error}"}
            self._reply(400, "Google authorization was not completed. You may close this tab.")
            return
        code = query.get("code", [""])[0]
        if not code:
            self.server.result = {"error": "OAuth callback did not include a code"}
            self._reply(400, "Authorization failed: no code was returned.")
            return
        self.server.result = {"code": code}
        self._reply(200, "Google MCP authorization complete. You may close this tab.")

    def _reply(self, status: int, message: str) -> None:
        body = f"<!doctype html><meta charset='utf-8'><title>Google MCP</title><p>{message}</p>".encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @override
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


def authenticate(timeout_seconds: int = 300) -> dict[str, Any]:
    """Run browser authorization and save tokens locally."""
    client = _client()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    expected_state = secrets.token_urlsafe(32)

    server = _CallbackServer(("127.0.0.1", 0), _CallbackHandler)
    server.result = {}
    server.expected_state = expected_state
    server.timeout = 1
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}"
    params = {
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": expected_state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    authorization_url = f"{client['auth_uri']}?{urllib.parse.urlencode(params)}"
    print("Opening Google authorization in your browser...")
    print(f"If it does not open, visit:\n{authorization_url}\n")
    webbrowser.open(authorization_url)

    deadline = time.monotonic() + timeout_seconds
    while not server.result and time.monotonic() < deadline:
        server.handle_request()
    server.server_close()
    if not server.result:
        raise AuthError("Timed out waiting for Google authorization")
    if "error" in server.result:
        raise AuthError(server.result["error"])

    payload = {
        "client_id": client["client_id"],
        "code": server.result["code"],
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    if client.get("client_secret"):
        payload["client_secret"] = client["client_secret"]
    token = _token_request(payload, client)
    saved: dict[str, Any] = {
        "access_token": token["access_token"],
        "refresh_token": token.get("refresh_token"),
        "expires_at": int(time.time()) + int(token.get("expires_in", 3600)),
        "scope": token.get("scope", " ".join(SCOPES)),
        "token_type": token.get("token_type", "Bearer"),
    }
    try:
        profile = requests.get(
            GMAIL_PROFILE_URI,
            headers={"Authorization": f"Bearer {token['access_token']}"},
            timeout=REQUEST_TIMEOUT,
        )
        if profile.ok:
            email = profile.json().get("emailAddress")
            if isinstance(email, str):
                saved["account_email"] = email
    except (requests.RequestException, ValueError):
        pass
    if not saved.get("refresh_token"):
        raise AuthError(
            "Google did not return a refresh token. Revoke the app's access, then run google-mcp auth again"
        )
    _atomic_write_state(saved)
    return {"account_email": saved.get("account_email"), "expires_at": saved["expires_at"]}


def access_token(*, force_refresh: bool = False) -> str:
    """Return a current access token, refreshing it when needed."""
    state = _load_state()
    token = state.get("access_token")
    expiry = int(state.get("expires_at") or 0)
    if not force_refresh and isinstance(token, str) and token and expiry > int(time.time()) + 60:
        return token
    refresh_token = state.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise AuthError("No Google refresh token is available. Run: google-mcp auth")
    client = _client()
    payload = {
        "client_id": client["client_id"],
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    if client.get("client_secret"):
        payload["client_secret"] = client["client_secret"]
    refreshed = _token_request(payload, client)
    state.update(
        {
            "access_token": refreshed["access_token"],
            "expires_at": int(time.time()) + int(refreshed.get("expires_in", 3600)),
            "token_type": refreshed.get("token_type", state.get("token_type", "Bearer")),
        }
    )
    if refreshed.get("scope"):
        state["scope"] = refreshed["scope"]
    _atomic_write_state(state)
    return str(refreshed["access_token"])


def account_email() -> str | None:
    """Return the authorized account email, if known."""
    value = _load_state().get("account_email")
    return value if isinstance(value, str) else None


def status() -> dict[str, Any]:
    """Return non-secret local authorization status."""
    credentials_present = client_secrets_path().is_file()
    if not state_path().is_file():
        return {"configured": credentials_present, "authorized": False, "missing_scopes": list(SCOPES)}
    state = _load_state()
    raw_scope = state.get("scope")
    granted = sorted(raw_scope.split()) if isinstance(raw_scope, str) else []
    return {
        "configured": credentials_present,
        "authorized": bool(state.get("refresh_token")),
        "account_email": state.get("account_email"),
        "access_token_expires_at": state.get("expires_at"),
        "granted_scopes": granted,
        "missing_scopes": [scope for scope in SCOPES if scope not in granted],
        "state_file": str(state_path()),
    }


def clear_authorization() -> bool:
    """Delete the local OAuth state without touching the client JSON."""
    path = state_path()
    if not path.exists():
        return False
    path.unlink()
    return True
