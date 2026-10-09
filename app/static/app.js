/* Client side of the gallery: fetches /api/* and paints Tailwind-classed DOM.
   Modes: browse | random | tags (index) | tag (filter) | search | semantic | similar */

const S = { mode: "browse", tag: null, day: null, editor: null, q: null, sim: null, order: "new", page: 1, size: 25, pages: 1, total: 0 };
const MAX_PAGES = 6; // how many pages "更多" may stack into one canvas

const grid = document.getElementById("grid");
const head = document.getElementById("head");
const pager = document.getElementById("pager");
const field = document.getElementById("q");

/* ── tiny dom helpers ──────────────────────────────────────────────── */
function el(tag, cls, txt) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (txt != null) n.textContent = txt;
  return n;
}
function add(parent, node) { parent.appendChild(node); return node; }
function num(n) { return n == null ? "—" : n.toLocaleString("en-US"); }
function day(iso) { return iso ? iso.slice(0, 10) : ""; }
function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function highlight(text, terms) {
  const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  if (!terms.length) return esc(text);
  const re = new RegExp("(" + terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")", "gi");
  return esc(text).replace(re, '<mark class="rounded-sm bg-amber-300/70 px-1 text-white">$1</mark>');
}

/* ── the card ──────────────────────────────────────────────────────── */
function card(rec, terms) {
  const art = el("article", "relative mb-5 break-inside-avoid");
  const link = el("a", "group grid items-stretch rounded-2xl ring-1 ring-white/8 bg-white/4 p-2 shadow-2xl transition duration-300 ease-out hover:ring-amber-300/50 hover:-translate-y-1 hover:bg-white/7 hover:shadow-2xl focus:ring-2 focus:ring-amber-300/60");
  link.href = "/story?id=" + encodeURIComponent(rec.id);
  add(art, link);

  const box = el("div", "relative overflow-hidden rounded-xl");
  add(link, box);

  // shimmer sits under the img, so it only shows while the .webp streams in
  const sk = el("div", "sk absolute inset-0");
  add(box, sk);

  const img = el("img", "relative z-10 block w-full max-w-full aspect-[1/2] object-cover object-top transition duration-500 hover:scale-105");
  // grid cards paint a 1:2 crop, so the 480px thumbnail is plenty (and ~6x
  // smaller: a page of 25 drops from ~3.6 MB to ~0.4 MB). Falls back to the
  // original server-side when the thumbnail has not been built yet.
  img.src = rec.image + "?thumb=1";
  img.alt = rec.title || rec.id;
  img.loading = "lazy";
  img.decoding = "async";
  if (rec.image_w && rec.image_h) { img.width = rec.image_w; img.height = rec.image_h; }
  add(box, img);

  const veil = el("div", "absolute inset-x-0 bottom-0 z-20 h-[66%] bg-[linear-gradient(to_bottom,transparent,#0b0b1000,#0b0b10d9)] transition duration-500");
  add(box, veil);

  const cap = el("div", "absolute inset-x-0 bottom-0 z-30 px-3 pb-2 pt-6 flex flex-wrap items-center gap-1");
  add(box, cap);
  cap.innerHTML =
    '<h3 class="line-clamp-2 font-serif italic text-sm text-white/95 tracking-[.02em] drop-shadow-[0_1px_2px_rgba(0,0,0,.8)]">' +
    highlight(rec.title || rec.id, terms) + "</h3>";

  // hover overlay: all tags at full length, grid stays uniform
  const all = el("div", "absolute inset-x-0 bottom-0 z-40 max-h-full overflow-y-auto bg-black/85 px-3 py-2 opacity-0 transition duration-300 group-hover:opacity-100");
  add(box, all);
  const list = el("div", "flex flex-wrap items-center gap-1");
  add(all, list);
  (rec.tags || []).forEach((t) => {
    const s = el("button", "cursor-pointer rounded-full bg-white/10 px-2 py-0.5 text-[10px] text-amber-50/90 transition duration-200 hover:bg-amber-300/25 hover:text-amber-50");
    s.dataset.tag = t;
    s.textContent = t;
    add(list, s);
  });

  const meta = el("div", "mt-2 px-2 py-1.5 rounded-lg bg-white/5 flex flex-nowrap items-center gap-1 text-xs text-slate-400 overflow-hidden");
  add(link, meta);
  (rec.tags || []).slice(0, 1).forEach((t) => {
    const chip = el("button", "min-w-0 truncate cursor-pointer rounded-full bg-white/8 px-2 py-0.5 text-[10px] text-amber-100/70 transition duration-200 hover:bg-amber-300/25 hover:text-amber-50 focus:ring-1 focus:ring-amber-300/60");
    chip.dataset.tag = t;
    chip.textContent = t;
    chip.title = t;
    add(meta, chip);
  });
  if (rec.score != null) add(meta, el("span", "shrink-0 text-[10px] text-amber-300/80", "score " + rec.score));
  if (!rec.local_image_available) add(meta, el("span", "shrink-0 text-[10px] text-amber-400/80", T("remote")));
  const dt = el("button", "shrink-0 ml-auto cursor-pointer border-0 bg-transparent px-1 text-slate-500 transition duration-200 hover:text-amber-200 focus:ring-1 focus:ring-amber-300/60");
  dt.dataset.date = day(rec.created_at);
  dt.textContent = day(rec.created_at);
  dt.title = "查看这一天的全部";
  add(meta, dt);
  return art;
}

