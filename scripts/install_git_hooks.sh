#!/bin/sh
# Install repo-local git hooks (secret scan on every commit).
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

chmod +x .githooks/pre-commit scripts/check_secrets.py
git config core.hooksPath .githooks

echo "Installed git hooks from .githooks/ (core.hooksPath=.githooks)"
echo "Pre-commit: scripts/check_secrets.py"
