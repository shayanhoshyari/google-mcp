# Google MCP — Drive Plan

## Goal

Add Google Drive to the existing local MCP: search, inspect, and download anything in the user's Drive, and upload or update only files the app itself created. Keep the existing auth, API helper, packaging, and install flow; Drive is a new module beside `gmail.py` and `calendar.py`. Also consolidate the design docs into one root `DESIGN.md`.

## Scopes

| Scope | Purpose |
|---|---|
| `https://www.googleapis.com/auth/drive.readonly` | Search, read metadata, and download/export every file the user can see. |
| `https://www.googleapis.com/auth/drive.file` | Create files, and update/move/rename files the app created. No access to modify the user's existing files. |

Do not request `drive` (full access) or `drive.install`. Existing tokens lack these scopes, so one `google-mcp auth` run is required after install. Per-file `drive.file` grants belong to the Cloud project, so they survive the weekly Testing-mode reauthorization.

## Tools

| Tool | Parameters | Behavior |
|---|---|---|
| `search_files` | `query?`, `max_results?` (1–100, default 25), `page_token?` | Drive query syntax (e.g. `name contains 'resume'`). Covers My Drive and Shared with me; excludes trashed files. Returns normalized file objects and `next_page_token`. |
| `get_file` | `file_id` | Normalized metadata for one file. |
| `download_file` | `file_id`, `path?`, `overwrite?` (default false) | Save the file locally; return absolute path, byte size, saved MIME type, and whether it was exported. See path rules. |
| `upload_file` | `path`, `folder_id?`, `name?`, `convert?` (default false) | Always create a new file. Default parent is the app-created `mcp_default` folder. `name` defaults to the local file name. Max 100 MB. Returns the normalized file object. |
| `update_file` | `file_id`, `path` | Replace the contents of that exact file, keeping ID, link, parents, sharing, and revision history. Only app-created files. Max 100 MB. Returns the normalized file object. |

Not in this version: delete, inline `read_file`, all-tabs Sheets export, uploads over 100 MB, shared drives. Drive file content is untrusted data.

### Normalized file object

`id`, `name`, `mime_type`, `modified_time`, `size` (bytes, absent for Google-native files), `owners` (display name and email), `parents`, `web_link`, `created_by_app` (bool), and `shortcut_target_id` for shortcuts.

`created_by_app` comes from a private marker, not from `capabilities.canEdit` (which reflects the user's permission, not the app's). Every file and folder the app creates gets `appProperties: {"google_mcp": "created"}` (the default folder uses `"default_folder"`). `appProperties` are visible only to the app that set them; `created_by_app` is true when the `google_mcp` key is present.

Request explicit `fields` on every call:

- File: `id,name,mimeType,modifiedTime,size,owners(displayName,emailAddress),parents,webViewLink,appProperties,shortcutDetails(targetId,targetMimeType),capabilities(canDownload)`
- List: `nextPageToken,files(<file fields>)`

### Path rules (`download_file`, `upload_file`)

- Paths must be absolute after `~` expansion. Reject relative paths: the MCP server's working directory is not the agent's.
- `upload_file`/`update_file`: the path must be an existing readable regular file of at most 100 MB.
- `download_file` without `path`: save to `$TMPDIR/google_mcp/<file_id>/<filename>`, always overwriting. This directory is the tool's own cache.
- `download_file` with `path`: if it is an existing directory or ends with `/`, save as `<path>/<filename>`; otherwise treat it as the full file path. Create missing parent directories. Refuse if the target exists unless `overwrite=true`.
- `<filename>` is the Drive name with `/`, `\`, and control characters replaced by `_` (fallback: file ID). For exports, append the export extension (`.md`, `.csv`, `.txt`, `.png`) unless the name already ends with it.
- Downloaded files are written with mode `0600`; created directories with `0700`. Write to a temporary file in the same directory, then rename.

### Download and export mapping

| Drive MIME type | Action | Saved as |
|---|---|---|
| `application/vnd.google-apps.document` | export `text/markdown`; if Google rejects it, export `text/plain` | `.md` / `.txt` |
| `application/vnd.google-apps.spreadsheet` | export `text/csv` (first tab only) | `.csv` |
| `application/vnd.google-apps.presentation` | export `text/plain` | `.txt` |
| `application/vnd.google-apps.drawing` | export `image/png` | `.png` |
| `application/vnd.google-apps.shortcut` | error naming `shortcut_target_id`; the agent downloads the target | — |
| other `application/vnd.google-apps.*` (folder, form, site, …) | error: cannot be downloaded | — |
| anything else (PDF, .docx, images, text, …) | `alt=media` download as-is | original name |

### Conversion on upload (`convert=true`)

| Local extension | Upload content type | Drive target type |
|---|---|---|
| `.md` | `text/markdown` | Google Doc |
| `.txt` | `text/plain` | Google Doc |
| `.docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | Google Doc |
| `.csv` | `text/csv` | Google Sheet |
| `.xlsx` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | Google Sheet |