/* ── tag / editor chip wall ────────────────────────────────────────── */
function chipWall(items, attr) {
  attr = attr || "tag";
  const key = attr === "editor" ? "editor" : "tag";
  // col-span-full: the grid's other children are 1-column cards, but a chip
  // wall must span the whole row (in the old multicol layout it just filled
  // the flow width, so this was invisible until the grid switch broke it)
  const wrap = el("div", "w-full col-span-full flex flex-wrap items-center gap-3");
  const max = items.length ? items[0].count : 1;
  items.forEach((t) => {
    const name = t[key];
    const b = el("button", "cursor-pointer rounded-full px-3 py-1.5 text-slate-300 transition duration-200 hover:bg-amber-500/25 hover:text-amber-100 focus:ring-1 focus:ring-amber-300/60");
    b.dataset[attr] = name;
    b.textContent = name + " · " + num(t.count);
    b.style.fontSize = 11 + 13 * Math.sqrt(t.count / max) + "px";
    b.style.opacity = (0.45 + 0.55 * Math.sqrt(t.count / max)).toFixed(2);
    add(wrap, b);
  });
  return wrap;
}

/* ── fetch + paint ─────────────────────────────────────────────────── */
function endpoint(page) {
  const ps = Math.min(S.size, S.mode === "random" ? 20 : 50);
  if (S.mode === "search") return "/api/search?q=" + encodeURIComponent(S.q) + "&page=" + page + "&page_size=" + ps;
  if (S.mode === "semantic") return "/api/semantic?q=" + encodeURIComponent(S.q) + "&page=" + page + "&page_size=" + ps;
  if (S.mode === "similar") return "/api/similar/" + encodeURIComponent(S.sim) + "?n=" + Math.min(ps, 20);
  if (S.mode === "tag") return "/api/tags/" + encodeURIComponent(S.tag) + "?page=" + page + "&page_size=" + ps;
  if (S.mode === "date") return "/api/dates/" + S.day + "?page=" + page + "&page_size=" + ps;
  if (S.mode === "editor") return "/api/editors/" + encodeURIComponent(S.editor) + "?page=" + page + "&page_size=" + ps;
  if (S.mode === "tags") return "/api/tags?page=" + page + "&page_size=50";
  if (S.mode === "editors") return "/api/editors?page=" + page + "&page_size=50";
  if (S.mode === "random") return "/api/random?n=" + ps;
  return "/api/stories?page=" + page + "&page_size=" + ps + "&order=" + S.order;
}

