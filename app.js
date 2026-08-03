/* =========================================================
 * 全球 AI 大模型综合能力排行榜 — 实时数据引擎
 * ---------------------------------------------------------
 * 1) 优先实时抓取 Artificial Analysis 公开排行榜页面(CORS 允许),
 *    浏览器内解析 Next.js RSC payload,数据时间 = 刚刚;
 * 2) 失败则回退到仓库内每日快照 data/llms.json(GitHub Actions 更新);
 * 3) 合并 overrides.json(新发布但 AA 尚未收录的模型,如 Qwen3.8-Max)。
 * ========================================================= */
"use strict";

const AA_PAGE = "https://artificialanalysis.ai/leaderboards/models";
const SNAPSHOT = "data/llms.json";
const OVERRIDES = "overrides.json";
const RAW_PREFIX = "https://raw.githubusercontent.com";
const REPO = ""; // 部署时自动从 <link repo> 读取

const AUTO_REFRESH_MS = 30 * 60 * 1000; // 30 分钟自动刷新
const CACHE_TTL = 30 * 60 * 1000;

const DIMS = [
  { key: "composite", label: "综合", w: 0 },
  { key: "intelligenceIndex", label: "智能", w: 0.35 },
  { key: "codingIndex", label: "编码", w: 0.25 },
  { key: "agenticIndex", label: "Agentic", w: 0.2 },
  { key: "terminal", label: "Terminal", w: 0.2 },
];

const state = {
  models: [],        // 合并后的模型(含 overlay)
  sortKey: "composite",
  desc: true,
  limit: 20,
  aggregate: true,   // 默认同一基础模型只保留最强变体
  source: null,      // "live" | "snapshot"
  fetchedAt: null,
  count: 0,
  refreshing: false,
};

/* ---------------- helpers ---------------- */
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function fmt(v, d = 1) {
  if (v == null || !isFinite(v)) return null;
  return v.toFixed(d);
}
function pct(v) {
  if (v == null || !isFinite(v)) return null;
  return (v * 100).toFixed(1);
}
function nowStr() {
  return new Date().toLocaleString("zh-CN", { hour12: false });
}
function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (isErr ? " err" : "");
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.className = "toast"), 2600);
}

