"""Semantic-search backend over the llama-server embedding service.

The embedding model (Granite-Embedding-97M-R2, 384 dims, L2-normalized)
runs outside this service — typically ``./llama-server.sh`` on :8023.
This module:

- builds the text that gets embedded for each record (``record_text``),
- talks to ``POST {EMBED_URL}/v1/embeddings`` with the stdlib only,
- memory-maps the precomputed matrix in ``embeddings.f32`` + ids in
  ``embeddings.ids.json`` (written by ``build_embeddings.py``),
- ranks by dot product (= cosine, vectors are unit length).

No third-party dependencies: storage is raw little-endian float32,
ranking is a pure-Python dot loop (~0.15 s over 10k rows).
"""

from __future__ import annotations

import heapq
import json
import os
import struct
import urllib.request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMBED_URL = os.environ.get("EMBED_URL", "http://localhost:8023").rstrip("/")
EMBED_FILE = os.path.join(BASE_DIR, os.environ.get("EMBED_FILE", "embeddings.f32"))
EMBED_IDS = os.path.join(BASE_DIR, os.environ.get("EMBED_IDS", "embeddings.ids.json"))

DIM = 384
MAX_CHARS = 3000  # keeps every record well under the 1024-token server slot


def record_text(r: dict) -> str:
    """The text that gets embedded: title + tags + story_text, truncated."""
    parts = [
        (r.get("title") or "").strip(),
        " ".join(t for t in (r.get("tags") or []) if t).strip(),
        (r.get("story_text") or "").strip(),
    ]
    text = "\n".join(p for p in parts if p)
    return text[:MAX_CHARS] if len(text) > MAX_CHARS else text


def embed_inputs(texts: list[str], timeout: int = 120) -> list[list[float]]:
    """Embed one batch via llama-server's OpenAI-compatible endpoint."""
    payload = json.dumps({"input": texts}).encode("utf-8")
    req = urllib.request.Request(
        f"{EMBED_URL}/v1/embeddings",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.load(resp)
    rows = sorted(body["data"], key=lambda d: d["index"])
    return [[float(x) for x in d["embedding"]] for d in rows]


def ping(timeout: int = 5) -> bool:
    try:
        with urllib.request.urlopen(f"{EMBED_URL}/health", timeout=timeout):
            return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Local vector cache (loaded once, keyed to dataset order)
# ---------------------------------------------------------------------------

_VECTORS: list[list[float]] | None = None
_IDS: list[str] = []
_LOADED_FOR: int = -1


def _read_matrix() -> tuple[list[list[float]], list[str]] | None:
    if not (os.path.isfile(EMBED_FILE) and os.path.isfile(EMBED_IDS)):
        return None
    with open(EMBED_IDS, encoding="utf-8") as fh:
        ids = json.load(fh)
    n = len(ids)
    size = os.path.getsize(EMBED_FILE)
    if n == 0 or size != n * DIM * 4:
        return None
    with open(EMBED_FILE, "rb") as fh:
        raw = fh.read()
    vecs: list[list[float]] = []
    off = 0
    step = DIM * 4
    unpack = struct.unpack
    fmt = f"<{DIM}f"
    for _ in range(n):
        vecs.append(list(unpack(fmt, raw[off : off + step])))
        off += step
    return vecs, ids


def ensure_loaded(total: int) -> bool:
    """(Re)load the cache when the dataset size changed. True when usable."""
    global _VECTORS, _IDS, _LOADED_FOR
    if _VECTORS is not None and _LOADED_FOR == total and len(_IDS) == total:
        return True
    got = _read_matrix()
    if got is None or len(got[1]) != total:
        _VECTORS, _IDS, _LOADED_FOR = None, [], total
        return False
    _VECTORS, _IDS = got
    _LOADED_FOR = total
    return True


def status(total: int) -> dict:
    ok = ensure_loaded(total)
    return {
        "url": EMBED_URL,
        "dim": DIM,
        "indexed": len(_IDS) if ok else 0,
        "total": total,
        "ready": ok,
        "live": ping(),
    }


def rank(query: list[float], top_k: int) -> list[tuple[float, int]]:
    """Top-k (score, row) pairs, best first. Vectors are unit length."""
    assert _VECTORS is not None
    top_k = max(1, min(top_k, len(_VECTORS)))
    scored = (
        (sum(a * b for a, b in zip(query, row)), i)
        for i, row in enumerate(_VECTORS)
    )
    return heapq.nlargest(top_k, scored, key=lambda p: p[0])


def row_ids() -> list[str]:
    return _IDS
