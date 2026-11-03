"""Run many write operations from one JSONL file, in one process.

Why this module exists
----------------------
One CLI call per item pays process start-up plus a bridge round-trip every
time, which makes library maintenance slow (#7). ``batch`` reads a JSONL file,
validates every line before touching the library, then runs the operations in
order inside a single process. Consecutive ``tag`` operations are sent to
Zotero in one bridge call.

Line format
-----------
One JSON object per line; blank lines and lines starting with ``#`` are
ignored. ``op`` may be omitted when the command is given ``--op``.

``{"op": "tag", "key": "ABCD1234", "add": ["reviewed"], "remove": ["unread"]}``
    ``key`` may be ``keys`` (a list); ``add``/``remove`` take a string or a list.
``{"op": "update", "key": "ABCD1234", "fields": {"title": "New title"}}``
``{"op": "note", "key": "ABCD1234", "file": "notes/abcd.md"}``
    or ``"text": "..."``; ``format`` is text|markdown|html, defaulting to
    markdown for ``.md``/``.markdown`` files and text otherwise. Relative
    ``file`` paths resolve against the JSONL file's directory.
``{"op": "add-doi", "doi": "10.1000/xyz", "collection": "COLLKEY", "tags": ["t"], "fetch_pdf": true}``
    ``if_exists`` is file|skip|duplicate (default file).
``{"op": "add-pmid", "pmid": "12345678", "collection": "COLLKEY", "tags": ["t"], "fetch_pdf": true}``

``item_key`` is accepted as an alias for ``key``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable

from cli_anything.zotero.core.results import result_payload

OPS = ("tag", "update", "note", "add-doi", "add-pmid")
TAG_CHUNK = 200

_KEY = re.compile(r"^[A-Z0-9]{8}$")
_PMID = re.compile(r"^\d{1,9}$")


class BatchLineError(ValueError):
    """A line that cannot be run as written."""


# ── Parsing / validation (pure) ───────────────────────────────────────


def _str_list(value: Any, name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise BatchLineError(f"`{name}` must be a string or a list of strings")
    return [v.strip() for v in value if v.strip()]


def _item_key(obj: dict[str, Any]) -> str:
    key = obj.get("key", obj.get("item_key"))
    if not isinstance(key, str) or not _KEY.match(key.strip()):
        raise BatchLineError("`key` must be an 8-character Zotero item key")
    return key.strip()


def normalize_op(obj: dict[str, Any], *, default_op: str | None = None, base_dir: Path | None = None) -> dict[str, Any]:
    """Validate one parsed line and return its canonical form."""
    op = obj.get("op", default_op)
    if op not in OPS:
        raise BatchLineError(f"unknown or missing `op` {op!r}; expected one of {', '.join(OPS)}")

    if op == "tag":
        if "keys" in obj:
            keys = obj["keys"]
            if not isinstance(keys, list) or not keys:
                raise BatchLineError("`keys` must be a non-empty list")
            for k in keys:
                if not isinstance(k, str) or not _KEY.match(k.strip()):
                    raise BatchLineError(f"invalid item key in `keys`: {k!r}")
            keys = [k.strip() for k in keys]
        else:
            keys = [_item_key(obj)]
        add = _str_list(obj.get("add"), "add")
        remove = _str_list(obj.get("remove"), "remove")
        if not add and not remove:
            raise BatchLineError("`tag` needs `add` and/or `remove`")
        return {"op": op, "keys": keys, "add": add, "remove": remove}

    if op == "update":
        fields = obj.get("fields")
        if not isinstance(fields, dict) or not fields:
            raise BatchLineError("`fields` must be a non-empty object")
        if not all(isinstance(k, str) and isinstance(v, (str, int, float)) for k, v in fields.items()):
            raise BatchLineError("`fields` values must be strings or numbers")
        return {"op": op, "key": _item_key(obj), "fields": {k: str(v) for k, v in fields.items()}}

    if op == "note":
        key = _item_key(obj)
        text, file_ = obj.get("text"), obj.get("file")
        if (text is None) == (file_ is None):
            raise BatchLineError("`note` needs exactly one of `text` or `file`")
        if text is not None and not isinstance(text, str):
            raise BatchLineError("`text` must be a string")
        path = None
        if file_ is not None:
            if not isinstance(file_, str) or not file_.strip():
                raise BatchLineError("`file` must be a path")
            path = Path(file_).expanduser()
            if not path.is_absolute() and base_dir is not None:
                path = base_dir / path
            if not path.is_file():
                raise BatchLineError(f"note file not found: {path}")
        fmt = obj.get("format")
        if fmt is None:
            fmt = "markdown" if path is not None and path.suffix.lower() in {".md", ".markdown"} else "text"
        if fmt not in {"text", "markdown", "html"}:
            raise BatchLineError("`format` must be text, markdown or html")
        return {"op": op, "key": key, "text": text, "file": str(path) if path else None, "format": fmt}

    # add-doi / add-pmid
    tags = _str_list(obj.get("tags"), "tags")
    collection = obj.get("collection")
    if collection is not None and (not isinstance(collection, str) or not _KEY.match(collection.strip())):
        raise BatchLineError("`collection` must be an 8-character collection key")
    fetch_pdf = obj.get("fetch_pdf", False)
    if not isinstance(fetch_pdf, bool):
        raise BatchLineError("`fetch_pdf` must be true or false")
    common = {"collection": collection.strip() if collection else None, "tags": tags, "fetch_pdf": fetch_pdf}
    if op == "add-doi":
        doi = obj.get("doi")
        if not isinstance(doi, str) or "10." not in doi:
            raise BatchLineError("`doi` must be a DOI string")
        if_exists = obj.get("if_exists", "file")
        if if_exists not in {"file", "skip", "duplicate"}:
            raise BatchLineError("`if_exists` must be file, skip or duplicate")
        return {"op": op, "doi": doi.strip(), "if_exists": if_exists, **common}
    pmid = obj.get("pmid")
    pmid = str(pmid).strip() if isinstance(pmid, (str, int)) else ""
    if not _PMID.match(pmid):
        raise BatchLineError("`pmid` must be a numeric PubMed ID")
    return {"op": op, "pmid": pmid, **common}


def parse_lines(
    lines: Iterable[str],
    *,
    default_op: str | None = None,
    base_dir: Path | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse JSONL into ``(ops, errors)``. Each op carries its 1-based ``line``."""
    ops: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for lineno, raw in enumerate(lines, start=1):
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            errors.append({"line": lineno, "error": f"invalid JSON: {exc.msg}"})
            continue
        if not isinstance(obj, dict):
            errors.append({"line": lineno, "error": "each line must be a JSON object"})
            continue
        try:
            op = normalize_op(obj, default_op=default_op, base_dir=base_dir)
        except BatchLineError as exc:
            errors.append({"line": lineno, "op": obj.get("op", default_op), "error": str(exc)})
            continue
        op["line"] = lineno
        ops.append(op)
    return ops, errors


