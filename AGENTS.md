# Google MCP development notes

`DESIGN.md` is the source of truth: change it first, then the code, then `src/google_mcp/instructions.md` and the user docs (`README.md`, `INSTALL.md`), which link to it instead of repeating it.

Use `uv`. Run `make dev` after dependency changes, and `make lint` and `make test` (offline, no Google calls) before handing off. Credentials and OAuth state belong only in `~/.claude/google_mcp/config/`; never add them to the repository or print token values.

Do not widen what the server can do without the user asking: no sending mail, deleting, or new scopes, and no requests outside Google beyond `unsubscribe_one_click`. The limits are in [DESIGN.md](DESIGN.md#4-oauth-scopes) and [security](DESIGN.md#9-security-and-data-flow).

This folder is mirrored to the public repo `shayanhoshyari/google-mcp` by `make publish`; xiaoverse is the source of truth. Keep everything here safe to publish: no personal emails, IDs, or job-search details, including in `plans/` and tests.
