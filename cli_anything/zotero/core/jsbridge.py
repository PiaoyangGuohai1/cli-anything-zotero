"""Zotero JavaScript execution bridge.

Two transport modes (auto-selected):
1. HTTP mode (preferred): POST to /cli-bridge/eval on Zotero's HTTP server.
   Zero UI, instant response, returns structured data.
   The endpoint is registered automatically by the CLI Bridge Zotero plugin.

2. AppleScript mode (deprecated, macOS-only fallback): GUI automation of
   Zotero's "Run JavaScript" dialog. Used only when the plugin is not
   installed and the platform is macOS.

Requirements: Zotero 7+ running with the CLI Bridge plugin installed.
              Install via: cli-anything-zotero app install-plugin
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import warnings

_BRIDGE_URL = "http://localhost:23119/cli-bridge/eval"
_RESULT_FILE = os.path.join(tempfile.gettempdir(), "zotero-cli-result.json")

_LOCALE = os.environ.get("ZOTERO_LOCALE", "en")

_MENU_PATHS = {
    "en": 'click menu item "Run JavaScript" of menu "Developer" of menu item "Developer" of menu "Tools" of menu bar 1',
    "zh": 'click menu item "Run JavaScript" of menu "开发者" of menu item "开发者" of menu "工具" of menu bar 1',
}

# Registration JS — adds the /cli-bridge/eval HTTP endpoint to Zotero
_REGISTER_JS = (
    "var ep = function() {}; "
    "ep.prototype = {supportedMethods: ['POST'], supportedDataTypes: ['text/plain'], "
    "permitBookmarklet: false, "
    "init: async function(options) { "
    "try { var result = await eval('(async () => {' + options.data + '})()'); "
    "return [200, 'application/json', JSON.stringify(result)]; "
    "} catch(e) { return [500, 'application/json', JSON.stringify({error: e.message})]; } "
    "}}; "
    "Zotero.Server.Endpoints['/cli-bridge/eval'] = ep; "
    "return 'registered';"
)


def _check_applescript_platform() -> None:
    """Guard for the deprecated AppleScript code path (macOS only)."""
    if sys.platform != "darwin":
        raise RuntimeError(
            "AppleScript is not available on this platform. "
            "Install the CLI Bridge plugin: cli-anything-zotero app install-plugin"
        )


def bridge_endpoint_active() -> bool:
    """Check if the CLI bridge HTTP endpoint is registered and responding."""
    try:
        req = urllib.request.Request(
            _BRIDGE_URL,
            data=b"return 'ping';",
            headers={"Content-Type": "text/plain"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def _execute_http(code: str, *, timeout: int = 30) -> dict:
    """Execute JS via the HTTP bridge. Returns {"ok": bool, "data": ..., "error": ...}."""
    try:
        req = urllib.request.Request(
            _BRIDGE_URL,
            data=code.encode("utf-8"),
            headers={"Content-Type": "text/plain"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                data = body
            return {"ok": True, "data": data, "error": None}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            err = json.loads(body)
            return {"ok": False, "data": None, "error": err.get("error", body)}
        except json.JSONDecodeError:
            return {"ok": False, "data": None, "error": body}
    except Exception as e:
        return {"ok": False, "data": None, "error": str(e)}


def _execute_applescript(code: str, *, wait_seconds: int = 3, capture: bool = True) -> dict:
    """Execute JS via AppleScript GUI automation (deprecated macOS-only fallback)."""
    _check_applescript_platform()

    if capture and os.path.exists(_RESULT_FILE):
        os.remove(_RESULT_FILE)

    js_code = _inject_result_capture(code) if capture else code
    escaped = js_code.replace("\\", "\\\\").replace('"', '\\"')

    menu_click = _MENU_PATHS.get(_LOCALE, _MENU_PATHS["en"])

    applescript = f'''
tell application "Zotero" to activate
delay 0.5
-- Close any existing Run JavaScript window first
tell application "System Events"
    tell process "zotero"
        set winNames to name of every window
        repeat with w in winNames
            if w contains "JavaScript" or w contains "javascript" then
                keystroke "w" using command down
                delay 0.3
            end if
        end repeat
    end tell
end tell
delay 0.3
tell application "System Events"
    tell process "zotero"
        {menu_click}
    end tell
end tell
delay 1.5
tell application "System Events"
    keystroke "a" using command down
    delay 0.3
    set the clipboard to "{escaped}"
    keystroke "v" using command down
    delay 0.5
    keystroke "r" using command down
end tell
delay {wait_seconds}
tell application "System Events"
    tell process "zotero"
        keystroke "w" using command down
    end tell
end tell
'''
    try:
        subprocess.run(
            ["osascript", "-e", applescript],
            capture_output=True, text=True, timeout=wait_seconds + 15,
        )
        if capture:
            return _read_result()
        return {"ok": True, "error": None}
    except subprocess.TimeoutExpired:
        return {"ok": False, "data": None, "error": "AppleScript timed out"}
    except Exception as e:
        return {"ok": False, "data": None, "error": str(e)}


def _inject_result_capture(code: str) -> str:
    """Wrap JS code in an async function to capture return value via temp file."""
    # Use forward slashes so the path is valid JavaScript on all platforms.
    safe_path = _RESULT_FILE.replace("\\", "/")
    return (
        f"var __r = await (async () => {{ {code} }})(); "
        f"await Zotero.File.putContentsAsync('{safe_path}', JSON.stringify(__r)); "
        f"return __r;"
    )


def _read_result() -> dict:
    """Read and parse the result file written by Zotero JS."""
    try:
        with open(_RESULT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        os.remove(_RESULT_FILE)
        return {"ok": True, "data": data, "error": None}
    except FileNotFoundError:
        return {"ok": True, "data": None, "error": None}
    except json.JSONDecodeError:
        try:
            with open(_RESULT_FILE, "r", encoding="utf-8") as f:
                raw = f.read()
            os.remove(_RESULT_FILE)
            return {"ok": True, "data": raw, "error": None}
        except Exception as e:
            return {"ok": True, "data": None, "error": f"JSON decode error: {e}"}


def ensure_bridge() -> dict:
    """Ensure the HTTP bridge is registered.

    The recommended way is to install the CLI Bridge Zotero plugin, which
    registers the endpoint automatically on startup (works on all platforms).
    On macOS, AppleScript is kept as a deprecated fallback.
    """
    if bridge_endpoint_active():
        return {"ok": True, "data": "HTTP bridge already active", "error": None}

    # Plugin not loaded — try AppleScript fallback on macOS
    if sys.platform == "darwin":
        warnings.warn(
            "AppleScript bridge registration is deprecated. "
            "Install the CLI Bridge plugin instead: "
            "cli-anything-zotero app install-plugin",
            DeprecationWarning,
            stacklevel=2,
        )
        result = _execute_applescript(_REGISTER_JS, wait_seconds=4, capture=True)
        if bridge_endpoint_active():
            return {"ok": True, "data": "HTTP bridge registered via AppleScript (deprecated)", "error": None}
        return {"ok": False, "data": None, "error": f"Failed to register HTTP bridge: {result}"}

    # Non-macOS: no fallback available
    return {
        "ok": False,
        "data": None,
        "error": (
            "JS Bridge endpoint not available. "
            "Install the CLI Bridge plugin: cli-anything-zotero app install-plugin, "
            "then restart Zotero."
        ),
    }


def execute_js(code: str, *, wait_seconds: int = 3, capture: bool = True) -> dict:
    """Execute JavaScript in Zotero.

    Uses the HTTP bridge (registered by the CLI Bridge plugin).
    Falls back to AppleScript on macOS if the plugin is not installed.

    Returns {"ok": bool, "data": <result>, "error": str | None}
    """
    # Try HTTP first (no UI)
    if bridge_endpoint_active():
        return _execute_http(code, timeout=max(wait_seconds, 10))

    # Auto-register bridge, then retry HTTP
    reg = ensure_bridge()
    if reg["ok"] and bridge_endpoint_active():
        return _execute_http(code, timeout=max(wait_seconds, 10))

    # On macOS, final fallback to AppleScript (deprecated)
    if sys.platform == "darwin":
        return _execute_applescript(code, wait_seconds=wait_seconds, capture=capture)

    # Non-macOS: return the registration error
    return reg


# ── High-level functions ─────────────────────────────────────────────

def attach_pdf(item_key: str, pdf_path: str, *, library_id: int = 1) -> dict:
    """Attach a local PDF file to an existing Zotero item."""
    abs_path = os.path.abspath(pdf_path)
    if not os.path.isfile(abs_path):
        return {"ok": False, "error": f"File not found: {abs_path}"}
    js = (
        f"var item = Zotero.Items.getByLibraryAndKey({library_id}, '{item_key}'); "
        f"if (!item) {{ return 'ERROR: item {item_key} not found'; }} "
        f"var att = await Zotero.Attachments.importFromFile({{file: '{abs_path}', parentItemID: item.id}}); "
        f"return 'OK: ' + att.key + ' attached to ' + item.getField('title').substring(0,60);"
    )
    return execute_js(js, wait_seconds=4)


def find_pdf(item_key: str, *, library_id: int = 1) -> dict:
    """Trigger 'Find Available PDF' for a single Zotero item."""
    js = (
        f"var item = Zotero.Items.getByLibraryAndKey({library_id}, '{item_key}'); "
        f"if (!item) {{ return 'ERROR: item {item_key} not found'; }} "
        f"var att = await Zotero.Attachments.addAvailablePDF(item); "
        f"return att ? 'FOUND: ' + att.key : 'NOT_FOUND: no PDF available for ' + item.getField('title').substring(0,60);"
    )
    return execute_js(js, wait_seconds=15)


def update_item_fields(item_key: str, fields_dict: dict[str, str], *, library_id: int = 1) -> dict:
    """Update metadata fields on an existing Zotero item."""
    if not fields_dict:
        return {"ok": False, "error": "No fields provided"}
    set_lines = " ".join(
        f"item.setField('{k}', '{v.replace(chr(39), chr(92) + chr(39))}');"
        for k, v in fields_dict.items()
    )
    js = (
        f"var item = Zotero.Items.getByLibraryAndKey({library_id}, '{item_key}'); "
        f"if (!item) {{ return 'ERROR: item {item_key} not found'; }} "
        f"{set_lines} "
        f"await item.saveTx(); "
        f"return 'OK: updated ' + item.getField('title').substring(0,60);"
    )
    return execute_js(js, wait_seconds=4)


def manage_tags(item_key: str, add_tags: list[str], remove_tags: list[str], *, library_id: int = 1) -> dict:
    """Add and/or remove tags on an existing Zotero item."""
    if not add_tags and not remove_tags:
        return {"ok": False, "error": "No tags to add or remove"}
    tag_lines = ""
    for t in add_tags:
        safe = t.replace("'", "\\'")
        tag_lines += f"item.addTag('{safe}'); "
    for t in remove_tags:
        safe = t.replace("'", "\\'")
        tag_lines += f"item.removeTag('{safe}'); "
    js = (
        f"var item = Zotero.Items.getByLibraryAndKey({library_id}, '{item_key}'); "
        f"if (!item) {{ return 'ERROR: item {item_key} not found'; }} "
        f"{tag_lines}"
        f"await item.saveTx(); "
        f"return 'OK: tags updated for ' + item.getField('title').substring(0,60);"
    )
    return execute_js(js, wait_seconds=4)


def import_from_doi(doi: str, *, collection_key: str | None = None, tags: list[str] | None = None, library_id: int = 1) -> dict:
    """Import an item into Zotero by DOI using the built-in translator."""
    safe_doi = doi.replace("'", "\\'")
    collection_js = ""
    if collection_key:
        collection_js = (
            f"var col = Zotero.Collections.getByLibraryAndKey({library_id}, '{collection_key}'); "
            f"if (col) {{ await col.addItem(item.id); }} "
        )
    tag_js = ""
    if tags:
        for t in tags:
            safe = t.replace("'", "\\'")
            tag_js += f"item.addTag('{safe}'); "
        tag_js += "await item.saveTx(); "
    js = (
        f"var translate = new Zotero.Translate.Search(); "
        f"translate.setIdentifier({{DOI: '{safe_doi}'}}); "
        f"var translators = await translate.getTranslators(); "
        f"translate.setTranslator(translators); "
        f"var items = await translate.translate({{libraryID: {library_id}}}); "
        f"if (!items || !items.length) {{ return 'ERROR: no results for DOI {safe_doi}'; }} "
        f"var item = items[0]; "
        f"{collection_js}{tag_js}"
        f"return 'OK: imported ' + item.getField('title').substring(0,60) + ' (key: ' + item.key + ')';"
    )
    return execute_js(js, wait_seconds=15)


def import_from_pmid(pmid: str, *, collection_key: str | None = None, tags: list[str] | None = None, library_id: int = 1) -> dict:
    """Import an item into Zotero by PMID using the built-in translator."""
    safe_pmid = pmid.replace("'", "\\'")
    collection_js = ""
    if collection_key:
        collection_js = (
            f"var col = Zotero.Collections.getByLibraryAndKey({library_id}, '{collection_key}'); "
            f"if (col) {{ await col.addItem(item.id); }} "
        )
    tag_js = ""
    if tags:
        for t in tags:
            safe = t.replace("'", "\\'")
            tag_js += f"item.addTag('{safe}'); "
        tag_js += "await item.saveTx(); "
    js = (
        f"var translate = new Zotero.Translate.Search(); "
        f"translate.setIdentifier({{PMID: '{safe_pmid}'}}); "
        f"var translators = await translate.getTranslators(); "
        f"translate.setTranslator(translators); "
        f"var items = await translate.translate({{libraryID: {library_id}}}); "
        f"if (!items || !items.length) {{ return 'ERROR: no results for PMID {safe_pmid}'; }} "
        f"var item = items[0]; "
        f"{collection_js}{tag_js}"
        f"return 'OK: imported ' + item.getField('title').substring(0,60) + ' (key: ' + item.key + ')';"
    )
    return execute_js(js, wait_seconds=15)


def search_fulltext(query: str, *, limit: int = 10, library_id: int = 1) -> dict:
    """Search full-text content of PDFs in Zotero library."""
    safe_query = query.replace("'", "\\'")
    js = (
        f"var s = new Zotero.Search(); "
        f"s.libraryID = {library_id}; "
        f"s.addCondition('fulltextContent', 'contains', '{safe_query}'); "
        f"var ids = await s.search(); "
        f"var items = await Zotero.Items.getAsync(ids); "
        f"return items.slice(0, {limit}).map(i => ({{key: i.key, title: i.getField('title'), date: i.getField('date')}}));"
    )
    return execute_js(js, wait_seconds=8)


def get_annotations(item_key: str, *, library_id: int = 1) -> dict:
    """Get annotations/highlights for a Zotero item (searches its PDF attachments)."""
    js = (
        f"var item = Zotero.Items.getByLibraryAndKey({library_id}, '{item_key}'); "
        f"if (!item) {{ return 'ERROR: item {item_key} not found'; }} "
        f"var attIDs = item.getAttachments(); "
        f"var allAnnots = []; "
        f"for (var aid of attIDs) {{ "
        f"  var att = Zotero.Items.get(aid); "
        f"  if (att && att.isPDFAttachment && att.isPDFAttachment()) {{ "
        f"    try {{ var annots = att.getAnnotations(); "
        f"      allAnnots = allAnnots.concat(annots.map(a => ({{type: a.annotationType, "
        f"        text: (a.annotationText || '').substring(0, 200), "
        f"        comment: a.annotationComment || '', color: a.annotationColor || '', "
        f"        page: a.annotationPageLabel || ''}})));  "
        f"    }} catch(e) {{}} "
        f"  }} "
        f"}} "
        f"return {{count: allAnnots.length, annotations: allAnnots}};"
    )
    return execute_js(js, wait_seconds=5)


def delete_item(item_key: str, *, library_id: int = 1) -> dict:
    """Delete a Zotero item permanently."""
    js = (
        f"var item = Zotero.Items.getByLibraryAndKey({library_id}, '{item_key}'); "
        f"if (!item) {{ return 'ERROR: item {item_key} not found'; }} "
        f"var title = item.getField('title').substring(0,60); "
        f"await item.eraseTx(); "
        f"return 'DELETED: ' + title;"
    )
    return execute_js(js, wait_seconds=4)


def find_duplicates(*, limit: int = 50, library_id: int = 1) -> dict:
    """Find duplicate items in a Zotero library."""
    js = (
        f"try {{ var dup = new Zotero.Duplicates({library_id}); "
        f"await dup._findDuplicates(); "
        f"var map = dup.getSetItemsByItemID(); "
        f"var itemIDs = Object.keys(map).map(Number).filter(Boolean); "
        f"var items = itemIDs.map(id => Zotero.Items.get(id)).filter(i => i && !i.isAttachment() && !i.isNote()); "
        f"return {{count: items.length, items: items.slice(0, {limit}).map(i => ({{key: i.key, "
        f"title: i.getField('title').substring(0,80), date: i.getField('date'), "
        f"setID: map[i.id]}}))}}; "
        f"}} catch(e) {{ return {{error: e.message, count: 0, items: []}}; }}"
    )
    return execute_js(js, wait_seconds=10)


def collection_stats(collection_key: str, *, library_id: int = 1) -> dict:
    """Get statistics for a Zotero collection."""
    js = (
        f"var c = Zotero.Collections.getByLibraryAndKey({library_id}, '{collection_key}'); "
        f"if (!c) {{ return 'ERROR: collection {collection_key} not found'; }} "
        f"var ids = c.getChildItems(true); "
        f"var items = ids.map(id => Zotero.Items.get(id)).filter(i => i && !i.isAttachment() && !i.isNote()); "
        f"var total = items.length; "
        f"var withPDF = items.filter(i => i.getAttachments().some(aid => {{ "
        f"var a = Zotero.Items.get(aid); return a && a.attachmentContentType === 'application/pdf'; }})).length; "
        f"var years = {{}}; var journals = {{}}; "
        f"items.forEach(i => {{ var y = (i.getField('date') || '').substring(0,4); if (y) years[y] = (years[y]||0) + 1; "
        f"var j = i.getField('publicationTitle') || ''; if (j) journals[j] = (journals[j]||0) + 1; }}); "
        f"return {{total: total, withPDF: withPDF, noPDF: total - withPDF, byYear: years, "
        f"topJournals: Object.entries(journals).sort((a,b)=>b[1]-a[1]).slice(0,10).map(e=>({{journal:e[0],count:e[1]}}))}};"
    )
    return execute_js(js, wait_seconds=8)


def trigger_sync() -> dict:
    """Trigger a Zotero sync operation."""
    js = "await Zotero.Sync.Runner.sync(); return 'Sync completed';"
    return execute_js(js, wait_seconds=30)


def find_pdfs_in_collection(collection_key: str, *, library_id: int = 1) -> dict:
    """Trigger 'Find Available PDF' for all items missing PDFs in a collection."""
    js = (
        f"var c = Zotero.Collections.getByLibraryAndKey({library_id}, '{collection_key}'); "
        f"if (!c) {{ return 'ERROR: collection {collection_key} not found'; }} "
        "var ids = c.getChildItems(true); "
        "var items = ids.map(id => Zotero.Items.get(id)).filter(i => i && !i.isAttachment() && !i.isNote()); "
        "var noPDF = items.filter(i => { var a = i.getAttachments(); return !a.some(id => { var x = Zotero.Items.get(id); return x && x.attachmentContentType === 'application/pdf'; }); }); "
        "var r = []; "
        "for (var i of noPDF) { try { var a = await Zotero.Attachments.addAvailablePDF(i); r.push(i.getField('title').substring(0,50) + ': ' + (a ? 'FOUND' : 'not found')); } catch(e) { r.push(i.getField('title').substring(0,50) + ': ERROR'); } } "
        "return {checked: noPDF.length, found: r.filter(x=>x.includes('FOUND')).length, details: r};"
    )
    return execute_js(js, wait_seconds=120)


def search_annotations(query: str = "", *, colors: list[str] | None = None, limit: int = 20, library_id: int = 1) -> dict:
    """Search annotations across all items by keyword, color, or both."""
    # Use annotationText search if query provided, otherwise get all annotations
    if query:
        safe_q = query.replace("'", "\\'")
        search_cond = f"s.addCondition('annotationText', 'contains', '{safe_q}');"
    else:
        search_cond = "s.addCondition('itemType', 'is', 'annotation');"

    # Build color filter as JS-side post-filter
    color_filter = "true"
    if colors:
        color_list = json.dumps(colors)
        color_filter = f"{color_list}.includes(a.annotationColor)"

    js = (
        f"var s = new Zotero.Search(); s.libraryID = {library_id}; "
        f"{search_cond} "
        f"var ids = await s.search(); "
        f"var annots = await Zotero.Items.getAsync(ids); "
        f"var filtered = annots.filter(a => {color_filter}); "
        f"return filtered.slice(0, {limit}).map(a => {{ "
        f"var parent = Zotero.Items.get(a.parentItemID); "
        f"var grandparent = parent ? Zotero.Items.get(parent.parentItemID) : null; "
        f"var title = grandparent ? grandparent.getField('title').substring(0,60) : (parent ? parent.getField('title').substring(0,60) : ''); "
        f"return {{type: a.annotationType, text: (a.annotationText || '').substring(0,200), "
        f"comment: a.annotationComment || '', color: a.annotationColor || '', "
        f"page: a.annotationPageLabel || '', parentTitle: title}}; }});"
    )
    return execute_js(js, wait_seconds=8)


def remove_from_collection(item_key: str, collection_key: str, *, library_id: int = 1) -> dict:
    """Remove an item from a collection (does NOT delete the item)."""
    js = (
        f"var col = Zotero.Collections.getByLibraryAndKey({library_id}, '{collection_key}'); "
        f"if (!col) {{ return 'ERROR: collection {collection_key} not found'; }} "
        f"var item = Zotero.Items.getByLibraryAndKey({library_id}, '{item_key}'); "
        f"if (!item) {{ return 'ERROR: item {item_key} not found'; }} "
        f"await col.removeItem(item.id); "
        f"return 'OK: removed ' + item.getField('title').substring(0,50) + ' from ' + col.name;"
    )
    return execute_js(js, wait_seconds=4)


def delete_collection(collection_key: str, *, delete_items: bool = False, library_id: int = 1) -> dict:
    """Delete a collection. Items are kept unless delete_items=True."""
    js = (
        f"var col = Zotero.Collections.getByLibraryAndKey({library_id}, '{collection_key}'); "
        f"if (!col) {{ return 'ERROR: collection {collection_key} not found'; }} "
        f"var name = col.name; "
        f"{'await col.eraseTx();' if delete_items else 'await col.eraseTx({deleteItems: false});'} "
        f"return 'DELETED: collection ' + name;"
    )
    return execute_js(js, wait_seconds=4)


def update_collection(collection_key: str, *, name: str | None = None, parent_key: str | None = None, library_id: int = 1) -> dict:
    """Rename a collection or move it under a different parent."""
    set_lines = ""
    if name:
        safe_name = name.replace("'", "\\'")
        set_lines += f"col.name = '{safe_name}'; "
    if parent_key:
        set_lines += (
            f"var parent = Zotero.Collections.getByLibraryAndKey({library_id}, '{parent_key}'); "
            f"if (parent) {{ col.parentID = parent.id; }} "
        )
    if not set_lines:
        return {"ok": False, "data": None, "error": "No changes specified (use --name or --parent)"}
    js = (
        f"var col = Zotero.Collections.getByLibraryAndKey({library_id}, '{collection_key}'); "
        f"if (!col) {{ return 'ERROR: collection {collection_key} not found'; }} "
        f"{set_lines}"
        f"await col.saveTx(); "
        f"return 'OK: updated collection ' + col.name;"
    )
    return execute_js(js, wait_seconds=4)
