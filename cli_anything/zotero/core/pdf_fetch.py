"""Open-access PDF cascade for agent workflows.

Order (default):
  1) Zotero built-in Find Available PDF (JS bridge)
  2) Unpaywall
  3) EuropePMC / PMC
  4) bioRxiv / medRxiv (10.1101 / 10.64898)
  5) arXiv (10.48550/arXiv.* or bare arXiv ids)
"""

from __future__ import annotations

import json
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from cli_anything.zotero.core.imports import normalize_doi
from cli_anything.zotero.core.results import result_payload
from cli_anything.zotero.utils import zotero_sqlite

_ARXIV_RE = re.compile(r"(?:arxiv(?:\.org)?/(?:abs|pdf)/|arxiv:)?(\d{4}\.\d{4,5})(v\d+)?", re.I)
_DEFAULT_SOURCES = ("zotero", "unpaywall", "epmc", "biorxiv", "arxiv")
_USER_AGENT = "cli-anything-zotero/1.1 (mailto:cli-anything@local; research agent)"


def parse_sources(value: str | None) -> list[str]:
    if not value:
        return list(_DEFAULT_SOURCES)
    parts = [p.strip().lower() for p in value.split(",") if p.strip()]
    unknown = [p for p in parts if p not in _DEFAULT_SOURCES and p != "all"]
    if "all" in parts:
        return list(_DEFAULT_SOURCES)
    if unknown:
        raise ValueError(f"Unknown PDF sources: {unknown}. Allowed: {list(_DEFAULT_SOURCES)}")
    return parts or list(_DEFAULT_SOURCES)


def extract_arxiv_id(text: str | None) -> str | None:
    if not text:
        return None
    m = _ARXIV_RE.search(text.strip())
    return m.group(1) if m else None


def _http_get_bytes(url: str, *, timeout: int = 45, accept: str = "*/*") -> tuple[bytes, str, str]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": _USER_AGENT, "Accept": accept},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        final = resp.geturl()
        ctype = resp.headers.get("Content-Type", "")
        return data, final, ctype


def _http_get_json(url: str, *, timeout: int = 30) -> Any:
    raw, _, _ = _http_get_bytes(url, timeout=timeout, accept="application/json")
    return json.loads(raw.decode("utf-8", errors="replace"))


def _is_pdf(data: bytes) -> bool:
    return bool(data) and data[:5].startswith(b"%PDF") and len(data) >= 8000


def _write_temp_pdf(data: bytes, prefix: str = "zotero-pdf-") -> Path:
    tmp = tempfile.NamedTemporaryFile(prefix=prefix, suffix=".pdf", delete=False)
    tmp.write(data)
    tmp.flush()
    tmp.close()
    return Path(tmp.name)


def download_from_url(url: str, *, timeout: int = 60) -> Path | None:
    try:
        data, final, ctype = _http_get_bytes(url, timeout=timeout, accept="application/pdf,*/*")
    except Exception:
        return None
    if not _is_pdf(data):
        return None
    return _write_temp_pdf(data)


