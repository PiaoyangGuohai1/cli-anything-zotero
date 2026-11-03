"""Duplicate detection over live Zotero data, without Zotero private APIs.

Why this module exists
----------------------
The previous implementation called ``Zotero.Duplicates._findDuplicates()`` --
a private method. Under Zotero 9.0.6 that call resolves to ``undefined`` and
``getSetItemsByItemID()`` returns ``[null]``, so the old code computed
``Object.keys([null]).map(Number).filter(Boolean)`` -> ``[]`` and reported
``count: 0`` at exit 0. Nothing threw, so the surrounding ``catch`` never fired:
a library with real duplicates was indistinguishable from a clean one.

The replacement fetches ordinary item fields through the public API and does the
clustering here, in Python, where it can be tested without a running Zotero.

Matching rules
--------------
Two items are clustered when either holds:

a. ``doi`` -- their normalized DOIs are byte-equal.
b. ``title+creator+year`` -- their normalized titles are equal AND they share at
   least one normalized creator surname AND their years agree (within
   ``year_tolerance``, default 0).

Rule (b) requires positive confirmation on both creator and year. An item with
no creators, or with no parseable year, is never clustered by title alone --
being conservative matters more than recall for a finder that feeds a merge.

Clusters are transitive (union-find), so an item linked by DOI to one copy and
by title to another lands in a single cluster rather than two.

Citation keys
-------------
Every reported copy carries its Better BibTeX citation key and whether that key
is *pinned* (``Citation Key: ...`` in the item's Extra field). Merging a cluster
whose copies carry different pinned keys silently orphans whichever key a
manuscript already cites, so clusters flag that case explicitly.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from itertools import combinations
from typing import Any

from cli_anything.zotero.core.results import result_payload

# ── Normalization (single implementation, shared with hygiene.py) ─────

_HTML_TAG = re.compile(r"<[^>]+>")
# LaTeX accent macros: \~{n}, \'e, \"{o}, \c{c}. The accented letter must survive
# WITHOUT a word break -- stripping the braces naively turns Ni\~{n}o into "ni n o".
_LATEX_ACCENT = re.compile(r"\\[`'^\"~=.uvHtcdbr]\s*\{?\s*([a-zA-Z])\s*\}?")
_LATEX_CMD = re.compile(r"\\[a-zA-Z@]+\s*")
# Apostrophes are elided, not spaced: "Nino's" -> "ninos", not "nino s".
_APOSTROPHE = re.compile(r"['\u2018\u2019\u02bc]")
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_UNDERSCORE = re.compile(r"_+")
_WHITESPACE = re.compile(r"\s+")
_DOI_SHAPE = re.compile(r"^10\.\d{4,9}/\S+$")
_DOI_EXTRACT = re.compile(r"10\.\d{4,9}/\S+")


def _strip_diacritics(text: str) -> str:
    """NFKD-decompose and drop combining marks."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_title(value: str | None) -> str:
    """Fold a title to a comparison token.

    NFKD, strip diacritics, strip HTML tags and LaTeX markup, strip punctuation,
    collapse whitespace, lowercase.
    """
    text = value or ""
    text = _HTML_TAG.sub(" ", text)
    text = text.replace("$", " ")
    text = _LATEX_ACCENT.sub(r"\1", text)
    text = _LATEX_CMD.sub(" ", text)
    # Braces are grouping, not word boundaries: drop them rather than spacing them.
    text = text.replace("{", "").replace("}", "")
    text = _APOSTROPHE.sub("", text)
    text = _strip_diacritics(text)
    text = text.lower()
    text = _NON_WORD.sub(" ", text)
    text = _UNDERSCORE.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def normalize_doi(value: str | None) -> str:
    """Fold a DOI to a comparison token (bare ``10.x/y``, lowercased).

    Extraction is shape-directed rather than prefix-stripping, so every resolver
    form folds to the same token in one pass: ``doi:10.x/y``,
    ``https://doi.org/10.x/y``, ``http://dx.doi.org/10.x/y``,
    ``http://doi.acm.org/10.x/y``, and the doubled prefixes Zotero imports
    sometimes carry (``https://doi.org/https://doi.org/10.x/y``). A single
    prefix strip leaves those unequal to the bare DOI and they silently never
    match.
    """
    text = (value or "").strip().lower()
    if not text:
        return ""
    match = _DOI_EXTRACT.search(text)
    if match:
        text = match.group(0)
    return text.rstrip(" .),;")


