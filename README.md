# image-story-pictorial — FastAPI image-story service

Serves the 10,485 records in `my_rag_stories.json` plus the 10,482 local
`.webp` files in `downloaded_images/`, and renders them in a bilingual
(EN default, 中文 toggle) Tailwind CSS magazine-style gallery UI. Keyword +
tag search plus embedding semantic search (RAG-ready: precomputed vectors,
no reranker yet).

中文版: [README.zh-CN.md](README.zh-CN.md)

## Run

`serve.sh` owns the process — pidfile in `.run/`, log in `.run/serve.log`:

```bash
uv pip install -r requirements.txt --python .venv   # fastapi, uvicorn

./serve.sh start          # background, defaults HOST=0.0.0.0 PORT=8000
./serve.sh status         # pid, tracked/untracked, uptime, RSS, /api/health
./serve.sh stop           # TERM, escalates to KILL only if it ignores TERM
./serve.sh restart
./serve.sh logs           # tail -f .run/serve.log (-n N to size the window)
./serve.sh health         # pretty-printed /api/health
./serve.sh open           # every UI/API URL for the current port
```

Host and port come from the environment or flags, and one service at a time is
allowed — `start` reports the port the live pid is actually bound to:

```bash
PORT=9000 ./serve.sh start
./serve.sh start --host 127.0.0.1 --port 9000
```

`stop` also finds instances that were launched outside the script (by pattern,
not just from the pidfile), so a stray `uvicorn` on the port cannot block a
restart.

Equivalent one-liner without the script:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

Startup loads the JSON once (~0.5 s, ~85 MB steady RSS, ~520 MB peak during
parse). Images are never preloaded — each is streamed with `sendfile`.
Without `downloaded_images/`, image requests `307`-redirect to the remote
`image_url`, so the site still works — but only for https hosts listed in
`IMAGE_REMOTE_HOSTS` (default `feimatrix.com`), so the redirect can't be aimed
at an arbitrary address.

Optional generated assets (both additive — skip either and the site still
works, it just costs bytes):

```bash
# 480px grid thumbnails: a 25-card page drops from ~3.6 MB to ~0.4 MB
.venv/bin/python build/make_thumbs.py            # ImageMagick or cwebp
# fetch the 3 records that have no local image (kills the 307 hop)
.venv/bin/python build/fetch_missing.py
./serve.sh restart
```

Regression harness — boots a throwaway copy of the site on a spare port and
drives every route (headers, filters, search, semantic, moderation
upload→approve→reject, auth, escaping, crawler tags, matrix integrity, rate
limit). No CI needed: run it after any backend change, it exits non-zero on the
first failure and prints `N passed, 0 failed`:

```bash
.venv/bin/python build/smoke.py [-v] [--keep]
```

## Deploy on a small (1 GB) box

The site runs on a 1 GB VPS ("chicken") with these rules:

- **swap first**: `sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile` and add to `/etc/fstab`. Startup peak is ~520 MB; without swap the box OOM-kills during boot.
- **prefers-reduced-motion / a11y baseline**: skip link + noscript banner on
  both pages, `←/→` paging on the record view, and the ambient drift/shimmer
  animations disabled under `prefers-reduced-motion: reduce`.
- **one uvicorn worker, always**: all indices are in-memory and uploads rewrite
  one JSON file — multiple workers fork the dataset and race on writes.
- **systemd, not serve.sh**: `deploy/systemd.service` (capped at 700 MB RSS,
  restarts on crash, journald instead of a growing serve.log). serve.sh stays
  for local dev.
- **Caddy in front**: `deploy/Caddyfile` — TLS + HSTS terminate at the proxy,
  uvicorn stays plain HTTP on 127.0.0.1:8000. Caddy **overwrites**
  `X-Forwarded-For` with the real peer, which is what the app's rate limiter
  reads (15 requests / 20 s per IP on search/semantic/similar/upload); the
  default append behaviour would let a client spoof the first hop.