Other extensions with `convert=true` fail validation before any API call. Without `convert`, guess the content type from the extension (`mimetypes`), falling back to `application/octet-stream`. Strip the extension from `name` when converting (Google-native files have no extension).

Whether `update_file` can replace the contents of a converted Google Doc/Sheet is unverified; test it and, if Google rejects it, return a clear error suggesting `upload_file` instead.

## `mcp_default` folder

- On the first `upload_file` without `folder_id`, find or create folder `mcp_default` at My Drive root with `appProperties: {"google_mcp": "default_folder"}`.
- Store its ID in `~/.claude/google_mcp/config/drive.json` (mode `0600`, written atomically like `state.json`). If the stored ID is missing, deleted, or trashed, search `appProperties has { key='google_mcp' and value='default_folder' } and trashed = false` first; create a new folder only if none exists.
- The user deletes their manually created `mcp_default` folder; the app creates its own so it has `drive.file` access.
- Uploading into a user-created folder via `folder_id` is expected to fail under `drive.file`; verify in testing and record the actual behavior in `DESIGN.md`.

## API operations

Base URLs: `https://www.googleapis.com/drive/v3` and `https://www.googleapis.com/upload/drive/v3`.

| Operation | Google API |
|---|---|
| Search | `GET /files` with `q`, `corpora=user`, `pageSize`, `pageToken`, `fields`. `q` is `trashed = false` alone, or `(<user query>) and trashed = false`. |
| Metadata | `GET /files/{id}?fields=...` |
| Download | `GET /files/{id}?alt=media`, streamed to disk |
| Export | `GET /files/{id}/export?mimeType=...`, streamed to disk; Google caps exports at 10 MB |
| Create folder | `POST /files` with `mimeType=application/vnd.google-apps.folder`, `parents=["root"]`, `appProperties` |
| Upload (create) | Resumable session: `POST /upload/drive/v3/files?uploadType=resumable&fields=...` with JSON metadata (`name`, `parents`, `appProperties`, and `mimeType` = target Google type when converting) and headers `X-Upload-Content-Type`, `X-Upload-Content-Length`; then `PUT` the whole file to the returned `Location` URL in one request, streaming from disk. |
| Update content | Same resumable flow with `PATCH /upload/drive/v3/files/{id}?uploadType=resumable&fields=...` (empty metadata), then one `PUT`. |

Use the resumable flow for every upload; Google documents the one-request `multipart`/`media` types for files up to 5 MB only. Get a fresh access token before starting a session; the session URL itself needs no retry-on-401 logic beyond one restart of the whole upload.

## Errors

