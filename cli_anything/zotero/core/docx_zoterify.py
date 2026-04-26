from __future__ import annotations

import copy
import json
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from cli_anything.zotero.core import docx as docx_tools
from cli_anything.zotero.core.discovery import RuntimeContext
from cli_anything.zotero.utils import zotero_paths


DEFAULT_STYLE = "http://www.zotero.org/styles/apa"
DEFAULT_LOCALE = "en-US"
DEFAULT_FIELD_TYPE = "Bookmark"
DEFAULT_BACKEND = "libreoffice"
DEFAULT_BIBLIOGRAPHY = "auto"
_BIBLIOGRAPHY_MODES = {"auto", "none"}
_LINK_BASE = "https://www.zotero.org/?"
_NEXT_STEP = "Run: zotero-cli app install-plugin, restart Zotero, then verify with: zotero-cli app plugin-status"


def build_working_docx(
    runtime: RuntimeContext,
    path: str | Path,
    output: str | Path,
    *,
    session: dict[str, Any] | None = None,
    overwrite: bool = False,
    bibliography: str = DEFAULT_BIBLIOGRAPHY,
) -> dict[str, Any]:
    """Copy a placeholder DOCX and replace Zotero placeholders with note-citation links."""
    _require_bibliography_mode(bibliography)
    source_path = docx_tools._validated_docx_path(path)
    output_path = Path(output).expanduser()
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {output_path}")

    validation = docx_tools.validate_placeholders(runtime, source_path, session=session)
    if not validation["ok"]:
        raise ValueError("DOCX placeholders are not ready for zoterify conversion.")
    if not validation["placeholder_count"]:
        raise ValueError("No Zotero placeholders were found. Use {{zotero:ITEMKEY}} or {{zotero:KEY1,KEY2}}.")

    item_by_key = {str(item["key"]): item for item in validation["items"]}
    root = ET.fromstring(docx_tools._read_document_xml(source_path))
    placeholders = _replace_placeholders_with_note_links(root, item_by_key)
    bibliography_placeholder = _insert_bibliography_placeholder(root) if bibliography == "auto" else None

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source_path) as source_zip:
        rels_root = docx_tools._read_or_create_document_rels(source_zip)
        relationship_entries = list(placeholders)
        if bibliography_placeholder is not None:
            relationship_entries.append(bibliography_placeholder)
        for placeholder in relationship_entries:
            docx_tools._add_relationship(
                rels_root,
                placeholder["relationship_id"],
                docx_tools._ZOTERO_REL_TYPE,
                _LINK_BASE + placeholder["placeholder_id"],
                target_mode="External",
            )
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as output_zip:
            for info in source_zip.infolist():
                if info.filename in {"word/document.xml", "word/_rels/document.xml.rels"}:
                    continue
                output_zip.writestr(info, source_zip.read(info.filename))
            output_zip.writestr("word/document.xml", ET.tostring(root, encoding="utf-8", xml_declaration=True))
            output_zip.writestr("word/_rels/document.xml.rels", ET.tostring(rels_root, encoding="utf-8", xml_declaration=True))

    return {
        "ok": True,
        "path": str(source_path),
        "output": str(output_path),
        "placeholder_count": len(placeholders),
        "citation_count": sum(len(entry["keys"]) for entry in placeholders),
        "placeholders": placeholders,
        "bibliography": bibliography,
        "bibliography_placeholder": bibliography_placeholder,
        "items": validation["items"],
    }


