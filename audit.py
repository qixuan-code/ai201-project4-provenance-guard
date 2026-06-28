"""
Audit log — append-only JSON Lines store.

Every attribution decision and every appeal is recorded here.
Records are never updated in-place; appeals and review decisions
are appended as new top-level keys on the original entry via
update_entry(), which rewrites the matching line.

Schema (planning.md §Audit Log Schema):

    content_id    str      uuid, returned to submitter
    timestamp     str      ISO 8601 UTC
    creator_id    str      provided by submitter
    requester_id  str      IP address (hashed for privacy in production)
    status        str      "decided" | "under_review" | "overturned" | "upheld"
    attribution   str      "ai" | "human" | "uncertain"
    confidence    float    0.0–1.0
    label_variant str      "ai" | "human" | "uncertain"
    signals       dict     perplexity_raw, perplexity_score, burstiness_raw,
                           burstiness_score, ai_s1, ai_s2, disagreement
    appeal        dict|None  appended when POST /appeal is called
    review        dict|None  appended when a human reviewer resolves the appeal
"""

import json
import os
import threading
from pathlib import Path
from typing import Any, Optional

_LOG_PATH = Path(os.environ.get("AUDIT_LOG_PATH", "audit_log.jsonl"))
_lock = threading.Lock()


def log_decision(entry: dict[str, Any]) -> None:
    """
    Append a new decision record to the audit log.

    Args:
        entry: dict conforming to the audit log schema above.
               Caller is responsible for including all required fields.
    """
    with _lock:
        with _LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def update_entry(content_id: str, updates: dict[str, Any]) -> bool:
    """
    Merge `updates` into the log entry matching `content_id`.

    Used by the appeals endpoint to:
      - set status = "under_review"
      - attach the appeal sub-dict

    Rewrites the entire file holding the lock; acceptable for MVP scale.
    Returns True if the entry was found and updated, False otherwise.
    """
    with _lock:
        if not _LOG_PATH.exists():
            return False

        lines = _LOG_PATH.read_text(encoding="utf-8").splitlines()
        found = False
        new_lines: list[str] = []

        for line in lines:
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("content_id") == content_id:
                record.update(updates)
                found = True
            new_lines.append(json.dumps(record, ensure_ascii=False))

        if found:
            _LOG_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

        return found


def get_entries(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """
    Read log entries, optionally filtered by status.

    Used by GET /log.
    """
    if not _LOG_PATH.exists():
        return []

    with _lock:
        lines = _LOG_PATH.read_text(encoding="utf-8").splitlines()

    records = [json.loads(l) for l in lines if l.strip()]

    if status:
        records = [r for r in records if r.get("status") == status]

    return records[offset : offset + limit]


def get_entry(content_id: str) -> Optional[dict[str, Any]]:
    """Return the single log entry for `content_id`, or None."""
    entries = get_entries(limit=0)  # limit=0 → no slice needed; but get_entries slices
    # Re-read without slicing
    if not _LOG_PATH.exists():
        return None
    with _lock:
        lines = _LOG_PATH.read_text(encoding="utf-8").splitlines()
    for line in lines:
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("content_id") == content_id:
            return record
    return None
