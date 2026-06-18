#!/usr/bin/env python3
"""Block commits that would expose secrets or forbidden local-only files."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import PurePosixPath

# Paths that must never be committed (basename or suffix match).
FORBIDDEN_BASENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    "credentials.json",
    "secrets.json",
    "id_rsa",
    "id_ed25519",
}

FORBIDDEN_SUFFIXES = (
    ".pem",
    ".p12",
    ".pfx",
    ".key",
    ".keystore",
    ".jks",
)

# Assignment-style secret patterns (staged content).
PLACEHOLDER_VALUES = {
    "",
    "''",
    '""',
    "your_local_key",
    "your-key-here",
    "changeme",
    "placeholder",
    "xxx",
    "none",
    "null",
}

ENV_SECRET = re.compile(
    r"(?m)^\s*(?:export\s+)?([A-Z][A-Z0-9_]*(?:API[_-]?KEY|_SECRET|_PASSWORD|_TOKEN))\s*=\s*([^\s#]+)"
)

LITERAL_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("GitHub token", re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    ("GitHub fine-grained token", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("OpenAI key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("Stripe live key", re.compile(r"sk_live_[A-Za-z0-9]{20,}")),
    ("Private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
]

ALLOWLIST_PATHS = {
    ".env.example",
    "scripts/check_secrets.py",
    "docs/SECURITY.md",
    "docs/API_KEYS_AND_DATA_SOURCES.md",
}


def _run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def staged_files() -> list[str]:
    out = _run_git("diff", "--cached", "--name-only", "--diff-filter=ACM")
    return [line.strip() for line in out.splitlines() if line.strip()]


def staged_content(path: str) -> str:
    return _run_git("show", f":{path}")


def is_forbidden_path(path: str) -> str | None:
    pure = PurePosixPath(path.replace("\\", "/"))
    name = pure.name
    if name in FORBIDDEN_BASENAMES:
        return f"forbidden file `{path}`"
    if name.startswith(".env.") and name != ".env.example":
        return f"forbidden env file `{path}`"
    if any(name.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES):
        return f"forbidden credential file `{path}`"
    return None


def scan_content(path: str, content: str) -> list[str]:
    if path in ALLOWLIST_PATHS:
        return []
    hits: list[str] = []
    for label, pattern in LITERAL_SECRET_PATTERNS:
        if pattern.search(content):
            hits.append(f"{path}: possible {label}")
    for match in ENV_SECRET.finditer(content):
        raw_value = match.group(2).strip("'\"")
        lowered = raw_value.lower()
        if lowered in PLACEHOLDER_VALUES or lowered.startswith("your_"):
            continue
        if len(raw_value) < 8:
            continue
        hits.append(f"{path}: possible secret env assignment `{match.group(1)}`")
    return hits


def main() -> int:
    try:
        paths = staged_files()
    except RuntimeError as exc:
        print(f"[check_secrets] {exc}", file=sys.stderr)
        return 1

    if not paths:
        return 0

    failures: list[str] = []
    for path in paths:
        reason = is_forbidden_path(path)
        if reason:
            failures.append(reason)
            continue
        try:
            content = staged_content(path)
        except RuntimeError as exc:
            failures.append(f"could not read staged `{path}`: {exc}")
            continue
        failures.extend(scan_content(path, content))

    if failures:
        print("Secret scan failed — commit blocked.", file=sys.stderr)
        for item in failures:
            print(f"  - {item}", file=sys.stderr)
        print(
            "\nRemove secrets from staged files, use `.env` locally, and keep `.env.example` "
            "comment-only.\nSee docs/SECURITY.md.",
            file=sys.stderr,
        )
        return 1

    print(f"[check_secrets] OK ({len(paths)} staged file(s) scanned)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