def normalize_surname(value: str | None) -> str:
    """Fold a creator surname to a comparison token."""
    text = _strip_diacritics(value or "").lower()
    text = _NON_WORD.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def looks_like_doi(value: str) -> bool:
    """True when a normalized DOI has real DOI shape.

    Guards against clustering on junk that landed in the DOI field.
    """
    return bool(_DOI_SHAPE.match(value))


# ── Fetch (public Zotero API only) ────────────────────────────────────

# Ships item fields, not duplicate verdicts. All matching happens in Python.
FETCH_ITEMS_JS = """
var LIB = {library_id};
var all = await Zotero.Items.getAll(LIB, true, false, false);
var everything = await Zotero.Items.getAll(LIB, false, false, false);
var km = null;
try {{ km = (Zotero.BetterBibTeX && Zotero.BetterBibTeX.KeyManager) ? Zotero.BetterBibTeX.KeyManager : null; }}
catch (e) {{ km = null; }}
var recs = [];
for (var it of all) {{
  if (!it || !it.isRegularItem || !it.isRegularItem()) continue;
  if (it.deleted) continue;
  var creators = [];
  try {{ creators = (it.getCreators() || []).map(function (c) {{ return c.lastName || c.name || ''; }}).filter(Boolean); }} catch (e) {{}}
  var nAtt = 0, nPdf = 0;
  try {{
    var aids = it.getAttachments() || [];
    nAtt = aids.length;
    for (var ai = 0; ai < aids.length; ai++) {{
      var a = Zotero.Items.get(aids[ai]);
      if (a && a.attachmentContentType === 'application/pdf') nPdf++;
    }}
  }} catch (e) {{}}
  var extra = ''; try {{ extra = it.getField('extra') || ''; }} catch (e) {{}}
  var doi = ''; try {{ doi = it.getField('DOI') || ''; }} catch (e) {{}}
  if (!doi && extra) {{
    var dm = /(?:^|\\n)\\s*DOI\\s*:\\s*(\\S+)/i.exec(extra);
    if (dm) doi = dm[1];
  }}
  var date = ''; try {{ date = it.getField('date') || ''; }} catch (e) {{}}
  var year = null;
  try {{ var pd = Zotero.Date.strToDate(date); if (pd && pd.year) year = parseInt(pd.year, 10); }} catch (e) {{}}
  var ck = null, pinned = false;
  var km_ = /(?:^|\\n)\\s*Citation Key\\s*:\\s*([^\\s\\n]+)/i.exec(extra);
  if (km_) {{ ck = km_[1]; pinned = true; }}
  if (km) {{ try {{ var kr = km.get(it.id); if (kr && kr.citationKey && !ck) ck = kr.citationKey; }} catch (e) {{}} }}
  recs.push({{ key: it.key, itemType: it.itemType, title: it.getField('title') || '',
    date: date, year: year, DOI: doi, creators: creators.slice(0, 12),
    numAttachments: nAtt, numPdfAttachments: nPdf,
    citationKey: ck, citationKeyPinned: pinned }});
}}
return {{ ok: true, scanned: recs.length, libraryTopLevel: all.length,
  libraryAllItems: everything.length, bbt: !!km, items: recs }};
"""


def fetch_items_js(library_id: int) -> str:
    return FETCH_ITEMS_JS.format(library_id=int(library_id))


# ── Clustering ────────────────────────────────────────────────────────


