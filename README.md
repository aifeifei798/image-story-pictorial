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
`image_url`, so the site still works.

## Web UI

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
| `GET /api/images/{id}` | `image/webp` bytes, `Cache-Control: immutable` |
| `GET /api/tags?page=&page_size=` | all tags by count desc |
| `GET /api/tags/{tag}?page=` | URL-encode spaces (`urban%20romance`), case-insensitive |
| `GET /api/dates/{date}?page=` | one day (`2026-02-07`), newest first |
| `GET /api/editors?page=` | 109 bylines from the `Editor:` story line, by count |
| `GET /api/editors/{editor}?page=` | one editor's works (`Monica`), newest first |
| `GET /api/search?q=&page=` | substring AND over title+tags+story_text, OR fallback |
| `GET /api/semantic?q=&page=` | cosine search over Granite-Embedding-97M vectors |
| `GET /api/similar/{id}?n=8` | nearest neighbours of one record (n ≤ 20, no server call) |
| `GET /api/random?n=1` | n ≤ 20 |
| `POST /api/upload` | ingest one story: multipart `image` (.webp) + `title` + `story_text` (required), `tags` (comma-separated) + `editor` (optional). Header `X-Upload-Token` must equal `$UPLOAD_TOKEN`. The vector is embedded inline (502 if the embedding service is down, nothing written); indices hot-update, no restart needed |

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
app/static/       index.html · story.html · app.js · lang.js · tailwind.css (generated)
build_embeddings.py  embed all records -> embeddings.f32 + embeddings.ids.json
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

`summarize()` reads each `.webp` header (pure Python, no Pillow) and returns
`image_w` / `image_h`, so the UI can set `width`/`height` on every `<img>` —
masonry lays out with zero reflow.

## Known data quirks (handled)

- 3 records store a remote URL in `local_image_path` instead of a path.
  `GET /api/images/{id}` answers `307` to `image_url` for those ids
  (listed by `/api/health`).
- 9 records have no tags; they appear in `/api/stories` but match no tag.
- 2,201 records have no `Editor:` byline; they match no editor filter.
- Titles repeat (9,284 unique of 10,485) — always key on `id`.
- `created_at` is uniform ISO-8601 UTC, so string sort == date sort.
