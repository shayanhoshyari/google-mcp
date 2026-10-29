# Changelog

## 0.5.2 - 2026-10-28

- HTML-only email bodies keep link targets as `[text](url)` instead of dropping them. Entities are decoded once, so URLs such as `?a=1&copy=2` are no longer mangled.

## 0.5.1 - 2026-10-28

- `list_senders` rows add `unsubscribe_message_id`, the message the `unsubscribe` value came from; `unsubscribe_one_click` now points at it instead of `latest_message_id`, which may have no header.
- Out-of-range `max_messages` in `list_senders` and `read_thread` is reported under its own name instead of `max_results`.

## 0.5.0 - 2026-10-28

- Unsubscribe: `list_emails`, `read_email`, and `read_thread` return a parsed `unsubscribe` field from `List-Unsubscribe` / `List-Unsubscribe-Post`.
- New `list_senders` groups recent mail by sender with counts, labels, and unsubscribe methods.
- New `unsubscribe_one_click` POSTs the RFC 8058 one-click unsubscribe to the URL in a message's own header. This is the server's only request outside Google. No new scopes.
- Add offline tests (`tests/`, `make test`) with `pytest` in the dev extra.

## 0.4.0 - 2026-10-22

- Gmail attachments: `read_email`, `read_thread`, and the new `read_draft` list attachments; the new `download_attachment` saves one to local disk.
- Drafts can attach local files (`attachments`, 25 MB total). `update_draft` keeps existing attachments, replaces one when a new file has the same name, and drops named ones with `remove_attachments`; it no longer refuses drafts with attachments.
- Drafts are saved through Gmail's media upload endpoint. No new scopes.
- Move the shared local path helpers out of `drive.py` into `local_files.py`.

## 0.3.0 - 2026-10-22

- Add Gmail drafts: `list_drafts`, `create_draft` (optionally threaded as a reply), and `update_draft`. Nothing is ever sent.
- Add the `gmail.compose` scope; run `google-mcp auth` once after upgrading.

## 0.2.0 - 2026-10-04

- Add Google Drive: `search_files`, `get_file`, `download_file` (exports Docs, Sheets, Slides, and Drawings), `upload_file`, and `update_file`.
- Drive reads cover the whole Drive; uploads and updates touch only files the app created (`drive.file`). Uploads default to an app-created `mcp_default` folder.
- Add `drive.readonly` and `drive.file` scopes; run `google-mcp auth` once after upgrading. `google-mcp status` now lists granted and missing scopes.
- Consolidate `src/DESIGN.md` and `HOW_IT_WORKS.md` into a root `DESIGN.md`.

## 0.1.0 - 2026-09-27

- Add local Desktop OAuth with PKCE and secure token storage.
- Add read-only Gmail message and thread tools.
- Add Google Calendar read, create, update, delete, and RSVP tools.
- Add standalone Claude Code/Cursor installation and personal-account setup documentation.
