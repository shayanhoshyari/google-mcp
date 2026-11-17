"""Offline tests for the binary sync CLI (`google-mcp files`), with Drive replaced by a fake."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, BinaryIO

import pytest

from google_mcp import drive, files_sync
from google_mcp.files_sync import SyncError


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeDrive:
    def __init__(self) -> None:
        self.blobs: dict[str, dict[str, Any]] = {}
        self.uploads: list[str] = []
        self.fail_upload: set[str] = set()
        self.corrupt: set[str] = set()

    def files_folder(self) -> str:
        return "folder1"

    def find_blob(self, folder_id: str, digest: str) -> str | None:
        return next((i for i, b in self.blobs.items() if b["sha256"] == digest and b["parent"] == folder_id), None)

    def upload_blob(self, source: Path, name: str, folder_id: str, digest: str) -> dict[str, Any]:
        if source.name in self.fail_upload:
            raise drive.GoogleAPIError("Google API returned HTTP 500: boom", status=500)
        data = source.read_bytes()
        blob_id = f"id{len(self.blobs) + 1}"
        self.blobs[blob_id] = {"name": name, "data": data, "sha256": digest, "parent": folder_id}
        self.uploads.append(name)
        return {"id": blob_id, "sha256Checksum": sha(data)}

    def download_blob(self, file_id: str, destination: BinaryIO) -> int:
        data = self.blobs[file_id]["data"]
        if file_id in self.corrupt:
            data = b"corrupt" + data
        destination.write(data)
        return len(data)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeDrive:
    monkeypatch.chdir(tmp_path)
    fake = FakeDrive()
    for name in ("files_folder", "find_blob", "upload_blob", "download_blob"):
        monkeypatch.setattr(drive, name, getattr(fake, name))
    return fake


@pytest.fixture
def folder(tmp_path: Path, fake: FakeDrive) -> Path:
    (tmp_path / ".git").mkdir()
    path = tmp_path / "assets"
    files_sync.init(path)
    return path


def entry(data: bytes, drive_id: str = "x", **extra: Any) -> dict[str, Any]:
    return {"sha256": sha(data), "size": len(data), "drive_id": drive_id, **extra}


# Manifest


def test_init_writes_gitignore_and_empty_manifest(folder: Path) -> None:
    assert (folder / ".gitignore").read_text() == "*\n!.gitignore\n!files.toml\n"
    assert files_sync.read_manifest(folder) == {}
    assert files_sync.init(folder) == []


def test_init_refuses_other_gitignore_and_nesting(tmp_path: Path, folder: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    (other / ".gitignore").write_text("*.log\n")
    with pytest.raises(SyncError, match="other content"):
        files_sync.init(other)
    with pytest.raises(SyncError, match="inside the binary folder"):
        files_sync.init(folder / "sub")


def test_manifest_round_trip_keeps_unknown_keys(folder: Path) -> None:
    entries = {"b/c.bin": entry(b"c", note='say "hi"', pinned=True), "a.png": entry(b"a", count=3)}
    files_sync.write_manifest(folder, entries)
    assert files_sync.read_manifest(folder) == entries
    text = (folder / "files.toml").read_text()
    assert text.index('["a.png"]') < text.index('["b/c.bin"]')
    assert text.index("drive_id") < text.index("count")


def test_manifest_output_is_independent_of_insertion_order(folder: Path, tmp_path: Path) -> None:
    items = [(f"f{i}.bin", entry(bytes([i]))) for i in (3, 1, 2)] + [("B.bin", entry(b"B")), ("a.bin", entry(b"a"))]
    first = files_sync.render_manifest(dict(items))
    second = files_sync.render_manifest(dict(reversed(items)))
    assert first == second
    assert first.index('["B.bin"]') < first.index('["a.bin"]')  # code-point order, not locale
    assert files_sync.write_manifest(folder, dict(items))
    assert not files_sync.write_manifest(folder, dict(reversed(items)))


def test_malformed_manifest_names_the_line(folder: Path) -> None:
    (folder / "files.toml").write_text('["a.bin"]\nsha256 = \n')
    with pytest.raises(SyncError, match="line 2"):
        files_sync.read_manifest(folder)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('sha256 = "abc"\nsize = 1\ndrive_id = "x"', "sha256"),
        (f'sha256 = "{"a" * 64}"\nsize = -1\ndrive_id = "x"', "size"),
        (f'sha256 = "{"a" * 64}"\nsize = 1\ndrive_id = ""', "drive_id"),
        (f'sha256 = "{"a" * 64}"\nsize = 1\ndrive_id = "x"\nratio = 1.5', "ratio"),
    ],
)
def test_manifest_values_are_validated(folder: Path, body: str, message: str) -> None:
    (folder / "files.toml").write_text(f'["a.bin"]\n{body}\n')
    with pytest.raises(SyncError, match=message):
        files_sync.read_manifest(folder)


@pytest.mark.parametrize("rel", ["../x", "/etc/passwd", "a/../b", "./a", ".hidden", "a/.git/x", "a//b", "a\\b", ""])
def test_path_validation_rejects_unsafe_paths(rel: str) -> None:
    with pytest.raises(ValueError):
        files_sync.check_path(rel)


def test_manifest_with_unsafe_path_is_refused(folder: Path) -> None:
    files_sync.write_manifest(folder, {"ok.bin": entry(b"x")})
    text = (folder / "files.toml").read_text().replace('"ok.bin"', '"../escape.bin"')
    (folder / "files.toml").write_text(text)
    with pytest.raises(SyncError, match=r"\.\."):
        files_sync.read_manifest(folder)


# Discovery and scanning


def test_find_folders_below_and_from_inside(tmp_path: Path, folder: Path) -> None:
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "files.toml").write_text("")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "files.toml").write_text("")
    files_sync.init(tmp_path / "app" / "data")
    assert files_sync.find_folders(tmp_path) == [tmp_path / "app" / "data", folder]
    (folder / "deep").mkdir()
    assert files_sync.find_folders(folder / "deep") == [folder]
    assert files_sync.find_folders(folder / "files.toml") == [folder]


def test_scan_skips_dotfiles_and_refuses_nesting_and_symlinks(folder: Path) -> None:
    (folder / ".DS_Store").write_bytes(b"x")
    (folder / ".cache").mkdir()
    (folder / ".cache" / "x.bin").write_bytes(b"x")
    (folder / "sub").mkdir()
    (folder / "sub" / "a.bin").write_bytes(b"a")
    assert list(files_sync.scan(folder)) == ["sub/a.bin"]
    (folder / "link.bin").symlink_to(folder / "sub" / "a.bin")
    with pytest.raises(SyncError, match="symlinks"):
        files_sync.scan(folder)
    (folder / "link.bin").unlink()
    (folder / "sub" / "files.toml").write_text("")
    with pytest.raises(SyncError, match="nested"):
        files_sync.scan(folder)


def test_status_classifies_files(folder: Path) -> None:
    (folder / "same.bin").write_bytes(b"same")
    (folder / "changed.bin").write_bytes(b"new content")
    (folder / "new.bin").write_bytes(b"new")
    files_sync.write_manifest(
        folder, {"same.bin": entry(b"same"), "changed.bin": entry(b"old"), "gone.bin": entry(b"gone")}
    )
    current = files_sync.state(folder)
    assert (current.missing, current.modified, current.new) == (["gone.bin"], ["changed.bin"], ["new.bin"])
    assert files_sync.status(folder).counts == {"missing": 1, "modified": 1, "new": 1}


# Push


def test_push_uploads_dedups_and_names_blobs(folder: Path, fake: FakeDrive) -> None:
    (folder / "icon-192.png").write_bytes(b"icon")
    (folder / "copy.png").write_bytes(b"icon")
    (folder / "model").write_bytes(b"weights")
    (folder / "data.tar.gz").write_bytes(b"tar")
    report = files_sync.push(folder)
    assert report.counts == {"uploaded": 3, "linked": 1}
    assert sorted(fake.uploads) == sorted(
        [f"copy.{sha(b'icon')[:8]}.png", f"model.{sha(b'weights')[:8]}", f"data.tar.{sha(b'tar')[:8]}.gz"]
    )
    entries = files_sync.read_manifest(folder)
    assert entries["icon-192.png"]["drive_id"] == entries["copy.png"]["drive_id"]
    assert entries["model"] == entry(b"weights", entries["model"]["drive_id"])

    before = (folder / "files.toml").read_text()
    again = files_sync.push(folder)
    assert again.counts == {} and again.lines == []
    assert (folder / "files.toml").read_text() == before


def test_push_reuses_existing_blob_by_sha256(folder: Path, fake: FakeDrive) -> None:
    fake.blobs["old"] = {"name": "anything", "data": b"x", "sha256": sha(b"x"), "parent": "folder1"}
    (folder / "x.bin").write_bytes(b"x")
    assert files_sync.push(folder).counts == {"linked": 1}
    assert fake.uploads == []
    assert files_sync.read_manifest(folder)["x.bin"]["drive_id"] == "old"


def test_push_drops_missing_and_keeps_unknown_keys(folder: Path, fake: FakeDrive) -> None:
    (folder / "a.bin").write_bytes(b"v2")
    files_sync.write_manifest(folder, {"a.bin": entry(b"v1", note="keep"), "gone.bin": entry(b"gone")})
    report = files_sync.push(folder)
    assert report.counts == {"dropped": 1, "uploaded": 1}
    entries = files_sync.read_manifest(folder)
    assert list(entries) == ["a.bin"]
    assert entries["a.bin"]["note"] == "keep" and entries["a.bin"]["sha256"] == sha(b"v2")


def test_push_dry_run_writes_nothing(folder: Path, fake: FakeDrive) -> None:
    (folder / "a.bin").write_bytes(b"a")
    files_sync.write_manifest(folder, {"gone.bin": entry(b"gone")})
    before = (folder / "files.toml").read_text()
    report = files_sync.push(folder, dry_run=True)
    assert report.counts == {"would drop": 1, "would upload": 1}
    assert fake.uploads == [] and (folder / "files.toml").read_text() == before


def test_failed_upload_keeps_previous_entry(folder: Path, fake: FakeDrive) -> None:
    (folder / "old.bin").write_bytes(b"changed")
    (folder / "fresh.bin").write_bytes(b"fresh")
    (folder / "ok.bin").write_bytes(b"ok")
    files_sync.write_manifest(folder, {"old.bin": entry(b"original", "keep-id")})
    fake.fail_upload = {"old.bin", "fresh.bin"}
    report = files_sync.push(folder)
    assert report.counts == {"failed": 2, "uploaded": 1}
    entries = files_sync.read_manifest(folder)
    assert sorted(entries) == ["ok.bin", "old.bin"]
    assert entries["old.bin"]["drive_id"] == "keep-id"


# Pull


def test_pull_restores_missing_and_modified(folder: Path, fake: FakeDrive) -> None:
    (folder / "sub").mkdir()
    (folder / "sub" / "a.bin").write_bytes(b"a")
    (folder / "b.bin").write_bytes(b"b")
    files_sync.push(folder)
    (folder / "sub" / "a.bin").unlink()
    (folder / "sub").rmdir()
    (folder / "b.bin").write_bytes(b"local edit")
    (folder / "extra.bin").write_bytes(b"extra")
    report = files_sync.pull(folder)
    assert report.counts == {"new": 1, "pulled": 1, "replaced": 1}
    assert (folder / "sub" / "a.bin").read_bytes() == b"a"
    assert (folder / "b.bin").read_bytes() == b"b"
    assert (folder / "extra.bin").read_bytes() == b"extra"
    assert files_sync.pull(folder).counts == {"new": 1}


def test_pull_rejects_hash_mismatch_without_touching_files(folder: Path, fake: FakeDrive) -> None:
    (folder / "a.bin").write_bytes(b"a")
    (folder / "b.bin").write_bytes(b"b")
    files_sync.push(folder)
    entries = files_sync.read_manifest(folder)
    fake.corrupt = {entries["a.bin"]["drive_id"], entries["b.bin"]["drive_id"]}
    (folder / "a.bin").unlink()
    (folder / "b.bin").write_bytes(b"local edit")
    report = files_sync.pull(folder)
    assert report.counts == {"failed": 2}
    assert not (folder / "a.bin").exists()
    assert (folder / "b.bin").read_bytes() == b"local edit"
    assert [p.name for p in folder.iterdir() if p.name.endswith(".part")] == []


def test_blob_name() -> None:
    digest = "9f2c41ab" + "0" * 56
    assert files_sync.blob_name("icon-192.png", digest) == "icon-192.9f2c41ab.png"
    assert files_sync.blob_name("model", digest) == "model.9f2c41ab"
    assert files_sync.blob_name("data.tar.gz", digest) == "data.tar.9f2c41ab.gz"


def test_app_folders_share_drive_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(drive.auth, "config_dir", lambda: tmp_path)
    calls: list[tuple[str, dict[str, Any]]] = []

    def request(method: str, url: str, *, params: Any = None, json_body: Any = None) -> dict[str, Any]:
        calls.append((method, params or {}))
        if method == "POST":
            return {"id": f"new-{json_body['appProperties']['google_mcp']}"}
        return {"files": []}

    monkeypatch.setattr(drive, "request", request)
    assert drive._default_folder() == "new-default_folder"
    assert drive.files_folder() == "new-files_folder"
    assert "value='files_folder'" in calls[2][1]["q"]
    stored = drive._stored_folders()
    assert stored == {"default_folder_id": "new-default_folder", "files_folder_id": "new-files_folder"}
