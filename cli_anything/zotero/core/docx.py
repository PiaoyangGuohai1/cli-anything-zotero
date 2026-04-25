from __future__ import annotations

import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from cli_anything.zotero.core import catalog
from cli_anything.zotero.core.discovery import RuntimeContext


_WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
_AUTHOR = r"[A-Z][A-Za-z'’.-]+(?:\s+(?:&|and)\s+[A-Z][A-Za-z'’.-]+|\s+et\s+al\.)?"
_AUTHOR_YEAR_RE = re.compile(rf"\({_AUTHOR},\s+\d{{4}}[a-z]?(?:;\s*{_AUTHOR},\s+\d{{4}}[a-z]?)*\)")
_NUMERIC_RE = re.compile(r"\[(?:\d+(?:\s*[-,]\s*\d+)*)\]")
_PLACEHOLDER_RE = re.compile(r"\{\{\s*zotero\s*:\s*([^}]*)\s*\}\}", re.IGNORECASE)
_ZOTERO_KEY_RE = re.compile(r"^[A-Z0-9]{8}$")


def inspect_citations(path: str | Path, *, sample_limit: int = 10) -> dict[str, Any]:
    """Inspect a DOCX file for citation field systems and static citation text."""
    docx_path = Path(path).expanduser()
    if not docx_path.exists():
        raise FileNotFoundError(f"DOCX file not found: {docx_path}")
    if docx_path.suffix.lower() != ".docx":
        raise ValueError(f"Expected a .docx file: {docx_path}")

    document_xml = _read_document_xml(docx_path)
    root = ET.fromstring(document_xml)
    instructions = _field_instructions(root)
    fields = [_field_report(instruction) for instruction in instructions]
    field_counts = Counter(field["system"] for field in fields)
    visible_text = _visible_text(root)
    static_matches = _static_citation_matches(visible_text)
    systems = sorted(system for system, count in field_counts.items() if count)
    if static_matches:
        systems.append("static-text")

    return {
        "path": str(docx_path),
        "has_fields": bool(fields),
        "systems": systems,
        "field_counts": dict(sorted(field_counts.items())),
        "field_count": len(fields),
        "fields": fields[:sample_limit],
        "static_citation_count": len(static_matches),
        "static_citation_samples": static_matches[:sample_limit],
        "notes": _notes(field_counts, bool(static_matches)),
    }


def inspect_placeholders(path: str | Path, *, sample_limit: int = 10) -> dict[str, Any]:
    """Inspect a DOCX file for Zotero-bound AI citation placeholders."""
    docx_path = _validated_docx_path(path)
    root = ET.fromstring(_read_document_xml(docx_path))
    visible_text = _visible_text(root)
    placeholders: list[dict[str, Any]] = []
    invalid_placeholders: list[dict[str, Any]] = []
    key_occurrences: list[str] = []

    for match in _PLACEHOLDER_RE.finditer(visible_text):
        raw = match.group(0)
        keys, invalid_parts = _parse_placeholder_keys(match.group(1))
        entry = {
            "raw": raw,
            "keys": keys,
            "context": _context(visible_text, match.start(), match.end()),
        }
        placeholders.append(entry)
        key_occurrences.extend(keys)
        if invalid_parts or not keys:
            invalid_placeholders.append(
                {
                    **entry,
                    "invalid_parts": invalid_parts or [match.group(1).strip()],
                    "reason": "Expected comma-separated 8-character Zotero item keys.",
                }
            )

    counts = Counter(key_occurrences)
    unique_keys = sorted(counts)
    notes = _placeholder_notes(placeholders, invalid_placeholders)
    return {
        "path": str(docx_path),
        "placeholder_count": len(placeholders),
        "citation_count": len(key_occurrences),
        "unique_keys": unique_keys,
        "duplicate_keys": sorted(key for key, count in counts.items() if count > 1),
        "placeholders": placeholders[:sample_limit],
        "invalid_placeholders": invalid_placeholders[:sample_limit],
        "notes": notes,
    }


