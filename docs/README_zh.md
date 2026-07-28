# cli-anything-zotero 中文文档

**让 AI 帮你管理 Zotero 文献库。**

基于 [CLI-Anything](https://github.com/HKUDS/CLI-Anything) 框架。专为 AI Agent 和高级用户设计。

> **MCP legacy 提示：** `v0.9.5` 是最后一个包含 `zotero-mcp` 命令和 `cli-anything-zotero[mcp]` extra 的版本。后续版本以 CLI/SDK 为主。仍需要 MCP 的用户请固定安装 `pip install "cli-anything-zotero[mcp]==0.9.5"`，或使用 `legacy/mcp` 分支。

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

> **注意：** 日常管文献（搜、导入、导出、挂 PDF）只需要上面这些。  
> **动态 DOCX 引用**（可在 LibreOffice/Word 里 Refresh 的 Zotero 字段）还要额外装软件，见下一节。

---

## DOCX 引用：静态 vs 动态

AI 写稿时请在 DOCX 里插入占位符 `{{zotero:ITEMKEY}}` 或 `{{zotero:KEY1,KEY2}}`，再用 `docx` 命令转换成最终引用。

| 模式 | 命令 | 输出 | 是否要额外软件 |
|------|------|------|----------------|
| **静态**（简单终稿默认） | `docx render-citations` 或 `docx cite --mode static` | 普通文字引用 + 静态参考文献 | **不用。** 装好 pip 包 + JS Bridge + 打开 Zotero 即可 |
| **动态**（可刷新字段） | `docx insert-citations` 或 `docx cite --mode dynamic` | 真正的 Zotero 字段 + 可 Refresh 参考文献 | **要。** 见下表 |
| **自动** | `docx cite --mode auto` | 环境齐则动态，否则静态 | 能动态时同动态要求 |

**动态模式不是 `pip install` 就自带的**，还需要：

| 依赖 | 作用 |
|------|------|
| Zotero 桌面端（运行中） | 文献库 + 字处理集成 |
| CLI Bridge 插件（`zotero-cli app install-plugin`） | 本地转换桥 |
| [LibreOffice](https://www.libreoffice.org/) | 打开/保存 DOCX 并写入字段 |
| [Zotero 的 LibreOffice 插件](https://www.zotero.org/support/word_processor_plugin_installation) | 生成可刷新的引用/参考文献字段 |

安装或换机器后先检查：

```bash
zotero-cli --json docx doctor
```

`doctor` 若提示缺 LibreOffice / LO 插件 / Bridge，请改用**静态**模式，或把缺的组件装齐。  
macOS 上动态全流程已测通；Windows/Linux 上 `doctor` 可用，但自动打开/保存 LibreOffice 仍可能需人工操作。

不确定用哪种时：

```bash
zotero-cli --json docx cite draft.docx --output draft-cited.docx --mode auto --force
```

---

## CLI 优先使用方式

`cli-anything-zotero` 现在以 CLI/SDK 为主要入口。推荐让 Codex、Claude Code、Cursor 或其他能运行 shell 的 AI 工具直接调用 `zotero-cli`。

仍需要 MCP 的用户请固定安装最后支持版本：

```bash
pip install "cli-anything-zotero[mcp]==0.9.5"
```

`legacy/mcp` 分支和 `v0.9.5` release 会保留，但后续 MCP 不再接收功能更新。

---

## 安装

**前提：** Python 3.10+，Zotero 7/8/9（运行中）。

### 第一步：安装

```bash
pip install cli-anything-zotero
```

这会安装 `zotero-cli` 命令。旧的 `cli-anything-zotero` 命令会继续作为兼容别名保留。

### 第二步：安装 JS Bridge 插件（一次性操作，两种模式都需要）

```bash
zotero-cli app install-plugin
```

首次安装需要在 Zotero 中手动导入：
1. 上面的命令会生成一个 `.xpi` 文件并显示路径
2. 在 Zotero 中：**工具 -> 插件 -> 齿轮图标 -> Install Plugin From File...**
3. 选择 `.xpi` 文件，**重启 Zotero**

> 装好后以后升级都是自动的。

已有旧版本的用户，如果要使用“占位符 -> 动态 DOCX 引用”能力，需要同时更新
Python 包和 Zotero Bridge 插件：

```bash
python -m pip install -U cli-anything-zotero
zotero-cli app install-plugin
# 重启 Zotero
zotero-cli app plugin-status
zotero-cli docx doctor
```

### 第三步：配置你的 AI 客户端

无需额外客户端配置。告诉你的 AI 助手 `zotero-cli` 可用即可，AI 可以通过 `zotero-cli --help` 自动发现所有命令。

验证安装：

```bash
zotero-cli app ping
zotero-cli js "return Zotero.version"
```

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
zotero-cli item find "机器学习"
zotero-cli item search-fulltext "CRISPR"
zotero-cli collection tree
```

**导入**
```bash
zotero-cli import doi "10.1038/s41586-024-07871-6" --tag "综述"
# DOI：库内去重 → Zotero translator → Crossref BibTeX 回退
zotero-cli import doi "10.1038/s41586-024-07871-6" --no-translator
zotero-cli collection find-pdfs COLLECTION_KEY --timeout-per-item 45
zotero-cli import pmid "37821702" --collection FMTCPUWN
zotero-cli import file ./refs.ris
```

**读取与导出**
```bash
zotero-cli item get ITEM_KEY
zotero-cli item find "关键词" --scope fields
zotero-cli item export ITEM_KEY --format bibtex
zotero-cli export bib --items KEY1,KEY2 --output refs.bib
zotero-cli item citation ITEM_KEY
zotero-cli item context ITEM_KEY              # LLM 友好格式
zotero-cli docx inspect-citations draft.docx  # 检测 Zotero/EndNote/静态引用字段
zotero-cli docx validate-placeholders draft.docx
zotero-cli docx render-citations draft.docx --output draft-static.docx --force
zotero-cli docx doctor
zotero-cli docx insert-citations draft.docx --output draft-zotero.docx --force
```

AI 生成 DOCX 时，应插入 `{{zotero:ITEMKEY}}` 或
`{{zotero:KEY1,KEY2}}` 这样的 Zotero 绑定占位符，最后再选择输出模式
（完整依赖表见上文 [DOCX 引用：静态 vs 动态](#docx-引用静态-vs-动态)）：

- 静态引用：`docx render-citations` → 普通文本 + 静态参考文献；**不需要 LibreOffice**；不能 Refresh。
- 动态引用：`docx insert-citations` → 可刷新的 Zotero 字段；**额外需要 LibreOffice + Zotero LO 插件 + Bridge**。

当用户只说“帮我插入文献”而没有说明模式时，AI 应先询问要静态还是动态。  
没有装 LibreOffice 时优先静态。承诺动态转换前必须先跑 `docx doctor`。

AI 推荐流程（用户给定占位符文稿后）：

1. `zotero-cli --json docx validate-placeholders <原稿.docx>`
2. 如果用户需要可刷新/可继续编辑的引用：
   - `zotero-cli --json docx doctor`
   - `zotero-cli --json docx insert-citations <原稿.docx> --output <最终.docx> --force`
   - 如果失败，返回 `doctor` 的失败层级并给出修复建议，不直接假设成功。
3. 如果用户只要一次性定稿，或者动态流程不可用：
   - `zotero-cli --json docx render-citations <原稿.docx> --output <最终.docx> --force`

默认只保留两类输出：
- 原始占位符文稿
- 最终输出文稿
- 仅当用户明确要求 `--debug-dir` 时才保留中间调试文件。

这个可选工作流的平台状态：
- macOS：已经端到端验证，可自动打开、转换、保存，并生成 Word 可打开的 DOCX。
- Windows/Linux：基础 CLI 可以使用，`docx doctor` 也可以检查依赖；但动态 DOCX 引用的 LibreOffice 自动打开/保存还需要真实 Windows/Linux 桌面环境验证。在平台自动化验证完成前，用户可能需要手动打开或保存 LibreOffice 文档。

`validate-placeholders`、`zoterify-preflight`、`zoterify-probe` 是安装检查或
出错排查命令，不是每天都要跑的正式流程。只有需要排查时，才给
转换命令额外传 `--debug-dir <目录>`，让它保存 placeholder map、bridge 返回值
和 inspect 结果。`docx prepare-zotero-import` 只保留为实验性调试命令；
在 Zotero 9 + LibreOffice 测试中它不稳定，因此不作为正式写作流程推荐。
`docx insert-citations` 和 `docx render-citations` 是 AI 写作场景两种正式输出。
`item citation` 和 `item bibliography` 只适合静态预览；它们不是 Word 或
LibreOffice Zotero 插件可刷新的字段。BIB 导出是独立导出功能，不接入 DOCX
写作流程。

**写入与管理**
```bash
zotero-cli item update KEY --field title="新标题"
zotero-cli item tag KEY --add "重要"
zotero-cli item attach KEY ./论文.pdf
zotero-cli item find-pdf KEY
zotero-cli note add KEY --text "我的笔记"
zotero-cli sync
```

**高级功能**
```bash
zotero-cli item search-annotations "风险"
zotero-cli item annotations KEY
zotero-cli item metrics KEY                   # NIH 引用指标
zotero-cli collection stats COLLECTION_KEY
zotero-cli js "return await Zotero.Items.getAll(1).then(i => i.length)"
```

---

## 可选功能

| 功能 | 需要 | 命令 |
|------|------|------|
| 语义搜索 | 嵌入 API（Ollama/LM Studio 等） | `item semantic-search`, `item similar`, `item build-index` |
| AI 分析 | `OPENAI_API_KEY` | `item analyze` |

---

## Legacy MCP 用户

MCP 支持冻结在 `v0.9.5`。如需继续使用旧 MCP server，请安装：

```bash
pip install "cli-anything-zotero[mcp]==0.9.5"
```

也可以使用 `legacy/mcp` 分支从源码安装。从 `v1.0.0` 开始，主线只维护 CLI/SDK surface，不再安装 `zotero-mcp` 命令。

## 许可证

[Apache 2.0](../LICENSE)
