from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import uuid
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from cli_anything.zotero.core import catalog
from cli_anything.zotero.core.discovery import RuntimeContext


_WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
_REL_NS = {"r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_AUTHOR = r"[A-Z][A-Za-z'’.-]+(?:\s+(?:&|and)\s+[A-Z][A-Za-z'’.-]+|\s+et\s+al\.)?"
_AUTHOR_YEAR_RE = re.compile(rf"\({_AUTHOR},\s+\d{{4}}[a-z]?(?:;\s*{_AUTHOR},\s+\d{{4}}[a-z]?)*\)")
_NUMERIC_RE = re.compile(r"\[(?:\d+(?:\s*[-,]\s*\d+)*)\]")
_PLACEHOLDER_RE = re.compile(r"\{\{\s*zotero\s*:\s*([^}]*)\s*\}\}", re.IGNORECASE)
_ZOTERO_KEY_RE = re.compile(r"^[A-Z0-9]{8}$")
_ZOTERO_BOOKMARK_RE = re.compile(r"^ZOTERO_BREF_(.+)$")
_ZOTERO_CUSTOM_PROP_RE = re.compile(r"^(ZOTERO_BREF_.+)_(\d+)$")
_ZOTERO_TRANSFER_MARKER = "ZOTERO_TRANSFER_DOCUMENT"
_ZOTERO_LINK_TARGET = "https://www.zotero.org/"
_ZOTERO_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"

ET.register_namespace("w", _WORD_NS["w"])
ET.register_namespace("r", _REL_NS["r"])
ET.register_namespace("", _PACKAGE_REL_NS)


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
    fields.extend(_zotero_bookmark_reports(docx_path, root))
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


def zoterify_preflight(
    runtime: RuntimeContext,
    path: str | Path,
    *,
    sample_limit: int = 10,
    session: dict[str, Any] | None = None,
    check_external: bool = True,
) -> dict[str, Any]:
    """Check whether a placeholder DOCX is ready for Zotero/LibreOffice conversion."""
    placeholder_report = validate_placeholders(runtime, path, sample_limit=sample_limit, session=session)
    citation_report = inspect_citations(path, sample_limit=sample_limit)
    placeholder_check = _placeholder_preflight_check(placeholder_report)
    existing_field_check = {
        "ok": True,
        "field_count": citation_report["field_count"],
        "field_counts": citation_report["field_counts"],
        "systems": citation_report["systems"],
        "static_citation_count": citation_report["static_citation_count"],
    }
    external_check = _external_preflight_check(runtime) if check_external else _skipped_external_check()

    ready = placeholder_check["ok"] and existing_field_check["ok"] and external_check["ok"]
    notes = list(placeholder_report["notes"])
    if citation_report["field_count"] or citation_report["static_citation_count"]:
        notes.extend(citation_report["notes"])
    if external_check.get("skipped"):
        notes.append("External checks were skipped; run without --skip-external-checks before attempting real field conversion.")
    elif external_check["ok"]:
        notes.append("Java, LibreOffice, Zotero, and the Zotero LibreOffice plugin look available for the next conversion stage.")
    else:
        notes.append("One or more external dependencies are not ready for Zotero/LibreOffice field conversion.")

    return {
        "path": placeholder_report["path"],
        "ok": ready,
        "ready": ready,
        "checks": {
            "placeholders": placeholder_check,
            "existing_fields": existing_field_check,
            "external": external_check,
        },
        "items": placeholder_report["items"],
        "notes": _dedupe(notes),
    }


