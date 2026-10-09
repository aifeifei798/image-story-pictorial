"""Tailwind UI + JSON API over my_rag_stories.json + downloaded_images/."""

from __future__ import annotations

import os
import pathlib
import secrets
import threading
import time
import urllib.parse
from collections import deque
from datetime import datetime, timezone

from fastapi import FastAPI, File, Form, Header, HTTPException, Path, Query, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse

from . import embeddings as EMB
from .dataset import (
    BY_ID,
    DATE_INDEX,
    DATE_MAX,
    DATE_MIN,
    EDITOR_DISPLAY,
    EDITOR_INDEX,
    IMAGE_DIR,
    MISSING_LOCAL,
    ORDER_NEW,
    PENDING,
    POS,
    SORTED_NEW,
    SORTED_OLD,
    STORIES,
    TAG_DISPLAY,
    TAG_INDEX,
    persist_raw,
    remove_record,
    set_status,
    paginate,
    random_stories,
    register_record,
    search,
    summarize,
)

UPLOAD_TOKEN = os.environ.get("UPLOAD_TOKEN")
if not UPLOAD_TOKEN:
    raise RuntimeError(
        "UPLOAD_TOKEN is not set — refusing to start with an open /api/upload"
    )

MAX_IMAGE_BYTES = 20 * 1024 * 1024

# ---------------------------------------------------------------------------
# Security headers (TLS/HSTS belong to the reverse proxy, see deploy/Caddyfile)
# ---------------------------------------------------------------------------

SEC_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https://feimatrix.com; "
        "connect-src 'self'; base-src 'none'; form-action 'self'"
    ),
}

app = FastAPI(
    title="my_rag_stories",
    description="Browse, tag-filter, keyword-search and semantic-search 10k "
    "image stories; images are streamed from downloaded_images/ as image/webp.",
    docs_url=None if not os.environ.get("DEV_DOCS") else "/docs",
    redoc_url=None,
)

# ---------------------------------------------------------------------------
# Static assets (the Tailwind UI)
# ---------------------------------------------------------------------------

STATIC_DIR = pathlib.Path(__file__).resolve().parent / "static"
HTML = {"Cache-Control": "no-cache", **SEC_HEADERS}
ASSET = {"Cache-Control": "public, max-age=31536000, immutable", **SEC_HEADERS}

# ---------------------------------------------------------------------------
# Rate limiting for the expensive routes. Behind a reverse proxy the peer is
# the proxy itself, so prefer X-Forwarded-For (first hop).
# ---------------------------------------------------------------------------

RATE_WINDOW = 20.0
RATE_MAX = 15
_rate_hist: dict[str, deque] = {}

# Sync endpoints run in the threadpool, so approval must be serialized: two
# concurrent approvals would interleave register_record + try_append and
# desynchronize embeddings.ids.json from embeddings.f32.
_APPROVE_LOCK = threading.Lock()


def _rate(request: Request) -> None:
    fwd = request.headers.get("x-forwarded-for")
    ip = (fwd.split(",")[0].strip() if fwd else None) or (request.client and request.client.host) or "?"
    now = time.monotonic()
    hist = _rate_hist.setdefault(ip, deque())
    while hist and now - hist[0] > RATE_WINDOW:
        hist.popleft()
    if len(hist) >= RATE_MAX:
        raise HTTPException(status_code=429, detail="too many requests, slow down")
    hist.append(now)
    if len(_rate_hist) > 5000:
        _rate_hist.clear()


def _require_upload_token(x_upload_token: str | None) -> None:
    if not x_upload_token or not secrets.compare_digest(x_upload_token, UPLOAD_TOKEN):
        raise HTTPException(status_code=401, detail="bad or missing X-Upload-Token")


# ---------------------------------------------------------------------------
# The Tailwind UI
# ---------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(
        STATIC_DIR / "index.html",
        media_type="text/html; charset=utf-8",
        headers=HTML,
    )


@app.get("/story", include_in_schema=False)
def story():
    return FileResponse(
        STATIC_DIR / "story.html",
        media_type="text/html; charset=utf-8",
        headers=HTML,
    )


@app.get("/static/{name}")
def static_asset(name: str = Path(..., pattern=r"^[\w.-]+$")):
    """Flat whitelist: only files that live directly in app/static/."""
    suffix = name.rsplit(".", 1)[-1] if "." in name else ""
    media = {"css": "text/css", "js": "text/javascript", "svg": "image/svg+xml"}.get(suffix)
    path = STATIC_DIR / name
    if media is None or not path.is_file():
        raise HTTPException(status_code=404, detail=f"unknown asset: {name}")
    return FileResponse(
        path,
        media_type=media + ("; charset=utf-8" if suffix != "svg" else ""),
        headers=ASSET,
    )


