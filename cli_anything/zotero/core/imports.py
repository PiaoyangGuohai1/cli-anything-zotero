from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from cli_anything.zotero.core.discovery import RuntimeContext
from cli_anything.zotero.utils import zotero_http, zotero_sqlite


_TREE_VIEW_ID_RE = re.compile(r"^[LC]\d+$")
_PDF_MAGIC = b"%PDF-"
_ATTACHMENT_RESULT_CREATED = "created"
_ATTACHMENT_RESULT_FAILED = "failed"
_ATTACHMENT_RESULT_SKIPPED = "skipped_duplicate"


def _require_connector(runtime: RuntimeContext) -> None:
    if not runtime.connector_available:
        raise RuntimeError(f"Zotero connector is not available: {runtime.connector_message}")


def _read_text_file(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "utf-16", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(errors="replace")


def _read_json_items(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON import file: {path}: {exc}") from exc
    if isinstance(payload, dict):
        payload = payload.get("items")
    if not isinstance(payload, list):
        raise RuntimeError("JSON import expects an array of official Zotero connector item objects")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(f"JSON import item {index} is not an object")
        copied = dict(item)
        copied.setdefault("id", f"cli-anything-zotero-{index}")
        normalized.append(copied)
    return normalized


def _read_json_payload(path: Path, *, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON {label}: {path}: {exc}") from exc


def _default_user_library_target(runtime: RuntimeContext) -> str:
    sqlite_path = runtime.environment.sqlite_path
    if sqlite_path.exists():
        library_id = zotero_sqlite.default_library_id(sqlite_path)
        if library_id is not None:
            return f"L{library_id}"
    return "L1"


def _session_library_id(session: dict[str, Any] | None) -> int | None:
    session = session or {}
    current_library = session.get("current_library")
    if current_library is None:
        return None
    return zotero_sqlite.normalize_library_ref(current_library)


def _resolve_target(runtime: RuntimeContext, collection_ref: str | None, session: dict[str, Any] | None = None) -> dict[str, Any]:
    session = session or {}
    session_library_id = _session_library_id(session)
    if collection_ref:
        if _TREE_VIEW_ID_RE.match(collection_ref):
            kind = "library" if collection_ref.startswith("L") else "collection"
            return {"treeViewID": collection_ref, "source": "explicit", "kind": kind}
        collection = zotero_sqlite.resolve_collection(
            runtime.environment.sqlite_path,
            collection_ref,
            library_id=session_library_id,
        )
        if not collection:
            raise RuntimeError(f"Collection not found: {collection_ref}")
        return {
            "treeViewID": f"C{collection['collectionID']}",
            "source": "explicit",
            "kind": "collection",
            "collectionID": collection["collectionID"],
            "collectionKey": collection["key"],
            "collectionName": collection["collectionName"],
            "libraryID": collection["libraryID"],
        }

    current_collection = session.get("current_collection")
    if current_collection:
        if _TREE_VIEW_ID_RE.match(str(current_collection)):
            kind = "library" if str(current_collection).startswith("L") else "collection"
            return {"treeViewID": str(current_collection), "source": "session", "kind": kind}
        collection = zotero_sqlite.resolve_collection(
            runtime.environment.sqlite_path,
            current_collection,
            library_id=session_library_id,
        )
        if collection:
            return {
                "treeViewID": f"C{collection['collectionID']}",
                "source": "session",
                "kind": "collection",
                "collectionID": collection["collectionID"],
                "collectionKey": collection["key"],
                "collectionName": collection["collectionName"],
                "libraryID": collection["libraryID"],
            }

    if runtime.connector_available:
        selected = zotero_http.get_selected_collection(runtime.environment.port)
        if selected.get("id") is not None:
            return {
                "treeViewID": f"C{selected['id']}",
                "source": "selected",
                "kind": "collection",
                "collectionID": selected["id"],
                "collectionName": selected.get("name"),
                "libraryID": selected.get("libraryID"),
                "libraryName": selected.get("libraryName"),
            }
        return {
            "treeViewID": f"L{selected['libraryID']}",
            "source": "selected",
            "kind": "library",
            "libraryID": selected.get("libraryID"),
            "libraryName": selected.get("libraryName"),
        }

    return {
        "treeViewID": _default_user_library_target(runtime),
        "source": "user_library",
        "kind": "library",
    }


def _normalize_tags(tags: list[str] | tuple[str, ...]) -> list[str]:
    return [tag.strip() for tag in tags if tag and tag.strip()]


def _session_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _normalize_attachment_int(value: Any, *, name: str, minimum: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Attachment `{name}` must be an integer") from exc
    if normalized < minimum:
        comparator = "greater than or equal to" if minimum == 0 else f"at least {minimum}"
        raise RuntimeError(f"Attachment `{name}` must be {comparator}")
    return normalized


def _normalize_attachment_descriptor(
    raw: Any,
    *,
    index_label: str,
    attachment_label: str,
    default_delay_ms: int,
    default_timeout: int,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RuntimeError(f"{index_label} {attachment_label} must be an object")
    has_path = "path" in raw and raw.get("path") not in (None, "")
    has_url = "url" in raw and raw.get("url") not in (None, "")
    if has_path == has_url:
        raise RuntimeError(f"{index_label} {attachment_label} must include exactly one of `path` or `url`")
    title = str(raw.get("title") or "PDF").strip() or "PDF"
    delay_ms = _normalize_attachment_int(raw.get("delay_ms", default_delay_ms), name="delay_ms", minimum=0)
    timeout = _normalize_attachment_int(raw.get("timeout", default_timeout), name="timeout", minimum=1)
    if has_path:
        source = str(raw["path"]).strip()
        if not source:
            raise RuntimeError(f"{index_label} {attachment_label} path must not be empty")
        return {
            "source_type": "file",
            "source": source,
            "title": title,
            "delay_ms": delay_ms,
            "timeout": timeout,
        }
    source = str(raw["url"]).strip()
    if not source:
        raise RuntimeError(f"{index_label} {attachment_label} url must not be empty")
    return {
        "source_type": "url",
        "source": source,
        "title": title,
        "delay_ms": delay_ms,
        "timeout": timeout,
    }


def _extract_inline_attachment_plans(
    items: list[dict[str, Any]],
    *,
    default_delay_ms: int,
    default_timeout: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    stripped_items: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        copied = dict(item)
        raw_attachments = copied.pop("attachments", [])
        if raw_attachments in (None, []):
            stripped_items.append(copied)
            continue
        if not isinstance(raw_attachments, list):
            raise RuntimeError(f"JSON import item {index + 1} attachments must be an array")
        normalized = [
            _normalize_attachment_descriptor(
                descriptor,
                index_label=f"JSON import item {index + 1}",
                attachment_label=f"attachment {attachment_index + 1}",
                default_delay_ms=default_delay_ms,
                default_timeout=default_timeout,
            )
            for attachment_index, descriptor in enumerate(raw_attachments)
        ]
        plans.append({"index": index, "attachments": normalized})
        stripped_items.append(copied)
    return stripped_items, plans


def _read_attachment_manifest(
    path: Path,
    *,
    default_delay_ms: int,
    default_timeout: int,
) -> list[dict[str, Any]]:
    payload = _read_json_payload(path, label="attachment manifest")
    if not isinstance(payload, list):
        raise RuntimeError("Attachment manifest expects an array of {index, attachments} objects")
    manifest: list[dict[str, Any]] = []
    seen_indexes: set[int] = set()
    for entry_index, entry in enumerate(payload, start=1):
        label = f"manifest entry {entry_index}"
        if not isinstance(entry, dict):
            raise RuntimeError(f"{label} must be an object")
        if "index" not in entry:
            raise RuntimeError(f"{label} is missing required `index`")
        index = _normalize_attachment_int(entry["index"], name="index", minimum=0)
        if index in seen_indexes:
            raise RuntimeError(f"{label} reuses import index {index}")
        seen_indexes.add(index)
        attachments = entry.get("attachments")
        if not isinstance(attachments, list):
            raise RuntimeError(f"{label} attachments must be an array")
        normalized = [
            _normalize_attachment_descriptor(
                descriptor,
                index_label=label,
                attachment_label=f"attachment {attachment_index + 1}",
                default_delay_ms=default_delay_ms,
                default_timeout=default_timeout,
            )
            for attachment_index, descriptor in enumerate(attachments)
        ]
        expected_title = entry.get("expected_title")
        if expected_title is not None and not isinstance(expected_title, str):
            raise RuntimeError(f"{label} expected_title must be a string")
        manifest.append(
            {
                "index": index,
                "expected_title": expected_title,
                "attachments": normalized,
            }
        )
    return manifest


def _item_title(item: dict[str, Any]) -> str | None:
    for field in ("title", "bookTitle", "publicationTitle"):
        value = item.get(field)
        if value:
            return str(value)
    return None


def _normalize_url_for_dedupe(url: str) -> str:
    parsed = urllib.parse.urlsplit(url.strip())
    normalized_path = parsed.path or "/"
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), normalized_path, parsed.query, ""))


def _attachment_result(
    *,
    item_index: int,
    parent_connector_id: Any,
    descriptor: dict[str, Any],
    status: str,
    error: str | None = None,
) -> dict[str, Any]:
    payload = {
        "item_index": item_index,
        "parent_connector_id": parent_connector_id,
        "source_type": descriptor["source_type"],
        "source": descriptor["source"],
        "title": descriptor["title"],
        "status": status,
    }
    if error is not None:
        payload["error"] = error
    return payload


def _attachment_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "planned_count": len(results),
        "created_count": sum(1 for result in results if result["status"] == _ATTACHMENT_RESULT_CREATED),
        "failed_count": sum(1 for result in results if result["status"] == _ATTACHMENT_RESULT_FAILED),
        "skipped_count": sum(1 for result in results if result["status"] == _ATTACHMENT_RESULT_SKIPPED),
    }


def _ensure_pdf_bytes(content: bytes, *, source: str) -> None:
    if not content.startswith(_PDF_MAGIC):
        raise RuntimeError(f"Attachment source is not a PDF: {source}")


def _read_local_pdf(path_text: str) -> tuple[bytes, str]:
    path = Path(path_text).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Attachment file not found: {path}")
    resolved = path.resolve()
    content = resolved.read_bytes()
    _ensure_pdf_bytes(content, source=str(resolved))
    return content, resolved.as_uri()


def _download_remote_pdf(url: str, *, delay_ms: int, timeout: int) -> bytes:
    if delay_ms:
        time.sleep(delay_ms / 1000)
    request = urllib.request.Request(url, headers={"Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", response.getcode())
            if int(status) != 200:
                raise RuntimeError(f"Attachment download returned HTTP {status}: {url}")
            content = response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Attachment download returned HTTP {exc.code}: {url}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Attachment download failed for {url}: {exc.reason}") from exc
    _ensure_pdf_bytes(content, source=url)
    return content


def _perform_attachment_upload(
    runtime: RuntimeContext,
    *,
    session_id: str,
    connector_items: list[dict[str, Any]],
    plans: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    seen_by_item: dict[str, dict[str, set[str]]] = {}
    for plan in plans:
        item_index = int(plan["index"])
        attachments = list(plan.get("attachments") or [])
        imported_item = connector_items[item_index] if 0 <= item_index < len(connector_items) else None
        expected_title = plan.get("expected_title")
        if imported_item is None:
            message = f"Import returned no item at index {item_index}"
            results.extend(
                _attachment_result(
                    item_index=item_index,
                    parent_connector_id=None,
                    descriptor=descriptor,
                    status=_ATTACHMENT_RESULT_FAILED,
                    error=message,
                )
                for descriptor in attachments
            )
            continue
        imported_title = _item_title(imported_item)
        if expected_title is not None and imported_title != expected_title:
            message = (
                f"Imported item title mismatch at index {item_index}: "
                f"expected {expected_title!r}, got {imported_title!r}"
            )
            results.extend(
                _attachment_result(
                    item_index=item_index,
                    parent_connector_id=imported_item.get("id"),
                    descriptor=descriptor,
                    status=_ATTACHMENT_RESULT_FAILED,
                    error=message,
                )
                for descriptor in attachments
            )
            continue
        parent_connector_id = imported_item.get("id")
        if not parent_connector_id:
            message = f"Imported item at index {item_index} did not include a connector id"
            results.extend(
                _attachment_result(
                    item_index=item_index,
                    parent_connector_id=None,
                    descriptor=descriptor,
                    status=_ATTACHMENT_RESULT_FAILED,
                    error=message,
                )
                for descriptor in attachments
            )
            continue

        dedupe_state = seen_by_item.setdefault(
            str(parent_connector_id),
            {"paths": set(), "urls": set(), "hashes": set()},
        )
        for descriptor in attachments:
            try:
                if descriptor["source_type"] == "file":
                    canonical_path = str(Path(descriptor["source"]).expanduser().resolve())
                    if canonical_path in dedupe_state["paths"]:
                        results.append(
                            _attachment_result(
                                item_index=item_index,
                                parent_connector_id=parent_connector_id,
                                descriptor=descriptor,
                                status=_ATTACHMENT_RESULT_SKIPPED,
                            )
                        )
                        continue
                    content, metadata_url = _read_local_pdf(descriptor["source"])
                else:
                    normalized_url = _normalize_url_for_dedupe(descriptor["source"])
                    if normalized_url in dedupe_state["urls"]:
                        results.append(
                            _attachment_result(
                                item_index=item_index,
                                parent_connector_id=parent_connector_id,
                                descriptor=descriptor,
                                status=_ATTACHMENT_RESULT_SKIPPED,
                            )
                        )
                        continue
                    content = _download_remote_pdf(
                        descriptor["source"],
                        delay_ms=int(descriptor["delay_ms"]),
                        timeout=int(descriptor["timeout"]),
                    )
                    metadata_url = descriptor["source"]

                content_hash = hashlib.sha256(content).hexdigest()
                if content_hash in dedupe_state["hashes"]:
                    results.append(
                        _attachment_result(
                            item_index=item_index,
                            parent_connector_id=parent_connector_id,
                            descriptor=descriptor,
                            status=_ATTACHMENT_RESULT_SKIPPED,
                        )
                    )
                    continue

                zotero_http.connector_save_attachment(
                    runtime.environment.port,
                    session_id=session_id,
                    parent_item_id=parent_connector_id,
                    title=descriptor["title"],
                    url=metadata_url,
                    content=content,
                    timeout=int(descriptor["timeout"]),
                )
                dedupe_state["hashes"].add(content_hash)
                if descriptor["source_type"] == "file":
                    dedupe_state["paths"].add(canonical_path)
                else:
                    dedupe_state["urls"].add(normalized_url)
                results.append(
                    _attachment_result(
                        item_index=item_index,
                        parent_connector_id=parent_connector_id,
                        descriptor=descriptor,
                        status=_ATTACHMENT_RESULT_CREATED,
                    )
                )
            except Exception as exc:
                results.append(
                    _attachment_result(
                        item_index=item_index,
                        parent_connector_id=parent_connector_id,
                        descriptor=descriptor,
                        status=_ATTACHMENT_RESULT_FAILED,
                        error=str(exc),
                    )
                )
    return _attachment_summary(results), results


def enable_local_api(
    runtime: RuntimeContext,
    *,
    launch: bool = False,
    wait_timeout: int = 30,
) -> dict[str, Any]:
    profile_dir = runtime.environment.profile_dir
    if profile_dir is None:
        raise RuntimeError("Active Zotero profile could not be resolved")
    before = runtime.environment.local_api_enabled_configured
    written_path = runtime.environment.profile_dir / "user.js"
    from cli_anything.zotero.utils import zotero_paths  # local import to avoid cycle
    zotero_paths.ensure_local_api_enabled(profile_dir)
    payload = {
        "profile_dir": str(profile_dir),
        "user_js_path": str(written_path),
        "already_enabled": before,
        "enabled": True,
        "launched": False,
        "connector_ready": runtime.connector_available,
        "local_api_ready": runtime.local_api_available,
    }
    if launch:
        from cli_anything.zotero.core import discovery  # local import to avoid cycle
        refreshed = discovery.build_runtime_context(
            backend=runtime.backend,
            data_dir=str(runtime.environment.data_dir),
            profile_dir=str(profile_dir),
            executable=str(runtime.environment.executable) if runtime.environment.executable else None,
        )
        launch_payload = discovery.launch_zotero(refreshed, wait_timeout=wait_timeout)
        payload.update(
            {
                "launched": True,
                "launch": launch_payload,
                "connector_ready": launch_payload["connector_ready"],
                "local_api_ready": launch_payload["local_api_ready"],
            }
        )
    return payload


def import_file(
    runtime: RuntimeContext,
    path: str | Path,
    *,
    collection_ref: str | None = None,
    tags: list[str] | tuple[str, ...] = (),
    session: dict[str, Any] | None = None,
    attachments_manifest: str | Path | None = None,
    attachment_delay_ms: int = 0,
    attachment_timeout: int = 60,
    connector_timeout: int = 120,
    split_bib: bool = True,
) -> dict[str, Any]:
    _require_connector(runtime)
    source_path = Path(path).expanduser()
    if not source_path.exists():
        raise FileNotFoundError(f"Import file not found: {source_path}")
    content = _read_text_file(source_path)
    manifest_path = Path(attachments_manifest).expanduser() if attachments_manifest is not None else None
    plans = (
        _read_attachment_manifest(
            manifest_path,
            default_delay_ms=attachment_delay_ms,
            default_timeout=attachment_timeout,
        )
        if manifest_path is not None
        else []
    )
    # Detect content type from file extension for connector/import
    _content_types = {
        ".bib": "text/x-bibtex", ".bibtex": "text/x-bibtex",
        ".ris": "application/x-research-info-systems",
        ".enw": "application/x-endnote-refer", ".refer": "application/x-endnote-refer",
        ".xml": "text/xml", ".mods": "text/xml",
        ".csv": "text/csv",
    }
    ct = _content_types.get(source_path.suffix.lower(), "text/plain")
    entry_count = _count_bibtex_entries(content) if ct == "text/x-bibtex" else 1
    # Large multi-entry BibTeX files frequently exceed a single connector call.
    if split_bib and ct == "text/x-bibtex" and entry_count > 1:
        return _import_bibtex_entries(
            runtime,
            content,
            source_path=source_path,
            collection_ref=collection_ref,
            tags=tags,
            session=session,
            plans=plans,
            connector_timeout=connector_timeout,
        )

    session_id = _session_id("import-file")
    imported = zotero_http.connector_import_text(
        runtime.environment.port,
        content,
        session_id=session_id,
        content_type=ct,
        timeout=connector_timeout,
    )
    target = _resolve_target(runtime, collection_ref, session=session)
    normalized_tags = _normalize_tags(list(tags))
    zotero_http.connector_update_session(
        runtime.environment.port,
        session_id=session_id,
        target=target["treeViewID"],
        tags=normalized_tags,
    )
    attachment_summary, attachment_results = _perform_attachment_upload(
        runtime,
        session_id=session_id,
        connector_items=imported,
        plans=plans,
    )
    return {
        "action": "import_file",
        "path": str(source_path),
        "status": "partial_success" if attachment_summary["failed_count"] else "success",
        "sessionID": session_id,
        "target": target,
        "tags": normalized_tags,
        "imported_count": len(imported),
        "items": imported,
        "attachment_summary": attachment_summary,
        "attachment_results": attachment_results,
        "connector_timeout": connector_timeout,
        "entry_count": entry_count,
    }


def import_json(
    runtime: RuntimeContext,
    path: str | Path,
    *,
    collection_ref: str | None = None,
    tags: list[str] | tuple[str, ...] = (),
    session: dict[str, Any] | None = None,
    attachment_delay_ms: int = 0,
    attachment_timeout: int = 60,
) -> dict[str, Any]:
    _require_connector(runtime)
    source_path = Path(path).expanduser()
    if not source_path.exists():
        raise FileNotFoundError(f"Import JSON file not found: {source_path}")
    items = _read_json_items(source_path)
    items, plans = _extract_inline_attachment_plans(
        items,
        default_delay_ms=attachment_delay_ms,
        default_timeout=attachment_timeout,
    )
    session_id = _session_id("import-json")
    zotero_http.connector_save_items(runtime.environment.port, items, session_id=session_id)
    target = _resolve_target(runtime, collection_ref, session=session)
    normalized_tags = _normalize_tags(list(tags))
    zotero_http.connector_update_session(
        runtime.environment.port,
        session_id=session_id,
        target=target["treeViewID"],
        tags=normalized_tags,
    )
    attachment_summary, attachment_results = _perform_attachment_upload(
        runtime,
        session_id=session_id,
        connector_items=items,
        plans=plans,
    )
    return {
        "action": "import_json",
        "path": str(source_path),
        "status": "partial_success" if attachment_summary["failed_count"] else "success",
        "sessionID": session_id,
        "target": target,
        "tags": normalized_tags,
        "submitted_count": len(items),
        "items": [
            {
                "id": item.get("id"),
                "itemType": item.get("itemType"),
                "title": item.get("title") or item.get("bookTitle") or item.get("publicationTitle"),
            }
            for item in items
        ],
        "attachment_summary": attachment_summary,
        "attachment_results": attachment_results,
    }


# ── DOI helpers / robust import ──────────────────────────────────────

_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+", re.I)
_BIBTEX_ENTRY_RE = re.compile(r"(?m)^\s*@\w+\s*\{")


def normalize_doi(doi: str) -> str:
    """Strip URL prefixes and trailing punctuation from a DOI string."""
    text = (doi or "").strip()
    text = re.sub(r"^https?://(dx\.)?doi\.org/", "", text, flags=re.I)
    text = re.sub(r"^doi:\s*", "", text, flags=re.I)
    text = text.strip().rstrip(" .),;")
    return text


def _count_bibtex_entries(content: str) -> int:
    return len(_BIBTEX_ENTRY_RE.findall(content or ""))


def _split_bibtex_entries(content: str) -> list[str]:
    """Split a BibTeX file into individual entry strings."""
    text = content or ""
    matches = list(_BIBTEX_ENTRY_RE.finditer(text))
    if not matches:
        stripped = text.strip()
        return [stripped] if stripped else []
    entries: list[str] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        entry = text[start:end].strip()
        if entry:
            entries.append(entry)
    return entries


def fetch_crossref_bibtex(doi: str, *, timeout: int = 30) -> str:
    """Fetch BibTeX metadata for a DOI from Crossref content negotiation."""
    normalized = normalize_doi(doi)
    if not normalized:
        raise RuntimeError("DOI is empty")
    url = f"https://api.crossref.org/works/{urllib.parse.quote(normalized)}/transform/application/x-bibtex"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "cli-anything-zotero/1.0 (mailto:cli-anything@local)",
            "Accept": "application/x-bibtex",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace").strip()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Crossref BibTeX fetch failed for {normalized}: HTTP {exc.code} {detail}") from exc
    except Exception as exc:
        raise RuntimeError(f"Crossref BibTeX fetch failed for {normalized}: {exc}") from exc
    if not body or "@" not in body:
        raise RuntimeError(f"Crossref returned empty/invalid BibTeX for {normalized}")
    return body


def _import_bibtex_entries(
    runtime: RuntimeContext,
    content: str,
    *,
    source_path: Path,
    collection_ref: str | None,
    tags: list[str] | tuple[str, ...],
    session: dict[str, Any] | None,
    plans: list[dict[str, Any]],
    connector_timeout: int,
) -> dict[str, Any]:
    entries = _split_bibtex_entries(content)
    target = _resolve_target(runtime, collection_ref, session=session)
    normalized_tags = _normalize_tags(list(tags))
    all_items: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    attachment_results: list[dict[str, Any]] = []

    for index, entry in enumerate(entries):
        session_id = _session_id(f"import-file-part-{index}")
        try:
            imported = zotero_http.connector_import_text(
                runtime.environment.port,
                entry,
                session_id=session_id,
                content_type="text/x-bibtex",
                timeout=connector_timeout,
            )
            zotero_http.connector_update_session(
                runtime.environment.port,
                session_id=session_id,
                target=target["treeViewID"],
                tags=normalized_tags,
            )
            # Attachment plans only apply to a single bulk import; skip on split path unless one entry.
            if plans and len(entries) == 1:
                _summary, part_results = _perform_attachment_upload(
                    runtime,
                    session_id=session_id,
                    connector_items=imported,
                    plans=plans,
                )
                attachment_results.extend(part_results)
            all_items.extend(imported)
        except Exception as exc:
            failures.append({"index": index, "error": str(exc), "entry_preview": entry[:120]})

    status = "success"
    if failures and all_items:
        status = "partial_success"
    elif failures and not all_items:
        status = "error"
    return {
        "action": "import_file",
        "path": str(source_path),
        "status": status,
        "target": target,
        "tags": normalized_tags,
        "imported_count": len(all_items),
        "items": all_items,
        "failed_count": len(failures),
        "failures": failures,
        "split_bib": True,
        "entry_count": len(entries),
        "connector_timeout": connector_timeout,
        "attachment_summary": _attachment_summary(attachment_results) if attachment_results else {
            "planned_count": 0,
            "created_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
        },
        "attachment_results": attachment_results,
    }


def _application_import_payload(transport: dict[str, Any]) -> dict[str, Any] | None:
    """Extract application-level import result from a JS bridge transport envelope."""
    if not isinstance(transport, dict):
        return None
    if not transport.get("ok"):
        return {
            "ok": False,
            "code": "BRIDGE_ERROR",
            "error": transport.get("error") or "JS bridge transport failed",
            "error_name": transport.get("error_name"),
            "error_stack": transport.get("error_stack"),
        }
    data = transport.get("data")
    if data is None:
        return {
            "ok": False,
            "code": "EMPTY_RESULT",
            "error": "JS bridge returned empty success (data is null) — import did not complete",
        }
    if isinstance(data, dict):
        # Structured payload from import_from_doi/pmid
        if "ok" in data:
            return data
        return {"ok": True, "result": data}
    if isinstance(data, str):
        text = data.strip()
        if text.startswith("OK:") or text.startswith("FOUND:"):
            # Legacy string form: OK: imported Title (key: ABCDEFGH)
            key = None
            m = re.search(r"\(key:\s*([A-Z0-9]+)\)", text)
            if m:
                key = m.group(1)
            return {"ok": True, "code": "IMPORTED", "message": text, "key": key}
        if text.startswith("ERROR:") or text.startswith("NOT_FOUND") or text.startswith("TIMEOUT"):
            return {"ok": False, "code": "LEGACY_ERROR", "error": text}
        return {"ok": False, "code": "UNEXPECTED_RESULT", "error": text}
    return {"ok": False, "code": "UNEXPECTED_RESULT", "error": f"Unexpected bridge data type: {type(data).__name__}"}


def import_doi(
    runtime: RuntimeContext,
    bridge: Any,
    doi: str,
    *,
    collection_key: str | None = None,
    tags: list[str] | tuple[str, ...] = (),
    session: dict[str, Any] | None = None,
    dedupe: bool = True,
    if_exists: str = "file",
    prefer_translator: bool = True,
    connector_timeout: int = 120,
    library_id: int = 1,
) -> dict[str, Any]:
    """Import a DOI robustly: optional dedupe → Zotero translator → Crossref BibTeX fallback.

    if_exists:
      - file: reuse existing DOI item; add collection/tags if missing
      - skip: reuse without modifying membership/tags
      - duplicate: always create a new item (implies no dedupe)
    """
    from cli_anything.zotero.core.results import normalize_if_exists, result_payload

    try:
        if_exists = normalize_if_exists(if_exists)
    except ValueError as exc:
        return result_payload(
            action="import_doi",
            ok=False,
            status="error",
            code="INVALID_IF_EXISTS",
            DOI=doi,
            error=str(exc),
        )

    if if_exists == "duplicate":
        dedupe = False
    elif if_exists in {"file", "skip"}:
        dedupe = True

    normalized = normalize_doi(doi)
    if not normalized or not _DOI_RE.search(normalized):
        return result_payload(
            action="import_doi",
            ok=False,
            status="error",
            code="INVALID_DOI",
            DOI=doi,
            error=f"Invalid DOI: {doi!r}",
        )

    tag_list = list(tags) if tags else []
    attempts: list[dict[str, Any]] = []

    # 1) Dedupe by existing DOI in library
    if dedupe and hasattr(bridge, "find_items_by_doi"):
        existing_transport = bridge.find_items_by_doi(normalized, library_id=library_id)
        existing_data = existing_transport.get("data") if existing_transport.get("ok") else None
        if isinstance(existing_data, list) and existing_data:
            item = existing_data[0]
            key = item.get("key")
            modified = False
            if if_exists == "file":
                if key and collection_key and hasattr(bridge, "add_to_collection"):
                    try:
                        bridge.add_to_collection(key, collection_key, library_id=library_id)
                        modified = True
                    except Exception:
                        pass
                if key and tag_list and hasattr(bridge, "manage_tags"):
                    try:
                        bridge.manage_tags(key, tag_list, [], library_id=library_id)
                        modified = True
                    except Exception:
                        pass
            return result_payload(
                action="import_doi",
                ok=True,
                status="already_exists",
                code="ALREADY_EXISTS",
                DOI=normalized,
                key=key,
                title=item.get("title"),
                source="library-dedupe",
                if_exists=if_exists,
                modified=modified,
                existing_count=len(existing_data),
                attempts=attempts,
            )

    # 2) Zotero built-in DOI translator
    translator_error = None
    if prefer_translator and hasattr(bridge, "import_from_doi"):
        transport = bridge.import_from_doi(
            normalized,
            collection_key=collection_key,
            tags=tag_list or None,
            library_id=library_id,
        )
        app = _application_import_payload(transport) or {
            "ok": False,
            "code": "BRIDGE_ERROR",
            "error": "translator import returned no payload",
        }
        attempts.append({"step": "zotero-translator", **{k: app.get(k) for k in ("ok", "code", "error", "key")}})
        if app.get("ok") and app.get("key"):
            return result_payload(
                action="import_doi",
                ok=True,
                status="success",
                code=app.get("code") or "IMPORTED",
                DOI=normalized,
                key=app.get("key"),
                title=app.get("title"),
                source=app.get("source") or "zotero-translator",
                if_exists=if_exists,
                attempts=attempts,
            )
        # Also accept ok with key missing but message (legacy)
        if app.get("ok") and not app.get("error"):
            return result_payload(
                action="import_doi",
                ok=True,
                status="success",
                code=app.get("code") or "IMPORTED",
                DOI=normalized,
                key=app.get("key"),
                title=app.get("title"),
                source=app.get("source") or "zotero-translator",
                message=app.get("message"),
                if_exists=if_exists,
                attempts=attempts,
            )
        translator_error = app.get("error") or app.get("code") or "translator failed"

    # 3) Crossref BibTeX → connector import
    try:
        _require_connector(runtime)
        bibtex = fetch_crossref_bibtex(normalized)
        session_id = _session_id("import-doi-crossref")
        imported = zotero_http.connector_import_text(
            runtime.environment.port,
            bibtex,
            session_id=session_id,
            content_type="text/x-bibtex",
            timeout=connector_timeout,
        )
        target = _resolve_target(runtime, collection_key, session=session)
        normalized_tags = _normalize_tags(tag_list)
        zotero_http.connector_update_session(
            runtime.environment.port,
            session_id=session_id,
            target=target["treeViewID"],
            tags=normalized_tags,
        )
        item0 = imported[0] if imported else {}
        key = item0.get("key")
        attempts.append({"step": "crossref-bibtex", "ok": True, "key": key})
        return result_payload(
            action="import_doi",
            ok=True,
            status="success",
            code="IMPORTED",
            DOI=normalized,
            key=key,
            title=item0.get("title"),
            source="crossref-bibtex",
            items=imported,
            target=target,
            tags=normalized_tags,
            translator_error=translator_error,
            if_exists=if_exists,
            attempts=attempts,
        )
    except Exception as exc:
        attempts.append({"step": "crossref-bibtex", "ok": False, "error": str(exc)})
        return result_payload(
            action="import_doi",
            ok=False,
            status="error",
            code="IMPORT_FAILED",
            DOI=normalized,
            error=str(exc),
            translator_error=translator_error,
            if_exists=if_exists,
            attempts=attempts,
        )

