# MCP Server Reference

cli-anything-zotero provides an [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server with **56 tools**, giving AI clients full access to your local Zotero library.

## Quick Start

```bash
# Install with MCP support
pip install 'cli-anything-zotero[mcp]'

# Start the server
zotero-mcp
```

## Client Configuration

### Claude Desktop / Claude Code

```json
{
  "mcpServers": {
    "zotero": {
      "command": "zotero-mcp"
    }
  }
}
```

### Cursor

Add to Cursor Settings → MCP Servers:

```json
{
  "zotero": {
    "command": "zotero-mcp"
  }
}
```

### LM Studio / Other MCP Clients

Same format — provide `zotero-mcp` as the command.

> **Note:** The server uses **stdio** transport by default.

---

## Tool Reference

Tool names follow the `group_action` pattern, matching the CLI structure (e.g. CLI `item find` → MCP `item_find`).

### library (1 tool)

| Tool | CLI equivalent | Description |
|------|----------------|-------------|
| `library_list` | `library list` | List all libraries (user and group) |

### collection (11 tools)

| Tool | CLI equivalent | Description |
|------|----------------|-------------|
| `collection_list` | `collection list` | List all collections |
| `collection_find` | `collection find` | Search collections by name |
| `collection_tree` | `collection tree` | Get the full collection hierarchy as a tree |
| `collection_get` | `collection get` | Get details of a specific collection |
| `collection_items` | `collection items` | List all items in a collection |
| `collection_stats` | `collection stats` | Get statistics for a collection |
| `collection_create` | `collection create` | Create a new collection |
| `collection_delete` | `collection delete` | Delete a collection |
| `collection_rename` | `collection rename` | Rename or move a collection |
| `collection_remove_item` | `collection remove-item` | Remove an item from a collection |
| `collection_find_pdfs` | `collection find-pdfs` | Find PDFs for all items missing them |

### item (27 tools)

| Tool | CLI equivalent | Description |
|------|----------------|-------------|
| `item_list` | `item list` | List items in the library |
| `item_find` | `item find` | Search by keyword across title, author, abstract, tags |
| `item_get` | `item get` | Get full metadata for a specific item |
| `item_children` | `item children` | Get child items (notes, attachments) |
| `item_notes` | `item notes` | Get all notes attached to an item |
| `item_attachments` | `item attachments` | Get all attachments of an item |
| `item_file` | `item file` | Get the main file (PDF) path |
| `item_context` | `item context` | Get rich LLM-ready context |
| `item_export` | `item export` | Export in BibTeX, CSL-JSON, RIS, etc. |
| `item_citation` | `item citation` | Get a static formatted citation preview |
| `item_bibliography` | `item bibliography` | Get a static formatted bibliography preview |
| `item_tag` | `item tag` | Add or remove tags on an item |
| `item_update` | `item update` | Update metadata fields |
| `item_delete` | `item delete` | Move an item to trash |
| `item_find_pdf` | `item find-pdf` | Auto-find and download a PDF |
| `item_attach` | `item attach` | Attach a local PDF file |
| `item_add_to_collection` | `item add-to-collection` | Add an item to a collection |
| `item_move_to_collection` | `item move-to-collection` | Move an item to a collection |
| `item_annotations` | `item annotations` | Get PDF annotations (highlights, comments) |
| `item_search_annotations` | `item search-annotations` | Search across all annotations |
| `item_search_fulltext` | `item search-fulltext` | Search inside PDF content |
| `item_semantic_search` | `item semantic-search` | Semantic vector search |
| `item_similar` | `item similar` | Find similar items via embeddings |
| `item_build_index` | `item build-index` | Build the semantic vector index |
| `item_duplicates` | `item duplicates` | Find duplicate items |
| `item_metrics` | `item metrics` | Get NIH iCite citation metrics |
| `item_analyze` | `item analyze` | AI analysis (requires `OPENAI_API_KEY`) |

### docx (4 tools)

| Tool | CLI equivalent | Description |
|------|----------------|-------------|
| `docx_inspect_citations` | `docx inspect-citations` | Detect Zotero, EndNote, CSL/Mendeley-like fields and static citation text |
| `docx_inspect_placeholders` | `docx inspect-placeholders` | Detect Zotero placeholders such as `{{zotero:ITEMKEY}}` |
| `docx_validate_placeholders` | `docx validate-placeholders` | Verify placeholder keys resolve to real local Zotero items |
| `docx_render_citations` | `docx render-citations` | Convert placeholders into static citation text and a static bibliography |

### note (2 tools)

| Tool | CLI equivalent | Description |
|------|----------------|-------------|
| `note_get` | `note get` | Get the full content of a note |
| `note_add` | `note add` | Add a note to an item |

### tag (2 tools)

| Tool | CLI equivalent | Description |
|------|----------------|-------------|
| `tag_list` | `tag list` | List all tags in the library |
| `tag_items` | `tag items` | List items with a specific tag |

### search (3 tools)

| Tool | CLI equivalent | Description |
|------|----------------|-------------|
| `search_list` | `search list` | List saved searches |
| `search_get` | `search get` | Get details of a saved search |
| `search_items` | `search items` | Get items matching a saved search |

### style (1 tool)

| Tool | CLI equivalent | Description |
|------|----------------|-------------|
| `style_list` | `style list` | List available citation styles |

### import (3 tools)

| Tool | CLI equivalent | Description |
|------|----------------|-------------|
| `import_doi` | `import doi` | Import by DOI |
| `import_pmid` | `import pmid` | Import by PubMed ID |
| `import_file` | `import file` | Import from local RIS/BibTeX/CSL-JSON file |

### Top-level (2 tools)

| Tool | CLI equivalent | Description |
|------|----------------|-------------|
| `sync` | `sync` | Trigger Zotero sync |
| `js` | `js` | Execute arbitrary JavaScript in Zotero |

---

## CLI vs MCP: When to Use Which

| | CLI | MCP |
|---|---|---|
| **Use when** | Terminal workflows, shell scripts, REPL exploration | AI clients (Claude Desktop, Cursor, LM Studio) |
| **Interface** | `zotero-cli <command>` | Tools called by the AI client |
| **Output** | Human-readable or `--json` | Structured for LLM consumption |
| **Capabilities** | Same 56 operations | Same 56 operations |

Both use the same underlying core — the MCP server wraps the same functions the CLI calls.
