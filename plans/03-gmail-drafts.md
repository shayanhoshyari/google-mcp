# Google MCP — Gmail Drafts Plan

Phase 1 (drafts, plain text, no attachments) shipped in 0.3.0; `DESIGN.md` describes it as built. Phase 2 (attachments) is at the end of this file.

## Goal

Let the MCP create Gmail drafts and edit existing drafts, so an agent can prepare an email or a reply for the user to review and send from Gmail. The MCP never sends mail: there is no send tool. Everything else in Gmail stays read-only (no labels, archive, or delete). Drafts go into `gmail.py` beside the read tools and reuse the existing auth, request helper, and body decoding.

## Scopes

| Scope | Purpose |
|---|---|
| `https://www.googleapis.com/auth/gmail.readonly` | Unchanged. Also covers `drafts.list` and `drafts.get`. |
| `https://www.googleapis.com/auth/gmail.compose` | New. Required by `drafts.create` and `drafts.update`. |

Google has no drafts-only scope. `gmail.compose` ("Manage drafts and send emails") also allows sending and deleting drafts, so "no sending" is enforced by the tool surface, not by Google: a leaked `state.json` could send mail as the user, where today it can only read. `gmail.compose` is a restricted scope like `gmail.readonly`, so the External / Testing setup and weekly reauthorization are unchanged. Existing tokens lack it: one `google-mcp auth` run after upgrading, and `status` lists it under `missing_scopes` until then. Do not request `gmail.send`, `gmail.modify`, or `https://mail.google.com/`.

## Tools

| Tool | Parameters | Behavior |
|---|---|---|
| `list_drafts` | `query?`, `max_results?` (1–50, default 10), `page_token?` | Read-only. List drafts (Gmail search syntax in `query`) with `draft_id`, `message_id`, `thread_id`, `to`, `cc`, `subject`, `snippet`. Needed to find the ID of a draft the user wrote in Gmail. |
| `create_draft` | `to?`, `subject?`, `body`, `cc?`, `bcc?`, `reply_to_message_id?` | Create a new plain-text draft. Returns the normalized draft. |
| `update_draft` | `draft_id`, `to?`, `subject?`, `body?`, `cc?`, `bcc?` | Change only the supplied fields of one draft, keep the rest. Returns the normalized draft. |

`to`, `cc`, `bcc` are lists of addresses (`"Name <a@b.com>"` or `a@b.com`). In `update_draft`, an omitted field is kept and an empty list clears it.

