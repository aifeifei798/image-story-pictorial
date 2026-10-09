# AGENTS.md — my_rag_stories website

FastAPI gallery over `my_rag_stories.json` (10,485 records) + `downloaded_images/`.
No git repo, no CI, no tests. README.md is accurate — trust it, keep it in sync.

## Run

- `./serve.sh restart` (also `start|stop|status|logs|health`). One instance at a time.
- Backend changes (anything under `app/*.py`) need a restart. Static files
  (`app/static/*`) are served per-request — no restart needed.
- Deps: `uv pip install -r requirements.txt --python .venv`. The venv has no
  pip and no numpy — backend is stdlib-only (`urllib`, `struct`) except
  `python-multipart`, which FastAPI needs to parse `/api/upload` form data.

## Static-asset cache traps (will bite you)

- `GET /` and `/story` send `Cache-Control: no-cache`, but `/static/{css,js}`
  send `immutable` (1 year). After editing `app.js` or `tailwind.css` you MUST
  bump the `?v=N` query in the HTML `<link>`/`<script>` refs, or users see stale UI.
- `app/static/tailwind.css` is **generated, never hand-edit**:
  `node_modules/.bin/tailwindcss -i build/input.css -o app/static/tailwind.css --minify --config tailwind.config.js`
  then `.venv/bin/python build/check_classes.py` (every class token used in
  `index.html`/`story.html`/`app.js` must exist in the CSS — exit 1 if missing).

## Backend notes

- `app/dataset.py` runs at import (~0.5 s, ~520 MB peak): indices `BY_ID`,
  `TAG_INDEX`/`TAG_DISPLAY`, `DATE_INDEX`, `EDITOR_INDEX`/`EDITOR_DISPLAY`,
  `ORDER_NEW`/`POS`. Editor bylines are parsed from an `Editor: <name>` line
  inside `story_text` (`re.IGNORECASE` — a missing flag once zeroed the index).
- `app/embeddings.py` + `build_embeddings.py`: semantic search over a raw
  float32 matrix (`embeddings.f32` + `embeddings.ids.json`, 384-dim,
  L2-normalized so cosine = dot). Vectors come from an EXTERNAL llama-server
  (`../granite-embedding-97m-multilingual-r2/llama-server.sh`, port 8023,
  `EMBED_URL` override). If the service is down or row count != dataset size,
  `/api/semantic` and `/api/similar` return 502/503 by design.
- After `my_rag_stories.json` changes, re-run `.venv/bin/python build_embeddings.py`.
- Upload: `POST /api/upload` (multipart webp + title/story_text required,
  tags/editor optional; `X-Upload-Token` == `$UPLOAD_TOKEN`, gate crashes
  startup if unset). `serve.sh start` generates/persists the token in
  `.run/upload_token` (KEY=VALUE, gitignored — systemd's EnvironmentFile reads
  it too). Uploads land `status: pending` in `PENDING` (dataset.py splits them
  at import) — invisible to every public route until `POST /api/approve/{id}`
  (embeds + `register_record` + `try_append`; 502 if the embedding service is
  down, record stays pending) or dropped by `POST /api/reject/{id}`.
  `persist_raw`/`set_status`/`remove_record` hold an flock on `.run/dataset.lock`
  so concurrent uploads never race on the single JSON file. `register_record`
  inserts by `created_at` bisect (approve order ≠ upload order). Memory indices
  hot-update via `register_record` + `EMB.try_append` (only when a complete
  matrix exists, else `embedded: false` — backfill with `build_embeddings.py`).
  Approve's mutate section (`set_status` + `register_record` + `try_append`)
  runs under `_APPROVE_LOCK` — sync handlers share the threadpool and two
  concurrent approvals would desync `embeddings.ids.json` from the .f32 file.
  `/api/reject/{id}` pops PENDING *before* touching the file/disk so a failed
  unlink can't leave a phantom pending record.
- `/api/images/{id}` 307s to a record's `image_url` only for https hosts in
  `IMAGE_REMOTE_HOSTS` (default `feimatrix.com`, subdomains included) — that
  keeps the route from becoming an open redirect for dataset-controlled URLs.
- Frontend escaping: titles/editors/tags are data, so `app.js` (`esc()`) and
  `story.html`'s inline script escape every interpolation. Adding a new
  `innerHTML` sink for API content needs the same treatment — and bump the
  `?v=` in the HTML after touching `app.js`.
