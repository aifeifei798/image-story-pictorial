"""One-shot dataset loader: reads my_rag_stories.json and builds in-memory indices.

Everything here runs at import time so that request handlers are pure dict
lookups. 10k records / 18 MB of JSON cost ~0.3 s of startup and ~90 MB RSS.
Images themselves are never preloaded; they are streamed by FileResponse.
"""

from __future__ import annotations

import json
import os
import random
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_FILE = BASE_DIR / "my_rag_stories.json"
IMAGE_DIR = BASE_DIR / "downloaded_images"

# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

_RAW = json.loads(DATA_FILE.read_text(encoding="utf-8"))


def _webp_size(path: str) -> tuple[int, int] | None:
    """Width/height from the RIFF header — no Pillow, ~10 k small reads at import."""
    try:
        with open(path, "rb") as fh:
            b = fh.read(30)
    except OSError:
        return None
    if len(b) < 30 or b[0:4] != b"RIFF" or b[8:12] != b"WEBP":
        return None
    kind = b[12:16]
    if kind == b"VP8 ":  # bitstream
        return ((b[26] | b[27] << 8) & 0x3FFF, (b[28] | b[29] << 8) & 0x3FFF)
    if kind == b"VP8X":  # extended
        return (1 + (b[24] | b[25] << 8 | b[26] << 16), 1 + (b[27] | b[28] << 8 | b[29] << 16))
    if kind == b"VP8D":  # extended lossless
        return (
            1 + ((b[23] | b[24] << 8) & 0x3FFF),
            1 + (((b[24] >> 6) | b[25] << 2 | b[26] << 10) & 0x3FFF),
        )
    return None


def _local_image(record: dict) -> str | None:
    """Absolute path of the local webp, or None when it was never downloaded.

    Three records store a remote URL in ``local_image_path`` instead of a path;
    those fall back to ``image_url`` at request time.
    """
    raw = (record.get("local_image_path") or "").strip()
    candidates: list[Path] = []
    if raw and not raw.startswith(("http://", "https://")):
        p = Path(raw)
        candidates.append(p if p.is_absolute() else BASE_DIR / p)
    candidates.append(IMAGE_DIR / f"{record['id']}.webp")
    for c in candidates:
        if c.is_file():
            return str(c.resolve())
    return None


STORIES: list[dict] = []
BY_ID: dict[str, dict] = {}
MISSING_LOCAL: list[str] = []

for _rec in _RAW:
    _rec = dict(_rec)
    _rec["_local_image"] = _local_image(_rec)
    _rec["_size"] = _webp_size(_rec["_local_image"]) if _rec["_local_image"] else None
    _rec["_created_sort"] = _rec.get("created_at") or ""
    _m = re.search(r"(?m)^\s*editor\s*:\s*(.+?)\s*$", _rec.get("story_text") or "", re.IGNORECASE)
    _rec["_editor"] = _m.group(1).strip() if _m else None
    if _rec["_local_image"] is None:
        MISSING_LOCAL.append(_rec["id"])
    if _rec["id"] in BY_ID:
        continue  # ids are unique in practice; keep the first occurrence
    BY_ID[_rec["id"]] = _rec
    STORIES.append(_rec)

del _RAW

# ---------------------------------------------------------------------------
# Indices
# ---------------------------------------------------------------------------

# created_at is a uniform ISO-8601 UTC string ("2026-02-07T19:24:08.000Z"),
# so lexicographic ordering is also chronological ordering.
SORTED_NEW: list[dict] = sorted(STORIES, key=lambda r: r["_created_sort"], reverse=True)
SORTED_OLD: list[dict] = sorted(STORIES, key=lambda r: r["_created_sort"])

# newest-first id sequence + positions, for prev/next navigation
ORDER_NEW: list[str] = [r["id"] for r in SORTED_NEW]
POS: dict[str, int] = {sid: i for i, sid in enumerate(ORDER_NEW)}

# tag (lowercase) -> ids newest first, plus a canonical display spelling
TAG_INDEX: dict[str, list[str]] = {}
TAG_DISPLAY: dict[str, str] = {}
for _r in SORTED_NEW:
    for _t in _r.get("tags") or []:
        key = _t.strip().lower()
        if not key:
            continue
        TAG_INDEX.setdefault(key, []).append(_r["id"])
        TAG_DISPLAY.setdefault(key, _t)

# day (YYYY-MM-DD) -> ids newest first
DATE_INDEX: dict[str, list[str]] = {}
for _r in SORTED_NEW:
    _day = (_r.get("created_at") or "")[:10]
    if len(_day) == 10 and _day[4] == "-" and _day[7] == "-":
        DATE_INDEX.setdefault(_day, []).append(_r["id"])

# editor (lowercase) -> ids newest first, plus a canonical display spelling.
# The byline lives inside story_text as an "Editor: <name>" line.
EDITOR_INDEX: dict[str, list[str]] = {}
EDITOR_DISPLAY: dict[str, str] = {}
for _r in SORTED_NEW:
    if not _r.get("_editor"):
        continue
    key = _r["_editor"].strip().lower()
    if not key:
        continue
    EDITOR_INDEX.setdefault(key, []).append(_r["id"])
    EDITOR_DISPLAY.setdefault(key, _r["_editor"])

# id -> (title, tags, story_text) already lowercased, for search scoring
SEARCH: dict[str, tuple[str, str, str]] = {
    r["id"]: (
        (r.get("title") or "").lower(),
        " ".join(r.get("tags") or []).lower(),
        (r.get("story_text") or "").lower(),
    )
    for r in STORIES
}

DATE_MIN = SORTED_OLD[0]["_created_sort"] if SORTED_OLD else ""
DATE_MAX = SORTED_NEW[0]["_created_sort"] if SORTED_NEW else ""

