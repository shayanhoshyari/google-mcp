"""Local path rules shared by Drive and Gmail: absolute paths, safe names, atomic private writes."""

from __future__ import annotations

import mimetypes
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO

from google_mcp.utils import require_text

MAX_FILENAME_CHARS = 200


def absolute(path: str) -> str:
    expanded = os.path.expanduser(require_text(path, "path"))
    if not os.path.isabs(expanded):
        raise ValueError("path must be absolute (after ~ expansion); the server's working directory is not yours")
    return expanded


def safe_name(name: Any, fallback: str) -> str:
    """Make a remote file name safe to use as a single local path component."""
    raw = str(name) if isinstance(name, str) else ""
    cleaned = "".join("_" if char in "/\\" or ord(char) < 32 or ord(char) == 127 else char for char in raw)
    cleaned = cleaned.strip()[:MAX_FILENAME_CHARS]
    return cleaned if cleaned and cleaned not in {".", ".."} else fallback


def cache_dir(*parts: str) -> Path:
    """Return the tool's own download cache directory under $TMPDIR/google_mcp/."""
    return Path(tempfile.gettempdir()).joinpath("google_mcp", *(safe_name(part, "file") for part in parts))


def target(path: str | None, cache: Path, filename: str, overwrite: bool) -> Path:
    """Resolve the local save path from the path rules and refuse to clobber unless allowed."""
    if path is None:
        return cache / filename
    expanded = absolute(path)
    as_directory = expanded.endswith("/") or os.path.isdir(expanded)
    base = Path(os.path.normpath(expanded))
    resolved = base / filename if as_directory else base
    if os.path.lexists(resolved) and not overwrite:
        raise ValueError(f"{resolved} already exists; pass overwrite=true to replace it")
    return resolved


def make_directories(directory: Path) -> None:
    """Create missing directories with mode 0700."""
    missing: list[Path] = []
    current = directory
    while not current.exists():
        missing.append(current)
        current = current.parent
    for created in reversed(missing):
        created.mkdir(mode=0o700, exist_ok=True)
        os.chmod(created, 0o700)


def atomic_write(destination: Path, write: Callable[[BinaryIO], int]) -> int:
    """Write into a temporary file next to the destination with mode 0600, then rename it into place."""
    make_directories(destination.parent)
    descriptor, temporary = tempfile.mkstemp(dir=destination.parent, prefix=".google_mcp-", suffix=".part")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            written = write(handle)
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return written


def local_file(path: str, max_bytes: int) -> Path:
    """Validate a file to read: absolute, existing, readable, regular, within the size cap."""
    source = Path(os.path.normpath(absolute(path)))
    if not source.is_file():
        raise ValueError(f"{source} does not exist or is not a regular file")
    if not os.access(source, os.R_OK):
        raise ValueError(f"{source} is not readable")
    if source.stat().st_size > max_bytes:
        raise ValueError(f"{source} is larger than the {max_bytes // (1024 * 1024)} MB limit")
    return source


def content_type(source: Path) -> str:
    return mimetypes.guess_type(source.name)[0] or "application/octet-stream"