# ── Execution ─────────────────────────────────────────────────────────


def _bridge_row(result: dict[str, Any]) -> tuple[bool, Any]:
    """Interpret a JS bridge result whose data may be an 'ERROR: ...' string."""
    if not result.get("ok"):
        return False, result.get("error") or "JS bridge call failed"
    data = result.get("data")
    if isinstance(data, str) and data.startswith("ERROR"):
        return False, data
    if isinstance(data, dict) and data.get("ok") is False:
        return False, data.get("error") or data
    return True, data


def _run_tags(bridge: Any, ops: list[dict[str, Any]], library_id: int) -> list[dict[str, Any]]:
    entries = [
        {"line": op["line"], "key": key, "add": op["add"], "remove": op["remove"]}
        for op in ops
        for key in op["keys"]
    ]
    rows: list[dict[str, Any]] = []
    for start in range(0, len(entries), TAG_CHUNK):
        chunk = entries[start:start + TAG_CHUNK]
        result = bridge.manage_tags_many(chunk, library_id=library_id)
        data = result.get("data") if result.get("ok") else None
        if not isinstance(data, list) or len(data) != len(chunk):
            error = result.get("error") or f"unexpected bridge reply: {str(data)[:200]}"
            rows.extend({"line": e["line"], "op": "tag", "key": e["key"], "ok": False, "error": error} for e in chunk)
            continue
        for entry, reply in zip(chunk, data):
            row = {"line": entry["line"], "op": "tag", "key": entry["key"], "ok": bool(reply.get("ok"))}
            if row["ok"]:
                row["changed"] = bool(reply.get("changed"))
                row["title"] = reply.get("title")
            else:
                row["error"] = reply.get("error") or "tag update failed"
            rows.append(row)
    return rows


