"""Binary sync: folders of binaries kept in Drive, tracked in git only by a `files.toml` manifest.

A binary folder holds a `.gitignore` that ignores everything but itself and the manifest, the
manifest, and the binaries at any depth. `pull` makes the folder match the manifest; `push` makes
the manifest match the folder. Drive blobs are append-only. See DESIGN.md, "Binary sync CLI".
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, cast

from google_mcp import drive, local_files
from google_mcp.google_api import GoogleAPIError

MANIFEST = "files.toml"
GITIGNORE = ".gitignore"
GITIGNORE_TEXT = "*\n!.gitignore\n!files.toml\n"
HEADER = "# Synced with `google-mcp files`; run `google-mcp files pull` to download these files.\n"
KNOWN_KEYS = ("sha256", "size", "drive_id")
SKIP_DIRS = {"node_modules"}
SHA256 = re.compile(r"[0-9a-f]{64}")
BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")

Entry = dict[str, Any]


class SyncError(ValueError):
    """A folder or manifest problem that stops work on that folder."""


# Paths


def check_path(rel: str) -> str:
    """Validate a manifest key: a relative `/`-separated path with no empty, `.`, `..`, or dot parts."""
    if not isinstance(rel, str) or not rel:
        raise ValueError("path must be a non-empty string")
    if "\\" in rel or any(ord(char) < 32 or ord(char) == 127 for char in rel):
        raise ValueError(f"{rel!r}: path has a backslash or control character")
    for part in rel.split("/"):
        if part in ("", ".", ".."):
            raise ValueError(f"{rel!r}: path must be relative, with no empty, '.' or '..' parts")
        if part.startswith("."):
            raise ValueError(f"{rel!r}: dotfiles and dot-directories are never synced")
    if rel == MANIFEST:
        raise ValueError(f"{rel!r} is the manifest itself")
    return rel


def _target(folder: Path, rel: str) -> Path:
    """Return where `rel` lives, refusing anything that resolves outside the folder."""
    target = folder / check_path(rel)
    if not target.parent.resolve().is_relative_to(folder.resolve()):
        raise SyncError(f"{rel!r} resolves outside {folder}")
    return target


def _show(path: Path) -> str:
    return os.path.relpath(path)


def _bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


# Manifest


def _string(value: str) -> str:
    """Render a TOML basic string. JSON escapes are valid TOML escapes; DEL must be escaped too."""
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def _key(key: str) -> str:
    return key if BARE_KEY.fullmatch(key) else _string(key)


def _value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return _string(value)


def read_manifest(folder: Path) -> dict[str, Entry]:
    """Parse and validate `files.toml`. The manifest is repository content: treat it as untrusted."""
    path = folder / MANIFEST
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise SyncError(f"{_show(path)}: {exc}") from exc
    entries: dict[str, Entry] = {}
    for rel, value in data.items():
        where = f"{_show(path)}: [{_string(rel)}]"
        if not isinstance(value, dict):
            raise SyncError(f"{where} must be a table")
        try:
            check_path(rel)
        except ValueError as exc:
            raise SyncError(f"{_show(path)}: {exc}") from exc
        entry = cast(Entry, value)
        sha, size, drive_id = entry.get("sha256"), entry.get("size"), entry.get("drive_id")
        if not isinstance(sha, str) or not SHA256.fullmatch(sha):
            raise SyncError(f"{where}: sha256 must be 64 lowercase hex characters")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise SyncError(f"{where}: size must be a non-negative integer")
        if not isinstance(drive_id, str) or not drive_id:
            raise SyncError(f"{where}: drive_id must be a non-empty string")
        for key, extra in entry.items():
            if key not in KNOWN_KEYS and not isinstance(extra, str | int):
                raise SyncError(f"{where}: {key} must be a string, integer, or boolean")
        entries[rel] = dict(entry)
    return entries


def render_manifest(entries: dict[str, Entry]) -> str:
    """Render entries sorted by path (code-point order), known keys first, so output is stable."""
    parts = [HEADER]
    for rel in sorted(entries):
        entry = entries[rel]
        lines = [f"\n[{_string(rel)}]"]
        lines += [f"{key} = {_value(entry[key])}" for key in KNOWN_KEYS]
        lines += [f"{_key(key)} = {_value(entry[key])}" for key in sorted(set(entry) - set(KNOWN_KEYS))]
        parts.append("\n".join(lines) + "\n")
    return "".join(parts)


def _write_text(path: Path, text: str) -> None:
    local_files.atomic_write(path, lambda handle: handle.write(text.encode()), private=False)


def write_manifest(folder: Path, entries: dict[str, Entry]) -> bool:
    """Write the manifest only if its rendering changed; return whether it was written."""
    path = folder / MANIFEST
    text = render_manifest(entries)
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except (OSError, UnicodeDecodeError):
        pass
    _write_text(path, text)
    return True


# Discovery and scanning


def _owner(directory: Path) -> Path | None:
    """Return the binary folder containing `directory` (itself included), stopping at a git root."""
    for candidate in (directory, *directory.parents):
        if (candidate / MANIFEST).is_file():
            return candidate
        if (candidate / ".git").exists():
            return None
    return None


def find_folders(path: str | Path) -> list[Path]:
    """Return the binary folders a command acts on: the one `path` is inside, or every one below it."""
    start = Path(os.path.abspath(os.path.expanduser(path)))
    if not start.exists():
        raise SyncError(f"{start} does not exist")
    owner = _owner(start if start.is_dir() else start.parent)
    if owner is not None:
        return [owner]
    if not start.is_dir():
        return []
    folders: list[Path] = []
    for root, dirs, files in os.walk(start):
        if MANIFEST in files:
            folders.append(Path(root))
            dirs.clear()
            continue
        dirs[:] = sorted(name for name in dirs if not name.startswith(".") and name not in SKIP_DIRS)
    return folders


def scan(folder: Path) -> dict[str, Path]:
    """Return the folder's syncable files by relative path. Symlinks and nested manifests are errors."""
    found: dict[str, Path] = {}
    for root, dirs, files in os.walk(folder):
        base = Path(root)
        for name in list(dirs):
            if name.startswith("."):
                dirs.remove(name)
            elif (base / name).is_symlink():
                raise SyncError(f"{_show(base / name)}: symlinks are refused in a binary folder")
        dirs.sort()
        for name in sorted(files):
            if name.startswith("."):
                continue
            path = base / name
            if name == MANIFEST:
                if base == folder:
                    continue
                raise SyncError(f"{_show(path)}: nested binary folder; {_show(folder)} owns its whole subtree")
            if path.is_symlink():
                raise SyncError(f"{_show(path)}: symlinks are refused in a binary folder")
            if not path.is_file():
                raise SyncError(f"{_show(path)}: not a regular file")
            rel = path.relative_to(folder).as_posix()
            try:
                check_path(rel)
            except ValueError as exc:
                raise SyncError(f"{_show(path)}: {exc}") from exc
            found[rel] = path
    return found


