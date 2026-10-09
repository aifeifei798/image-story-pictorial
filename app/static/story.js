/* Detail page: pulls one record from /api/stories/{id} and lays it out. */
document.addEventListener("DOMContentLoaded", function () {
  applyStatic();
  /* ← 返回: wired here, not with an inline onclick attribute — the CSP is
     script-src 'self' (no 'unsafe-inline'), so an HTML onclick is blocked. */
  document.getElementById("back-btn").onclick = function () { history.back(); };
  const stage = document.getElementById("stage");
  const id = new URLSearchParams(location.search).get("id");
  if (!id) { stage.innerHTML = "<p class='text-amber-200 text-sm'>" + T("err_noid") + "</p>"; return; }
  fetch("/api/stories/" + encodeURIComponent(id)).then(function (r) { return r.json() }).then(function (d) {
    if (!d.id) { stage.innerHTML = "<p class='text-amber-200 text-sm'>" + T("err_unknown") + id + "</p>"; return; }
    document.title = (d.title || d.id) + " · my_rag_stories";

    /* titles/editors/tags are user-supplied data — never interpolate raw */
    var esc = function (s) {
      return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
        .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    };

    const head = document.getElementById("head");
    head.innerHTML =
      '<h1 class="font-serif italic text-3xl text-white/95 leading-tight tracking-[.01em]">' + esc(d.title || "") + "</h1>" +
      '<div class="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-400">' +
        '<a class="text-slate-500 transition duration-200 hover:text-amber-200" title="' + esc(T("see_day")) + '" href="/?date=' + (d.created_at || "").slice(0, 10) + '">' + (d.created_at || "").slice(0, 10) + "</a>" +
        (d.editor ? '<a class="rounded-full px-2 py-0.5 text-[11px] text-amber-100/80 bg-white/7 transition duration-200 hover:bg-amber-500/25 hover:text-amber-100" href="/?editor=' + encodeURIComponent(d.editor) + '">✎ ' + esc(d.editor) + "</a>" : "") +
        '<span class="px-2 py-0.5 rounded-full bg-white/8 text-amber-100/80">' + (d.tags || []).length + " tags</span>" +
      "</div>";

    const img = document.getElementById("frame");
    img.innerHTML =
      '<img class="block h-auto w-full max-w-full rounded-xl ring-1 ring-white/15 shadow-2xl" src="' + d.image + '" alt="' + esc(d.title || "") + '"' +
      (d.image_w ? ' width="' + d.image_w + '" height="' + d.image_h + '"' : "") + '">' +
      '<div class="absolute inset-0 rounded-xl bg-[radial-gradient(circle_at_50%_120%,#d4af3726,transparent)] mix-blend-screen pointer-events-none"></div>';

    /* click-to-zoom lightbox */
    const zoom = document.getElementById("zoom");
    const zoomImg = document.getElementById("zoom-img");
    const closeZoom = function () {
      zoom.classList.add("hidden"); zoom.classList.remove("flex");
      document.body.style.overflow = "";
    };
    img.onclick = function () {
      zoomImg.src = d.image;
      zoomImg.alt = d.title || d.id;
      zoom.classList.remove("hidden"); zoom.classList.add("flex");
      document.body.style.overflow = "hidden";
    };
    zoom.onclick = closeZoom;
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") closeZoom();
    });

    const body = document.getElementById("body");
    var text = (d.story_text || "").replace(/^\s*editor\s*:.*$/gim, ""); // byline shown in head
    var html = "", ledeDone = false;
    ((text.split(/\n\s*\n/))).forEach(function (b) {
      var t = b.replace(/^\s+|\s+$/g, "");
      if (!t) return;
      var m = t.match(/^(\d+)\.\s*([^\n]{1,90})$/);
      if (m) {
        html += "<h2 class='mt-8 flex items-baseline gap-3 font-serif text-lg tracking-[.02em] text-slate-100'>" +
          "<span class='text-sm text-amber-200'>" + m[1] + "</span>" + esc(m[2]) + "</h2>";
      } else if (!ledeDone) {
        html += "<p class='first-line font-serif italic text-lg leading-relaxed text-slate-100'>" + esc(t).replace(/\n/g, "<br>") + "</p>" +
          "<div class='my-6 h-px bg-white/10'></div>";
        ledeDone = true;
      } else {
        html += "<p class='mt-5 text-[15px] leading-relaxed text-slate-300'>" + esc(t).replace(/\n/g, "<br>") + "</p>";
      }
    });
    body.innerHTML = html;

    const chips = document.getElementById("chips");
    (d.tags || []).forEach(function (t) {
      const b = document.createElement("button");
      b.className = "cursor-pointer rounded-full px-2 py-0.5 text-[11px] text-slate-400 bg-white/7 transition duration-200 hover:bg-amber-500/25 hover:text-amber-100 focus:ring-1 focus:ring-amber-300/60";
      b.dataset.tag = t;
      b.textContent = t;
      b.title = T("see_tag") + t;
      b.onclick = function () { location.href = "/?tag=" + encodeURIComponent(t); };
      chips.appendChild(b);
    });

    const acts = document.getElementById("acts");
    const mk = function (cls, txt, href) {
      const a = document.createElement("a");
      a.className = cls; a.href = href; a.textContent = txt;
      acts.appendChild(a);
    };
    if (d.prev)
      mk("rounded-lg px-3 py-1.5 text-xs text-slate-300 ring-1 ring-white/12 transition duration-200 hover:ring-amber-300/60 hover:text-amber-100", T("prev"),
         "/story?id=" + encodeURIComponent(d.prev));
    if (d.next)
      mk("rounded-lg px-3 py-1.5 text-xs text-slate-300 ring-1 ring-white/12 transition duration-200 hover:ring-amber-300/60 hover:text-amber-100", T("next"),
         "/story?id=" + encodeURIComponent(d.next));
    mk("rounded-lg px-3 py-1.5 text-xs text-slate-300 ring-1 ring-white/12 transition duration-200 hover:ring-amber-300/60 hover:text-amber-100", T("sim"),
       "/?similar=" + encodeURIComponent(d.id));
    const rb = document.createElement("button");
    rb.className = "cursor-pointer rounded-lg px-3 py-1.5 text-xs text-slate-300 ring-1 ring-white/12 transition duration-200 hover:ring-amber-300/60 hover:text-amber-100";
    rb.textContent = T("rand");
    rb.onclick = function () {
      fetch("/api/random?n=1").then(function (r) { return r.json(); }).then(function (rd) {
        const it = rd.items && rd.items[0];
        if (it) location.href = "/story?id=" + encodeURIComponent(it.id);
      });
    };
    acts.appendChild(rb);
    /* keyboard paging: ← prev, → next (the buttons stay for pointer users) */
    const goto = function (id) { if (id) location.href = "/story?id=" + encodeURIComponent(id); };
    document.addEventListener("keydown", function (ev) {
      if (ev.target && /^(INPUT|TEXTAREA|SELECT)$/.test(ev.target.tagName)) return;
      if (ev.key === "ArrowLeft") goto(d.prev);
      else if (ev.key === "ArrowRight") goto(d.next);
      else return;
      ev.preventDefault();
    });
  }).catch(function (e) {
    stage.innerHTML = "<p class='text-amber-200 text-sm'>" + T("err_load") + e.message + "</p>";
  });
});
