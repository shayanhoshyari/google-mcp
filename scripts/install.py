#!/bin/bash
# Google MCP standalone installer. The shell header locates uv, then runs this file as Python.

""":"
set -euo pipefail

INSTALL_ROOT="$HOME/.claude/google_mcp"
UV_ROOT="$INSTALL_ROOT/uv"

if [ -n "${UV_BINARY:-}" ] && [ -f "${UV_BINARY}" ]; then
    :
elif [ -f "$UV_ROOT/bin/uv" ]; then
    export UV_BINARY="$UV_ROOT/bin/uv"
elif command -v uv >/dev/null 2>&1; then
    export UV_BINARY="$(command -v uv)"
else
    echo "Installing uv into $UV_ROOT/bin ..."
    curl -LsSf https://astral.sh/uv/install.sh | UV_UNMANAGED_INSTALL="$UV_ROOT/bin" sh
    export UV_BINARY="$UV_ROOT/bin/uv"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$UV_BINARY" run --script "$SCRIPT_DIR/install.py" "$@"
"""

# /// script
# requires-python = ">=3.13"
# dependencies = ["json5>=0.12,<1"]
# ///

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import json5

PROJECT_DIR = Path(__file__).resolve().parent.parent
INSTALL_ROOT = Path.home() / ".claude" / "google_mcp"
VENV = INSTALL_ROOT / "venv"
PYTHON = VENV / "bin" / "python"


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def run(command: list[str], **kwargs: Any) -> None:
    print(f"  $ {' '.join(command)}")
    completed = subprocess.run(command, **kwargs)
    if completed.returncode:
        fail(f"Command failed: {' '.join(command)}")


def load_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json5.loads(path.read_text())
    except ValueError as exc:
        fail(f"Refusing to overwrite invalid JSON in {path}: {exc}")
    if not isinstance(value, dict):
        fail(f"Refusing to overwrite {path}: its top-level value is not an object")
    return value


def install() -> None:
    uv = os.environ.get("UV_BINARY")
    if not uv or not Path(uv).is_file():
        fail("UV_BINARY is missing; invoke this script with bash scripts/install.py")
    if VENV.exists():
        print(f"Removing the previous standalone environment at {VENV}")
        shutil.rmtree(VENV)
    INSTALL_ROOT.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(VENV)}
    run(
        [uv, "sync", "--no-editable", "--no-dev", "--reinstall-package", "xiaoverse-google-mcp"],
        cwd=PROJECT_DIR,
        env=env,
    )


def register_opencode() -> None:
    config_root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "opencode"
    path = config_root / "opencode.jsonc"
    if not path.exists():
        path = config_root / "opencode.json"
    settings = load_settings(path)
    settings.setdefault("$schema", "https://opencode.ai/config.json")
    servers = settings.setdefault("mcp", {})
    if not isinstance(servers, dict):
        fail(f"Refusing to overwrite {path}: mcp is not an object")
    servers["google-mcp"] = {
        "type": "local",
        "command": [str(PYTHON), "-m", "google_mcp"],
        "enabled": True,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n")
    os.chmod(path, 0o600)
    print(f"Registered google-mcp in OpenCode: {path}")


def register() -> None:
    entry = {"command": str(PYTHON), "args": ["-m", "google_mcp"]}
    for label, path in (
        ("Claude Code", Path.home() / ".claude.json"),
        ("Cursor", Path.home() / ".cursor" / "mcp.json"),
    ):
        settings = load_settings(path)
        servers = settings.setdefault("mcpServers", {})
        if not isinstance(servers, dict):
            fail(f"Refusing to overwrite {path}: mcpServers is not an object")
        servers["google-mcp"] = entry
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, indent=2) + "\n")
        os.chmod(path, 0o600)
        print(f"Registered google-mcp in {label}: {path}")

    register_opencode()

    codex = shutil.which("codex")
    if codex:
        run([codex, "mcp", "add", "google-mcp", "--", str(PYTHON), "-m", "google_mcp"])
        print(f"Registered google-mcp in Codex: {Path.home() / '.codex' / 'config.toml'}")
    else:
        print("Codex CLI was not found; skipped Codex registration.")


def main() -> None:
    print("Google MCP standalone installer")
    install()
    register()
    print(f"\nInstalled Python: {PYTHON}")
    print("OAuth data stays in ~/.claude/google_mcp/config/ and survives reinstalling the venv.")
    print("\nNext steps:")
    print(f"  {PYTHON} -m google_mcp configure /path/to/client_secret_....json")
    print(f"  {PYTHON} -m google_mcp auth")
    print("  Restart Claude Code, Cursor, Codex, or OpenCode after authorization.")


if __name__ == "__main__":
    main()
