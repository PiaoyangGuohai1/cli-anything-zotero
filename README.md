# cli-anything-zotero

**Full-featured CLI for Zotero reference management** -- search, import, export, PDF management, semantic search, and more, all from the command line.

> [**中文文档**](#中文文档) | **English** | **WeChat Group 微信交流群** 👇

<img src="docs/images/wechat-group.jpg" alt="WeChat Group QR Code" width="250">

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

The CLI includes a lightweight Zotero bootstrap plugin that registers the `/cli-bridge/eval` HTTP endpoint automatically when Zotero starts. No GUI interaction needed.

```
Zotero startup → CLI Bridge plugin loads → registers POST /cli-bridge/eval

CLI call → HTTP POST to localhost:23119/cli-bridge/eval
         → Zotero executes JS in privileged context
         → returns JSON result
         → zero UI, millisecond response
```

The plugin works on **all platforms** (macOS, Windows, Linux). On macOS, an AppleScript fallback exists for users who have not yet installed the plugin (deprecated).

---

## Prerequisites

- **macOS, Windows, or Linux**
- **Zotero 7 or 8** (must be running)
- **Python 3.10+**
- **Local embedding API** (optional, for semantic search -- any OpenAI-compatible endpoint)

## Installation

### Step 1: Install Python and Git

**macOS / Linux** -- Python 3.10+ and git are usually pre-installed. If not:

```bash
# macOS
brew install python git

# Ubuntu/Debian
sudo apt install python3 python3-pip git
```

**Windows** -- Python and git are NOT pre-installed. Open PowerShell and run:

```powershell
winget install Python.Python.3.12
winget install Git.Git
```

> **Important:** After installing, you must **close and reopen PowerShell** for the `python`, `pip`, and `git` commands to become available.

### Step 2: Install the CLI Tool

```bash
git clone https://github.com/PiaoyangGuohai1/cli-anything-zotero.git
cd cli-anything-zotero
pip install -e .
```

If you don't want to install git, you can also run:

```bash
pip install https://github.com/PiaoyangGuohai1/cli-anything-zotero/archive/refs/heads/main.zip
```

### Step 3: Install the JS Bridge Plugin into Zotero (one-time)

The JS Bridge plugin is what allows the CLI to execute privileged operations in Zotero (import, PDF management, sync, etc.). **Without it, only read-only SQLite commands will work.**

```bash
# Make sure Zotero is running first, then:
cli-anything-zotero app install-plugin
```

**On first install, automatic plugin loading is NOT supported** -- Zotero 7/8 requires manual plugin installation through its UI:

1. The command above generates a `.xpi` file and prints its path
2. In Zotero, go to **Tools → Plugins** (or **Add-ons**)
3. Click the **gear icon ⚙** in the top-right → **Install Plugin From File...**
4. Navigate to and select the `.xpi` file shown in the command output
5. **Restart Zotero** after installation

> **Note:** Once the plugin is installed and active, future runs of `app install-plugin` (e.g. after upgrades) will install automatically without manual steps.

### Step 4: Verify

```bash
# Check plugin and endpoint status
cli-anything-zotero app plugin-status --json
# ✓ Should show: "plugin_installed": true, "endpoint_active": true

# Check Zotero connectivity
cli-anything-zotero app ping

# Test the JS bridge
cli-anything-zotero js "return 'Hello from Zotero!'"
```

### Troubleshooting

| Problem | Solution |
|---------|----------|
| `git`/`pip` not recognized (Windows) | Close and reopen PowerShell after installing Python/Git |
| `Cannot resolve Zotero profile directory` | Make sure Zotero has been launched at least once |
| Plugin not appearing after install | Did you restart Zotero? The plugin only loads on startup |
| `endpoint_active: false` after restart | The plugin may have failed to load -- reinstall via Zotero UI |
| `--json` flag not recognized | Put `--json` **before** the subcommand, or upgrade to the latest version which supports `--json` anywhere |

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

- **Zotero must be running** -- all backends connect to Zotero's HTTP server or read its SQLite database.
- **CLI Bridge plugin required for JS bridge commands** -- install via `cli-anything-zotero app install-plugin`. The plugin registers the `/cli-bridge/eval` endpoint on Zotero startup. On macOS, AppleScript fallback is available but deprecated.
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

CLI 包含一个轻量级 Zotero 启动插件，在 Zotero 启动时自动注册 `/cli-bridge/eval` 端点，无需 GUI 交互。

```
Zotero 启动 → CLI Bridge 插件加载 → 注册 POST /cli-bridge/eval

CLI 调用 → HTTP POST → Zotero 在特权上下文中执行 JS → 返回 JSON 结果
            零 UI 干扰，毫秒级响应
```

插件支持**所有平台**（macOS、Windows、Linux）。macOS 上保留了 AppleScript 降级方案（已弃用）。

## 前置条件

- **macOS、Windows 或 Linux**
- **Zotero 7 或 8**（必须运行中）
- **Python 3.10+**
- **本地嵌入 API**（可选，语义搜索用）

## 安装

### 第一步：安装 Python 和 Git

**macOS / Linux** -- 通常已预装。如果没有：

```bash
# macOS
brew install python git

# Ubuntu/Debian
sudo apt install python3 python3-pip git
```

**Windows** -- Python 和 Git 默认未安装，打开 PowerShell 运行：

```powershell
winget install Python.Python.3.12
winget install Git.Git
```

> **重要：** 安装完成后必须**关闭并重新打开 PowerShell**，`python`、`pip`、`git` 命令才会生效。

### 第二步：安装 CLI 工具

```bash
git clone https://github.com/PiaoyangGuohai1/cli-anything-zotero.git
cd cli-anything-zotero
pip install -e .
```

不想装 git 也可以直接：`pip install https://github.com/PiaoyangGuohai1/cli-anything-zotero/archive/refs/heads/main.zip`

### 第三步：安装 JS 桥插件到 Zotero（一次性操作）

JS 桥插件让 CLI 能在 Zotero 中执行特权操作（导入、PDF 管理、同步等）。**不装插件的话，只有 SQLite 只读命令能用。**

```bash
# 确保 Zotero 正在运行，然后：
cli-anything-zotero app install-plugin
```

**首次安装需要手动操作** -- Zotero 7/8 不支持自动加载外部插件，必须通过 UI 安装：

1. 上面的命令会生成一个 `.xpi` 文件并显示其路径
2. 在 Zotero 中打开 **工具 → 插件**
3. 点击右上角 **齿轮图标 ⚙** → **Install Plugin From File...**
4. 选择命令输出中显示的 `.xpi` 文件
5. **重启 Zotero**

> **提示：** 插件装好激活后，以后再运行 `app install-plugin`（如升级版本时）会自动安装，无需手动操作。

### 第四步：验证

```bash
# 检查插件和端点状态
cli-anything-zotero app plugin-status --json
# ✓ 应显示: "plugin_installed": true, "endpoint_active": true

# 检查 Zotero 连接
cli-anything-zotero app ping

# 测试 JS 桥
cli-anything-zotero js "return 'Hello from Zotero!'"
```

### 常见问题

| 问题 | 解决方案 |
|------|----------|
| Windows 上 `git`/`pip` 提示"无法识别" | 安装 Python/Git 后必须**重新打开 PowerShell** |
| `Cannot resolve Zotero profile directory` | 确保 Zotero 至少启动过一次 |
| 安装插件后在 Zotero 中看不到 | 是否重启了 Zotero？插件只在启动时加载 |
| `endpoint_active: false` | 插件可能加载失败，通过 Zotero UI 重新安装 .xpi |

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

- **Zotero 必须运行** -- 所有后端连接 Zotero 的 HTTP 服务或读取其 SQLite 数据库
- **JS 桥命令需安装 CLI Bridge 插件** -- 运行 `cli-anything-zotero app install-plugin` 安装，插件在 Zotero 启动时自动注册端点。macOS 上保留 AppleScript 降级（已弃用）
- **语义搜索需配置** -- 需要本地嵌入 API 和预建向量索引

## 许可证

Apache 2.0 -- 与上游 [CLI-Anything](https://github.com/HKUDS/CLI-Anything) 项目一致。
