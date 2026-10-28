"use strict";

/* ---------- утилиты ---------- */
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtInt = (v) => (v == null ? "—" : Math.round(v).toLocaleString("ru-RU"));
const fmtPct = (v, d = 1) => (v == null ? "—" : (v * 100).toFixed(d).replace(".", ",") + "%");
const fmtX = (v) => (v == null ? "—" : v.toFixed(2).replace(".", ",") + "×");
const signPct = (v) => (v == null ? "—" : (v > 0 ? "+" : "") + fmtPct(v));

let CONFIG = { llm: { enabled: false }, vk_token_configured: false };
let FACTS = {};
let currentJob = null;

/* ---------- форма ---------- */
const PLACEHOLDERS = { telegram: "@channel или t.me/channel", vk: "vk.com/club123 или короткое имя" };

function makeSource(container, removable) {
  const node = $("#source-tpl").content.firstElementChild.cloneNode(true);
  const platform = $(".platform", node), ident = $(".ident", node), csv = $(".csv-wrap", node);
  const sync = () => {
    const p = platform.value;
    ident.hidden = p === "csv";
    csv.hidden = p !== "csv";
    if (p !== "csv") ident.placeholder = PLACEHOLDERS[p];
    updateVkToken();
  };
  platform.addEventListener("change", sync);
  $(".csv-file", node).addEventListener("change", (e) => uploadCsv(node, e.target.files[0]));
  if (removable) {
    const rm = $(".remove", node);
    rm.hidden = false;
    rm.addEventListener("click", () => { node.remove(); updateVkToken(); toggleAdd(); });
  }
  container.appendChild(node);
  sync();
  return node;
}

async function uploadCsv(row, file) {
  if (!file) return;
  const status = $(".csv-status", row);
  status.textContent = "Загружаю…";
  const fd = new FormData();
  fd.append("file", file);
  fd.append("name", $(".csv-name", row).value || file.name.replace(/\.csv$/i, ""));
  if ($(".csv-subs", row).value) fd.append("subscribers", $(".csv-subs", row).value);
  try {
    const r = await fetch("/api/upload", { method: "POST", body: fd });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || "не удалось загрузить файл");
    row.dataset.uploadId = data.upload_id;
    status.textContent = `${data.posts} постов загружено`;
  } catch (e) {
    delete row.dataset.uploadId;
    status.textContent = e.message;
  }
}

function readSource(row) {
  const platform = $(".platform", row).value;
  if (platform === "csv") return { platform, upload_id: row.dataset.uploadId || "" };
  return { platform, id: $(".ident", row).value.trim() };
}

function updateVkToken() {
  const anyVk = [...document.querySelectorAll(".platform")].some((s) => s.value === "vk");
  $("#vk-token-wrap").hidden = !(anyVk && !CONFIG.vk_token_configured);
}

function toggleAdd() {
  $("#add-comp").hidden = document.querySelectorAll("#competitors .src-row").length >= 5;
}

function showFormError(msg) {
  const el = $("#form-error");
  el.textContent = msg || "";
  el.hidden = !msg;
}

