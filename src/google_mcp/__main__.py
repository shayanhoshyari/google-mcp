"""CLI and module entry point for Google MCP."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime

from google_mcp import auth, files_sync
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

    files = commands.add_parser(
        "files", help="Sync folders of binaries with Drive through a files.toml manifest (see DESIGN.md)"
    )
    actions = files.add_subparsers(dest="files_command", required=True)
    init = actions.add_parser("init", help="Make a folder a binary folder: write its .gitignore and files.toml")
    init.add_argument("dir")
    for name, text in (
        ("status", "List missing, modified, and new files (offline); exit 1 if any"),
        ("pull", "Make each binary folder match its manifest: download missing and modified files"),
        ("push", "Make each manifest match its folder: upload new and modified files, drop missing ones"),
    ):
        action = actions.add_parser(name, help=text)
        action.add_argument("path", nargs="?", default=".", help="a folder to search, or a path inside one")
        if name == "push":
            action.add_argument("--dry-run", action="store_true", help="print the plan without uploading or writing")
    return parser


def _files(args: argparse.Namespace) -> int:
    """Run a `files` command; return the exit code (0 done, 1 files failed or differ, 2 usage or manifest)."""
    if args.files_command == "init":
        try:
            created = files_sync.init(args.dir)
        except (files_sync.SyncError, OSError) as exc:
            print(exc, file=sys.stderr)
            return 2
        for path in created:
            print(f"created {path}")
        if not created:
            print(f"{args.dir} is already a binary folder")
        return 0
    try:
        folders = files_sync.find_folders(args.path)
    except files_sync.SyncError as exc:
        print(exc, file=sys.stderr)
        return 2
    if not folders:
        print(f"No {files_sync.MANIFEST} in or under {args.path}", file=sys.stderr)
        return 0
    command = args.files_command
    code = 0
    total: Counter[str] = Counter()
    for folder in folders:
        try:
            if command == "status":
                report = files_sync.status(folder)
            elif command == "pull":
                report = files_sync.pull(folder)
            else:
                report = files_sync.push(folder, dry_run=args.dry_run)
        except (files_sync.SyncError, OSError) as exc:
            print(exc, file=sys.stderr)
            code = 2
            continue
        for line in report.lines:
            print(line)
        for line in report.errors:
            print(line, file=sys.stderr)
        total.update(report.counts)
    if total["failed"] or (command == "status" and total.total()):
        code = max(code, 1)
    summary = ", ".join(f"{count} {verb}" for verb, count in total.items() if count)
    print(f"{command}: {summary or ('all files match' if command == 'status' else 'up to date')}")
    return code


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    if args.command is None:
        parser.print_help(sys.stderr)
        raise SystemExit(2)
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
        if args.command == "files":
            raise SystemExit(_files(args))
        from google_mcp.server import main as server_main

        server_main()
    except AuthError as exc:
        print(f"Google MCP authentication error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