- **backup timer**: `deploy/backup.sh` snapshots the JSON + vectors (keep 5) —
  one corrupt write means all 10k records. Schedule it with the bundled unit:
  ```bash
  sudo cp deploy/backup.service deploy/backup.timer /etc/systemd/system/
  sudo systemctl enable --now image-story-backup.timer
  systemctl list-timers image-story-backup.timer   # next fire time
  ```
  It runs `Type=oneshot` at `Nice=10`/idle I/O, so it never competes with the
  app for the box's one core.
- **thumbnails are cheap to rebuild**: `downloaded_thumbs/` is regenerable
  (`build/make_thumbs.py`) and absent-by-default — the API falls back to the
  original. Build it once after deploy and the grid serves 480px copies;
  the detail page, lightbox and slideshow keep using the original file.
- **embedding service**: Granite-97M llama-server needs ~300-500 MB — on a 1 GB
  box it fits only with swap and a small context (`-c 512 -t 1`), and semantic
  queries will be slow. Alternative: keep the chicken browse-only (semantic
  answers 503 by design) and tunnel `EMBED_URL` from the big box
  (`ssh -R 8023:localhost:8023 bigbox`).
- memory budget: app ~300 MB + Caddy ~50 MB + embedding ~400 MB ≈ 750 MB —
  tight but survivable with swap; vectors are stored as `array('f')` rows
  (~16 MB) instead of Python float lists (~130 MB) to keep the app under
  ~300 MB.

## Web UI

Every page renders in the browser from the same JSON API. `/story?id=<id>`
additionally ships crawler metadata (`<title>`, description, `og:*`,
canonical, sitemap `lastmod`) so link unfurlers and search engines see
something useful; `SITE_URL` sets the public origin for those absolute URLs
(and enables `/sitemap.xml`).

Accessibility and courtesy bits: a keyboard-only **skip link** on both pages, a
`<noscript>` banner (the JSON API still works without JS), `←/→` paging on the
story view, and `prefers-reduced-motion` turning the ambient drift/shimmer
transitions off. The slideshow pauses while the tab is hidden.

Open `http://localhost:8000/` — the browser fetches the same JSON API and paints
it into a Tailwind-classed DOM (dark gold-on-black magazine theme, CSS
multi-column masonry, sticky glass navbar, masthead + cover story + editor
wall). No framework, no CDN, works offline. UI strings live in
`app/static/lang.js` (English default, `?lang=zh` overrides and persists).

| Route | Serves |
|---|---|
| `GET /` | gallery shell (`index.html`, `Cache-Control: no-cache`) |
| `GET /story?id=<id>` | single-record detail page (`story.html`) |
| `GET /static/{name}` | whitelist by extension: `.css` `.js` `.svg` only, `immutable` |

UI modes, all driven by query params on `/`:

| URL | Mode |
|---|---|
| `/` | browse, newest first |
| `/?order=old` | oldest first |
| `/?mode=random` | `/api/random` shuffle |
| `/?mode=tags` | tag wall, chips scaled by count |
| `/?mode=editors` | editor wall, chips scaled by count |
| `/?editor=Monica` | one editor's works |
| `/?tag=urban%20romance` | one tag |
| `/?date=2026-02-07` | one day's stories |
| `/?q=school+uniform` | keyword search, hits highlighted with `<mark>` |
| `/?q=校服` | CJK queries go straight to semantic search (corpus is English, keyword would miss); English keyword with 0 hits also falls back to semantic |
| `/?similar=<id>` | nearest neighbours of one record (story page ✦ Similar) |
| `/?lang=zh` | Chinese UI (default English, toggle in nav, persists) |

The nav also has a slideshow button: fullscreen overlay, one random story
(image + full text) every 3 s, click anywhere or ESC to stop.

Cards link to `/story?id=…`; the detail page has prev/next navigation,
"✦ Similar" (embedding neighbours via `/api/similar`) and "🎲 Surprise me".
Clicking a card's tag, date or the editor byline filters to that facet.

