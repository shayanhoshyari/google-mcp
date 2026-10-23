# Google MCP development notes

`DESIGN.md` is the source of truth. Keep OAuth scopes, tool behavior, and user documentation aligned with it.

Use `uv` for the environment. Run `make dev` after dependency changes and `make lint` before handing off code changes. Credentials and OAuth state belong only in `~/.claude/google_mcp/config/`; never add them to the repository or print token values.

Gmail messages remain read-only; the only Gmail writes are drafts (`create_draft`, `update_draft`, including their attachments). Never add a tool that sends mail or deletes drafts, and never request `gmail.send`, `gmail.modify`, or `https://mail.google.com/`. Calendar mutations must target explicit event IDs and tool descriptions must mention when Google can notify other attendees. Drive reads may cover the whole Drive, but Drive writes (`upload_file`, `update_file`) may only create files or modify files the app created (`created_by_app`); never request the full `drive` scope.

This folder is mirrored to the public repo `shayanhoshyari/google-mcp`; xiaoverse is the source of truth. `make publish` pushes the committed state of this folder as one snapshot commit (no monorepo history). Keep everything here safe to publish: no personal emails, IDs, or job-search details, including in `plans/` and tests.
