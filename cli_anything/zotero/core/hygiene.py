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


def _preview_js(keep_key: str, merge_keys: list[str], library_id: int) -> str:
    keys_json = json_dumps_js([keep_key, *merge_keys])
    return (
        "function summarize(item) { "
        "  if (!item) return null; "
        "  var tags = (item.getTags() || []).map(t => (t && (t.tag || t.name)) || String(t)); "
        "  var cols = (item.getCollections() || []).map(id => { "
        "    var c = Zotero.Collections.get(id); "
        "    return c ? {id: c.id, key: c.key, name: c.name} : {id: id}; "
        "  }); "
        "  var attachments = (item.getAttachments() || []).map(id => { "
        "    var a = Zotero.Items.get(id); "
        "    if (!a) return {id: id}; "
        "    return {key: a.key, title: a.getField('title'), contentType: a.attachmentContentType || '', "
        "            filename: a.attachmentFilename || '', linkMode: a.attachmentLinkMode}; "
        "  }); "
        "  var notes = ((item.getNotes && item.getNotes()) || []).map(id => { "
        "    var n = Zotero.Items.get(id); "
        "    if (!n) return {id: id}; "
        "    var title = ''; try { title = n.getNoteTitle ? n.getNoteTitle() : n.getField('title'); } catch (e) {} "
        "    return {key: n.key, title: (title || '').substring(0, 80)}; "
        "  }); "
        "  return {key: item.key, title: item.getField('title'), DOI: item.getField('DOI') || '', "
        "          date: item.getField('date') || '', itemType: item.itemType, "
        "          tags: tags, collections: cols, attachments: attachments, notes: notes, "
        "          nAttachments: attachments.length, nNotes: notes.length, nTags: tags.length, nCollections: cols.length}; "
        "} "
        f"var keys = {keys_json}; "
        f"var keep = Zotero.Items.getByLibraryAndKey({library_id}, keys[0]); "
        "if (!keep) { return {ok:false, error:'keep item not found', keep: keys[0]}; } "
        "var keepSum = summarize(keep); "
        "var others = []; var missing = []; "
        "for (var i = 1; i < keys.length; i++) { "
        f"  var it = Zotero.Items.getByLibraryAndKey({library_id}, keys[i]); "
        "  if (!it) { missing.push(keys[i]); continue; } "
        "  others.push(summarize(it)); "
        "} "
        "var keepTagSet = new Set(keepSum.tags || []); "
        "var keepColSet = new Set((keepSum.collections || []).map(c => c.key || String(c.id))); "
        "var tagsToAdd = []; var colsToAdd = []; var attachmentsToMove = 0; var notesToMove = 0; "
        "for (var o of others) { "
        "  attachmentsToMove += (o.nAttachments || 0); "
        "  notesToMove += (o.nNotes || 0); "
        "  for (var t of (o.tags || [])) { if (!keepTagSet.has(t)) { tagsToAdd.push(t); keepTagSet.add(t); } } "
        "  for (var c of (o.collections || [])) { "
        "    var ck = c.key || String(c.id); "
        "    if (!keepColSet.has(ck)) { colsToAdd.push(c); keepColSet.add(ck); } "
        "  } "
        "} "
        "return {ok:true, keep: keepSum, others: others, missing: missing, "
        "        will: {move_attachments: attachmentsToMove, move_notes: notesToMove, "
        "               add_tags: tagsToAdd, add_collections: colsToAdd, trash_items: others.map(o => o.key)}}; "
    )


def json_dumps_js(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


def preview_merge(
    bridge: Any,
    keep_key: str,
    merge_keys: list[str],
    *,
    library_id: int = 1,
) -> dict[str, Any]:
    """Return a detailed merge preview without modifying the library."""
    merge_keys = [k for k in merge_keys if k and k != keep_key]
    if not keep_key or not merge_keys:
        return result_payload(
            action="item_merge_preview",
            ok=False,
            status="error",
            code="INVALID_ARGS",
            error="keep key and at least one other key are required",
        )
    transport = bridge.execute_js(_preview_js(keep_key, merge_keys, library_id), wait_seconds=20)
    if not transport.get("ok"):
        return result_payload(
            action="item_merge_preview",
            ok=False,
            status="error",
            code="BRIDGE_ERROR",
            error=transport.get("error") or "bridge preview failed",
            keep=keep_key,
            merge=merge_keys,
        )
    data = transport.get("data")
    if not isinstance(data, dict) or not data.get("ok"):
        return result_payload(
            action="item_merge_preview",
            ok=False,
            status="error",
            code="PREVIEW_FAILED",
            error=(data or {}).get("error") if isinstance(data, dict) else "preview failed",
            keep=keep_key,
            merge=merge_keys,
            result=data,
        )
    will = data.get("will") or {}
    return result_payload(
        action="item_merge_preview",
        ok=True,
        status="dry_run",
        code="DRY_RUN",
        keep=data.get("keep"),
        others=data.get("others") or [],
        missing=data.get("missing") or [],
        will=will,
        summary={
            "trash_count": len(will.get("trash_items") or []),
            "move_attachments": will.get("move_attachments") or 0,
            "move_notes": will.get("move_notes") or 0,
            "add_tags": will.get("add_tags") or [],
            "add_collections": [c.get("name") or c.get("key") for c in (will.get("add_collections") or [])],
        },
        message=(
            f"Would trash {len(will.get('trash_items') or [])} item(s) into keep={keep_key}: "
            f"move {will.get('move_attachments') or 0} attachment(s), "
            f"{will.get('move_notes') or 0} note(s); "
            f"add {len(will.get('add_tags') or [])} tag(s), "
            f"{len(will.get('add_collections') or [])} collection(s). "
            "Re-run with --confirm to apply."
        ),
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

    preview = preview_merge(bridge, keep_key, merge_keys, library_id=library_id)
    if dry_run:
        # Keep action name stable for CLI consumers
        preview["action"] = "item_merge"
        preview["plan"] = {"keep": keep_key, "merge": merge_keys, "dry_run": True}
        preview["dry_run"] = True
        return preview
    if not preview.get("ok"):
        preview["action"] = "item_merge"
        preview["dry_run"] = False
        return preview
    if preview.get("missing"):
        return result_payload(
            action="item_merge",
            ok=False,
            status="error",
            code="ITEMS_MISSING",
            error=f"Missing items: {preview.get('missing')}",
            preview=preview,
            dry_run=False,
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
        preview_summary=preview.get("summary"),
        results=results,
        succeeded=succeeded,
        failed=len(results) - succeeded,
    )