async function paint(page, append) {
  let data;
  try {
    const res = await fetch(endpoint(page));
    data = await res.json();
    if (!res.ok) {
      const detail = res.status === 404
        ? "“" + esc(data && data.detail ? data.detail : (S.tag || S.q)) + "” " + T("err_noresult")
        : T("err_api") + " " + res.status;
      head.innerHTML = '<p class="text-amber-200 text-sm">' + detail + "</p>";
      return;
    }
  } catch (err) {
    add(head, el("p", "text-amber-200 text-sm", T("err_fail") + err.message));
    return;
  }

  S.page = page;
  if (S.mode === "search" && (data.total || 0) === 0 && !S._fb && S.q) {
    // keyword miss (e.g. a Chinese term over the English corpus) — one
    // automatic retry as semantic search instead of a dead page.
    S._fb = true;
    S.mode = "semantic";
    paint(page, append);
    return;
  }
  const wasFallback = !!S._fb;
  S._fb = false;
  S.total = data.total != null ? data.total : (data.items || []).length;
  S.pages = Math.min(data.pages || 1, MAX_PAGES);

  const terms = (S.mode === "search" || S.mode === "semantic") && S.q
    ? S.q.split(/\s+/).filter((t) => t.length >= 2) : [];
  const items = data.items || [];

  if (!append) grid.innerHTML = "";
  if (S.mode === "tags") add(grid, chipWall(data.items || []));
  else if (S.mode === "editors") add(grid, chipWall(data.items || [], "editor"));
  else items.forEach((it) => add(grid, card(it, terms)));

  const h = el("div", "flex flex-wrap items-baseline gap-4 justify-start");
  head.innerHTML = "";
  const label =
    S.mode === "search" ? T("m_search") + " “" + S.q + "”" :
    S.mode === "semantic" ? (wasFallback ? T("m_semantic_fb") + " “" + S.q + "”" : T("m_semantic") + " “" + S.q + "”") :
    S.mode === "similar" ? T("m_similar") + " " + (S.sim || "").slice(0, 8) + "…" :
    S.mode === "tag" ? T("m_tag") + " “" + S.tag + "”" :
    S.mode === "date" ? T("m_date") + " " + S.day :
    S.mode === "editor" ? T("m_editor") + " " + S.editor :
    S.mode === "tags" ? T("m_tags") :
    S.mode === "editors" ? T("m_editors") :
    S.mode === "random" ? T("m_random") :
    (S.order === "old" ? T("m_all_old") : T("m_all_new"));
  add(h, el("h1", "font-serif italic text-2xl text-slate-100 tracking-[.02em]", label));
  add(h, el("span", "ml-3 text-xs text-slate-500", T("stats", { t: num(S.total), p: S.page, pp: data.pages || 1, s: data.page_size || S.size })));
  if (S.mode !== "browse") {
    const c = el("button", "cursor-pointer rounded-lg px-3 py-1.5 text-sm text-slate-400 ring-1 ring-white/10 bg-white/5 transition duration-200 hover:text-amber-100 hover:ring-amber-300/50 hover:bg-amber-500/15 focus:ring-2 focus:ring-amber-300/60");
    c.dataset.clear = "1";
    c.textContent = T("clear");
    add(h, c);
  }
  add(head, h);

  pager.innerHTML = "";
  const mk = (txt, to) => {
    const b = el("button", "cursor-pointer rounded-lg px-3 py-1.5 text-sm text-slate-400 ring-1 ring-white/10 bg-white/5 transition duration-200 hover:text-amber-100 hover:ring-amber-300/50 hover:bg-amber-500/15 focus:ring-2 focus:ring-amber-300/60");
    b.dataset.page = to;
    b.textContent = txt;
    add(pager, b);
    return b;
  };
  if (S.page > 1) { mk(T("pager_prev"), S.page - 1); mk(T("pager_first"), 1); }
  if (S.page < (data.pages || 1)) mk(T("pager_next"), S.page + 1);
  if (S.page < S.pages && S.mode !== "tags" && S.mode !== "editors") mk(T("pager_more"), S.page + 1).dataset.more = "1";
  if (S.page > 1) add(pager, mk(T("pager_top"), 1));
  if (S.mode === "random") mk(T("pager_again"), 1);
  cover();
}

/* ── magazine masthead + cover story ─────────────────────────────── */
fetch("/api/health").then((r) => r.json()).then((h) => {
  document.getElementById("mast-no").textContent = mastNo((h.date_max || "").slice(0, 7));
  document.getElementById("mast-sub").textContent = mastSub(h);
}).catch(() => {});

