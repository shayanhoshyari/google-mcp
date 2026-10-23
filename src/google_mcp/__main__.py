"""CLI and module entry point for Google MCP."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime

from google_mcp import auth
from google_mcp.auth import AuthError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="google-mcp", description="Local Gmail, Google Calendar, and Google Drive MCP server"
    )
    commands = parser.add_subparsers(dest="command")
    configure = commands.add_parser("configure", help="Store a downloaded Google Desktop OAuth client JSON")
    configure.add_argument("client_secrets")
    commands.add_parser("auth", help="Authorize a Google account in the browser")
    commands.add_parser("status", help="Show local authorization status without secrets")
    commands.add_parser("clear-auth", help="Delete the local OAuth token state")
    commands.add_parser("serve", help="Run the stdio MCP server")
    return parser


def main() -> None:
    args = _parser().parse_args()
    try:
        if args.command == "configure":
            destination = auth.configure(args.client_secrets)
            print(f"Saved Desktop OAuth client to {destination}")
            print("Next: google-mcp auth")
            return
        if args.command == "auth":
            result = auth.authenticate()
            print(f"Authorized {result.get('account_email') or 'Google account'}")
            return
        if args.command == "status":
            value = auth.status()
            expiry = value.get("access_token_expires_at")
            if isinstance(expiry, int):
                value["access_token_expires_at"] = (
                    datetime.fromtimestamp(expiry, UTC).isoformat().replace("+00:00", "Z")
                )
            print(json.dumps(value, indent=2))
            return
        if args.command == "clear-auth":
            print(
                "Removed local authorization." if auth.clear_authorization() else "No local authorization was present."
            )
            return
        from google_mcp.server import main as server_main

        server_main()
    except AuthError as exc:
        print(f"Google MCP authentication error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
