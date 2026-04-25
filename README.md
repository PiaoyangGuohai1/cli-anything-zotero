# cli-anything-zotero

[![PyPI](https://img.shields.io/pypi/v/cli-anything-zotero?color=blue)](https://pypi.org/project/cli-anything-zotero/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)
[![License](https://img.shields.io/github/license/PiaoyangGuohai1/cli-anything-zotero)](LICENSE)
[![GitHub release](https://img.shields.io/github/v/release/PiaoyangGuohai1/cli-anything-zotero)](https://github.com/PiaoyangGuohai1/cli-anything-zotero/releases)
[![GitHub stars](https://img.shields.io/github/stars/PiaoyangGuohai1/cli-anything-zotero)](https://github.com/PiaoyangGuohai1/cli-anything-zotero/stargazers)

**Let AI manage your Zotero library.**

[中文文档](docs/README_zh.md) | English

---

## For Non-Programmers

This tool is designed to be **used by AI, not memorized by you**. After a simple install (~3 minutes), just talk to your AI assistant in plain language:

> "Find papers about diabetes and kidney disease in my Zotero library"
>
> "Import this DOI into my CKM collection: 10.1038/s41586-024-07871-6"
>
> "Export all papers in my thesis collection as BibTeX"
>
> "Find PDFs for items in my review collection that are missing them"

**All you need to do:**
1. Follow the [Installation](#installation) steps below
2. Tell your AI assistant (Claude Code, Cursor, etc.) what you need
3. That's it

---

## What It Does

Built on [CLI-Anything](https://github.com/HKUDS/CLI-Anything) by [HKUDS](https://github.com/HKUDS), this tool gives AI agents full access to your local Zotero library through a **JS Bridge** — a lightweight Zotero plugin that exposes a privileged JavaScript endpoint.

**Key capabilities:**
- **Search & browse** — keyword search, full-text PDF search, collection tree, tags
- **Import** — from DOI, PMID, RIS/BibTeX files, or JSON
- **Export** — BibTeX, CSL-JSON, RIS, CSV, formatted citations
- **PDF management** — attach files, auto-find PDFs online, search annotations
- **Write operations** — update metadata, manage tags, add notes, trigger sync
- **Advanced** — execute arbitrary Zotero JS, semantic search with local embeddings, AI analysis
- **MCP server** — 52 tools for Claude Desktop, Cursor, LM Studio, and other MCP clients

All write operations run locally through the JS Bridge — no API key or internet connection required.

---

## Choose Your Mode

This tool supports two modes. **Pick the one that fits your AI client:**

| | CLI Mode | MCP Mode |
|---|---|---|
| **How AI calls it** | Shell commands (`zotero-cli item find ...`) | Structured tool calls (no command-line needed) |
| **Works with** | Any AI that can run shell commands (Claude Code, ChatGPT, Cursor, Windsurf, Cline, etc.) | AI clients with MCP support (Claude Desktop, Cursor, Claude Code, LM Studio, etc.) |
| **AI learning curve** | AI runs `--help` once to discover all 70+ commands | Zero — 52 tools are auto-registered with typed parameters |
| **Error rate** | Occasional typos by AI (self-corrects) | Near-zero (parameters are type-constrained) |
| **Install** | `pip install cli-anything-zotero` | `pip install 'cli-anything-zotero[mcp]'` + client config |

> **Not sure?** If your AI client supports MCP, choose MCP — it's more reliable. Otherwise, CLI works everywhere.

---

## Installation

**Prerequisites:** Python 3.10+, Zotero 7/8/9 (running).

### Step 1: Install the package

**CLI Mode** (for any AI assistant):
```bash
pip install cli-anything-zotero
```

**MCP Mode** (for Claude Desktop, Cursor, Claude Code, etc.):
```bash
pip install 'cli-anything-zotero[mcp]'
```

> Both modes are included in the same package. The `[mcp]` extra just adds MCP protocol dependencies.
> The package name stays `cli-anything-zotero`; the user-facing commands are `zotero-cli` for CLI workflows and `zotero-mcp` for MCP clients. The old `cli-anything-zotero` command remains as a compatibility alias.

### Step 2: Install the JS Bridge Plugin (one-time, both modes)

```bash
zotero-cli app install-plugin
```

First install requires manual steps in Zotero:
1. The command generates a `.xpi` file and prints its path
2. In Zotero: **Tools → Plugins → gear icon → Install Plugin From File...**
3. Select the `.xpi` file, then **restart Zotero**

> After the first install, future upgrades via `app install-plugin` are automatic.

### Step 3: Set up your AI client

<details>
<summary><b>CLI Mode — No extra setup needed</b></summary>

Just tell your AI assistant the tool is available. It will run `zotero-cli --help` to discover all commands automatically.

Verify it works:
```bash
zotero-cli app ping
zotero-cli js "return Zotero.version"
```
</details>

<details>
<summary><b>MCP Mode — Configure your AI client</b></summary>

**Claude Code:**
```bash
claude mcp add zotero --scope user -- zotero-mcp
```

**Claude Desktop / Cursor / LM Studio** — add to your MCP config file:
```json
{
  "mcpServers": {
    "zotero": {
      "command": "zotero-mcp"
    }
  }
}
```

After restarting your AI client, 52 Zotero tools will be available automatically.

Full MCP reference: **[docs/MCP.md](docs/MCP.md)**
</details>

### Troubleshooting

| Problem | Solution |
|---------|----------|
| `Cannot resolve Zotero profile directory` | Launch Zotero at least once first |
| Plugin not appearing | Restart Zotero after installing the `.xpi` |
| `endpoint_active: false` | Plugin failed to load — reinstall via Zotero UI |
| Windows: `pip` not recognized | Close and reopen PowerShell after installing Python |

---

## Usage (CLI Mode)

**Search & Browse**
```bash
zotero-cli item find "machine learning"
zotero-cli item search-fulltext "CRISPR"
zotero-cli collection tree
```

**Import**
```bash
zotero-cli import doi "10.1038/s41586-024-07871-6" --tag "review"
zotero-cli import pmid "37821702" --collection FMTCPUWN
zotero-cli import file ./refs.ris
```

**Read & Export**
```bash
zotero-cli item get ITEM_KEY
zotero-cli item export ITEM_KEY --format bibtex
zotero-cli item citation ITEM_KEY
zotero-cli item context ITEM_KEY              # LLM-ready context
zotero-cli docx inspect-citations draft.docx  # detect Zotero/EndNote/static citation fields
```

**Write & Manage**
```bash
zotero-cli item update KEY --field title="New Title"
zotero-cli item tag KEY --add "important"
zotero-cli item attach KEY ./paper.pdf
zotero-cli item find-pdf KEY
zotero-cli note add KEY --text "My note"
zotero-cli sync
```

**Advanced**
```bash
zotero-cli item search-annotations "risk"
zotero-cli item annotations KEY
zotero-cli item metrics KEY                   # NIH citation metrics
zotero-cli collection stats COLLECTION_KEY
zotero-cli js "return await Zotero.Items.getAll(1).then(i => i.length)"
```

Full command reference: **[docs/COMMANDS.md](docs/COMMANDS.md)**

---

## Optional Features

These require extra services. Everything else works without them.

### Semantic Search

Any OpenAI-compatible `/v1/embeddings` endpoint ([Ollama](https://ollama.com), [LM Studio](https://lmstudio.ai), OpenAI, etc.).

```bash
zotero-cli item build-index                            # one-time
zotero-cli item semantic-search "cardiovascular risk"
zotero-cli item similar ITEM_KEY
```

| Variable | Default | Description |
|----------|---------|-------------|
| `ZOTERO_EMBED_API` | `http://127.0.0.1:8080/v1/embeddings` | Embedding API endpoint |
| `ZOTERO_EMBED_MODEL` | `nomic-embed-text` | Model name |
| `ZOTERO_EMBED_KEY` | *(empty)* | API key (if needed) |

### AI Analysis

```bash
export OPENAI_API_KEY=sk-...
zotero-cli item analyze ITEM_KEY --question "What are the main findings?"
```

---

## Upgrading to 0.4.0

**Breaking change for MCP users:** All MCP tool names have been renamed from mixed conventions to a consistent `group_action` pattern matching the CLI. If you have agent prompts or configs referencing old tool names, update them:

<details>
<summary>MCP tool rename table (click to expand)</summary>

| Old name (0.3.x) | New name (0.4.0) |
|---|---|
| `list_libraries` | `library_list` |
| `list_collections` | `collection_list` |
| `find_collections` | `collection_find` |
| `get_collection` | `collection_get` |
| `create_collection` | `collection_create` |
| `delete_collection` | `collection_delete` |
| `update_collection` | `collection_rename` |
| `find_pdfs_in_collection` | `collection_find_pdfs` |
| `remove_from_collection` | `collection_remove_item` |
| `list_items` | `item_list` |
| `find_items` | `item_find` |
| `get_item` | `item_get` |
| `export_item` | `item_export` |
| `citation_item` | `item_citation` |
| `bibliography_item` | `item_bibliography` |
| `manage_tags` | `item_tag` |
| `update_item_fields` | `item_update` |
| `delete_item` | `item_delete` |
| `find_pdf` | `item_find_pdf` |
| `attach_pdf` | `item_attach` |
| `add_to_collection` | `item_add_to_collection` |
| `get_annotations` | `item_annotations` |
| `search_annotations` | `item_search_annotations` |
| `search_fulltext` | `item_search_fulltext` |
| `semantic_search` | `item_semantic_search` |
| `find_similar` | `item_similar` |
| `build_index` | `item_build_index` |
| `find_duplicates` | `item_duplicates` |
| `get_citation_metrics` | `item_metrics` |
| `analyze_item` | `item_analyze` |
| `get_note` | `note_get` |
| `add_note` | `note_add` |
| `list_tags` | `tag_list` |
| `list_searches` | `search_list` |
| `list_styles` | `style_list` |
| `import_from_doi` | `import_doi` |
| `import_from_pmid` | `import_pmid` |
| `trigger_sync` | `sync` |
| `execute_js` | `js` |

New tools in 0.4.0: `search_get`, `search_items`, `item_move_to_collection`
</details>

**CLI users:** No breaking changes. The `--help` output now shows all commands at once.

---

## Related Projects

There are several great tools in the Zotero ecosystem. Each has different strengths depending on your use case:

| | **cli-anything-zotero** | [zotero-mcp](https://github.com/54yyyu/zotero-mcp) | [zotero-cli-cc](https://github.com/Agents365-ai/zotero-cli-cc) | [pyzotero-cli](https://github.com/chriscarrollsmith/pyzotero-cli) |
|---|---|---|---|---|
| **Approach** | Local JS Bridge | Web API + MCP | Web API + CLI | Web API + CLI |
| **Best for** | Local-first, full control | MCP-native workflows | Agent-driven research | Scripting & automation |
| **Write ops** | Local (no API key) | Via Web API | Via Web API | Via Web API |
| **MCP support** | 52 tools | Yes | 45 tools | No |
| **Terminal CLI** | Yes | No | Yes | Yes |
| **Zotero JS access** | Yes | No | No | No |
| **License** | Apache 2.0 | MIT | CC BY-NC 4.0 | MIT |

---

## License

[Apache 2.0](LICENSE)
