#!/usr/bin/env bash
# Mirror the committed state of this folder to the public repo as one snapshot commit.
# xiaoverse stays the source of truth; never edit the public repo directly.
set -euo pipefail

REPO="shayanhoshyari/google-mcp"
here="$(cd "$(dirname "$0")/.." && pwd)"
cd "$here"

if [ -n "$(git status --porcelain -- .)" ]; then
  echo "Uncommitted changes in $here; commit them first (publish uses HEAD)." >&2
  exit 1
fi

root="$(git rev-parse --show-toplevel)"
prefix="$(git rev-parse --show-prefix)"
sha="$(git rev-parse --short HEAD)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

env -u GITHUB_TOKEN git clone --quiet "https://github.com/$REPO.git" "$tmp/repo"
cd "$tmp/repo"
git checkout --quiet -B main
find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
git -C "$root" archive "HEAD:$prefix" | tar -x -C .
git add -A

if git diff --cached --quiet; then
  echo "$REPO already matches xiaoverse@$sha."
  exit 0
fi

git commit --quiet -m "Sync from xiaoverse@$sha"
env -u GITHUB_TOKEN git push --quiet origin main
echo "Published xiaoverse@$sha to https://github.com/$REPO"
