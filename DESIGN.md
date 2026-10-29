# Google MCP — Design

## 1. Purpose

A local, stdio MCP server for personal Gmail, Google Calendar, and Google Drive access. Gmail is read-only except drafts, which the server can create and edit (with attachments) but never send, and attachments of messages and drafts can be downloaded; the server can also send the RFC 8058 one-click unsubscribe request named in a message's own header; Calendar can manage events, and Drive can search, inspect, and download any file the user can see, while uploads and updates are limited to files the app itself created. The server calls Google's public Gmail, Calendar, and Drive REST APIs directly with delegated OAuth authorization. It is implemented in this repository and does not depend on a hosted MCP service or copy code from another MCP project.

The design follows `agents/mcp/microsoft_agent_v2`: readable per-service modules, a small shared API/auth layer, standalone installation, local token storage, and design documentation as the source of truth.

## 2. Architecture and installation

```text
MCP client → local google-mcp process → Gmail API / Calendar API / Drive API
                                 ↘ local OAuth state
                                 ↘ one-click unsubscribe POST (sender's server; unsubscribe_one_click only)
```

The server uses MCP stdio transport and is installed into a standalone venv at `~/.claude/google_mcp/venv/`. The source checkout is not needed after installation. The installer registers `google-mcp` with Claude Code, Cursor, Codex (if available), and OpenCode, preserving unrelated settings and MCP entries. It refuses invalid configuration; JSONC rewriting removes comments. See [INSTALL.md](INSTALL.md) for setup, registration paths, and maintenance.

Local configuration and credentials live beside the standalone venv in `~/.claude/google_mcp/config/`:

```text
~/.claude/google_mcp/config/
  client_secrets.json  # Desktop OAuth client credentials; mode 0600
  state.json           # OAuth access/refresh token; mode 0600
  drive.json           # ID of the app-created mcp_default Drive folder; mode 0600
```