@app.get("/api/health")
def health() -> dict:
    emb = {k: v for k, v in EMB.status(len(STORIES)).items() if k != "url"}
    return {
        "total": len(BY_ID),
        "pending": len(PENDING),
        "tags": len(TAG_INDEX),
        "editors": len(EDITOR_INDEX),
        "date_min": DATE_MIN,
        "date_max": DATE_MAX,
        "missing_local_images": len(MISSING_LOCAL),
        "embedding": emb,
    }


# ---------------------------------------------------------------------------
# Stories
# ---------------------------------------------------------------------------


@app.get("/api/stories")
def list_stories(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    order: str = Query("new", pattern="^(new|old)$"),
) -> dict:
    seq = SORTED_NEW if order == "new" else SORTED_OLD
    meta, window = paginate(seq, page, page_size)
    return {**meta, "order": order, "items": [summarize(r) for r in seq[window]]}


@app.get("/api/stories/{story_id}")
def get_story(story_id: str) -> dict:
    r = BY_ID.get(story_id)
    if r is None:
        raise HTTPException(status_code=404, detail=f"unknown story id: {story_id}")
    i = POS[story_id]
    return {
        "id": r["id"],
        "title": r.get("title"),
        "status": r.get("status"),
        "tags": r.get("tags") or [],
        "editor": r.get("_editor"),
        "created_at": r.get("created_at"),
        "prev": ORDER_NEW[i - 1] if i > 0 else None,
        "next": ORDER_NEW[i + 1] if i + 1 < len(ORDER_NEW) else None,
        "image": f"/api/images/{r['id']}",
        "image_url": r.get("image_url"),
        "local_image_path": r["_local_image"],
        "image_w": (r["_size"] or (None, None))[0],
        "image_h": (r["_size"] or (None, None))[1],
        "story_text": r.get("story_text"),
    }


@app.get("/api/stories/{story_id}/raw")
def get_story_raw(story_id: str) -> dict:
    """The record exactly as stored in my_rag_stories.json (no derived fields)."""
    r = BY_ID.get(story_id)
    if r is None:
        raise HTTPException(status_code=404, detail=f"unknown story id: {story_id}")
    return {k: v for k, v in r.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


REMOTE_IMAGE_HOSTS = {
    h.strip().lower()
    for h in os.environ.get("IMAGE_REMOTE_HOSTS", "feimatrix.com").split(",")
    if h.strip()
}


def _remote_image_allowed(url: str) -> bool:
    """Only https URLs on a known host — never turn /api/images into an open
    redirect for whatever a record's ``image_url`` happens to say."""
    if not url.startswith("https://"):
        return False
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in REMOTE_IMAGE_HOSTS)


@app.get("/api/images/{story_id}")
def get_image(story_id: str):
    r = BY_ID.get(story_id)
    if r is None:
        raise HTTPException(status_code=404, detail=f"unknown story id: {story_id}")
    if r["_local_image"] is None:
        # 3 records were never downloaded -> hand the browser the remote URL.
        remote = r.get("image_url") or ""
        if _remote_image_allowed(remote):
            return RedirectResponse(remote, status_code=307)
        raise HTTPException(status_code=404, detail="no local image, no trusted remote image")
    return FileResponse(
        r["_local_image"],
        media_type="image/webp",
        headers={"Cache-Control": "public, max-age=31536000, immutable", **SEC_HEADERS},
    )


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


@app.get("/api/tags")
def list_tags(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
) -> dict:
    ordered = sorted(TAG_INDEX, key=lambda t: (-len(TAG_INDEX[t]), t))
    meta, window = paginate(ordered, page, page_size)
    return {
        **meta,
        "items": [
            {"tag": TAG_DISPLAY[t], "count": len(TAG_INDEX[t])} for t in ordered[window]
        ],
    }


