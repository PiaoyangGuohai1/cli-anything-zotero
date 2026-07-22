"""Shared machine-readable result helpers for agent-facing CLI commands."""

from __future__ import annotations

from typing import Any


def result_payload(
    *,
    action: str,
    ok: bool,
    status: str | None = None,
    code: str | None = None,
    error: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build a stable agent result object.

    Required fields:
      - action: command/action name
      - ok: boolean success flag
      - status: success | already_exists | partial_success | error | ...
      - code: machine code (optional but recommended)
    """
    if status is None:
        status = "success" if ok else "error"
    payload: dict[str, Any] = {
        "action": action,
        "ok": ok,
        "status": status,
    }
    if code is not None:
        payload["code"] = code
    if error is not None:
        payload["error"] = error
    payload.update(extra)
    return payload


def exit_code_for(payload: dict[str, Any]) -> int:
    """Map a result payload to a process exit code."""
    if payload.get("ok") is False:
        return 1
    status = str(payload.get("status") or "")
    if status in {"partial_success", "error", "failed", "timeout"}:
        return 1
    return 0


def normalize_if_exists(value: str | None, *, default: str = "file") -> str:
    """Normalize if-exists policy: file | skip | duplicate."""
    text = (value or default).strip().lower()
    if text not in {"file", "skip", "duplicate"}:
        raise ValueError(f"Unsupported if-exists policy: {value!r} (use file|skip|duplicate)")
    return text
