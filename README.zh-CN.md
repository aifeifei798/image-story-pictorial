# image-story-pictorial — FastAPI 图片故事服务

站点数据来自 `my_rag_stories.json`（10,485 条记录）和 `downloaded_images/`
（10,485 个本地 `.webp` 图片），前端是一个 Tailwind CSS 杂志风格的画廊界面，
默认英文，导航栏可切到中文。关键词搜索、标签搜索、向量语义搜索三者并存
（RAG-ready：向量已预计算，reranker 尚未接入）。

英文版: [README.md](README.md)

## 运行

`serve.sh` 负责进程生命周期 — pid 文件在 `.run/`，日志在 `.run/serve.log`：

```bash
uv pip install -r requirements.txt --python .venv   # fastapi、uvicorn

./serve.sh start          # 后台，默认 HOST=0.0.0.0 PORT=8000
./serve.sh status         # pid、是否由脚本托管、运行时长、RSS、/api/health
./serve.sh stop           # 先发 TERM；进程不理睬时才升级到 KILL
./serve.sh restart
./serve.sh logs           # tail -f .run/serve.log（-n N 控制窗口行数）
./serve.sh health         # 格式化打印 /api/health
./serve.sh open           # 列出当前端口下所有 UI/API 地址
./serve.sh keep           # 看门狗：进程消失时把它拉回来
```

Host 与端口可由环境变量或命令行参数指定，同时只允许跑一个实例 —
`start` 会报出那个活着的 pid 实际绑定的端口：

```bash
PORT=9000 ./serve.sh start
./serve.sh start --host 127.0.0.1 --port 9000
```

`stop` 除了 pid 文件，还按进程名匹配那些不是由本脚本启动的实例，所以端口上
残留的 `uvicorn` 不会挡住一次重启。

`keep` 相当于本地版的 systemd `Restart=always`：它盯着 pid 文件，进程一旦
消失（崩溃、OOM 被杀、一次坏的部署）就在 10 秒内把它拉回来；连续失败五次
就放弃 —— 那是启动期就失败的情形（JSON 损坏、缺 token），重试永远没用。
想让它在关掉终端后继续活着，就后台跑：

```bash
nohup ./serve.sh keep >>.run/keep.log 2>&1 &   # 站点本身已经 nohup 了
```

按 Ctrl-C 只停看门狗、不动站点，所以它更像暂停键而不是停止键。部署机上
`deploy/systemd.service` 本来就有 `Restart=always` —— `keep` 只用于本地的
`serve.sh` 实例。

不用脚本的等价一行命令：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

启动时一次性加载 JSON（约 0.5 秒，稳态 RSS 约 85 MB，解析峰值约 520 MB）。
图片从不预载，全部走 `sendfile` 流式输出。缺少 `downloaded_images/` 时，图片
请求会 `307` 跳到远端 `image_url`，站点照样能用 —— 但只对 `IMAGE_REMOTE_HOSTS`
（默认 `feimatrix.com`）里列出的 https 主机放行，所以这个跳转无法被引到任意
地址。

可选的生成产物（两者都是增量的 —— 跳过任何一个站点照样能跑，只是多花些
流量）：

```bash
# 480px 网格缩略图：一页 25 张卡片从约 3.6 MB 降到约 0.4 MB
.venv/bin/python build/make_thumbs.py            # 需要 ImageMagick 或 cwebp
# 把 3 条只有远端图片的记录拉到本地（省掉那一次 307 跳转）
.venv/bin/python build/fetch_missing.py
./serve.sh restart
```

回归测试脚本 —— 在备用端口上拉起一份一次性副本，把所有路由都跑一遍
（响应头、过滤、搜索、语义、审核的 upload→approve→reject 全链路、鉴权、
转义、爬虫标签、矩阵完整性、限流）。不需要 CI：任何后端改动之后跑它，
首个失败即以非零码退出并打印 `N passed, 0 failed`：

```bash
.venv/bin/python build/smoke.py [-v] [--keep]
```

