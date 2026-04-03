# cli-anything-zotero

A macOS CLI that manages the **Zotero 7/8 desktop app** through four native backends: SQLite, Connector API, Local API, and a JavaScript bridge. Built on the [CLI-Anything](https://github.com/HKUDS/CLI-Anything) framework by [HKUDS](https://github.com/HKUDS).

Designed for AI agents and power users who need programmatic, non-GUI access to a running Zotero instance.

---

## Architecture

The CLI routes each command through the most appropriate backend:

| Backend | Endpoint | Role |
|---|---|---|
| **SQLite** | `~/Zotero/zotero.sqlite` (read-only) | Fast offline queries: list items, collections, tags, saved searches |
| **Connector API** | `localhost:23119/connector/*` | Official write operations: import by DOI/PMID, save web pages |
| **Local API** | `localhost:23119/api/*` | Search, export, citation rendering, bibliography generation |
| **JS Bridge** | `localhost:23119/cli-bridge/eval` | Privileged Zotero JS execution: attach PDFs, find available PDFs, delete items, sync, semantic search |

### The JS Bridge

The JS bridge is the key innovation. It auto-registers an HTTP eval endpoint (`/cli-bridge/eval`) into Zotero's built-in HTTP server on port 23119.

**How it works:**
1. First call uses AppleScript to inject the endpoint registration code into Zotero (one-time setup per Zotero session).
2. All subsequent calls go through HTTP -- zero UI popups, millisecond response times.
3. If Zotero restarts, the bridge re-registers automatically on next use.

This gives the CLI full access to Zotero's internal JavaScript API without requiring any plugins or extensions.

---

## Prerequisites

- **macOS** (required for JS bridge AppleScript fallback)
- **Zotero 7 or 8** (must be running)
- **Python 3.10+**

---

## Installation

```bash
git clone https://github.com/HKUDS/CLI-Anything.git
cd CLI-Anything/zotero/agent-harness
pip install -e .
```

Verify the installation:

```bash
cli-anything-zotero app ping
```

---

## Command Reference

### Global Options

```
--json                       Emit machine-readable JSON
--backend [auto|sqlite|api]  Force a specific backend (default: auto)
--data-dir TEXT              Explicit Zotero data directory
--profile-dir TEXT           Explicit Zotero profile directory
--executable TEXT            Explicit Zotero executable path
```

### `app` -- Application and Runtime

| Command | Description |
|---|---|
| `app ping` | Check if Zotero is running and reachable |
| `app status` | Show runtime status and backend availability |
| `app version` | Print Zotero version |
| `app launch` | Launch the Zotero application |
| `app enable-local-api` | Enable the Zotero Local API |

### `item` -- Item Inspection and Rendering

| Command | Description |
|---|---|
| `item list` | List items in the library or current collection |
| `item get` | Get full metadata for an item by key |
| `item find` | Find items by title, creator, or other fields |
| `item context` | Show item context (metadata, notes, annotations summary) |
| `item citation` | Render a formatted citation for an item |
| `item bibliography` | Render a bibliography entry for an item |
| `item export` | Export items in BibTeX, CSL JSON, or other formats |
| `item children` | List child items (notes, attachments) of an item |
| `item attachments` | List attachments for an item |
| `item file` | Get the file path of an item's PDF attachment |
| `item notes` | List notes attached to an item |
| `item annotations` | View annotations and highlights for a Zotero item |
| `item analyze` | Analyze an item (extract key information) |
| `item metrics` | Fetch NIH iCite citation metrics for an item |
| `item tag` | Add or remove tags on an existing Zotero item |
| `item update` | Update metadata fields on an existing Zotero item |
| `item delete` | Delete a Zotero item permanently (via JS bridge) |
| `item attach` | Attach a local PDF file to an existing Zotero item |
| `item find-pdf` | Trigger Zotero's "Find Available PDF" for a single item |
| `item add-to-collection` | Add an item to a collection |
| `item move-to-collection` | Move an item to a different collection |
| `item duplicates` | Find duplicate items in the library (via JS bridge) |
| `item search-annotations` | Search annotations across all items by keyword |
| `item search-fulltext` | Search full-text content of PDFs in the library |
| `item semantic-search` | Semantic search across the library using embeddings |
| `item similar` | Find items similar to a given item using embeddings |

### `collection` -- Collection Management

| Command | Description |
|---|---|
| `collection list` | List all collections |
| `collection get` | Get details for a collection by key |
| `collection find` | Find collections by name |
| `collection tree` | Show the collection hierarchy as a tree |
| `collection items` | List items in a collection |
| `collection stats` | Get statistics for a collection (via JS bridge) |
| `collection create` | Create a new collection |
| `collection rename` | Rename or move a collection (via JS bridge) |
| `collection delete` | Delete a collection (via JS bridge) |
| `collection remove-item` | Remove an item from a collection (item is not deleted) |
| `collection find-pdfs` | Find available PDFs for all items missing PDFs in a collection |
| `collection use-selected` | Set the currently selected collection as the session default |

### `import` -- Import and Write

| Command | Description |
|---|---|
| `import doi` | Import an item by DOI using Zotero's built-in translator |
| `import pmid` | Import an item by PMID using Zotero's built-in translator |
| `import file` | Import from a local file |
| `import json` | Import from a JSON payload |

### `note` -- Notes

| Command | Description |
|---|---|
| `note get` | Read a child note |
| `note add` | Add a child note to an item |

### `search` -- Saved Searches

| Command | Description |
|---|---|
| `search list` | List all saved searches |
| `search get` | Get details for a saved search |
| `search items` | List items matching a saved search |

### `tag` -- Tags

| Command | Description |
|---|---|
| `tag list` | List all tags in the library |
| `tag items` | List items with a specific tag |

### `style` -- Citation Styles

| Command | Description |
|---|---|
| `style list` | List installed CSL citation styles |

### `session` -- Session and REPL Context

| Command | Description |
|---|---|
| `session status` | Show current session state |
| `session history` | Show command history |
| `session use-library` | Set the active library |
| `session use-collection` | Set the active collection |
| `session use-item` | Set the active item |
| `session use-selected` | Use the currently selected item in Zotero |
| `session clear-library` | Clear the active library |
| `session clear-collection` | Clear the active collection |
| `session clear-item` | Clear the active item |

### `js` -- JavaScript Execution

```bash
cli-anything-zotero js "Zotero.Items.getAll(1).length"
```

Execute arbitrary JavaScript in Zotero's JS console via the JS bridge. Accepts a `--wait` option for long-running scripts.

### `sync` -- Trigger Sync

```bash
cli-anything-zotero sync
```

Trigger a Zotero sync operation via the JS bridge.

### `repl` -- Interactive REPL

```bash
cli-anything-zotero repl
```

Start an interactive shell with tab completion and command history.

---

## Configuration

Environment variables for optional features:

| Variable | Default | Description |
|---|---|---|
| `ZOTERO_LOCALE` | `en` | Zotero UI language (`en`/`zh`) for AppleScript menu navigation |
| `ZOTERO_EMBED_API` | `http://127.0.0.1:8080/v1/embeddings` | OpenAI-compatible embedding API URL |
| `ZOTERO_EMBED_MODEL` | `nomic-embed-text` | Embedding model name |
| `ZOTERO_EMBED_KEY` | (empty) | API key for embedding service |
| `ZOTERO_VECTOR_DB` | `~/Zotero/zotero-mcp-vectors.sqlite` | Path to vector database for semantic search |

---

## Limitations

- **macOS only.** The JS bridge depends on AppleScript for initial registration. Linux support is possible with `xdotool` but not yet implemented.
- **Zotero must be running.** The CLI communicates with Zotero over HTTP; it cannot operate on a closed application.
- **Semantic search** requires a separate OpenAI-compatible embedding API and a pre-built vector index.
- **JS bridge endpoint is session-only.** It re-registers automatically when Zotero restarts, but the endpoint does not persist across Zotero sessions.

---

## License

Apache 2.0 -- same as the parent [CLI-Anything](https://github.com/HKUDS/CLI-Anything) project.