Not in this version: send, delete, HTML bodies, attachments, reading a draft's full body by draft ID (`read_email` with the draft's `message_id` already does that), `From` aliases (Gmail fills in the account's default sender).

### Replies (`reply_to_message_id`)

The draft must land in the original thread, so `create_draft`:

1. Reads the original message's `Message-ID`, `References`, `Subject`, `From`, `Reply-To` headers and `threadId` (`format=metadata`).
2. Sets `In-Reply-To: <Message-ID>`, `References: <References> <Message-ID>`, and the draft's `threadId`.
3. Defaults `subject` to `Re: <original subject>` (no double `Re:`) and `to` to the original `Reply-To`, else `From`.

It does not quote the original message in the body. Without a reply target, `to` is required.

### Updating a draft

Gmail's `drafts.update` replaces the whole message, so `update_draft`:

1. Fetches the draft (`format=full`).
2. Refuses, without writing, if the draft has attachments: rebuilding it would drop them.
3. Rebuilds a plain-text message from the current `To`, `Cc`, `Bcc`, `Subject`, `In-Reply-To`, `References`, and decoded body, with the supplied fields applied, and keeps the `threadId`, so replies stay threaded.
4. Requires at least one field to change.

Consequence: a draft written in Gmail with formatting (bold, links, signature images) becomes plain text after an update. The tool description says so. If `body` is omitted and the existing body was truncated at the 60 000-character cap, refuse rather than save a truncated body.

Any draft can be updated, including ones the user wrote by hand. Gmail has no equivalent of Drive's private `appProperties`, so limiting edits to app-created drafts would need a custom header or a local ID list; not worth it for drafts, since nothing leaves the mailbox until the user sends it. `update_draft` targets an explicit `draft_id`, like Calendar's explicit event IDs.

### Normalized draft object

`draft_id`, `message_id`, `thread_id`, `to`, `cc`, `bcc`, `subject`, `in_reply_to` (when a reply), `body`, `body_truncated`, `has_attachments`.

## API operations

| Operation | Google API |
|---|---|
| List drafts | `GET /gmail/v1/users/me/drafts` with `q`, `maxResults`, `pageToken`, then `GET /drafts/{id}?format=metadata` per draft |
| Get draft | `GET /gmail/v1/users/me/drafts/{id}?format=full` |
| Create draft | `POST /gmail/v1/users/me/drafts` with `{"message": {"raw", "threadId"?}}` |
| Update draft | `PUT /gmail/v1/users/me/drafts/{id}` with `{"id", "message": {"raw", "threadId"?}}` |

`raw` is a base64url RFC 2822 message built with Python's `email.message.EmailMessage` (UTF-8, non-ASCII headers encoded by the library). The header policy rejects CR/LF in header values, which blocks header injection through `to`/`subject`.

Errors: 403 `ACCESS_TOKEN_SCOPE_INSUFFICIENT` / `insufficientPermissions` → "Gmail draft access is not authorized yet; run `google-mcp auth`." 404 → "Draft not found."

## Files to change

- `src/google_mcp/auth.py`: add `gmail.compose` to `SCOPES`.
- `src/google_mcp/gmail.py`: the three functions, MIME builder, draft normalizer, error mapping. Module docstring stops saying read-only.
- `src/google_mcp/server.py`: tool definitions and dispatch. Descriptions say drafts are never sent and the user sends from Gmail; `update_draft` says it rewrites as plain text and drops formatting.
- `src/google_mcp/instructions.md`: Gmail is read-only except drafts; no sending; use `list_drafts`/`create_draft` IDs for `update_draft`; confirm the intended draft before editing one the user wrote.
- `DESIGN.md`: scopes (§4), Gmail tools (§5), API operations (§6), errors (§7), README contract (§8, six scopes), security (§9: the token can technically send).
- `README.md`, `AGENTS.md`: Gmail "read-only except drafts; never sends"; six consent-screen scopes; one reauth after upgrading.
- `CHANGELOG.md`, `pyproject.toml`: 0.3.0.

## Test session (live, personal account)

1. `google-mcp auth`; `status` shows `gmail.compose` granted.
2. `create_draft` to the user's own address; it appears in Gmail Drafts, not Sent.
3. `update_draft` changing only the subject; body and recipients are kept.
4. `create_draft` with `reply_to_message_id` of a disposable thread; in Gmail it shows inside that thread with `Re:` subject.
5. `update_draft` on a hand-written draft with formatting → becomes plain text (expected); on a draft with an attachment → refused, draft unchanged.
6. `list_drafts` finds all of them; the user deletes the test drafts in Gmail.
7. Header-injection check: a `subject` containing a newline is rejected before any API call.

## Phase 2: attachments (0.4.0)

### Goal

Attach local files to drafts (the main case: the agent attaches the user's resume to a reply), keep existing attachments when a draft is updated, and let the agent see and download attachments from received emails and drafts, so the user no longer downloads them by hand. Still no sending.

### Scopes

No new scopes. `messages.attachments.get` accepts `gmail.readonly`; draft writes already have `gmail.compose`.

### Tool changes

| Tool | Change |
|---|---|
| `read_email`, `read_thread` | Each message gets an `attachments` list (see the attachment object below). Bodies are unchanged. |
| `read_draft` (new) | `draft_id` → the full normalized draft: headers, body, `attachments`. Saves the detour through `read_email` and gives the agent a way to check a draft before editing it. |
| `list_drafts` | Adds `has_attachments` (no per-file list, to stay compact). |
| `create_draft` | New `attachments?`: list of absolute local file paths. |
| `update_draft` | New `attachments?` (paths to add) and `remove_attachments?` (filenames to drop). Existing attachments are kept by default. No more refusal for drafts with attachments. |
| `download_attachment` (new) | `message_id`, `filename`, `part_id?`, `path?`, `overwrite?` → saves one attachment to local disk; returns `path`, `size`, `mime_type`. Works for received messages and drafts (a draft is a message; use its current `message_id`). |

Draft-returning tools (`create_draft`, `update_draft`, `read_draft`) return the `attachments` list, so the agent and user can confirm the resume is actually attached.

### Attachment object

`filename`, `mime_type`, `size` (bytes, as Gmail reports it), `part_id` (the MIME part's position, e.g. `2` or `1.3`), and `inline: true` for images embedded with a `Content-ID` (e.g. pasted images, signature logos).

`download_attachment` selects by `filename`; `part_id` only disambiguates when two attachments share a name (otherwise the call fails and lists the candidates). It never takes Gmail's `attachmentId`: Gmail does not keep those IDs stable across reads of the same message, so the tool re-reads the message (`format=full`) and resolves the part fresh at download time.

Over-25 MB files that Gmail turned into Drive links are not MIME attachments and are not listed; use the Drive tools for those.

### Reading local files (draft attachments)

- Same rules as `upload_file`: absolute paths only (after `~` expansion), existing readable regular files. Only attach files the user asked for.
- MIME type from the file extension (`mimetypes`), falling back to `application/octet-stream`; the filename in the email is the local file's name.
- Limit: 25 MB for the whole draft (existing + new attachments + body), Gmail's sending limit; a bigger draft could not be sent anyway. Checked before any write, with an error naming the total.

### Updating a draft with attachments

`update_draft` still rebuilds the full message (Gmail's update replaces it), now as `multipart/mixed`:

1. Fetch the draft (`format=full`) and collect its attachment parts.
2. Download each kept part's bytes (inline `body.data`, or `messages.attachments.get` with the part's current `attachmentId`), keeping filename and MIME type (`Content-ID` is dropped; the body is plain text).
3. Drop parts named in `remove_attachments` (each name must match an existing attachment, else fail before writing).
4. Add new files. A new file with the same name as an existing attachment replaces it (the "attach my updated resume" case).
5. Rebuild: plain-text body + attachments, plus the preserved headers (`From`, `Reply-To`, `To`, `Cc`, `Bcc`, `Subject`, `In-Reply-To`, `References`) and `threadId`.

Inline images survive as files but are no longer shown inline, since the body is plain text; the tool description says so. Everything else from phase 1 holds: formatting is lost, `From` aliases are kept.

Because Gmail replaces the message, the draft's `message_id` (and its attachments' `part_id`s) change after every update. `draft_id` is stable; the returned draft carries the new `message_id`.

### Downloading

- Default location `$TMPDIR/google_mcp/gmail/<message_id>/<filename>`, always overwritten (the tool's cache), like Drive downloads.
- Explicit `path`: same rules as `download_file` (directory or full path, missing parents created, refuses to overwrite without `overwrite=true`).
- Files `0600`, directories `0700`, written to a temporary file and renamed. Filenames sanitized like Drive names.
- Downloaded content is untrusted data, never instructions.

To share these rules, move the path helpers out of `drive.py` (`_absolute`, `_safe_name`, `_target`, `_make_directories`, atomic write, `_local_file`, `_content_type`) into a small `local_files.py` used by both modules; the Drive cache stays at `$TMPDIR/google_mcp/<file_id>/`.

### Saving large drafts

The JSON `raw` field in a normal request is fine for text but awkward for multi-MB attachments. Save drafts through the media upload endpoint: `POST|PUT /upload/gmail/v1/users/me/drafts[/{id}]?uploadType=multipart` with a JSON part (`{"id"?, "message": {"threadId"?}}`) and a `message/rfc822` part holding the MIME message. Use it for every save, so there is one code path. Verify in the live test that `threadId` from the JSON part is honored and that a ~10 MB draft saves.

### Files to change

- `src/google_mcp/local_files.py` (new): shared path, naming, and atomic-write helpers moved out of `drive.py`.
- `src/google_mcp/drive.py`: use `local_files`; no behavior change.
- `src/google_mcp/gmail.py`: attachment listing, `read_draft`, `download_attachment`, multipart builder, attachment preservation, upload-endpoint save.
- `src/google_mcp/google_api.py`: a helper for the multipart (related) upload request, with the same 401 retry and error handling.
- `src/google_mcp/server.py`, `instructions.md`: new tools and parameters; descriptions say attachments are read from local paths the user names, downloads write to disk, and nothing is sent.
- `DESIGN.md`, `README.md`, `CHANGELOG.md`, `pyproject.toml` (0.4.0).

### Test session (live)

1. `read_email` on a message with a PDF and a pasted image lists both (image `inline: true`); `download_attachment` saves the PDF to the default cache and to an explicit path; a second explicit download is refused without `overwrite`.
2. `create_draft` replying to a disposable thread with the resume attached; Gmail shows it in the thread with the attachment.
3. `update_draft` changing only the body: the attachment is still there. Then attach a newer file with the same name: replaced, not duplicated. Then `remove_attachments`.
4. `update_draft` on a hand-written draft with a pasted image: image kept as an attachment.
5. A draft over 25 MB is refused before any write; a ~10 MB one saves.
6. `read_draft` and `download_attachment` with the draft's current `message_id` after an update.
