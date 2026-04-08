# cli-anything-zotero

[![PyPI](https://img.shields.io/pypi/v/cli-anything-zotero?color=blue)](https://pypi.org/project/cli-anything-zotero/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)
[![License](https://img.shields.io/github/license/PiaoyangGuohai1/cli-anything-zotero)](LICENSE)
[![GitHub release](https://img.shields.io/github/v/release/PiaoyangGuohai1/cli-anything-zotero)](https://github.com/PiaoyangGuohai1/cli-anything-zotero/releases)
[![GitHub stars](https://img.shields.io/github/stars/PiaoyangGuohai1/cli-anything-zotero)](https://github.com/PiaoyangGuohai1/cli-anything-zotero/stargazers)

**Let AI manage your Zotero library.**

> [**中文文档**](#中文文档) | **English** | **WeChat Group** 👇

<img src="docs/images/wechat-group.jpg" alt="WeChat Group QR Code" width="250">

---

## For Non-Programmers: You Don't Need to Read This Whole Page

This tool is designed to be **used by AI, not memorized by you**. After a simple install (takes ~3 minutes), you just talk to your AI assistant in plain language:

> "Help me find papers about diabetes and kidney disease in my Zotero library"
>
> "Import this DOI into my CKM collection: 10.1038/s41586-024-07871-6"
>
> "Export all papers in my thesis collection as BibTeX"
>
> "Find PDFs for all items in my review collection that are missing them"
>
> "Summarize the key findings of this paper: ITEM_KEY"

The AI reads the command reference automatically -- you never need to. Just install it and start asking.

**All you need to do:**
1. Follow the [Installation](#installation) steps below (Python + one Zotero plugin)
2. Tell your AI assistant (Claude Code, Cursor, etc.) what you need
3. That's it

---

## 对非程序员朋友的说明

这个工具**不需要你记住任何命令**，安装完之后直接用中文告诉 AI 你想做什么就行：

> "帮我在 Zotero 里搜一下关于糖尿病和肾病的文献"
>
> "把这个 DOI 导入到我的 CKM 合集里：10.1038/s41586-024-07871-6"
>
> "把毕业论文合集里的文献全部导出成 BibTeX"
>
> "帮我找一下综述合集里缺 PDF 的文献，自动下载"
>
> "总结一下这篇文章的主要发现"

**你只需要：**
1. 按照下面的[安装步骤](#中文文档)装好（Python + 一个 Zotero 插件，3 分钟）
2. 打开你的 AI 工具（Claude Code、Cursor 等），用自然语言说你想做什么
3. 没了

---

Built on [CLI-Anything](https://github.com/HKUDS/CLI-Anything) by [HKUDS](https://github.com/HKUDS). Designed for AI agents (Claude Code, Cursor, Codex, etc.) and power users.

## Why This Tool?

Zotero's built-in HTTP server was designed for browser extensions, not for AI agents or CLI workflows. It has no API for attaching PDFs, updating metadata, triggering sync, or running full-text search.

This CLI fills those gaps through a **JS Bridge** -- a lightweight Zotero plugin that exposes a privileged JavaScript endpoint. Zero UI popup, millisecond response.

---

## Installation

**Prerequisites:** Python 3.10+, Zotero 7/8 (running). No other system tools needed.

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
2. In Zotero: **Tools -> Plugins -> gear icon -> Install Plugin From File...**
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
| `endpoint_active: false` | Plugin failed to load -- reinstall via Zotero UI |
| Windows: `pip` not recognized | Close and reopen PowerShell after installing Python |

---

## Core Features

Everything below works out of the box after installation. No extra services needed.

**Search & Browse**
```bash
cli-anything-zotero item find "machine learning"      # keyword search
cli-anything-zotero item search-fulltext "CRISPR"      # search inside PDFs
cli-anything-zotero collection tree                     # browse collection hierarchy
```

**Import**
```bash
cli-anything-zotero import doi "10.1038/s41586-024-07871-6" --tag "review"
cli-anything-zotero import pmid "37821702" --collection FMTCPUWN
cli-anything-zotero import file ./refs.ris
```

**Read & Export**
```bash
cli-anything-zotero item get ITEM_KEY                   # full metadata
cli-anything-zotero item export ITEM_KEY --format bibtex
cli-anything-zotero item citation ITEM_KEY               # formatted citation
cli-anything-zotero item context ITEM_KEY                # LLM-ready context
```

**Write & Manage**
```bash
cli-anything-zotero item update KEY --field title="New Title"
cli-anything-zotero item tag KEY --add "important"
cli-anything-zotero item attach KEY ./paper.pdf
cli-anything-zotero item find-pdf KEY                    # auto-find PDF online
cli-anything-zotero note add KEY --text "My note"
cli-anything-zotero sync
```

**Advanced**
```bash
cli-anything-zotero item search-annotations "risk"       # search all highlights
cli-anything-zotero item annotations KEY                  # view PDF annotations
cli-anything-zotero item metrics KEY                      # NIH citation metrics
cli-anything-zotero collection stats COLLECTION_KEY       # collection statistics
cli-anything-zotero js "return await Zotero.Items.getAll(1).then(i => i.length)"
```

---

## Optional Features

These require extra services. Everything else works without them.

### Semantic Search -- requires an embedding API

Any OpenAI-compatible `/v1/embeddings` endpoint ([Ollama](https://ollama.com), [LM Studio](https://lmstudio.ai), OpenAI, etc.).

```bash
# 1. Build the vector index (one-time)
cli-anything-zotero item build-index

# 2. Search
cli-anything-zotero item semantic-search "cardiovascular risk prediction"
cli-anything-zotero item similar ITEM_KEY
```

| Variable | Default | Description |
|----------|---------|-------------|
| `ZOTERO_EMBED_API` | `http://127.0.0.1:8080/v1/embeddings` | Embedding API endpoint |
| `ZOTERO_EMBED_MODEL` | `nomic-embed-text` | Model name |
| `ZOTERO_EMBED_KEY` | *(empty)* | API key (if needed) |

### AI Analysis -- requires OpenAI API key

```bash
export OPENAI_API_KEY=sk-...
cli-anything-zotero item analyze ITEM_KEY --question "What are the main findings?"
```

---

## Full Command Reference

40+ commands across 12 groups. See **[docs/COMMANDS.md](docs/COMMANDS.md)** for the complete reference.

---

## License

Apache 2.0 -- same as [CLI-Anything](https://github.com/HKUDS/CLI-Anything).

---

<a id="中文文档"></a>

# 中文文档

**让 AI 帮你管理 Zotero 文献库。**

基于 [CLI-Anything](https://github.com/HKUDS/CLI-Anything) 框架。专为 AI Agent 和高级用户设计。

## 为什么需要这个工具？

Zotero 内置 HTTP 服务只为浏览器扩展设计，无法添加 PDF、更新元数据、触发同步或全文搜索。本工具通过 **JS 桥**填补这些空缺 -- 零弹窗、毫秒级响应。

## 安装

**前提：** Python 3.10+，Zotero 7/8（运行中）。无需其他系统工具。

### 第一步：安装 CLI

```bash
pip install cli-anything-zotero
```

### 第二步：安装 JS 桥插件（一次性操作）

```bash
cli-anything-zotero app install-plugin
```

首次安装需要在 Zotero 中手动导入：
1. 上面的命令会生成一个 `.xpi` 文件并显示路径
2. 在 Zotero 中：**工具 -> 插件 -> 齿轮图标 -> Install Plugin From File...**
3. 选择 `.xpi` 文件，**重启 Zotero**

> 装好后以后升级都是自动的。

### 第三步：验证

```bash
cli-anything-zotero app plugin-status --json
cli-anything-zotero app ping
```

## 核心功能

安装完成后开箱即用，无需额外服务。

```bash
# 搜索
cli-anything-zotero item find "机器学习"
cli-anything-zotero item search-fulltext "CRISPR"
cli-anything-zotero collection tree

# 导入
cli-anything-zotero import doi "10.1038/s41586-024-07871-6" --tag "综述"
cli-anything-zotero import pmid "37821702"

# 读取与导出
cli-anything-zotero item get ITEM_KEY
cli-anything-zotero item export ITEM_KEY --format bibtex
cli-anything-zotero item context ITEM_KEY            # LLM 友好格式

# 写入与管理
cli-anything-zotero item update KEY --field title="新标题"
cli-anything-zotero item attach KEY ./论文.pdf
cli-anything-zotero note add KEY --text "我的笔记"
cli-anything-zotero sync
```

## 可选功能

| 功能 | 需要 | 命令 |
|------|------|------|
| 语义搜索 | 嵌入 API（Ollama/LM Studio 等） | `item semantic-search`, `item similar`, `item build-index` |
| AI 分析 | `OPENAI_API_KEY` | `item analyze` |

## 完整命令参考

40+ 命令，12 个分组。详见 **[docs/COMMANDS.md](docs/COMMANDS.md)**。

## 许可证

Apache 2.0