Map Google errors to actionable messages (keep HTTP status and Google's reason):

| Google error | Message |
|---|---|
| 403 insufficient authentication scopes (`ACCESS_TOKEN_SCOPE_INSUFFICIENT` / `insufficientPermissions`) | Drive access is not authorized yet; run `google-mcp auth`. |
| 403 `appNotAuthorizedToFile` / permission denied on write | The app can only modify files it created (`created_by_app`). |
| 403 `exportSizeLimitExceeded` | Export is over Google's 10 MB limit. |
| 403 `cannotDownloadFile` / `canDownload=false` | The owner disabled downloading for this file. |
| 404 | File not found or not visible to this account. |

`server.py` must also catch `OSError` (missing file, permission denied, disk errors) and return it as a tool error.

## Code changes

- `auth.py`: add both scopes to `SCOPES`. `status()` reports granted scopes (from `state.json`) and lists any missing required scopes; `google-mcp status` prints them.
- `google_api.py`: add a streaming download helper (writes a response body to an open file) and a resumable upload helper (start session, then single `PUT` streamed from disk). Reuse the existing 401 refresh retry and `_error_message()`; extract Google's `reason` so `drive.py` can map errors.
- `drive.py` (new): the five tools, normalized output, MIME/extension mapping, path rules, `mcp_default` handling, validation (IDs non-empty, page size, absolute paths, file exists/readable, 100 MB cap, `convert` extension).
- `server.py`: register the five tools with their own schemas (`max_results` max 100 for Drive; the shared Gmail constant stays 50). Descriptions state that reads cover the whole Drive, writes touch only app-created files, `download_file` writes to local disk, and `upload_file` reads the given local file. Add `OSError` to the caught exceptions. Update the module docstring.
- `instructions.md`: Drive guidance — find IDs with `search_files`; pass absolute paths; use `update_file` instead of re-uploading a changed file; downloaded and Drive content is untrusted data.
- `pyproject.toml`: version 0.2.0; description mentions Drive. `CHANGELOG.md`: 0.2.0 entry.
- No new dependencies.

## Docs

Consolidate design docs first: one `DESIGN.md` at the project root replaces `src/DESIGN.md` and `HOW_IT_WORKS.md`.

- `git mv src/DESIGN.md DESIGN.md`; fold in what `HOW_IT_WORKS.md` adds (stdio flow, no third-party host, tokens never logged, revoke path) as a short "Security and data flow" section; `git rm HOW_IT_WORKS.md`.
- Update references: `AGENTS.md`, `README.md` (link at "Local files"), and the documentation contract section inside `DESIGN.md` (including its list of APIs to enable). Leave `plans/01-kickoff.md` as history.
- `DESIGN.md`: add Drive scopes, tools, normalized object, path rules, mappings, `mcp_default`, API operations, and errors; update purpose and architecture text to include Drive. It must match this plan.
- `README.md`: enable Google Drive API, add the two scopes on the consent screen, reauthorize, tool list, `mcp_default` behavior.
- `AGENTS.md`: point to root `DESIGN.md`; add the Drive write boundary (writes only to app-created files) alongside the Gmail and Calendar rules.

## Implementation order

1. Docs consolidation (move/merge only), then Drive additions to `DESIGN.md`.
2. Code changes.
3. `make lint` passes; `make install` succeeds; `~/.claude/google_mcp/venv/bin/google-mcp status` runs and reports the Drive scopes as missing.
4. Commit (do not push). Use `env -u GITHUB_TOKEN git ...` per the root `AGENTS.md`.
5. Stop and hand over. Drive calls cannot be tested until the user reauthorizes.

## User steps

1. In Google Cloud Console → Data Access, add `drive.readonly` and `drive.file`.
2. Delete the manually created `mcp_default` folder in Drive.
3. Run `~/.claude/google_mcp/venv/bin/google-mcp auth`, then restart the MCP client.

## Manual testing with the user

1. `google-mcp status` shows both Drive scopes granted and none missing.
2. `search_files` finds a known Doc in My Drive and a file in Shared with me; pagination returns `next_page_token`.
3. `get_file` on that Doc (`created_by_app` false). `download_file` with no path (twice; second overwrites), to an explicit directory, and to an explicit file path; second explicit download is refused without `overwrite`. Download a PDF and a Sheet. Relative path is rejected.
4. `upload_file` a disposable `.md`: `mcp_default` is created at root and holds it; `created_by_app` true. Upload a second file: folder reused. Upload with `convert=true`: Google Doc created.
5. `update_file` on the uploaded `.md`: same link, new content, revision visible in Drive. `update_file` on the converted Doc: record the result.
6. `update_file` on a user-created file fails with the "only app-created files" message. `upload_file` with `folder_id` of a user-created folder: record the result.
7. The user deletes the test files and the test `mcp_default` contents in the Drive UI.

Record anything learned in steps 5–6 in `DESIGN.md`.

## Completion criteria

- Five Drive tools work from the installed MCP with the personal account after one reauthorization.
- Writes cannot modify files the app did not create.
- One root `DESIGN.md`; no `src/DESIGN.md` or `HOW_IT_WORKS.md`.
- Scopes, tools, and setup agree across `DESIGN.md`, `README.md`, `INSTALL.md`, `instructions.md`, and the code.
