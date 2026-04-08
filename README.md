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
- **MCP server** — 49 tools for Claude Desktop, Cursor, LM Studio, and other MCP clients

All write operations run locally through the JS Bridge — no API key or internet connection required.

---

## Installation

**Prerequisites:** Python 3.10+, Zotero 7/8 (running).

### 1. Install the CLI

```bash
pip install cli-anything-zotero
```

Or install from source:

```bash
git clone https://github.com/PiaoyangGuohai1/cli-anything-zotero.git
cd cli-anything-zotero && pip install -e .
```

### 2. Install the JS Bridge Plugin (one-time)

```bash
cli-anything-zotero app install-plugin
```

First install requires manual steps in Zotero:
1. The command generates a `.xpi` file and prints its path
2. In Zotero: **Tools → Plugins → gear icon → Install Plugin From File...**
3. Select the `.xpi` file, then **restart Zotero**

> After the first install, future upgrades via `app install-plugin` are automatic.

### 3. Verify

```bash
cli-anything-zotero app plugin-status --json
# Should show: "plugin_installed": true, "endpoint_active": true

cli-anything-zotero app ping
cli-anything-zotero js "return Zotero.version"
```

### Troubleshooting

| Problem | Solution |
|---------|----------|
| `Cannot resolve Zotero profile directory` | Launch Zotero at least once first |
| Plugin not appearing | Restart Zotero after installing the `.xpi` |
| `endpoint_active: false` | Plugin failed to load — reinstall via Zotero UI |
| Windows: `pip` not recognized | Close and reopen PowerShell after installing Python |

---

## Usage

**Search & Browse**
```bash
cli-anything-zotero item find "machine learning"
cli-anything-zotero item search-fulltext "CRISPR"
cli-anything-zotero collection tree
```

**Import**
```bash
cli-anything-zotero import doi "10.1038/s41586-024-07871-6" --tag "review"
cli-anything-zotero import pmid "37821702" --collection FMTCPUWN
cli-anything-zotero import file ./refs.ris
```

**Read & Export**
```bash
cli-anything-zotero item get ITEM_KEY
cli-anything-zotero item export ITEM_KEY --format bibtex
cli-anything-zotero item citation ITEM_KEY
cli-anything-zotero item context ITEM_KEY              # LLM-ready context
```

**Write & Manage**
```bash
cli-anything-zotero item update KEY --field title="New Title"
cli-anything-zotero item tag KEY --add "important"
cli-anything-zotero item attach KEY ./paper.pdf
cli-anything-zotero item find-pdf KEY
cli-anything-zotero note add KEY --text "My note"
cli-anything-zotero sync
```

**Advanced**
```bash
cli-anything-zotero item search-annotations "risk"
cli-anything-zotero item annotations KEY
cli-anything-zotero item metrics KEY                   # NIH citation metrics
cli-anything-zotero collection stats COLLECTION_KEY
cli-anything-zotero js "return await Zotero.Items.getAll(1).then(i => i.length)"
```

Full command reference: **[docs/COMMANDS.md](docs/COMMANDS.md)**

---

## MCP Server

49 tools for AI clients that support the [Model Context Protocol](https://modelcontextprotocol.io/). Full reference: **[docs/MCP.md](docs/MCP.md)**

```bash
pip install 'cli-anything-zotero[mcp]'
zotero-cli mcp serve
```

Client configuration (Claude Desktop / Cursor / LM Studio):

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

---

## Optional Features

These require extra services. Everything else works without them.

### Semantic Search

Any OpenAI-compatible `/v1/embeddings` endpoint ([Ollama](https://ollama.com), [LM Studio](https://lmstudio.ai), OpenAI, etc.).

```bash
cli-anything-zotero item build-index                            # one-time
cli-anything-zotero item semantic-search "cardiovascular risk"
cli-anything-zotero item similar ITEM_KEY
```

| Variable | Default | Description |
|----------|---------|-------------|
| `ZOTERO_EMBED_API` | `http://127.0.0.1:8080/v1/embeddings` | Embedding API endpoint |
| `ZOTERO_EMBED_MODEL` | `nomic-embed-text` | Model name |
| `ZOTERO_EMBED_KEY` | *(empty)* | API key (if needed) |

### AI Analysis

```bash
export OPENAI_API_KEY=sk-...
cli-anything-zotero item analyze ITEM_KEY --question "What are the main findings?"
```

---

## Related Projects

There are several great tools in the Zotero ecosystem. Each has different strengths depending on your use case:

| | **cli-anything-zotero** | [zotero-mcp](https://github.com/54yyyu/zotero-mcp) | [zotero-cli-cc](https://github.com/Agents365-ai/zotero-cli-cc) | [pyzotero-cli](https://github.com/chriscarrollsmith/pyzotero-cli) |
|---|---|---|---|---|
| **Approach** | Local JS Bridge | Web API + MCP | Web API + CLI | Web API + CLI |
| **Best for** | Local-first, full control | MCP-native workflows | Agent-driven research | Scripting & automation |
| **Write ops** | Local (no API key) | Via Web API | Via Web API | Via Web API |
| **MCP support** | 49 tools | Yes | 45 tools | No |
| **Terminal CLI** | Yes | No | Yes | Yes |
| **Zotero JS access** | Yes | No | No | No |
| **License** | Apache 2.0 | MIT | CC BY-NC 4.0 | MIT |

---

## License

[Apache 2.0](LICENSE)