def validate_placeholders(
    runtime: RuntimeContext,
    path: str | Path,
    *,
    sample_limit: int = 10,
    session: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate DOCX Zotero placeholders against the local Zotero database."""
    report = inspect_placeholders(path, sample_limit=sample_limit)
    items: list[dict[str, Any]] = []
    missing_keys: list[str] = []
    errors: dict[str, str] = {}

    for key in report["unique_keys"]:
        try:
            item = catalog.get_item(runtime, key, session=session)
        except Exception as exc:
            missing_keys.append(key)
            errors[key] = str(exc)
            continue
        items.append(_item_summary(item))

    report.update(
        {
            "ok": not report["invalid_placeholders"] and not missing_keys,
            "valid_count": len(items),
            "missing_count": len(missing_keys),
            "items": items,
            "missing_keys": missing_keys,
            "errors": errors,
        }
    )
    if missing_keys:
        report["notes"].append("Some Zotero placeholder keys do not resolve to local Zotero items.")
    if report["ok"]:
        report["notes"].append("All Zotero placeholders resolve to real local Zotero items.")
    return report


def _validated_docx_path(path: str | Path) -> Path:
    docx_path = Path(path).expanduser()
    if not docx_path.exists():
        raise FileNotFoundError(f"DOCX file not found: {docx_path}")
    if docx_path.suffix.lower() != ".docx":
        raise ValueError(f"Expected a .docx file: {docx_path}")
    return docx_path


def _read_document_xml(path: Path) -> bytes:
    try:
        with zipfile.ZipFile(path) as zf:
            return zf.read("word/document.xml")
    except KeyError as exc:
        raise ValueError(f"DOCX is missing word/document.xml: {path}") from exc
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Invalid DOCX file: {path}") from exc


def _field_instructions(root: ET.Element) -> list[str]:
    instructions: list[str] = []
    for elem in root.findall(".//w:instrText", _WORD_NS):
        text = "".join(elem.itertext()).strip()
        if text:
            instructions.append(_normalize_space(text))

    instr_attr = f"{{{_WORD_NS['w']}}}instr"
    for elem in root.findall(".//w:fldSimple", _WORD_NS):
        text = elem.attrib.get(instr_attr, "").strip()
        if text:
            instructions.append(_normalize_space(text))
    return instructions


def _field_report(instruction: str) -> dict[str, str]:
    return {
        "system": _classify_instruction(instruction),
        "instruction": _truncate(instruction, 240),
    }


def _classify_instruction(instruction: str) -> str:
    upper = instruction.upper()
    if "ADDIN ZOTERO" in upper or "ZOTERO_ITEM" in upper or "ZOTERO_BIBL" in upper:
        return "zotero"
    if "ADDIN EN.CITE" in upper or "ADDIN EN.REFLIST" in upper:
        return "endnote"
    if "MENDELEY" in upper:
        return "mendeley"
    if "CSL_CITATION" in upper or "CSL_BIBLIOGRAPHY" in upper:
        return "csl"
    if "ADDIN" in upper:
        return "unknown-addin"
    return "word-field"


def _visible_text(root: ET.Element) -> str:
    text_nodes = ["".join(elem.itertext()) for elem in root.findall(".//w:t", _WORD_NS)]
    return _normalize_space(" ".join(text_nodes))


def _static_citation_matches(text: str) -> list[str]:
    matches = list(_AUTHOR_YEAR_RE.findall(text)) + list(_NUMERIC_RE.findall(text))
    deduped: list[str] = []
    seen: set[str] = set()
    for match in matches:
        if match in seen:
            continue
        seen.add(match)
        deduped.append(match)
    return deduped


def _parse_placeholder_keys(raw_keys: str) -> tuple[list[str], list[str]]:
    keys: list[str] = []
    invalid_parts: list[str] = []
    for part in raw_keys.split(","):
        candidate = part.strip().upper()
        if not candidate:
            continue
        if _ZOTERO_KEY_RE.fullmatch(candidate):
            keys.append(candidate)
        else:
            invalid_parts.append(part.strip())
    return keys, invalid_parts


def _context(text: str, start: int, end: int, radius: int = 80) -> str:
    prefix_start = max(0, start - radius)
    suffix_end = min(len(text), end + radius)
    context = text[prefix_start:suffix_end]
    if prefix_start > 0:
        context = "..." + context
    if suffix_end < len(text):
        context += "..."
    return _normalize_space(context)


def _placeholder_notes(placeholders: list[dict[str, Any]], invalid_placeholders: list[dict[str, Any]]) -> list[str]:
    notes: list[str] = []
    if placeholders:
        notes.append("Zotero placeholders are present; validate them before converting or finalizing the DOCX.")
    else:
        notes.append("No Zotero placeholders were detected. AI-authored DOCX citation insertion should use {{zotero:ITEMKEY}} placeholders.")
    if invalid_placeholders:
        notes.append("Some Zotero placeholders are malformed and should be fixed before document conversion.")
    return notes


def _item_summary(item: dict[str, Any]) -> dict[str, Any]:
    fields = item.get("fields") or {}
    date_text = str(fields.get("date") or item.get("date") or "")
    year_match = re.search(r"\d{4}", date_text)
    return {
        "itemID": item.get("itemID"),
        "key": item.get("key"),
        "libraryID": item.get("libraryID"),
        "typeName": item.get("typeName"),
        "title": item.get("title") or fields.get("title") or "",
        "year": year_match.group(0) if year_match else None,
        "doi": fields.get("DOI") or fields.get("doi"),
        "pmid": fields.get("PMID") or fields.get("pmid"),
    }


def _notes(field_counts: Counter[str], has_static_text: bool) -> list[str]:
    notes: list[str] = []
    if field_counts.get("endnote"):
        notes.append("EndNote fields are present; Zotero cannot refresh these as Zotero citations.")
    if field_counts.get("zotero"):
        notes.append("Zotero citation fields are present and should be managed with the Zotero word processor plugin.")
    if field_counts.get("csl") or field_counts.get("mendeley"):
        notes.append("CSL/Mendeley-like fields are present; verify which word processor plugin created them before editing.")
    if has_static_text:
        notes.append("Static citation-looking text is present; these citations may not be refreshable fields.")
    if not field_counts and not has_static_text:
        notes.append("No citation fields or common static citation patterns were detected.")
    return notes


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _truncate(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    return text[: max_length - 1] + "…"
