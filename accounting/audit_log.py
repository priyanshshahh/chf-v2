"""Tamper-evident operations log for CHF fund accounting.

JSON-lines file (default ``data/accounting/audit.log``) where every entry is
chained to its predecessor with SHA-256:

    entry_hash = sha256(ts_utc | actor | action | payload_hash | prev_hash)

``prev_hash`` of the first entry is the 64-zero genesis hash. Editing,
deleting, or reordering any historical line breaks every subsequent
``entry_hash``, which :func:`verify_chain` detects.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

DEFAULT_AUDIT_LOG_PATH = Path("data/accounting/audit.log")
GENESIS_HASH = "0" * 64


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_payload(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _entry_hash(ts_utc: str, actor: str, action: str, payload_hash: str, prev_hash: str) -> str:
    return _sha256("|".join([ts_utc, actor, action, payload_hash, prev_hash]))


def _last_entry_hash(log_path: Path) -> str:
    if not log_path.exists():
        return GENESIS_HASH
    last_line = ""
    with log_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                last_line = line
    if not last_line:
        return GENESIS_HASH
    return str(json.loads(last_line)["entry_hash"])


def log_event(
    actor: str,
    action: str,
    payload: dict,
    log_path: Path = DEFAULT_AUDIT_LOG_PATH,
) -> dict:
    """Append one chained entry to the audit log and return it."""
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ts_utc = datetime.now(timezone.utc).isoformat()
    payload_hash = _sha256(_canonical_payload(payload))
    prev_hash = _last_entry_hash(path)
    entry = {
        "ts_utc": ts_utc,
        "actor": actor,
        "action": action,
        "payload_hash": payload_hash,
        "prev_hash": prev_hash,
        "entry_hash": _entry_hash(ts_utc, actor, action, payload_hash, prev_hash),
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
    return entry


def verify_chain(log_path: Path = DEFAULT_AUDIT_LOG_PATH) -> Tuple[bool, Optional[str]]:
    """Verify the full hash chain. Returns (ok, error_description)."""
    path = Path(log_path)
    if not path.exists():
        return True, None
    prev_hash = GENESIS_HASH
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                return False, f"line {lineno}: invalid JSON ({exc})"
            if entry.get("prev_hash") != prev_hash:
                return False, f"line {lineno}: prev_hash mismatch (chain broken)"
            expected = _entry_hash(
                str(entry.get("ts_utc", "")),
                str(entry.get("actor", "")),
                str(entry.get("action", "")),
                str(entry.get("payload_hash", "")),
                str(entry.get("prev_hash", "")),
            )
            if entry.get("entry_hash") != expected:
                return False, f"line {lineno}: entry_hash mismatch (entry tampered)"
            prev_hash = str(entry["entry_hash"])
    return True, None
