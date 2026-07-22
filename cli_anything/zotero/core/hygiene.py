"""Library hygiene: duplicates by DOI/title and merge helpers."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from cli_anything.zotero.core.results import result_payload
from cli_anything.zotero.utils import zotero_sqlite


def _norm_doi(value: str | None) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"^https?://(dx\.)?doi\.org/", "", text)
    text = re.sub(r"^doi:\s*", "", text)
    return text.rstrip(" .),;")


def _norm_title(value: str | None) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\s]", "", text)
    return text


def find_duplicates(
    runtime: Any,
    *,
    by: str = "doi",
    library_id: int = 1,
    limit: int = 50,
) -> dict[str, Any]:
    by = (by or "doi").lower()
    if by not in {"doi", "title", "zotero"}:
        return result_payload(
            action="item_duplicates",
            ok=False,
            status="error",
            code="INVALID_BY",
            error="by must be one of: doi, title, zotero",
        )

    if by == "zotero":
        # Caller should use bridge.find_duplicates; this is a placeholder envelope
        return result_payload(
            action="item_duplicates",
            ok=True,
            status="success",
            code="USE_BRIDGE",
            by="zotero",
            message="Use bridge Zotero.Duplicates via item duplicates --by zotero",
        )

    items = zotero_sqlite.fetch_items(runtime.environment.sqlite_path, library_id=library_id)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        if item.get("isAttachment") or item.get("isNote") or item.get("isAnnotation"):
            continue
        if by == "doi":
            key = _norm_doi(item.get("DOI") or "")
            if not key:
                continue
        else:
            key = _norm_title(item.get("title") or "")
            if not key or len(key) < 8:
                continue
        groups[key].append(
            {
                "key": item.get("key"),
                "title": item.get("title"),
                "DOI": item.get("DOI") or "",
                "date": item.get("date") or item.get("dateAdded"),
                "hasPdf": bool(item.get("hasPdf")),
            }
        )

    dup_groups = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        # prefer keep candidate with PDF then newest dateAdded proxy
        members_sorted = sorted(members, key=lambda m: (not m.get("hasPdf"), m.get("date") or ""), reverse=False)
        # hasPdf True first: not hasPdf False sorts after True if reverse False with (not hasPdf)
        members_sorted = sorted(members, key=lambda m: (0 if m.get("hasPdf") else 1, m.get("date") or ""))
        dup_groups.append(
            {
                "match": key,
                "count": len(members_sorted),
                "keep_suggestion": members_sorted[0].get("key"),
                "items": members_sorted,
            }
        )
        if len(dup_groups) >= limit:
            break

    dup_groups.sort(key=lambda g: g["count"], reverse=True)
    return result_payload(
        action="item_duplicates",
        ok=True,
        status="success",
        code="OK",
        by=by,
        group_count=len(dup_groups),
        groups=dup_groups,
    )


def merge_items(
    bridge: Any,
    keep_key: str,
    merge_keys: list[str],
    *,
    library_id: int = 1,
    dry_run: bool = True,
) -> dict[str, Any]:
    merge_keys = [k for k in merge_keys if k and k != keep_key]
    if not keep_key or not merge_keys:
        return result_payload(
            action="item_merge",
            ok=False,
            status="error",
            code="INVALID_ARGS",
            error="keep key and at least one other key are required",
        )

    plan = {"keep": keep_key, "merge": merge_keys, "dry_run": dry_run}
    if dry_run:
        return result_payload(
            action="item_merge",
            ok=True,
            status="dry_run",
            code="DRY_RUN",
            plan=plan,
            message=(
                f"Would merge {merge_keys} into {keep_key}: move attachments/notes, "
                "union tags/collections, trash merged items. Re-run with --confirm."
            ),
        )

    results = []
    for other in merge_keys:
        js = (
            f"var keep = Zotero.Items.getByLibraryAndKey({library_id}, '{keep_key}'); "
            f"var other = Zotero.Items.getByLibraryAndKey({library_id}, '{other}'); "
            f"if (!keep || !other) {{ return {{ok:false, error:'item not found', keep:'{keep_key}', other:'{other}'}}; }} "
            # move child attachments/notes
            "var childIDs = other.getAttachments().concat(other.getNotes ? other.getNotes() : []); "
            "var moved = 0; "
            "for (var cid of childIDs) { var child = Zotero.Items.get(cid); "
            "  if (!child) continue; child.parentItemID = keep.id; await child.saveTx(); moved++; } "
            # collections
            "var cols = other.getCollections(); "
            "for (var col of cols) { keep.addToCollection(col); } "
            # tags
            "var tags = other.getTags(); "
            "for (var t of tags) { keep.addTag(t.tag || t); } "
            "await keep.saveTx(); "
            # trash other
            "other.deleted = true; await other.saveTx(); "
            "return {ok:true, keep: keep.key, other: other.key, moved_children: moved};"
        )
        transport = bridge.execute_js(js, wait_seconds=30)
        data = transport.get("data") if transport.get("ok") else None
        results.append(
            {
                "other": other,
                "ok": bool(isinstance(data, dict) and data.get("ok")),
                "result": data,
                "error": None if transport.get("ok") else transport.get("error"),
            }
        )

    succeeded = sum(1 for r in results if r["ok"])
    ok = succeeded == len(results)
    return result_payload(
        action="item_merge",
        ok=ok,
        status="success" if ok else ("partial_success" if succeeded else "error"),
        code="MERGED" if ok else "MERGE_PARTIAL",
        keep=keep_key,
        merge=merge_keys,
        dry_run=False,
        results=results,
        succeeded=succeeded,
        failed=len(results) - succeeded,
    )
