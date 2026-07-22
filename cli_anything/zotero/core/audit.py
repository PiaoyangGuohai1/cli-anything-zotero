"""Append-only audit log for privileged / write operations."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def audit_dir() -> Path:
    override = os.environ.get("ZOTERO_CLI_AUDIT_DIR")
    if override:
        path = Path(override).expanduser()
    else:
        path = Path("~/.local/share/cli-anything-zotero").expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def audit_path() -> Path:
    return audit_dir() / "audit.jsonl"


def log_event(
    action: str,
    *,
    ok: bool | None = None,
    status: str | None = None,
    code: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Append one audit event. Returns the written entry."""
    entry: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "action": action,
    }
    if ok is not None:
        entry["ok"] = ok
    if status is not None:
        entry["status"] = status
    if code is not None:
        entry["code"] = code
    # Drop bulky nested blobs
    for key, value in fields.items():
        if value is None:
            continue
        if key in {"import_result", "convert", "doctor", "attempts", "items", "details"}:
            continue
        if isinstance(value, (str, int, float, bool)):
            entry[key] = value
        elif isinstance(value, (list, dict)):
            # keep small structures only
            text = json.dumps(value, ensure_ascii=False)
            if len(text) <= 2000:
                entry[key] = value
            else:
                entry[key] = {"_truncated": True, "type": type(value).__name__, "size": len(text)}
        else:
            entry[key] = str(value)[:500]

    path = audit_path()
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def log_payload(payload: dict[str, Any], *, action: str | None = None) -> dict[str, Any] | None:
    """Log a result_payload-like dict if it looks like a write action."""
    if not isinstance(payload, dict):
        return None
    act = action or payload.get("action")
    if not act:
        return None
    # Skip pure read/doctor unless explicitly write-ish
    readish = {
        "app_doctor",
        "item_duplicates",
        "item_merge_preview",
        "collection_stats",
    }
    if act in readish and payload.get("status") == "dry_run":
        # still log dry-run merges for audit trail of intent
        pass
    return log_event(
        str(act),
        ok=payload.get("ok"),
        status=payload.get("status"),
        code=payload.get("code"),
        key=payload.get("key"),
        keep=payload.get("keep") if not isinstance(payload.get("keep"), dict) else (payload.get("keep") or {}).get("key"),
        DOI=payload.get("DOI"),
        path=payload.get("path") or payload.get("output"),
        source=payload.get("source"),
        mode_used=payload.get("mode_used"),
        dry_run=payload.get("dry_run"),
        error=payload.get("error"),
        collection=payload.get("collection"),
        url=payload.get("url"),
        arxiv_id=payload.get("arxiv_id"),
        succeeded=payload.get("succeeded"),
        failed=payload.get("failed"),
        found=payload.get("found"),
        checked=payload.get("checked"),
    )


def tail(limit: int = 20) -> list[dict[str, Any]]:
    path = audit_path()
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[dict[str, Any]] = []
    for line in lines[-max(1, limit) :]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            out.append({"raw": line, "ok": False, "error": "invalid json line"})
    return out