class _UnionFind:
    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, index: int) -> int:
        while self._parent[index] != index:
            self._parent[index] = self._parent[self._parent[index]]
            index = self._parent[index]
        return index

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[max(ra, rb)] = min(ra, rb)


def _surnames(record: dict[str, Any]) -> set[str]:
    return {
        normalize_surname(name)
        for name in (record.get("creators") or [])
        if normalize_surname(name)
    }


def _year(record: dict[str, Any]) -> int | None:
    value = record.get("year")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    match = re.search(r"\b(1[6-9]\d{2}|20\d{2}|21\d{2})\b", str(record.get("date") or ""))
    return int(match.group(1)) if match else None


def _keep_rank(record: dict[str, Any]) -> tuple:
    """Sort key for the suggested keeper: pinned key, then PDF, then richness."""
    return (
        0 if record.get("citationKeyPinned") else 1,
        0 if (record.get("numPdfAttachments") or 0) > 0 else 1,
        -(record.get("numAttachments") or 0),
        str(record.get("date") or ""),
        str(record.get("key") or ""),
    )


def cluster_records(
    records: list[dict[str, Any]],
    *,
    year_tolerance: int = 0,
    min_title_chars: int = 8,
) -> list[dict[str, Any]]:
    """Cluster item records into duplicate groups. Pure function, no I/O."""
    total = len(records)
    if total < 2:
        return []

    uf = _UnionFind(total)
    reasons: dict[int, set[str]] = defaultdict(set)
    match_tokens: dict[int, set[str]] = defaultdict(set)

    # (a) exact DOI agreement
    by_doi: dict[str, list[int]] = defaultdict(list)
    for index, record in enumerate(records):
        doi = normalize_doi(record.get("DOI"))
        if doi and looks_like_doi(doi):
            by_doi[doi].append(index)
    for doi, members in by_doi.items():
        if len(members) < 2:
            continue
        for other in members[1:]:
            uf.union(members[0], other)
        for member in members:
            reasons[member].add("doi")
            match_tokens[member].add(f"doi:{doi}")

    # (b) normalized title, confirmed by creator surname and year
    by_title: dict[str, list[int]] = defaultdict(list)
    for index, record in enumerate(records):
        title = normalize_title(record.get("title"))
        if len(title) >= min_title_chars:
            by_title[title].append(index)
    for title, members in by_title.items():
        if len(members) < 2:
            continue
        for left, right in combinations(members, 2):
            shared = _surnames(records[left]) & _surnames(records[right])
            if not shared:
                continue
            year_left, year_right = _year(records[left]), _year(records[right])
            if year_left is None or year_right is None:
                continue
            if abs(year_left - year_right) > year_tolerance:
                continue
            uf.union(left, right)
            for member in (left, right):
                reasons[member].add("title+creator+year")
                match_tokens[member].add(f"title:{title}")

    grouped: dict[int, list[int]] = defaultdict(list)
    for index in range(total):
        grouped[uf.find(index)].append(index)

    clusters: list[dict[str, Any]] = []
    for members in grouped.values():
        if len(members) < 2:
            continue
        member_records = sorted((records[i] for i in members), key=_keep_rank)
        cluster_reasons: set[str] = set()
        tokens: set[str] = set()
        for i in members:
            cluster_reasons |= reasons[i]
            tokens |= match_tokens[i]
        pinned_keys = sorted(
            {
                str(r.get("citationKey"))
                for r in member_records
                if r.get("citationKeyPinned") and r.get("citationKey")
            }
        )
        all_keys = sorted(
            {str(r.get("citationKey")) for r in member_records if r.get("citationKey")}
        )
        clusters.append(
            {
                "match": sorted(tokens)[0] if tokens else "",
                "match_tokens": sorted(tokens),
                "match_reason": sorted(cluster_reasons),
                "count": len(member_records),
                "keep_suggestion": member_records[0].get("key"),
                "citation_keys": all_keys,
                "pinned_citation_keys": pinned_keys,
                "citation_key_conflict": len(pinned_keys) > 1,
                "years": sorted({y for y in (_year(r) for r in member_records) if y is not None}),
                "total_attachments": sum(int(r.get("numAttachments") or 0) for r in member_records),
                "items": member_records,
            }
        )

    clusters.sort(key=lambda c: (-c["count"], str(c.get("keep_suggestion") or "")))
    return clusters


