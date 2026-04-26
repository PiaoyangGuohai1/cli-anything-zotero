from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from cli_anything.zotero.core import docx as docx_tools
from cli_anything.zotero.core import rendering
from cli_anything.zotero.core.discovery import RuntimeContext


DEFAULT_STYLE = "apa"
DEFAULT_LOCALE = "en-US"
DEFAULT_BIBLIOGRAPHY = "auto"
_BIBLIOGRAPHY_MODES = {"auto", "none"}
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def render_static_citations(
    runtime: RuntimeContext,
    path: str | Path,
    output: str | Path,
    *,
    style: str = DEFAULT_STYLE,
    locale: str = DEFAULT_LOCALE,
    bibliography: str = DEFAULT_BIBLIOGRAPHY,
    session: dict[str, Any] | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Replace Zotero placeholders in a DOCX with static citation text."""
    _require_bibliography_mode(bibliography)
    source_path = docx_tools._validated_docx_path(path)
    output_path = Path(output).expanduser()
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {output_path}")

    validation = docx_tools.validate_placeholders(runtime, source_path, session=session)
    if not validation["ok"]:
        raise ValueError("DOCX placeholders are not ready for static citation rendering.")
    if not validation["placeholder_count"]:
        raise ValueError("No Zotero placeholders were found. Use {{zotero:ITEMKEY}} or {{zotero:KEY1,KEY2}}.")

    item_by_key = {str(item["key"]): item for item in validation["items"]}
    rendered_items = _render_items(runtime, item_by_key, style=style, locale=locale, session=session)
    root = ET.fromstring(docx_tools._read_document_xml(source_path))
    rendered_placeholders = _replace_placeholders_with_static_text(root, rendered_items)
    bibliography_entries: list[dict[str, str]] = []
    if bibliography == "auto":
        bibliography_entries = _bibliography_entries(rendered_items, rendered_placeholders)
        _insert_static_bibliography(root, bibliography_entries)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source_path) as source_zip:
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as output_zip:
            for info in source_zip.infolist():
                if info.filename == "word/document.xml":
                    continue
                output_zip.writestr(info, source_zip.read(info.filename))
            output_zip.writestr("word/document.xml", ET.tostring(root, encoding="utf-8", xml_declaration=True))

    inspection = docx_tools.inspect_citations(output_path, sample_limit=10000)
    return {
        "ok": True,
        "mode": "static",
        "input": str(source_path),
        "output": str(output_path),
        "style": style,
        "locale": locale,
        "bibliography": bibliography,
        "placeholder_count": len(rendered_placeholders),
        "citation_count": sum(len(entry["keys"]) for entry in rendered_placeholders),
        "bibliography_count": len(bibliography_entries),
        "rendered_placeholders": rendered_placeholders,
        "items": validation["items"],
        "inspection": {
            "field_count": inspection["field_count"],
            "field_counts": inspection["field_counts"],
            "systems": inspection["systems"],
            "static_citation_count": inspection["static_citation_count"],
        },
        "notes": [
            "Static citations were rendered as ordinary DOCX text.",
            "The output cannot be refreshed by the Zotero word processor plugin; rerender from the placeholder DOCX if citation data changes.",
        ],
    }


def _render_items(
    runtime: RuntimeContext,
    item_by_key: dict[str, dict[str, Any]],
    *,
    style: str,
    locale: str,
    session: dict[str, Any] | None,
) -> dict[str, dict[str, str]]:
    rendered: dict[str, dict[str, str]] = {}
    for key in sorted(item_by_key):
        citation = rendering.citation_item(runtime, key, style=style, locale=locale, session=session).get("citation") or ""
        bibliography = rendering.bibliography_item(runtime, key, style=style, locale=locale, session=session).get("bibliography") or ""
        rendered[key] = {
            "citation": _plain_text(str(citation)),
            "bibliography": _plain_text(str(bibliography)),
        }
    return rendered


def _replace_placeholders_with_static_text(root: ET.Element, rendered_items: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    placeholders: list[dict[str, Any]] = []
    for text_node in list(root.findall(".//w:t", docx_tools._WORD_NS)):
        text = "".join(text_node.itertext())
        if not docx_tools._PLACEHOLDER_RE.search(text):
            continue

        parts: list[str] = []
        cursor = 0
        for match in docx_tools._PLACEHOLDER_RE.finditer(text):
            parts.append(text[cursor : match.start()])
            keys, invalid_parts = docx_tools._parse_placeholder_keys(match.group(1))
            if invalid_parts or not keys:
                raise ValueError(f"Invalid Zotero placeholder: {match.group(0)}")
            citation = _combined_citation([rendered_items[key]["citation"] for key in keys])
            placeholders.append({"raw": match.group(0), "keys": keys, "citation": citation})
            parts.append(citation)
            cursor = match.end()
        parts.append(text[cursor:])
        rendered_text = "".join(parts)
        if rendered_text[:1].isspace() or rendered_text[-1:].isspace():
            text_node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        text_node.text = rendered_text
    return placeholders


def _combined_citation(citations: list[str]) -> str:
    cleaned = [citation.strip() for citation in citations if citation.strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    if all(citation.startswith("(") and citation.endswith(")") for citation in cleaned):
        return "(" + "; ".join(citation[1:-1].strip() for citation in cleaned) + ")"
    return "; ".join(cleaned)


def _bibliography_entries(
    rendered_items: dict[str, dict[str, str]],
    rendered_placeholders: list[dict[str, Any]],
) -> list[dict[str, str]]:
    keys: list[str] = []
    seen: set[str] = set()
    for placeholder in rendered_placeholders:
        for key in placeholder["keys"]:
            if key not in seen:
                seen.add(key)
                keys.append(key)
    return [{"key": key, "bibliography": rendered_items[key]["bibliography"]} for key in keys if rendered_items[key]["bibliography"]]


def _insert_static_bibliography(root: ET.Element, entries: list[dict[str, str]]) -> None:
    if not entries:
        return
    body = root.find("w:body", docx_tools._WORD_NS)
    if body is None:
        raise ValueError("DOCX document.xml has no w:body element.")
    insert_at = _bibliography_insert_index(list(body))
    if insert_at is None:
        insert_at = _append_references_heading(body)
    for offset, entry in enumerate(entries):
        body.insert(insert_at + offset, _paragraph_with_text(entry["bibliography"]))


def _bibliography_insert_index(children: list[ET.Element]) -> int | None:
    headings = {"references", "bibliography", "works cited", "参考文献", "參考文獻"}
    for index, child in enumerate(children):
        if child.tag != docx_tools._w("p"):
            continue
        text = "".join(child.itertext()).strip().lower()
        if text in headings:
            return index + 1
    return None


def _append_references_heading(body: ET.Element) -> int:
    sect_pr = body.find("w:sectPr", docx_tools._WORD_NS)
    heading = _paragraph_with_text("References")
    if sect_pr is not None:
        index = list(body).index(sect_pr)
        body.insert(index, heading)
        return index + 1
    body.append(heading)
    return len(list(body))


def _paragraph_with_text(text: str) -> ET.Element:
    paragraph = ET.Element(docx_tools._w("p"))
    run = ET.SubElement(paragraph, docx_tools._w("r"))
    text_node = ET.SubElement(run, docx_tools._w("t"))
    if text[:1].isspace() or text[-1:].isspace():
        text_node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    text_node.text = text
    return paragraph


def _plain_text(value: str) -> str:
    text = _HTML_TAG_RE.sub("", value)
    return html.unescape(text).strip()


def _require_bibliography_mode(bibliography: str) -> None:
    if bibliography not in _BIBLIOGRAPHY_MODES:
        raise ValueError("Bibliography mode must be one of: auto, none.")
