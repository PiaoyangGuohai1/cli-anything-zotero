# Roadmap — cli-anything-zotero

Last updated: 2026-07-22  
Current release: **v1.1.0** (CLI Bridge plugin **1.2.0**)

## Positioning

> Local **coding-agent runtime** for the Zotero desktop app (SQLite + Local API + Connector + JS Bridge).  
> Not a second zotero-mcp (chat/MCP product). Not a pure Web-API wrapper (pyzotero-cli).

**Do** deepen: idempotent batch writes, observability, PDF attach, DOCX cite chain.  
**Don't** prioritize: MCP-first product, cloud-only mode as default, heavy semantic-search platform.

## Done (baseline)

| Version | Theme |
|---------|--------|
| ≤1.0.0 | CLI-first; MCP frozen at 0.9.5 |
| **1.1.0** | DOI: dedupe → translator → Crossref BibTeX; bridge error serialization; per-item `find-pdfs`; BibTeX split; longer connector timeout |

## 1.2.0 — Agent contract (in progress → nearly done)

Theme: **agents can safely batch-run write ops**.

- [x] `app doctor` — connector, local API, plugin version, bridge ping, next steps
- [x] Shared result helpers (`core/results.py`); import doi uses schema
- [x] No false success for bridge empty payloads (`emit_js(require_data=...)`, DOI app-level checks)
- [x] Exit codes aligned with `status` (`exit_code_for`)
- [x] DOI `--if-exists file|skip|duplicate` (+ file/tags on reuse)
- [x] SQLite listings expose `DOI`, `hasPdf`, `date`
- [x] Plugin version mismatch warning on `import doi`
- [x] Docs + skill + ROADMAP/TODO hygiene
- [ ] Remaining: roll result schema to attach/find-pdf/file import; optional hard-fail on plugin mismatch

## 1.3.0 — Ingest & PDF (mostly done)

Theme: **one verb to add literature + get PDFs**.

- [x] Unified `add` entry: `doi` / `arxiv` / `url` / `file` / `bibtex`
- [x] PDF cascade: Zotero find-pdf → Unpaywall → EuropePMC/PMC → bioRxiv/arXiv
- [x] `item fetch-pdf` / `collection fetch-pdfs` with `--jsonl-progress` + `--limit`
- [x] Resume for `collection fetch-pdfs --resume` / `--reset-resume`
- [ ] Optional custom hook source

## 1.4.0 — Library hygiene (started)

Theme: **keep the library clean under agent churn**.

- [x] `item duplicates --by doi|title|zotero`
- [x] `item merge KEEP OTHER... --dry-run/--confirm`
- [x] CSL-JSON / Crossref JSON auto-import
- [ ] Better partial/resume for multi-entry imports
- [ ] Safer merge previews (list attachments/tags before confirm)

## 1.5.0 — Writing chain & polish

Theme: **unique DOCX path + agent packaging**.

- [ ] DOCX one-shot: doctor → validate → zoterify (clear errors)
- [ ] Optional audit log for privileged `js` / write ops
- [ ] First-class Agent Skill refresh (retry chains, when to use which import)
- [ ] Optional degraded mode when Zotero is closed (read-only Web API; never replace bridge as primary)

## Explicit non-goals (near term)

- MCP as the primary product surface
- Competing with zotero-mcp on semantic search / Scite / full annotation product
- Reimplementing Zotero translators or citeproc in Python
- Default dependency on commercial LLM APIs

## Tracking

Active checklist: [TODO.md](./TODO.md)
