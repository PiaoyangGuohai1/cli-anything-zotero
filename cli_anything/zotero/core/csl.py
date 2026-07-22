"""CSL-JSON → Zotero connector item conversion."""

from __future__ import annotations

from typing import Any

_CSL_TYPE_MAP = {
    "article-journal": "journalArticle",
    "article-magazine": "magazineArticle",
    "article-newspaper": "newspaperArticle",
    "article": "journalArticle",
    "book": "book",
    "chapter": "bookSection",
    "paper-conference": "conferencePaper",
    "thesis": "thesis",
    "report": "report",
    "webpage": "webpage",
    "post-weblog": "blogPost",
    "post": "forumPost",
    "manuscript": "manuscript",
    "dataset": "document",
    "software": "computerProgram",
    "patent": "patent",
    "bill": "bill",
    "map": "map",
    "motion_picture": "film",
    "song": "audioRecording",
    "speech": "presentation",
    "entry-encyclopedia": "encyclopediaArticle",
    "entry-dictionary": "dictionaryEntry",
}


def looks_like_csl_item(obj: dict[str, Any]) -> bool:
    if not isinstance(obj, dict):
        return False
    if obj.get("itemType") and ("creators" in obj or "title" in obj):
        # already connector-ish
        return False
    return bool(obj.get("type") or obj.get("DOI") or obj.get("title")) and (
        "author" in obj or "issued" in obj or "container-title" in obj or "id" in obj or "DOI" in obj
    )


def _issued_to_date(issued: Any) -> str:
    if not isinstance(issued, dict):
        return ""
    parts = issued.get("date-parts") or issued.get("raw")
    if isinstance(parts, str):
        return parts
    if isinstance(parts, list) and parts:
        first = parts[0]
        if isinstance(first, list):
            return "-".join(str(x) for x in first if x is not None)
    return ""


def _authors_to_creators(authors: Any) -> list[dict[str, Any]]:
    creators: list[dict[str, Any]] = []
    if not isinstance(authors, list):
        return creators
    for author in authors:
        if not isinstance(author, dict):
            continue
        if author.get("literal"):
            creators.append({"creatorType": "author", "name": author["literal"]})
            continue
        creators.append(
            {
                "creatorType": "author",
                "firstName": author.get("given") or "",
                "lastName": author.get("family") or "",
            }
        )
    return creators


def csl_item_to_connector(item: dict[str, Any], *, index: int = 1) -> dict[str, Any]:
    item_type = _CSL_TYPE_MAP.get(str(item.get("type") or "").lower(), "journalArticle")
    out: dict[str, Any] = {
        "itemType": item_type,
        "title": item.get("title") or item.get("container-title") or "Untitled",
        "id": f"cli-anything-csl-{index}",
    }
    if item.get("DOI") or item.get("doi"):
        out["DOI"] = item.get("DOI") or item.get("doi")
    if item.get("URL") or item.get("url"):
        out["url"] = item.get("URL") or item.get("url")
    if item.get("abstract"):
        out["abstractNote"] = item["abstract"]
    if item.get("container-title"):
        out["publicationTitle"] = item["container-title"]
    if item.get("volume"):
        out["volume"] = str(item["volume"])
    if item.get("issue"):
        out["issue"] = str(item["issue"])
    if item.get("page"):
        out["pages"] = str(item["page"])
    if item.get("publisher"):
        out["publisher"] = item["publisher"]
    if item.get("language"):
        out["language"] = item["language"]
    if item.get("ISSN"):
        out["ISSN"] = item["ISSN"] if isinstance(item["ISSN"], str) else ",".join(item["ISSN"])
    if item.get("ISBN"):
        out["ISBN"] = item["ISBN"] if isinstance(item["ISBN"], str) else ",".join(item["ISBN"])
    date = _issued_to_date(item.get("issued"))
    if date:
        out["date"] = date
    creators = _authors_to_creators(item.get("author") or item.get("editor"))
    if creators:
        out["creators"] = creators
    tags = []
    for t in item.get("keyword") or item.get("categories") or []:
        if isinstance(t, str):
            tags.append({"tag": t})
    if tags:
        out["tags"] = tags
    return out


def normalize_import_json_payload(payload: Any) -> tuple[list[dict[str, Any]], str]:
    """Return (connector_items, format_label)."""
    # Crossref work object
    if isinstance(payload, dict) and payload.get("message") and isinstance(payload["message"], dict):
        msg = payload["message"]
        if msg.get("DOI") or msg.get("title"):
            # lightweight crossref → csl-ish
            title = msg.get("title")
            if isinstance(title, list):
                title = title[0] if title else ""
            csl = {
                "type": "article-journal",
                "title": title,
                "DOI": msg.get("DOI"),
                "URL": msg.get("URL"),
                "container-title": (msg.get("container-title") or [None])[0],
                "volume": msg.get("volume"),
                "issue": msg.get("issue"),
                "page": msg.get("page"),
                "author": [
                    {"family": a.get("family"), "given": a.get("given")}
                    for a in (msg.get("author") or [])
                    if isinstance(a, dict)
                ],
                "issued": msg.get("issued") or {"date-parts": [msg.get("published-print", {}).get("date-parts", [[]])[0] if msg.get("published-print") else []]},
            }
            return [csl_item_to_connector(csl, index=1)], "crossref"

    items_raw: list[Any]
    if isinstance(payload, list):
        items_raw = payload
    elif isinstance(payload, dict):
        if isinstance(payload.get("items"), list):
            items_raw = payload["items"]
        elif looks_like_csl_item(payload) or payload.get("itemType"):
            items_raw = [payload]
        else:
            raise RuntimeError("JSON import expects an array, {items:[...]}, CSL object, or Crossref work")
    else:
        raise RuntimeError("JSON import expects an array of objects")

    if not items_raw:
        return [], "empty"

    # Detect format from first object
    first = items_raw[0] if isinstance(items_raw[0], dict) else {}
    if first.get("itemType"):
        fmt = "connector"
        out = []
        for i, item in enumerate(items_raw, start=1):
            if not isinstance(item, dict):
                raise RuntimeError(f"JSON import item {i} is not an object")
            copied = dict(item)
            copied.setdefault("id", f"cli-anything-zotero-{i}")
            out.append(copied)
        return out, fmt

    if looks_like_csl_item(first) or first.get("type"):
        out = []
        for i, item in enumerate(items_raw, start=1):
            if not isinstance(item, dict):
                raise RuntimeError(f"JSON import item {i} is not an object")
            out.append(csl_item_to_connector(item, index=i))
        return out, "csl-json"

    # Fallback: treat as connector-like
    out = []
    for i, item in enumerate(items_raw, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(f"JSON import item {i} is not an object")
        copied = dict(item)
        copied.setdefault("itemType", "journalArticle")
        copied.setdefault("id", f"cli-anything-zotero-{i}")
        out.append(copied)
    return out, "connector-fallback"