- Static UI: every `<script>` is an external file now (story.html's inline
  script moved to `app/static/story.js`), which is what lets `script-src` drop
  `'unsafe-inline'`. Adding an inline handler anywhere re-enables that XSS
  class. `story.html` is also read by `/story?id=` to inject per-record
  crawler metadata (`/api/images` now takes `?thumb=1` for the 480px grid
  copy — `?v=` in the HTML must be bumped after touching app.js).
- Grid thumbnails: `build/make_thumbs.py` writes `downloaded_thumbs/{id}.webp`
  (480px, ImageMagick/cwebp, ~33 KB avg vs ~146 KB) and `/api/images/{id}?thumb=1`
  serves them with an original-file fallback, so a half-built dir only costs
  bytes. `card()` requests `?thumb=1`; hero/lightbox/slideshow keep the original.
- `build/smoke.py` is the regression harness (no CI): copies the dataset to
  /tmp, boots uvicorn on a spare port, drives every route incl. moderation,
  auth, escaping, crawler tags, matrix integrity and the rate limiter, and
  exits non-zero on the first failure. Run it after any backend change —
  78 checks, ~40 s. `--keep` leaves the sandbox + server log for post-mortem.
- `deploy/backup.timer` + `.service` schedule `deploy/backup.sh` daily
  (`OnCalendar=daily`, `RandomizedDelaySec=30m`, `Persistent=true`); the unit
  is `Type=oneshot` + `Nice=10`/idle I/O. Install both, not just the script.
- `build/fetch_missing.py` downloads the records that have no local .webp, so
  `/api/images/{id}` stops 307-ing to the remote host (3 were fixed; health's
  `missing_local_images` tracks stragglers).

- `/api/similar/{id}` checks the id **before** the matrix: unknown id → 404
  even when the matrix is stale, known id on a stale matrix → 503 naming
  `build_embeddings.py`. `_require_vectors()` is what produces the
  "row count != dataset size" 503 (browse never depends on it).
- Rate limiter prunes empty per-IP deques at the 5000-entry ceiling instead of
  clearing the table — a full clear would hand every live client a fresh
  15-request budget.
- CSP carries `frame-ancestors 'self'` next to `X-Frame-Options: SAMEORIGIN`.
- Frontend affordances added after the review: `←/→` page through
  prev/next on the story page (scoped so input fields keep their arrows),
  the slideshow pauses on `visibilitychange` and resumes on return (a hidden
  tab otherwise keeps the 3 s cadence and preloads images), `skip-link` +
  `<noscript>` banner on both pages, and `prefers-reduced-motion` kills the
  ambient drift/shimmer and the transitions. Any CSS/JS change needs the `?v=`
  bump in index.html/story.html.
- `deploy/systemd.service` uses `MemoryMax=700M` (cgroup v2 name) with the
  legacy `MemoryLimit` line kept commented for older systemd.

- `MAX_CHARS = 3000` (≈750 tokens — fits the 1024-token slot of llama-server
  `-c 8192 --parallel 8`). Changing it invalidates the old matrix — re-run
  `build_embeddings.py` after any `record_text`/MAX_CHARS change. Slot math:
  llama-server gives each parallel slot `n_ctx / --parallel` tokens, so a
  record over ~3000 chars 400s under `--parallel 8` (raise `-c` or drop
  `--parallel` to embed longer stories). Corpus: 10485 records, chars p50 1323
  / p99 2455 / max 5687 (1074 tok) → 51 records lose their tail.
  `build_embeddings.py` writes `.part` files and renames on success, so a
  killed rebuild can't leave a short matrix on disk.

## Frontend notes (`app/static/app.js`, no framework)

- Global state `S` (`mode`, `tag|day|editor|q|sim`, `page`, `size: 25`).
  Modes: `browse|random|tags|tag|date|editors|editor|search|semantic|similar`.
- Search box: CJK queries skip keyword search (corpus is ~all English, 7/10485
  CJK records) and go straight to `/api/semantic`; English keyword with 0 hits
  auto-retries once as semantic (`S._fb` guard prevents loops).
- Card chips/date buttons sit inside the card `<a>` — the grid delegated
  handler must `preventDefault()` or clicks navigate to the story page.
- `/api/similar?n=` caps at 20, but page size is 25 — frontend clamps
  (`Math.min(ps, 20)`); don't regress this.
- i18n: all user-facing strings go through `app/static/lang.js` (`T(key)`,
  `data-i18n` / `data-i18n-html` / `data-i18n-ph` attrs, `mastNo`/`mastSub`
  helpers). Default `en`, `?lang=` overrides + persists to localStorage.
  `lang.js` must load before `app.js` / the story inline script.