def _run_one(
    op: dict[str, Any],
    *,
    runtime: Any,
    bridge: Any,
    session: dict[str, Any] | None,
    library_id: int,
    pdf_sources: str | None,
) -> dict[str, Any]:
    row: dict[str, Any] = {"line": op["line"], "op": op["op"]}
    if op["op"] == "update":
        row["key"] = op["key"]
        ok, detail = _bridge_row(bridge.update_item_fields(op["key"], op["fields"], library_id=library_id))
        row.update({"ok": ok, "result" if ok else "error": detail})
        return row

    if op["op"] == "note":
        from cli_anything.zotero.core import notes as notes_mod

        row["key"] = op["key"]
        content = op["text"] if op["text"] is not None else Path(op["file"]).read_text(encoding="utf-8")
        note_html = notes_mod._normalize_note_html(content, op["format"])
        ok, detail = _bridge_row(bridge.add_child_note(op["key"], note_html, library_id=library_id))
        if ok and isinstance(detail, dict):
            row.update({"ok": True, "note_key": detail.get("key"), "title": detail.get("title")})
        else:
            row.update({"ok": False, "error": detail})
        return row

    if op["op"] == "add-doi":
        from cli_anything.zotero.core import add as add_mod

        payload = add_mod.add_doi(
            runtime,
            bridge,
            op["doi"],
            collection_key=op["collection"],
            tags=op["tags"],
            session=session,
            if_exists=op["if_exists"],
            fetch_pdf=op["fetch_pdf"],
            pdf_sources=pdf_sources,
            library_id=library_id,
        )
        row.update({
            "doi": op["doi"],
            "ok": bool(payload.get("ok")),
            "status": payload.get("status"),
            "code": payload.get("code"),
            "key": payload.get("key"),
            "title": payload.get("title"),
        })
        if not row["ok"]:
            row["error"] = payload.get("error") or payload.get("code") or "import failed"
            attempts = (payload.get("import_result") or {}).get("attempts")
            if attempts:
                row["attempts"] = attempts
        if "pdf" in payload:
            row["pdf_status"] = (payload["pdf"] or {}).get("status")
        return row

    # add-pmid
    from cli_anything.zotero.core import pdf_fetch

    row["pmid"] = op["pmid"]
    ok, detail = _bridge_row(
        bridge.import_from_pmid(op["pmid"], collection_key=op["collection"], tags=op["tags"] or None, library_id=library_id)
    )
    if not ok or not isinstance(detail, dict) or not detail.get("key"):
        row.update({"ok": False, "error": detail if not ok else f"no item key in reply: {str(detail)[:200]}"})
        return row
    row.update({"ok": True, "status": "success", "key": detail["key"], "title": detail.get("title")})
    if op["fetch_pdf"]:
        pdf = pdf_fetch.fetch_pdf_for_item(
            runtime, bridge, detail["key"], sources=pdf_fetch.parse_sources(pdf_sources), library_id=library_id
        )
        row["pdf_status"] = pdf.get("status")
        if not pdf.get("ok") and pdf.get("status") != "already_has_pdf":
            row["status"] = "partial_success"
    return row