def digest(path: Path) -> tuple[str, int]:
    """Return the file's SHA-256 hex digest and size."""
    with path.open("rb") as handle:
        sha = hashlib.file_digest(handle, "sha256").hexdigest()
        return sha, os.fstat(handle.fileno()).st_size


@dataclass
class FolderState:
    folder: Path
    entries: dict[str, Entry]
    files: dict[str, Path]
    local: dict[str, tuple[str, int]]
    missing: list[str]
    modified: list[str]
    new: list[str]


def state(folder: Path) -> FolderState:
    """Compare the manifest with the folder's files (offline)."""
    entries = read_manifest(folder)
    files = scan(folder)
    local: dict[str, tuple[str, int]] = {}
    missing: list[str] = []
    modified: list[str] = []
    new: list[str] = []
    for rel in sorted(set(entries) | set(files)):
        if rel not in files:
            missing.append(rel)
            continue
        local[rel] = digest(files[rel])
        if rel not in entries:
            new.append(rel)
        elif (entries[rel]["sha256"], entries[rel]["size"]) != local[rel]:
            modified.append(rel)
    return FolderState(folder, entries, files, local, missing, modified, new)


# Commands


@dataclass
class Report:
    """What a command did to one folder: stdout lines, stderr lines, and counts per verb."""

    lines: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    counts: Counter[str] = field(default_factory=Counter)

    def add(self, verb: str, text: str) -> None:
        self.lines.append(f"{verb} {text}")
        self.counts[verb] += 1

    def fail(self, text: str) -> None:
        self.errors.append(f"failed {text}")
        self.counts["failed"] += 1