def zoterify_probe(bridge: Any, *, backend: str = DEFAULT_BACKEND) -> dict[str, Any]:
    """Probe Zotero bridge and LibreOffice integration readiness."""
    _require_libreoffice_backend(backend)
    bridge_active = bool(bridge.bridge_endpoint_active())
    if not bridge_active:
        return {
            "ready": False,
            "backend": backend,
            "placeholder_preflight": _placeholder_preflight_capability(),
            "bridge": {
                "active": False,
                "port": getattr(bridge, "port", None),
                "js_result": None,
                "next_step": _NEXT_STEP,
            },
            "zotero_integration": {"exists": None, "application_instantiable": None, "error": None},
            "libreoffice": {"active_document": None, "processor": None, "error": None},
        }

    result = bridge.execute_js_http_required(_probe_js(), wait_seconds=10)
    if not result.get("ok"):
        return {
            "ready": False,
            "backend": backend,
            "placeholder_preflight": _placeholder_preflight_capability(),
            "bridge": {
                "active": True,
                "port": getattr(bridge, "port", None),
                "js_result": False,
                "error": result.get("error"),
                "next_step": "Restart Zotero and rerun zotero-cli app plugin-status.",
            },
            "zotero_integration": {"exists": None, "application_instantiable": None, "error": None},
            "libreoffice": {"active_document": None, "processor": None, "error": None},
        }

    data = result.get("data")
    if not isinstance(data, dict):
        data = {}
    return _normalize_probe_payload(data, bridge=bridge, backend=backend)


def zoterify_doctor(runtime: RuntimeContext, bridge: Any, *, backend: str = DEFAULT_BACKEND) -> dict[str, Any]:
    """Report install/setup readiness for the optional LibreOffice-backed DOCX workflow."""
    _require_libreoffice_backend(backend)
    profile_dir = runtime.environment.profile_dir
    bridge_active = bool(bridge.bridge_endpoint_active())
    bridge_probe = zoterify_probe(bridge, backend=backend) if bridge_active else None
    zotero_check = docx_tools._check_zotero_runtime(runtime)
    libreoffice_check = docx_tools._check_libreoffice()
    libreoffice_plugin_check = docx_tools._check_libreoffice_plugin(runtime)
    installed_bridge_version = zotero_paths.installed_plugin_version(profile_dir)
    bundled_bridge_version = zotero_paths.bundled_plugin_version()
    bridge_update_available = bool(
        installed_bridge_version
        and bundled_bridge_version
        and installed_bridge_version != bundled_bridge_version
    )
    bridge_installed = zotero_paths.plugin_installed(profile_dir)
    integration_payload = bridge_probe.get("zotero_integration", {}) if bridge_probe else {}
    libreoffice_payload = bridge_probe.get("libreoffice", {}) if bridge_probe else {}
    runtime_integration_ok = integration_payload.get("application_instantiable")
    active_document = libreoffice_payload.get("active_document")
    requirements = {
        "python_package": {
            "ok": True,
            "package": "cli-anything-zotero",
            "minimum_version_for_docx_dynamic_citations": "0.8.0",
        },
        "zotero_desktop": zotero_check,
        "cli_bridge_plugin": {
            "ok": bool(bridge_installed and bridge_active and not bridge_update_available),
            "xpi_installed": bridge_installed,
            "endpoint_active": bridge_active,
            "installed_version": installed_bridge_version,
            "bundled_version": bundled_bridge_version,
            "update_available": bridge_update_available,
            "xpi_path": str(zotero_paths.plugin_xpi_path(profile_dir)) if profile_dir else None,
        },
        "libreoffice": libreoffice_check,
        "zotero_libreoffice_integration": {
            "ok": bool(libreoffice_plugin_check["ok"] and runtime_integration_ok is not False),
            "installed_in_libreoffice": bool(libreoffice_plugin_check["ok"]),
            "installed_paths": libreoffice_plugin_check["installed_paths"],
            "bundled_paths": libreoffice_plugin_check["bundled_paths"],
            "runtime_application_instantiable": runtime_integration_ok,
            "runtime_error": integration_payload.get("error"),
        },
        "active_libreoffice_document": {
            "ok": active_document is True,
            "required_for_probe_ready": True,
            "required_for_insert_citations": False,
            "value": active_document,
            "error": libreoffice_payload.get("error"),
        },
    }
    installation_ready = bool(
        requirements["zotero_desktop"]["ok"]
        and requirements["cli_bridge_plugin"]["ok"]
        and requirements["libreoffice"]["ok"]
        and requirements["zotero_libreoffice_integration"]["ok"]
    )
    conversion_probe_ready = bool(bridge_probe and bridge_probe.get("ready"))
    return {
        "ok": installation_ready,
        "ready": installation_ready,
        "backend": backend,
        "workflow": "optional LibreOffice-backed dynamic DOCX citations",
        "installation_ready": installation_ready,
        "conversion_probe_ready": conversion_probe_ready,
        "requirements": requirements,
        "probe": bridge_probe,
        "upgrade_steps": _upgrade_steps(),
        "next_steps": _doctor_next_steps(requirements, installation_ready, conversion_probe_ready),
    }


