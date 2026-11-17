# Google MCP — Binary Sync Plan

## Goal

Keep binary files that code needs on disk (assets, data, model files) out of a git repository, stored in the user's Drive, and get them back on any checkout with one command. The repository tracks only a small text manifest per folder of binaries. This is a CLI on the existing `google-mcp` entry point, not an MCP tool: it is run from a shell, by a person or an agent, in the repository it works on. It reuses the existing auth, Drive helpers, and scopes; no new scopes.

Nothing here knows about a particular repository: the folder layout, manifest name, and Drive layout below are this package's own convention, documented in `DESIGN.md`.

## Conventions

### Binary folder

A folder that holds only synced binaries (for example `assets/` beside the code that reads it). It contains:

- `.gitignore`, so git sees only the two text files:

  ```
  *
  !.gitignore
  !files.toml
  ```

- `files.toml`, the manifest.
- The binaries, at any depth below the folder. Dotfiles (`.DS_Store`, editor files) are never synced. Symlinks are refused. A `files.toml` inside another binary folder is an error: one folder owns its whole subtree.

### Manifest (`files.toml`)

```toml
# Synced with `google-mcp files`; run `google-mcp files pull` to download these files.

["icons/icon-192.png"]
sha256 = "9f2c…"
size = 18234
drive_id = "1AbC…"
```

- One table per file, keyed by its path relative to the folder, `/`-separated.
- Every write sorts the tables by that path (plain code-point order, so the same on every OS and locale), with fixed key order inside each table (`sha256`, `size`, `drive_id`, then unknown keys sorted). The output depends only on the entries, never on scan or upload order, so a push that changes nothing leaves the file byte-identical and a change shows as one block.
- `pull` reads only the manifest and never searches Drive.
- Read with `tomllib`; written by a small writer in the module (keys and values are plain strings and integers), so no new dependency. Unknown keys are kept as written; a malformed manifest is an error naming the file and line.

### Drive layout

- One folder, `google_mcp_files`, at My Drive root, marked `appProperties: {"google_mcp": "files_folder"}` and found the same way as `mcp_default` (ID cached in `drive.json` as `files_folder_id`, then a marker search, then create). The marker, not the name, identifies it.
- Each file is stored there once per distinct content, named `<stem>.<first 8 hex of sha256><ext>` after the local file it was first pushed from (`icon-192.9f2c41ab.png`; `model.9f2c41ab` for a file with no extension; `<ext>` is the last suffix only, so `data.tar.gz` becomes `data.tar.9f2c41ab.gz`). The name is for a person browsing Drive; the CLI never relies on it.
- Each blob carries `appProperties: {"google_mcp": "created", "sha256": "<full hex digest>"}`. Blobs are found by that property, so identical content is stored once across all folders and repositories, even under different local names.
- Append-only: the CLI only creates files. It never calls `update_file`, deletes, or trashes. Changing a file uploads a new blob; old blobs stay, so any past commit's manifest can still be pulled.

## Commands

All take an optional `path` (default `.`), resolved against the shell's working directory. Commands that scan look for every `files.toml` under `path`, skipping dot-directories and `node_modules`; `path` may also be a binary folder itself or a file inside one.

| Command | Network | Behavior |
|---|---|---|
| `google-mcp files init <dir>` | no | Create `<dir>` if needed and write the `.gitignore` and an empty `files.toml`. Refuses if either exists with other content. |
| `google-mcp files status [path]` | no | Per binary folder, list files that are `missing` (in the manifest, not on disk), `modified` (hash differs), `new` (on disk, not in the manifest). Exit 1 when anything is listed, 0 when all match. |
| `google-mcp files pull [path]` | Drive reads | Make the folder match the manifest: download every `missing` and `modified` file, overwriting local changes. Files already matching are not downloaded. `new` files are left alone and reported. |
| `google-mcp files push [path] [--dry-run]` | Drive reads and creates | Make the manifest match the folder: upload every `new` or `modified` file and update its entry, and drop the entries of `missing` files (their blobs stay in Drive). `--dry-run` prints the plan without uploading or writing. |

`init` and `status` are additions to the pull/push in the request: `init` so a new binary folder doesn't need its `.gitignore` typed by hand, `status` so an agent can check a checkout offline before deciding to pull.

Output is one line per file acted on (`pulled icons/icon-192.png (18 KB)`, `replaced …`, `dropped …`) and a summary line, to stdout; errors to stderr. Exit codes: 0 done, 1 some files failed, 2 usage or manifest error. A failure on one file doesn't stop the others; a manifest is written once per folder after its uploads, only with entries whose upload succeeded.

### Pull, per missing file

1. `GET /files/{drive_id}?alt=media`, streamed into a temporary file beside the target while hashing.
2. If the SHA-256 differs from the manifest, delete the temporary file and fail that entry.
3. Otherwise create parent directories and rename into place. Files get the default mode (umask, typically `0644`), not `0600` like `download_file`'s: these are project files other tools read, not private downloads. A new `local_files` helper takes the mode as a parameter; the existing private-write callers are unchanged.

Paths from the manifest are validated before any request: relative, no `..`, no absolute paths, no dot-components, and the resolved target stays inside the binary folder. A manifest is repository content, so it is treated as untrusted input.

### Push, per new or modified file