@app.get("/api/tags/{tag}")
def stories_by_tag(
    tag: str = Path(..., min_length=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
) -> dict:
    key = tag.strip().lower()
    ids = TAG_INDEX.get(key)
    if ids is None:
        raise HTTPException(status_code=404, detail=f"unknown tag: {tag}")
    meta, window = paginate(ids, page, page_size)
    return {
        **meta,
        "tag": TAG_DISPLAY[key],
        "items": [summarize(BY_ID[sid]) for sid in ids[window]],
    }


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


@app.get("/api/dates/{day}")
def stories_by_date(
    day: str = Path(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
) -> dict:
    ids = DATE_INDEX.get(day)
    if ids is None:
        raise HTTPException(status_code=404, detail=f"unknown date: {day}")
    meta, window = paginate(ids, page, page_size)
    return {
        **meta,
        "date": day,
        "items": [summarize(BY_ID[sid]) for sid in ids[window]],
    }


# ---------------------------------------------------------------------------
# Editors (bylines extracted from the "Editor: <name>" story_text line)
# ---------------------------------------------------------------------------


@app.get("/api/editors")
def list_editors(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
) -> dict:
    ordered = sorted(EDITOR_INDEX, key=lambda t: (-len(EDITOR_INDEX[t]), t))
    meta, window = paginate(ordered, page, page_size)
    return {
        **meta,
        "items": [
            {"editor": EDITOR_DISPLAY[t], "count": len(EDITOR_INDEX[t])}
            for t in ordered[window]
        ],
    }


@app.get("/api/editors/{editor}")
def stories_by_editor(
    editor: str = Path(..., min_length=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
) -> dict:
    key = editor.strip().lower()
    ids = EDITOR_INDEX.get(key)
    if ids is None:
        raise HTTPException(status_code=404, detail=f"unknown editor: {editor}")
    meta, window = paginate(ids, page, page_size)
    return {
        **meta,
        "editor": EDITOR_DISPLAY[key],
        "items": [summarize(BY_ID[sid]) for sid in ids[window]],
    }


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


@app.get("/api/search")
def search_stories(
    request: Request,
    q: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
) -> dict:
    _rate(request)
    hits = search(q)
    meta, window = paginate(hits, page, page_size)
    return {
        **meta,
        "query": q,
        "items": [{**summarize(r), "score": score} for score, r in hits[window]],
    }


# ---------------------------------------------------------------------------
# Semantic search (Granite-Embedding-97M via llama-server + local matrix)
# ---------------------------------------------------------------------------


def _require_vectors() -> None:
    if not EMB.ensure_loaded(len(STORIES)):
        raise HTTPException(
            status_code=503,
            detail="embedding 索引缺失或过期 — 运行 build_embeddings.py 生成 (见 README)",
        )


@app.get("/api/semantic")
def semantic_search(
    request: Request,
    q: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
) -> dict:
    """Cosine search over title+tags+story_text embeddings, best first."""
    _rate(request)
    _require_vectors()
    try:
        vec = EMB.embed_inputs([q[: EMB.MAX_CHARS]])[0]
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"embedding 服务不可达 ({EMB.EMBED_URL}): {exc}"
        )
    ids = EMB.row_ids()
    want = min(len(ids), page * page_size)
    ranked = EMB.rank(vec, want)
    window = ranked[(page - 1) * page_size :]
    items = [
        {**summarize(BY_ID[ids[row]]), "score": round(score, 4)}
        for score, row in window
        if ids[row] in BY_ID
    ]
    pages = max(1, (len(ids) + page_size - 1) // page_size)
    return {
        "total": len(ids),
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "query": q,
        "items": items,
    }


@app.get("/api/similar/{story_id}")
def similar_stories(
    story_id: str,
    request: Request,
    n: int = Query(8, ge=1, le=20),
) -> dict:
    """Nearest neighbours of one record's stored vector (no server call)."""
    _rate(request)
    _require_vectors()
    ids = EMB.row_ids()
    try:
        row = ids.index(story_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"unknown story id: {story_id}")
    vec = EMB._VECTORS[row]  # type: ignore[union-attr]
    ranked = [p for p in EMB.rank(vec, n + 1) if p[1] != row][:n]
    return {
        "id": story_id,
        "items": [
            {**summarize(BY_ID[ids[r]]), "score": round(s, 4)}
            for s, r in ranked
            if ids[r] in BY_ID
        ],
    }


# ---------------------------------------------------------------------------
# Random
# ---------------------------------------------------------------------------


@app.get("/api/random")
def get_random(n: int = Query(1, ge=1, le=20)) -> dict:
    return {"items": [summarize(r) for r in random_stories(n)]}


# ---------------------------------------------------------------------------
# Upload (API only, no UI). Auth: X-Upload-Token header == $UPLOAD_TOKEN.
# Uploads land as status=pending: invisible to every public route until
# approved. Embedding happens at approve time, so a dead embedding service
# never blocks ingestion.
# ---------------------------------------------------------------------------


@app.post("/api/upload", status_code=201)
async def upload_story(
    request: Request,
    image: UploadFile = File(...),
    title: str = Form(...),
    story_text: str = Form(...),
    tags: str = Form(""),
    editor: str = Form(""),
    x_upload_token: str | None = Header(default=None),
) -> dict:
    """Ingest one story as pending: webp + metadata in, nothing public yet."""
    _rate(request)
    _require_upload_token(x_upload_token)
    title = title.strip()
    story_text = story_text.strip()
    if not title or len(title) > 500:
        raise HTTPException(status_code=400, detail="title required, max 500 chars")
    if not story_text or len(story_text) > 100000:
        raise HTTPException(status_code=400, detail="story_text required, max 100000 chars")
    tag_list = [t.strip() for t in tags.replace("，", ",").split(",")]
    tag_list = [t for t in tag_list if t][:30]
    blob = await image.read()
    if not blob or len(blob) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="image required, max 20 MB")
    if len(blob) < 30 or blob[0:4] != b"RIFF" or blob[8:12] != b"WEBP":
        raise HTTPException(status_code=400, detail="image must be a .webp file")

    sid = secrets.token_hex(12)
    while sid in BY_ID or sid in PENDING:
        sid = secrets.token_hex(12)
    raw: dict = {
        "id": sid,
        "title": title,
        "status": "pending",
        "image_url": None,
        "local_image_path": f"./downloaded_images/{sid}.webp",
        "story_text": story_text,
        "tags": tag_list,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }
    if editor.strip():
        raw["editor"] = editor.strip()

    try:
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        (IMAGE_DIR / f"{sid}.webp").write_bytes(blob)
        persist_raw(raw)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"upload failed: {exc}")
    PENDING[sid] = raw
    return {
        "id": sid,
        "status": "pending",
        "image": f"/api/images/{sid}",
        "editor": raw.get("editor"),
        "tags": tag_list,
        "next": f"POST /api/approve/{sid} to publish",
    }


@app.get("/api/pending")
def list_pending(x_upload_token: str | None = Header(default=None)) -> dict:
    """Pending (not yet public) uploads, newest first."""
    _require_upload_token(x_upload_token)
    items = sorted(PENDING.values(), key=lambda r: r.get("created_at") or "", reverse=True)
    return {
        "total": len(items),
        "items": [
            {
                "id": r["id"],
                "title": r.get("title"),
                "tags": r.get("tags") or [],
                "created_at": r.get("created_at"),
            }
            for r in items
        ],
    }


@app.post("/api/approve/{story_id}", status_code=200)
def approve_story(
    story_id: str,
    x_upload_token: str | None = Header(default=None),
) -> dict:
    """Publish one pending record: embed, index, flip status in the file."""
    _require_upload_token(x_upload_token)
    raw = PENDING.get(story_id)
    if raw is None:
        raise HTTPException(status_code=404, detail=f"unknown pending id: {story_id}")
    raw = dict(raw, status="published")
    text = EMB.record_text(raw)
    try:
        vec = EMB.embed_inputs([text])[0]
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"embedding 服务不可达 ({EMB.EMBED_URL}): {exc} — 记录仍 pending，重试 later",
        )
    with _APPROVE_LOCK:
        if not set_status(story_id, "published"):
            raise HTTPException(status_code=500, detail="status flip failed in JSON file")
        rec = register_record(raw)
        embedded = EMB.try_append(vec, story_id, len(STORIES) - 1)
        PENDING.pop(story_id, None)
    return {
        "id": story_id,
        "status": "published",
        "embedded": embedded,
        "embedded_truncated": len(text) >= EMB.MAX_CHARS,
    }


