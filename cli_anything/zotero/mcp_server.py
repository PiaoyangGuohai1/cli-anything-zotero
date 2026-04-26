"""MCP (Model Context Protocol) server for cli-anything-zotero.

Wraps existing core functions as MCP tools for use with
Claude Desktop, Cursor, LM Studio, and other MCP-compatible clients.

Tool naming convention: ``group_action`` to match the CLI structure.
For example, CLI ``item find`` → MCP tool ``item_find``.
"""

from __future__ import annotations

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    raise ImportError(
        "MCP server requires the 'mcp' package. "
        "Install it with: pip install 'cli-anything-zotero[mcp]'"
    )

from cli_anything.zotero.core import catalog, discovery, docx as docx_tools, docx_static, jsbridge, notes, rendering, semantic, analysis, metrics
from cli_anything.zotero.core import session as session_mod

# ---------------------------------------------------------------------------
# Server instance
# ---------------------------------------------------------------------------

server = FastMCP(
    "zotero",
    instructions=(
        "MCP server for managing Zotero 7/8/9 libraries. "
        "Provides tools for searching, browsing, importing, exporting, "
        "and managing bibliographic references via local SQLite, "
        "Connector API, and JS Bridge plugin. "
        "Tool names follow the pattern group_action (e.g. item_find, collection_list)."
    ),
)

# ---------------------------------------------------------------------------
# Lazy runtime
# ---------------------------------------------------------------------------

_config: dict = {}
_runtime_cache: discovery.RuntimeContext | None = None


def _get_runtime() -> discovery.RuntimeContext:
    global _runtime_cache
    if _runtime_cache is None:
        _runtime_cache = discovery.build_runtime_context(
            backend=_config.get("backend", "auto"),
            data_dir=_config.get("data_dir"),
            profile_dir=_config.get("profile_dir"),
            executable=_config.get("executable"),
        )
    return _runtime_cache


_bridge_cache: jsbridge.JSBridgeClient | None = None


def _get_bridge() -> jsbridge.JSBridgeClient:
    global _bridge_cache
    if _bridge_cache is None:
        _bridge_cache = jsbridge.JSBridgeClient(port=_get_runtime().environment.port)
    return _bridge_cache


def _library_id() -> int:
    """Resolve library ID from session state, defaulting to 1 (user library)."""
    session = _session()
    return int(session.get("current_library", 1))


def _session() -> dict:
    return session_mod.load_session_state()


def _unwrap_js(result: dict) -> dict:
    """Unwrap JS Bridge result envelope, raising on error."""
    if not result.get("ok"):
        raise ValueError(result.get("error", "JS Bridge operation failed"))
    return result.get("data", result)


# ===================================================================
# library — Library operations
# ===================================================================

@server.tool(description="List all libraries (user and group libraries) in Zotero.")
def library_list() -> list[dict]:
    return catalog.list_libraries(_get_runtime())


# ===================================================================
# collection — Collection operations (matches CLI `collection *`)
# ===================================================================

@server.tool(description="List all collections in the Zotero library.")
def collection_list() -> list[dict]:
    return catalog.list_collections(_get_runtime(), session=_session())


@server.tool(description="Search for collections by name.")
def collection_find(query: str, limit: int = 20) -> list[dict]:
    return catalog.find_collections(_get_runtime(), query, limit=limit, session=_session())


@server.tool(description="Get the full collection hierarchy as a tree.")
def collection_tree() -> list[dict]:
    return catalog.collection_tree(_get_runtime(), session=_session())


@server.tool(description="Get details of a specific collection by key or ID.")
def collection_get(ref: str) -> dict:
    return catalog.get_collection(_get_runtime(), ref, session=_session())


@server.tool(description="List all items in a specific collection.")
def collection_items(ref: str) -> list[dict]:
    return catalog.collection_items(_get_runtime(), ref, session=_session())


@server.tool(description="Get statistics for a collection (item count, type breakdown, etc.).")
def collection_stats(collection_key: str) -> dict:
    return _unwrap_js(_get_bridge().collection_stats(collection_key, library_id=_library_id()))


@server.tool(description="Create a new collection. Optionally nest under a parent collection.")
def collection_create(name: str, parent_key: str | None = None) -> dict:
    return _unwrap_js(_get_bridge().create_collection(name, parent_key=parent_key, library_id=_library_id()))


@server.tool(description="Delete a collection. Optionally delete all items inside it.")
def collection_delete(collection_key: str, delete_items: bool = False) -> dict:
    return _unwrap_js(_get_bridge().delete_collection(collection_key, delete_items=delete_items, library_id=_library_id()))


