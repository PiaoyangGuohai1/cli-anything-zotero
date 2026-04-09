# cli-anything-zotero 中文文档

**让 AI 帮你管理 Zotero 文献库。**

基于 [CLI-Anything](https://github.com/HKUDS/CLI-Anything) 框架。专为 AI Agent 和高级用户设计。

**微信交流群** 👇

<img src="images/wechat-group.jpg" alt="微信群二维码" width="250">

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
1. 按照下面的安装步骤装好（Python + 一个 Zotero 插件，3 分钟）
2. 打开你的 AI 工具（Claude Code、Cursor 等），用自然语言说你想做什么
3. 没了

---

## 选择你的模式

本工具支持两种模式，**选择适合你 AI 客户端的那个：**

| | CLI 模式 | MCP 模式 |
|---|---|---|
| **AI 怎么调用** | Shell 命令（`cli-anything-zotero item find ...`） | 结构化工具调用（不需要命令行） |
| **适配平台** | 任何能跑 shell 的 AI（Claude Code、ChatGPT、Cursor、Windsurf、Cline 等） | 支持 MCP 的 AI 客户端（Claude Desktop、Cursor、Claude Code、LM Studio 等） |
| **AI 学习成本** | AI 运行一次 `--help` 即可学会全部 70+ 命令 | 零 — 52 个工具自动注册，参数有类型约束 |
| **出错率** | AI 偶尔拼错命令（会自动纠正） | 接近零（参数有类型约束） |
| **安装** | `pip install cli-anything-zotero` | `pip install 'cli-anything-zotero[mcp]'` + 客户端配置 |

> **不确定选哪个？** 如果你的 AI 客户端支持 MCP，选 MCP — 更可靠。否则，CLI 在所有平台都能用。

---

## 安装

**前提：** Python 3.10+，Zotero 7/8（运行中）。

### 第一步：安装

**CLI 模式**（适用于所有 AI 助手）：
```bash
pip install cli-anything-zotero
```

**MCP 模式**（适用于 Claude Desktop、Cursor、Claude Code 等）：
```bash
pip install 'cli-anything-zotero[mcp]'
```

> 两种模式在同一个包里。`[mcp]` 只是额外安装 MCP 协议依赖。

### 第二步：安装 JS Bridge 插件（一次性操作，两种模式都需要）

```bash
cli-anything-zotero app install-plugin
```

首次安装需要在 Zotero 中手动导入：
1. 上面的命令会生成一个 `.xpi` 文件并显示路径
2. 在 Zotero 中：**工具 -> 插件 -> 齿轮图标 -> Install Plugin From File...**
3. 选择 `.xpi` 文件，**重启 Zotero**

> 装好后以后升级都是自动的。

### 第三步：配置你的 AI 客户端

<details>
<summary><b>CLI 模式 — 无需额外配置</b></summary>

告诉你的 AI 助手这个工具可用即可。AI 会运行 `cli-anything-zotero --help` 自动发现所有命令。

验证安装：
```bash
cli-anything-zotero app ping
cli-anything-zotero js "return Zotero.version"
```
</details>

<details>
<summary><b>MCP 模式 — 配置 AI 客户端</b></summary>

**Claude Code：**
```bash
claude mcp add zotero --scope user -- cli-anything-zotero mcp serve
```

**Claude Desktop / Cursor / LM Studio** — 在 MCP 配置文件中添加：
```json
{
  "mcpServers": {
    "zotero": {
      "command": "cli-anything-zotero",
      "args": ["mcp", "serve"]
    }
  }
}
```

重启 AI 客户端后，52 个 Zotero 工具将自动可用。

完整 MCP 参考：**[MCP.md](MCP.md)**
</details>

### 常见问题

| 问题 | 解决方案 |
|------|----------|
| `Cannot resolve Zotero profile directory` | 先启动一次 Zotero |
| 插件没显示 | 安装 `.xpi` 后重启 Zotero |
| `endpoint_active: false` | 插件加载失败 — 通过 Zotero UI 重新安装 |
| Windows: `pip` 不识别 | 安装 Python 后关闭并重新打开 PowerShell |

---

## 用法（CLI 模式）

**搜索与浏览**
```bash
cli-anything-zotero item find "机器学习"
cli-anything-zotero item search-fulltext "CRISPR"
cli-anything-zotero collection tree
```

**导入**
```bash
cli-anything-zotero import doi "10.1038/s41586-024-07871-6" --tag "综述"
cli-anything-zotero import pmid "37821702" --collection FMTCPUWN
cli-anything-zotero import file ./refs.ris
```

**读取与导出**
```bash
cli-anything-zotero item get ITEM_KEY
cli-anything-zotero item export ITEM_KEY --format bibtex
cli-anything-zotero item citation ITEM_KEY
cli-anything-zotero item context ITEM_KEY              # LLM 友好格式
```

**写入与管理**
```bash
cli-anything-zotero item update KEY --field title="新标题"
cli-anything-zotero item tag KEY --add "重要"
cli-anything-zotero item attach KEY ./论文.pdf
cli-anything-zotero item find-pdf KEY
cli-anything-zotero note add KEY --text "我的笔记"
cli-anything-zotero sync
```

**高级功能**
```bash
cli-anything-zotero item search-annotations "风险"
cli-anything-zotero item annotations KEY
cli-anything-zotero item metrics KEY                   # NIH 引用指标
cli-anything-zotero collection stats COLLECTION_KEY
cli-anything-zotero js "return await Zotero.Items.getAll(1).then(i => i.length)"
```

---

## 可选功能

| 功能 | 需要 | 命令 |
|------|------|------|
| 语义搜索 | 嵌入 API（Ollama/LM Studio 等） | `item semantic-search`, `item similar`, `item build-index` |
| AI 分析 | `OPENAI_API_KEY` | `item analyze` |

---

## 升级到 0.4.0

**MCP 用户注意：** 所有 MCP 工具名已重命名为 `group_action` 格式以匹配 CLI 结构。详见英文 README 的 [Upgrading to 0.4.0](../README.md#upgrading-to-040) 部分。

**CLI 用户：** 无破坏性变更。`--help` 现在一次性显示所有命令。

---

## 许可证

[Apache 2.0](../LICENSE)
