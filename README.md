[![MseeP.ai Security Assessment Badge](https://mseep.net/pr/piaoyangguohai1-cli-anything-zotero-badge.png)](https://mseep.ai/app/piaoyangguohai1-cli-anything-zotero)

# cli-anything-zotero

[![PyPI](https://img.shields.io/pypi/v/cli-anything-zotero?color=blue)](https://pypi.org/project/cli-anything-zotero/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)
[![License](https://img.shields.io/github/license/PiaoyangGuohai1/cli-anything-zotero)](LICENSE)
[![GitHub release](https://img.shields.io/github/v/release/PiaoyangGuohai1/cli-anything-zotero)](https://github.com/PiaoyangGuohai1/cli-anything-zotero/releases)
[![GitHub stars](https://img.shields.io/github/stars/PiaoyangGuohai1/cli-anything-zotero)](https://github.com/PiaoyangGuohai1/cli-anything-zotero/stargazers)

**Let AI manage your Zotero library.**

[中文文档](docs/README_zh.md) | English

> **MCP legacy notice:** `v0.9.5` is the final release with the `zotero-mcp` command and `cli-anything-zotero[mcp]` extra. New releases are CLI/SDK-first. Existing MCP users should pin `pip install "cli-anything-zotero[mcp]==0.9.5"` or use the `legacy/mcp` branch.

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

All write operations run locally through the JS Bridge — no API key or internet connection required.

---

## CLI-First Usage

`cli-anything-zotero` is now maintained as a CLI/SDK-first tool. The primary interface is the `zotero-cli` shell command, which works well for Codex, Claude Code, Cursor, shell scripts, and other agents that can run terminal commands.

For legacy MCP users, install the frozen MCP release explicitly:

```bash
pip install "cli-anything-zotero[mcp]==0.9.5"
```

The `legacy/mcp` branch and the `v0.9.5` release remain available, but MCP receives no new feature maintenance after that line.

---

## Installation

**Prerequisites:** Python 3.10+, Zotero 7/8/9 (running).

### Step 1: Install the package

```bash
pip install cli-anything-zotero
```

This installs the `zotero-cli` command. The old `cli-anything-zotero` command remains as a compatibility alias.

### Step 2: Install the JS Bridge Plugin (one-time, both modes)

```bash
zotero-cli app install-plugin
```

First install requires manual steps in Zotero:
1. The command generates a `.xpi` file and prints its path
2. In Zotero: **Tools → Plugins → gear icon → Install Plugin From File...**
3. Select the `.xpi` file, then **restart Zotero**

> After the first install, future upgrades via `app install-plugin` are automatic.

For existing users upgrading to the dynamic DOCX citation workflow, update both
the Python package and the Zotero bridge plugin:

```bash
python -m pip install -U cli-anything-zotero
zotero-cli app install-plugin
# restart Zotero
zotero-cli app plugin-status
zotero-cli docx doctor
```

### Step 3: Set up your AI client

No client-specific setup is required. Tell your AI assistant that `zotero-cli` is available; it can run `zotero-cli --help` to discover commands.

Verify it works:

```bash
zotero-cli app ping
zotero-cli js "return Zotero.version"
```

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
zotero-cli item find "keyword" --scope fields
zotero-cli item export ITEM_KEY --format bibtex
zotero-cli export bib --items KEY1,KEY2 --output refs.bib
zotero-cli item citation ITEM_KEY
zotero-cli item context ITEM_KEY              # LLM-ready context
zotero-cli docx inspect-citations draft.docx  # detect Zotero/EndNote/static citation fields
zotero-cli docx validate-placeholders draft.docx
zotero-cli docx render-citations draft.docx --output draft-static.docx --force
zotero-cli docx doctor
zotero-cli docx insert-citations draft.docx --output draft-zotero.docx --force
```

For AI-authored DOCX workflows, use Zotero-bound placeholders such as
`{{zotero:ITEMKEY}}` or `{{zotero:KEY1,KEY2}}`, then choose the final output
mode:

- Static citations: `docx render-citations` replaces placeholders with ordinary citation text and appends a static bibliography. It only needs Zotero's Local API, so it is the easiest path for lightweight reports or one-off documents. Static output cannot be refreshed by the Zotero word processor plugin.
- Dynamic citations: `docx insert-citations` converts placeholders into real Zotero/LibreOffice fields and creates or updates a refreshable bibliography field. This is the better path for theses, manuscripts, and documents that will be edited or restyled later.

AI agents should ask the user which mode they want when the request is
ambiguous. If the user only wants a simple final DOCX and has not installed
LibreOffice, prefer static citations. Dynamic DOCX citation insertion is an
optional LibreOffice-backed workflow: it requires Zotero Desktop, LibreOffice,
the Zotero LibreOffice Add-in, and the CLI Bridge plugin. Run `docx doctor`
when setting up a machine or when an AI agent needs to decide whether the
workflow is installed.

Recommended AI protocol:

1. `zotero-cli --json docx validate-placeholders <input.docx>`
2. If the user wants editable references / refresh support:
   - `zotero-cli --json docx doctor`
   - `zotero-cli --json docx insert-citations <input.docx> --output <final.docx> --force`
   - If conversion fails, report the failing layer from `doctor` and ask the user for next steps.
3. If the user wants static output or dynamic is unavailable:
   - `zotero-cli --json docx render-citations <input.docx> --output <final.docx> --force`

Keep these files only as handoff artifacts:
- Placeholder draft (`<input.docx>`)
- Final converted draft (`<final.docx>`)
- No intermediate DOCX should be exposed unless `--debug-dir` is explicitly requested.

Platform support for this optional workflow:
- macOS: tested end-to-end with automatic open, conversion, save, and Word-compatible DOCX output.
- Windows/Linux: the base CLI works, and `docx doctor` can report missing dependencies. Full automatic LibreOffice open/save for dynamic DOCX citations still needs real Windows/Linux desktop validation; users may need to open or save the LibreOffice document manually until platform automation is verified.

`validate-placeholders`, `zoterify-preflight`, and `zoterify-probe` are
diagnostics for setup or failure cases. Add `--debug-dir` only when you want
JSON artifacts for troubleshooting.
`docx prepare-zotero-import` exists only as an experimental debugging command;
it is not a supported writing workflow after Zotero 9 + LibreOffice testing.
`docx insert-citations` and `docx render-citations` are the two supported outputs
for AI-authored citation insertion.
`item citation` and `item bibliography` render static previews; they are not
refreshable Word/LibreOffice Zotero fields. BIB export is a separate export
feature and is not part of the DOCX writing workflow.

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

## Legacy MCP Users

MCP support is frozen at `v0.9.5`. To keep using the previous MCP server, install:

```bash
pip install "cli-anything-zotero[mcp]==0.9.5"
```

You can also use the `legacy/mcp` branch for source installs. Starting with `v1.0.0`, the maintained package installs only CLI/SDK surfaces and no longer provides the `zotero-mcp` command.

## Related Projects

There are several great tools in the Zotero ecosystem. Each has different strengths depending on your use case:

| | **cli-anything-zotero** | [zotero-mcp](https://github.com/54yyyu/zotero-mcp) | [zotero-cli-cc](https://github.com/Agents365-ai/zotero-cli-cc) | [pyzotero-cli](https://github.com/chriscarrollsmith/pyzotero-cli) |
|---|---|---|---|---|
| **Approach** | Local JS Bridge | Web API + MCP | Web API + CLI | Web API + CLI |
| **Best for** | Local-first, full control | MCP-native workflows | Agent-driven research | Scripting & automation |
| **Write ops** | Local (no API key) | Via Web API | Via Web API | Via Web API |
| **MCP support** | Legacy via v0.9.5 | Yes | 45 tools | No |
| **Terminal CLI** | Yes | No | Yes | Yes |
| **Zotero JS access** | Yes | No | No | No |
| **License** | Apache 2.0 | MIT | CC BY-NC 4.0 | MIT |

---

## License

[Apache 2.0](LICENSE)