/* ---------- задачи ---------- */
async function startJob(payload) {
  showFormError("");
  $("#run").disabled = true;
  $("#demo").disabled = true;
  $("#result").hidden = true;
  $("#steps").innerHTML = "";
  $("#progress").hidden = false;
  try {
    const r = await fetch("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || "Не удалось запустить анализ");
    currentJob = data.job_id;
    poll(data.job_id);
  } catch (e) {
    finish();
    $("#progress").hidden = true;
    showFormError(e.message);
  }
}

async function poll(id) {
  const r = await fetch(`/api/jobs/${id}`);
  const job = await r.json();
  $("#steps").innerHTML = job.steps.map((s) => `<li><span class="t">${s.t.toFixed(1)} с</span>${esc(s.msg)}</li>`).join("");
  if (job.status === "done") {
    finish();
    $("#progress").hidden = true;
    render(job.result, id);
  } else if (job.status === "error") {
    finish();
    $("#progress").hidden = true;
    showFormError(job.error);
  } else {
    setTimeout(() => poll(id), 700);
  }
}

function finish() {
  $("#run").disabled = false;
  $("#demo").disabled = false;
}

/* ---------- графика ---------- */
function wrap(text, max) {
  const words = String(text).split(/\s+/);
  const lines = [];
  let cur = "";
  for (const w of words) {
    if ((cur + " " + w).trim().length > max && cur) { lines.push(cur); cur = w; } else cur = (cur + " " + w).trim();
  }
  if (cur) lines.push(cur);
  return lines.slice(0, 3);
}

function compassSvg(directions, author, compact) {
  const W = 560, H = 400, ox = 96, oy = 318, L = 236;
  const angles = [64, 36, 8];
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Три направления развития канала" class="draw">`;
  for (const r of [90, 180, 270]) s += `<circle class="ring" cx="${ox}" cy="${oy}" r="${r}"/>`;
  s += `<line x1="${ox}" y1="${oy}" x2="${W - 10}" y2="${oy}" stroke="#C9D3CC"/><line x1="${ox}" y1="${oy}" x2="${ox}" y2="10" stroke="#C9D3CC"/>`;
  directions.slice(0, 3).forEach((d, i) => {
    const a = (angles[i] * Math.PI) / 180;
    const ex = ox + L * Math.cos(a), ey = oy - L * Math.sin(a);
    const hx = ox + (L - 14) * Math.cos(a), hy = oy - (L - 14) * Math.sin(a);
    const delay = 0.15 + i * 0.25;
    s += `<path class="arrow-path" style="--len:${L};animation-delay:${delay}s" d="M${ox},${oy} L${hx.toFixed(1)},${hy.toFixed(1)}"/>`;
    const deg = -angles[i];
    s += `<polygon class="arrow-head" style="animation-delay:${delay + 0.9}s" points="0,-7 16,0 0,7" transform="translate(${hx.toFixed(1)},${hy.toFixed(1)}) rotate(${deg})"/>`;
    const lx = ex + 16, ly = ey - (i === 0 ? 4 : 0);
    if (compact) {
      s += `<g class="dir-tag" style="animation-delay:${delay + 0.9}s"><circle cx="${lx + 20}" cy="${ly - 6}" r="24" fill="#A3195B"/><text class="dir-num" style="font-size:26px" x="${lx + 20}" y="${ly + 3}" text-anchor="middle">${i + 1}</text></g>`;
    } else {
      s += `<g class="dir-tag" style="animation-delay:${delay + 0.9}s"><circle cx="${lx + 11}" cy="${ly - 4}" r="11" fill="#A3195B"/><text class="dir-num" x="${lx + 11}" y="${ly + 1}" text-anchor="middle">${i + 1}</text>`;
      wrap(d.title, 24).forEach((line, j) => {
        s += `<text class="dir-label" x="${lx + 28}" y="${ly + j * 17}">${esc(line)}</text>`;
      });
      s += `</g>`;
    }
  });
  s += `<circle cx="${ox}" cy="${oy}" r="9" fill="#16233A"/><circle cx="${ox}" cy="${oy}" r="16" fill="none" stroke="#16233A" stroke-width="1.5"/>`;
  const fs = compact ? ' style="font-size:24px"' : "";
  s += `<text class="axis"${fs} x="${ox + (compact ? 30 : -8)}" y="${oy + (compact ? 50 : 36)}" text-anchor="middle">канал сейчас</text>`;
  if (!compact) s += `<text class="axis" x="${ox - 8}" y="${oy + 52}" text-anchor="middle">${esc(fmtInt(author.median_views))} просмотров на пост</text>`;
  return s + "</svg>";
}

function lineChart(points) {
  if (!points || points.length < 2) return `<p class="cap">Недостаточно данных для графика.</p>`;
  const W = 600, H = 210, pl = 52, pr = 12, pt = 12, pb = 28;
  const vals = points.map((p) => p.median_views);
  const max = Math.max(...vals) * 1.1, min = 0;
  const x = (i) => pl + (i / (points.length - 1)) * (W - pl - pr);
  const y = (v) => pt + (1 - (v - min) / (max - min)) * (H - pt - pb);
  const d = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.median_views).toFixed(1)}`).join(" ");
  const area = `${d} L${x(points.length - 1).toFixed(1)},${y(0)} L${x(0)},${y(0)} Z`;
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Медианный охват по неделям">`;
  for (const t of [0, 0.5, 1]) {
    const v = max * t;
    s += `<line x1="${pl}" x2="${W - pr}" y1="${y(v)}" y2="${y(v)}" stroke="#D8E0DA"/><text class="axis" x="${pl - 8}" y="${y(v) + 4}" text-anchor="end">${fmtInt(v)}</text>`;
  }
  s += `<path class="area" d="${area}"/><path class="line" d="${d}"/>`;
  const fmtD = (s) => new Date(s).toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
  s += `<text class="axis" x="${x(0)}" y="${H - 6}">${fmtD(points[0].week)}</text>`;
  s += `<text class="axis" x="${x(points.length - 1)}" y="${H - 6}" text-anchor="end">${fmtD(points[points.length - 1].week)}</text>`;
  const last = points.length - 1;
  s += `<circle class="dot" cx="${x(last)}" cy="${y(points[last].median_views)}" r="4"/>`;
  return s + "</svg>";
}

