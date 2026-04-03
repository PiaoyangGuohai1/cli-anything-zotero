# cli-anything-zotero

**Full-featured CLI for Zotero reference management** -- search, import, export, PDF management, semantic search, and more, all from the command line.

> [**中文文档**](#中文文档) | **English**

Built on the [CLI-Anything](https://github.com/HKUDS/CLI-Anything) framework by [HKUDS](https://github.com/HKUDS). Designed for AI agents (Claude Code, Cursor, Codex, etc.) and power users who need programmatic access to a running Zotero instance without touching the GUI.

---

## Why This Tool?

Zotero's built-in HTTP server (port 23119) was designed for browser extensions, not for AI agents or CLI workflows. It is **read-only** for most operations and has no API for:

- Attaching PDFs to existing items
- Triggering "Find Available PDF"
- Updating metadata on existing items
- Semantic search across your library

This CLI solves these gaps through a **JavaScript bridge** -- a lightweight HTTP endpoint injected into Zotero's internal server that can execute any privileged Zotero JS operation, with zero UI popup and millisecond response time.

## Architecture

Four backend layers, auto-selected per command:

| Layer | Endpoint | Operations | Speed |
|-------|----------|-----------|-------|
| **SQLite** | `~/Zotero/zotero.sqlite` (read-only) | Search, list, get, export metadata | Instant |
| **Connector API** | `localhost:23119/connector/*` | Import items + attachments | ~1s |
| **Local API** | `localhost:23119/api/*` | Citation rendering, BibTeX export | ~1s |
| **JS Bridge** | `localhost:23119/cli-bridge/eval` | Everything else (attach PDF, find PDF, update, tags, sync, semantic search) | ~0.5s |

### How the JS Bridge Works

```
First call:  AppleScript opens Zotero's "Run JavaScript" dialog (one-time popup)
             → registers POST /cli-bridge/eval endpoint into Zotero's HTTP server
             → closes dialog

All subsequent calls:  HTTP POST to localhost:23119/cli-bridge/eval
                       → Zotero executes JS in privileged context
                       → returns JSON result
                       → zero UI, millisecond response
```

The endpoint lives in memory. It auto-re-registers on Zotero restart (first call triggers it).

---

## Prerequisites

- **macOS** (required for AppleScript fallback in JS bridge)
- **Zotero 7 or 8** (must be running)
- **Python 3.10+**
- **Local embedding API** (optional, for semantic search -- any OpenAI-compatible endpoint)

## Installation

```bash
git clone https://github.com/PiaoyangGuohai1/cli-anything-zotero.git
cd cli-anything-zotero
pip install -e .

# Verify
cli-anything-zotero app ping
```

## Quick Start

```bash
# Search your library
cli-anything-zotero item find "machine learning"

# Import a paper by DOI
cli-anything-zotero import doi "10.1038/s41586-024-07871-6" --tag "review"

# Export BibTeX
cli-anything-zotero item export 9LPV3KTS --format bibtex

# Semantic search (requires embedding API)
cli-anything-zotero item semantic-search "cardiovascular risk prediction"

# Collection statistics
cli-anything-zotero --json collection stats IPR57X6F

# Attach a PDF to an existing item
cli-anything-zotero item attach ITEM_KEY ./paper.pdf

# Find available PDFs for a whole collection
cli-anything-zotero collection find-pdfs COLLECTION_KEY

# Trigger Zotero sync
cli-anything-zotero sync
```

---

## Command Reference

### app -- Application Status

| Command | Description |
|---------|-------------|
| `app ping` | Check if Zotero is running |
| `app status` | Runtime and backend status |
| `app version` | Package and Zotero version |
| `app launch` | Launch Zotero if not running |
| `app enable-local-api` | Enable Local API in Zotero prefs |

### item -- Item Operations (26 commands)

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
| `item search-fulltext <query>` | Search inside PDF text | JS Bridge |
| `item search-annotations <query>` | Search highlights/notes across library | JS Bridge |
| `item semantic-search <query>` | AI semantic search (embedding-based) | Embedding API + SQLite |
| `item similar <key>` | Find similar items | Embedding API + SQLite |
| `item annotations <key>` | View annotations for an item | JS Bridge |
| `item metrics <ref> [--pmid]` | NIH iCite citation metrics | iCite API |
| `item duplicates` | Find duplicate items | JS Bridge |
| `item export <ref> --format bibtex` | Export RIS/BibTeX/CSL JSON/CSV | Local API |
| `item citation <ref>` | Render inline citation | Local API |
| `item bibliography <ref>` | Render bibliography entry | Local API |
| `item analyze <ref> --question Q` | AI analysis (requires OpenAI key) | SQLite + OpenAI |
| `item update <key> --field k=v` | Update metadata fields | JS Bridge |
| `item tag <key> --add/--remove` | Add or remove tags | JS Bridge |
| `item attach <key> <pdf_path>` | Attach local PDF to item | JS Bridge |
| `item find-pdf <key>` | Trigger "Find Available PDF" | JS Bridge |
| `item delete <key> --confirm` | Delete item permanently | JS Bridge |
| `item add-to-collection <ref> <col>` | Add item to collection | SQLite (experimental) |
| `item move-to-collection <ref> <col>` | Move item between collections | SQLite (experimental) |

### collection -- Collection Management (12 commands)

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

### import -- Import Items (4 commands)

| Command | Description | Backend |
|---------|-------------|---------|
| `import doi <doi> [--tag T] [--collection K]` | Import by DOI with auto-metadata | JS Bridge |
| `import pmid <pmid> [--tag T] [--collection K]` | Import by PMID with auto-metadata | JS Bridge |
| `import file <path> [--collection K]` | Import from RIS/BibTeX file | Connector |
| `import json <path> [--collection K]` | Import from JSON | Connector |

### Other Commands

| Command | Description |
|---------|-------------|
| `sync` | Trigger Zotero sync |
| `js <code> [--wait N]` | Execute arbitrary Zotero JavaScript |
| `tag list` | List all tags |
| `tag items <tag>` | Items with a specific tag |
| `note get <ref>` | Read a note |
| `note add <ref> --text T` | Add note to item |
| `search list` | List saved searches |
| `search items <ref>` | Run a saved search |
| `style list` | List installed CSL citation styles |
| `repl` | Interactive REPL mode |

---

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `ZOTERO_LOCALE` | `en` | Zotero UI language (`en` or `zh`) for AppleScript menu navigation |
| `ZOTERO_EMBED_API` | `http://127.0.0.1:8080/v1/embeddings` | OpenAI-compatible embedding API endpoint |
| `ZOTERO_EMBED_MODEL` | `nomic-embed-text` | Embedding model name |
| `ZOTERO_EMBED_KEY` | *(empty)* | API key for embedding service |
| `ZOTERO_VECTOR_DB` | `~/Zotero/zotero-mcp-vectors.sqlite` | Path to vector database for semantic search |

### Semantic Search Setup

Semantic search requires two things:

1. **An embedding API** -- any OpenAI-compatible `/v1/embeddings` endpoint. Options:
   - [Ollama](https://ollama.com) with `nomic-embed-text`
   - [LM Studio](https://lmstudio.ai)
   - [OMLX](https://github.com/nickhardware/omlx) or any local embedding server
   - OpenAI API (`ZOTERO_EMBED_API=https://api.openai.com/v1/embeddings`)

2. **A pre-built vector index** -- the tool reads from an existing SQLite vector database (768-dimensional vectors). The [zotero-mcp plugin](https://github.com/SilentEchoes77/zotero-mcp-plugin) can build this index automatically.

## Limitations

- **macOS only** -- the JS bridge fallback uses AppleScript. Linux support is possible (via `xdotool`) but not yet implemented.
- **Zotero must be running** -- all backends connect to Zotero's HTTP server or read its SQLite database.
- **JS bridge is session-scoped** -- the `/cli-bridge/eval` endpoint lives in memory and is lost on Zotero restart. The first CLI call after restart automatically re-registers it (one brief UI popup).
- **Semantic search requires setup** -- needs a local embedding API and pre-built vector index.

## License

Apache 2.0 -- same as the parent [CLI-Anything](https://github.com/HKUDS/CLI-Anything) project.

---

<a id="中文文档"></a>

# 中文文档

## 简介

**cli-anything-zotero** 是一个全功能的 Zotero 文献管理命令行工具 -- 搜索、导入、导出、PDF 管理、语义搜索，全部通过命令行完成。

基于 [HKUDS](https://github.com/HKUDS) 的 [CLI-Anything](https://github.com/HKUDS/CLI-Anything) 框架构建。专为 AI Agent（Claude Code、Cursor、Codex 等）和高级用户设计，无需 GUI 即可操控 Zotero。

## 为什么需要这个工具？

Zotero 的内置 HTTP 服务（端口 23119）是为浏览器扩展设计的，不是为 AI Agent 设计的。它**只读**，且没有以下 API：

- 给已有条目添加 PDF 附件
- 触发「查找可用的 PDF」
- 更新已有条目的元数据
- 语义搜索文献库

本工具通过 **JavaScript 桥** 解决这些问题 -- 在 Zotero 内置 HTTP 服务中注入一个轻量级端点，可执行任意 Zotero JS 操作，零弹窗、毫秒级响应。

## 架构

四层后端，按命令自动选择：

| 层 | 端点 | 操作 |
|----|------|------|
| **SQLite** | `~/Zotero/zotero.sqlite`（只读） | 搜索、列表、详情、导出元数据 |
| **Connector API** | `localhost:23119/connector/*` | 导入条目+附件 |
| **Local API** | `localhost:23119/api/*` | 引文渲染、BibTeX 导出 |
| **JS 桥** | `localhost:23119/cli-bridge/eval` | 其他所有操作 |

### JS 桥工作原理

```
首次调用：AppleScript 打开 Zotero「运行 JavaScript」对话框（一次性弹窗）
          → 注册 POST /cli-bridge/eval 端点到 Zotero HTTP 服务
          → 关闭对话框

后续所有调用：HTTP POST → Zotero 在特权上下文中执行 JS → 返回 JSON 结果
              零 UI 干扰，毫秒级响应
```

## 前置条件

- **macOS**（AppleScript 依赖）
- **Zotero 7 或 8**（必须运行中）
- **Python 3.10+**
- **本地嵌入 API**（可选，语义搜索用）

## 安装

```bash
git clone https://github.com/PiaoyangGuohai1/cli-anything-zotero.git
cd cli-anything-zotero
pip install -e .

# 验证
cli-anything-zotero app ping
```

## 快速上手

```bash
# 搜索文献
cli-anything-zotero item find "机器学习"

# 通过 DOI 导入
cli-anything-zotero import doi "10.1038/s41586-024-07871-6" --tag "综述"

# 导出 BibTeX
cli-anything-zotero item export ITEM_KEY --format bibtex

# 语义搜索（需要嵌入 API）
cli-anything-zotero item semantic-search "心血管风险预测"

# 集合统计
cli-anything-zotero --json collection stats COLLECTION_KEY

# 给条目添加 PDF
cli-anything-zotero item attach ITEM_KEY ./论文.pdf

# 批量查找集合内缺失的 PDF
cli-anything-zotero collection find-pdfs COLLECTION_KEY

# 同步
cli-anything-zotero sync
```

## 命令一览

共 **40+ 命令**，覆盖 Zotero 的全部常用操作：

| 分类 | 命令数 | 示例 |
|------|--------|------|
| 搜索 | 6 | `item find`, `item search-fulltext`, `item semantic-search`, `item similar`, `item search-annotations`, `collection find` |
| 读取 | 9 | `item get`, `item children`, `item attachments`, `item annotations`, `item metrics`, `item duplicates`, `collection items`, `collection stats`, `tag list` |
| 写入 | 7 | `item update`, `item tag`, `item attach`, `item find-pdf`, `item delete`, `collection rename`, `collection delete` |
| 导入 | 4 | `import doi`, `import pmid`, `import file`, `import json` |
| 导出 | 3 | `item export`, `item citation`, `item bibliography` |
| 集合 | 12 | `collection list/find/tree/get/items/create/stats/find-pdfs/remove-item/rename/delete/use-selected` |
| 工具 | 4 | `sync`, `js`, `repl`, `app ping` |

完整命令参考请见英文文档的 [Command Reference](#command-reference) 部分。

## 配置

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `ZOTERO_LOCALE` | `en` | Zotero 界面语言（`en` 或 `zh`） |
| `ZOTERO_EMBED_API` | `http://127.0.0.1:8080/v1/embeddings` | 嵌入 API 地址 |
| `ZOTERO_EMBED_MODEL` | `nomic-embed-text` | 嵌入模型名 |
| `ZOTERO_EMBED_KEY` | *（空）* | 嵌入 API 密钥 |
| `ZOTERO_VECTOR_DB` | `~/Zotero/zotero-mcp-vectors.sqlite` | 向量数据库路径 |

### 语义搜索配置

需要两样东西：
1. **嵌入 API** -- 任何兼容 OpenAI `/v1/embeddings` 的服务（Ollama、LM Studio、OMLX 等）
2. **预建向量索引** -- 工具读取现有的 SQLite 向量数据库（768 维），可通过 [zotero-mcp 插件](https://github.com/SilentEchoes77/zotero-mcp-plugin) 自动建立

## 限制

- **仅 macOS** -- JS 桥依赖 AppleScript，Linux 支持可行（xdotool）但未实现
- **Zotero 必须运行** -- 所有后端连接 Zotero 的 HTTP 服务或读取其 SQLite 数据库
- **JS 桥仅当次会话有效** -- Zotero 重启后首次 CLI 调用自动重新注册（短暂弹窗一次）
- **语义搜索需配置** -- 需要本地嵌入 API 和预建向量索引

## 许可证

Apache 2.0 -- 与上游 [CLI-Anything](https://github.com/HKUDS/CLI-Anything) 项目一致。
