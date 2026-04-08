"""MCP (Model Context Protocol) server for cli-anything-zotero.

Wraps existing core functions as MCP tools for use with
Claude Desktop, Cursor, LM Studio, and other MCP-compatible clients.
"""

from __future__ import annotations

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    raise ImportError(
        "MCP server requires the 'mcp' package. "
        "Install it with: pip install 'cli-anything-zotero[mcp]'"
    )

from cli_anything.zotero.core import catalog, discovery, jsbridge, notes, rendering, semantic, analysis, metrics
from cli_anything.zotero.core import session as session_mod

# ---------------------------------------------------------------------------
# Server instance
# ---------------------------------------------------------------------------

server = FastMCP(
    "zotero",
    instructions=(
        "MCP server for managing Zotero 7/8 libraries. "
        "Provides tools for searching, browsing, importing, exporting, "
        "and managing bibliographic references via local SQLite, "
        "Connector API, and JS Bridge plugin."
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
# Tier 1 — Core Read Operations
# ===================================================================

@server.tool(description="List all libraries (user and group libraries) in Zotero.")
def list_libraries() -> list[dict]:
    return catalog.list_libraries(_get_runtime())


@server.tool(description="List all collections in the Zotero library.")
def list_collections() -> list[dict]:
    return catalog.list_collections(_get_runtime(), session=_session())


@server.tool(description="Search for collections by name.")
def find_collections(query: str, limit: int = 20) -> list[dict]:
    return catalog.find_collections(_get_runtime(), query, limit=limit, session=_session())


@server.tool(description="Get the full collection hierarchy as a tree.")
def collection_tree() -> list[dict]:
    return catalog.collection_tree(_get_runtime(), session=_session())


@server.tool(description="Get details of a specific collection by key or ID.")
def get_collection(ref: str) -> dict:
    return catalog.get_collection(_get_runtime(), ref, session=_session())


@server.tool(description="List all items in a specific collection.")
def collection_items(ref: str) -> list[dict]:
    return catalog.collection_items(_get_runtime(), ref, session=_session())


@server.tool(description="List items in the library, optionally limited.")
def list_items(limit: int = 50) -> list[dict]:
    return catalog.list_items(_get_runtime(), session=_session(), limit=limit)


@server.tool(description="Search for items by keyword across title, author, abstract, and tags.")
def find_items(
    query: str,
    collection_ref: str | None = None,
    limit: int = 20,
    exact_title: bool = False,
) -> list[dict]:
    return catalog.find_items(
        _get_runtime(), query,
        collection_ref=collection_ref,
        limit=limit,
        exact_title=exact_title,
        session=_session(),
    )


@server.tool(description="Get full metadata for a specific item by key or ID.")
def get_item(ref: str) -> dict:
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


@server.tool(description="List all tags in the library.")
def list_tags() -> list[dict]:
    return catalog.list_tags(_get_runtime(), session=_session())


@server.tool(description="List items that have a specific tag.")
def tag_items(tag_ref: str) -> list[dict]:
    return catalog.tag_items(_get_runtime(), tag_ref, session=_session())


@server.tool(description="List saved searches in the library.")
def list_searches() -> list[dict]:
    return catalog.list_searches(_get_runtime(), session=_session())


@server.tool(description="List available citation styles.")
def list_styles() -> list[dict]:
    return catalog.list_styles(_get_runtime())


# ===================================================================
# Tier 2 — Export and Rendering
# ===================================================================

@server.tool(description="Export an item in a specific format (bibtex, csljson, ris, etc.).")
def export_item(ref: str, fmt: str = "bibtex") -> dict:
    return rendering.export_item(_get_runtime(), ref, fmt, session=_session())


@server.tool(description="Get a formatted citation for an item (e.g. APA, Nature, Vancouver).")
def citation_item(ref: str, style: str = "apa") -> dict:
    return rendering.citation_item(_get_runtime(), ref, style=style, session=_session())


@server.tool(description="Get a formatted bibliography entry for an item.")
def bibliography_item(ref: str, style: str = "apa") -> dict:
    return rendering.bibliography_item(_get_runtime(), ref, style=style, session=_session())


@server.tool(description="Get the full content of a specific note by note key.")
def get_note(ref: str) -> dict:
    return notes.get_note(_get_runtime(), ref, session=_session())


@server.tool(description="Get rich LLM-ready context for an item (metadata + abstract + notes).")
def item_context(ref: str, include_notes: bool = True) -> dict:
    return analysis.build_item_context(_get_runtime(), ref, include_notes=include_notes, session=_session())


# ===================================================================
# Tier 3 — JS Bridge Read Operations (requires Zotero running)
# ===================================================================

@server.tool(description="Search full-text content inside PDFs in the Zotero library. Requires Zotero running with JS Bridge plugin.")
def search_fulltext(query: str, limit: int = 10) -> dict:
    return _unwrap_js(_get_bridge().search_fulltext(query, limit=limit, library_id=_library_id()))


@server.tool(description="Get PDF annotations (highlights, comments) for an item.")
def get_annotations(item_key: str) -> dict:
    return _unwrap_js(_get_bridge().get_annotations(item_key, library_id=_library_id()))


@server.tool(description="Search across all PDF annotations by keyword or color.")
def search_annotations(query: str = "", limit: int = 20) -> dict:
    return _unwrap_js(_get_bridge().search_annotations(query, limit=limit, library_id=_library_id()))


@server.tool(description="Get statistics for a collection (item count, type breakdown, etc.).")
def collection_stats(collection_key: str) -> dict:
    return _unwrap_js(_get_bridge().collection_stats(collection_key, library_id=_library_id()))


@server.tool(description="Find duplicate items in the library.")
def find_duplicates(limit: int = 50) -> dict:
    return _unwrap_js(_get_bridge().find_duplicates(limit=limit, library_id=_library_id()))


# ===================================================================
# Tier 4 — Write Operations
# ===================================================================

@server.tool(description="Import an item by DOI. Optionally add to a collection and apply tags.")
def import_from_doi(doi: str, collection_key: str | None = None, tags: list[str] | None = None) -> dict:
    return _unwrap_js(_get_bridge().import_from_doi(doi, collection_key=collection_key, tags=tags, library_id=_library_id()))


@server.tool(description="Import an item by PubMed ID. Optionally add to a collection and apply tags.")
def import_from_pmid(pmid: str, collection_key: str | None = None, tags: list[str] | None = None) -> dict:
    return _unwrap_js(_get_bridge().import_from_pmid(pmid, collection_key=collection_key, tags=tags, library_id=_library_id()))


@server.tool(description="Add or remove tags on an item.")
def manage_tags(item_key: str, add_tags: list[str] | None = None, remove_tags: list[str] | None = None) -> dict:
    return _unwrap_js(_get_bridge().manage_tags(item_key, add_tags=add_tags or [], remove_tags=remove_tags or [], library_id=_library_id()))


@server.tool(description="Update metadata fields of an item (e.g. title, date, abstract). Pass fields as {\"title\": \"New Title\", \"date\": \"2024\"}.")
def update_item_fields(item_key: str, fields: dict[str, str] | None = None) -> dict:
    return _unwrap_js(_get_bridge().update_item_fields(item_key, fields or {}, library_id=_library_id()))


@server.tool(description="Add a note to an item.")
def add_note(item_ref: str, text: str, fmt: str = "text") -> dict:
    return notes.add_note(_get_runtime(), item_ref, text=text, fmt=fmt, session=_session())


@server.tool(description="Automatically find and download a PDF for an item from online sources. May take 10-30 seconds.")
def find_pdf(item_key: str, timeout: int = 30) -> dict:
    return _unwrap_js(_get_bridge().find_pdf(item_key, timeout=timeout, library_id=_library_id()))


@server.tool(description="Attach a local PDF file to an item.")
def attach_pdf(item_key: str, pdf_path: str) -> dict:
    return _unwrap_js(_get_bridge().attach_pdf(item_key, pdf_path, library_id=_library_id()))


@server.tool(description="Add an item to a collection.")
def add_to_collection(item_key: str, collection_key: str) -> dict:
    return _unwrap_js(_get_bridge().add_to_collection(item_key, collection_key, library_id=_library_id()))


@server.tool(description="Remove an item from a collection (does not delete the item).")
def remove_from_collection(item_key: str, collection_key: str) -> dict:
    return _unwrap_js(_get_bridge().remove_from_collection(item_key, collection_key, library_id=_library_id()))


@server.tool(description="Trigger Zotero sync to push/pull changes with the server.")
def trigger_sync() -> dict:
    return _unwrap_js(_get_bridge().trigger_sync())


# ===================================================================
# Tier 5 — Advanced
# ===================================================================

@server.tool(description="Semantic vector search across items. Requires a pre-built embedding index.")
def semantic_search(query: str, top_k: int = 10, min_score: float = 0.3) -> dict:
    return semantic.semantic_search(query, top_k=top_k, min_score=min_score)


@server.tool(description="Find items similar to a given item using vector embeddings.")
def find_similar(item_key: str, top_k: int = 5, min_score: float = 0.5) -> dict:
    return semantic.find_similar(item_key, top_k=top_k, min_score=min_score)


@server.tool(description="Get NIH iCite citation metrics for a PubMed ID.")
def get_citation_metrics(pmid: str) -> dict:
    return metrics.get_metrics(pmid)


@server.tool(description="Execute arbitrary JavaScript code inside Zotero via JS Bridge. Advanced escape hatch — you can run any Zotero API call.")
def execute_js(code: str) -> dict:
    return _unwrap_js(_get_bridge().execute_js(code))


# ===================================================================
# Tier 6 — Collection & Item Management (write, destructive)
# ===================================================================

@server.tool(description="Create a new collection. Optionally nest under a parent collection.")
def create_collection(name: str, parent_key: str | None = None) -> dict:
    return _unwrap_js(_get_bridge().create_collection(name, parent_key=parent_key, library_id=_library_id()))


@server.tool(description="Delete a collection. Optionally delete all items inside it.")
def delete_collection(collection_key: str, delete_items: bool = False) -> dict:
    return _unwrap_js(_get_bridge().delete_collection(collection_key, delete_items=delete_items, library_id=_library_id()))


@server.tool(description="Rename a collection or move it under a different parent.")
def update_collection(collection_key: str, name: str | None = None, parent_key: str | None = None) -> dict:
    return _unwrap_js(_get_bridge().update_collection(collection_key, name=name, parent_key=parent_key, library_id=_library_id()))


@server.tool(description="Delete an item (move to trash). This is destructive.")
def delete_item(item_key: str) -> dict:
    return _unwrap_js(_get_bridge().delete_item(item_key, library_id=_library_id()))


@server.tool(description="Find PDFs for all items in a collection that are missing them. May take a while.")
def find_pdfs_in_collection(collection_key: str) -> dict:
    return _unwrap_js(_get_bridge().find_pdfs_in_collection(collection_key, library_id=_library_id()))


@server.tool(description="Build the semantic vector index for all items. Required before semantic_search/find_similar. May take several minutes for large libraries.")
def build_index() -> dict:
    runtime = _get_runtime()
    return semantic.build_index(str(runtime.environment.sqlite_path))


@server.tool(description="Analyze an item using AI (requires OPENAI_API_KEY env var). Returns structured summary.")
def analyze_item(ref: str, question: str = "Summarize the key findings.", model: str = "gpt-4o-mini") -> dict:
    return analysis.analyze_item(
        _get_runtime(), ref,
        question=question,
        model=model,
        session=_session(),
    )


@server.tool(description="Import items from a local RIS, BibTeX, or CSL-JSON file into the library.")
def import_file(path: str, collection_ref: str | None = None, tags: list[str] | None = None) -> dict:
    from cli_anything.zotero.core import imports
    return imports.import_file(
        _get_runtime(), path,
        collection_ref=collection_ref,
        tags=list(tags) if tags else [],
        session=_session(),
    )


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