def zoterify_document(
    runtime: RuntimeContext,
    bridge: Any,
    path: str | Path,
    output: str | Path,
    *,
    backend: str = DEFAULT_BACKEND,
    style: str = DEFAULT_STYLE,
    locale: str = DEFAULT_LOCALE,
    field_type: str = DEFAULT_FIELD_TYPE,
    bibliography: str = DEFAULT_BIBLIOGRAPHY,
    session: dict[str, Any] | None = None,
    open_document: bool = True,
    overwrite: bool = False,
    debug_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Convert {{zotero:KEY}} placeholders into Zotero LibreOffice fields."""
    _require_libreoffice_backend(backend)
    _require_bibliography_mode(bibliography)
    if not bridge.bridge_endpoint_active():
        raise RuntimeError(
            "CLI Bridge endpoint is not active. Run: zotero-cli app install-plugin, "
            "restart Zotero, then verify with: zotero-cli app plugin-status"
        )

    final_output_path = Path(output).expanduser()
    if final_output_path.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {final_output_path}")
    conversion_output_path = _working_output_path(final_output_path) if open_document else final_output_path
    working = build_working_docx(runtime, path, conversion_output_path, session=session, overwrite=True, bibliography=bibliography)
    debug_path = Path(debug_dir).expanduser() if debug_dir is not None else None
    if debug_path is not None:
        _write_debug_json(debug_path / "01-placeholder-map.json", working)
    output_path = Path(working["output"])
    try:
        open_result = _open_in_libreoffice(output_path) if open_document else {"attempted": False, "ok": None}
        if open_result.get("ok"):
            time.sleep(3)
        bridge_result = bridge.execute_js_http_required(
            _zoterify_js(
                placeholders=working["placeholders"],
                bibliography_placeholder=working["bibliography_placeholder"],
                style=docx_tools._normalize_style_id(style),
                locale=locale,
                field_type=field_type,
                bibliography=bibliography,
            ),
            wait_seconds=60,
        )
        if not bridge_result.get("ok"):
            raise RuntimeError(f"Zotero LibreOffice conversion failed: {bridge_result.get('error')}")

        bridge_payload = bridge_result.get("data")
        if not isinstance(bridge_payload, dict):
            raise RuntimeError(f"Zotero LibreOffice conversion returned an unexpected result: {bridge_payload!r}")
        if debug_path is not None:
            _write_debug_json(debug_path / "02-bridge-result.json", bridge_payload)
        if not bridge_payload.get("converted"):
            error = bridge_payload.get("error") or "LibreOffice did not report a completed placeholder conversion."
            raise RuntimeError(_friendly_conversion_error(str(error), final_output_path))

        save_result = _save_active_libreoffice_document(output_path) if open_result.get("ok") else {"attempted": False, "ok": None}
        if save_result.get("ok"):
            time.sleep(1)
        inspection = docx_tools.inspect_citations(output_path, sample_limit=10000)
        zotero_counts = _zotero_field_type_counts(inspection)
        bibliography_expected = bibliography == "auto"
        ready_for_user = bool(
            zotero_counts["citation"] >= working["placeholder_count"]
            and (not bibliography_expected or zotero_counts["bibliography"] >= 1)
        )
        if debug_path is not None:
            _write_debug_json(debug_path / "03-inspect-citations.json", inspection)
        if not ready_for_user:
            raise RuntimeError(
                "Zotero conversion did not persist to the output DOCX. "
                f"stage=inspect-citations output={final_output_path} "
                f"citation_fields={zotero_counts['citation']} bibliography_fields={zotero_counts['bibliography']}. "
                "Close any existing LibreOffice window for this output file, or choose a new --output path, then rerun the same command."
            )
        _normalize_custom_properties_for_word(output_path)
        if output_path != final_output_path:
            output_path.replace(final_output_path)
            output_path = final_output_path
            inspection = docx_tools.inspect_citations(output_path, sample_limit=10000)
            zotero_counts = _zotero_field_type_counts(inspection)
    except Exception:
        if output_path != final_output_path and debug_path is None:
            try:
                output_path.unlink()
            except FileNotFoundError:
                pass
        raise
    return {
        "ok": True,
        "input": str(Path(path).expanduser()),
        "backend": backend,
        "path": str(Path(path).expanduser()),
        "output": str(final_output_path),
        "style": docx_tools._normalize_style_id(style),
        "locale": locale,
        "field_type": field_type,
        "bibliography": bibliography,
        "placeholder_count": working["placeholder_count"],
        "converted_placeholders": working["placeholder_count"],
        "citation_count": working["citation_count"],
        "citation_fields": zotero_counts["citation"],
        "bibliography_fields": zotero_counts["bibliography"],
        "has_zotero_fields": bool(inspection["field_counts"].get("zotero")),
        "saved": bool(save_result.get("ok")),
        "save": save_result,
        "ready_for_user": ready_for_user,
        "open": open_result,
        "bridge": bridge_payload,
        "inspection": {
            "field_count": inspection["field_count"],
            "field_counts": inspection["field_counts"],
            "systems": inspection["systems"],
        },
        "artifacts": {
            "input": str(Path(path).expanduser()),
            "output": str(final_output_path),
            "debug_dir": str(debug_path) if debug_path is not None else None,
        },
        "notes": _zoterify_notes(inspection),
    }


def _insert_bibliography_placeholder(root: ET.Element) -> dict[str, Any]:
    body = root.find("w:body", docx_tools._WORD_NS)
    if body is None:
        raise ValueError("DOCX document.xml has no w:body element.")
    placeholder_id = "ZOTERO_CLI_BIBLIOGRAPHY_" + uuid.uuid4().hex
    relationship_id = "rIdZoteroCliBib" + uuid.uuid4().hex[:16]
    paragraph = ET.Element(docx_tools._w("p"))
    paragraph.append(_hyperlink_node(relationship_id, placeholder_id))

    children = list(body)
    insert_at = _bibliography_insert_index(children)
    placement = "after-existing-heading"
    if insert_at is None:
        insert_at = _append_references_heading(body)
        placement = "appended-heading"
    body.insert(insert_at, paragraph)
    return {
        "placeholder_id": placeholder_id,
        "relationship_id": relationship_id,
        "placement": placement,
    }


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
    heading = ET.Element(docx_tools._w("p"))
    run = ET.SubElement(heading, docx_tools._w("r"))
    text = ET.SubElement(run, docx_tools._w("t"))
    text.text = "References"
    if sect_pr is not None:
        index = list(body).index(sect_pr)
        body.insert(index, heading)
        return index + 1
    body.append(heading)
    return len(list(body))


def _replace_placeholders_with_note_links(root: ET.Element, item_by_key: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    parent_map = {child: parent for parent in root.iter() for child in parent}
    placeholders: list[dict[str, Any]] = []
    for text_node in list(root.findall(".//w:t", docx_tools._WORD_NS)):
        text = "".join(text_node.itertext())
        if not docx_tools._PLACEHOLDER_RE.search(text):
            continue
        run = parent_map.get(text_node)
        if run is None or run.tag != docx_tools._w("r"):
            continue
        container = parent_map.get(run)
        if container is None:
            continue

        new_nodes: list[ET.Element] = []
        cursor = 0
        for match in docx_tools._PLACEHOLDER_RE.finditer(text):
            if match.start() > cursor:
                new_nodes.append(_run_with_text(run, text[cursor : match.start()]))
            keys, invalid_parts = docx_tools._parse_placeholder_keys(match.group(1))
            if invalid_parts or not keys:
                raise ValueError(f"Invalid Zotero placeholder: {match.group(0)}")
            placeholder_id = "ZOTERO_CLI_PLACEHOLDER_" + uuid.uuid4().hex
            relationship_id = "rIdZoteroCli" + uuid.uuid4().hex[:16]
            entry = {
                "placeholder_id": placeholder_id,
                "relationship_id": relationship_id,
                "keys": keys,
                "items": [_placeholder_item(item_by_key[key]) for key in keys],
                "citation": _citation_payload(keys, item_by_key),
            }
            placeholders.append(entry)
            new_nodes.append(_hyperlink_node(relationship_id, placeholder_id))
            cursor = match.end()
        if cursor < len(text):
            new_nodes.append(_run_with_text(run, text[cursor:]))

        index = list(container).index(run)
        container.remove(run)
        for offset, node in enumerate(new_nodes):
            container.insert(index + offset, node)
    return placeholders


def _citation_payload(keys: list[str], item_by_key: dict[str, dict[str, Any]]) -> dict[str, Any]:
    citation_items = []
    for key in keys:
        item = item_by_key.get(key)
        if not item:
            raise ValueError(f"Zotero item key was not resolved: {key}")
        citation_items.append({"id": int(item["itemID"])})
    return {
        "citationItems": citation_items,
        "properties": {"noteIndex": 0},
        "schema": "https://github.com/citation-style-language/schema/raw/master/csl-citation.json",
    }


def _placeholder_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "itemID": int(item["itemID"]),
        "key": item.get("key"),
        "libraryID": item.get("libraryID"),
        "title": item.get("title") or "",
    }


def _run_with_text(template_run: ET.Element, text: str) -> ET.Element:
    run = ET.Element(docx_tools._w("r"))
    run_properties = template_run.find("w:rPr", docx_tools._WORD_NS)
    if run_properties is not None:
        run.append(copy.deepcopy(run_properties))
    text_node = ET.Element(docx_tools._w("t"))
    if text[:1].isspace() or text[-1:].isspace():
        text_node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    text_node.text = text
    run.append(text_node)
    return run


def _hyperlink_node(rel_id: str, text: str) -> ET.Element:
    hyperlink = ET.Element(docx_tools._w("hyperlink"), {docx_tools._r("id"): rel_id})
    run = ET.SubElement(hyperlink, docx_tools._w("r"))
    text_node = ET.SubElement(run, docx_tools._w("t"))
    text_node.text = text
    return hyperlink


def _probe_js() -> str:
    return """
const out = {
  bridge: { active: true, js_result: true },
  zotero_integration: { exists: !!Zotero.Integration, application_instantiable: false, error: null },
  libreoffice: { active_document: false, processor: null, error: null }
};
try {
  if (!Zotero.Integration) {
    out.ready = false;
    return out;
  }
  const app = Zotero.Integration.getApplication('LibreOffice', 'refresh');
  out.zotero_integration.application_instantiable = !!app;
  out.libreoffice.processor = app && app.processorName || 'LibreOffice';
  try {
    const doc = await app.getActiveDocument();
    out.libreoffice.active_document = !!doc;
  } catch (e) {
    out.libreoffice.error = e && (e.message || e.toString()) || String(e);
  }
} catch (e) {
  out.zotero_integration.error = e && (e.message || e.toString()) || String(e);
}
out.ready = !!(out.bridge.active && out.zotero_integration.exists && out.zotero_integration.application_instantiable && out.libreoffice.active_document);
return out;
"""


def _zoterify_js(
    *,
    placeholders: list[dict[str, Any]],
    bibliography_placeholder: dict[str, Any] | None,
    style: str,
    locale: str,
    field_type: str,
    bibliography: str,
) -> str:
    payload = {
        "placeholders": placeholders,
        "bibliography": {
            "mode": bibliography,
            "placeholder": bibliography_placeholder,
        },
        "style": style,
        "locale": locale,
        "fieldType": field_type,
    }
    payload_js = json.dumps(payload, ensure_ascii=False)
    return f"""
try {{
const payload = {payload_js};
const result = {{ ready: false, converted: false, field_count: 0, citation_field_count: 0, bibliography_field_count: 0, document_data_written: false, updated: false }};
if (!Zotero.Integration) {{
  throw new Error('Zotero.Integration is not available');
}}
const app = Zotero.Integration.getApplication('LibreOffice', 'refresh');
const doc = await app.getActiveDocument();
if (!doc) {{
  throw new Error('LibreOffice has no active document. Open the output DOCX in LibreOffice and retry.');
}}
const placeholderIDs = payload.placeholders.map(p => p.placeholder_id);
let allPlaceholderIDs = Array.from(placeholderIDs);
if (payload.bibliography.mode === 'auto') {{
  if (!payload.bibliography.placeholder) {{
    throw new Error('Bibliography mode is auto, but no bibliography placeholder was prepared.');
  }}
  allPlaceholderIDs.push(payload.bibliography.placeholder.placeholder_id);
}}
const convertedFields = await doc.convertPlaceholdersToFields(allPlaceholderIDs, 0, payload.fieldType);
if (convertedFields.length !== allPlaceholderIDs.length) {{
  throw new Error(`Converted ${{convertedFields.length}} placeholders, expected ${{allPlaceholderIDs.length}}`);
}}
const fields = convertedFields.slice(0, payload.placeholders.length);
const bibliographyFields = convertedFields.slice(payload.placeholders.length);
result.citation_field_count = fields.length;
result.bibliography_field_count = bibliographyFields.length;
const session = new Zotero.Integration.Session(doc, app);
session.agent = 'LibreOffice';
session._doc = doc;
session.rebuildCiteprocState = true;
session.progressBar = new Zotero.Integration.Progress(4, false);
const data = new Zotero.Integration.DocumentData();
data.style.styleID = payload.style;
data.style.locale = payload.locale;
data.style.hasBibliography = payload.bibliography.mode === 'auto';
data.style.bibliographyStyleHasBeenSet = false;
data.prefs.fieldType = payload.fieldType;
data.prefs.noteType = 0;
data.prefs.automaticJournalAbbreviations = false;
await session.setData(data, true);
Zotero.Integration.currentSession = session;
Zotero.Integration.currentDoc = doc;
for (let i = 0; i < fields.length; i++) {{
  const citationField = new Zotero.Integration.CitationField(fields[i], 'TEMP');
  await citationField.setCode(JSON.stringify(payload.placeholders[i].citation));
}}
let sessionFields = Array.from(fields);
if (bibliographyFields.length) {{
  const bibliographyField = new Zotero.Integration.BibliographyField(bibliographyFields[0], 'TEMP');
  await bibliographyField.clearCode();
  sessionFields.push(bibliographyField);
}}
session._fields = sessionFields;
await session.updateFromDocument(false);
await session.updateDocument(false, true, false);
await doc.setDocumentData(session.data.serialize());
if (doc.complete) {{
  await doc.complete();
}}
try {{
  const finalFields = Array.from(await doc.getFields(payload.fieldType));
  result.field_count = finalFields.length;
  result.citation_field_count = 0;
  result.bibliography_field_count = 0;
  for (const field of finalFields) {{
    const code = String(await field.getCode());
    if (code.includes('CSL_BIBLIOGRAPHY') || code.startsWith('BIBL')) {{
      result.bibliography_field_count++;
    }} else if (code.includes('CSL_CITATION') || code.startsWith('ITEM') || code.startsWith('CITATION')) {{
      result.citation_field_count++;
    }}
  }}
}} catch (_) {{
  result.field_count = fields.length + bibliographyFields.length;
}}
result.document_data_written = true;
result.updated = true;
result.converted = true;
result.ready = true;
return result;
}} catch (e) {{
  return {{
    ready: false,
    converted: false,
    error: e && (e.message || e.toString()) || String(e),
    error_type: Object.prototype.toString.call(e),
    stack: e && e.stack || null
  }};
}}
"""


def _normalize_probe_payload(data: dict[str, Any], *, bridge: Any, backend: str) -> dict[str, Any]:
    bridge_payload = data.get("bridge") if isinstance(data.get("bridge"), dict) else {}
    integration_payload = data.get("zotero_integration") if isinstance(data.get("zotero_integration"), dict) else {}
    libreoffice_payload = data.get("libreoffice") if isinstance(data.get("libreoffice"), dict) else {}
    bridge_payload.setdefault("active", True)
    bridge_payload.setdefault("port", getattr(bridge, "port", None))
    bridge_payload.setdefault("js_result", True)
    integration_payload.setdefault("exists", None)
    integration_payload.setdefault("application_instantiable", None)
    integration_payload.setdefault("error", None)
    libreoffice_payload.setdefault("active_document", None)
    libreoffice_payload.setdefault("processor", None)
    libreoffice_payload.setdefault("error", None)
    ready = bool(data.get("ready"))
    return {
        "ready": ready,
        "backend": backend,
        "placeholder_preflight": _placeholder_preflight_capability(),
        "bridge": bridge_payload,
        "zotero_integration": integration_payload,
        "libreoffice": libreoffice_payload,
    }


def _open_in_libreoffice(path: Path) -> dict[str, Any]:
    if sys.platform == "darwin":
        try:
            subprocess.run(["open", "-a", "LibreOffice", str(path)], capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"attempted": True, "ok": False, "error": str(exc)}
        return {"attempted": True, "ok": True, "method": "open -a LibreOffice"}

    soffice = docx_tools._find_libreoffice_executable()
    if soffice is None:
        return {"attempted": True, "ok": False, "error": "LibreOffice executable was not found."}
    try:
        subprocess.Popen([str(soffice), str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        return {"attempted": True, "ok": False, "soffice": str(soffice), "error": str(exc)}
    return {"attempted": True, "ok": True, "soffice": str(soffice)}


def _working_output_path(output_path: Path) -> Path:
    return output_path.with_name(f".{output_path.stem}.zoterify-work-{uuid.uuid4().hex}{output_path.suffix}")


def _normalize_custom_properties_for_word(path: Path) -> None:
    """Rewrite docProps/custom.xml using an XML serializer Word accepts more reliably.

    LibreOffice may save Zotero JSON in custom properties with every quote as
    &quot;. The XML is valid, but Word for Mac can reject those files while
    accepting the same properties after normal XML text serialization.
    """
    with zipfile.ZipFile(path) as source_zip:
        if "docProps/custom.xml" not in source_zip.namelist():
            return
        custom_root = ET.fromstring(source_zip.read("docProps/custom.xml"))
        custom_xml = ET.tostring(custom_root, encoding="utf-8", xml_declaration=True)
        entries = [(info, source_zip.read(info.filename)) for info in source_zip.infolist()]

    temp_path = path.with_name(f".{path.stem}.word-compat-{uuid.uuid4().hex}{path.suffix}")
    with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as output_zip:
        for info, data in entries:
            if info.filename == "docProps/custom.xml":
                data = custom_xml
            output_zip.writestr(info, data)
    temp_path.replace(path)


def _placeholder_preflight_capability() -> dict[str, Any]:
    return {
        "ok": True,
        "accepted_patterns": ["{{zotero:ITEMKEY}}", "{{zotero:KEY1,KEY2}}"],
        "key_rule": "8 uppercase letters or digits after normalization",
    }


def _write_debug_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _zotero_field_type_counts(inspection: dict[str, Any]) -> dict[str, int]:
    counts = {"citation": 0, "bibliography": 0}
    for field in inspection.get("fields", []):
        if field.get("system") != "zotero":
            continue
        instruction = str(field.get("instruction") or "").upper()
        if "CSL_BIBLIOGRAPHY" in instruction or "ZOTERO_BIBL" in instruction:
            counts["bibliography"] += 1
        elif "CSL_CITATION" in instruction or "ZOTERO_ITEM" in instruction or "ADDIN ZOTERO" in instruction:
            counts["citation"] += 1
    return counts


def _friendly_conversion_error(error: str, output_path: Path) -> str:
    if "convertPlaceholdersToFields: number of placeholders (0)" in error:
        return (
            "LibreOffice active document does not contain the prepared Zotero placeholders. "
            f"The output file may already be open in LibreOffice: {output_path}. "
            "Close that LibreOffice window, or choose a new --output path, then rerun the same command."
        )
    if "convertPlaceholdersToFields: number of placeholders" in error:
        return (
            "LibreOffice saw a different number of Zotero placeholders than the CLI prepared. "
            "Close other LibreOffice documents or choose a new --output path, then rerun the same command. "
            f"Original Zotero error: {error}"
        )
    return error


def _save_active_libreoffice_document(path: Path) -> dict[str, Any]:
    if sys.platform != "darwin":
        return {"attempted": False, "ok": None, "reason": "automatic LibreOffice save is only implemented on macOS"}
    target_name = json.dumps(path.name)
    script = f'''
tell application "LibreOffice" to activate
delay 0.5
tell application "System Events"
  set targetName to {target_name}
  set foundTarget to false
  if exists process "soffice" then
    tell process "soffice"
      set frontmost to true
      repeat with w in windows
        try
          if name of w contains targetName then
            perform action "AXRaise" of w
            set foundTarget to true
            exit repeat
          end if
        end try
      end repeat
      if foundTarget is false then
        error "LibreOffice target document window was not found: " & targetName
      end if
      delay 0.3
      keystroke "s" using command down
    end tell
  else
    error "LibreOffice process was not found"
  end if
end tell
delay 1.0
tell application "System Events"
  if exists process "soffice" then
    tell process "soffice"
      if exists button "Use Word 2007 Format" of window 1 then
        click button "Use Word 2007 Format" of window 1
      end if
    end tell
  end if
end tell
'''
    try:
        completed = subprocess.run(["osascript"], input=script, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"attempted": True, "ok": False, "error": str(exc)}
    return {
        "attempted": True,
        "ok": completed.returncode == 0,
        "method": "osascript command-s",
        "stderr": completed.stderr.strip() or None,
    }


def _zoterify_notes(inspection: dict[str, Any]) -> list[str]:
    if inspection["field_count"]:
        return ["The output DOCX contains citation fields according to inspect-citations."]
    return [
        "The bridge reported conversion, but inspect-citations did not see saved fields in the DOCX yet.",
        "Save the document from LibreOffice, then rerun docx inspect-citations on the output file.",
    ]


def _upgrade_steps() -> list[str]:
    return [
        "python -m pip install -U cli-anything-zotero",
        "zotero-cli app install-plugin",
        "Restart Zotero so the updated CLI Bridge endpoint is active.",
        "Run: zotero-cli app plugin-status",
        "Run: zotero-cli docx doctor",
    ]


def _doctor_next_steps(requirements: dict[str, Any], installation_ready: bool, conversion_probe_ready: bool) -> list[str]:
    if installation_ready and conversion_probe_ready:
        return ["Ready. Run: zotero-cli docx insert-citations input.docx --output final.docx --force"]
    steps: list[str] = []
    if not requirements["zotero_desktop"]["ok"]:
        steps.append("Install and open Zotero Desktop, then make sure Zotero's local connector API is available.")
    bridge = requirements["cli_bridge_plugin"]
    if not bridge["xpi_installed"] or bridge["update_available"]:
        steps.append("Update the Python package and CLI Bridge plugin: python -m pip install -U cli-anything-zotero && zotero-cli app install-plugin")
        steps.append("Restart Zotero, then run: zotero-cli app plugin-status")
    elif not bridge["endpoint_active"]:
        steps.append("Restart Zotero. If the bridge is still inactive, rerun: zotero-cli app install-plugin")
    if not requirements["libreoffice"]["ok"]:
        steps.append("Install LibreOffice, then reopen Zotero and LibreOffice.")
    integration = requirements["zotero_libreoffice_integration"]
    if not integration["installed_in_libreoffice"]:
        steps.append("In Zotero, open Settings/Preferences > Cite > Word Processors, then install the LibreOffice Add-in.")
    elif integration["runtime_application_instantiable"] is False:
        steps.append("Reinstall the Zotero LibreOffice Add-in from Zotero Settings/Preferences > Cite > Word Processors, then restart Zotero and LibreOffice.")
    if installation_ready and not conversion_probe_ready:
        steps.append("For a strict probe, open a DOCX in LibreOffice and rerun: zotero-cli docx zoterify-probe")
        steps.append("For normal AI use, run insert-citations with --open so the command can open the prepared DOCX automatically.")
    return steps or ["Rerun: zotero-cli docx doctor"]


def _require_libreoffice_backend(backend: str) -> None:
    if backend.lower() != DEFAULT_BACKEND:
        raise ValueError("Only the libreoffice backend is supported for docx zoterify.")


def _require_bibliography_mode(bibliography: str) -> None:
    if bibliography not in _BIBLIOGRAPHY_MODES:
        raise ValueError("Bibliography mode must be one of: auto, none.")
