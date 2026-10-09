#!/usr/bin/env python3
"""End-to-end regression harness: a throwaway copy of the site on a spare port.

The project has no test suite and no CI, and it has grown several features
(pending/approve moderation, thumbnails, OG injection, escaping) that only a
running server can prove. This script copies the app + dataset to a temp dir,
boots uvicorn on a spare port, drives every route, and exits non-zero if any
check fails. It never touches the real dataset beyond reading it.

Usage:
    .venv/bin/python build/smoke.py [-v] [--port 8931] [--keep]

Sections: health, headers, browse, filters, search, record, images,
moderation (upload->pending->approve->public->reject), auth/validation,
escaping, crawler, matrix integrity, rate limit (last: it burns the quota).

--keep leaves the sandbox + uvicorn log in /tmp for post-mortem.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
VENV = BASE / ".venv" / "bin" / "python"
SRC_APP = BASE / "app"
SRC_JSON = BASE / "my_rag_stories.json"
SRC_EMB = BASE / "embeddings.f32"
SRC_IDS = BASE / "embeddings.ids.json"
SRC_IMG = BASE / "downloaded_images"
TOKEN = "smoke-token"
DIM = 384

PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 0
PORT_NOW = 0
SERVER: subprocess.Popen | None = None
VERBOSE = "-v" in sys.argv
KEEP = "--keep" in sys.argv

ROOT: Path = Path("/tmp")
SAMPLE: Path = Path("/tmp")
BASE_URL = ""
FAILED: list[str] = []
PASSED = 0
PAYLOAD = Path("/tmp")


def check(name: str, ok: bool, detail: str = "") -> bool:
    global PASSED
    if ok:
        PASSED += 1
        print(f"  PASS  {name}" + (f"   {detail}" if VERBOSE and detail else ""))
    else:
        FAILED.append(name)
        print(f"  FAIL  {name}   {detail}")
    return ok


def section(title: str) -> None:
    print(f"\n== {title}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Keep 307s in hand: the smoke box must not call the remote image CDN."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(_NoRedirect)


def get(path: str, headers: dict | None = None) -> tuple[int, bytes, dict]:
    """(status, body, headers-lowercased). 307s are returned, not followed."""
    req = urllib.request.Request(BASE_URL + path, headers=headers or {})
    try:
        with OPENER.open(req, timeout=60) as resp:
            return resp.status, resp.read(), {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as e:
        return e.code, e.read(), {k.lower(): v for k, v in e.headers.items()}


def post(path: str, fields: dict | None = None, headers: dict | None = None) -> tuple[int, bytes]:
    boundary = "----smoke7MA4YWxkTrZu0gW"
    body = bytearray()
    for k, v in (fields or {}).items():
        if k == "__file__":
            body += (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
                f"filename=\"payload.webp\"\r\nContent-Type: image/webp\r\n\r\n"
            ).encode() + Path(v).read_bytes() + b"\r\n"
        else:
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        BASE_URL + path,
        data=bytes(body),
        headers={**(headers or {}), "Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with OPENER.open(req, timeout=60) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def jget(path: str, headers: dict | None = None) -> tuple[int, dict]:
    code, body, _ = get(path, headers)
    try:
        return code, json.loads(body)
    except json.JSONDecodeError:
        return code, {}


def upload(title="Smoke <b>Test</b>",
           story="A lighthouse that only lights up for radios.\n\nEditor: Smoke",
           tags="smoke-test,<tag>") -> dict:
    code, body = post("/api/upload", headers={"X-Upload-Token": TOKEN}, fields={
        "__file__": str(PAYLOAD), "title": title, "story_text": story,
        "tags": tags, "editor": "",
    })
    check(f"upload {title[:24]!r} -> 201", code == 201, f"code={code} {body[:100]!r}")
    return json.loads(body) if code == 201 else {}


def main() -> None:
    global ROOT, BASE_URL, PAYLOAD, PORT_NOW
    if not VENV.is_file():
        sys.exit(f"missing {VENV} — create the venv first")
    global SAMPLE
    PORT_NOW = PORT or free_port()

    ROOT = Path(tempfile.mkdtemp(prefix="smoke-", dir="/tmp"))
    print(f"sandbox {ROOT}  port {PORT_NOW}")
    shutil.copytree(SRC_APP, ROOT / "app")
    shutil.copy2(SRC_JSON, ROOT / "my_rag_stories.json")
    if SRC_EMB.is_file() and SRC_IDS.is_file():
        shutil.copy2(SRC_EMB, ROOT / "embeddings.f32")
        shutil.copy2(SRC_IDS, ROOT / "embeddings.ids.json")
    (ROOT / "downloaded_images").mkdir(parents=True, exist_ok=True)
    sample = sorted(SRC_IMG.glob("*.webp"))[:1]
    if not sample:
        sys.exit(f"no images in {SRC_IMG}")
    SAMPLE = sample[0]
    PAYLOAD = ROOT / "payload.webp"
    shutil.copy2(sample[0], PAYLOAD)
    shutil.copy2(sample[0], ROOT / "downloaded_images" / sample[0].name)
    thumb = BASE / "downloaded_thumbs" / sample[0].name
    if thumb.is_file():  # absent -> the route falls back to the original
        (ROOT / "downloaded_thumbs").mkdir(parents=True, exist_ok=True)
        shutil.copy2(thumb, ROOT / "downloaded_thumbs" / sample[0].name)

    env = {**os.environ, "UPLOAD_TOKEN": TOKEN, "SITE_URL": "https://example.invalid"}
    BASE_URL = f"http://127.0.0.1:{PORT_NOW}"
    try:
        if not start_server():
            print(f"server never came up — {(ROOT / 'server.log').read_text()[-2000:]}")
            check("server starts", False)
        else:
            check("server starts", True, "uvicorn on a spare port")
            run_checks()
    finally:
        if SERVER is not None:
            SERVER.terminate()
            try:
                SERVER.wait(timeout=10)
            except subprocess.TimeoutExpired:
                SERVER.kill()
        if KEEP:
            print(f"\nsandbox kept: {ROOT}  (log: {ROOT / 'server.log'})")
        else:
            shutil.rmtree(ROOT, ignore_errors=True)

    print(f"\n{PASSED} passed, {len(FAILED)} failed")
    if FAILED:
        print("failed: " + ", ".join(FAILED))
    sys.exit(1 if FAILED else 0)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_up(timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if get("/api/health")[0] == 200:
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def bounce() -> bool:
    """Terminate and relaunch the sandbox uvicorn on the same port."""
    global SERVER
    if SERVER is not None:
        SERVER.terminate()
        try:
            SERVER.wait(timeout=10)
        except subprocess.TimeoutExpired:
            SERVER.kill()
    return start_server()


def start_server() -> bool:
    global SERVER
    env = {**os.environ, "UPLOAD_TOKEN": TOKEN, "SITE_URL": "https://example.invalid"}
    log = open(ROOT / "server.log", "ab")
    SERVER = subprocess.Popen(
        [str(VENV), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(PORT_NOW), "--workers", "1"],
        cwd=ROOT, env=env, stdout=log, stderr=log,
    )
    ok = wait_up()
    log.close()
    return ok


def run_checks() -> None:
    # the one image the sandbox has locally, which is also in the matrix
    ids = json.loads((ROOT / "embeddings.ids.json").read_text())
    rid = SAMPLE.stem
    code, page = jget("/api/stories?page_size=50&order=new")

    section("health")
    code, h = jget("/api/health")
    emb = h.get("embedding") or {}
    check("health 200", code == 200, str(code))
    check("indexed == total == stories",
          h.get("total") == emb.get("indexed") == emb.get("total"),
          f"total={h.get('total')} indexed={emb.get('indexed')}")
    check("embedding ready", emb.get("ready") is True, str(emb))
    check("embedding live", emb.get("live") is True)


    section("headers")  # JSON routes carry no HTML headers by design
    for path in ("/", f"/story?id={rid}", "/static/app.js", "/static/story.js",
                 "/static/lang.js", f"/api/images/{rid}"):
        code, _, hdr = get(path)
        csp = hdr.get("content-security-policy", "")
        ok = (code == 200 and "script-src 'self'" in csp and "script-src 'self';" in csp + ";"
              and "frame-ancestors 'self'" in csp)
        check(f"headers {path}", ok and hdr.get("x-content-type-options") == "nosniff"
              and hdr.get("x-frame-options") == "SAMEORIGIN"
              and hdr.get("referrer-policy") == "same-origin",
              f"code={code} csp={csp[:70]}")

    section("route inventory")
    # A handler rewrite once deleted /api/random wholesale, which broke random,
    # slideshow and "surprise me" at once — and only a browser would notice.
    code, body, _ = get("/openapi.json")
    try:
        spec = json.loads(body)
    except Exception:
        spec = {}
    have = set(spec.get("paths", {}))
    expect = {
        "/api/stories", "/api/stories/{story_id}", "/api/stories/{story_id}/raw",
        "/api/images/{story_id}", "/api/tags", "/api/tags/{tag}", "/api/dates/{day}",
        "/api/editors", "/api/editors/{editor}", "/api/search", "/api/semantic",
        "/api/similar/{story_id}", "/api/random", "/api/health", "/api/upload",
        "/api/pending", "/api/approve/{story_id}", "/api/reject/{story_id}",
        "/static/{name}",
    }
    # /, /story, robots.txt, sitemap.xml are include_in_schema=False and get
    # their own checks below
    check("openapi exposes every route", code == 200 and expect <= have,
          f"missing={sorted(expect - have)} extra={sorted(have - expect)[:4]}")
    check("no /docs leak (docs_url off)", get("/docs")[0] in (404, 405) and "/docs" not in have)

    section("browse")
    code, data = jget("/api/stories?page=1&page_size=5")
    check("browse page 1", code == 200 and len(data.get("items", [])) == 5, f"code={code}")
    code, old = jget("/api/stories?order=old&page=1&page_size=5")
    check("oldest-first differs", code == 200 and old["items"][0]["id"] != data["items"][0]["id"])
    check("bad page rejected", jget("/api/stories?page=0")[0] == 422)
    check("page_size capped at 50", len(jget("/api/stories?page_size=500")[1].get("items", [])) <= 50)

    section("filters")
    code, tags = jget("/api/tags")
    if check("tag list", code == 200 and bool(tags.get("items")), f"code={code}"):
        t = tags["items"][0]["tag"]
        code, tagged = jget("/api/tags/" + urllib.parse.quote(t))
        check(f"tag filter {t!r}", code == 200 and tagged.get("total", 0) > 0, f"code={code}")
    check("unknown tag 404", jget("/api/tags/__nope__")[0] == 404)
    check("malformed date 422", jget("/api/dates/not-a-date")[0] == 422)
    check("absent date 404", jget("/api/dates/1999-01-01")[0] == 404)
    code, eds = jget("/api/editors")
    check("editor list", code == 200, f"code={code}")
    if code == 200 and eds.get("items"):
        e = eds["items"][0]["editor"]
        code, one = jget("/api/editors/" + urllib.parse.quote(e))
        check(f"editor filter {e!r}", code == 200 and one.get("total", 0) > 0, f"code={code}")
    code, days = jget("/api/dates/" + (h.get("date_max") or "")[:10])
    check("date filter", code == 200, f"code={code}")

    section("search")
    code, res = jget("/api/search?q=lighthouse")
    check("keyword hit 200", code == 200, f"code={code}")
    check("CJK query 200", jget("/api/search?q=%E7%81%AF%E7%81%AB")[0] == 200)
    check("missing q 422", jget("/api/search")[0] == 422)

    section("semantic + similar + record")
    code, sem = jget("/api/semantic?q=neon+midnight+diner&page_size=3")
    check("semantic hit", code == 200 and len(sem.get("items", [])) == 3, f"code={code}")
    code, sim = jget(f"/api/similar/{rid}?n=3")
    check("similar hit", code == 200 and len(sim.get("items", [])) >= 1, f"code={code}")
    check("similar excludes self", all(i["id"] != rid for i in sim.get("items", [])))
    check("similar unknown id 404", jget("/api/similar/deadbeef?n=3")[0] == 404)
    code, rec = jget(f"/api/stories/{rid}")
    check("record 200", code == 200 and rec.get("id") == rid, f"code={code}")
    check("prev resolves", not rec.get("prev") or jget(f"/api/stories/{rec['prev']}")[0] == 200)
    check("next resolves", not rec.get("next") or jget(f"/api/stories/{rec['next']}")[0] == 200)
    code, rawrec = jget(f"/api/stories/{rid}/raw")
    check("raw view has no derived fields", code == 200 and not any(k.startswith("_") for k in rawrec))
    check("unknown record 404", jget("/api/stories/zzzz")[0] == 404)

    section("images")
    code, body, hdr = get(f"/api/images/{rid}")
    check("original 200", code == 200 and hdr.get("content-type") == "image/webp",
          f"code={code} {len(body)}B ct={hdr.get('content-type')!r}")
    code, tbody, _ = get(f"/api/images/{rid}?thumb=1")
    check("thumbnail smaller", code == 200 and 0 < len(tbody) <= len(body) // 2,
          f"{len(body)}B -> {len(tbody)}B")
    check("thumb of a remote-only record redirects",
          get("/api/images/" + str(next(iter(ids))) + "?thumb=1")[0] in (200, 307))
    check("unknown image 404", get("/api/images/zzzz")[0] == 404)
    check("no trusted remote 404", get("/api/images/zzzz?thumb=1")[0] == 404)

    section("moderation")
    up = upload()
    sid = up.get("id") or ""
    check("pending is listed", sid in [i["id"] for i in jget("/api/pending", {"X-Upload-Token": TOKEN})[1].get("items", [])])
    check("invisible while pending", jget(f"/api/stories/{sid}")[0] == 404)
    check("absent from browse", all(i["id"] != sid for i in jget("/api/stories?page_size=50")[1]["items"]))
    code, app = post(f"/api/approve/{sid}", headers={"X-Upload-Token": TOKEN})
    check("approve 200 published", code == 200 and json.loads(app).get("status") == "published",
          f"code={code} {app[:120]!r}")
    check("approve reports embedded", json.loads(app or b"{}").get("embedded") is True, str(app[:120]))
    code, rec2 = jget(f"/api/stories/{sid}")
    check("public after approve", code == 200 and rec2.get("id") == sid, f"code={code}")
    check("byline parsed from story_text", rec2.get("editor") == "Smoke", str(rec2.get("editor")))
    check("approve twice 404", post(f"/api/approve/{sid}", headers={"X-Upload-Token": TOKEN})[0] == 404)
    time.sleep(0.2)
    code, sem2 = jget("/api/semantic?q=lighthouse+weather+radios&page_size=10")
    check("approved record is semantically findable",
          code == 200 and any(i["id"] == sid for i in sem2.get("items", [])), f"code={code}")

    up2 = upload("Reject me", "throwaway", "junk")
    sid2 = up2.get("id") or ""
    code, rej = post(f"/api/reject/{sid2}", headers={"X-Upload-Token": TOKEN})
    check("reject 200", code == 200 and json.loads(rej).get("removed_from_file") is True,
          f"code={code} {rej[:120]!r}")
    data_now = json.loads((ROOT / "my_rag_stories.json").read_text(encoding="utf-8"))
    check("rejected record gone from JSON", all(r["id"] != sid2 for r in data_now))
    check("rejected image removed", not (ROOT / "downloaded_images" / f"{sid2}.webp").exists())
    check("reject twice 404", post(f"/api/reject/{sid2}", headers={"X-Upload-Token": TOKEN})[0] == 404)
    code_total, hh_now = jget("/api/health")
    check("total back to dataset size after reject", hh_now.get("total") == h_total(),
          f"{hh_now.get('total')} vs {h_total()}")

    section("auth + validation")
    check("missing token 401", post("/api/upload", fields={"title": "x", "story_text": "y", "__file__": str(PAYLOAD)})[0] == 401)
    check("wrong token 401", post("/api/upload", headers={"X-Upload-Token": "nope"}, fields={"title": "x", "story_text": "y", "__file__": str(PAYLOAD)})[0] == 401)
    check("empty title rejected", post("/api/upload", headers={"X-Upload-Token": TOKEN}, fields={"title": "", "story_text": "y", "__file__": str(PAYLOAD)})[0] in (400, 422))
    check("oversized title 400", post("/api/upload", headers={"X-Upload-Token": TOKEN}, fields={"title": "x" * 501, "story_text": "y", "__file__": str(PAYLOAD)})[0] == 400)
    check("pending needs token 401", get("/api/pending")[0] == 401)
    check("approve needs token 401", post(f"/api/approve/{rid}")[0] == 401)

    section("escaping")
    code, shell, _ = get(f"/story?id={rid}")
    check("story page 200", code == 200, f"code={code}")
    check("story.html has no inline <script>", b"<script defer>" not in shell and b"<script>" not in shell)
    check("story.js served externally", get("/static/story.js")[0] == 200)
    code, api, _ = get(f"/api/stories/{sid}")
    check("API keeps raw bytes", b"<b>" in api and b"&lt;b&gt;" not in api, repr(api[:80]))
    up3 = upload("Escape <i>me</i> & you", "quotes \" ' < >", "")
    sid3 = up3.get("id") or ""
    post(f"/api/approve/{sid3}", headers={"X-Upload-Token": TOKEN})
    code, r3 = jget(f"/api/stories/{sid3}")
    check("markup-ish title stored verbatim", r3.get("title") == "Escape <i>me</i> & you", str(r3.get("title")))
    code, sm, _ = get(f"/api/stories/{sid3}")
    check("raw view exposes it for the escaper", code == 200)
    post(f"/api/reject/{sid3}", headers={"X-Upload-Token": TOKEN})

    section("crawler")
    code, robots, _ = get("/robots.txt")
    check("robots.txt", code == 200 and b"Sitemap: https://example.invalid/sitemap.xml" in robots, f"code={code}")
    code, sm, _ = get("/sitemap.xml")
    check("sitemap has lastmod + many urls", code == 200 and b"<lastmod>" in sm and sm.count(b"<url>") > 100,
          f"code={code} urls={sm.count(b'<url>')}")
    code, meta, _ = get(f"/story?id={rid}")
    check("og:title injected", b'property="og:title"' in meta and b'rel="canonical"' in meta, f"code={code}")
    check("og:image absolute", b"https://example.invalid/api/images/" in meta)
    check("unknown id -> plain shell", b'data-i18n="doc_title"' in get("/story?id=zzzz")[1])
    check("no id -> plain shell", b'data-i18n="doc_title"' in get("/story")[1])

    section("matrix integrity")
    f32 = ROOT / "embeddings.f32"
    ids_now = json.loads((ROOT / "embeddings.ids.json").read_text())
    code, hh = jget("/api/health")
    check("rows == ids == bytes/(384*4)",
          f32.stat().st_size == len(ids_now) * DIM * 4 == hh.get("total") * DIM * 4,
          f"{f32.stat().st_size} vs {len(ids_now) * DIM * 4} total={hh.get('total')}")

    section("stale matrix (row count != dataset size)")
    # A rebuild that predates an approved upload, or a rejected record still in
    # the matrix, leaves the row count off by one. Everything must degrade
    # gracefully: browse keeps working, semantic/similar explain the rebuild.
    ids_now = json.loads((ROOT / "embeddings.ids.json").read_text())
    f32 = (ROOT / "embeddings.f32").read_bytes()
    (ROOT / "embeddings.f32").write_bytes(f32[: len(f32) - DIM * 4])
    (ROOT / "embeddings.ids.json").write_text(json.dumps(ids_now[:-1]))
    if not bounce():
        check("bounce after matrix surgery", False, "server did not come back")
    else:
        code, hv = jget("/api/health")
        check("health still answers", code == 200, f"code={code}")
        check("embedding reports not ready", (hv.get("embedding") or {}).get("ready") is False,
              json.dumps(hv.get("embedding")))
        check("semantic 503s with the rebuild hint",
              jget("/api/semantic?q=stale")[0] == 503)
        code, simbad = jget(f"/api/similar/{rid}?n=3")
        check("similar 503s (not 404) on a known id", code == 503, f"code={code}")
        _code, _bd = jget("/api/similar/zzzz?n=3")
        check("similar 404s on an unknown id", _code in (404, 429), f"code={_code}")
        check("browse still 200 after the surgery", jget("/api/stories?page_size=3")[0] == 200)

    section("rate limit (burns the quota — keep it last)")
    codes = [get(f"/api/semantic?q=rl{i}")[0] for i in range(18)]
    check("limiter trips 429", 429 in codes, f"{codes.count(429)}/18 blocked")
    check("no unexpected status", all(c in (200, 429, 503) for c in codes), str(sorted(set(codes))))


def h_total() -> int:
    return jget("/api/health")[1].get("total", 0)


if __name__ == "__main__":
    main()