1. Hash the file; refuse files over the existing 100 MB upload cap.
2. Look for an existing blob: `GET /files` with `q = appProperties has { key='sha256' and value='<sha256>' } and '<folder_id>' in parents and trashed = false`. If found, reuse its ID (dedup across folders, and a rerun after a crash between upload and manifest write uploads nothing).
3. Otherwise upload with the existing resumable flow: `name = <stem>.<sha256[:8]><ext>`, `parents = [folder_id]`, `appProperties` with the `created` marker and `sha256`, content type guessed from the extension (`local_files.content_type`).
4. Check the returned `sha256Checksum` (requested in `fields`) against the local hash.
5. Record `sha256`, `size`, `drive_id` in the manifest.

Uploads run one at a time; a thread pool can come later if a folder with many files makes it slow.

## Code

- New module `src/google_mcp/files_sync.py`: manifest read/write, discovery, hashing, status, pull, push. Plain functions returning result records; the CLI prints them.
- `drive.py`: generalize `_default_folder()` into a helper that finds or creates an app folder by marker and `drive.json` key, used by both `mcp_default` and `google_mcp_files`. Expose small functions for "download blob to a stream" and "find blob by `sha256` in folder" so `files_sync.py` doesn't build Drive URLs itself.
- `__main__.py`: a `files` subcommand group with `init`, `status`, `pull`, `push`. Auth errors keep the existing message and exit code.
- `__main__.py`: a subcommand is required. Bare `google-mcp` prints help and exits 2 instead of starting the server; the server runs only as `google-mcp serve`. No fallback for the old bare form.
- `scripts/install.py`: register the server as `<venv python> -m google_mcp serve` in Claude Code, Cursor, OpenCode, and Codex (one `make install` rewrites existing entries).

## Tests (offline)

`tests/test_files_sync.py`, with Drive calls monkeypatched:

- Manifest round-trip, unknown keys kept, malformed file reported with its line.
- Entries inserted in different orders produce byte-identical files; a no-op push doesn't rewrite the manifest.
- Path validation rejects `..`, absolute, and dot paths.
- Discovery: nested binary folders are an error; dot-directories, `node_modules`, dotfiles skipped.
- `status` classifies missing, modified, new.
- `pull` downloads missing and modified files, skips matching ones, leaves new files alone, and rejects a hash mismatch without leaving a file (a modified file stays as it was).
- `push` reuses an existing blob found by `sha256`, uploads otherwise with the `<stem>.<hash8><ext>` name (with and without an extension), drops missing entries, writes nothing on `--dry-run`, and keeps a failed upload's previous entry (or none, for a new file).

## Docs

- `DESIGN.md`: new section for the CLI (conventions, commands, Drive layout, the append-only rule); §2 lists `files_folder_id` in `drive.json`; §3's CLI list gains `files` and lists `google-mcp serve` instead of bare `google-mcp` for the server; §6 Drive gains the blob search and the `sha256Checksum` field; §9 notes the CLI writes only inside the binary folders under the given path.
- `README.md`: one paragraph, "Binary sync CLI", linking to the design section.
- `INSTALL.md`: the registered command now ends in `serve`.
- `AGENTS.md`: the CLI only creates Drive files in its own folder; never update or delete there.
- `CHANGELOG.md`, `pyproject.toml`: 0.6.0, no new scopes. Breaking: the server needs `serve`; re-run `make install` to update client registrations.

## Test session (live)

0. `make install`. Check that Claude Code (`~/.claude.json`), Cursor, OpenCode, and Codex now register `-m google_mcp serve`, and that bare `google-mcp` prints help. Restart Claude Code and call one Drive tool to confirm the server starts.
1. `init` a scratch folder, add two small binaries and one identical copy under another name, `push`: two blobs uploaded with `<stem>.<hash8><ext>` names and `sha256` properties, three manifest entries, `google_mcp_files` created and its ID cached.
2. `push` again: nothing uploaded.
3. Delete the local files, `status` lists them missing, `pull` restores them byte for byte.
4. Edit one file, `pull` restores the manifest version. Edit it again, `push` uploads a new blob and the old one stays in Drive.
5. Delete the cached `files_folder_id`; `push` finds the folder by marker instead of creating a second.
6. Delete one local file, `push` drops its entry; its blob stays in Drive.

## Not in this version

Shared drives, other people pulling from your Drive (blobs aren't shared), deleting old blobs, files over 100 MB, parallel transfers, an MCP tool for sync.

## Results (2026-11-16)

Built as 0.6.0. `make test`: 65 passed (31 new in `tests/test_files_sync.py`); `make lint` passes.

0. `make install` registers `-m google_mcp serve` in Claude Code, Cursor, OpenCode, and Codex; bare `google-mcp` prints help and exits 2. The Claude Code restart is left to the user.
1. Three files, two contents: 2 uploaded, 1 linked by `sha256`; blobs named `copy.91bce5a3.png` and `model.3e153637`, marked `created_by_app`; `files_folder_id` cached beside `default_folder_id`. The shared blob takes the name of the file pushed first (`copy.png` sorts before `icon-192.png`).
2. Second push: `up to date`, manifest untouched.
3. All files deleted: `status` lists 3 missing (exit 1); `pull` restores them byte for byte, mode `0644`.
4. A local edit is replaced by `pull`; after another edit, `push` uploads `model.b52dbe9c` and `model.3e153637` stays in Drive.
5. With `files_folder_id` removed from `drive.json`, `push` finds the same folder by marker and caches it again.
6. A deleted file's entry is dropped; its blob stays. `--dry-run` reports `would upload` and makes no call.

The test blobs remain in `google_mcp_files`, as the CLI never deletes.