def prepare_zotero_import_document(
    runtime: RuntimeContext,
    path: str | Path,
    output: str | Path,
    *,
    style: str = "http://www.zotero.org/styles/apa",
    locale: str = "en-US",
    sample_limit: int = 10,
    session: dict[str, Any] | None = None,
    check_external: bool = True,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Create a Zotero transfer DOCX from {{zotero:ITEMKEY}} placeholders.

    The output is meant to be opened in LibreOffice and imported through the
    Zotero word processor plugin. It uses Zotero's transfer-document markers,
    not static citation text.
    """
    source_path = _validated_docx_path(path)
    output_path = Path(output).expanduser()
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {output_path}")

    preflight = zoterify_preflight(
        runtime,
        source_path,
        sample_limit=sample_limit,
        session=session,
        check_external=check_external,
    )
    if not preflight["checks"]["placeholders"]["ok"]:
        raise ValueError("DOCX placeholders are not ready for Zotero import.")

    item_by_key = {str(item["key"]): item for item in preflight["items"]}
    document_xml = _read_document_xml(source_path)
    document_root = ET.fromstring(document_xml)
    replacement_count = _replace_placeholders_with_import_links(document_root, item_by_key)
    _insert_transfer_marker(document_root)
    prefs_rel_id = f"rIdZoteroImportPrefs{uuid.uuid4().hex[:8]}"
    normalized_style = _normalize_style_id(style)
    _append_document_preferences_link(document_root, prefs_rel_id, style=normalized_style, locale=locale, zotero_version=runtime.environment.version)

    with zipfile.ZipFile(source_path) as source_zip:
        rels_root = _read_or_create_document_rels(source_zip)
        used_rel_ids = _relationship_ids(rels_root)
        _add_hyperlink_relationships(rels_root, replacement_count, used_rel_ids)
        _add_relationship(rels_root, prefs_rel_id, _ZOTERO_REL_TYPE, _ZOTERO_LINK_TARGET, target_mode="External")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as output_zip:
            for info in source_zip.infolist():
                if info.filename in {"word/document.xml", "word/_rels/document.xml.rels"}:
                    continue
                output_zip.writestr(info, source_zip.read(info.filename))
            output_zip.writestr("word/document.xml", ET.tostring(document_root, encoding="utf-8", xml_declaration=True))
            output_zip.writestr("word/_rels/document.xml.rels", ET.tostring(rels_root, encoding="utf-8", xml_declaration=True))

    return {
        "ok": True,
        "path": str(source_path),
        "output": str(output_path),
        "citation_count": preflight["checks"]["placeholders"]["citation_count"],
        "placeholder_count": preflight["checks"]["placeholders"]["placeholder_count"],
        "style": normalized_style,
        "locale": locale,
        "next_step": "Open the output DOCX in LibreOffice, click Zotero Refresh, and confirm the Zotero import prompt.",
        "notes": [
            "Created a Zotero transfer DOCX with ITEM CSL_CITATION import links.",
            "The output is not final until the Zotero LibreOffice plugin imports and refreshes it.",
        ],
    }


def _validated_docx_path(path: str | Path) -> Path:
    docx_path = Path(path).expanduser()
    if not docx_path.exists():
        raise FileNotFoundError(f"DOCX file not found: {docx_path}")
    if docx_path.suffix.lower() != ".docx":
        raise ValueError(f"Expected a .docx file: {docx_path}")
    return docx_path


def _replace_placeholders_with_import_links(root: ET.Element, item_by_key: dict[str, dict[str, Any]]) -> int:
    parent_map = {child: parent for parent in root.iter() for child in parent}
    replacements = 0
    for text_node in list(root.findall(".//w:t", _WORD_NS)):
        text = "".join(text_node.itertext())
        if not _PLACEHOLDER_RE.search(text):
            continue
        run = parent_map.get(text_node)
        if run is None or run.tag != _w("r"):
            continue
        container = parent_map.get(run)
        if container is None:
            continue

        new_nodes: list[ET.Element] = []
        cursor = 0
        for match in _PLACEHOLDER_RE.finditer(text):
            if match.start() > cursor:
                new_nodes.append(_run_with_text(run, text[cursor : match.start()]))
            keys, invalid_parts = _parse_placeholder_keys(match.group(1))
            if invalid_parts or not keys:
                raise ValueError(f"Invalid Zotero placeholder: {match.group(0)}")
            citation_code = _citation_import_code(keys, item_by_key)
            rel_id = f"rIdZoteroImport{replacements + 1}"
            new_nodes.append(_hyperlink_node(rel_id, citation_code))
            replacements += 1
            cursor = match.end()
        if cursor < len(text):
            new_nodes.append(_run_with_text(run, text[cursor:]))

        index = list(container).index(run)
        container.remove(run)
        for offset, node in enumerate(new_nodes):
            container.insert(index + offset, node)
    return replacements


def _citation_import_code(keys: list[str], item_by_key: dict[str, dict[str, Any]]) -> str:
    citation_items = []
    for key in keys:
        item = item_by_key.get(key)
        if not item:
            raise ValueError(f"Zotero item key was not resolved: {key}")
        citation_items.append({"id": int(item["itemID"])})
    payload = {
        "citationItems": citation_items,
        "properties": {"noteIndex": 0},
        "schema": "https://github.com/citation-style-language/schema/raw/master/csl-citation.json",
    }
    return "ITEM CSL_CITATION " + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _insert_transfer_marker(root: ET.Element) -> None:
    body = root.find("w:body", _WORD_NS)
    if body is None:
        raise ValueError("DOCX is missing a word/document.xml body.")
    marker = ET.Element(_w("p"))
    marker.append(_plain_run(_ZOTERO_TRANSFER_MARKER))
    body.insert(0, marker)


def _append_document_preferences_link(root: ET.Element, rel_id: str, *, style: str, locale: str, zotero_version: str) -> None:
    body = root.find("w:body", _WORD_NS)
    if body is None:
        raise ValueError("DOCX is missing a word/document.xml body.")
    paragraph = ET.Element(_w("p"))
    paragraph.append(_hyperlink_node(rel_id, "DOCUMENT_PREFERENCES" + _document_preferences_xml(style=style, locale=locale, zotero_version=zotero_version)))
    transfer_marker_index = _body_transfer_marker_index(body)
    if transfer_marker_index is not None:
        body.insert(transfer_marker_index + 1, paragraph)
    elif (sect_pr_index := _body_sect_pr_index(body)) is not None:
        body.insert(sect_pr_index, paragraph)
    else:
        body.append(paragraph)


def _document_preferences_xml(*, style: str, locale: str, zotero_version: str) -> str:
    session_id = uuid.uuid4().hex
    version = zotero_version if zotero_version and zotero_version != "unknown" else "9.0.0"
    return (
        f'<data data-version="3" zotero-version="{version}">'
        f'<session id="{session_id}"/>'
        f'<style id="{style}" locale="{locale}" hasBibliography="1" bibliographyStyleHasBeenSet="0"/>'
        '<prefs><pref name="fieldType" value="ReferenceMark"/></prefs>'
        "</data>"
    )


def _normalize_style_id(style: str) -> str:
    style = style.strip()
    if not style:
        raise ValueError("Zotero style cannot be empty.")
    if "://" in style:
        return style
    return f"http://www.zotero.org/styles/{style}"


def _body_sect_pr_index(body: ET.Element) -> int | None:
    for index, child in enumerate(list(body)):
        if child.tag == _w("sectPr"):
            return index
    return None


def _body_transfer_marker_index(body: ET.Element) -> int | None:
    for index, child in enumerate(list(body)):
        if _visible_text(child) == _ZOTERO_TRANSFER_MARKER:
            return index
    return None


def _run_with_text(template_run: ET.Element, text: str) -> ET.Element:
    run = ET.Element(_w("r"))
    run_properties = template_run.find("w:rPr", _WORD_NS)
    if run_properties is not None:
        run.append(copy.deepcopy(run_properties))
    text_node = ET.Element(_w("t"))
    if text[:1].isspace() or text[-1:].isspace():
        text_node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    text_node.text = text
    run.append(text_node)
    return run


def _plain_run(text: str) -> ET.Element:
    run = ET.Element(_w("r"))
    text_node = ET.Element(_w("t"))
    text_node.text = text
    run.append(text_node)
    return run


def _hyperlink_node(rel_id: str, text: str) -> ET.Element:
    hyperlink = ET.Element(_w("hyperlink"), {_r("id"): rel_id})
    run = ET.SubElement(hyperlink, _w("r"))
    run_properties = ET.SubElement(run, _w("rPr"))
    ET.SubElement(run_properties, _w("rStyle"), {_w("val"): "Hyperlink"})
    text_node = ET.SubElement(run, _w("t"))
    text_node.text = text
    return hyperlink


def _read_or_create_document_rels(source_zip: zipfile.ZipFile) -> ET.Element:
    try:
        return ET.fromstring(source_zip.read("word/_rels/document.xml.rels"))
    except KeyError:
        return ET.Element(f"{{{_PACKAGE_REL_NS}}}Relationships")


def _relationship_ids(root: ET.Element) -> set[str]:
    return {value for elem in root.findall(f"{{{_PACKAGE_REL_NS}}}Relationship") if (value := elem.attrib.get("Id"))}


def _add_hyperlink_relationships(root: ET.Element, count: int, used_ids: set[str]) -> None:
    for index in range(1, count + 1):
        rel_id = f"rIdZoteroImport{index}"
        if rel_id in used_ids:
            rel_id = _next_relationship_id(used_ids)
        _add_relationship(root, rel_id, _ZOTERO_REL_TYPE, _ZOTERO_LINK_TARGET, target_mode="External")
        used_ids.add(rel_id)


def _add_relationship(root: ET.Element, rel_id: str, rel_type: str, target: str, *, target_mode: str | None = None) -> None:
    attrib = {"Id": rel_id, "Type": rel_type, "Target": target}
    if target_mode:
        attrib["TargetMode"] = target_mode
    ET.SubElement(root, f"{{{_PACKAGE_REL_NS}}}Relationship", attrib)


def _next_relationship_id(used_ids: set[str]) -> str:
    counter = 1
    while f"rIdZoteroImportAuto{counter}" in used_ids:
        counter += 1
    rel_id = f"rIdZoteroImportAuto{counter}"
    used_ids.add(rel_id)
    return rel_id


def _w(name: str) -> str:
    return f"{{{_WORD_NS['w']}}}{name}"


def _r(name: str) -> str:
    return f"{{{_REL_NS['r']}}}{name}"


def _placeholder_preflight_check(report: dict[str, Any]) -> dict[str, Any]:
    has_placeholders = report["citation_count"] > 0
    return {
        "ok": report["ok"] and has_placeholders,
        "placeholder_count": report["placeholder_count"],
        "citation_count": report["citation_count"],
        "unique_keys": report["unique_keys"],
        "invalid_count": len(report["invalid_placeholders"]),
        "missing_count": report["missing_count"],
        "missing_keys": report["missing_keys"],
        "valid_count": report["valid_count"],
    }


def _skipped_external_check() -> dict[str, Any]:
    return {
        "ok": True,
        "skipped": True,
        "java": {"ok": True, "skipped": True},
        "libreoffice": {"ok": True, "skipped": True},
        "zotero": {"ok": True, "skipped": True},
        "plugin": {"ok": True, "skipped": True},
    }


def _external_preflight_check(runtime: RuntimeContext) -> dict[str, Any]:
    java = _check_java()
    libreoffice = _check_libreoffice()
    zotero = _check_zotero_runtime(runtime)
    plugin = _check_libreoffice_plugin(runtime)
    ok = java["ok"] and libreoffice["ok"] and zotero["ok"] and plugin["ok"]
    return {
        "ok": ok,
        "skipped": False,
        "java": java,
        "libreoffice": libreoffice,
        "zotero": zotero,
        "plugin": plugin,
    }


def _check_java() -> dict[str, Any]:
    java = shutil.which("java")
    javac = shutil.which("javac")
    java_home = _java_home()
    version = _command_output([java, "-version"]) if java else ""
    javac_version = _command_output([javac, "-version"]) if javac else ""
    return {
        "ok": bool(java and javac),
        "java": java,
        "javac": javac,
        "java_home": java_home,
        "version": version,
        "javac_version": javac_version,
    }


def _java_home() -> str | None:
    java_home_tool = Path("/usr/libexec/java_home")
    if not java_home_tool.exists():
        return None
    try:
        result = subprocess.run([str(java_home_tool)], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() or None


def _check_libreoffice() -> dict[str, Any]:
    soffice = _find_libreoffice_executable()
    uno_python = _find_libreoffice_python(soffice)
    return {
        "ok": bool(soffice and soffice.exists()),
        "soffice": str(soffice) if soffice else None,
        "python": str(uno_python) if uno_python else None,
        "uno_python_checked": False,
        "uno_python_note": "Skipped because launching LibreOfficePython can crash on some macOS LibreOffice builds and is not required for Zotero plugin import.",
    }


def _find_libreoffice_executable() -> Path | None:
    candidates: list[Path] = []
    for name in ("soffice", "libreoffice"):
        resolved = shutil.which(name)
        if resolved:
            candidates.append(Path(resolved))
    candidates.append(Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _find_libreoffice_python(soffice: Path | None) -> Path | None:
    candidates: list[Path] = []
    if soffice is not None:
        app_bundle = _containing_app_bundle(soffice)
        if app_bundle is not None:
            candidates.append(app_bundle / "Contents" / "Resources" / "python")
    candidates.append(Path("/Applications/LibreOffice.app/Contents/Resources/python"))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _containing_app_bundle(path: Path) -> Path | None:
    for parent in (path, *path.parents):
        if parent.suffix == ".app":
            return parent
    return None


def _check_zotero_runtime(runtime: RuntimeContext) -> dict[str, Any]:
    environment = runtime.environment
    return {
        "ok": bool(environment.executable_exists and runtime.connector_available),
        "executable": str(environment.executable) if environment.executable else None,
        "version": environment.version,
        "port": environment.port,
        "connector_available": runtime.connector_available,
        "connector_message": runtime.connector_message,
        "local_api_available": runtime.local_api_available,
        "local_api_message": runtime.local_api_message,
    }


def _check_libreoffice_plugin(runtime: RuntimeContext) -> dict[str, Any]:
    installed_paths = _installed_libreoffice_plugin_paths()
    bundled_paths = _bundled_libreoffice_plugin_paths(runtime)
    return {
        "ok": bool(installed_paths),
        "installed_paths": [str(path) for path in installed_paths],
        "bundled_paths": [str(path) for path in bundled_paths],
    }


def _installed_libreoffice_plugin_paths() -> list[Path]:
    base = Path.home() / "Library" / "Application Support" / "LibreOffice" / "4" / "user" / "uno_packages" / "cache" / "uno_packages"
    if not base.exists():
        return []
    return sorted(path for path in base.glob("*/Zotero_LibreOffice_Integration.oxt") if path.exists())


def _bundled_libreoffice_plugin_paths(runtime: RuntimeContext) -> list[Path]:
    candidates: list[Path] = [Path("/Applications/Zotero.app/Contents/Resources/integration/libreoffice/Zotero_LibreOffice_Integration.oxt")]
    install_dir = runtime.environment.install_dir
    if install_dir is not None:
        candidates.append(install_dir.parent / "Resources" / "integration" / "libreoffice" / "Zotero_LibreOffice_Integration.oxt")
    return _dedupe_paths(path for path in candidates if path.exists())


def _command_output(command: list[str | None]) -> str:
    if not command[0]:
        return ""
    try:
        result = subprocess.run([str(part) for part in command if part], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    return (result.stdout + result.stderr).strip()


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _dedupe_paths(paths: Any) -> list[Path]:
    deduped: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return deduped


def _read_document_xml(path: Path) -> bytes:
    try:
        with zipfile.ZipFile(path) as zf:
            return zf.read("word/document.xml")
    except KeyError as exc:
        raise ValueError(f"DOCX is missing word/document.xml: {path}") from exc
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Invalid DOCX file: {path}") from exc


def _read_optional_zip_member(path: Path, member: str) -> bytes | None:
    try:
        with zipfile.ZipFile(path) as zf:
            return zf.read(member)
    except KeyError:
        return None
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


def _zotero_bookmark_reports(path: Path, root: ET.Element) -> list[dict[str, str]]:
    bookmark_names = _zotero_bookmark_names(root)
    if not bookmark_names:
        return []
    custom_properties = _zotero_custom_properties(path)
    reports: list[dict[str, str]] = []
    for name in bookmark_names:
        code = custom_properties.get(name, "")
        instruction = code or f"{name} bookmark without custom property data"
        reports.append(
            {
                "system": "zotero",
                "instruction": _truncate(_normalize_space(instruction), 240),
                "field_type": "bookmark",
                "bookmark": name,
            }
        )
    return reports


def _zotero_bookmark_names(root: ET.Element) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    name_attr = _w("name")
    for elem in root.findall(".//w:bookmarkStart", _WORD_NS):
        name = elem.attrib.get(name_attr, "")
        if not _ZOTERO_BOOKMARK_RE.fullmatch(name) or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _zotero_custom_properties(path: Path) -> dict[str, str]:
    custom_xml = _read_optional_zip_member(path, "docProps/custom.xml")
    if not custom_xml:
        return {}
    root = ET.fromstring(custom_xml)
    chunks: dict[str, list[tuple[int, str]]] = {}
    for prop in root.findall(".//{http://schemas.openxmlformats.org/officeDocument/2006/custom-properties}property"):
        name = prop.attrib.get("name", "")
        match = _ZOTERO_CUSTOM_PROP_RE.fullmatch(name)
        if not match:
            continue
        base, index_text = match.groups()
        text = "".join(prop.itertext())
        chunks.setdefault(base, []).append((int(index_text), text))
    return {base: "".join(text for _, text in sorted(parts)) for base, parts in chunks.items()}


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
