"""Semantic search for Zotero library using local embedding API + vector SQLite database.

Queries a local OpenAI-compatible embedding API to convert text to vectors,
then performs cosine similarity search against a SQLite vector store.
The vector index can be built with `zotero-cli item build-index`.
"""

from __future__ import annotations

import json
import os
import sqlite3
import struct
import urllib.request
from pathlib import Path

_EMBED_API = os.environ.get("ZOTERO_EMBED_API", "http://127.0.0.1:8080/v1/embeddings")
_EMBED_MODEL = os.environ.get("ZOTERO_EMBED_MODEL", "nomic-embed-text")
_EMBED_KEY = os.environ.get("ZOTERO_EMBED_KEY", "")
_VECTOR_DB = os.environ.get("ZOTERO_VECTOR_DB", str(Path.home() / "Zotero" / "cli-anything-vectors.sqlite"))


def _get_embedding(text: str) -> list[float]:
    """Get embedding vector from local OMLX API."""
    body = json.dumps({"input": text, "model": _EMBED_MODEL}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if _EMBED_KEY:
        headers["Authorization"] = f"Bearer {_EMBED_KEY}"
    req = urllib.request.Request(
        _EMBED_API,
        data=body,
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    return data["data"][0]["embedding"]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _decode_f32_vector(blob: bytes | str) -> list[float]:
    """Decode float32 vector blob."""
    if isinstance(blob, str):
        blob = blob.encode("latin-1")
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def _load_f32_vectors(conn: sqlite3.Connection, language: str = "all", exclude_key: str | None = None) -> list[tuple]:
    """Load float32 vectors with metadata from embeddings + vectors_f32 tables."""
    lang_filter = f"AND e.language = '{language}'" if language != "all" else ""
    key_filter = f"AND e.item_key != '{exclude_key}'" if exclude_key else ""

    rows = conn.execute(
        f"SELECT e.item_key, e.chunk_id, v.vector, e.chunk_text, e.language "
        f"FROM vectors_f32 v "
        f"JOIN embeddings e ON v.item_key = e.item_key AND v.chunk_id = e.chunk_id "
        f"WHERE 1=1 {lang_filter} {key_filter}"
    ).fetchall()
    return rows


def _detect_language(text: str) -> str:
    """Simple heuristic: if >30% CJK characters, return 'zh', else 'en'."""
    if not text:
        return "en"
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    return "zh" if cjk / max(len(text), 1) > 0.3 else "en"


def _encode_f32_vector(vec: list[float]) -> bytes:
    """Encode float32 vector to bytes."""
    return struct.pack(f"{len(vec)}f", *vec)


def build_index(zotero_sqlite: str, *, batch_size: int = 20) -> dict:
    """Build the vector index from Zotero's SQLite database.

    Reads item metadata (title, abstract, creators) from zotero.sqlite,
    generates embeddings via the configured API, and stores them in the
    vector database.

    Returns {"ok": bool, "indexed": int, "skipped": int, "error": ...}
    """
    if not os.path.exists(zotero_sqlite):
        return {"ok": False, "indexed": 0, "skipped": 0, "error": f"Zotero DB not found: {zotero_sqlite}"}

    # Read items from Zotero SQLite
    try:
        src = sqlite3.connect(f"file:{zotero_sqlite}?mode=ro&immutable=1", uri=True)
        rows = src.execute("""
            SELECT i.key, MAX(CASE WHEN f.fieldName='title' THEN iv.value END),
                   MAX(CASE WHEN f.fieldName='abstractNote' THEN iv.value END)
            FROM items i
            JOIN itemData id ON i.itemID = id.itemID
            JOIN itemDataValues iv ON id.valueID = iv.valueID
            JOIN fields f ON id.fieldID = f.fieldID
            WHERE i.itemTypeID NOT IN (1, 14)
              AND f.fieldName IN ('title', 'abstractNote')
            GROUP BY i.key
        """).fetchall()
        src.close()
    except Exception as e:
        return {"ok": False, "indexed": 0, "skipped": 0, "error": f"Read error: {e}"}

    if not rows:
        return {"ok": True, "indexed": 0, "skipped": 0, "error": None}

    # Create/open vector DB
    db = sqlite3.connect(_VECTOR_DB)
    db.execute("""CREATE TABLE IF NOT EXISTS embeddings (
        item_key TEXT, chunk_id INTEGER, chunk_text TEXT, language TEXT,
        PRIMARY KEY (item_key, chunk_id))""")
    db.execute("""CREATE TABLE IF NOT EXISTS vectors_f32 (
        item_key TEXT, chunk_id INTEGER, vector BLOB,
        PRIMARY KEY (item_key, chunk_id))""")

    # Check which keys already indexed
    existing = {r[0] for r in db.execute("SELECT DISTINCT item_key FROM embeddings").fetchall()}

    indexed = 0
    skipped = 0
    errors = []

    for key, title, abstract in rows:
        if key in existing:
            skipped += 1
            continue

        text = (title or "") + "\n" + (abstract or "")
        text = text.strip()
        if not text:
            skipped += 1
            continue

        try:
            vec = _get_embedding(text)
        except Exception as e:
            errors.append(f"{key}: {e}")
            continue

        lang = _detect_language(text)
        db.execute("INSERT OR REPLACE INTO embeddings VALUES (?, 0, ?, ?)", (key, text[:2000], lang))
        db.execute("INSERT OR REPLACE INTO vectors_f32 VALUES (?, 0, ?)", (key, _encode_f32_vector(vec)))
        indexed += 1

        if indexed % batch_size == 0:
            db.commit()

    db.commit()
    db.close()

    result = {"ok": True, "indexed": indexed, "skipped": skipped, "total": len(rows), "db_path": _VECTOR_DB, "error": None}
    if errors:
        result["errors"] = errors[:10]
    return result


def semantic_search(query: str, *, top_k: int = 10, min_score: float = 0.3, language: str = "all") -> dict:
    """Semantic search across Zotero library using local embedding model.

    Args:
        query: Natural language search query
        top_k: Number of results to return
        min_score: Minimum cosine similarity score (0-1)
        language: Filter by language ("zh", "en", "all")
    """
    if not os.path.exists(_VECTOR_DB):
        return {"ok": False, "data": None, "error": f"Vector DB not found: {_VECTOR_DB}"}

    try:
        query_vec = _get_embedding(query)
    except Exception as e:
        return {"ok": False, "data": None, "error": f"Embedding API error: {e}"}

    try:
        conn = sqlite3.connect(f"file:{_VECTOR_DB}?mode=ro&immutable=1", uri=True)
        rows = _load_f32_vectors(conn, language=language)
        conn.close()
    except Exception as e:
        return {"ok": False, "data": None, "error": f"DB error: {e}"}

    scored = []
    for item_key, chunk_id, blob, text, lang in rows:
        vec = _decode_f32_vector(blob)
        score = _cosine_similarity(query_vec, vec)
        if score >= min_score:
            scored.append({
                "item_key": item_key,
                "score": round(score, 4),
                "chunk_text": (text or "")[:200],
                "language": lang,
            })

    scored.sort(key=lambda x: x["score"], reverse=True)
    seen = set()
    results = []
    for item in scored:
        if item["item_key"] not in seen:
            seen.add(item["item_key"])
            results.append(item)
        if len(results) >= top_k:
            break

    return {"ok": True, "data": results, "error": None}


def find_similar(item_key: str, *, top_k: int = 5, min_score: float = 0.5) -> dict:
    """Find items similar to a given item using embeddings."""
    if not os.path.exists(_VECTOR_DB):
        return {"ok": False, "data": None, "error": f"Vector DB not found: {_VECTOR_DB}"}

    try:
        conn = sqlite3.connect(f"file:{_VECTOR_DB}?mode=ro&immutable=1", uri=True)

        target_row = conn.execute(
            "SELECT vector FROM vectors_f32 WHERE item_key = ? AND chunk_id = 0",
            (item_key,),
        ).fetchone()
        if not target_row:
            conn.close()
            return {"ok": False, "data": None, "error": f"No embedding for item {item_key}"}

        target_vec = _decode_f32_vector(target_row[0])
        rows = _load_f32_vectors(conn, exclude_key=item_key)
        conn.close()
    except Exception as e:
        return {"ok": False, "data": None, "error": f"DB error: {e}"}

    scored = []
    for item_key_r, chunk_id, blob, text, lang in rows:
        vec = _decode_f32_vector(blob)
        score = _cosine_similarity(target_vec, vec)
        if score >= min_score:
            scored.append({
                "item_key": item_key_r,
                "score": round(score, 4),
                "chunk_text": (text or "")[:200],
                "language": lang,
            })

    scored.sort(key=lambda x: x["score"], reverse=True)
    seen = set()
    results = []
    for item in scored:
        if item["item_key"] not in seen:
            seen.add(item["item_key"])
            results.append(item)
        if len(results) >= top_k:
            break

    return {"ok": True, "data": results, "error": None}
