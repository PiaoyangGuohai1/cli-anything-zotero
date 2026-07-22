"""Unified literature ingest entrypoints for agents."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from cli_anything.zotero.core import imports as imports_mod
from cli_anything.zotero.core import pdf_fetch
from cli_anything.zotero.core.results import result_payload
from cli_anything.zotero.utils import zotero_http

_ARXIV_ID_RE = re.compile(r"^(?:arXiv:)?(\d{4}\.\d{4,5})(?:v\d+)?$", re.I)


def normalize_arxiv_id(value: str) -> str:
    text = (value or "").strip()
    text = re.sub(r"^https?://(www\.)?arxiv\.org/(abs|pdf)/", "", text, flags=re.I)
    text = text.replace(".pdf", "")
    m = _ARXIV_ID_RE.match(text) or re.search(r"(\d{4}\.\d{4,5})", text)
    if not m:
        raise ValueError(f"Invalid arXiv id: {value!r}")
    return m.group(1) if m.lastindex else m.group(0)


def add_doi(
    runtime: Any,
    bridge: Any,
    doi: str,
    *,
    collection_key: str | None = None,
    tags: list[str] | tuple[str, ...] = (),
    session: dict[str, Any] | None = None,
    if_exists: str = "file",
    prefer_translator: bool = True,
    fetch_pdf: bool = False,
    pdf_sources: str | None = None,
    library_id: int = 1,
) -> dict[str, Any]:
    imported = imports_mod.import_doi(
        runtime,
        bridge,
        doi,
        collection_key=collection_key,
        tags=tags,
        session=session,
        if_exists=if_exists,
        prefer_translator=prefer_translator,
        library_id=library_id,
    )
    payload = result_payload(
        action="add_doi",
        ok=bool(imported.get("ok")),
        status=imported.get("status") or ("success" if imported.get("ok") else "error"),
        code=imported.get("code"),
        error=imported.get("error"),
        DOI=imported.get("DOI") or imports_mod.normalize_doi(doi),
        key=imported.get("key"),
        title=imported.get("title"),
        source=imported.get("source"),
        import_result=imported,
    )
    if not payload["ok"] or not payload.get("key") or not fetch_pdf:
        return payload

    sources = pdf_fetch.parse_sources(pdf_sources)
    pdf_result = pdf_fetch.fetch_pdf_for_item(
        runtime,
        bridge,
        payload["key"],
        sources=sources,
        library_id=library_id,
    )
    payload["pdf"] = pdf_result
    if not pdf_result.get("ok") and pdf_result.get("status") != "already_has_pdf":
        payload["status"] = "partial_success"
        payload["code"] = "IMPORTED_PDF_MISSING"
    return payload


def add_arxiv(
    runtime: Any,
    bridge: Any,
    arxiv_id: str,
    *,
    collection_key: str | None = None,
    tags: list[str] | tuple[str, ...] = (),
    session: dict[str, Any] | None = None,
    if_exists: str = "file",
    fetch_pdf: bool = True,
    pdf_sources: str | None = None,
    library_id: int = 1,
) -> dict[str, Any]:
    try:
        aid = normalize_arxiv_id(arxiv_id)
    except ValueError as exc:
        return result_payload(action="add_arxiv", ok=False, status="error", code="INVALID_ARXIV", error=str(exc))

    doi = f"10.48550/arXiv.{aid}"
    # Prefer DOI path first; Crossref often has arXiv DOIs. Fallback to arXiv bibtex.
    imported = imports_mod.import_doi(
        runtime,
        bridge,
        doi,
        collection_key=collection_key,
        tags=tags,
        session=session,
        if_exists=if_exists,
        prefer_translator=True,
        library_id=library_id,
    )
    if not imported.get("ok"):
        # Fallback: arXiv bibtex endpoint
        try:
            imports_mod._require_connector(runtime)
            bib_url = f"https://arxiv.org/bibtex/{aid}"
            req_data, _, _ = pdf_fetch._http_get_bytes(bib_url, timeout=30, accept="application/x-bibtex,text/plain,*/*")
            bibtex = req_data.decode("utf-8", errors="replace")
            if "@" not in bibtex:
                raise RuntimeError("empty arXiv bibtex")
            session_id = imports_mod._session_id("add-arxiv")
            items = zotero_http.connector_import_text(
                runtime.environment.port,
                bibtex,
                session_id=session_id,
                content_type="text/x-bibtex",
                timeout=120,
            )
            target = imports_mod._resolve_target(runtime, collection_key, session=session)
            tags_n = imports_mod._normalize_tags(list(tags))
            zotero_http.connector_update_session(
                runtime.environment.port,
                session_id=session_id,
                target=target["treeViewID"],
                tags=tags_n,
            )
            item0 = items[0] if items else {}
            imported = result_payload(
                action="import_doi",
                ok=True,
                status="success",
                code="IMPORTED",
                key=item0.get("key"),
                title=item0.get("title"),
                DOI=doi,
                source="arxiv-bibtex",
                items=items,
            )
        except Exception as exc:
            return result_payload(
                action="add_arxiv",
                ok=False,
                status="error",
                code="IMPORT_FAILED",
                error=str(exc),
                arxiv_id=aid,
                DOI=doi,
                import_result=imported,
            )

    payload = result_payload(
        action="add_arxiv",
        ok=bool(imported.get("ok")),
        status=imported.get("status") or "success",
        code=imported.get("code") or "IMPORTED",
        key=imported.get("key"),
        title=imported.get("title"),
        DOI=imported.get("DOI") or doi,
        arxiv_id=aid,
        source=imported.get("source"),
        import_result=imported,
    )
    if payload["ok"] and payload.get("key") and fetch_pdf:
        sources = pdf_fetch.parse_sources(pdf_sources or "zotero,arxiv,unpaywall")
        pdf_result = pdf_fetch.fetch_pdf_for_item(
            runtime,
            bridge,
            payload["key"],
            sources=sources,
            library_id=library_id,
        )
        payload["pdf"] = pdf_result
        if not pdf_result.get("ok") and pdf_result.get("status") != "already_has_pdf":
            payload["status"] = "partial_success"
            payload["code"] = "IMPORTED_PDF_MISSING"
    return payload


def add_url(
    runtime: Any,
    bridge: Any,
    url: str,
    *,
    collection_key: str | None = None,
    tags: list[str] | tuple[str, ...] = (),
    session: dict[str, Any] | None = None,
    if_exists: str = "file",
    fetch_pdf: bool = False,
    pdf_sources: str | None = None,
    library_id: int = 1,
) -> dict[str, Any]:
    """Ingest from a URL: arXiv / DOI / generic webpage."""
    text = (url or "").strip()
    if not text:
        return result_payload(action="add_url", ok=False, status="error", code="INVALID_URL", error="empty url")

    # arXiv
    if "arxiv.org" in text.lower() or re.search(r"\d{4}\.\d{4,5}", text):
        try:
            normalize_arxiv_id(text)
            payload = add_arxiv(
                runtime,
                bridge,
                text,
                collection_key=collection_key,
                tags=tags,
                session=session,
                if_exists=if_exists,
                fetch_pdf=fetch_pdf,
                pdf_sources=pdf_sources,
                library_id=library_id,
            )
            payload["action"] = "add_url"
            payload["url"] = text
            payload["url_kind"] = "arxiv"
            return payload
        except ValueError:
            pass

    # DOI URL or bare DOI in URL
    doi_match = re.search(r"(10\.\d{4,9}/[^\s?#]+)", text)
    if "doi.org" in text.lower() or doi_match:
        doi = imports_mod.normalize_doi(doi_match.group(1) if doi_match else text)
        payload = add_doi(
            runtime,
            bridge,
            doi,
            collection_key=collection_key,
            tags=tags,
            session=session,
            if_exists=if_exists,
            fetch_pdf=fetch_pdf,
            pdf_sources=pdf_sources,
            library_id=library_id,
        )
        payload["action"] = "add_url"
        payload["url"] = text
        payload["url_kind"] = "doi"
        return payload

    # Generic webpage item via connector
    try:
        imports_mod._require_connector(runtime)
        title = text
        try:
            raw, final, _ = pdf_fetch._http_get_bytes(text, timeout=20, accept="text/html,*/*")
            html = raw.decode("utf-8", errors="ignore")
            m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
            if m:
                title = re.sub(r"\s+", " ", m.group(1)).strip()[:300] or text
            # DOI on page?
            dm = re.search(r"(10\.\d{4,9}/[A-Za-z0-9./_;()-]+)", html)
            if dm:
                return add_url(
                    runtime,
                    bridge,
                    f"https://doi.org/{imports_mod.normalize_doi(dm.group(1))}",
                    collection_key=collection_key,
                    tags=tags,
                    session=session,
                    if_exists=if_exists,
                    fetch_pdf=fetch_pdf,
                    pdf_sources=pdf_sources,
                    library_id=library_id,
                )
        except Exception:
            final = text

        item = {
            "itemType": "webpage",
            "title": title,
            "url": text,
            "id": "cli-anything-url-1",
            "accessDate": "",
        }
        session_id = imports_mod._session_id("add-url")
        zotero_http.connector_save_items(runtime.environment.port, [item], session_id=session_id)
        target = imports_mod._resolve_target(runtime, collection_key, session=session)
        tags_n = imports_mod._normalize_tags(list(tags))
        zotero_http.connector_update_session(
            runtime.environment.port,
            session_id=session_id,
            target=target["treeViewID"],
            tags=tags_n,
        )
        return result_payload(
            action="add_url",
            ok=True,
            status="success",
            code="IMPORTED",
            url=text,
            url_kind="webpage",
            title=title,
            source="connector-webpage",
            final_url=final,
            target=target,
        )
    except Exception as exc:
        return result_payload(
            action="add_url",
            ok=False,
            status="error",
            code="IMPORT_FAILED",
            url=text,
            error=str(exc),
        )


def add_bibtex(
    runtime: Any,
    path: str | Path,
    *,
    collection_key: str | None = None,
    tags: list[str] | tuple[str, ...] = (),
    session: dict[str, Any] | None = None,
) -> dict[str, Any]:
    imported = imports_mod.import_file(
        runtime,
        path,
        collection_ref=collection_key,
        tags=tags,
        session=session,
    )
    ok = imported.get("status") in {"success", "partial_success"}
    return result_payload(
        action="add_bibtex",
        ok=ok and imported.get("status") != "error",
        status=imported.get("status") or "success",
        code="IMPORTED" if ok else "IMPORT_FAILED",
        imported_count=imported.get("imported_count"),
        items=imported.get("items"),
        import_result=imported,
        error=None if ok else "bibtex import failed",
    )


def add_file(
    runtime: Any,
    bridge: Any,
    path: str | Path,
    *,
    collection_key: str | None = None,
    tags: list[str] | tuple[str, ...] = (),
    session: dict[str, Any] | None = None,
    if_exists: str = "file",
    fetch_pdf: bool = False,
    library_id: int = 1,
) -> dict[str, Any]:
    source = Path(path).expanduser()
    if not source.is_file():
        return result_payload(
            action="add_file",
            ok=False,
            status="error",
            code="FILE_NOT_FOUND",
            error=f"File not found: {source}",
        )

    suffix = source.suffix.lower()
    if suffix in {".bib", ".bibtex", ".ris", ".enw", ".csv", ".json"}:
        if suffix == ".json":
            imported = imports_mod.import_json(
                runtime,
                source,
                collection_ref=collection_key,
                tags=tags,
                session=session,
            )
            return result_payload(
                action="add_file",
                ok=imported.get("status") != "error",
                status=imported.get("status") or "success",
                code="IMPORTED",
                path=str(source),
                kind="json",
                import_result=imported,
                imported_count=imported.get("submitted_count"),
            )
        imported = imports_mod.import_file(
            runtime,
            source,
            collection_ref=collection_key,
            tags=tags,
            session=session,
        )
        return result_payload(
            action="add_file",
            ok=imported.get("status") in {"success", "partial_success"},
            status=imported.get("status") or "success",
            code="IMPORTED",
            path=str(source),
            kind=suffix.lstrip("."),
            import_result=imported,
            imported_count=imported.get("imported_count"),
        )

    if suffix == ".pdf":
        # Try DOI from filename
        doi_match = re.search(r"(10\.\d{4,9}/[^\s]+)", source.stem.replace("_", "/"))
        doi = imports_mod.normalize_doi(doi_match.group(1)) if doi_match else ""
        if doi and re.search(r"10\.\d{4,9}/", doi):
            imported = imports_mod.import_doi(
                runtime,
                bridge,
                doi,
                collection_key=collection_key,
                tags=tags,
                session=session,
                if_exists=if_exists,
                library_id=library_id,
            )
            key = imported.get("key")
            if imported.get("ok") and key:
                attach = bridge.attach_pdf(key, str(source), library_id=library_id)
                return result_payload(
                    action="add_file",
                    ok=True,
                    status="success" if attach.get("ok") else "partial_success",
                    code="IMPORTED_WITH_PDF" if attach.get("ok") else "IMPORTED_ATTACH_FAILED",
                    path=str(source),
                    kind="pdf",
                    DOI=doi,
                    key=key,
                    title=imported.get("title"),
                    import_result=imported,
                    attach_result=attach.get("data"),
                    error=None if attach.get("ok") else attach.get("error"),
                )

        # Standalone PDF attachment import via bridge
        abs_path = str(source.resolve()).replace("\\", "\\\\").replace("'", "\\'")
        title = source.stem.replace("'", "\\'")
        js = (
            f"var att = await Zotero.Attachments.importFromFile({{file: '{abs_path}'}}); "
            f"if (!att) {{ return {{ok:false, error:'importFromFile returned empty'}}; }} "
            f"att.setField('title', '{title}'); "
            f"await att.saveTx(); "
        )
        if collection_key:
            js += (
                f"var col = Zotero.Collections.getByLibraryAndKey({library_id}, '{collection_key}'); "
                f"if (col) {{ att.addToCollection(col.id); await att.saveTx(); }} "
            )
        if tags:
            for t in tags:
                safe = str(t).replace("'", "\\'")
                js += f"att.addTag('{safe}'); "
            js += "await att.saveTx(); "
        js += "return {ok:true, key: att.key, title: att.getField('title')};"
        transport = bridge.execute_js(js, wait_seconds=30)
        data = transport.get("data") if transport.get("ok") else None
        if isinstance(data, dict) and data.get("ok"):
            return result_payload(
                action="add_file",
                ok=True,
                status="success",
                code="ATTACHED_STANDALONE",
                path=str(source),
                kind="pdf",
                key=data.get("key"),
                title=data.get("title") or source.stem,
                source="attachment-import",
            )
        return result_payload(
            action="add_file",
            ok=False,
            status="error",
            code="PDF_IMPORT_FAILED",
            path=str(source),
            error=(data or {}).get("error") if isinstance(data, dict) else transport.get("error"),
        )

    return result_payload(
        action="add_file",
        ok=False,
        status="error",
        code="UNSUPPORTED_FILE",
        path=str(source),
        error=f"Unsupported file type: {suffix}",
    )
