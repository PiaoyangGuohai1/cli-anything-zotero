"""NIH iCite citation metrics lookup.

Fetches citation metrics (citation count, RCR, NIH percentile, etc.)
from the NIH iCite API for a given PMID.
"""

from __future__ import annotations

import json
import urllib.request


def get_metrics(pmid: str) -> dict:
    """Fetch citation metrics from NIH iCite for a given PMID."""
    url = f"https://icite.od.nih.gov/api/pubs?pmids={pmid}&format=json"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        return {"error": f"Failed to fetch metrics for PMID {pmid}: {e}"}
    if not data.get("data"):
        return {"error": f"No data for PMID {pmid}"}
    d = data["data"][0]
    return {
        "pmid": d.get("pmid"),
        "title": d.get("title", "")[:80],
        "year": d.get("year"),
        "journal": d.get("journal", ""),
        "citation_count": d.get("citation_count", 0),
        "rcr": d.get("relative_citation_ratio"),
        "nih_percentile": d.get("nih_percentile"),
        "expected_citations": d.get("expected_citations_per_year"),
        "doi": d.get("doi", ""),
    }