@server.tool(description="Rename a collection or move it under a different parent.")
def collection_rename(collection_key: str, name: str | None = None, parent_key: str | None = None) -> dict:
    return _unwrap_js(_get_bridge().update_collection(collection_key, name=name, parent_key=parent_key, library_id=_library_id()))


@server.tool(description="Remove an item from a collection (does not delete the item).")
def collection_remove_item(collection_key: str, item_key: str) -> dict:
    return _unwrap_js(_get_bridge().remove_from_collection(item_key, collection_key, library_id=_library_id()))


@server.tool(description="Find PDFs for all items in a collection that are missing them. May take a while.")
def collection_find_pdfs(collection_key: str) -> dict:
    return _unwrap_js(_get_bridge().find_pdfs_in_collection(collection_key, library_id=_library_id()))


# ===================================================================
# item — Item operations (matches CLI `item *`)
# ===================================================================

@server.tool(description="List items in the library, optionally limited.")
def item_list(limit: int = 50) -> list[dict]:
    return catalog.list_items(_get_runtime(), session=_session(), limit=limit)


@server.tool(description="Search for items by keyword with a Zotero quick-search scope.")
def item_find(
    query: str,
    collection_ref: str | None = None,
    limit: int = 20,
    exact_title: bool = False,
    search_scope: str = "titleCreatorYear",
) -> list[dict]:
    return catalog.find_items(
        _get_runtime(), query,
        collection_ref=collection_ref,
        limit=limit,
        exact_title=exact_title,
        search_scope=search_scope,
        session=_session(),
    )


@server.tool(description="Get full metadata for a specific item by key or ID.")
def item_get(ref: str) -> dict:
    return catalog.get_item(_get_runtime(), ref, session=_session())


@server.tool(description="Get child items (notes, attachments) of an item.")
def item_children(ref: str) -> list[dict]:
    return catalog.item_children(_get_runtime(), ref, session=_session())


@server.tool(description="Get all notes attached to an item.")
def item_notes(ref: str) -> list[dict]:
    return catalog.item_notes(_get_runtime(), ref, session=_session())


@server.tool(description="Get all attachments of an item.")
def item_attachments(ref: str) -> list[dict]:
    return catalog.item_attachments(_get_runtime(), ref, session=_session())


@server.tool(description="Get the main file (PDF) path for an item.")
def item_file(ref: str) -> dict:
    return catalog.item_file(_get_runtime(), ref, session=_session())


@server.tool(description="Get rich LLM-ready context for an item (metadata + abstract + notes).")
def item_context(ref: str, include_notes: bool = True) -> dict:
    return analysis.build_item_context(_get_runtime(), ref, include_notes=include_notes, session=_session())


@server.tool(description="Export an item in a specific format (bibtex, csljson, ris, etc.).")
def item_export(ref: str, fmt: str = "bibtex") -> dict:
    return rendering.export_item(_get_runtime(), ref, fmt, session=_session())


@server.tool(description="Get a formatted citation for an item (e.g. APA, Nature, Vancouver).")
def item_citation(ref: str, style: str = "apa") -> dict:
    return rendering.citation_item(_get_runtime(), ref, style=style, session=_session())


@server.tool(description="Get a formatted bibliography entry for an item.")
def item_bibliography(ref: str, style: str = "apa") -> dict:
    return rendering.bibliography_item(_get_runtime(), ref, style=style, session=_session())


@server.tool(description="Inspect a DOCX for Zotero, EndNote, CSL/Mendeley-like fields and static citation text.")
def docx_inspect_citations(path: str, sample_limit: int = 10) -> dict:
    return docx_tools.inspect_citations(path, sample_limit=sample_limit)


@server.tool(description="Inspect a DOCX for Zotero-bound AI citation placeholders like {{zotero:ITEMKEY}}.")
def docx_inspect_placeholders(path: str, sample_limit: int = 10) -> dict:
    return docx_tools.inspect_placeholders(path, sample_limit=sample_limit)


@server.tool(description="Validate DOCX Zotero placeholders against real local Zotero items.")
def docx_validate_placeholders(path: str, sample_limit: int = 10) -> dict:
    return docx_tools.validate_placeholders(_get_runtime(), path, sample_limit=sample_limit, session=_session())


@server.tool(description="Convert DOCX Zotero placeholders into static citation text and a static bibliography.")
def docx_render_citations(path: str, output: str, style: str = "apa", locale: str = "en-US", bibliography: str = "auto", overwrite: bool = False) -> dict:
    return docx_static.render_static_citations(
        _get_runtime(),
        path,
        output,
        style=style,
        locale=locale,
        bibliography=bibliography,
        session=_session(),
        overwrite=overwrite,
    )


