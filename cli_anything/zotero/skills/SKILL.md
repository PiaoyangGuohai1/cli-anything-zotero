---
name: cli-anything-zotero
description: >-
  Full-featured CLI for Zotero reference management. Search, import by DOI/PMID,
  export BibTeX, attach PDFs, semantic search, manage tags/collections, and more.
  Use this tool whenever you need to interact with a Zotero library programmatically.
---

# cli-anything-zotero

Agent-native CLI for Zotero 7/8/9 desktop. 40+ commands across four backends.

## Before You Start

Run `zotero-cli app check-update` at the beginning of a session.
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
zotero-cli item find "query" --limit 10

# Full-text search inside PDFs
zotero-cli item search-fulltext "query" --limit 10

# Semantic search (requires ZOTERO_EMBED_* env vars)
zotero-cli item semantic-search "natural language query" --top-k 10

# Find papers similar to a known item
zotero-cli item similar ITEM_KEY --top-k 5

# Search annotations/highlights by keyword or color
zotero-cli item search-annotations "keyword" --color "#ff6666" --limit 10

# Find duplicates
zotero-cli item duplicates --limit 20
```

### Read Item Details

```bash
# Full item metadata (returns JSON with title, creators, DOI, tags, attachments, etc.)
zotero-cli --json item get ITEM_KEY

# List attachments
zotero-cli --json item attachments ITEM_KEY

# View PDF annotations
zotero-cli --json item annotations ITEM_KEY

# Citation metrics from NIH iCite
zotero-cli --json item metrics PMID --pmid
# Or by Zotero item key (auto-extracts PMID from extra field)
zotero-cli --json item metrics ITEM_KEY
```

### Import Papers

```bash
# Import by DOI (auto-fetches all metadata)
zotero-cli import doi "10.1038/s41586-024-07871-6" --tag "review" --collection COLLECTION_KEY

# Import by PMID
zotero-cli import pmid "38551621" --tag "epidemiology"

# Import from file (RIS, BibTeX, etc.)
zotero-cli import file ./references.ris --collection COLLECTION_KEY
```

### Export & Citations

```bash
# Export one item in BibTeX
zotero-cli item export ITEM_KEY --format bibtex

# Export a standalone BibTeX file for downstream tools
zotero-cli export bib --items KEY1,KEY2 --output refs.bib
zotero-cli export bib --collection COLLECTION_KEY --output refs.bib

# Other formats: ris, biblatex, csljson, csv, mods, refer
zotero-cli item export ITEM_KEY --format ris

# Render citation preview (static HTML, not a refreshable DOCX field)
zotero-cli item citation ITEM_KEY --style apa

# Render bibliography preview (static HTML, not a refreshable DOCX field)
zotero-cli item bibliography ITEM_KEY --style apa

# Inspect citation fields inside a DOCX before mixing Zotero/EndNote/static references
zotero-cli --json docx inspect-citations manuscript.docx

# AI-authored DOCX citations must use real Zotero placeholders, then validate them
zotero-cli --json docx inspect-placeholders manuscript.docx
zotero-cli --json docx validate-placeholders manuscript.docx

# Static final DOCX: replace placeholders with ordinary text citations and bibliography
zotero-cli --json docx render-citations manuscript.docx --output manuscript-static.docx --force

# Dynamic final DOCX: use Zotero/LibreOffice fields when the user needs Refresh
zotero-cli --json docx doctor
zotero-cli --json docx insert-citations manuscript.docx --output manuscript-zotero.docx --force

### AI DOCX Citation Decision Flow

When the user provides a DOCX draft that contains citations, follow this explicit branch:

- Ask for intent if mode is unclear:
  - "Do you want static references (final text now), or dynamic references (refreshable in Zotero/LibreOffice)?"
- If static is requested:
  - `zotero-cli --json docx validate-placeholders <docx>`
  - `zotero-cli --json docx render-citations <docx> --output <final.docx> --force`
- If dynamic is requested or user mentions later editing/refresh:
  - `zotero-cli --json docx validate-placeholders <docx>`
  - `zotero-cli --json docx doctor`
  - `zotero-cli --json docx insert-citations <docx> --output <final.docx> --force`
- If dynamic conversion returns environment errors:
  - do not retry blindly
  - report exact error context from `docx doctor`/`docx zoterify-probe`
  - offer fallback to static mode (`render-citations`)

