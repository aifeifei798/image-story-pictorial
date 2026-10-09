# my_rag_stories — FastAPI image-story service

Serves the 10,485 records in `my_rag_stories.json` plus the 10,482 local
`.webp` files in `downloaded_images/`, and renders them in a bilingual
(默认英文，导航可切中文) Tailwind CSS magazine-style gallery UI. Keyword +
tag search plus embedding semantic search (RAG-ready: precomputed vectors,
no reranker yet).

英文版: [README.md](README.md)

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
`image_url`, so the site still works — but only for https hosts里
`IMAGE_REMOTE_HOSTS`（default `feimatrix.com`），so the redirect can't be
aimed at an arbitrary address。

Optional 生成物（都 optional，skip 里 site still works，only bytes 里 cost）:

```bash
# 480px 网格缩略图：一页 25 张 from ~3.6 MB → ~0.4 MB
.venv/bin/python build/make_thumbs.py            # ImageMagick 或 cwebp
# 抓回 3 张 missing local image（kill the 307 hop）
.venv/bin/python build/fetch_missing.py
./serve.sh restart
```

回归 harness里（boot a throwaway copy on a spare port，drive every route：
headers、filters、search、semantic、moderation upload→approve→reject、auth、
escaping、crawler tags、matrix integrity、rate limit）。No CI 里 need：any
backend change 后 run it，first failure 即 non-zero exit，PASS all 里
`N passed, 0 failed`:

```bash
.venv/bin/python build/smoke.py [-v] [--keep]
```

## 部署在 1G 小鸡

这套在 1G 内存 VPS（"小鸡"）能跑，规则：

- **swap 先**：`sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile` 并 `/etc/fstab`里。启动峰值 ~520 MB，无 swap 即 OOM-kill 里。
- **uvicorn 单 worker，永远**：索引全在内存，上传重写单 JSON 文件——多 worker 即数据集 fork + 写 race。
- **systemd 而非 serve.sh**：`deploy/systemd.service`（RSS 封顶 700M、崩溃自动重启、journald 而非 growing serve.log）。serve.sh 保持 for 本地 dev。
- **Caddy 前面**：`deploy/Caddyfile` — TLS + HSTS 终结 at 代理，uvicorn 保持 plain HTTP on 127.0.0.1:8000。Caddy 里 `X-Forwarded-For` 里 overwrite with the real peer for 的 app 的 rate limiter（15 req / 20 s per IP on search/semantic/similar/upload）——append 即 first hop spoofable。
- **backup timer**：`deploy/backup.sh` 快照 JSON + vectors（keep 5）——one corrupt write 即 all 10k records 全灭。Schedule 里 bundled unit：
  ```bash
  sudo cp deploy/backup.service deploy/backup.timer /etc/systemd/system/
  sudo systemctl enable --now image-story-backup.timer
  systemctl list-timers image-story-backup.timer   # next fire time
  ```
  它里 `Type=oneshot` + `Nice=10`/idle I/O，so 里 never compete with the app for 一 core。
- **thumbnails 里 cheap to rebuild**：`downloaded_thumbs/` 里 regenerable
  （`build/make_thumbs.py`）and absent-by-default——API 里 fallback to 原图。
  Deploy 后 build once，grid 里 480px copies serve；detail page、lightbox、
  slideshow 里 still 原图。
- **embedding 服务**：Granite-97M llama-server 需要 ~300-500 MB——在 1G 小鸡里它 only fits with swap + small context (`-c 512 -t 1`)，semantic 查询 will be slow。Alternative: 小鸡 keep browse-only（semantic 503 by design）+ tunnel `EMBED_URL` from 大 box（`ssh -R 8023:localhost:8023 bigbox`）。
- memory budget: app ~300 MB + Caddy ~50 MB + embedding ~400 MB ≈ 750 MB——tight but survivable with swap；vectors 是 stored as `array('f')` rows (~16 MB) instead of Python float lists (~130 MB) to keep the app under ~300 MB.

## Web UI

Every page 里 browser 里 renders from 里 same JSON API。`/story?id=<id>`
additionally 里 crawler metadata（`<title>`、description、`og:*`、canonical、
sitemap `lastmod`），so link unfurlers and search engines 里 see useful；
`SITE_URL` 里 sets 里 public origin for those absolute URLs（and enables
`/sitemap.xml`）。

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
| `/?lang=zh` | Chinese UI (default English, toggle in nav, persists) |
| `/?tag=urban%20romance` | one tag |
| `/?date=2026-02-07` | one day's stories |
| `/?q=school+uniform` | keyword search, hits highlighted with `<mark>` |
| `/?q=校服` | 含 CJK 的查询直接走语义搜索（语料是英文，关键词必空）；英文关键词 0 命中时也自动降级到语义 |
| `/?similar=<id>` | nearest neighbours of one record (story page ✦ 语义相似) |

The nav also has a slideshow button: fullscreen overlay, one random story
(image + full text) every 3 s, click anywhere or ESC to stop.