## 在 1 GB 小机器上部署

站点跑在一台 1 GB VPS（昵称 chicken）上，规矩如下：

- **先加 swap**：`sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile`，并写进 `/etc/fstab`。
  启动峰值约 520 MB，没有 swap 这台机器会在起进程时被 OOM 杀掉。
- **prefers-reduced-motion / 无障碍基线**：两个页面都有 skip link 和
  noscript 横幅，详情页支持 `←/→` 翻页，环境级的漂移/微光动画在
  `prefers-reduced-motion: reduce` 下关闭。
- **永远只用一个 uvicorn worker**：所有索引都在内存里，上传又会重写同一个
  JSON 文件 —— 多 worker 会各自 fork 一份数据集，然后在写入上互相踩。
- **用 systemd，不用 serve.sh**：`deploy/systemd.service`（RSS 上限 700 MB、
  崩溃自动重启、日志进 journald 而不是越长越大的 serve.log）。serve.sh 留给
  本地开发。
- **种好 `.run/env`**：把 `deploy/env.example` 拷成 `.run/env`，让 `SITE_URL`
  和 Caddy 的主机名一致 —— 否则 `/sitemap.xml` 直接 404、robots.txt 没有
  `Sitemap:` 行、每一条 `og:image` 都是相对地址。
- **前面放 Caddy**：`deploy/Caddyfile` —— TLS 和 HSTS 终结在反代这一层，
  uvicorn 只在 127.0.0.1:8000 上跑明文 HTTP。Caddy 会用真实对端地址
  **覆盖** `X-Forwarded-For`，而这正是应用限流读的字段（search/semantic/
  similar/upload 每 IP 15 次 / 20 秒）；默认的追加行为会让客户端伪造第一跳。
- **备份定时器**：`deploy/backup.sh` 对 JSON 和向量做快照（保留 5 份）——
  一次坏写就等于 10k 条记录全没。用配套单元排期：
  ```bash
  sudo cp deploy/backup.service deploy/backup.timer /etc/systemd/system/
  sudo systemctl enable --now image-story-backup.timer
  systemctl list-timers image-story-backup.timer   # 下次触发时间
  ```
  它是 `Type=oneshot`、`Nice=10`/空闲 I/O，所以永远不会和应用抢这颗唯一的
  核心。

- **缩略图重建很便宜**：`downloaded_thumbs/` 可以随时用
  `build/make_thumbs.py` 重新生成，默认不存在 —— API 找不到就回落原图。
  部署后构建一次，网格就直接发 480px 的副本；详情页、灯箱和幻灯片继续用
  原始文件。
- **向量服务**：Granite-97M llama-server 需要约 300–500 MB —— 在 1 GB 机器
  上只有加了 swap、并把上下文压小（`-c 512 -t 1`）才装得下，而且语义查询会
  很慢。另一条路：让 chicken 只做浏览（语义接口按设计回 503），把
  `EMBED_URL` 从大机器隧道过来（`ssh -R 8023:localhost:8023 bigbox`）。
- 内存预算：应用约 300 MB + Caddy 约 50 MB + 向量服务约 400 MB ≈ 750 MB ——
  配合 swap 紧但活得下去；向量以 `array('f')` 行存储（约 16 MB），而不是
  Python float 列表（约 130 MB），正是为了让应用稳在 300 MB 以内。

## 网页界面

每个页面都在浏览器里由同一个 JSON API 渲染出来。`/story?id=<id>` 还会输出
给爬虫看的元数据（`<title>`、描述、`og:*`、canonical、sitemap 的 `lastmod`），
让链接预览和搜索引擎抓到像样的内容；这些绝对地址的站点原点由 `SITE_URL`
给出（它同时决定 `/sitemap.xml` 是否存在）。

### 站点原点（`SITE_URL`）

