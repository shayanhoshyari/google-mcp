# Google MCP

A local MCP server for a personal Google account: Gmail, Google Calendar, and Google Drive through Google's public APIs. The code and OAuth tokens stay on your computer; requests go directly to Google over HTTPS.

What it can change:

- **Gmail** messages are read-only. Drafts can be created and edited, with attachments, but are never sent; you send them from Gmail. Google has no drafts-only permission, so the authorization technically allows sending, but no tool does it.
- **Unsubscribe**: one-click unsubscribe from mailing lists that support it sends a request to the sender's server, the only request that does not go to Google.
- **Calendar** tools create, update, delete, and RSVP to events. Changes to shared events and RSVPs can notify other people.
- **Drive** reads cover your whole Drive; uploads and updates only touch files this app created.

## Tools

- Gmail: `list_emails`, `read_email`, `read_thread`, `download_attachment`, `list_senders`, `unsubscribe_one_click`, `list_drafts`, `read_draft`, `create_draft`, `update_draft`
- Calendar: `list_calendars`, `list_events`, `get_event`, `create_event`, `update_event`, `delete_event`, `respond_to_event`
- Drive: `search_files`, `get_file`, `download_file`, `upload_file`, `update_file`

## More

- [INSTALL.md](INSTALL.md): Google Cloud setup, install, authorization, updates, local files, removal.
- [DESIGN.md](DESIGN.md): tool behavior, scopes, API calls, and security.
- [CHANGELOG.md](CHANGELOG.md): releases, including which ones add scopes.