function divergingBars(buckets) {
  const rows = (buckets || []).filter((b) => b.n > 0);
  if (!rows.length) return `<p class="cap">Недостаточно данных.</p>`;
  const dev = Math.max(0.5, ...rows.map((b) => Math.abs(b.median_virality - 1)));
  let s = "";
  for (const b of rows) {
    const v = b.median_virality, w = Math.min(50, (Math.abs(v - 1) / dev) * 50);
    const left = v >= 1 ? 50 : 50 - w;
    s += `<div class="div-row${b.reliable ? "" : " thin"}"${b.reliable ? "" : ' title="Мало постов — вывод ненадёжен"'}>
      <span class="lbl">${esc(b.key)}</span>
      <span class="div-track"><span class="div-bar ${v >= 1 ? "up" : "down"}" style="left:${left}%;width:${Math.max(w, 0.6)}%"></span></span>
      <span class="val">${fmtX(v)} <small>n=${b.n}</small></span></div>`;
  }
  s += `<div class="div-scale"><span></span><span><span>${fmtX(1 - dev)}</span><span>1×</span><span>${fmtX(1 + dev)}</span></span><span></span></div>`;
  return s;
}

/* ---------- факты ---------- */
const chips = (ids) => (ids || []).map((id) => `<button type="button" class="fact" data-f="${esc(id)}" aria-expanded="false">${esc(id)}</button>`).join("");

function showTip(btn) {
  const tip = $("#tip");
  const f = FACTS[btn.dataset.f];
  if (!f) return;
  document.querySelectorAll(".fact[aria-expanded=true]").forEach((b) => b.setAttribute("aria-expanded", "false"));
  btn.setAttribute("aria-expanded", "true");
  tip.innerHTML = `${esc(f.text)}<span class="c">${esc(f.id)} · ${esc(f.category)} · уверенность: ${esc(f.confidence)}</span>`;
  tip.hidden = false;
  const r = btn.getBoundingClientRect();
  const left = Math.min(window.scrollX + r.left, window.scrollX + document.documentElement.clientWidth - tip.offsetWidth - 12);
  tip.style.left = `${Math.max(8, left)}px`;
  tip.style.top = `${window.scrollY + r.bottom + 8}px`;
}

function hideTip() {
  $("#tip").hidden = true;
  document.querySelectorAll(".fact[aria-expanded=true]").forEach((b) => b.setAttribute("aria-expanded", "false"));
}

document.addEventListener("click", (e) => {
  const btn = e.target.closest(".fact");
  if (btn) { btn.getAttribute("aria-expanded") === "true" ? hideTip() : showTip(btn); return; }
  if (!e.target.closest("#tip")) hideTip();
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideTip(); });