# ---------------------------------------------------------------------------
# Helpers shared by the routes
# ---------------------------------------------------------------------------

PAGE_SIZE_MAX = 50
DEFAULT_PAGE_SIZE = 20


def paginate(seq: list, page: int, page_size: int) -> tuple[dict, slice]:
    """Return the public paging envelope plus the slice to take over ``seq``."""
    page_size = max(1, min(page_size, PAGE_SIZE_MAX))
    total = len(seq)
    pages = max(1, (total + page_size - 1) // page_size)
    offset = (page - 1) * page_size
    return (
        {"total": total, "page": page, "page_size": page_size, "pages": pages},
        slice(offset, min(offset + page_size, total)),
    )


def summarize(r: dict) -> dict:
    """Compact list item: metadata only, story_text omitted."""
    return {
        "id": r["id"],
        "title": r.get("title"),
        "tags": r.get("tags") or [],
        "created_at": r.get("created_at"),
        "image": f"/api/images/{r['id']}",
        "image_url": r.get("image_url"),
        "local_image_available": r["_local_image"] is not None,
        "image_w": (r["_size"] or (None, None))[0],
        "image_h": (r["_size"] or (None, None))[1],
    }


_QUERY_SPLIT = re.compile(r"[^\w]+")


def _query_terms(query: str) -> list[str]:
    terms = [t for t in _QUERY_SPLIT.split(query.lower()) if len(t) >= 2]
    if not terms:
        q = query.strip().lower()
        terms = [q] if q else []
    return terms


def search(query: str) -> list[tuple[int, dict]]:
    """AND-substring search over title + tags + story_text.

    Returns (score, record) pairs best-first. Falls back to OR ranking when
    the AND query matches nothing, so one typo'd term still returns results.
    """
    terms = _query_terms(query)
    if not terms:
        return []

    def score(sid: str, require_all: bool) -> int:
        title, tags, text = SEARCH[sid]
        total = 0
        for t in terms:
            c_title = title.count(t)
            c_tags = tags.count(t)
            c_text = text.count(t)
            if c_title + c_tags + c_text == 0:
                if require_all:
                    return -1
                continue
            total += c_title * 3 + c_tags * 3 + c_text
        return total

    hits = []
    for r in STORIES:
        s = score(r["id"], require_all=True)
        if s >= 0:
            hits.append((s, r))
    if not hits:
        for r in STORIES:
            s = score(r["id"], require_all=False)
            if s > 0:
                hits.append((s, r))

    hits.sort(key=lambda pair: (-pair[0], pair[1]["_created_sort"]))
    return hits


def random_stories(n: int) -> list[dict]:
    n = max(1, min(n, 20))
    return random.sample(SORTED_NEW, min(n, len(SORTED_NEW)))


# ---------------------------------------------------------------------------
# Upload support: persist + index a brand-new record without a restart
# ---------------------------------------------------------------------------


def make_record(raw: dict) -> dict:
    """Attach derived fields (_local_image/_size/_created_sort/_editor).

    An explicit ``editor`` key wins; otherwise the byline is parsed out of
    ``story_text`` with the same regex used at import time.
    """
    r = dict(raw)
    r["_local_image"] = _local_image(r)
    r["_size"] = _webp_size(r["_local_image"]) if r["_local_image"] else None
    r["_created_sort"] = r.get("created_at") or ""
    ed = r.get("editor")
    ed = ed.strip() if isinstance(ed, str) else ""
    if not ed:
        m = re.search(
            r"(?m)^\s*editor\s*:\s*(.+?)\s*$",
            r.get("story_text") or "",
            re.IGNORECASE,
        )
        ed = m.group(1).strip() if m else ""
    r["_editor"] = ed or None
    return r


def persist_raw(raw: dict) -> None:
    """Atomically append a raw record to my_rag_stories.json.

    The file is ``indent=2, ensure_ascii=False`` — dumping with the same
    parameters keeps every existing byte untouched.
    """
    tmp = DATA_FILE.with_suffix(".json.tmp")
    with open(DATA_FILE, encoding="utf-8") as fh:
        data = json.load(fh)
    data.append({k: v for k, v in raw.items() if not k.startswith("_")})
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)


def register_record(raw: dict) -> dict:
    """Index an already-persisted raw record in every in-memory structure.

    The record must be the newest one (``created_at`` >= ``DATE_MAX``), so it
    goes to the head of each newest-first index. Returns the derived record.
    """
    global DATE_MAX
    r = make_record(raw)
    sid = r["id"]
    BY_ID[sid] = r
    STORIES.append(r)
    SORTED_NEW.insert(0, r)
    SORTED_OLD.append(r)
    ORDER_NEW.insert(0, sid)
    for i, s in enumerate(ORDER_NEW):
        POS[s] = i
    for t in r.get("tags") or []:
        key = t.strip().lower()
        if not key:
            continue
        TAG_INDEX.setdefault(key, []).insert(0, sid)
        TAG_DISPLAY.setdefault(key, t)
    day = (r.get("created_at") or "")[:10]
    if len(day) == 10 and day[4] == "-" and day[7] == "-":
        DATE_INDEX.setdefault(day, []).insert(0, sid)
    if r.get("_editor"):
        key = r["_editor"].strip().lower()
        if key:
            EDITOR_INDEX.setdefault(key, []).insert(0, sid)
            EDITOR_DISPLAY.setdefault(key, r["_editor"])
    SEARCH[sid] = (
        (r.get("title") or "").lower(),
        " ".join(r.get("tags") or []).lower(),
        (r.get("story_text") or "").lower(),
    )
    if r["_created_sort"] >= DATE_MAX:
        DATE_MAX = r["_created_sort"]
    return r