let HERO_ID = null;
async function cover() {
  const box = document.getElementById("hero");
  if (!box) return;
  if (S.mode !== "browse" || S.page !== 1) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  if (HERO_ID) return;
  try {
    const l = await (await fetch("/api/stories?page=1&page_size=1&order=new")).json();
    const id = l.items && l.items[0] && l.items[0].id;
    if (!id) { box.classList.add("hidden"); return; }
    const d = await (await fetch("/api/stories/" + encodeURIComponent(id))).json();
    if (!d.id) { box.classList.add("hidden"); return; }
    HERO_ID = d.id;
    const url = "/story?id=" + encodeURIComponent(d.id);
    document.getElementById("hero-a").href = url;
    document.getElementById("hero-link").href = url;
    const img = document.getElementById("hero-img");
    img.src = d.image;
    img.alt = d.title || d.id;
    document.getElementById("hero-title").textContent = d.title || d.id;
    document.getElementById("hero-meta").textContent =
      (d.editor ? "✎ " + d.editor + " · " : "") +
      (d.created_at || "").slice(0, 10) + " · " + (d.tags || []).length + " tags";
    const first = ((d.story_text || "").split(/\n\s*\n/)[0] || "").replace(/\s+/g, " ").trim();
    document.getElementById("hero-ex").textContent =
      first.length > 160 ? first.slice(0, 160) + "…" : first;
  } catch (e) { box.classList.add("hidden"); }
}

/* pager buttons: append vs replace */
pager.addEventListener("click", (ev) => {
  const t = ev.target;
  if (t.tagName !== "BUTTON") return;
  const to = Number(t.dataset.page);
  const more = t.dataset.more === "1";
  if (Number.isFinite(to)) paint(to, more);
});

/* ✕ 清除 button in the head row: back to browse */
head.addEventListener("click", (ev) => {
  const t = ev.target;
  if (t.tagName !== "BUTTON" || !t.dataset.clear) return;
  S.mode = "browse";
  S.tag = S.day = S.editor = S.q = S.sim = null;
  S._fb = false;
  field.value = "";
  paint(1, false);
});

/* nav buttons switch mode (the lang toggle has no data-mode: skip it) */
document.getElementById("nav").addEventListener("click", (ev) => {
  const t = ev.target;
  if (t.tagName !== "BUTTON" || !t.dataset.mode) return;
  S.mode = t.dataset.mode;
  S.page = 1;
  S._fb = false;
  if (S.mode === "tags") S.tag = null;
  paint(1, false);
});

/* tag / editor / date buttons on cards and walls jump to that filter.
   preventDefault: the card itself is a link to the story page. */
grid.addEventListener("click", (ev) => {
  const t = ev.target;
  if (t.tagName !== "BUTTON") return;
  if (!t.dataset.tag && !t.dataset.date && !t.dataset.editor) return;
  ev.preventDefault();
  S._fb = false;
  if (t.dataset.tag) { S.mode = "tag"; S.tag = t.dataset.tag; }
  else if (t.dataset.date) { S.mode = "date"; S.day = t.dataset.date; }
  else { S.mode = "editor"; S.editor = t.dataset.editor; }
  paint(1, false);
});

/* search form submit runs a search instead of reloading.
   CJK queries skip keyword search: the corpus is English, substring match
   would always miss, while the embedding model is cross-lingual. */
const CJK = /[㐀-䶿぀-ヿ가-힯豈-﫿]/;
document.getElementById("search-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const q = field.value.trim();
  if (!q) return;
  S._fb = false;
  S.mode = (ev.submitter && ev.submitter.dataset.mode === "semantic") || CJK.test(q) ? "semantic" : "search";
  S.q = q;
  field.className = "w-full rounded-lg bg-amber-500/12 ring-1 ring-amber-300/60 px-3 py-1.5 text-sm text-slate-100";
  paint(1, false);
});

/* ── slideshow: a random story every 3 s ───────────────────────────── */
let SLIDE = { timer: null, items: [], i: 0, paused: false };

function slideText(it) {
  const box = document.getElementById("slide-text");
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const blocks = (it._full.story_text || "").replace(/^\s*editor\s*:.*$/gim, "").split(/\n\s*\n/);
  let html = "", first = true;
  blocks.forEach((b) => {
    const t = b.replace(/^\s+|\s+$/g, "");
    if (!t) return;
    const m = t.match(/^(\d+)\.\s*([^\n]{1,90})$/);
    if (m) {
      html += "<h2 class='mt-8 flex items-baseline gap-3 font-serif text-lg tracking-[.02em] text-slate-100'>" +
        "<span class='text-sm text-amber-200'>" + m[1] + "</span>" + esc(m[2]) + "</h2>";
    } else if (first) {
      html += "<p class='first-line font-serif italic text-lg leading-relaxed text-slate-100'>" + esc(t).replace(/\n/g, "<br>") + "</p>";
      first = false;
    } else {
      html += "<p class='mt-5 text-[15px] leading-relaxed text-slate-300'>" + esc(t).replace(/\n/g, "<br>") + "</p>";
    }
  });
  box.innerHTML = html;
}

