# Zotero package (`cli_anything.zotero`)

Python package behind **`zotero-cli`** / **`cli-anything-zotero`**.

## Docs

| Doc | Purpose |
|-----|---------|
| [../../README.md](../../README.md) | Install, quick start, positioning |
| [../../docs/README_zh.md](../../docs/README_zh.md) | 中文说明 |
| [../../docs/COMMANDS.md](../../docs/COMMANDS.md) | Command reference |
| [../../docs/ROADMAP.md](../../docs/ROADMAP.md) | Product roadmap (1.2–1.5) |
| [../../docs/TODO.md](../../docs/TODO.md) | Active implementation checklist |
| [skills/SKILL.md](skills/SKILL.md) | Agent skill brief |
| [tests/TEST.md](tests/TEST.md) | Test layout notes |

## Architecture (current)

Local agent runtime for the **Zotero desktop app**:

1. **SQLite** — offline inventory reads  
2. **Local API** (`localhost:23119/api`) — search, CSL, export  
3. **Connector** — official import / save session writes  
4. **CLI Bridge plugin** — privileged JS (translators, find PDF, attach, arbitrary `js`)

Do not treat experimental SQLite write flags as the primary write path; prefer connector + bridge while Zotero is running.

## Develop

```bash
pip install -e ".[dev]"
pytest cli_anything/zotero/tests -q
zotero-cli --json app doctor
```
