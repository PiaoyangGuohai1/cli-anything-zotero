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

## 为什么需要这个工具？

Zotero 内置 HTTP 服务只为浏览器扩展设计，无法添加 PDF、更新元数据、触发同步或全文搜索。本工具通过 **JS Bridge** 填补这些空缺——零弹窗、毫秒级响应。

## 安装

**前提：** Python 3.10+，Zotero 7/8（运行中）。无需其他系统工具。

### 第一步：安装 CLI

```bash
pip install cli-anything-zotero
```

### 第二步：安装 JS Bridge 插件（一次性操作）

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

## MCP 服务器

```bash
pip install 'cli-anything-zotero[mcp]'
zotero-cli mcp serve
```

客户端配置（Claude Desktop / Cursor / LM Studio）：

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

## 可选功能

| 功能 | 需要 | 命令 |
|------|------|------|
| 语义搜索 | 嵌入 API（Ollama/LM Studio 等） | `item semantic-search`, `item similar`, `item build-index` |
| AI 分析 | `OPENAI_API_KEY` | `item analyze` |

## 完整命令参考

详见 **[COMMANDS.md](COMMANDS.md)**。

## 许可证

Apache 2.0