function slideShow() {
  const it = SLIDE.items[SLIDE.i];
  if (!it) return;
  const img = document.getElementById("slide-img");
  img.src = it.image;
  img.alt = it.title || it.id;
  const a = document.getElementById("slide-title");
  a.textContent = (it.title || it.id) + " · " + (SLIDE.i + 1) + "/" + SLIDE.items.length;
  a.href = "/story?id=" + encodeURIComponent(it.id);
  const meta = document.getElementById("slide-meta");
  const full = it._full;
  meta.textContent = ((full && full.editor) ? "✎ " + full.editor + " · " : "") + (it.created_at || "").slice(0, 10);
  if (full) { slideText(it); }
  else {
    document.getElementById("slide-text").innerHTML = "";
    fetch("/api/stories/" + encodeURIComponent(it.id)).then((r) => r.json()).then((d) => {
      it._full = d;
      if (SLIDE.timer && SLIDE.items[SLIDE.i] === it) { slideText(it); meta.textContent = (d.editor ? "✎ " + d.editor + " · " : "") + (it.created_at || "").slice(0, 10); }
    }).catch(() => {});
  }
  const nx = SLIDE.items[SLIDE.i + 1];
  if (nx) {
    const pre = new Image(); pre.src = nx.image;
    if (!nx._full) fetch("/api/stories/" + encodeURIComponent(nx.id)).then((r) => r.json()).then((d) => { nx._full = d; }).catch(() => {});
  }
}

async function slideRefill() {
  const d = await (await fetch("/api/random?n=10")).json();
  SLIDE.items = d.items || [];
}

async function slideAdvance() {
  SLIDE.i++;
  if (SLIDE.i >= SLIDE.items.length) { await slideRefill(); SLIDE.i = 0; }
  slideShow();
}

function startSlide() {
  if (SLIDE.timer) return;
  const box = document.getElementById("slide");
  box.classList.remove("hidden");
  box.classList.add("flex");
  document.body.style.overflow = "hidden";
  SLIDE.i = 0;
  SLIDE.items = [];
  slideRefill().then(slideShow).catch(() => stopSlide());
  SLIDE.timer = setInterval(slideAdvance, 3000);
  box.onclick = function (ev) {
    if (ev.target.tagName === "A") return; // title link goes to the story
    stopSlide();
  };
}

/* A hidden tab must not keep the 3 s cadence: it burns CPU in the background
   and preloads images nobody is looking at. Pause + resume on visibility. */
document.addEventListener("visibilitychange", function () {
  if (!SLIDE.timer && !SLIDE.paused) return;
  if (document.hidden) {
    if (SLIDE.timer) { clearInterval(SLIDE.timer); SLIDE.timer = null; SLIDE.paused = true; }
  } else if (SLIDE.paused) {
    SLIDE.paused = false;
    SLIDE.timer = setInterval(slideAdvance, 3000);
  }
});

function stopSlide() {
  if (SLIDE.timer) clearInterval(SLIDE.timer);
  SLIDE.timer = null;
  const box = document.getElementById("slide");
  box.classList.add("hidden");
  box.classList.remove("flex");
  document.body.style.overflow = "";
}

document.addEventListener("keydown", function (ev) {
  if (ev.key === "Escape" && SLIDE.timer) stopSlide();
});

/* boot from the URL */
(function () {
  applyStatic();
  document.getElementById("slide-btn").onclick = startSlide;
  const p = new URLSearchParams(location.search);
  if (p.get("q")) { S.mode = p.get("semantic") != null ? "semantic" : "search"; S.q = p.get("q"); }
  else if (p.get("tag")) { S.mode = "tag"; S.tag = p.get("tag"); }
  else if (p.get("date")) { S.mode = "date"; S.day = p.get("date"); }
  else if (p.get("editor")) { S.mode = "editor"; S.editor = p.get("editor"); }
  else if (p.get("similar")) { S.mode = "similar"; S.sim = p.get("similar"); }
  else if (p.get("mode")) S.mode = p.get("mode");
  if (p.get("order")) S.order = p.get("order");
  if (p.get("page")) S.page = Math.max(1, Number(p.get("page")));
  paint(S.page, false);
})();