一个开关同时打开三样东西：`/sitemap.xml`（没有它这个路由 404）、
`robots.txt` 里的 `Sitemap:` 行，以及每个 `/story?id=` 页面上的绝对
`canonical`/`og:*`/`twitter:*` 地址。`serve.sh` 按这个顺序取值：导出的
shell 环境变量 → `.run/env` → 内置默认 `https://feimatrix.com`，并把解析
结果写回 `.run/env`，这样重启（或换一个新 shell）后原点不会变：

```bash
cp deploy/env.example .run/env   # SITE_URL，外加可选的 EMBED_URL / IMAGE_REMOTE_HOSTS
./serve.sh restart               # start 会打印最终确定的原点
./serve.sh status                # status 里也能看到
```

`deploy/systemd.service` 通过 `EnvironmentFile` 读同一个路径，所以部署机上
也要做同样的拷贝 —— 让它始终指向 Caddy 服务的那个域名，否则 sitemap 和
`og:image` 的预览会指到别的地方去。

无障碍与礼貌细节：两个页面都有键盘专用的 **skip link**、`<noscript>`
横幅（没有 JS 时 JSON API 照样能用）、故事页的 `←/→` 翻页，以及
`prefers-reduced-motion` 下关闭的漂移/微光过渡。标签页隐藏时幻灯片自动暂停。

打开 `http://localhost:8000/` —— 浏览器请求同一个 JSON API，再把数据画进
带 Tailwind 类名的 DOM（黑底金字的暗色杂志主题，CSS Grid 画廊 —— 按视口
宽度排 1/2/3/4/5 列，卡片顶部对齐所以每行齐整，粘性玻璃导航栏，报头 +
封面故事 + 编辑墙）。没有框架、没有 CDN、离线可用。界面文案都在
`app/static/lang.js`（默认英文，`?lang=zh` 覆盖并记住）。

| 路由 | 提供什么 |
|---|---|
| `GET /` | 画廊外壳（`index.html`，`Cache-Control: no-cache`） |
| `GET /story?id=<id>` | 单条记录详情页（`story.html`） |
| `GET /static/{name}` | 按扩展名白名单：只认 `.css` `.js` `.svg`，`immutable` |

界面模式，全部由 `/` 上的查询参数驱动：

| URL | 模式 |
|---|---|
| `/` | 浏览，最新在前 |
| `/?order=old` | 最早在前 |
| `/?mode=random` | `/api/random` 洗牌 |
| `/?mode=tags` | 标签墙，chip 大小按数量缩放 |
| `/?mode=editors` | 编辑墙，chip 大小按数量缩放 |
| `/?editor=Monica` | 某位编辑的作品 |
| `/?tag=urban%20romance` | 某个标签 |
| `/?date=2026-02-07` | 某一天的故事 |
| `/?q=school+uniform` | 关键词搜索，命中处用 `<mark>` 高亮 |
| `/?q=校服` | 中文查询直接走语义搜索（语料是英文，关键词必然落空）；英文关键词 0 命中时同样回落到语义 |
| `/?similar=<id>` | 一条记录的最近邻（故事页的 ✦ Similar） |
| `/?lang=zh` | 中文界面（默认英文，导航栏可切，会记住） |

导航栏还有一个幻灯片按钮：全屏浮层，每 3 秒换一条随机故事（图片 + 全文），
点击任意位置或按 ESC 结束。

卡片点进去就是 `/story?id=…`；详情页有上下篇导航、「✦ Similar」（经
`/api/similar` 取向量近邻）和「🎲 Surprise me」。点卡片上的标签、日期或
编辑署名，就按那个维度过滤。

Tailwind v4.3.3 在本地编译（`@tailwindcss/cli`），所以
`app/static/tailwind.css` 是生成物 —— 只能重新生成，绝不手改：

```bash
node_modules/.bin/tailwindcss -i build/input.css -o app/static/tailwind.css \
  --minify --config tailwind.config.js
.venv/bin/python build/check_classes.py   # 断言 UI 里用到的每个类名都能编译出来
```

`package.json` 拉的是 `@tailwindcss/cli ^4.1.0`；仓库根目录的
`node_modules/`（约 21 MB）只为编译这一个 CSS 文件而存在。删掉它 UI 照样
能用，只是没法重新构建。

