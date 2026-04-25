# Full Command Reference

Complete command reference for `zotero-cli`. For installation and quick start, see the [README](../README.md).

> Tip: Run `zotero-cli <command> --help` for detailed usage of any command.

---

## app -- Application Status

| Command | Description |
|---------|-------------|
| `app ping` | Check if Zotero is running |
| `app status` | Runtime and backend status |
| `app version` | Package and Zotero version |
| `app launch` | Launch Zotero if not running |
| `app enable-local-api` | Enable Local API in Zotero prefs |
| `app plugin-status` | Check JS Bridge plugin installation |
| `app install-plugin` | Install/update the JS Bridge plugin |

## item -- Item Operations

### Core (works out of the box)

| Command | Description | Backend |
|---------|-------------|---------|
| `item find <query>` | Keyword search | SQLite |
| `item list [--limit N]` | List recent items | SQLite |
| `item get <ref>` | Full item details | SQLite |
| `item children <ref>` | Child items (attachments, notes) | SQLite |
| `item attachments <ref>` | List attachments | SQLite |
| `item notes <ref>` | List notes | SQLite |
| `item file <ref>` | Get attachment file path | SQLite |
| `item context <ref>` | Build LLM-ready context | SQLite |
| `item export <ref> --format bibtex` | Export RIS/BibTeX/CSL JSON/CSV | Local API |
| `item citation <ref>` | Render inline citation | Local API |
| `item bibliography <ref>` | Render bibliography entry | Local API |
| `item search-fulltext <query>` | Search inside PDF text | JS Bridge |
| `item search-annotations <query>` | Search highlights/notes across library | JS Bridge |
| `item annotations <key>` | View annotations for an item | JS Bridge |
| `item duplicates` | Find duplicate items | JS Bridge |
| `item metrics <ref> [--pmid]` | NIH iCite citation metrics | iCite API |
| `item update <key> --field k=v` | Update metadata fields | JS Bridge |
| `item tag <key> --add/--remove` | Add or remove tags | JS Bridge |
| `item attach <key> <pdf_path>` | Attach local PDF to item | JS Bridge |
| `item find-pdf <key>` | Trigger "Find Available PDF" | JS Bridge |
| `item delete <key> --confirm` | Delete item permanently | JS Bridge |
| `item add-to-collection <ref> <col>` | Add item to collection | SQLite (experimental) |
| `item move-to-collection <ref> <col>` | Move item between collections | SQLite (experimental) |

### Optional (requires extra services)

| Command | Description | Requires |
|---------|-------------|----------|
| `item semantic-search <query>` | AI semantic search (embedding-based) | Embedding API + `item build-index` |
| `item similar <key>` | Find similar items | Embedding API + `item build-index` |
| `item build-index` | Build vector index for semantic search | Embedding API |
| `item analyze <ref> --question Q` | AI analysis of a paper | `OPENAI_API_KEY` |

## collection -- Collection Management

| Command | Description | Backend |
|---------|-------------|---------|
| `collection list` | List all collections | SQLite |
| `collection find <query>` | Search collections by name | SQLite |
| `collection tree` | Display collection hierarchy | SQLite |
| `collection get <ref>` | Collection details | SQLite |
| `collection items <ref>` | List items in collection | SQLite |
| `collection create <name>` | Create new collection | SQLite (experimental) |
| `collection stats <key>` | Statistics (item count, PDF coverage, year/journal distribution) | JS Bridge |
| `collection find-pdfs <key>` | Batch "Find Available PDF" for items missing PDFs | JS Bridge |
| `collection remove-item <col_key> <item_key>` | Remove item from collection (keeps item) | JS Bridge |
| `collection rename <key> --name/--parent` | Rename or move collection | JS Bridge |
| `collection delete <key> --confirm` | Delete collection | JS Bridge |
| `collection use-selected` | Use currently selected collection in Zotero GUI | Connector |

## import -- Import Items

| Command | Description | Backend |
|---------|-------------|---------|
| `import doi <doi> [--tag T] [--collection K]` | Import by DOI with auto-metadata | JS Bridge |
| `import pmid <pmid> [--tag T] [--collection K]` | Import by PMID with auto-metadata | JS Bridge |
| `import file <path> [--collection K]` | Import from RIS/BibTeX file | Connector |
| `import json <path> [--collection K]` | Import from JSON | Connector |

## Other Commands

| Command | Description |
|---------|-------------|
| `docx inspect-citations <file.docx>` | Detect Zotero, EndNote, CSL/Mendeley-like fields and static citation text in a DOCX |
| `sync` | Trigger Zotero sync |
| `js <code> [--wait N]` | Execute arbitrary Zotero JavaScript |
| `tag list` | List all tags |
| `tag items <tag>` | Items with a specific tag |
| `note get <ref>` | Read a note |
| `note add <ref> --text T` | Add note to item |
| `search list` | List saved searches |
| `search items <ref>` | Run a saved search |
| `style list` | List installed CSL citation styles |
| `session status` | Show current session context |
| `session use-collection <ref>` | Set session collection |
| `session use-library <ref>` | Set session library |
| `repl` | Interactive REPL mode |

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `ZOTERO_LOCALE` | `en` | Zotero UI language (`en` or `zh`) |
| `ZOTERO_EMBED_API` | `http://127.0.0.1:8080/v1/embeddings` | OpenAI-compatible embedding API endpoint |
| `ZOTERO_EMBED_MODEL` | `nomic-embed-text` | Embedding model name |
| `ZOTERO_EMBED_KEY` | *(empty)* | API key for embedding service |
| `ZOTERO_VECTOR_DB` | `~/Zotero/cli-anything-vectors.sqlite` | Path to vector database for semantic search |

## Backend Architecture

Four backend layers, auto-selected per command:

| Layer | Endpoint | Operations | Speed |
|-------|----------|-----------|-------|
| **SQLite** | `~/Zotero/zotero.sqlite` (read-only) | Search, list, get, export metadata | Instant |
| **Connector API** | `localhost:23119/connector/*` | Import items + attachments | ~1s |
| **Local API** | `localhost:23119/api/*` | Citation rendering, BibTeX export | ~1s |
| **JS Bridge** | `localhost:23119/cli-bridge/eval` | Everything else (attach PDF, find PDF, update, tags, sync) | ~0.5s |
