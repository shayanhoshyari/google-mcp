# Install and maintenance

Run `make install` from this directory. The installer creates `~/.claude/google_mcp/venv/` and registers `google-mcp` with Claude Code, Cursor, Codex (if available), and OpenCode. See [local files](README.md#local-files) for registration paths. Other settings and MCP entries are preserved; invalid configuration is never overwritten. JSONC comments and trailing commas are accepted, but rewriting removes comments.

Then run:

```bash
~/.claude/google_mcp/venv/bin/python -m google_mcp configure /path/to/client_secret.json
~/.claude/google_mcp/venv/bin/python -m google_mcp auth
```

Re-run `make install` to update the standalone environment. OAuth files live under `~/.claude/google_mcp/config/`, beside the venv, and survive updates. When an update adds OAuth scopes (0.2.0 adds `drive.readonly` and `drive.file`, and requires the Drive API enabled in the Cloud project; 0.3.0 adds `gmail.compose`, which must also be added to the consent screen), `google-mcp status` lists them under `missing_scopes`; run `auth` once more to grant them.

Restart your MCP client after installation and authorization.

To remove local authorization only:

```bash
~/.claude/google_mcp/venv/bin/python -m google_mcp clear-auth
```

To revoke Google's copy of the grant, remove the app under your Google Account's third-party connections. To uninstall completely, remove `google-mcp` from the client configs listed under [local files](README.md#local-files) (`codex mcp remove google-mcp` for Codex), then remove `~/.claude/google_mcp/`.
