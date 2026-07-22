# Active TODO

Source of truth for **in-progress** work. Roadmap overview: [ROADMAP.md](./ROADMAP.md).

Legend: `[ ]` todo · `[~]` in progress · `[x]` done

---

## Now — post-1.2 polish

- [x] merge preview SQLite fallback
- [x] audit log (`audit path` / `audit tail`)
- [x] Live smoke: audit after write + merge preview
- [x] Skill mention audit

## Done — v1.5 DOCX + merge polish

- [x] `item merge` rich dry-run preview (attachments/tags/collections)
- [x] `docx cite` one-shot pipeline (`auto|static|dynamic`)
- [x] Live smoke: merge preview + docx cite static
- [x] Skill/docs for docx cite

## Done — v1.4 hygiene + 1.3 tail

- [x] `add url`
- [x] CSL-JSON / Crossref auto-import in `import json` / `add file`
- [x] `item duplicates --by doi|title|zotero`
- [x] `item merge --dry-run/--confirm`
- [x] `collection fetch-pdfs --resume`
- [x] Live smoke: add url + duplicates

## Done — v1.3.0 Ingest & PDF

- [x] `add doi|arxiv|file|bibtex|url`
- [x] `item fetch-pdf` OA cascade
- [x] `collection fetch-pdfs` + `--jsonl-progress` + `--resume`
- [x] attach/find-pdf result schema
- [x] Live smoke: add arxiv + fetch-pdf

## Done — v1.2.0 Agent contract

### Doctor & health
- [x] `zotero-cli app doctor [--json]` aggregates connector / local API / plugin / bridge
- [x] Write commands warn when plugin `<` bundled required version (`import doi` includes `plugin_warning`)

### Result contract
- [x] Shared helper module for success/error/partial payloads (`core/results.py`)
- [~] Import / attach / find-pdf use shared schema (import doi done; others next)
- [x] Exit codes: success=0, partial/error=1 (`exit_code_for`)

### Idempotency
- [x] Document and expose `--if-exists file|skip|duplicate` on DOI
- [x] Dedupe hit still applies tags/collection when `if-exists=file`

### Query fields for agents
- [x] Enrich collection/item listings with `DOI` + `hasPdf` (+ `date`) via SQLite
- [x] Prefer zero extra round-trips when SQLite can supply fields

### Docs / skill
- [x] README points to ROADMAP/TODO
- [x] SKILL.md: doctor first; import fallback chain
- [x] Delete outdated `ZOTERO.md`; slim package README

### Verification
- [x] Unit tests for doctor payload shape + result helpers
- [x] Live smoke: `app doctor` ready; collection items returns DOI/hasPdf

---

## Later

- [x] Package release v1.2.0
- [ ] Optional degraded read mode when Zotero is closed

---

## Retired / deleted plans

| Artifact | Action | Reason |
|----------|--------|--------|
| `ZOTERO.md` (repo root) | **Deleted 2026-07-22** | Pre-bridge operator doc; claimed JS privileged exec out of scope; Windows-only paths; contradicted current architecture |
| `cli_anything/zotero/README.md` (old 550-line harness guide) | **Replaced** with short pointer | Duplicated root README; outdated install paths (`zotero/agent-harness`) |