def init(directory: str | Path) -> list[Path]:
    """Make `directory` a binary folder; return the files created (none if already initialized)."""
    folder = Path(os.path.abspath(os.path.expanduser(directory)))
    outer = _owner(folder.parent)
    if outer is not None:
        raise SyncError(f"{_show(folder)} is inside the binary folder {_show(outer)}")
    if folder.exists() and not folder.is_dir():
        raise SyncError(f"{_show(folder)} exists and is not a directory")
    gitignore, manifest = folder / GITIGNORE, folder / MANIFEST
    if gitignore.exists() and gitignore.read_text(encoding="utf-8", errors="replace") != GITIGNORE_TEXT:
        raise SyncError(f"{_show(gitignore)} exists with other content; expected:\n{GITIGNORE_TEXT}")
    if manifest.exists():
        read_manifest(folder)
    created: list[Path] = []
    for path, text in ((gitignore, GITIGNORE_TEXT), (manifest, HEADER)):
        if not path.exists():
            _write_text(path, text)
            created.append(path)
    return created


def status(folder: Path) -> Report:
    report = Report()
    current = state(folder)
    for verb, paths in (("missing", current.missing), ("modified", current.modified), ("new", current.new)):
        for rel in paths:
            report.add(verb, _show(folder / rel))
    return report


class _HashingWriter:
    def __init__(self, handle: BinaryIO) -> None:
        self.handle = handle
        self.hash = hashlib.sha256()

    def write(self, data: bytes) -> int:
        self.hash.update(data)
        return self.handle.write(data)


def _download(entry: Entry, target: Path) -> None:
    """Download into a temporary file, verify the hash, then rename into place."""

    def write(handle: BinaryIO) -> int:
        hashing = _HashingWriter(handle)
        size = drive.download_blob(entry["drive_id"], cast(BinaryIO, hashing))
        if hashing.hash.hexdigest() != entry["sha256"]:
            raise SyncError(f"downloaded content does not match its sha256 in {MANIFEST}; file left unchanged")
        return size

    local_files.atomic_write(target, write, private=False)


def pull(folder: Path) -> Report:
    """Make the folder match the manifest: download missing and modified files."""
    report = Report()
    current = state(folder)
    for rel in current.new:
        report.add("new", f"{_show(folder / rel)} (not in {MANIFEST}; push adds it)")
    for rel in sorted(current.missing + current.modified):
        entry = current.entries[rel]
        shown = _show(folder / rel)
        try:
            _download(entry, _target(folder, rel))
        except (GoogleAPIError, OSError, ValueError) as exc:
            report.fail(f"{shown}: {exc}")
            continue
        report.add("replaced" if rel in current.modified else "pulled", f"{shown} ({_bytes(entry['size'])})")
    return report


def blob_name(name: str, sha256: str) -> str:
    """`<stem>.<first 8 hex of sha256><ext>`, e.g. `icon-192.9f2c41ab.png` or `model.9f2c41ab`."""
    path = PurePosixPath(name)
    return f"{path.stem}.{sha256[:8]}{path.suffix}"


def push(folder: Path, *, dry_run: bool = False) -> Report:
    """Make the manifest match the folder: upload new and modified files, drop entries of missing ones."""
    report = Report()
    current = state(folder)
    entries = dict(current.entries)
    for rel in current.missing:
        del entries[rel]
        report.add("would drop" if dry_run else "dropped", _show(folder / rel))
    folder_id: str | None = None
    for rel in sorted(current.new + current.modified):
        sha, size = current.local[rel]
        path = current.files[rel]
        shown = f"{_show(path)} ({_bytes(size)})"
        if dry_run:
            report.add("would upload", shown)
            continue
        if size > drive.MAX_UPLOAD_BYTES:
            report.fail(f"{shown}: larger than the {drive.MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit")
            continue
        try:
            if folder_id is None:
                folder_id = drive.files_folder()
            blob_id = drive.find_blob(folder_id, sha)
            verb = "linked"
            if blob_id is None:
                created = drive.upload_blob(path, blob_name(path.name, sha), folder_id, sha)
                checksum = created.get("sha256Checksum")
                if checksum is not None and checksum != sha:
                    raise SyncError("Drive's sha256 of the upload does not match; did the file change while pushing?")
                blob_id = created.get("id")
                if not isinstance(blob_id, str) or not blob_id:
                    raise GoogleAPIError("Google API did not return the new file's ID")
                verb = "uploaded"
        except (GoogleAPIError, OSError, ValueError) as exc:
            report.fail(f"{shown}: {exc}")
            continue
        entries[rel] = {**current.entries.get(rel, {}), "sha256": sha, "size": size, "drive_id": blob_id}
        report.add(verb, shown)
    if not dry_run and write_manifest(folder, entries):
        report.lines.append(f"wrote {_show(folder / MANIFEST)}")
    return report