Keep only user-facing outputs:
- input placeholder DOCX
- final converted DOCX
- debug artifacts only if user explicitly asked for `--debug-dir`

Behavior note:
- `item citation` and `item bibliography` are static previews and must **not** be used as a replacement for DOCX writing conversion.
- `docx prepare-zotero-import` remains experimental and should not be called in normal user workflows.
```

When drafting DOCX content with AI, never invent final static citations such as
`(Author, 2024)` unless the user explicitly asks for static prose. Insert
`{{zotero:ITEMKEY}}` or `{{zotero:KEY1,KEY2}}` placeholders from real Zotero
items and validate the document before handoff. When the user asks to insert
citations without specifying a mode, ask whether they want static citations
(`docx render-citations`, simplest) or dynamic Zotero fields
(`docx insert-citations`, refreshable but requires LibreOffice).

### PDF Management

```bash
# Attach a local PDF to an existing item
zotero-cli item attach ITEM_KEY /path/to/paper.pdf

# Trigger "Find Available PDF" for one item
zotero-cli item find-pdf ITEM_KEY

# Batch find PDFs for all items missing them in a collection
zotero-cli collection find-pdfs COLLECTION_KEY
```

### Write Operations

```bash
# Update metadata fields
zotero-cli item update ITEM_KEY --field title="New Title" --field DOI="10.1234/xxx"

# Add/remove tags
zotero-cli item tag ITEM_KEY --add "important" --remove "unread"

# Delete item (requires --confirm)
zotero-cli item delete ITEM_KEY --confirm
```

### Collection Management

```bash
# List all collections
zotero-cli collection list

# Collection hierarchy
zotero-cli collection tree

# Find collection by name
zotero-cli collection find "query"

# Items in a collection
zotero-cli --json collection items COLLECTION_KEY

# Collection statistics (total items, PDF coverage, year/journal distribution)
zotero-cli --json collection stats COLLECTION_KEY

# Create / rename / delete collection
zotero-cli collection create "New Collection"
zotero-cli collection rename COLLECTION_KEY --name "New Name"
zotero-cli collection delete COLLECTION_KEY --confirm

# Add/remove items from collection
zotero-cli item add-to-collection ITEM_KEY COLLECTION_KEY
zotero-cli collection remove-item COLLECTION_KEY ITEM_KEY
```

### Sync & Utilities

```bash
# Trigger Zotero sync
zotero-cli sync

# Execute arbitrary Zotero JavaScript
zotero-cli js "return Zotero.Items.getAll(1).length;" --wait 5

# Interactive REPL
zotero-cli repl
```

## Output Format

- Use `--json` flag on the root command for machine-readable JSON output:
  ```bash
  zotero-cli --json item find "query"
  ```
- Without `--json`, output is human-readable text.

## Important Constraints

- **All platforms**: Works on macOS, Windows, and Linux with the JS Bridge plugin installed.
- **Zotero must be running**: All commands connect to Zotero's HTTP server (port 23119) or read its SQLite database.
- **JS Bridge plugin required**: Install via `zotero-cli app install-plugin`. Once installed, all JS bridge commands work silently.
- **Semantic search**: Requires `ZOTERO_EMBED_API`, `ZOTERO_EMBED_MODEL`, and `ZOTERO_EMBED_KEY` environment variables, plus a pre-built vector index at `ZOTERO_VECTOR_DB`.
- **Item references**: Most commands accept a Zotero item key (8-char alphanumeric like `9LPV3KTS`), title fragment, or numeric ID.
- **Collection references**: Accept collection key or numeric ID.

## Common Workflows

### "Find papers about X and export BibTeX"
```bash
zotero-cli --json item find "X" --limit 20
# Pick relevant keys from output, then:
zotero-cli export bib --items KEY1,KEY2 --output refs.bib
```

### "Import a paper and attach its PDF"
```bash
zotero-cli import doi "10.xxxx/yyyy"
# Note the item key from output, then:
zotero-cli item attach ITEM_KEY /path/to/paper.pdf
```

### "What papers in collection X are missing PDFs?"
```bash
zotero-cli --json collection stats COLLECTION_KEY
# Shows total vs withPDF vs noPDF count
# Then batch-find:
zotero-cli collection find-pdfs COLLECTION_KEY
```

### "Find papers similar to one I'm reading"
```bash
zotero-cli --json item similar ITEM_KEY --top-k 10
```

## Version

0.9.4