@app.post("/api/reject/{story_id}", status_code=200)
def reject_story(
    story_id: str,
    x_upload_token: str | None = Header(default=None),
) -> dict:
    """Drop a pending record: out of the JSON file and off disk."""
    _require_upload_token(x_upload_token)
    if story_id not in PENDING:
        raise HTTPException(status_code=404, detail=f"unknown pending id: {story_id}")
    PENDING.pop(story_id, None)  # first: a failed unlink must not leave a phantom
    removed = remove_record(story_id)
    img = IMAGE_DIR / f"{story_id}.webp"
    try:
        img.unlink()
    except OSError:
        pass  # a stray file on disk is harmless; the record is already gone
    return {"id": story_id, "status": "rejected", "removed_from_file": removed}


# ---------------------------------------------------------------------------
# Crawler bits (only meaningful once SITE_URL is set by the proxy/deploy)
# ---------------------------------------------------------------------------

SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")


@app.get("/robots.txt", include_in_schema=False)
def robots() -> PlainTextResponse:
    lines = ["User-Agent: *", "Allow: /"]
    if SITE_URL:
        lines.append(f"Sitemap: {SITE_URL}/sitemap.xml")
    return PlainTextResponse("\n".join(lines) + "\n", headers=HTML)


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap() -> PlainTextResponse:
    if not SITE_URL:
        raise HTTPException(status_code=404, detail="set SITE_URL to serve a sitemap")
    urls = "".join(
        f"<url><loc>{SITE_URL}/story?id={r['id']}</loc></url>" for r in SORTED_NEW[:2000]
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{urls}</urlset>"
    )
    return PlainTextResponse(body, media_type="application/xml", headers=HTML)