/* ---------- отрисовка результата ---------- */
function render(res, jobId, reportHref, noScroll) {
  const an = res.analysis, st = res.strategy, a = an.author;
  FACTS = Object.fromEntries(an.facts.map((f) => [f.id, f]));
  const ch = a.channel;
  const accepted = new Set(st.accepted.map((r) => r.rec_id));
  const rejectedMap = Object.fromEntries(st.rejected.filter((r) => r.rec_id).map((r) => [r.rec_id, r]));
  const totalRecs = st.agents.reduce((n, ag) => n + ag.recommendations.length, 0);
  const pre = st.rejected.filter((r) => r.rejected_by === "проверка ссылок").length;
  const byCritic = st.rejected.filter((r) => r.rejected_by === "агент-критик").length;
  const modeTxt = st.mode === "llm" ? `агенты на модели ${st.model}`
    : st.mode === "mixed" ? `смешанный режим: модель ${st.model} отработала на ${st.llm_steps_ok} из 4 шагов`
    : st.model ? `модель ${st.model} не ответила — агенты на правилах` : "офлайн-режим: агенты на правилах, без LLM";

  let h = "";
  if (res.demo) h += `<div class="banner">Это синтетические демо-данные: каналы вымышлены, а закономерности заложены специально, чтобы показать, как Вектор их находит.</div>`;
  for (const w of res.warnings || []) h += `<div class="banner">${esc(w)}</div>`;
  for (const e of st.llm_errors || []) h += `<div class="banner">${esc(e.step)}: модель не сработала, использованы правила. Причина: ${esc(e.error)}</div>`;

  h += `<div class="result-head"><div><h2>Стратегия для «${esc(ch.title)}»</h2>
        <p class="sub">${esc(a.n_posts)} постов за ${esc(a.rhythm.period_days)} дн. · ${an.competitors.length} конкурента · ${esc(modeTxt)}</p></div>
        <a class="ghost" href="${reportHref || `/api/jobs/${jobId}/report.md`}" download="vektor-report.md">Скачать отчёт</a></div>`;

  // главный экран: роза направлений
  h += `<section class="panel compass"><div class="compass-fig">${compassSvg(st.directions, a, window.innerWidth < 640)}</div>
    <div class="compass-text"><p class="summary">${esc(st.summary)}</p>
      <dl class="kpis">
        <div><dt>Медианный охват поста</dt><dd>${fmtInt(a.median_views)}</dd></div>
        <div><dt>ERR — охват к подписчикам</dt><dd>${fmtPct(a.err)}</dd></div>
        <div><dt>Динамика охвата в месяц</dt><dd>${signPct(a.trend.monthly_change)}${a.trend.significant === false ? "<small>незначимо</small>" : ""}</dd></div>
        <div><dt>Постов в неделю</dt><dd>${esc(String(a.rhythm.posts_per_week ?? "—").replace(".", ","))}</dd></div>
      </dl></div></section>`;

  // направления
  h += `<section class="section"><div class="sec-head"><h2>Три направления</h2><p>Нажмите на номер факта, чтобы увидеть цифры, на которых основан вывод.</p></div><div class="directions">`;
  st.directions.forEach((d, i) => {
    h += `<article class="panel direction"><div class="num">${i + 1}</div><h3>${esc(d.title)}</h3><p>${esc(d.why)}</p>
      <ul>${(d.actions || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
      ${d.kpi ? `<div class="kpi">Как измерить: ${esc(d.kpi)}</div>` : ""}<div>${chips(d.facts)}</div></article>`;
  });
  h += `</div></section>`;

  // конвейер агентов
  h += `<section class="section"><div class="sec-head"><h2>Как работали агенты</h2>
    <p>Три агента независимо предлагают идеи. Затем программа отбрасывает идеи без ссылок на реальные факты, а агент-критик проверяет остальные.</p></div><div class="pipeline">`;
  for (const ag of st.agents) {
    h += `<div class="panel agent"><h3>${esc(ag.name)} <span class="temp">t = ${String(ag.temperature).replace(".", ",")}</span></h3>
      <p class="meta">видел фактов: ${ag.facts_seen}${ag.fallback ? " · LLM недоступна, сработали правила" : ""}</p><ul>`;
    for (const r of ag.recommendations) {
      const rej = rejectedMap[r.rec_id];
      const cls = accepted.has(r.rec_id) ? "acc" : rej ? "rej" : "";
      h += `<li class="${cls}" title="${esc(rej ? rej.reject_reason : r.action)}">${esc(r.title)}</li>`;
    }
    h += `</ul></div>`;
  }
  h += `<div class="flow-arrow" aria-hidden="true">→</div><div class="panel critic"><h3>Агент-критик</h3>
    <div class="big">${st.accepted.length}<span> из ${totalRecs}</span></div><p class="meta">идей принято</p>
    <p class="meta">Без ссылок на факты отброшено: ${pre}<br>Отклонено критиком: ${byCritic}</p></div></div>`;
  if (st.rejected.length) {
    h += `<details class="rejected"><summary>Почему отклонены ${st.rejected.length} идеи</summary><ul>`;
    for (const r of st.rejected) h += `<li><s>${esc(r.title)}</s> — ${esc(r.reject_reason)} <span class="m">(${esc(r.rejected_by)})</span></li>`;
    h += `</ul></details>`;
  }
  h += `</section>`;

  // контент-план
  if (st.content_plan.length) {
    h += `<section class="section"><div class="sec-head"><h2>План на две недели</h2></div><div class="plan">`;
    for (const p of st.content_plan) {
      h += `<div class="plan-item"><div class="day">День ${esc(p.day)}</div><div class="when">${esc(p.time)}, ${esc(p.format)}</div>
        <div class="topic">${esc(p.topic)}</div><div class="hook">${esc(p.hook)}</div></div>`;
    }
    h += `</div></section>`;
  }

  // эксперименты и чего избегать
  h += `<section class="section two">`;
  h += `<div class="panel"><h2>Что проверить экспериментом</h2>${st.experiments.length ? `<ol class="exp">${st.experiments.map((e) =>
    `<li>${esc(e.hypothesis)}<div class="k">${esc(e.how)} Метрика: ${esc(e.metric)}. Срок: ${esc(e.duration)}.</div></li>`).join("")}</ol>` : "<p>Все выводы достаточно надёжны — экспериментов не требуется.</p>"}</div>`;
  h += `<div class="panel"><h2>Чего избегать</h2>${st.avoid.length ? `<ul class="exp">${st.avoid.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "<p>Явно слабых тем и форматов не найдено.</p>"}</div>`;
  h += `</section>`;

  // аналитика
  h += `<section class="section"><div class="sec-head"><h2>Что показывают данные</h2>
    <p>Виральность — охват поста относительно обычного охвата канала. 1× — как обычно, 2× — вдвое больше. Бледные строки — мало постов, вывод ненадёжен.</p></div><div class="charts">`;
  h += `<div class="panel chart"><h3>Охват по неделям</h3><p class="cap">медиана просмотров поста</p>${lineChart(a.trend.weekly)}</div>`;
  h += `<div class="panel chart"><h3>Формат</h3><p class="cap">медианная виральность</p>${divergingBars(a.by_media)}</div>`;
  h += `<div class="panel chart"><h3>Время публикации, МСК</h3><p class="cap">медианная виральность</p>${divergingBars(a.by_slot)}</div>`;
  h += `<div class="panel chart"><h3>День недели</h3><p class="cap">медианная виральность</p>${divergingBars(a.by_weekday)}</div>`;
  h += `<div class="panel chart"><h3>Длина поста</h3><p class="cap">медианная виральность</p>${divergingBars(a.by_length)}</div>`;
  h += `<div class="panel chart"><h3>Форматы у конкурентов</h3><p class="cap">медианная виральность</p>${divergingBars(an.competitor_media)}</div>`;
  h += `</div></section>`;

  // бенчмарк
  h += `<section class="section"><div class="sec-head"><h2>Сравнение с конкурентами</h2></div><div class="panel table-wrap"><table>
    <thead><tr><th>Канал</th><th class="n">Подписчики</th><th class="n">Постов в неделю</th><th class="n">Медианный охват</th><th class="n">ERR</th><th class="n">ER</th><th class="n">Тренд в месяц</th><th>Лучший формат</th></tr></thead><tbody>`;
  for (const b of an.benchmark) {
    if (b.empty) continue;
    h += `<tr class="${b.role === "author" ? "me" : ""}"><td>${esc(b.title)}</td><td class="n">${fmtInt(b.subscribers)}</td><td class="n">${esc(String(b.posts_per_week ?? "—").replace(".", ","))}</td>
      <td class="n">${fmtInt(b.median_views)}</td><td class="n">${fmtPct(b.err)}</td><td class="n">${fmtPct(b.median_er)}</td><td class="n">${signPct(b.monthly_change)}</td><td>${esc(b.top_format ?? "—")}</td></tr>`;
  }
  h += `</tbody></table></div></section>`;

  // темы
  const tp = an.topic_performance;
  if (tp.ok) {
    const lbls = (arr) => new Set(arr.map((r) => r.label));
    const gaps = lbls(tp.gaps), strong = lbls(tp.strong), weak = lbls(tp.weak);
    const maxShare = Math.max(0.01, ...tp.table.map((r) => Math.max(r.author.share, r.competitors ? r.competitors.share : 0)));
    h += `<section class="section"><div class="sec-head"><h2>Темы</h2><p>Найдены автоматически: TF-IDF и NMF по текстам всех каналов.</p></div><div class="panel table-wrap"><table>
      <thead><tr><th>Тема</th><th>Доля у вас</th><th>Доля у конкурентов</th><th class="n">Виральность у вас</th><th class="n">У конкурентов</th></tr></thead><tbody>`;
    for (const r of tp.table) {
      const tag = gaps.has(r.label) ? `<span class="tag gap">свободная ниша</span>` : strong.has(r.label) ? `<span class="tag strong">сильная</span>` : weak.has(r.label) ? `<span class="tag weak">слабая</span>` : "";
      const bar = (v, c) => `<span class="share ${c}" style="width:${Math.round((v / maxShare) * 80)}px"></span>${fmtPct(v, 0)}`;
      h += `<tr><td>${esc(r.label)}${tag}</td><td>${bar(r.author.share, "")}</td><td>${r.competitors ? bar(r.competitors.share, "c") : "—"}</td>
        <td class="n">${fmtX(r.author.median_virality)}</td><td class="n">${r.competitors ? fmtX(r.competitors.median_virality) : "—"}</td></tr>`;
    }
    h += `</tbody></table></div></section>`;
  }

  // посты
  const postList = (arr, up) => `<ul class="posts">${arr.map((p) => `<li><span class="v ${up ? "up" : "down"}">${fmtX(p.virality)}</span>
      <span class="m">${esc(p.date)} · ${esc(p.media_type)} · ${fmtInt(p.views)} просмотров${p.url ? ` · <a href="${esc(p.url)}" target="_blank" rel="noopener">открыть</a>` : ""}</span>
      <p class="s">${esc(p.snippet || "без текста")}</p></li>`).join("")}</ul>`;
  h += `<section class="section two"><div class="panel"><h2>Лучшие посты</h2>${postList(a.top_posts, true)}</div>
        <div class="panel"><h2>Слабые посты</h2>${postList(a.flop_posts, false)}</div></section>`;

  // фактическая база
  h += `<section class="section"><details class="panel"><summary><h2 style="display:inline">Фактическая база (${an.facts.length})</h2></summary>
    <ul class="facts-list">${an.facts.map((f) => `<li><b>${esc(f.id)}</b>${esc(f.text)} <span class="c">— ${esc(f.category)}, уверенность: ${esc(f.confidence)}</span></li>`).join("")}</ul></details></section>`;

  const el = $("#result");
  el.innerHTML = h;
  el.hidden = false;
  if (!noScroll) el.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
}

/* ---------- старт ---------- */
function initStatic(S) {
  const href = URL.createObjectURL(new Blob([S.report], { type: "text/markdown;charset=utf-8" }));
  $("#demo").addEventListener("click", () => render(S.result, null, href));
  $("#form").addEventListener("submit", (e) => {
    e.preventDefault();
    showFormError("Это статическая демо-версия. Чтобы проанализировать свои каналы, запустите сервис — инструкция в репозитории на GitHub.");
  });
  const mode = $("#mode");
  mode.textContent = "Статическое демо";
  render(S.result, null, href, true);
}

async function init() {
  if (window.VEKTOR_STATIC) {
    makeSource($(".source[data-role=author]"), false);
    makeSource($("#competitors"), true);
    initStatic(window.VEKTOR_STATIC);
    return;
  }
  makeSource($(".source[data-role=author]"), false);
  makeSource($("#competitors"), true);
  $("#add-comp").addEventListener("click", () => { makeSource($("#competitors"), true); toggleAdd(); });
  $("#demo").addEventListener("click", () => startJob({ demo: true }));
  $("#form").addEventListener("submit", (e) => {
    e.preventDefault();
    const author = readSource($(".source[data-role=author] .src-row"));
    if (!(author.id || author.upload_id)) { showFormError("Укажите ваш канал или загрузите CSV."); return; }
    const competitors = [...document.querySelectorAll("#competitors .src-row")].map(readSource).filter((s) => s.id || s.upload_id);
    startJob({ author, competitors, max_posts: +$("#max-posts").value, vk_token: $("#vk-token").value.trim() || undefined });
  });
  try {
    CONFIG = await (await fetch("/api/config")).json();
  } catch (_) { /* интерфейс работает и без конфигурации */ }
  const mode = $("#mode");
  mode.textContent = CONFIG.llm.enabled ? `LLM: ${CONFIG.llm.model}` : "Офлайн-режим, без LLM";
  mode.classList.toggle("on", CONFIG.llm.enabled);
  updateVkToken();
}

init();