/* ---------------- AA 页面解析(浏览器端 RSC) ---------------- */
function cleanValue(v) {
  if (v === "$undefined") return null;
  if (Array.isArray(v)) return v.map(cleanValue);
  if (v && typeof v === "object") {
    const o = {};
    for (const k of Object.keys(v)) o[k] = cleanValue(v[k]);
    return o;
  }
  return v;
}
function* iterNested(obj) {
  yield obj;
  if (Array.isArray(obj)) for (const v of obj) yield* iterNested(v);
  else if (obj && typeof obj === "object") for (const k of Object.keys(obj)) yield* iterNested(obj[k]);
}
function findModelsRoot(obj) {
  for (const node of iterNested(obj)) {
    if (node && typeof node === "object" && !Array.isArray(node)) {
      const ms = node["models"];
      if (Array.isArray(ms) && ms.length && ms[0] && typeof ms[0] === "object" && "modelCreatorId" in ms[0]) {
        return cleanValue(ms);
      }
    }
  }
  return null;
}
function parseAAPage(html) {
  const re = /self\.__next_f\.push\(\[1,("(?:\\.|[^"\\])*")\]\)<\/script>/g;
  let m;
  while ((m = re.exec(html)) !== null) {
    let decoded;
    try { decoded = JSON.parse(m[1]); } catch (e) { continue; }
    if (typeof decoded !== "string" || !decoded.includes('"models":') || !decoded.includes(":")) continue;
    const payload = decoded.slice(decoded.indexOf(":") + 1);
    let obj;
    try { obj = JSON.parse(payload); } catch (e) { continue; }
    const models = findModelsRoot(obj);
    if (models) return models;
  }
  return null;
}

/* ---------------- 归一化 ---------------- */
function normalize(raw) {
  return {
    id: raw.id,
    name: raw.name,
    shortName: raw.shortName || raw.name,
    slug: raw.slug,
    releaseDate: raw.releaseDate,
    isReasoning: raw.isReasoning,
    deprecated: raw.deprecated === true,
    estimated: raw.estimated === true,
    official: raw.official || null,
    calibrationNote: raw.calibrationNote || null,
    sources: raw.sources || [],
    creator: {
      name: raw.modelCreatorName || (raw.creator && raw.creator.name),
      slug: raw.modelCreatorSlug || (raw.creator && raw.creator.slug),
      country: raw.modelCreatorCountry || (raw.creator && raw.creator.country),
      color: raw.modelCreatorColor || (raw.creator && raw.creator.color) || "#64748b",
    },
    evaluations: {
      intelligenceIndex: raw.evaluations ? raw.evaluations.intelligenceIndex : raw.intelligenceIndex ?? null,
      intelligenceIndexIsEstimated: raw.evaluations ? raw.evaluations.intelligenceIndexIsEstimated : null,
      codingIndex: raw.evaluations ? raw.evaluations.codingIndex : raw.codingIndex ?? null,
      agenticIndex: raw.evaluations ? raw.evaluations.agenticIndex : raw.agenticIndex ?? null,
      terminalbenchHard: raw.evaluations ? raw.evaluations.terminalbenchHard : raw.terminalbenchHard ?? null,
      terminalbenchV21: raw.evaluations ? raw.evaluations.terminalbenchV21 : raw.terminalbenchV21 ?? null,
    },
    pricing: raw.pricing || null,
    performance: raw.performance || null,
    contextWindowTokens: raw.contextWindowTokens ?? null,
    totalParameters: raw.totalParameters ?? null,
    activeParameters: raw.activeParameters ?? null,
    isOpenWeights: raw.isOpenWeights ?? null,
  };
}

/* ---------------- 评分 ---------------- */
function terminalScore(m) {
  const ev = m.evaluations;
  const v21 = ev.terminalbenchV21;
  const hard = ev.terminalbenchHard;
  if (v21 != null && isFinite(v21)) return { v: v21 * 100, ver: "2.1" };
  if (hard != null && isFinite(hard)) return { v: hard * 100, ver: "Hard" };
  return { v: null, ver: null };
}
function compositeScore(m) {
  const ev = m.evaluations;
  const t = terminalScore(m);
  const dims = [
    { v: ev.intelligenceIndex, w: 0.35 },
    { v: ev.codingIndex, w: 0.25 },
    { v: ev.agenticIndex, w: 0.2 },
    { v: t.v, w: 0.2 },
  ];
  let sum = 0, wsum = 0;
  for (const d of dims) {
    if (d.v != null && isFinite(d.v)) { sum += d.v * d.w; wsum += d.w; }
  }
  return wsum > 0 ? sum / wsum : null;
}
function dimValue(m, key) {
  if (key === "composite") return compositeScore(m);
  if (key === "terminal") return terminalScore(m).v;
  const v = m.evaluations[key];
  return v != null && isFinite(v) ? v : null;
}

/* ---------------- 聚合 ---------------- */
function baseNameOf(m) {
  const n = (m.name || "").trim();
  const i = n.indexOf("(");
  return (i > 0 ? n.slice(0, i) : n).trim();
}
// 同一基础模型的多个推理档位/变体,仅保留综合分最高的(可切换显示全部)
function aggregateByBase(models) {
  const map = new Map();
  for (const m of models) {
    const base = baseNameOf(m);
    const prev = map.get(base);
    if (!prev || m._composite > prev._composite) {
      map.set(base, { ...m, baseName: base, variants: prev ? prev.variants + 1 : 1 });
    } else {
      prev.variants += 1;
    }
  }
  return [...map.values()];
}

/* ---------------- 数据获取 ---------------- */
function getRepoBase() {
  // 1) 显式 <link rel="repo" href="https://github.com/user/repo">
  const link = document.querySelector('link[rel="repo"]');
  if (link && link.href) {
    const u = new URL(link.href);
    const parts = u.pathname.split("/").filter(Boolean);
    if (parts.length >= 2) return `${u.origin}/${parts[0]}/${parts[1]}/main`;
  }
  // 2) GitHub Pages 部署时从域名自动推断: https://user.github.io/repo/ → raw.githubusercontent.com/user/repo/main
  if (location.hostname.endsWith(".github.io")) {
    const user = location.hostname.split(".")[0];
    const parts = location.pathname.split("/").filter(Boolean);
    if (user && parts.length) return `https://raw.githubusercontent.com/${user}/${parts[0]}/main`;
  }
  return RAW_PREFIX + REPO;
}
function fetchLocal(url) {
  // GitHub Pages 同源相对路径;file:// 场景回退 raw URL
  return fetch(url, { signal: AbortSignal.timeout(20000) })
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error("local " + r.status))))
    .catch(() => fetch(getRepoBase() + "/" + url, { signal: AbortSignal.timeout(20000) }).then((r) => { if (!r.ok) throw new Error("raw " + r.status); return r.json(); }));
}

async function fetchLiveAA() {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 60000);
  try {
    const resp = await fetch(AA_PAGE, { signal: ctrl.signal });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const html = await resp.text();
    const raw = parseAAPage(html);
    if (!raw || !raw.length) throw new Error("no models payload");
    return raw;
  } finally {
    clearTimeout(timer);
  }
}