All credentials and tokens remain local. Gmail, Calendar, and Drive API calls go directly from the local process to Google over HTTPS. The one request outside Google is `unsubscribe_one_click` (see [unsubscribe](#unsubscribe)), which carries no Google credentials.

## 3. Authentication

Use Google's OAuth 2.0 installed/desktop-app authorization-code flow with PKCE (S256), a random state value, the system browser, and an ephemeral loopback listener bound only to `127.0.0.1`. The OAuth client is a Desktop app; the callback URI uses the actual loopback port selected at runtime. The local callback checks state before accepting the authorization code.

The CLI provides:

- `google-mcp configure <client-secrets.json>` — copy the downloaded Desktop OAuth client JSON into the user config directory with restrictive permissions.
- `google-mcp auth` — run interactive OAuth and store the resulting tokens locally.
- `google-mcp status` — show the authorized account, token expiry, granted scopes, and any required scopes that are missing, without printing secrets.
- `google-mcp` — run the stdio MCP server.

Access tokens are refreshed silently while the refresh token is valid. On `invalid_grant`, missing credentials, or expired Testing-mode refresh token, tools return a clear instruction to run `google-mcp auth`. Never include token values in messages or logs.

For a personal Google account, configure the user's own Cloud OAuth project as External / Testing and add that Gmail address as a test user. Testing-mode refresh tokens for Gmail, Calendar, and Drive scopes expire after seven days. The expected private-use workflow is to run `google-mcp auth` again when that happens. Per-file `drive.file` grants belong to the Cloud project, so they survive that reauthorization. Each release that adds scopes needs those scopes added to the consent screen and one `google-mcp auth` run after upgrading: 0.2.0 (Drive scopes) and 0.3.0 (`gmail.compose`); 0.4.0 and 0.5.0 add none; Google Workspace Developer Preview membership is not required for this local implementation.

## 4. OAuth scopes

Request only these scopes:

| Scope | Purpose |
|---|---|
| `https://www.googleapis.com/auth/gmail.readonly` | Search, list, and read messages, threads, and drafts. |
| `https://www.googleapis.com/auth/gmail.compose` | Create and update drafts. |
| `https://www.googleapis.com/auth/calendar.calendarlist.readonly` | List the user's calendars. |
| `https://www.googleapis.com/auth/calendar.events` | Read and manage events, including create, update, delete, and invitation response. |
| `https://www.googleapis.com/auth/drive.readonly` | Search, read metadata, and download/export every file the user can see. |
| `https://www.googleapis.com/auth/drive.file` | Create files, and update/move/rename files the app created. No access to modify the user's existing files. |

Do not request `gmail.send`, `gmail.modify`, or `https://mail.google.com/`, and do not request `drive` (full access) or `drive.install`. The only write capabilities affect Gmail drafts, Calendar events, and app-created Drive files.

Google has no drafts-only scope: `gmail.compose` also permits sending mail and deleting drafts. The server enforces "drafts only, never send" by exposing no send or delete tool and calling only the draft create/update endpoints. The stored refresh token could still send mail if it were stolen, which is why it stays in a `0600` file in the local config directory.

## 5. Tools

### Gmail (read-only messages, draft writes)

| Tool | Parameters | Behavior |
|---|---|---|
| `list_emails` | `query?`, `max_results?`, `page_token?` | List inbox messages by default or search with Gmail query syntax. Return compact metadata and snippets. |
| `read_email` | `message_id` | Return headers, decoded plain-text body, and `attachments` (when any) for one message or draft message. If the message has only HTML, return readable text derived from the HTML, with link URLs kept. |
| `read_thread` | `thread_id`, `max_messages?` | Return messages in chronological order, with headers, readable bodies, and `attachments` (when any). |
| `list_senders` | `query?` (default `newer_than:90d -in:sent -in:chats`), `max_messages?` (1–2000, default 500) | Group matching messages by sender address with counts and unsubscribe methods. Read-only. See [unsubscribe](#unsubscribe). |
| `unsubscribe_one_click` | `message_id` | POST the RFC 8058 one-click unsubscribe to the `https` URL in that message's `List-Unsubscribe` header. See [unsubscribe](#unsubscribe). |
| `download_attachment` | `message_id`, `filename`, `part_id?`, `path?`, `overwrite?` (default false) | Save one attachment of a message or draft to local disk; return absolute path, byte size, and MIME type. See attachments below and the [path rules](#path-rules). |
| `list_drafts` | `query?`, `max_results?`, `page_token?` | List drafts (optionally filtered with Gmail query syntax) with `draft_id`, `message_id`, `thread_id`, sender, recipients, subject, snippet, and `has_attachments`. |
| `read_draft` | `draft_id` | Return the full normalized draft, including body and `attachments`. |
| `create_draft` | `body`, `to?`, `subject?`, `cc?`, `bcc?`, `reply_to_message_id?`, `attachments?` | Create a plain-text draft, attaching local files. `to` is required unless replying. Returns the normalized draft. |
| `update_draft` | `draft_id`, `to?`, `subject?`, `body?`, `cc?`, `bcc?`, `attachments?`, `remove_attachments?` | Change only supplied fields; omitted fields and existing attachments are kept and an empty recipient list clears that field. Returns the normalized draft. |

`to`, `cc`, and `bcc` are lists of addresses (`Name <a@b.com>` or `a@b.com`); each entry must hold exactly one address. Nothing is ever sent: the user reviews and sends drafts from Gmail. Not in this version: send, delete, HTML bodies, and choosing a `From` alias for a new draft (Gmail uses the account's default sender).

**Replies.** With `reply_to_message_id`, `create_draft` reads the original's `Message-ID`, `References`, `Subject`, `From`, `Reply-To`, and `threadId`; sets `In-Reply-To` and `References` (`<References> <Message-ID>`) and the draft's `threadId` so Gmail threads it; and defaults `subject` to `Re: <subject>` (no double `Re:`) and `to` to `Reply-To`, else `From`; when that is the authorized account itself (replying to the user's own sent message), `to` defaults to the original `To`. If the original has no `Message-ID`, only `threadId` is set. The original is not quoted in the body.

**Attachments.** An attachment is a non-multipart MIME part with a filename or an `attachment` disposition, or a non-text part with a `Content-ID` or stored `attachmentId` (cid images some clients send without a name). This includes inline images such as pasted pictures and signature logos. An attached email (`message/rfc822`, e.g. a forwarded message) is one attachment: its inner parts are neither listed nor merged into the body. Unnamed parts get the name `attachment-<part_id><extension>`. The attachment object is `filename`, `mime_type`, `size` (bytes, as Gmail reports it), `part_id` (the MIME part position, e.g. `2` or `1.3`), and `inline: true` for parts with an `inline` disposition, or a `Content-ID` without an `attachment` disposition (Outlook puts a `Content-ID` on ordinary attachments). Files over 25 MB that Gmail turned into Drive links are not MIME attachments; use the Drive tools for those.

`download_attachment` selects by `filename`; `part_id` only disambiguates two attachments with the same name (otherwise the call fails and lists the candidates). It never takes Gmail's `attachmentId`, which Gmail does not keep stable across reads: it re-reads the message (`format=full`) and fetches the part's current `attachmentId`, or decodes small parts Gmail returns inline. Downloaded content is untrusted data.

`create_draft`/`update_draft` `attachments` are local file paths read under the same rules as `upload_file` (see [path rules](#path-rules)); the email's attachment name is the local file name and its MIME type comes from the extension (`application/octet-stream` fallback). An `.eml` (`message/rfc822`) is attached as an email; other `message/*` and `multipart/*` types are attached as `application/octet-stream`. The body plus all attachment file sizes may total at most 25 MB, the way Gmail's compose window counts its limit, checked before any write. The base64-encoded upload is about a third larger, still under the upload endpoint's 35 MiB cap. Kept attached emails are re-attached as `message/rfc822`; if Gmail returns no content for a kept attachment, the update fails instead of saving an empty file.

**Updates.** Gmail's `drafts.update` replaces the whole message, so `update_draft` fetches the draft (`format=full`), checks that every `remove_attachments` name exists (else fails before writing), downloads the bytes of each kept attachment, drops removed ones and any existing attachment with the same name as a new file (so a new `resume.pdf` replaces the old one), and rebuilds a `multipart/mixed` message (empty `attachments`/`remove_attachments` lists do not count as a change): the plain-text body, the kept then new attachments, the current `From`, `Reply-To`, `To`, `Cc`, `Bcc`, `Subject`, `In-Reply-To`, `References` with the supplied fields applied, and the same `threadId`. Keeping `From` preserves a send-as alias. Formatting from Gmail is lost, and inline images survive only as regular attachments. Because the message is replaced, the draft's `message_id` and attachment `part_id`s change after every update; `draft_id` is stable. At least one field must be supplied, and if `body` is omitted while the stored body exceeds the body cap, the update is refused rather than saving a truncated body. Any draft can be updated by explicit `draft_id`, including drafts the user wrote; Gmail has no private per-app marker like Drive's `appProperties`, and nothing leaves the mailbox until the user sends it.

**Verified behavior.** Tested against the live API on 2026-10-22 with the personal account:

- Drafts save through the upload endpoint, and `threadId` in the JSON metadata part is honored: a reply draft lands in the original thread, with `In-Reply-To` set, and stays there after an update. A reply to the user's own message defaults `to` to that message's recipients.
- A 10 MB attachment saves and downloads byte-identical; a draft over 25 MB is refused before any write.
- `update_draft` keeps attachments on a body-only update, replaces a same-name file, removes by name, and keeps `From`. On a hand-written Gmail draft with a pasted image, the image survives as an attachment and the body becomes plain text. The draft's `message_id` changes on every update.
- Gmail marks pasted images in a forwarded message as `attachment` disposition with a `Content-ID`, so they are not flagged `inline`.
- A bounce (`multipart/report`) carries the original as an expanded `message/rfc822` part with no filename or `attachmentId`; it is listed as `attachment-<part_id>.eml` and downloads as a complete email through the raw message.
- `download_attachment` honors the path rules: default cache, explicit directory, refusal without `overwrite`, overwrite with it.

<a id="unsubscribe"></a>**Unsubscribe.** Bulk mail carries `List-Unsubscribe` (RFC 2369): one or more `<uri>` values, `https:` or `mailto:`. With `List-Unsubscribe-Post: List-Unsubscribe=One-Click` (RFC 8058), an HTTPS POST of `List-Unsubscribe=One-Click` to the `https` URI unsubscribes with no page to visit; this is what Gmail's own Unsubscribe button uses. The Gmail API has no unsubscribe endpoint.

`list_emails`, `read_email`, and `read_thread` request both headers and, when `List-Unsubscribe` yields a usable method, return `unsubscribe`: `one_click_url` (the first `https` URI, only when the one-click `List-Unsubscribe-Post` value is present, compared case-insensitively), `urls` (the other `http(s)` URIs with a host), and `mailto` (`to`, `subject`, `body` from the first `mailto:` URI with an address; query values are percent-decoded). Whitespace inside the angle brackets (line folding) is removed; URIs with other schemes and malformed values are ignored, and the field is absent when nothing usable is left.

`list_senders` pages `messages.list` (500 per page) up to `max_messages`, then reads each message's metadata on 8 worker threads, retrying a 429 or rate-limit 403 up to three times with backoff (1, 2, 4 s) and skipping messages deleted in between. Messages are grouped by lower-cased `From` address. Each row has `address`, `name` (the latest non-empty display name), `count`, `latest_date`, `latest_message_id`, up to three recent distinct `subjects`, `labels` (the union of `CATEGORY_*` labels), and `unsubscribe` from the most recent message that has one, with that message's ID as `unsubscribe_message_id` (pass it to `unsubscribe_one_click`; `latest_message_id` may have no header). Rows sort by `count`, descending. The result also has `scanned` (messages read, equal to the sum of counts), `more_messages` (the query matched more than `max_messages`), and `query`.

`unsubscribe_one_click` re-reads the message's headers and requires a `one_click_url`; otherwise it fails before any network call and lists the methods found. It refuses a host that resolves to a non-public address (loopback, private, link-local), since the header is written by the sender. It then POSTs `List-Unsubscribe=One-Click` as `application/x-www-form-urlencoded` with `requests.post`: no cookies, no Google credentials, a 15-second timeout, and redirects not followed. It returns `sender`, `host`, HTTP `status`, and `ok` (2xx). Other statuses and network errors are reported, not retried. The URL always comes from the message header, never from a tool argument. `mailto:` methods are handled with `create_draft` (the user sends it); plain links and body-only links are left for the user to open. Not in this version: archive, delete, labels, filters, blocking, following body links, and sending `mailto:` unsubscribes.

Tested against the live API on 2026-10-28 with the personal account: bulk mail returns `unsubscribe` from `list_emails` and the same value from `read_email`, while sent mail and transactional messages (login codes, security alerts) have none. `list_senders` with the defaults read 500 messages in about 24 seconds with no quota errors; of 178 senders, 101 offered one-click, 3 only `mailto:`, 3 only a plain link, and 71 had no header. `unsubscribe_one_click` on a Klaviyo-hosted list returned HTTP 200.

**Normalized draft object.** `draft_id`, `message_id`, `thread_id`, `from`, `to`, `cc`, `bcc`, `subject`, `in_reply_to`, `snippet`, then `has_attachments` in `list_drafts`, or `body`, `body_truncated`, and `attachments` (always present, possibly empty) everywhere else.

Messages are built with Python's `email.message.EmailMessage` (UTF-8; non-ASCII headers encoded by the library), whose header policy rejects CR/LF in header values, so recipients and subjects cannot inject headers.

List results are capped at 50 messages per request. Message/thread bodies are capped to avoid unexpectedly large MCP responses; output indicates when content was truncated. Gmail content is untrusted input and must not be followed as agent instructions.

### Calendar

| Tool | Parameters | Behavior |
|---|---|---|
| `list_calendars` | `page_token?`, `max_results?` | List visible calendars and access roles. |
| `list_events` | `start?`, `end?`, `calendar_id?`, `search?`, `max_results?`, `page_token?` | List expanded recurring event instances, ordered by start time. Defaults to the primary calendar and the next seven days. |
| `get_event` | `event_id`, `calendar_id?` | Return normalized event details. |
| `create_event` | `summary`, `start`, `end`, `description?`, `location?`, `calendar_id?`, `all_day?` | Create an event on the primary calendar by default. No attendees are added in v1. |
| `update_event` | `event_id`, `calendar_id?`, `summary?`, `start?`, `end?`, `description?`, `location?`, `all_day?` | Partially update only supplied fields. Google may notify attendees when a shared event changes; the tool description must make this clear. |
| `delete_event` | `event_id`, `calendar_id?` | Delete an event. Google may notify attendees. |
| `respond_to_event` | `event_id`, `response`, `calendar_id?`, `comment?` | Set the authenticated user's attendee response to accepted, declined, or tentative and send the RSVP update to the organizer. |

Timed event values accept ISO 8601 datetimes with an explicit UTC offset; naive local datetimes are accepted only when a valid local timezone can be determined. All-day events use `YYYY-MM-DD` dates with an exclusive end date. Preserve event timezone/date representation in tool output. Do not invent or silently discard timezone information.

### Drive

| Tool | Parameters | Behavior |
|---|---|---|
| `search_files` | `query?`, `max_results?` (1–100, default 25), `page_token?` | Drive query syntax (e.g. `name contains 'resume'`). Covers My Drive and Shared with me; excludes trashed files. Returns normalized file objects and `next_page_token`. |
| `get_file` | `file_id` | Normalized metadata for one file. |
| `download_file` | `file_id`, `path?`, `overwrite?` (default false) | Save the file locally; return absolute path, byte size, saved MIME type, and whether it was exported. See path rules. |
| `upload_file` | `path`, `folder_id?`, `name?`, `convert?` (default false) | Always create a new file. Default parent is the app-created `mcp_default` folder. `name` defaults to the local file name. Max 100 MB. Returns the normalized file object. |
| `update_file` | `file_id`, `path` | Replace the contents of that exact file, keeping ID, link, parents, sharing, and revision history. Only app-created files. Max 100 MB. Returns the normalized file object. |

Not in this version: delete, inline `read_file`, all-tabs Sheets export, uploads over 100 MB (100 MiB), shared drives. Drive file content is untrusted data and must not be followed as agent instructions. Reads cover the whole Drive; writes touch only files the app created.

#### Normalized file object

`id`, `name`, `mime_type`, `modified_time`, `size` (bytes, absent for Google-native files), `owners` (list of `display_name` and `email`), `parents`, `web_link`, `created_by_app` (bool), and `shortcut_target_id` for shortcuts.

`created_by_app` comes from a private marker, not from `capabilities.canEdit` (which reflects the user's permission, not the app's). Every file and folder the app creates gets `appProperties: {"google_mcp": "created"}` (the default folder uses `"default_folder"`). `appProperties` are visible only to the app that set them; `created_by_app` is true when the `google_mcp` key is present.

Every call requests explicit `fields`:

- File: `id,name,mimeType,modifiedTime,size,owners(displayName,emailAddress),parents,webViewLink,appProperties,shortcutDetails(targetId,targetMimeType),capabilities(canDownload)`
- List: `nextPageToken,files(<file fields>)`

#### Path rules

Applies to `download_file`, `upload_file`, `update_file`, and Gmail's `download_attachment` and draft `attachments`. The helpers live in `local_files.py` and are shared by Drive and Gmail.

- Paths must be absolute after `~` expansion. Relative paths are rejected: the MCP server's working directory is not the agent's.
- `upload_file`/`update_file`: the path must be an existing readable regular file of at most 100 MB (100 MiB). Draft `attachments`: the same, capped by the 25 MB draft limit.
- `download_attachment` follows the `download_file` rules below, with the default cache `$TMPDIR/google_mcp/gmail/<message_id>/<filename>` and `<filename>` the sanitized attachment name (fallback `attachment`).
- `download_file` without `path`: save to `$TMPDIR/google_mcp/<file_id>/<filename>`, always overwriting. This directory is the tool's own cache.
- `download_file` with `path`: if it is an existing directory or ends with `/`, save as `<path>/<filename>`; otherwise treat it as the full file path. Missing parent directories are created. The download is refused if the target exists unless `overwrite=true`.
- `<filename>` is the Drive name with `/`, `\`, and control characters replaced by `_` (fallback: file ID, also used for `.` and `..`; names are capped at 200 characters). For exports, the export extension (`.md`, `.csv`, `.txt`, `.png`) is appended unless the name already ends with it.
- Downloaded files are written with mode `0600`; created directories with `0700`. Content is written to a temporary file in the same directory, then renamed.

#### Download and export mapping

| Drive MIME type | Action | Saved as |
|---|---|---|
| `application/vnd.google-apps.document` | export `text/markdown`; if Google rejects it (HTTP 400 or `invalidExportFormat`), export `text/plain` | `.md` / `.txt` |
| `application/vnd.google-apps.spreadsheet` | export `text/csv` (first tab only) | `.csv` |
| `application/vnd.google-apps.presentation` | export `text/plain` | `.txt` |
| `application/vnd.google-apps.drawing` | export `image/png` | `.png` |
| `application/vnd.google-apps.shortcut` | error naming `shortcut_target_id`; the agent downloads the target | — |
| other `application/vnd.google-apps.*` (folder, form, site, …) | error: cannot be downloaded | — |
| anything else (PDF, .docx, images, text, …) | `alt=media` download as-is | original name |

#### Conversion on upload (`convert=true`)

| Local extension | Upload content type | Drive target type |
|---|---|---|
| `.md` | `text/markdown` | Google Doc |
| `.txt` | `text/plain` | Google Doc |
| `.docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | Google Doc |
| `.csv` | `text/csv` | Google Sheet |
| `.xlsx` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | Google Sheet |

Other extensions with `convert=true` fail validation before any API call. Without `convert`, the content type is guessed from the extension (`mimetypes`), falling back to `application/octet-stream`. When converting, a trailing convertible extension is stripped from the Drive name (Google-native files have no extension); other dots in the name are kept.

`update_file` can replace the contents of a converted Google Doc (verified; Sheets untested). If Google rejects it, the error says so and suggests `upload_file`.

#### `mcp_default` folder

- On the first `upload_file` without `folder_id`, find or create folder `mcp_default` at My Drive root with `appProperties: {"google_mcp": "default_folder"}`.
- Its ID is stored in `~/.claude/google_mcp/config/drive.json` (mode `0600`, written atomically like `state.json`). If the stored ID is missing, deleted, or trashed, search `appProperties has { key='google_mcp' and value='default_folder' } and trashed = false` first; create a new folder only if none exists.
- The folder is identified by its private marker, not its name. A hand-made folder named `mcp_default` is ignored (a second one is created); use `folder_id` to upload into any folder the user can write to.
- Uploading into a user-created folder via `folder_id` succeeds (see "Verified behavior"): `drive.file` limits modifying existing files, not creating new ones where the user can write.

#### Guard on `update_file`

`update_file` first reads the file's metadata and refuses, before uploading, any file without the `created_by_app` marker. Google's own 403/404 for files outside `drive.file` is mapped to the same message as a second line of defense.

#### Verified behavior

Tested against the live API on 2026-10-04 with the personal account:

- `search_files` pages correctly and finds files in My Drive and Shared with me. `get_file`, `download_file` (default cache path, explicit directory, explicit file path, refusal without `overwrite`, overwrite with it), PDF, Sheet (CSV), and Doc (Markdown) downloads work; a relative path is rejected.
- Doc Markdown export inlines images as base64 data at the end of the file, so a Doc with images can export to a multi-MB file.
- `upload_file` creates `mcp_default` at the My Drive root on first use, reuses it afterwards (ID cached in `drive.json`), and `convert=true` produces a Google Doc with the extension stripped from the name.
- `update_file` replaces the contents of an app-created `.md` file in place (same ID and link) and also of a converted Google Doc; the new content round-trips through export.
- `update_file` on a user-created file is refused by the client-side `created_by_app` guard before any upload. Google's own error for that case was not exercised, to avoid sending a write to a user's file.
- `upload_file` with the `folder_id` of a user-created folder succeeds and creates the new file there (marked `created_by_app`). The write boundary is therefore "never modify existing files the app did not create", not "never write into user folders".

## 6. API operations

### Gmail

| Operation | Google API |
|---|---|
| List/search messages | `GET /gmail/v1/users/me/messages` with `q`, `maxResults`, and `pageToken` |
| Read message headers/snippet | `GET /gmail/v1/users/me/messages/{id}?format=metadata` with `metadataHeaders` From, To, Subject, Date, List-Unsubscribe, List-Unsubscribe-Post |
| One-click unsubscribe (not Google) | `POST <one_click_url>` with body `List-Unsubscribe=One-Click` |
| Read full message | `GET /gmail/v1/users/me/messages/{id}?format=full` |
| Read thread | `GET /gmail/v1/users/me/threads/{id}?format=full` |
| Identify account | `GET /gmail/v1/users/me/profile` |
| Get attachment bytes | `GET /gmail/v1/users/me/messages/{id}/attachments/{attachmentId}` (`attachmentId` from a fresh `format=full` read) |
| Get an attached email | `GET /gmail/v1/users/me/messages/{id}?format=raw`, then walk the parsed MIME tree by `part_id` (Gmail expands attached emails into child parts with no `attachmentId`) |
| List drafts | `GET /gmail/v1/users/me/drafts` with `q`, `maxResults`, `pageToken`, then `GET /drafts/{id}?format=full` per draft (full, so `has_attachments` is exact) |
| Get draft | `GET /gmail/v1/users/me/drafts/{id}?format=full` |
| Create draft | `POST /upload/gmail/v1/users/me/drafts?uploadType=multipart` |
| Update draft | `PUT /upload/gmail/v1/users/me/drafts/{id}?uploadType=multipart` |

Draft saves use the media upload endpoint so multi-MB attachments do not travel as a base64 JSON string: a `multipart/related` body whose first part is JSON metadata (`{"id"?, "message": {"threadId"?}}`) and whose second is the `message/rfc822` MIME message (`google_api.multipart_upload`, with the same 401 retry as other requests).

Decode Gmail's base64url payload parts. Prefer `text/plain`; if unavailable, derive readable text from `text/html`. Links in the HTML keep their target as `[text](url)` (just the URL when the text is empty or is the URL itself; an image inside a link supplies its `alt` as text); only `http`, `https`, and `mailto` targets are kept, and links are shown, never followed. Attachment parts (see attachments in section 5) and attached emails are never part of the body.

### Calendar

| Operation | Google API |
|---|---|
| List calendars | `GET /calendar/v3/users/me/calendarList` |
| List events | `GET /calendar/v3/calendars/{calendarId}/events` with `singleEvents=true`, `orderBy=startTime`, optional `timeMin`, `timeMax`, `q`, and `pageToken` |
| Get event | `GET /calendar/v3/calendars/{calendarId}/events/{eventId}` |
| Create event | `POST /calendar/v3/calendars/{calendarId}/events` |
| Update event | `PATCH /calendar/v3/calendars/{calendarId}/events/{eventId}` with only supplied fields |
| Delete event | `DELETE /calendar/v3/calendars/{calendarId}/events/{eventId}` |
| Respond to invitation | `GET` event, then `PATCH` the attendee list preserving all attendees and changing only the authenticated user's response fields |

Use `sendUpdates=all` for update/delete/RSVP operations where notifications are appropriate. The response tool must first locate the current user in the event's attendee list and must fail without modifying the event if no matching self attendee exists.

### Drive

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

Every upload uses the resumable flow; Google documents the one-request `multipart`/`media` types for files up to 5 MB only. A fresh access token is obtained before starting a session; the session URL needs no retry-on-401 logic beyond one restart of the whole upload.

## 7. API client, errors, and output

`google_api.py` provides the authenticated HTTPS request helper, request timeout, JSON parsing, and consistent errors. API errors include the HTTP status and Google's safe error message/reason; they never include access or refresh tokens. Refresh and reauthorization errors are presented as actionable tool results.

All tools return compact JSON with stable field names rather than raw API resources. Pagination tokens are returned when Google supplies them. Cap tool result sizes and validate page sizes, event IDs, message IDs, file IDs, dates, time ranges, paths, enum values, and required fields before issuing API calls.

`GoogleAPIError` carries the HTTP status and Google's error reasons so service modules can map them. For Drive, errors become actionable messages that keep the HTTP status and Google's reason:

| Google error | Message |
|---|---|
| 403 insufficient authentication scopes (`ACCESS_TOKEN_SCOPE_INSUFFICIENT` / `insufficientPermissions`) | Drive access is not authorized yet; run `google-mcp auth`. |
| 403 `appNotAuthorizedToFile` / permission denied on write | The app can only modify files it created (`created_by_app`). |
| 403 `exportSizeLimitExceeded` | Export is over Google's 10 MB limit. |
| 403 `cannotDownloadFile` / `canDownload=false` | The owner disabled downloading for this file. |
| 404 | File not found or not visible to this account. |

`download_attachment` and draft attachments fail with a clear message, before any write, for an unknown filename (listing the available ones), an ambiguous filename without `part_id`, a `part_id` that does not match, an unknown `remove_attachments` name, or a draft over 25 MB.

For Gmail drafts, 403 insufficient scopes becomes "Gmail draft access is not authorized yet; run `google-mcp auth`." and a 404 on a request naming a draft ID becomes "Draft not found." `list_drafts` skips drafts deleted between listing and reading them.

`server.py` also catches `OSError` (missing file, permission denied, disk errors) and returns it as a tool error.

## 8. Development and documentation contract

- `DESIGN.md` and `src/google_mcp/instructions.md` define behavior and agent guidance.
- User docs are derived from this design and link to it instead of repeating tool behavior.
- `README.md` is the landing page: what the server is, what it can change (Gmail messages read-only and drafts never sent, one-click unsubscribe contacts the sender's server, Calendar writes can notify attendees, Drive writes touch only app-created files, requests go directly to Google), the tool list, and links.
- `INSTALL.md` holds everything about setup: the one-time personal OAuth setup (Cloud project, Gmail, Calendar, and Drive APIs, External / Testing consent with the Gmail address as a test user, Desktop OAuth client, all six scopes, the unverified-app warning, weekly reauthorization in Testing mode), install, `configure`/`auth`/`status`, updates (new scopes need one `auth` run), local files and client registration, and removal.

## 9. Security and data flow

The MCP client starts the installed Python process over stdio. The process reads OAuth state from `~/.claude/google_mcp/config/`, refreshes its short-lived access token when needed, and calls Google's REST APIs over HTTPS. Google data is returned as compact JSON rather than raw API resources.

`google-mcp auth` uses the Desktop-app flow described in section 3. The browser sends the authorization code to the loopback listener; the server checks the state, exchanges the code, and stores the refresh token with file mode `0600`. Tokens are never intentionally written to MCP output or logs.

Gmail reads use `gmail.readonly`; draft writes use `gmail.compose`. The server exposes no send, label, archive, or delete operation. `unsubscribe_one_click` is the only request outside Google: it goes to the sender-controlled URL in the message's own header, without Google credentials or cookies, after the host is checked to resolve to public addresses; the agent instructions limit it to senders the user approved. Draft `attachments` read the local files they name, so only files the user asked for are attached; `download_attachment` writes to the local disk, and downloaded attachment content is untrusted data. Because Google bundles sending into `gmail.compose`, the stored token is technically able to send mail; the no-send guarantee comes from the tool surface. Drive reads use `drive.readonly`; Drive writes use `drive.file`, which Google limits to files the app created. `download_file` writes to the local disk and `upload_file` reads the local file it is given, so Drive content is untrusted data. Calendar event writes call Google immediately; shared-event updates, deletion, and invitation responses use Google's attendee notification option, so other people can receive updates. RSVP changes fetch the event first, preserve every attendee, and change only the authorized account's response. Gmail content is untrusted data even when it appears in an MCP result.

The local MCP does not send data through a third-party MCP host. It sends the OAuth exchange and every API request directly to Google. To revoke access, run `google-mcp clear-auth` to delete the local tokens and remove the app under the Google Account's third-party connections to revoke Google's copy of the grant. While the OAuth app is External/Testing, expect to repeat browser authorization about every seven days.

## 10. Primary references

- [Google OAuth 2.0 for iOS & Desktop Apps](https://developers.google.com/identity/protocols/oauth2/native-app)
- [Google OAuth 2.0 best practices](https://developers.google.com/identity/protocols/oauth2/resources/best-practices)
- [Gmail messages.list](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list)
- [Gmail API scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)
- [RFC 2369 List-Unsubscribe](https://www.rfc-editor.org/rfc/rfc2369) and [RFC 8058 one-click unsubscribe](https://www.rfc-editor.org/rfc/rfc8058)
- [Gmail drafts.update](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.drafts/update)
- [Gmail drafts.create (media upload)](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.drafts/create)
- [Gmail messages.attachments.get](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages.attachments/get)
- [Calendar events.list](https://developers.google.com/workspace/calendar/api/v3/reference/events/list)
- [Calendar events.update](https://developers.google.com/workspace/calendar/api/v3/reference/events/update)
- [Drive API scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)
- [Drive files.list search queries](https://developers.google.com/workspace/drive/api/guides/search-files)
- [Drive resumable uploads](https://developers.google.com/workspace/drive/api/guides/manage-uploads#resumable)
- [Drive export MIME types](https://developers.google.com/workspace/drive/api/guides/ref-export-formats)