`/static/` 下的资源按 `immutable` 缓存 —— 改过 `app.js` 或 `tailwind.css`
之后，必须把 `<link>`/`<script>` 上的 `?v=N` 提一位。

## API

| 路由 | 说明 |
|---|---|
| `GET /api/health` | 总数、标签/编辑数量、日期范围、缺本地图片的 id、向量服务状态 |
| `GET /api/stories?page=1&page_size=20&order=new\|old` | 元数据列表，不含 `story_text` |
| `GET /api/stories/{id}` | 完整记录，含 `story_text`、`editor`、前后篇 id |
| `GET /api/stories/{id}/raw` | JSON 文件里的原始记录，一字不改 |
| `GET /api/images/{id}` | `image/webp` 字节，`Cache-Control: immutable`；`?thumb=1` 优先取 480px 网格副本（没有时回落原图） |
| `GET /api/tags?page=&page_size=` | 全部标签，按数量降序 |
| `GET /api/tags/{tag}?page=` | 空格要 URL 编码（`urban%20romance`），大小写不敏感 |
| `GET /api/dates/{date}?page=` | 某一天（`2026-02-07`），最新在前 |
| `GET /api/editors?page=` | 109 个署名，来自正文里的 `Editor:` 行，按数量排序 |
| `GET /api/editors/{editor}?page=` | 某位编辑的作品（`Monica`），最新在前 |
| `GET /api/search?q=&page=` | 标题+标签+正文的子串 AND 检索，兜底转 OR |
| `GET /api/semantic?q=&page=` | Granite-Embedding-97M 向量上的余弦检索 |
| `GET /api/similar/{id}?n=8` | 一条记录的最近邻（n ≤ 20，不产生服务端调用） |
| `GET /api/random?n=1` | n ≤ 20 |
| `POST /api/upload` | 投稿一条故事：multipart 的 `image`（.webp）+ `title` + `story_text` 必填，`tags`（逗号分隔）和 `editor` 可选。请求头 `X-Upload-Token` 必须等于 `$UPLOAD_TOKEN`。入库即 `status: pending` —— 未审核前对所有公开路由不可见 |
| `GET /api/pending` | 列出待审核的投稿（需 token） |
| `POST /api/approve/{id}` | 发布一条待审核记录：向量 + 建索引 + 翻状态（需 token；向量服务挂了返回 502，记录留在 pending） |
| `POST /api/reject/{id}` | 把待审核记录从 JSON 文件和磁盘上删掉（需 token） |

分页：`page` ≥ 1，`page_size` 1–50，响应形如
`{total, page, page_size, pages, items}`。

```bash
curl localhost:8000/api/health
curl "localhost:8000/api/stories?page=1&page_size=5"
curl -O localhost:8000/api/images/6988b858eb929600014610ee
curl "localhost:8000/api/search?q=school+uniform"
curl "localhost:8000/api/semantic?q=school+uniform"
curl "localhost:8000/api/similar/6988b858eb929600014610ee?n=5"
curl "localhost:8000/api/tags/urban%20romance"
```

上传（只有 API，没有界面；token 由 `./serve.sh start` 打印，并持久化在
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

交互式文档在 `/docs`。

## 目录结构

```
app/__init__.py   包标记
app/dataset.py    一次性加载 + 索引（BY_ID、TAG_INDEX、DATE_INDEX、EDITOR_INDEX、ORDER_NEW/POS、SEARCH、_webp_size）
app/embeddings.py llama-server 客户端 + embeddings.f32 矩阵 + 余弦排序
app/main.py       所有路由
app/static/       index.html · story.html · app.js · lang.js · story.js · tailwind.css（生成物）
build_embeddings.py  为全部记录建向量 -> embeddings.f32 + embeddings.ids.json
build/make_thumbs.py  480px 网格缩略图 -> downloaded_thumbs/（可选）
build/fetch_missing.py  把没有本地图片的记录下载下来
build/smoke.py    端到端回归脚本（一次性服务、备用端口）
embeddings.f32 / .ids.json  预计算的 (N, 384) float32 矩阵 + 行 id（已 gitignore，本地重建）
serve.sh          start/stop/restart/status/logs/health/open/keep
deploy/           systemd.service · Caddyfile · backup.{sh,service,timer} · env.example
.run/             serve.pid · serve.log · env · upload_token（权限 600）—— 由 serve.sh 创建
tailwind.config.js  构建输入：content glob 就是 UI 源码
build/input.css     @import tailwindcss 入口
build/check_classes.py  类名 token → 编译后选择器的断言
package.json        @tailwindcss/cli ^4.1.0
```