function mergeOverlays(models, overlays) {
  const list = models.slice();
  for (const ov of overlays || []) {
    const slugKey = (ov.slug || "").toLowerCase().replace(/[^a-z0-9]/g, "");
    const exists = list.some((m) => {
      const s = (m.slug || "").toLowerCase().replace(/[^a-z0-9]/g, "");
      return s === slugKey || (m.name || "").toLowerCase().includes((ov.shortName || "").toLowerCase());
    });
    if (!exists) list.push(ov);
  }
  return list;
}

async function loadData(forceLive = false) {
  if (state.refreshing) return;
  state.refreshing = true;
  const btn = $(".refresh-btn");
  btn.disabled = true;
  btn.classList.add("spin");
  $("#status-source").textContent = "正在获取最新数据…";

  const cached = !forceLive ? sessionStorage.getItem("aa_live") : null;
  let parsed = null;
  let source = null;
  let fetchedAt = null;

  if (cached) {
    try {
      const c = JSON.parse(cached);
      if (Date.now() - c.t < CACHE_TTL) {
        parsed = c.models;
        source = "live";
        fetchedAt = c.t;
      }
    } catch (e) { /* ignore */ }
  }

  if (!parsed) {
    try {
      parsed = await fetchLiveAA();
      source = "live";
      fetchedAt = Date.now();
      try { sessionStorage.setItem("aa_live", JSON.stringify({ t: fetchedAt, models: parsed })); } catch (e) {}
      toast("已实时拉取 Artificial Analysis 最新数据 ✓");
    } catch (e) {
      console.warn("live fetch failed, falling back to snapshot:", e);
      try {
        const snap = await fetchLocal(SNAPSHOT);
        parsed = snap.models;
        source = "snapshot";
        fetchedAt = snap.meta && snap.meta.fetched_at ? Date.parse(snap.meta.fetched_at) : null;
        toast("实时源暂不可用,已载入每日快照数据", true);
      } catch (e2) {
        toast("数据加载失败,请稍后重试", true);
        state.refreshing = false;
        btn.disabled = false;
        btn.classList.remove("spin");
        return;
      }
    }
  }

  // 合并 overrides
  let overlays = { models: [] };
  try { overlays = await fetchLocal(OVERRIDES); } catch (e) { console.warn("overrides unavailable", e); }
  const merged = mergeOverlays(parsed.map(normalize), (overlays.models || []).map(normalize));

  state.allModels = merged
    .filter((m) => !m.deprecated)
    .map((m) => ({ ...m, _composite: compositeScore(m), _terminal: terminalScore(m) }))
    .filter((m) => m._composite != null);
  state.models = state.aggregate ? aggregateByBase(state.allModels) : state.allModels;
  state.count = state.allModels.length;
  state.source = source;
  state.fetchedAt = fetchedAt;
  state.count = merged.length;
  state.refreshing = false;
  btn.disabled = false;
  btn.classList.remove("spin");

  render();
  renderChart();
  renderStatus();
}

/* ---------------- 渲染 ---------------- */
function rankBadge(i) {
  const cls = i === 0 ? "r1" : i === 1 ? "r2" : i === 2 ? "r3" : "other";
  return `<span class="badge-rank ${cls}">${i + 1}</span>`;
}

function scoreCell(val, opts = {}) {
  if (val == null) return `<span class="na">—</span>`;
  const w = Math.max(4, Math.min(100, val));
  return `<div class="val">${val.toFixed(1)}</div><div class="bar"><i style="width:${w}%"></i></div>`;
}

function modelCell(m, i) {
  const isQwen = /qwen/i.test(m.slug || m.name || "");
  const tags = [];
  if (isQwen && /3\.8/i.test(m.name || "")) tags.push('<span class="tag tag-new">今日发布</span>');
  if (m.estimated) tags.push('<span class="tag tag-est">估算</span>');
  if (m.isOpenWeights) tags.push('<span class="tag tag-open">开源</span>');
  const metaBits = [];
  if (m.creator && m.creator.name) metaBits.push(`<span class="creator-dot" style="background:${esc(m.creator.color || "#64748b")}"></span>${esc(m.creator.name)}`);
  if (m.variants > 1) metaBits.push(`${m.variants} 档`);
  if (m.releaseDate) metaBits.push(m.releaseDate);
  if (m.totalParameters) metaBits.push(Number(m.totalParameters) >= 1e12 ? `${(m.totalParameters / 1e12).toFixed(1)}T` : `${Math.round(m.totalParameters / 1e9)}B`);
  const title = m.name;
  return `<div class="mname"><span title="${esc(title)}">${esc(m.shortName || m.name)}</span>${tags.join("")}</div>
          <div class="mmeta">${metaBits.join(" · ")}</div>`;
}

