# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""Mirror the committed state of this folder to the public repo as one snapshot commit.

xiaoverse stays the source of truth; never edit the public repo directly.
"""

import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO = "shayanhoshyari/google-mcp"
HERE = Path(__file__).resolve().parent.parent
GIT_ENV = {k: v for k, v in os.environ.items() if k != "GITHUB_TOKEN"}


def git(*args: str, cwd: Path, capture: bool = False) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, env=GIT_ENV, check=True, stdout=subprocess.PIPE if capture else None
    )
    return result.stdout.decode().strip() if capture else ""


def main() -> int:
    if git("status", "--porcelain", "--", ".", cwd=HERE, capture=True):
        print(f"Uncommitted changes in {HERE}; commit them first (publish uses HEAD).", file=sys.stderr)
        return 1

    root = Path(git("rev-parse", "--show-toplevel", cwd=HERE, capture=True))
    prefix = git("rev-parse", "--show-prefix", cwd=HERE, capture=True)
    sha = git("rev-parse", "--short", "HEAD", cwd=HERE, capture=True)
    archive = subprocess.run(
        ["git", "archive", f"HEAD:{prefix}"], cwd=root, env=GIT_ENV, check=True, stdout=subprocess.PIPE
    ).stdout

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        git("clone", "--quiet", f"https://github.com/{REPO}.git", str(repo), cwd=Path(tmp))
        git("checkout", "--quiet", "-B", "main", cwd=repo)
        for entry in repo.iterdir():
            if entry.name != ".git":
                shutil.rmtree(entry) if entry.is_dir() and not entry.is_symlink() else entry.unlink()
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(repo, filter="data")
        git("add", "-A", cwd=repo)

        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo).returncode == 0:
            print(f"{REPO} already matches xiaoverse@{sha}.")
            return 0

        git("commit", "--quiet", "-m", f"Sync from xiaoverse@{sha}", cwd=repo)
        git("push", "--quiet", "origin", "main", cwd=repo)
    print(f"Published xiaoverse@{sha} to https://github.com/{REPO}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