@server.tool(description="Add or remove tags on an item.")
def item_tag(item_key: str, add_tags: list[str] | None = None, remove_tags: list[str] | None = None) -> dict:
    return _unwrap_js(_get_bridge().manage_tags(item_key, add_tags=add_tags or [], remove_tags=remove_tags or [], library_id=_library_id()))


@server.tool(description="Update metadata fields of an item (e.g. title, date, abstract). Pass fields as {\"title\": \"New Title\", \"date\": \"2024\"}.")
def item_update(item_key: str, fields: dict[str, str] | None = None) -> dict:
    return _unwrap_js(_get_bridge().update_item_fields(item_key, fields or {}, library_id=_library_id()))


@server.tool(description="Delete an item (move to trash). This is destructive.")
def item_delete(item_key: str) -> dict:
    return _unwrap_js(_get_bridge().delete_item(item_key, library_id=_library_id()))


@server.tool(description="Automatically find and download a PDF for an item from online sources. May take 10-30 seconds.")
def item_find_pdf(item_key: str, timeout: int = 30) -> dict:
    return _unwrap_js(_get_bridge().find_pdf(item_key, timeout=timeout, library_id=_library_id()))


@server.tool(description="Attach a local PDF file to an item.")
def item_attach(item_key: str, pdf_path: str) -> dict:
    return _unwrap_js(_get_bridge().attach_pdf(item_key, pdf_path, library_id=_library_id()))


@server.tool(description="Add an item to a collection.")
def item_add_to_collection(item_key: str, collection_key: str) -> dict:
    return _unwrap_js(_get_bridge().add_to_collection(item_key, collection_key, library_id=_library_id()))


@server.tool(description="Move an item to a target collection, removing from source collection(s). Specify from_collections OR set all_other_collections=True. Returns structured result with per-step outcomes.")
def item_move_to_collection(item_key: str, collection_key: str, from_collections: list[str] | None = None, all_other_collections: bool = False) -> dict:
    if not from_collections and not all_other_collections:
        raise ValueError("Provide from_collections OR set all_other_collections=True")
    if from_collections and all_other_collections:
        raise ValueError("Provide from_collections OR all_other_collections, not both")

    lib_id = _library_id()
    steps: list[dict] = []

    # Step 1: Add to target
    add_result = _unwrap_js(_get_bridge().add_to_collection(item_key, collection_key, library_id=lib_id))
    steps.append({"action": "add_to_collection", "collection": collection_key, "ok": True})

    # Determine source collections to remove from
    if all_other_collections:
        item = catalog.get_item(_get_runtime(), item_key, session=_session())
        source_keys = [k for k in item.get("collections", []) if k != collection_key]
    else:
        source_keys = list(from_collections or [])

    # Step 2: Remove from sources (best-effort, report each)
    for src_key in source_keys:
        try:
            _unwrap_js(_get_bridge().remove_from_collection(item_key, src_key, library_id=lib_id))
            steps.append({"action": "remove_from_collection", "collection": src_key, "ok": True})
        except Exception as exc:
            steps.append({"action": "remove_from_collection", "collection": src_key, "ok": False, "error": str(exc)})

    all_ok = all(s["ok"] for s in steps)
    return {"action": "item_move_to_collection", "ok": all_ok, "steps": steps}


@server.tool(description="Get PDF annotations (highlights, comments) for an item.")
def item_annotations(item_key: str) -> dict:
    return _unwrap_js(_get_bridge().get_annotations(item_key, library_id=_library_id()))


@server.tool(description="Search across all PDF annotations by keyword or color.")
def item_search_annotations(query: str = "", limit: int = 20) -> dict:
    return _unwrap_js(_get_bridge().search_annotations(query, limit=limit, library_id=_library_id()))


@server.tool(description="Search full-text content inside PDFs in the Zotero library. Requires Zotero running with JS Bridge plugin.")
def item_search_fulltext(query: str, limit: int = 10) -> dict:
    return _unwrap_js(_get_bridge().search_fulltext(query, limit=limit, library_id=_library_id()))


@server.tool(description="Semantic vector search across items. Requires a pre-built embedding index.")
def item_semantic_search(query: str, top_k: int = 10, min_score: float = 0.3) -> dict:
    return semantic.semantic_search(query, top_k=top_k, min_score=min_score)


@server.tool(description="Find items similar to a given item using vector embeddings.")
def item_similar(item_key: str, top_k: int = 5, min_score: float = 0.5) -> dict:
    return semantic.find_similar(item_key, top_k=top_k, min_score=min_score)


@server.tool(description="Build the semantic vector index for all items. Required before item_semantic_search/item_similar. May take several minutes for large libraries.")
def item_build_index() -> dict:
    runtime = _get_runtime()
    return semantic.build_index(str(runtime.environment.sqlite_path))


