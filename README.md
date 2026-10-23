# Google MCP

A local MCP server for a personal Google account. It reads Gmail (including downloading attachments) and writes Gmail drafts with attachments, reads or manages Google Calendar, and searches and downloads Google Drive files (uploading only files it created) through Google's public APIs. The code and OAuth tokens run on your computer; requests still go directly to Google over HTTPS.

Gmail messages are read-only. The MCP can create and edit drafts but never sends them; you review and send drafts from Gmail. Google has no drafts-only permission, so the authorization technically allows sending, but no tool does it. Calendar tools can create, update, delete, and RSVP to events. Updates to shared events and RSVPs can notify other people. Drive reads cover your whole Drive, but uploads and updates only touch files this app created.

## One-time Google setup

You need your own Google Cloud OAuth client. This works with a personal `@gmail.com` account and does not require Google Workspace Developer Preview access.

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create or select a project.
2. In **APIs & Services → Library**, enable **Gmail API**, **Google Calendar API**, and **Google Drive API**.
3. Open **Google Auth Platform** (or **APIs & Services → OAuth consent screen**, depending on the current Console UI).
4. Configure the app for **External** users and leave it in **Testing** mode. Add your personal Gmail address under **Test users**.
5. Add these scopes to the consent configuration:
   - `https://www.googleapis.com/auth/gmail.readonly`
   - `https://www.googleapis.com/auth/gmail.compose`
   - `https://www.googleapis.com/auth/calendar.calendarlist.readonly`
   - `https://www.googleapis.com/auth/calendar.events`
   - `https://www.googleapis.com/auth/drive.readonly`
   - `https://www.googleapis.com/auth/drive.file`
6. Open **Clients/Credentials**, create an **OAuth client ID**, choose **Desktop app**, and download its JSON file.
7. Keep that JSON private. Do not put it in this repository.

Google may show an unverified-app warning for your private Testing app. Continue only when the screen shows the Cloud project you created and the Gmail account you added as a test user. With these Gmail, Calendar, and Drive scopes, a Testing-mode authorization normally expires after seven days, so weekly reauthorization is expected.

## Install and authorize

From this directory:

```bash
make install
```

This creates a standalone environment at `~/.claude/google_mcp/venv/` and registers `google-mcp` with Claude Code, Cursor, Codex (if available), and OpenCode. See [INSTALL.md](INSTALL.md) for installer behavior, updates, and removal.

From this project directory, configure and authorize with `uv run`:

```bash
uv run google-mcp configure ~/Downloads/client_secret_....json
uv run google-mcp auth
uv run google-mcp status
```

These commands and the standalone MCP use the same OAuth files under `~/.claude/google_mcp/config/`. To run a command from outside the repository or verify the installed environment used by your MCP client, use the path printed by the installer:

```bash
~/.claude/google_mcp/venv/bin/google-mcp status
```

The auth command opens Google in your browser and listens briefly on a random `127.0.0.1` port for Google's callback. Restart your MCP client after installation and authorization. Re-run the `auth` command whenever Google says the authorization has expired.

Upgrading from 0.1.x (no Drive) or 0.2.x (no drafts) requires adding the new scopes to the consent screen (see the list above) and one extra `auth` run to grant them. Until then `status` lists them under `missing_scopes` and the affected tools report that access is not authorized.

## Available tools

- Gmail: `list_emails`, `read_email`, `read_thread`, `download_attachment`, `list_drafts`, `read_draft`, `create_draft`, `update_draft`
- Calendar: `list_calendars`, `list_events`, `get_event`, `create_event`, `update_event`, `delete_event`, `respond_to_event`
- Drive: `search_files`, `get_file`, `download_file`, `upload_file`, `update_file`

Event creation does not add attendees in this first version. All-day event end dates are exclusive: a one-day event on October 1 uses start `2026-10-01` and end `2026-10-02`.

### Gmail drafts and attachments

- `create_draft` makes a plain-text draft. Pass `reply_to_message_id` to reply in a thread; the subject then defaults to `Re: …` and the recipient to the original `Reply-To` or sender (or the original recipients when you reply to your own message).
- `create_draft` and `update_draft` attach local files by absolute path (`attachments`), up to 25 MB of files for the whole draft (counted like Gmail's compose window).
- `update_draft` changes only the fields you pass and keeps existing attachments. A new file with the same name as an attachment replaces it (handy for an updated resume); `remove_attachments` drops attachments by name. It rewrites the draft as plain text, so formatting added in Gmail (bold, links) is lost and pasted images become regular attachments. It keeps the sender, so send-as aliases survive.
- `read_email`, `read_thread`, and `read_draft` list attachments; `download_attachment` saves one by filename. Without a `path`, downloads go to `$TMPDIR/google_mcp/gmail/<message_id>/`; paths must be absolute. For a draft, use its current `message_id`, which changes after each update.
- No tool sends or deletes drafts.

### Drive behavior

- Search, metadata, and download cover everything you can see (My Drive and Shared with me). Google Docs download as Markdown (or plain text), Sheets as CSV (first tab only), Slides as text, and Drawings as PNG. Other files download as-is. Without a `path`, downloads go to `$TMPDIR/google_mcp/<file_id>/`; paths must be absolute.
- `upload_file` always creates a new file, by default in a folder named `mcp_default` at the root of My Drive. The app creates that folder itself on the first upload and recognizes it by a private marker, not its name, so a folder you make by hand with the same name is not used. Passing `folder_id` creates the new file in any folder you can write to. `convert=true` turns `.md`, `.txt`, `.docx` into a Google Doc and `.csv`, `.xlsx` into a Google Sheet.
- `update_file` replaces the contents of a file this app created, keeping its link and revision history. It refuses any other file.
- Files up to 100 MB can be uploaded. Delete, shared drives, and reading file contents inline are not supported.

## Local files

- OAuth client: `~/.claude/google_mcp/config/client_secrets.json`
- OAuth state and refresh token: `~/.claude/google_mcp/config/state.json`
- Drive default folder ID: `~/.claude/google_mcp/config/drive.json`
- Standalone environment: `~/.claude/google_mcp/venv/`

Client registration:

| Client | Config | Entry |
|---|---|---|
| Claude Code | `~/.claude.json` | `mcpServers.google-mcp` |
| Cursor | `~/.cursor/mcp.json` | `mcpServers.google-mcp` |
| Codex | `~/.codex/config.toml` | `mcp_servers.google-mcp` |
| OpenCode | `~/.config/opencode/opencode.jsonc` or `opencode.json` | `mcp.google-mcp` |

OpenCode uses `opencode.jsonc` if present, otherwise `opencode.json`, and honors `XDG_CONFIG_HOME`.

The config directory and files are created with restrictive permissions. See [DESIGN.md](DESIGN.md#9-security-and-data-flow) for the security and request flow.

## Manual test session

After setup, we will test it together in this order: narrow Gmail search and message/thread read; attachment download; create, update, and reply with disposable Gmail drafts, with and without attachments (delete them in Gmail afterwards); short Calendar range and one event read; create, update, verify, and delete a clearly named disposable event; optional RSVP with a disposable invitation; Drive search, download, upload, and update of disposable files; then explicit reauthorization. Report the prompt and exact error text if anything fails, and we will fix and retry that scenario.