Cards link to `/story?id=…`; the detail page has prev/next navigation,
"✦ 语义相似" (embedding neighbours via `/api/similar`) and "🎲 随机一张".
Clicking a card's tag, date or the editor byline filters to that facet.
Paging is driven by the 前/后 buttons under the grid.

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
| `GET /api/images/{id}` | `image/webp` bytes, `Cache-Control: immutable` | | `image/webp` bytes，`Cache-Control: immutable`；`?thumb=1` 里 prefers 480px grid copy（absent 里 original） |
| `GET /api/tags?page=&page_size=` | all tags by count desc |
| `GET /api/tags/{tag}?page=` | URL-encode spaces (`urban%20romance`), case-insensitive |
| `GET /api/dates/{date}?page=` | one day (`2026-02-07`), newest first |
| `GET /api/editors?page=` | 109 bylines from the `Editor:` story line, by count |
| `GET /api/editors/{editor}?page=` | one editor's works (`Monica`), newest first |
| `GET /api/search?q=&page=` | substring AND over title+tags+story_text, OR fallback |
| `GET /api/semantic?q=&page=` | cosine search over Granite-Embedding-97M vectors |
| `GET /api/similar/{id}?n=8` | nearest neighbours of one record (n ≤ 20, no server call) |
| `GET /api/random?n=1` | n ≤ 20 |
| `POST /api/upload` | 上传一条故事：multipart `image`（.webp）+ `title` + `story_text`（必填），`tags`（逗号分隔）+ `editor`（选填）。请求头 `X-Upload-Token` 必须等于 `$UPLOAD_TOKEN`。落盘为 `status: pending`，完全隐形到所有公开路由 until 批准 |
| `GET /api/pending` | 列出待审上传（token） |
| `POST /api/approve/{id}` | 发布一条待审记录：embed + 索引 + 翻 status（token；embedding 服务挂了就 502，记录 stays pending） |
| `POST /api/reject/{id}` | 删除一条待审记录（JSON 里 + 磁盘里）（token） |
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

Upload（纯 API，无页面；token 由 `./serve.sh start` 生成并存于
`.run/upload_token`）：

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
app/dataset.py    one-shot load + indices (BY_ID, TAG_INDEX, SEARCH, _webp_size)
app/embeddings.py llama-server client + embeddings.f32 matrix + cosine rank
app/main.py       routes
app/static/       index.html · story.html · app.js · lang.js · story.js · tailwind.css (generated)
build_embeddings.py  embed all records -> embeddings.f32 + embeddings.ids.json
build/make_thumbs.py  480px grid thumbnails -> downloaded_thumbs/ (optional)
build/fetch_missing.py  下载 missing local image 里 records
build/smoke.py      end-to-end regression harness (throwaway server, spare port)
embeddings.f32 / .ids.json  precomputed (N, 384) float32 matrix + row ids
serve.sh          start/stop/restart/status/logs/health/open
.run/             serve.pid · serve.log (created by serve.sh)
tailwind.config.js  build input: content globs = the 3 UI sources
build/input.css     @import tailwindcss entry
build/check_classes.py  class-token → compiled-selector assertion
package.json        @tailwindcss/cli ^4.1.0
```

## Embedding 语义搜索

向量模型跑在站外：`granite-embedding-97m-multilingual-r2/llama-server.sh`
（`:8023`，384 维，已 L2 归一化，cosine = 点积）。本站只存预计算矩阵，
查询时 embed 一次 query（~50 ms）再做 10k 点积排序（~0.15 s），无第三方依赖。

```bash
# 1. 先起 embedding 服务（隔壁目录）
../granite-embedding-97m-multilingual-r2/llama-server.sh
# 2. 生成矩阵（约 1 分钟，16.1 MB）
.venv/bin/python build_embeddings.py [--batch 32]
# 3. 重启本站
./serve.sh restart
```

`my_rag_stories.json` 变更后重跑第 2 步；行数对不上时 `/api/semantic` 和
`/api/similar` 返回 `503`。`EMBED_URL` 环境变量改服务地址
（默认 `http://localhost:8023`）。`/api/health` 的 `embedding` 段给出
`indexed/total/ready/live` 状态。

`live: false`（or `/api/semantic` answers `502`）里 llama-server not running —
it 是 a separate process and `serve.sh` 里 不管它：

```bash
cd ../granite-embedding-97m-multilingual-r2 && ./llama-server.sh   # :8023
```

Browse、tags、dates、editors、keyword search 里 all不受影响；only
semantic/similar + `/api/approve` 需要它。`/api/similar` 里 two failure modes 里
distinguish：unknown id 里 `404`（even 里 stale matrix），known id 里 stale
matrix 里 `503` naming `build_embeddings.py`。

服务里 `-c 8192 --parallel 8` 里 right：llama-server 把 `-c` 的 context 里 split
among parallel slots，每个 slot 只 get `n_ctx / --parallel` tokens。`--parallel 8`
里每个 story 超过 ~3000 characters 即 400 rejected。`MAX_CHARS = 3000`（≈750
tokens）里 1024-token slot 里 fits。10485里 51 stories 超过 3000 characters
（max 5687 characters / 1074 tokens）and get their tail clipped。Embed them
whole: raise `-c` or lower `--parallel`。`build_embeddings.py` 里 `.part` 里
write + rename on success，so a killed rebuild leaves the previous matrix intact。

`summarize()` reads each `.webp` header (pure Python, no Pillow) and returns
`image_w` / `image_h`, so the UI can set `width`/`height` on every `<img>` —
masonry lays out with zero reflow.

## Known data quirks (handled)

- 3 records store a remote URL in `local_image_path` instead of a path，so `/api/images/{id}` 里 used to `307`。它们里 locally fetched by `build/fetch_missing.py`（health 里 now `missing_local_images: 0`）；307 fallback 里 still there for future records。
- 9 records have no tags; they appear in `/api/stories` but match no tag.
- 2 records里 title/tag 里 `"` `<` `>`（`"we love you."`、`<Whispers>`），so every
  innerHTML sink in app.js / story.html escapes API data first；API 里 bytes 里
  original。
- 2,201 records have no `Editor:` byline; they match no editor filter.
- Titles repeat (9,284 unique of 10,485) — always key on `id`.
- `created_at` is uniform ISO-8601 UTC, so string sort == date sort.
