---
name: cli-anything-zotero
description: >-
  Full-featured CLI for Zotero reference management. Search, import by DOI/PMID,
  export BibTeX, attach PDFs, semantic search, manage tags/collections, and more.
  Use this tool whenever you need to interact with a Zotero library programmatically.
---

# cli-anything-zotero

Agent-native CLI for Zotero 7/8 desktop. 40+ commands across four backends.

## Before You Start

Run `cli-anything-zotero app check-update` at the beginning of a session.
If it reports an update, ask the user to run the upgrade command before proceeding.

## When to Use

- User asks to search, find, or look up papers in their Zotero library
- User needs to import a paper by DOI or PMID
- User wants to export citations or BibTeX
- User needs to attach a PDF to a Zotero item
- User wants to find available PDFs for items missing them
- User asks about collection statistics, duplicates, or annotations
- User needs semantic search ("find papers similar to X")
- User wants to update metadata, tags, or manage collections

## Key Commands by Task

### Search & Discovery

```bash
# Keyword search
cli-anything-zotero item find "query" --limit 10

# Full-text search inside PDFs
cli-anything-zotero item search-fulltext "query" --limit 10

# Semantic search (requires ZOTERO_EMBED_* env vars)
cli-anything-zotero item semantic-search "natural language query" --top-k 10

# Find papers similar to a known item
cli-anything-zotero item similar ITEM_KEY --top-k 5

# Search annotations/highlights by keyword or color
cli-anything-zotero item search-annotations "keyword" --color "#ff6666" --limit 10

# Find duplicates
cli-anything-zotero item duplicates --limit 20
```

### Read Item Details

```bash
# Full item metadata (returns JSON with title, creators, DOI, tags, attachments, etc.)
cli-anything-zotero --json item get ITEM_KEY

# List attachments
cli-anything-zotero --json item attachments ITEM_KEY

# View PDF annotations
cli-anything-zotero --json item annotations ITEM_KEY

# Citation metrics from NIH iCite
cli-anything-zotero --json item metrics PMID --pmid
# Or by Zotero item key (auto-extracts PMID from extra field)
cli-anything-zotero --json item metrics ITEM_KEY
```

### Import Papers

```bash
# Import by DOI (auto-fetches all metadata)
cli-anything-zotero import doi "10.1038/s41586-024-07871-6" --tag "review" --collection COLLECTION_KEY

# Import by PMID
cli-anything-zotero import pmid "38551621" --tag "epidemiology"

# Import from file (RIS, BibTeX, etc.)
cli-anything-zotero import file ./references.ris --collection COLLECTION_KEY
```

### Export & Citations

```bash
# Export BibTeX
cli-anything-zotero item export ITEM_KEY --format bibtex

# Other formats: ris, biblatex, csljson, csv, mods, refer
cli-anything-zotero item export ITEM_KEY --format ris

# Render citation (returns HTML)
cli-anything-zotero item citation ITEM_KEY --style apa

# Render bibliography entry
cli-anything-zotero item bibliography ITEM_KEY --style apa
```

### PDF Management

```bash
# Attach a local PDF to an existing item
cli-anything-zotero item attach ITEM_KEY /path/to/paper.pdf

# Trigger "Find Available PDF" for one item
cli-anything-zotero item find-pdf ITEM_KEY

# Batch find PDFs for all items missing them in a collection
cli-anything-zotero collection find-pdfs COLLECTION_KEY
```

### Write Operations

```bash
# Update metadata fields
cli-anything-zotero item update ITEM_KEY --field title="New Title" --field DOI="10.1234/xxx"

# Add/remove tags
cli-anything-zotero item tag ITEM_KEY --add "important" --remove "unread"

# Delete item (requires --confirm)
cli-anything-zotero item delete ITEM_KEY --confirm
```

### Collection Management

```bash
# List all collections
cli-anything-zotero collection list

# Collection hierarchy
cli-anything-zotero collection tree

# Find collection by name
cli-anything-zotero collection find "query"

# Items in a collection
cli-anything-zotero --json collection items COLLECTION_KEY

# Collection statistics (total items, PDF coverage, year/journal distribution)
cli-anything-zotero --json collection stats COLLECTION_KEY

# Create / rename / delete collection
cli-anything-zotero collection create "New Collection"
cli-anything-zotero collection rename COLLECTION_KEY --name "New Name"
cli-anything-zotero collection delete COLLECTION_KEY --confirm

# Add/remove items from collection
cli-anything-zotero item add-to-collection ITEM_KEY COLLECTION_KEY
cli-anything-zotero collection remove-item COLLECTION_KEY ITEM_KEY
```

### Sync & Utilities

```bash
# Trigger Zotero sync
cli-anything-zotero sync

# Execute arbitrary Zotero JavaScript
cli-anything-zotero js "return Zotero.Items.getAll(1).length;" --wait 5

# Interactive REPL
cli-anything-zotero repl
```

## Output Format

- Use `--json` flag on the root command for machine-readable JSON output:
  ```bash
  cli-anything-zotero --json item find "query"
  ```
- Without `--json`, output is human-readable text.

## Important Constraints

- **All platforms**: Works on macOS, Windows, and Linux with the JS Bridge plugin installed.
- **Zotero must be running**: All commands connect to Zotero's HTTP server (port 23119) or read its SQLite database.
- **JS Bridge plugin required**: Install via `cli-anything-zotero app install-plugin`. Once installed, all JS bridge commands work silently.
- **Semantic search**: Requires `ZOTERO_EMBED_API`, `ZOTERO_EMBED_MODEL`, and `ZOTERO_EMBED_KEY` environment variables, plus a pre-built vector index at `ZOTERO_VECTOR_DB`.
- **Item references**: Most commands accept a Zotero item key (8-char alphanumeric like `9LPV3KTS`), title fragment, or numeric ID.
- **Collection references**: Accept collection key or numeric ID.

## Common Workflows

### "Find papers about X and export BibTeX"
```bash
cli-anything-zotero --json item find "X" --limit 20
# Pick relevant keys from output, then:
cli-anything-zotero item export KEY1 --format bibtex
cli-anything-zotero item export KEY2 --format bibtex
```

### "Import a paper and attach its PDF"
```bash
cli-anything-zotero import doi "10.xxxx/yyyy"
# Note the item key from output, then:
cli-anything-zotero item attach ITEM_KEY /path/to/paper.pdf
```

### "What papers in collection X are missing PDFs?"
```bash
cli-anything-zotero --json collection stats COLLECTION_KEY
# Shows total vs withPDF vs noPDF count
# Then batch-find:
cli-anything-zotero collection find-pdfs COLLECTION_KEY
```

### "Find papers similar to one I'm reading"
```bash
cli-anything-zotero --json item similar ITEM_KEY --top-k 10
```

## Version

0.1.0
