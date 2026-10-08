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
- `MAX_CHARS = 24000` (≈6k tokens, fits an 8192-token llama-server context,
  `-c 8192`). Changing it invalidates the old matrix — re-run
  `build_embeddings.py` after any `record_text`/MAX_CHARS change.

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
