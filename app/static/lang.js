/* Shared i18n: I18N dict + T() + static-text swap + lang toggle.
   Default 'en'. ?lang= overrides and persists to localStorage. */

const LANG = (function () {
  const p = new URLSearchParams(location.search).get("lang");
  if (p === "zh" || p === "en") {
    try { localStorage.setItem("lang", p); } catch (e) {}
    return p;
  }
  try { return localStorage.getItem("lang") || "en"; } catch (e) { return "en"; }
})();

const I18N = {
  en: {
    doc_title: "my_rag_stories · Image Story Pictorial",
    logo_sub: "Image · Story Archive",
    nav_browse: "Browse", nav_random: "Random", nav_tags: "Tags", nav_editors: "Editors", nav_search: "Keyword search",
    back: "← Back",
    search_ph: "keywords… (Chinese goes semantic)",
    mast_title: "Image Story Pictorial",
    foot_data_html: 'Source <code class="px-2 py-0.5 rounded bg-white/6 text-slate-300">my_rag_stories.json</code> and ' +
      '<code class="px-2 py-0.5 rounded bg-white/6 text-slate-300">downloaded_images/</code> · ' +
      "raw .webp served · semantic search by Granite-Embedding-97M (llama-server :8023)",
    foot_story_1_html: 'Record view · <code class="px-2 py-0.5 rounded bg-white/6 text-slate-300">GET /api/stories/{id}</code> returns story_text and image',
    foot_story_2: "Raw webp served directly · similarity powered by embeddings",
    m_search: "Keyword", m_semantic: "Semantic",
    m_semantic_fb: "no keyword hit · switched to semantic",
    m_similar: "Similar to", m_tag: "Tag", m_date: "Date", m_editor: "Editor",
    m_tags: "Tag index", m_editors: "Editors", m_random: "Random picks",
    m_all_new: "All · newest first", m_all_old: "All · oldest first",
    stats: "{t} items · p.{p}/{pp} · {s}/page",
    pager_prev: "‹ Prev", pager_first: "« 1st", pager_next: "Next ›",
    pager_more: "⭳ More", pager_top: "↑ Top", pager_again: "↻ Again", nav_slideshow: "Slideshow",
    err_noresult: "no results — try tags or keywords", err_api: "API",
    err_fail: "Request failed: ", clear: "✕ Clear", remote: "remote",
    cover_kicker: "COVER STORY", read_more: "Read story →",
    err_noid: "Missing ?id=", err_unknown: "Unknown id: ", err_load: "Failed: ",
    see_day: "View this day", see_tag: "View tag ",
    sim: "✦ Similar", rand: "🎲 Surprise me", prev: "← Prev", next: "Next →",
    slide_hint: "click anywhere to stop · ESC",
    skip: "Skip to the gallery"
  },
  zh: {
    doc_title: "my_rag_stories · 影像故事档案",
    logo_sub: "影像 · 故事档案",
    nav_browse: "浏览", nav_random: "随机", nav_tags: "标签", nav_editors: "小编", nav_search: "关键词搜索",
    back: "← 返回",
    search_ph: "关键词…（中文直接语义搜）",
    mast_title: "影像故事画报",
    foot_data_html: '数据源 <code class="px-2 py-0.5 rounded bg-white/6 text-slate-300">my_rag_stories.json</code> 与 ' +
      '<code class="px-2 py-0.5 rounded bg-white/6 text-slate-300">downloaded_images/</code> · ' +
      "原 .webp 直接 serve · 语义搜索由 Granite-Embedding-97M（llama-server :8023）驱动",
    foot_story_1_html: '单记录视图 · <code class="px-2 py-0.5 rounded bg-white/6 text-slate-300">GET /api/stories/{id}</code> 返回 story_text 与 image',
    foot_story_2: "原 webp 直接 serve · 语义相似由 embedding 驱动",
    m_search: "关键词", m_semantic: "语义",
    m_semantic_fb: "关键词无命中 · 已切换语义",
    m_similar: "相似于", m_tag: "标签", m_date: "日期", m_editor: "小编",
    m_tags: "标签索引", m_editors: "小编", m_random: "随机抽样",
    m_all_new: "全部 · newest first", m_all_old: "全部 · oldest first",
    stats: "{t} 项 · 第 {p}/{pp} 页 · {s} / 页",
    pager_prev: "‹ 前", pager_first: "« 第 1", pager_next: "后 ›",
    pager_more: "⭳ 更多", pager_top: "↑ 顶部", pager_again: "↻ 再随机", nav_slideshow: "幻灯",
    err_noresult: "无结果 — 试试标签或关键词", err_api: "接口",
    err_fail: "请求失败: ", clear: "✕ 清除", remote: "远程",
    cover_kicker: "本期封面 · COVER", read_more: "阅读全文 →",
    err_noid: "缺少 ?id= 参数", err_unknown: "未知 id: ", err_load: "加载失败: ",
    see_day: "查看这一天的全部", see_tag: "查看标签 ",
    sim: "✦ 语义相似", rand: "🎲 随机一张", prev: "← 上一张", next: "下一张 →",
    slide_hint: "点击任意处停止 · ESC",
    skip: "跳到内容"
  }
};

function T(key, params) {
  let s = (I18N[LANG] && I18N[LANG][key]) || I18N.en[key] || key;
  if (params) Object.keys(params).forEach((k => { s = s.replace("{" + k + "}", params[k]); }));
  return s;
}

function setLang(l) {
  try { localStorage.setItem("lang", l); } catch (e) {}
  const u = new URL(location.href);
  u.searchParams.set("lang", l);
  location.href = u.toString();
}

function applyStatic() {
  document.documentElement.lang = LANG === "zh" ? "zh-CN" : "en";
  document.querySelectorAll("[data-i18n]").forEach((n => { n.textContent = T(n.getAttribute("data-i18n")); }));
  document.querySelectorAll("[data-i18n-html]").forEach((n => { n.innerHTML = T(n.getAttribute("data-i18n-html")); }));
  document.querySelectorAll("[data-i18n-ph]").forEach((n => { n.setAttribute("placeholder", T(n.getAttribute("data-i18n-ph"))); }));
  document.querySelectorAll("[data-lang-toggle]").forEach((n => {
    n.textContent = LANG === "zh" ? "EN" : "中文";
    n.title = LANG === "zh" ? "Switch to English" : "切换到中文";
    n.onclick = function () { setLang(LANG === "zh" ? "en" : "zh"); };
  }));
}

function mastNo(ym) { return LANG === "zh" ? "画 报 · 第 " + ym + " 期" : "PICTORIAL · VOL " + ym; }
function mastSub(h) {
  const r = (iso) => (iso || "").slice(0, 10);
  return LANG === "zh"
    ? num(h.total) + " 篇 · " + num(h.editors || 0) + " 位小编 · " + r(h.date_min) + " — " + r(h.date_max)
    : num(h.total) + " stories · " + num(h.editors || 0) + " editors · " + r(h.date_min) + " — " + r(h.date_max);
}
