from __future__ import annotations

import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


_WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
_AUTHOR = r"[A-Z][A-Za-z'’.-]+(?:\s+(?:&|and)\s+[A-Z][A-Za-z'’.-]+|\s+et\s+al\.)?"
_AUTHOR_YEAR_RE = re.compile(rf"\({_AUTHOR},\s+\d{{4}}[a-z]?(?:;\s*{_AUTHOR},\s+\d{{4}}[a-z]?)*\)")
_NUMERIC_RE = re.compile(r"\[(?:\d+(?:\s*[-,]\s*\d+)*)\]")


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