@server.tool(description="Find duplicate items in the library.")
def item_duplicates(limit: int = 50) -> dict:
    return _unwrap_js(_get_bridge().find_duplicates(limit=limit, library_id=_library_id()))


@server.tool(description="Fetch NIH iCite citation metrics for an item (by PMID).")
def item_metrics(pmid: str) -> dict:
    return metrics.get_metrics(pmid)


@server.tool(description="Analyze an item using AI (requires OPENAI_API_KEY env var). Returns structured summary.")
def item_analyze(ref: str, question: str = "Summarize the key findings.", model: str = "gpt-4o-mini") -> dict:
    return analysis.analyze_item(
        _get_runtime(), ref,
        question=question,
        model=model,
        session=_session(),
    )


# ===================================================================
# note — Note operations (matches CLI `note *`)
# ===================================================================

@server.tool(description="Get the full content of a specific note by note key.")
def note_get(ref: str) -> dict:
    return notes.get_note(_get_runtime(), ref, session=_session())


@server.tool(description="Add a note to an item.")
def note_add(item_ref: str, text: str, fmt: str = "text") -> dict:
    return notes.add_note(_get_runtime(), item_ref, text=text, fmt=fmt, session=_session())


# ===================================================================
# tag — Tag operations (matches CLI `tag *`)
# ===================================================================

@server.tool(description="List all tags in the library.")
def tag_list() -> list[dict]:
    return catalog.list_tags(_get_runtime(), session=_session())


@server.tool(description="List items that have a specific tag.")
def tag_items(tag_ref: str) -> list[dict]:
    return catalog.tag_items(_get_runtime(), tag_ref, session=_session())


# ===================================================================
# search — Saved search operations (matches CLI `search *`)
# ===================================================================

@server.tool(description="List saved searches in the library.")
def search_list() -> list[dict]:
    return catalog.list_searches(_get_runtime(), session=_session())


@server.tool(description="Get details of a specific saved search by key or ID.")
def search_get(ref: str) -> dict:
    return catalog.get_search(_get_runtime(), ref, session=_session())


@server.tool(description="Get items matching a saved search.")
def search_items(ref: str) -> list:
    return catalog.search_items(_get_runtime(), ref, session=_session())


# ===================================================================
# style — Citation style operations (matches CLI `style *`)
# ===================================================================

@server.tool(description="List available citation styles.")
def style_list() -> list[dict]:
    return catalog.list_styles(_get_runtime())


# ===================================================================
# import — Import operations (matches CLI `import *`)
# ===================================================================

@server.tool(description="Import an item by DOI. Optionally add to a collection and apply tags.")
def import_doi(doi: str, collection_key: str | None = None, tags: list[str] | None = None) -> dict:
    return _unwrap_js(_get_bridge().import_from_doi(doi, collection_key=collection_key, tags=tags, library_id=_library_id()))


@server.tool(description="Import an item by PubMed ID. Optionally add to a collection and apply tags.")
def import_pmid(pmid: str, collection_key: str | None = None, tags: list[str] | None = None) -> dict:
    return _unwrap_js(_get_bridge().import_from_pmid(pmid, collection_key=collection_key, tags=tags, library_id=_library_id()))


@server.tool(description="Import items from a local RIS, BibTeX, or CSL-JSON file into the library.")
def import_file(path: str, collection_ref: str | None = None, tags: list[str] | None = None) -> dict:
    from cli_anything.zotero.core import imports
    return imports.import_file(
        _get_runtime(), path,
        collection_ref=collection_ref,
        tags=list(tags) if tags else [],
        session=_session(),
    )


# ===================================================================
# Top-level operations (matches CLI `sync`, `js`)
# ===================================================================

@server.tool(description="Trigger Zotero sync to push/pull changes with the server.")
def sync() -> dict:
    return _unwrap_js(_get_bridge().trigger_sync())


@server.tool(description="Execute arbitrary JavaScript code inside Zotero via JS Bridge. Advanced escape hatch — you can run any Zotero API call.")
def js(code: str) -> dict:
    return _unwrap_js(_get_bridge().execute_js(code))


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_server(
    *,
    backend: str = "auto",
    data_dir: str | None = None,
    profile_dir: str | None = None,
    executable: str | None = None,
) -> FastMCP:
    """Create and return the MCP server, configured with the given options."""
    global _runtime_cache, _bridge_cache
    _runtime_cache = None  # Clear cached runtime so new config takes effect
    _bridge_cache = None  # Clear cached bridge client
    _config.update(
        backend=backend,
        data_dir=data_dir,
        profile_dir=profile_dir,
        executable=executable,
    )
    return server