def run_batch(
    ops: list[dict[str, Any]],
    *,
    runtime: Any,
    bridge: Any,
    session: dict[str, Any] | None = None,
    library_id: int = 1,
    stop_on_error: bool = False,
    pdf_sources: str | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """Run validated ops in order. Returns one row per op (per key for ``tag``)."""
    rows: list[dict[str, Any]] = []
    index = 0
    while index < len(ops):
        op = ops[index]
        if op["op"] == "tag":
            run_end = index
            while run_end < len(ops) and ops[run_end]["op"] == "tag":
                run_end += 1
            new_rows = _run_tags(bridge, ops[index:run_end], library_id)
            index = run_end
        else:
            try:
                new_rows = [_run_one(op, runtime=runtime, bridge=bridge, session=session,
                                     library_id=library_id, pdf_sources=pdf_sources)]
            except Exception as exc:  # one bad op must not abort the rest
                new_rows = [{"line": op["line"], "op": op["op"], "ok": False, "error": f"{type(exc).__name__}: {exc}"}]
            index += 1
        for row in new_rows:
            rows.append(row)
            if progress is not None:
                progress(row)
        if stop_on_error and any(not r["ok"] for r in new_rows):
            for op_left in ops[index:]:
                rows.append({"line": op_left["line"], "op": op_left["op"], "ok": False, "status": "skipped",
                             "error": "not run: an earlier operation failed (--stop-on-error)"})
            break
    return rows


def parse_keys(values: Iterable[str]) -> list[str]:
    """Validate item keys (one per value, ``#`` comments and blanks skipped), de-duplicated in order."""
    keys: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = raw.split("#", 1)[0].strip()
        if not text:
            continue
        if not _KEY.match(text):
            raise BatchLineError(f"invalid item key: {text!r}")
        if text not in seen:
            seen.add(text)
            keys.append(text)
    return keys


def tag_items(bridge: Any, keys: list[str], add: list[str], remove: list[str], *, library_id: int = 1) -> dict[str, Any]:
    """Tag many items in one bridge round-trip (chunks of ``TAG_CHUNK``)."""
    rows = _run_tags(bridge, [{"op": "tag", "line": 0, "keys": keys, "add": add, "remove": remove}], library_id)
    for row in rows:
        row.pop("line", None)
        row.pop("op", None)
    failed = [r for r in rows if not r["ok"]]
    if not failed:
        status, code = "success", "OK"
    elif len(failed) < len(rows):
        status, code = "partial_success", "SOME_FAILED"
    else:
        status, code = "error", "ALL_FAILED"
    return result_payload(
        action="item_tag",
        ok=not failed,
        status=status,
        code=code,
        add=add,
        remove=remove,
        total=len(rows),
        changed=sum(1 for r in rows if r.get("changed")),
        failed=len(failed),
        results=rows,
    )


def summarize(rows: list[dict[str, Any]], *, parse_errors: list[dict[str, Any]] | None = None,
              dry_run: bool = False, ops: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Build the final ``batch`` payload."""
    if parse_errors:
        return result_payload(
            action="batch",
            ok=False,
            status="error",
            code="INVALID_INPUT",
            error=f"{len(parse_errors)} line(s) failed validation; nothing was run.",
            invalid=parse_errors,
        )
    if dry_run:
        return result_payload(action="batch", ok=True, status="success", code="DRY_RUN",
                              dry_run=True, total=len(ops or []), operations=ops or [])
    failed = [r for r in rows if not r["ok"] and r.get("status") != "skipped"]
    skipped = [r for r in rows if r.get("status") == "skipped"]
    partial = [r for r in rows if r["ok"] and r.get("status") == "partial_success"]
    succeeded = len(rows) - len(failed) - len(skipped)
    if not failed and not skipped:
        status, code = "success", "OK"
    elif succeeded:
        status, code = "partial_success", "SOME_FAILED"
    else:
        status, code = "error", "ALL_FAILED"
    return result_payload(
        action="batch",
        ok=not failed and not skipped,
        status=status,
        code=code,
        total=len(rows),
        succeeded=succeeded,
        failed=len(failed),
        skipped=len(skipped),
        partial=len(partial),
        results=rows,
    )