def unpaywall_pdf_urls(doi: str, *, email: str = "cli-anything@local") -> list[str]:
    doi = normalize_doi(doi)
    url = f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?email={urllib.parse.quote(email)}"
    try:
        payload = _http_get_json(url, timeout=25)
    except Exception:
        return []
    urls: list[str] = []
    best = payload.get("best_oa_location") or {}
    for key in ("url_for_pdf", "url"):
        if best.get(key):
            urls.append(best[key])
    for loc in payload.get("oa_locations") or []:
        if loc.get("url_for_pdf"):
            urls.append(loc["url_for_pdf"])
    # dedupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def epmc_pdf_urls(doi: str) -> list[str]:
    doi = normalize_doi(doi)
    q = urllib.parse.quote(f'DOI:"{doi}"')
    url = (
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        f"?query={q}&format=json&resultType=core&pageSize=1"
    )
    try:
        payload = _http_get_json(url, timeout=25)
    except Exception:
        return []
    results = ((payload.get("resultList") or {}).get("result")) or []
    if not results:
        return []
    r0 = results[0]
    urls: list[str] = []
    pmcid = r0.get("pmcid")
    if pmcid:
        urls.append(f"https://europepmc.org/backend/ptpmcrender.fcgi?accid={pmcid}&blobtype=pdf")
        urls.append(f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf")
    for entry in ((r0.get("fullTextUrlList") or {}).get("fullTextUrl") or []):
        u = entry.get("url")
        if u and ("pdf" in u.lower() or entry.get("documentStyle") == "pdf"):
            urls.append(u)
    return urls


def preprint_pdf_urls(doi: str) -> list[str]:
    doi = normalize_doi(doi)
    d = doi.lower()
    urls: list[str] = []
    if d.startswith("10.1101/") or d.startswith("10.64898/"):
        urls.extend(
            [
                f"https://www.biorxiv.org/content/{doi}v1.full.pdf",
                f"https://www.biorxiv.org/content/{doi}v2.full.pdf",
                f"https://www.biorxiv.org/content/{doi}.full.pdf",
                f"https://www.medrxiv.org/content/{doi}v1.full.pdf",
            ]
        )
    return urls


def arxiv_pdf_urls(doi_or_id: str) -> list[str]:
    text = doi_or_id or ""
    arxiv_id = extract_arxiv_id(text)
    if not arxiv_id and "arxiv" in text.lower():
        # 10.48550/arXiv.2602.02093
        m = re.search(r"(\d{4}\.\d{4,5})", text)
        arxiv_id = m.group(1) if m else None
    if not arxiv_id:
        return []
    return [f"https://arxiv.org/pdf/{arxiv_id}.pdf", f"https://export.arxiv.org/pdf/{arxiv_id}.pdf"]


def cascade_download_pdf(
    *,
    doi: str | None,
    sources: list[str],
    timeout: int = 45,
) -> tuple[Path | None, list[dict[str, Any]]]:
    """Try OA sources in order. Returns (path, attempts)."""
    attempts: list[dict[str, Any]] = []
    doi_n = normalize_doi(doi or "") if doi else ""

    def try_urls(source: str, urls: list[str]) -> Path | None:
        for url in urls:
            path = download_from_url(url, timeout=timeout)
            attempts.append(
                {
                    "source": source,
                    "url": url,
                    "ok": bool(path),
                    "path": str(path) if path else None,
                }
            )
            if path:
                return path
        if not urls:
            attempts.append({"source": source, "ok": False, "error": "no candidate urls"})
        return None

    for source in sources:
        if source == "zotero":
            # handled by caller via bridge
            continue
        if source == "unpaywall":
            if not doi_n:
                attempts.append({"source": source, "ok": False, "error": "no DOI"})
                continue
            path = try_urls(source, unpaywall_pdf_urls(doi_n))
            if path:
                return path, attempts
        elif source == "epmc":
            if not doi_n:
                attempts.append({"source": source, "ok": False, "error": "no DOI"})
                continue
            path = try_urls(source, epmc_pdf_urls(doi_n))
            if path:
                return path, attempts
        elif source == "biorxiv":
            if not doi_n:
                attempts.append({"source": source, "ok": False, "error": "no DOI"})
                continue
            path = try_urls(source, preprint_pdf_urls(doi_n))
            if path:
                return path, attempts
        elif source == "arxiv":
            path = try_urls(source, arxiv_pdf_urls(doi_n or (doi or "")))
            if path:
                return path, attempts
    return None, attempts


def item_has_pdf(runtime: Any, item_key: str, *, library_id: int = 1) -> bool:
    item = zotero_sqlite.resolve_item(runtime.environment.sqlite_path, item_key, library_id=library_id)
    if not item:
        return False
    if item.get("hasPdf"):
        return True
    # resolve_item with include_related may not set hasPdf on older paths
    return bool(item.get("hasPdf"))


def fetch_pdf_for_item(
    runtime: Any,
    bridge: Any,
    item_key: str,
    *,
    sources: list[str] | None = None,
    library_id: int = 1,
    zotero_timeout: int = 45,
    download_timeout: int = 45,
    force: bool = False,
) -> dict[str, Any]:
    """Find/attach a PDF for one item using Zotero + OA cascade."""
    sources = sources or list(_DEFAULT_SOURCES)
    sqlite_path = runtime.environment.sqlite_path
    item = zotero_sqlite.resolve_item(sqlite_path, item_key, library_id=library_id)
    if not item:
        return result_payload(
            action="item_fetch_pdf",
            ok=False,
            status="error",
            code="ITEM_NOT_FOUND",
            key=item_key,
            error=f"Item not found: {item_key}",
        )

    # Refresh hasPdf via fields if needed
    has_pdf = bool(item.get("hasPdf"))
    doi = (item.get("DOI") or (item.get("fields") or {}).get("DOI") or "").strip()
    if not doi and item.get("fields"):
        doi = (item["fields"].get("DOI") or "").strip()
    if not doi:
        # cheap field fetch
        full = zotero_sqlite.resolve_item(sqlite_path, item_key, library_id=library_id)
        # resolve without include_related still has DOI from base select after our 1.2 change
        doi = (full or {}).get("DOI") or ""
        has_pdf = bool((full or {}).get("hasPdf"))

    if has_pdf and not force:
        return result_payload(
            action="item_fetch_pdf",
            ok=True,
            status="already_has_pdf",
            code="ALREADY_HAS_PDF",
            key=item_key,
            title=item.get("title"),
            DOI=doi,
            source="existing",
        )

    attempts: list[dict[str, Any]] = []

    # 1) Zotero find available PDF
    if "zotero" in sources:
        transport = bridge.find_pdf(item_key, library_id=library_id, timeout=zotero_timeout)
        data = transport.get("data")
        text = data if isinstance(data, str) else str(data)
        ok_z = bool(transport.get("ok") and isinstance(text, str) and text.startswith("FOUND:"))
        attempts.append(
            {
                "source": "zotero",
                "ok": ok_z,
                "message": text if transport.get("ok") else transport.get("error"),
            }
        )
        if ok_z:
            return result_payload(
                action="item_fetch_pdf",
                ok=True,
                status="success",
                code="FOUND",
                key=item_key,
                title=item.get("title"),
                DOI=doi,
                source="zotero",
                attachment_key=text.split("FOUND:", 1)[-1].strip(),
                attempts=attempts,
            )

    # 2) OA cascade download + attach
    oa_sources = [s for s in sources if s != "zotero"]
    path, dl_attempts = cascade_download_pdf(doi=doi or item_key, sources=oa_sources, timeout=download_timeout)
    attempts.extend(dl_attempts)
    if not path:
        return result_payload(
            action="item_fetch_pdf",
            ok=False,
            status="not_found",
            code="PDF_NOT_FOUND",
            key=item_key,
            title=item.get("title"),
            DOI=doi,
            error="No PDF found via configured sources",
            attempts=attempts,
        )

    attach = bridge.attach_pdf(item_key, str(path), library_id=library_id)
    attach_ok = bool(attach.get("ok"))
    attach_data = attach.get("data")
    # cleanup temp file best-effort
    try:
        path.unlink(missing_ok=True)  # type: ignore[arg-type]
    except Exception:
        pass

    if not attach_ok:
        return result_payload(
            action="item_fetch_pdf",
            ok=False,
            status="error",
            code="ATTACH_FAILED",
            key=item_key,
            title=item.get("title"),
            DOI=doi,
            error=attach.get("error") or str(attach_data),
            attempts=attempts,
        )

    return result_payload(
        action="item_fetch_pdf",
        ok=True,
        status="success",
        code="ATTACHED",
        key=item_key,
        title=item.get("title"),
        DOI=doi,
        source="oa-cascade",
        attach_result=attach_data,
        attempts=attempts,
    )


def fetch_pdfs_for_collection(
    runtime: Any,
    bridge: Any,
    collection_key: str,
    *,
    sources: list[str] | None = None,
    library_id: int = 1,
    limit: int | None = None,
    zotero_timeout: int = 45,
    download_timeout: int = 45,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    listed = bridge.list_items_missing_pdf(collection_key, library_id=library_id)
    if not listed.get("ok"):
        return result_payload(
            action="collection_fetch_pdfs",
            ok=False,
            status="error",
            code="LIST_FAILED",
            error=listed.get("error") or "failed to list items missing PDFs",
            collection=collection_key,
        )
    payload = listed.get("data") or {}
    if isinstance(payload, dict) and payload.get("ok") is False:
        return result_payload(
            action="collection_fetch_pdfs",
            ok=False,
            status="error",
            code="LIST_FAILED",
            error=payload.get("error") or "collection list failed",
            collection=collection_key,
        )
    missing = list((payload or {}).get("missing") or [])
    if limit is not None:
        missing = missing[: max(0, int(limit))]

    details: list[dict[str, Any]] = []
    found = 0
    for index, entry in enumerate(missing, start=1):
        key = entry.get("key")
        one = fetch_pdf_for_item(
            runtime,
            bridge,
            key,
            sources=sources,
            library_id=library_id,
            zotero_timeout=zotero_timeout,
            download_timeout=download_timeout,
        )
        if one.get("ok") and one.get("status") in {"success", "already_has_pdf"}:
            if one.get("code") in {"FOUND", "ATTACHED", "ALREADY_HAS_PDF"}:
                found += 1
        row = {
            "index": index,
            "total": len(missing),
            "key": key,
            "title": entry.get("title"),
            "DOI": entry.get("DOI"),
            "ok": one.get("ok"),
            "status": one.get("status"),
            "code": one.get("code"),
            "source": one.get("source"),
            "error": one.get("error"),
        }
        details.append(row)
        if progress_callback:
            progress_callback(row)

    if not missing:
        status = "success"
        ok = True
    elif found == 0:
        status = "not_found"
        ok = False
    elif found < len(missing):
        status = "partial_success"
        ok = True
    else:
        status = "success"
        ok = True

    return result_payload(
        action="collection_fetch_pdfs",
        ok=ok,
        status=status,
        code="DONE",
        collection=collection_key,
        checked=len(missing),
        found=found,
        missing_total=(payload or {}).get("missing_count"),
        details=details,
        sources=sources or list(_DEFAULT_SOURCES),
    )