# ── Bridge-facing entry point ─────────────────────────────────────────


def find_duplicates(
    bridge: Any,
    *,
    library_id: int = 1,
    limit: int = 50,
    year_tolerance: int = 0,
    timeout: int = 60,
) -> dict[str, Any]:
    """Find duplicate clusters via the JS bridge, using only public Zotero APIs.

    Never returns an empty success it cannot distinguish from a clean library:
    a bridge failure, a malformed payload, or a zero-item scan of a library that
    demonstrably holds items all produce ``ok=False`` (exit 1).
    """
    try:
        transport = bridge.execute_js(fetch_items_js(library_id), wait_seconds=timeout)
    except Exception as exc:  # transport blew up entirely
        return result_payload(
            action="item_duplicates",
            ok=False,
            status="error",
            code="BRIDGE_ERROR",
            error=f"JS bridge call failed: {exc}",
            by="zotero",
        )

    if not transport.get("ok"):
        return result_payload(
            action="item_duplicates",
            ok=False,
            status="error",
            code="BRIDGE_ERROR",
            error=transport.get("error") or "JS bridge returned a transport failure",
            by="zotero",
        )

    data = transport.get("data")
    if not isinstance(data, dict) or not data.get("ok") or not isinstance(data.get("items"), list):
        return result_payload(
            action="item_duplicates",
            ok=False,
            status="error",
            code="BRIDGE_BAD_PAYLOAD",
            error=(
                "JS bridge returned an unusable payload for the duplicate scan: "
                f"{str(data)[:300]}"
            ),
            by="zotero",
        )

    records: list[dict[str, Any]] = [r for r in data["items"] if isinstance(r, dict)]
    scanned = int(data.get("scanned") or len(records))
    library_top_level = int(data.get("libraryTopLevel") or 0)
    library_all_items = int(data.get("libraryAllItems") or 0)

    # Sanity signal: a zero scan of a library that plainly holds items is a
    # failure, not a clean library. This is the exact condition the old private
    # API produced silently.
    if scanned == 0 and (library_top_level > 0 or library_all_items > 0):
        return result_payload(
            action="item_duplicates",
            ok=False,
            status="error",
            code="EMPTY_SCAN",
            error=(
                f"Duplicate scan examined 0 items but library {library_id} holds "
                f"{library_top_level} top-level / {library_all_items} total items. "
                "Refusing to report a clean library on an empty scan."
            ),
            by="zotero",
            scanned=0,
            library_top_level=library_top_level,
            library_all_items=library_all_items,
        )

    clusters = cluster_records(records, year_tolerance=year_tolerance)
    truncated = len(clusters) > limit > 0
    reported = clusters[:limit] if limit and limit > 0 else clusters

    flat_items: list[dict[str, Any]] = []
    for set_id, cluster in enumerate(reported, start=1):
        cluster["setID"] = set_id
        for record in cluster["items"]:
            record["setID"] = set_id
            flat_items.append(record)

    return result_payload(
        action="item_duplicates",
        ok=True,
        status="success",
        code="OK",
        by="zotero",
        source="bridge",
        scanned=scanned,
        library_top_level=library_top_level,
        library_all_items=library_all_items,
        bbt_available=bool(data.get("bbt")),
        match_rules=["doi", "title+creator+year"],
        year_tolerance=year_tolerance,
        group_count=len(reported),
        total_group_count=len(clusters),
        truncated=truncated,
        # Legacy fields kept for existing parsers: item count and flat item list.
        count=len(flat_items),
        items=flat_items,
        groups=reported,
    )
