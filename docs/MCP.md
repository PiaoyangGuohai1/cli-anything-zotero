# MCP Server Reference

cli-anything-zotero provides an [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server with **49 tools**, giving AI clients full access to your local Zotero library.

## Quick Start

```bash
# Install with MCP support
pip install 'cli-anything-zotero[mcp]'

# Start the server
zotero-cli mcp serve
```

## Client Configuration

### Claude Desktop / Claude Code

```json
{
  "mcpServers": {
    "zotero": {
      "command": "zotero-cli",
      "args": ["mcp", "serve"]
    }
  }
}
```

### Cursor

Add to Cursor Settings → MCP Servers:

```json
{
  "zotero": {
    "command": "zotero-cli",
    "args": ["mcp", "serve"]
  }
}
```

### LM Studio / Other MCP Clients

Same format — provide `zotero-cli` as the command with `["mcp", "serve"]` as args.

> **Note:** The server uses **stdio** transport by default.

---

## Tool Reference

### Library & Collections (11 tools)

| Tool | Description |
|------|-------------|
| `list_libraries` | List all libraries (user and group) |
| `list_collections` | List all collections |
| `find_collections` | Search collections by name |
| `collection_tree` | Get the full collection hierarchy as a tree |
| `get_collection` | Get details of a specific collection |
| `collection_items` | List all items in a collection |
| `collection_stats` | Get statistics for a collection (item count, type breakdown) |
| `create_collection` | Create a new collection, optionally nested under a parent |
| `delete_collection` | Delete a collection, optionally with all items inside |
| `update_collection` | Rename a collection or move it under a different parent |
| `find_duplicates` | Find duplicate items in the library |

### Item Search & Browse (6 tools)

| Tool | Description |
|------|-------------|
| `find_items` | Search by keyword across title, author, abstract, and tags |
| `list_items` | List items in the library, optionally limited |
| `get_item` | Get full metadata for a specific item |
| `item_children` | Get child items (notes, attachments) |
| `item_context` | Get rich LLM-ready context (metadata + abstract + notes) |
| `search_fulltext` | Search inside PDF content via JS Bridge |

### Tags (3 tools)

| Tool | Description |
|------|-------------|
| `list_tags` | List all tags in the library |
| `tag_items` | List items that have a specific tag |
| `manage_tags` | Add or remove tags on an item |

### Export & Citation (5 tools)

| Tool | Description |
|------|-------------|
| `export_item` | Export in a specific format (BibTeX, CSL-JSON, RIS, etc.) |
| `citation_item` | Get a formatted citation (APA, Nature, Vancouver, etc.) |
| `bibliography_item` | Get a formatted bibliography entry |
| `list_styles` | List available citation styles |
| `list_searches` | List saved searches |

### Notes & Attachments (5 tools)

| Tool | Description |
|------|-------------|
| `item_notes` | Get all notes attached to an item |
| `get_note` | Get the full content of a specific note |
| `add_note` | Add a note to an item |
| `item_attachments` | Get all attachments of an item |
| `item_file` | Get the main file (PDF) path |

### Import (3 tools)

| Tool | Description |
|------|-------------|
| `import_from_doi` | Import by DOI, optionally into a collection with tags |
| `import_from_pmid` | Import by PubMed ID, optionally into a collection with tags |
| `import_file` | Import from a local RIS, BibTeX, or CSL-JSON file |

### PDF Management (4 tools)

| Tool | Description |
|------|-------------|
| `attach_pdf` | Attach a local PDF file to an item |
| `find_pdf` | Auto-find and download a PDF from online sources (10-30s) |
| `find_pdfs_in_collection` | Find PDFs for all items in a collection missing them |
| `get_annotations` | Get PDF annotations (highlights, comments) |

### Write & Edit (5 tools)

| Tool | Description |
|------|-------------|
| `update_item_fields` | Update item metadata fields |
| `add_to_collection` | Add an item to a collection |
| `remove_from_collection` | Remove an item from a collection (does not delete it) |
| `delete_item` | Move an item to trash |
| `trigger_sync` | Trigger Zotero sync to push/pull changes |

### Advanced (4 tools)

| Tool | Description |
|------|-------------|
| `search_annotations` | Search across all PDF annotations by keyword or color |
| `get_citation_metrics` | Get NIH iCite citation metrics for a PubMed ID |
| `analyze_item` | AI analysis of an item (requires `OPENAI_API_KEY`) |
| `execute_js` | Execute arbitrary JavaScript inside Zotero via JS Bridge |

### Semantic Search (3 tools)

Requires a pre-built embedding index. See [Optional Features](../README.md#optional-features) in the README.

| Tool | Description |
|------|-------------|
| `build_index` | Build the semantic vector index for all items (one-time) |
| `semantic_search` | Vector search across items |
| `find_similar` | Find items similar to a given item |

---

## CLI vs MCP: When to Use Which

| | CLI | MCP |
|---|---|---|
| **Use when** | Terminal workflows, shell scripts, REPL exploration | AI clients (Claude Desktop, Cursor, LM Studio) |
| **Interface** | `cli-anything-zotero <command>` | Tools called by the AI client |
| **Output** | Human-readable or `--json` | Structured for LLM consumption |
| **Capabilities** | Same 49 operations | Same 49 operations |

Both use the same underlying core — the MCP server wraps the same functions the CLI calls.
