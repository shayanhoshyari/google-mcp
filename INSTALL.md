# Install and maintenance

## One-time Google setup

You need your own Google Cloud OAuth client. This works with a personal `@gmail.com` account and does not require Google Workspace Developer Preview access.

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create or select a project.
2. In **APIs & Services → Library**, enable **Gmail API**, **Google Calendar API**, and **Google Drive API**.
3. Open **Google Auth Platform** (or **APIs & Services → OAuth consent screen**, depending on the current Console UI).
4. Configure the app for **External** users and leave it in **Testing** mode. Add your personal Gmail address under **Test users**.
5. Add these scopes to the consent configuration (what each is for: [DESIGN.md](DESIGN.md#4-oauth-scopes)):
   - `https://www.googleapis.com/auth/gmail.readonly`
   - `https://www.googleapis.com/auth/gmail.compose`
   - `https://www.googleapis.com/auth/calendar.calendarlist.readonly`
   - `https://www.googleapis.com/auth/calendar.events`
   - `https://www.googleapis.com/auth/drive.readonly`
   - `https://www.googleapis.com/auth/drive.file`
6. Open **Clients/Credentials**, create an **OAuth client ID**, choose **Desktop app**, and download its JSON file.
7. Keep that JSON private. Do not put it in this repository.

Google may show an unverified-app warning for your private Testing app. Continue only when the screen shows the Cloud project you created and the Gmail account you added as a test user. In Testing mode the authorization expires after seven days, so weekly reauthorization is expected.

## Install and authorize

From this directory:

```bash
make install
```

The installer creates a standalone environment at `~/.claude/google_mcp/venv/` and registers `google-mcp` with Claude Code, Cursor, Codex (if available), and OpenCode (see [local files](#local-files)). Other settings and MCP entries are preserved; invalid configuration is never overwritten. JSONC comments and trailing commas are accepted, but rewriting removes comments. The source checkout is not needed afterwards.

Then configure and authorize:

```bash
~/.claude/google_mcp/venv/bin/google-mcp configure ~/Downloads/client_secret_....json
~/.claude/google_mcp/venv/bin/google-mcp auth
~/.claude/google_mcp/venv/bin/google-mcp status
```

From this directory, `uv run google-mcp <command>` does the same and uses the same OAuth files. `auth` opens Google in your browser and listens briefly on a random `127.0.0.1` port for Google's callback. Restart your MCP client after installation and authorization. Re-run `auth` whenever Google says the authorization has expired.

## Updates

Re-run `make install`. OAuth files live under `~/.claude/google_mcp/config/`, beside the venv, and survive updates. When a release adds scopes (see [CHANGELOG.md](CHANGELOG.md)), add them to the consent screen and run `auth` once; until then `status` lists them under `missing_scopes` and the affected tools report that access is not authorized.

## Local files

- OAuth client: `~/.claude/google_mcp/config/client_secrets.json`
- OAuth state and refresh token: `~/.claude/google_mcp/config/state.json`
- Drive default folder ID: `~/.claude/google_mcp/config/drive.json`
- Standalone environment: `~/.claude/google_mcp/venv/`

The config directory and files are created with restrictive permissions.

| Client | Config | Entry |
|---|---|---|
| Claude Code | `~/.claude.json` | `mcpServers.google-mcp` |
| Cursor | `~/.cursor/mcp.json` | `mcpServers.google-mcp` |
| Codex | `~/.codex/config.toml` | `mcp_servers.google-mcp` |
| OpenCode | `~/.config/opencode/opencode.jsonc` or `opencode.json` | `mcp.google-mcp` |

OpenCode uses `opencode.jsonc` if present, otherwise `opencode.json`, and honors `XDG_CONFIG_HOME`.

## Removal

To remove local authorization only:

```bash
~/.claude/google_mcp/venv/bin/google-mcp clear-auth
```

To revoke Google's copy of the grant, remove the app under your Google Account's third-party connections. To uninstall completely, remove `google-mcp` from the client configs listed under [local files](#local-files) (`codex mcp remove google-mcp` for Codex), then remove `~/.claude/google_mcp/`.