function rowHtml(m, i) {
  const isQwen38 = /qwen/i.test(m.slug || "") && /3\.8/i.test(m.name || "");
  const t = m._terminal;
  const terminalLabel = t.v != null ? `TB ${t.ver}` : "";
  return `<tr class="${isQwen38 ? "qwen-row" : ""}">
    <td class="cell-rank">${rankBadge(i)}</td>
    <td>${modelCell(m, i)}</td>
    <td class="td-composite">${m._composite != null ? m._composite.toFixed(1) : "—"}</td>
    <td class="cell-score">${scoreCell(dimValue(m, "intelligenceIndex"))}</td>
    <td class="cell-score">${scoreCell(dimValue(m, "codingIndex"))}</td>
    <td class="cell-score">${scoreCell(dimValue(m, "agenticIndex"))}</td>
    <td class="cell-score dim-terminal" title="${terminalLabel}">${scoreCell(t.v)}</td>
  </tr>`;
}

function sortModels() {
  const key = state.sortKey;
  const get = (m) => dimValue(m, key);
  state.models.sort((a, b) => {
    const va = get(a), vb = get(b);
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    return state.desc ? vb - va : va - vb;
  });
}

function render() {
  sortModels();
  const tbody = $("#tbody");
  const rows = state.models.slice(0, state.limit).map(rowHtml).join("");
  tbody.innerHTML = rows;
  $("#more-wrap").style.display = state.models.length > state.limit ? "" : "none";
  const aggNote = state.aggregate ? "同一基础模型仅保留最强档位" : "含全部推理档位条目";
  $("#table-note").textContent = `已收录 ${state.count} 条记录 · 当前${state.aggregate ? `展示 ${state.models.length} 个基础模型` : `展示 ${state.models.length} 个条目`}(${aggNote})· 显示前 ${Math.min(state.limit, state.models.length)} 名`;
}

function renderChart() {
  const box = $("#chart-bars");
  const rows = state.models.slice(0, 12).map((m, i) => {
    const isQwen38 = /qwen/i.test(m.slug || "") && /3\.8/i.test(m.name || "");
    const label = (m.shortName || m.name).replace(/\s*\(.*\)$/, "");
    return `<div class="bar-row ${isQwen38 ? "qwen" : ""}">
      <div class="rank">${i + 1}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${Math.max(6, m._composite)}%">${esc(label)}</div></div>
      <div class="score">${m._composite.toFixed(1)}</div>
    </div>`;
  });
  box.innerHTML = rows.join("");
}

function renderStatus() {
  const srcEl = $("#status-source");
  if (state.source === "live") {
    srcEl.innerHTML = `<b>实时</b> · Artificial Analysis 页面直抓`;
  } else if (state.source === "snapshot") {
    srcEl.innerHTML = `<b>快照</b> · GitHub Actions 每日更新`;
  } else {
    srcEl.innerHTML = "—";
  }
  $("#status-time").textContent = state.fetchedAt ? new Date(state.fetchedAt).toLocaleString("zh-CN", { hour12: false }) : "—";
  $("#status-count").textContent = state.count + " 条记录";
  if (state.source === "live") {
    $("#status-live").textContent = "本次为实时抓取,数据即当前最新";
  } else if (state.source === "snapshot") {
    $("#status-live").textContent = "快照由每日自动任务更新,最新数据可能在实时源恢复后自动切换";
  }
}

/* ---------------- 交互 ---------------- */
function setupTabs() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      const key = tab.dataset.key;
      if (state.sortKey === key) {
        state.desc = !state.desc;
      } else {
        state.sortKey = key;
        state.desc = true;
      }
      document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.key === key));
      $("#tab-arrow").textContent = state.desc ? " ↓" : " ↑";
      render();
    });
  });
}

function setupAggregate() {
  const btn = $("#agg-btn");
  if (!btn) return;
  btn.addEventListener("click", () => {
    state.aggregate = !state.aggregate;
    state.models = state.aggregate ? aggregateByBase(state.allModels) : state.allModels;
    btn.textContent = state.aggregate ? "聚合模式:最强变体 ✓" : "聚合模式:全部档位";
    btn.classList.toggle("active", state.aggregate);
    render();
    renderChart();
  });
}

function startAutoRefresh() {
  const el = $("#status-timer");
  setInterval(() => {
    const remain = state.fetchedAt ? CACHE_TTL - (Date.now() - state.fetchedAt) : 0;
    if (remain > 0) {
      const m = Math.floor(remain / 60000), s = Math.floor((remain % 60000) / 1000);
      el.textContent = `${m}分${String(s).padStart(2, "0")}秒`;
    } else {
      el.textContent = "即将自动刷新…";
      loadData(true);
    }
  }, 1000);
}

/* ---------------- init ---------------- */
document.addEventListener("DOMContentLoaded", () => {
  setupTabs();
  setupAggregate();
  document.querySelector(".refresh-btn").addEventListener("click", () => loadData(true));
  document.querySelector(".more-btn").addEventListener("click", () => {
    state.limit += 30;
    render();
  });
  startAutoRefresh();
  loadData(false);
});