Tailwind v4.3.3 is compiled locally (`@tailwindcss/cli`), so `app/static/tailwind.css`
is generated output — regenerate, never hand-edit:

```bash
node_modules/.bin/tailwindcss -i build/input.css -o app/static/tailwind.css \
  --minify --config tailwind.config.js
.venv/bin/python build/check_classes.py   # asserts every class token in the UI compiles
```

`package.json` pulls `@tailwindcss/cli ^4.1.0`; `node_modules/` (~21 MB) at the
repo root exists only to build that one CSS file. Delete it and the UI still
works, it just cannot be rebuilt.

Static assets under `/static/` are served `immutable` — after editing `app.js`
or `tailwind.css`, bump the `?v=N` query on the `<link>`/`<script>` refs.

## API

| Route | Notes |
|---|---|
| `GET /api/health` | totals, tag/editor counts, date range, ids with no local image, embedding status |
| `GET /api/stories?page=1&page_size=20&order=new\|old` | metadata list, `story_text` omitted |
| `GET /api/stories/{id}` | full record incl. `story_text`, `editor`, prev/next ids |
| `GET /api/stories/{id}/raw` | record verbatim from the JSON file |
| `GET /api/images/{id}` | `image/webp` bytes, `Cache-Control: immutable`; `?thumb=1` prefers the 480px grid copy (original when absent) |
| `GET /api/tags?page=&page_size=` | all tags by count desc |
| `GET /api/tags/{tag}?page=` | URL-encode spaces (`urban%20romance`), case-insensitive |
| `GET /api/dates/{date}?page=` | one day (`2026-02-07`), newest first |
| `GET /api/editors?page=` | 109 bylines from the `Editor:` story line, by count |
| `GET /api/editors/{editor}?page=` | one editor's works (`Monica`), newest first |
| `GET /api/search?q=&page=` | substring AND over title+tags+story_text, OR fallback |
| `GET /api/semantic?q=&page=` | cosine search over Granite-Embedding-97M vectors |
| `GET /api/similar/{id}?n=8` | nearest neighbours of one record (n ≤ 20, no server call) |
| `GET /api/random?n=1` | n ≤ 20 |
| `POST /api/upload` | ingest one story: multipart `image` (.webp) + `title` + `story_text` (required), `tags` (comma-separated) + `editor` (optional). Header `X-Upload-Token` must equal `$UPLOAD_TOKEN`. Lands as `status: pending` — invisible to every public route until approved |
| `GET /api/pending` | list pending uploads (token) |
| `POST /api/approve/{id}` | publish one pending record: embed + index + flip status (token; 502 if the embedding service is down, record stays pending) |
| `POST /api/reject/{id}` | drop a pending record from the JSON file and disk (token) |

Paging: `page` ≥ 1, `page_size` 1–50, responses carry
`{total, page, page_size, pages, items}`.

```bash
curl localhost:8000/api/health
curl "localhost:8000/api/stories?page=1&page_size=5"
curl -O localhost:8000/api/images/6988b858eb929600014610ee
curl "localhost:8000/api/search?q=school+uniform"
curl "localhost:8000/api/semantic?q=school+uniform"
curl "localhost:8000/api/similar/6988b858eb929600014610ee?n=5"
curl "localhost:8000/api/tags/urban%20romance"
```

Upload (API only, no UI; token printed by `./serve.sh start`, persisted in
`.run/upload_token`):

```bash
TOKEN=$(cat .run/upload_token)
curl -X POST localhost:8000/api/upload -H "X-Upload-Token: $TOKEN" \
  -F "image=@photo.webp;type=image/webp" \
  -F "title=Midnight Diner" \
  -F "story_text=..." \
  -F "tags=noir, night" -F "editor=Monica"
# -> {"id": ..., "image": "/api/images/...", "embedded": true}
```

Interactive docs at `/docs`.

## Layout

