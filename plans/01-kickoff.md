# Google MCP — Kickoff Plan

## Goal

Create an original, local MCP server for a personal Google account, following the structure and design-first workflow of `agents/mcp/microsoft_agent_v2`. Use the `aaronsb/google-workspace-mcp` repository as a reference for setup and behavior only; use Google's API and OAuth documentation as the source of truth. Do not copy its implementation.

The server will run over stdio for Claude Code and call Gmail and Calendar APIs directly. OAuth credentials and user tokens will remain in local configuration/state files; API requests will go to Google.

## Initial scope

- Gmail: search/list messages and read individual messages or threads. Keep Gmail read-only in the first version: no sending, drafting, labeling, archiving, or deletion.
- Calendar: list and retrieve events, create and update events, delete events, and respond to invitations. Add availability/free-busy only if it fits cleanly in the initial tool set.
- Support one personal account initially. Keep account and auth code structured so multi-account support can be added later without changing tool behavior.
- Use explicit date/time and timezone handling, and return compact, normalized objects rather than raw Google API payloads.

## Design constraints

- `src/DESIGN.md` is the behavior and architecture source of truth; implement code from it.
- Use Google's OAuth 2.0 flow for installed/desktop applications with PKCE and a localhost callback. Request only the scopes required by the selected tools.
- Keep tokens in a user-local state directory with restrictive file permissions. Never store OAuth secrets or tokens in the repository.
- A personal Gmail account can be added as a test user to an OAuth app in External / Testing mode. Document the unverified-app prompt and the seven-day refresh-token expiry for Testing mode; explain that reauthorization is expected and that Google Workspace Developer Preview access is not required for this local server.
- Keep the first install independent of the source checkout, as with the Microsoft MCP.

## Proposed project structure

```text
agents/mcp/google_mcp/
  AGENTS.md                 # Development workflow and conventions
  README.md                 # Overview, one-time Google setup, first-run auth, usage
  INSTALL.md                # Install, registration, update, and uninstall steps
  HOW_IT_WORKS.md           # OAuth, local token storage, API call flow
  CHANGELOG.md
  Makefile                  # dev, auth, lint, test, install targets
  pyproject.toml
  uv.lock
  .python-version
  plans/
    01-kickoff.md
  src/
    DESIGN.md                # Source of truth for tools, scopes, auth, data shapes
    google_mcp/
      __init__.py
      __main__.py             # `python -m google_mcp` entry point
      server.py               # MCP server, tool schemas, dispatch
      auth.py                 # OAuth PKCE, token refresh, local state
      google_api.py           # Shared authenticated HTTP/client and error handling
      gmail.py                # Gmail search/list/read operations
      calendar.py             # Calendar event and invitation operations
      utils.py                # Shared date/time, pagination, and normalization helpers
      instructions.md         # Concise runtime guidance for the MCP client
  scripts/
    install.py
  tests/
    conftest.py
    test_auth.py
    test_gmail.py
    test_calendar.py
    test_server.py
```

Use the existing Microsoft package as a guide for packaging, installation footprint, documentation, tool descriptions, and development commands. Keep Google-specific OAuth and API details in this project rather than adding dependencies on the Microsoft package.

## Work sequence

1. **Finalize the design.** Document the tool names and schemas, exact Google API methods, requested OAuth scopes, response shapes, error behavior, pagination, and timezone rules in `src/DESIGN.md`.
2. **Implement authentication.** Add local Desktop OAuth setup, PKCE loopback callback, token refresh, expiration/re-auth handling, secure local state storage, and an explicit `auth` command.
3. **Implement API access.** Add a small shared Google API client and Gmail/Calendar modules with normalized outputs and consistent Google API error messages.
4. **Implement the MCP surface.** Register Gmail search/read tools and Calendar event tools in `server.py`; keep descriptions clear about Gmail's read-only access and Calendar's read/write/delete actions.
5. **Add package and install workflow.** Set up the Python package, development commands, and standalone install/uninstall flow modeled on `microsoft_agent_v2`.
6. **Document first-time setup.** Put the complete one-time Google Cloud and OAuth setup in `README.md`, with a shorter install path in `INSTALL.md`. Include creating a Cloud project, enabling Gmail and Calendar APIs, setting the OAuth audience to External / Testing, adding the personal Gmail account as a test user, creating a Desktop OAuth client, granting consent, the unverified-app warning, and the seven-day reauthorization expectation.
7. **Document operations.** Explain the local process, token location, API flow, data access, limitations, and how to revoke access in `HOW_IT_WORKS.md`.

## Manual testing with the user

After the local server is installed and registered with Claude Code, run a short manual test session together. The user runs the prompts in Claude Code and reports the result or error text; use each report to diagnose and fix issues, then rerun the affected scenario before moving on.

1. **First authorization and restart:** Authenticate the personal Gmail test account in the browser, confirm the server reports the expected account, restart Claude Code, and confirm the saved token is reused.
2. **Gmail read path:** Search with a narrow Gmail query, open one known message, and retrieve its thread. Confirm the sender, subject, date, and body match Gmail. No Gmail write operation is in scope.
3. **Calendar read path:** List events for a known short date range and inspect one event. Compare event titles and local start/end times with Google Calendar, including an all-day or timezone-sensitive event if available.
4. **Calendar write path:** Create a clearly named, disposable event with no attendees, update its title or time, verify the change in Google Calendar, and delete that same test event. Do not use an existing personal event for this check.
5. **Invitation response:** If invitation response is implemented in the initial release, test only with a disposable invitation whose organizer is controlled by the user; otherwise leave this scenario out of the first manual pass.
6. **Recovery path:** Verify the documented reauthorization command or flow works. Do not wait seven days; test the interactive reauthorization path directly.

Keep a short issue list during the session with the prompt, expected result, observed result, and fix. Continue the fix-and-retry loop until the agreed scenarios pass or a remaining issue is documented clearly.

## One-time Google setup to document in README

1. Create a Google Cloud project and enable the Gmail API and Google Calendar API.
2. Configure the OAuth consent screen for External users in Testing mode and add the personal Gmail account as a test user.
3. Create a Desktop app OAuth client; provide its client ID (and client secret if issued) through local configuration, never a committed file.
4. Start the MCP auth flow and grant the requested scopes in Google's consent screen.
5. Explain that Google's unverified-app warning is expected for a private test app, and that Testing-mode refresh tokens for Gmail/Calendar scopes expire after seven days, requiring periodic reauthorization.
6. Explain how to revoke access in Google Account security settings and how to remove local tokens during uninstall.

## Completion criteria

- The local stdio MCP starts from an installed standalone environment and exposes the designed Gmail and Calendar tools.
- OAuth works with a personal Gmail test user, stores tokens locally, refreshes access tokens, and gives a clear reauthorization path after the Testing-mode refresh token expires.
- Tool behavior, scopes, setup, and limitations agree across `src/DESIGN.md`, `README.md`, `INSTALL.md`, and `HOW_IT_WORKS.md`.
- Installation and OAuth setup require no Google Workspace Developer Preview membership.