## 向量语义搜索

模型跑在这个服务之外：
`granite-embedding-97m-multilingual-r2/llama-server.sh`
（`:8023`，384 维，L2 归一化，余弦 = 点积）。本站只持有预计算好的矩阵；
每次查询先把问题向量化一次（约 50 毫秒），再对 1 万个向量做点积排序
（约 0.15 秒）。没有任何第三方依赖。

```bash
# 1. 启动向量服务（兄弟目录）
../granite-embedding-97m-multilingual-r2/llama-server.sh
# 2. 构建矩阵（约 1 分钟，16.1 MB）
.venv/bin/python build_embeddings.py [--batch 32]
# 3. 重启本站
./serve.sh restart
```

`my_rag_stories.json` 变过之后要重跑第 2 步；行数一旦对不上，
`/api/semantic` 和 `/api/similar` 就回 `503`。环境变量 `EMBED_URL` 可以改
服务地址（默认 `http://localhost:8023`）。`/api/health` 的 `embedding`
一段会报 `indexed/total/ready/live`。

`live: false`（或者 `/api/semantic` 回 502）意味着 llama-server 没在跑 ——
它是独立进程，`serve.sh` 不管它：

```bash
cd ../granite-embedding-97m-multilingual-r2 && ./llama-server.sh   # :8023
```

无论它在不在，浏览、标签、日期、编辑和关键词搜索都正常；只有语义/similar
和 `/api/approve` 需要这个服务。`/api/similar` 能把两种故障分开：未知 id
即使矩阵过期也回 `404`，已知 id 遇上过期矩阵才回 `503` 并点名
`build_embeddings.py`。

槽位预算是 `n_ctx / --parallel` 个 token：按出厂的 `-c 8192 --parallel 8`
算是 1024 token，这正是 `MAX_CHARS = 3000`（约 750 token）的由来。10485 条
里有 51 条更长（最长 5687 字符 / 1074 token），结尾会被截掉；想完整向量化
就调大 `-c` 或调小 `--parallel`。`build_embeddings.py` 先写 `.part` 文件、
成功后才改名，所以一次被杀掉的重建不会毁掉旧矩阵。

`summarize()` 直接读 `.webp` 文件头（纯 Python，不用 Pillow），返回
`image_w` / `image_h`，让 UI 给每个 `<img>` 都带上 `width`/`height` ——
网格排版零回流。

## 已知的数据毛刺（都已处理）

- 3 条记录的 `local_image_path` 存的是远端 URL 而不是路径，所以
  `GET /api/images/{id}` 曾经 `307` 跳到那台主机。它们已经被
  `build/fetch_missing.py` 拉回本地（health 现在报
  `missing_local_images: 0`）；307 兜底为将来的新记录保留。
- 9 条记录没有标签；它们出现在 `/api/stories` 里，但匹配不到任何标签。
- 2 条记录的标题或标签里带引号/尖括号（`"we love you."`、`<Whispers>`），
  所有插值 API 数据的模板都会先做 HTML 转义；API 返回的仍是原始字节。
- 2,201 条记录没有 `Editor:` 署名，匹配不到任何编辑过滤。
- 标题会重复（10,485 条里 9,284 个唯一）—— 一律以 `id` 为准。
- `created_at` 是统一的 ISO-8601 UTC，所以字符串排序就等于按日期排序。