```
app/__init__.py   package marker
app/dataset.py    one-shot load + indices (BY_ID, TAG_INDEX, DATE_INDEX, EDITOR_INDEX, ORDER_NEW/POS, SEARCH, _webp_size)
app/embeddings.py llama-server client + embeddings.f32 matrix + cosine rank
app/main.py       routes
app/static/       index.html · story.html · app.js · lang.js · story.js · tailwind.css (generated)
build_embeddings.py  embed all records -> embeddings.f32 + embeddings.ids.json
build/make_thumbs.py  480px grid thumbnails -> downloaded_thumbs/ (optional)
build/fetch_missing.py  download the records that have no local image
build/smoke.py      end-to-end regression harness (throwaway server, spare port)
embeddings.f32 / .ids.json  precomputed (N, 384) float32 matrix + row ids (gitignored, rebuild locally)
serve.sh          start/stop/restart/status/logs/health/open
.run/             serve.pid · serve.log (created by serve.sh)
tailwind.config.js  build input: content globs = the UI sources
build/input.css     @import tailwindcss entry
build/check_classes.py  class-token → compiled-selector assertion
package.json        @tailwindcss/cli ^4.1.0
```

## Embedding semantic search

The vector model runs outside this service:
`granite-embedding-97m-multilingual-r2/llama-server.sh`
(`:8023`, 384 dims, L2-normalized, cosine = dot product). This site only keeps
the precomputed matrix; each query embeds once (~50 ms) then ranks 10k dot
products (~0.15 s). No third-party dependencies.

```bash
# 1. start the embedding service (sibling directory)
../granite-embedding-97m-multilingual-r2/llama-server.sh
# 2. build the matrix (~1 minute, 16.1 MB)
.venv/bin/python build_embeddings.py [--batch 32]
# 3. restart this site
./serve.sh restart
```

Re-run step 2 after `my_rag_stories.json` changes; when the row count no
longer matches, `/api/semantic` and `/api/similar` answer `503`. The
`EMBED_URL` env var changes the service address (default
`http://localhost:8023`). The `embedding` section of `/api/health` reports
`indexed/total/ready/live`.

`live: false` (or a `502` from `/api/semantic`) means the llama-server is not
running — it is a separate process and `serve.sh` does not manage it:

```bash
cd ../granite-embedding-97m-multilingual-r2 && ./llama-server.sh   # :8023
```

Browse, tags, dates, editors and keyword search keep working either way; only
semantic/similar and `/api/approve` need the service. `/api/similar` tells the
two failure modes apart: an unknown id is `404` even with a stale matrix, a
known id on a stale matrix is `503` naming `build_embeddings.py`.

The slot budget is `n_ctx / --parallel` tokens: with the shipped `-c 8192
--parallel 8` that is 1024 tokens, which is why `MAX_CHARS = 3000` (≈750
tokens). 51 of 10485 stories are longer (max 5687 characters / 1074 tokens)
and get their tail clipped; raise `-c` or lower `--parallel` to embed them
whole. `build_embeddings.py` writes `.part` files and renames on success, so a
killed rebuild leaves the previous matrix intact.

`summarize()` reads each `.webp` header (pure Python, no Pillow) and returns
`image_w` / `image_h`, so the UI can set `width`/`height` on every `<img>` —
masonry lays out with zero reflow.

## Known data quirks (handled)

- 3 records store a remote URL in `local_image_path` instead of a path, so
  `GET /api/images/{id}` used to answer `307` to that host. They were fetched
  locally by `build/fetch_missing.py` (health now reports
  `missing_local_images: 0`); the 307 fallback stays for future records.
- 9 records have no tags; they appear in `/api/stories` but match no tag.
- 2 records carry quotes/angle brackets inside a title or tag
  (`"we love you."`, `<Whispers>`), so every template that interpolates API
  data HTML-escapes it first; the API keeps the original bytes.
- 2,201 records have no `Editor:` byline; they match no editor filter.
- Titles repeat (9,284 unique of 10,485) — always key on `id`.
- `created_at` is uniform ISO-8601 UTC, so string sort == date sort.
