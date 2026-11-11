/* TalentHub 前端逻辑 — 原生 JS 单文件，无构建步骤。
   视图: #/dashboard #/talents #/applications #/import #/data #/settings */
"use strict";

/* ================= 常量（X/Apple 色系） ================= */
let STATUSES = ["潜在", "跟进中", "已邀约", "已面试", "已录用", "不合适", "已归档"];  // 启动时从 /api/statuses 动态刷新（支持自定义状态）
const STATUS_COLORS = { "潜在": "#536471", "跟进中": "#1d9bf0", "已邀约": "#9333ea", "已面试": "#f59e0b", "已录用": "#00ba7c", "不合适": "#f4212e", "已归档": "#8b98a5", "待审核": "#f59e0b", "已同意": "#00ba7c", "已拒绝": "#f4212e" };
const RATINGS = ["A", "B", "C", "D"];
const RATING_COLORS = { A: "#00ba7c", B: "#1d9bf0", C: "#f59e0b", D: "#98a5af" };
const EDU_OPTIONS = ["初中", "高中", "中专", "大专", "本科", "硕士", "博士", "博士后"];
const AVATAR_COLORS = ["#1d9bf0", "#f59e0b", "#7856ff", "#f4212e", "#00ba7c", "#ff7a00", "#794bc4", "#00a06a"];
const CHART_COLORS = ["#1d9bf0", "#0f1419", "#f59e0b", "#00ba7c", "#7856ff", "#f4212e", "#ff7a00", "#794bc4", "#00d5c0"];
const TOOLTIP = { backgroundColor: "#0f1419", borderWidth: 0, textStyle: { color: "#f7f9f9", fontSize: 12 } };

const FORM_FIELDS = [
  { key: "name", label: "姓名 *", type: "text", req: true },
  { key: "gender", label: "性别", type: "select", options: ["", "男", "女"] },
  { key: "age", label: "年龄", type: "number" },
  { key: "phone", label: "电话", type: "text" },
  { key: "email", label: "邮箱", type: "text" },
  { key: "wechat", label: "微信", type: "text" },
  { key: "city", label: "城市", type: "text" },
  { key: "hometown", label: "籍贯", type: "text" },
  { key: "political_status", label: "政治面貌", type: "select", options: ["", "群众", "共青团员", "中共党员", "预备党员", "民主党派", "无党派人士"] },
  { key: "education", label: "学历", type: "select", options: ["", ...EDU_OPTIONS] },
  { key: "school", label: "毕业院校", type: "text" },
  { key: "major", label: "专业", type: "text" },
  { key: "graduate_year", label: "毕业年份", type: "number" },
  { key: "work_years", label: "工作年限", type: "number", step: "0.5" },
  { key: "current_company", label: "当前公司", type: "text" },
  { key: "current_title", label: "当前职位", type: "text" },
  { key: "expected_title", label: "期望职位", type: "text" },
  { key: "expected_salary", label: "期望薪资", type: "text" },
  { key: "status", label: "状态", type: "select", options: STATUSES },
  { key: "rating", label: "评级", type: "select", options: ["", ...RATINGS] },
  { key: "tags", label: "标签", type: "text", placeholder: "多个标签用、分隔" },
  { key: "source", label: "来源", type: "text", placeholder: "招聘会/内推/猎聘…" },
  { key: "certificates", label: "证书", type: "text", wide: true, placeholder: "多个证书用、分隔" },
  { key: "languages", label: "语言能力", type: "text", wide: true, placeholder: "如 英语（CET-6）、日语（N1）" },
  { key: "skills", label: "技能", type: "textarea", wide: true },
  { key: "work_history", label: "工作经历（每段一行：时间 公司 职位）", type: "textarea", wide: true },
  { key: "project_experience", label: "项目经验", type: "textarea", wide: true },
  { key: "summary", label: "简介 / 自我评价", type: "textarea", wide: true },
];

/* work_history 在库里是 JSON 串，编辑表单里转成每段一行的可读文本 */
function whToText(wh) {
  if (!wh) return "";
  if (!String(wh).trim().startsWith("[")) return String(wh);
  try {
    return JSON.parse(wh).map(s => [s.period, s.company, s.title].filter(Boolean).join("  ")).join("\n");
  } catch (_) { return String(wh); }
}

/* ================= 小工具 ================= */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function api(path, opts = {}) {
  const o = { ...opts };
  const token = sessionStorage.getItem("th_token");
  if (token) {
    o.headers = Object.assign({}, o.headers, { Authorization: `Bearer ${token}` });
  }
  const res = await fetch(path, o);
  let body = null;
  try { body = await res.json(); } catch (_) { /* 非 JSON 响应 */ }
  if (res.status === 401 && body && body.detail === "locked") {
    sessionStorage.removeItem("th_token");
    showLockScreen();
    throw new Error("屏幕已锁定，请输入密码解锁");
  }
  if (!res.ok) {
    const detail = body && (body.detail || body.message) || `HTTP ${res.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body;
}

function toast(msg, type = "ok", title = "") {
  const box = $("#toasts");
  const el = document.createElement("div");
  el.className = `toast ${type === "ok" ? "" : type}`;
  const icons = { ok: "✓", err: "✕", warn: "⚠" };
  el.innerHTML = `<b>${icons[type] || "•"}</b><span>${title ? `<b>${esc(title)}</b> ` : ""}${esc(msg)}</span>`;
  box.appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .3s"; setTimeout(() => el.remove(), 320); }, type === "err" ? 6500 : 3800);
}

function confirmBox(title, text, okLabel = "确认") {
  return new Promise(resolve => {
    const mask = $("#confirm-mask"), box = $("#confirm-box");
    $("#cf-title").textContent = title;
    $("#cf-text").innerHTML = text;
    $("#cf-ok").textContent = okLabel;
    mask.hidden = box.hidden = false;
    requestAnimationFrame(() => mask.classList.add("show"));
    const done = v => {
      mask.classList.remove("show");
      setTimeout(() => { mask.hidden = box.hidden = true; }, 200);
      $("#cf-ok").onclick = $("#cf-cancel").onclick = null;
      resolve(v);
    };
    $("#cf-ok").onclick = () => done(true);
    $("#cf-cancel").onclick = () => done(false);
    mask.onclick = () => done(false);
  });
}

function avatarColor(seed) {
  const s = String(seed || "");
  let h = 0;
  for (const ch of s) h = (h * 31 + ch.codePointAt(0)) >>> 0;
  return AVATAR_COLORS[h % AVATAR_COLORS.length];
}

function shortDate(s) { return String(s || "").slice(0, 10); }

function debounce(fn, ms) {
  let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

function buildForm(container, values = {}, fields = FORM_FIELDS) {
  container.innerHTML = fields.map(f => {
    const v = values[f.key] ?? "";
    const wide = f.wide ? " wide" : "";
    const opts = f.key === "status" ? STATUSES : f.options;   // 状态跟随自定义配置
    if (f.type === "select") {
      return `<div class="fg-item${wide}"><label for="ff-${f.key}">${f.label}</label>
        <select id="ff-${f.key}" data-key="${f.key}" class="input">
        ${opts.map(o => `<option value="${esc(o)}" ${String(v) === o ? "selected" : ""}>${esc(o) || "—"}</option>`).join("")}
        ${opts.includes(v) || !v ? "" : `<option value="${esc(v)}" selected>${esc(v)}</option>`}
        </select></div>`;
    }
    if (f.type === "textarea") {
      return `<div class="fg-item${wide}"><label for="ff-${f.key}">${f.label}</label>
        <textarea id="ff-${f.key}" data-key="${f.key}" class="input" rows="2" placeholder="${esc(f.placeholder || "")}">${esc(v)}</textarea></div>`;
    }
    return `<div class="fg-item${wide}"><label for="ff-${f.key}">${f.label}</label>
      <input id="ff-${f.key}" data-key="${f.key}" class="input" type="${f.type}"
      ${f.step ? `step="${f.step}"` : ""} value="${esc(v)}" placeholder="${esc(f.placeholder || "")}"></div>`;
  }).join("");
}

function collectForm(container) {
  const out = {};
  $$("[data-key]", container).forEach(el => {
    let v = el.value.trim();
    if (el.type === "number" && v !== "") v = Number(v);
    if (el.type === "number" && v === "") v = null;
    out[el.dataset.key] = v;
  });
  return out;
}

function ratingBadge(r) {
  return r && RATINGS.includes(r)
    ? `<span class="rating-badge rb-${r}" title="评级 ${r}">${r}</span>`
    : `<span class="rating-badge rating-none" title="未评级">–</span>`;
}

function statusPill(s) {
  return `<span class="pill st-${esc(s || STATUSES[0])}">${esc(s || STATUSES[0])}</span>`;
}

function skillTags(str, max = 3) {
  const arr = String(str || "").split(/[、,，;；]/).map(x => x.trim()).filter(Boolean);
  if (!arr.length) return `<span class="muted">—</span>`;
  const shown = arr.slice(0, max).map(s => `<span class="skill-tag">${esc(s)}</span>`).join("");
  return `<div class="skill-tags" title="${esc(arr.join("、"))}">${shown}${arr.length > max ? `<span class="skill-tag more">+${arr.length - max}</span>` : ""}</div>`;
}

function confBar(c) {
  const pct = Math.round((c || 0) * 100);
  const color = pct >= 75 ? "#15803d" : pct >= 50 ? "#d97706" : "#be3455";
  return `<span class="conf-bar"><i style="width:${pct}%;background:${color}"></i></span><span class="conf-num">${pct}%</span>`;
}

/* ================= 全局状态 ================= */
const state = {
  view: "dashboard",
  query: { q: "", status: "", rating: "", education: "", skill: "", sort: "created_at", order: "desc", page: 1, size: 15 },
  selected: new Set(),
  drafts: [],
  tablePreview: null,
  currentTalent: null,
  charts: {},
};

/* ================= 路由 ================= */
const VIEW_TITLES = {
  dashboard: ["仪表盘", "人才结构一眼看清"],
  talents: ["人才库", "精准管理潜在人才"],
  applications: ["投递审核", "候选人投递 · 同意入选 / 拒绝"],
  ai: ["AI 助手", "本地小模型 / 外部大模型 · 流式对话"],
  import: ["导入中心", "简历自动提取 · 表格合并导入"],
  data: ["数据管理", "导出 / 备份 / 合并 / 日志"],
  settings: ["系统设置", "端口 · AI · 锁屏 · 版权"],
};

function route() {
  const hash = location.hash.replace(/^#\/?/, "") || "dashboard";
  const view = hash.split("?")[0];
  state.view = VIEW_TITLES[view] ? view : "dashboard";
  $$(".nav-item").forEach(a => a.classList.toggle("active", a.dataset.view === state.view));
  $$(".view").forEach(v => { v.hidden = v.id !== `view-${state.view}`; });
  $("#page-title").textContent = VIEW_TITLES[state.view][0];
  $("#page-sub").textContent = VIEW_TITLES[state.view][1];
  const loaders = { dashboard: loadDashboard, talents: loadTalents, applications: loadApplications, ai: loadAiView, data: loadDataView, settings: loadSettings };
  (loaders[state.view] || (() => {}))();
  $("#content").scrollTop = 0;
}
window.addEventListener("hashchange", route);

/* ================= 仪表盘 ================= */
function chart(id) {
  if (!state.charts[id]) {
    const el = document.getElementById(id);
    if (!el) return null;
    state.charts[id] = echarts.init(el);
  }
  return state.charts[id];
}

function chartBase(extra = {}) {
  return Object.assign({
    color: CHART_COLORS,
    animationDuration: 650,
    textStyle: { fontFamily: "-apple-system, PingFang SC, Microsoft YaHei, sans-serif" },
    toolbox: { feature: { saveAsImage: { title: "存为图片", pixelRatio: 2 } }, right: 6, top: 0, iconStyle: { borderColor: "#536471" } },
    tooltip: { trigger: "item", backgroundColor: "#0f1419", borderWidth: 0, textStyle: { color: "#f7f9f9", fontSize: 12 } },
  }, extra);
}

async function loadDashboard() {
  let s;
  try { s = await api("/api/stats"); }
  catch (e) { toast(e.message, "err", "统计加载失败"); return; }

  const cards = [
    { label: "人才总数", value: s.total, unit: "人", color: "#1d9bf0", sub: `累计入库候选人`, icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/></svg>` },
    { label: "本月新增", value: s.month_new, unit: "人", color: "#d97706", sub: s.total ? `占总量 <b>${Math.round(s.month_new / s.total * 100)}%</b>` : "—" , icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>` },
    { label: "高潜人才 (A/B)", value: s.high_potential, unit: "人", color: "#15803d", sub: s.total ? `占比 <b>${Math.round(s.high_potential / s.total * 100)}%</b>，优先跟进` : "评级后自动统计", icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>` },
    { label: "跟进中", value: s.following, unit: "人", color: "#2f6fed", sub: `状态为「跟进中」待推进`, icon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>` },
  ];
  $("#stat-cards").innerHTML = cards.map(c => `
    <div class="stat-card" style="--sc:${c.color}">
      <span class="sc-label">${c.icon}${c.label}</span>
      <span class="sc-value">${c.value}<small>${c.unit}</small></span>
      <span class="sc-sub">${c.sub}</span>
    </div>`).join("");

  const noData = s.total === 0;
  const emptyTip = "录入人才后这里会给出分布结论";

  /* 趋势 */
  const trend = chart("chart-trend");
  trend && trend.setOption(chartBase({
    grid: { left: 40, right: 20, top: 30, bottom: 28 },
    tooltip: { trigger: "axis", backgroundColor: "#0f1419", borderWidth: 0, textStyle: { color: "#f7f9f9", fontSize: 12 }, valueFormatter: v => `${v} 人` },
    xAxis: { type: "category", data: s.monthly_trend.map(m => m.month.slice(2).replace("-", "/")), axisLine: { lineStyle: { color: "#eff3f4" } }, axisLabel: { color: "#536471", fontSize: 11 } },
    yAxis: { type: "value", minInterval: 1, name: "人", nameTextStyle: { color: "#536471" }, splitLine: { lineStyle: { color: "#eff3f4" } }, axisLabel: { color: "#536471" } },
    series: [{ type: "line", data: s.monthly_trend.map(m => m.count), smooth: true, symbol: "circle", symbolSize: 6, lineStyle: { width: 2.5, color: "#1d9bf0" }, itemStyle: { color: "#1d9bf0" },
      areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: "rgba(29,155,240,.22)" }, { offset: 1, color: "rgba(29,155,240,.02)" }]) } }],
  }), true);
  if (noData) { $("#trend-insight").textContent = emptyTip; }
  else {
    const t = s.monthly_trend, l3 = t.slice(-3).reduce((a, b) => a + b.count, 0), p3 = t.slice(-6, -3).reduce((a, b) => a + b.count, 0);
    const diff = l3 - p3;
    $("#trend-insight").innerHTML = diff === 0 && l3 === 0 ? "近 6 个月没有新增记录，可从「导入中心」批量导入简历。"
      : `近 3 个月新增 <b>${l3}</b> 人，比前 3 个月${diff >= 0 ? "多" : "少"} <b>${Math.abs(diff)}</b> 人，入库节奏${diff > 0 ? "在加快" : diff < 0 ? "在放缓" : "持平"}。`;
  }

  /* 学历 donut */
  const edu = chart("chart-edu");
  edu && edu.setOption(chartBase({
    tooltip: { trigger: "item", backgroundColor: "#0f1419", borderWidth: 0, textStyle: { color: "#f7f9f9", fontSize: 12 }, formatter: p => `${p.name}：${p.value} 人（${p.percent}%）` },
    legend: { orient: "vertical", right: 8, top: "center", itemWidth: 10, itemHeight: 10, textStyle: { color: "#38444d", fontSize: 12 } },
    series: [{ type: "pie", radius: ["46%", "72%"], center: ["38%", "52%"], data: s.by_education,
      label: { show: false }, emphasis: { label: { show: true, fontSize: 15, fontWeight: 700, formatter: "{b}\n{c} 人" } },
      itemStyle: { borderRadius: 6, borderColor: "#fff", borderWidth: 2 } }],
  }), true);
  if (noData) { $("#edu-insight").textContent = emptyTip; }
  else {
    const top = s.by_education[0];
    const high = s.by_education.filter(e => ["本科", "硕士", "博士", "博士后"].includes(e.name)).reduce((a, b) => a + b.value, 0);
    $("#edu-insight").innerHTML = `以 <b>${esc(top.name)}</b> 为主（${top.value} 人），本科及以上共 <b>${high}</b> 人、占 <b>${Math.round(high / s.total * 100)}%</b>。`;
  }

  /* 状态横条 */
  const st = chart("chart-status");
  const stData = s.by_status.filter(x => x.value > 0);
  st && st.setOption(chartBase({
    grid: { left: 8, right: 44, top: 10, bottom: 8, containLabel: true },
    tooltip: { trigger: "item", backgroundColor: "#0f1419", borderWidth: 0, textStyle: { color: "#f7f9f9", fontSize: 12 }, formatter: p => `${p.name}：${p.value} 人` },
    xAxis: { type: "value", show: false, minInterval: 1 },
    yAxis: { type: "category", data: stData.map(x => x.name).reverse(), axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: "#38444d", fontSize: 12 } },
    toolbox: { show: false },
    series: [{ type: "bar", data: stData.map(x => ({ value: x.value, itemStyle: { color: STATUS_COLORS[x.name] || "#1d9bf0" } })).reverse(),
      barWidth: 14, itemStyle: { borderRadius: [0, 7, 7, 0] }, label: { show: true, position: "right", color: "#38444d", fontSize: 11, formatter: "{c} 人" } }],
  }), true);

  /* 技能 TOP10 */
  const sk = chart("chart-skills");
  sk && sk.setOption(chartBase({
    grid: { left: 8, right: 44, top: 10, bottom: 8, containLabel: true },
    tooltip: { trigger: "item", backgroundColor: "#0f1419", borderWidth: 0, textStyle: { color: "#f7f9f9", fontSize: 12 }, formatter: p => `${p.name}：${p.value} 人次` },
    xAxis: { type: "value", show: false, minInterval: 1 },
    yAxis: { type: "category", data: s.top_skills.map(x => x.name).reverse(), axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: "#38444d", fontSize: 11.5, width: 90, overflow: "truncate" } },
    toolbox: { show: false },
    series: [{ type: "bar", data: s.top_skills.map(x => x.value).reverse(), barWidth: 12,
      itemStyle: { borderRadius: [0, 6, 6, 0], color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [{ offset: 0, color: "#1d9bf0" }, { offset: 1, color: "#71c9f8" }]) },
      label: { show: true, position: "right", color: "#38444d", fontSize: 11, formatter: "{c}" } }],
  }), true);

  /* 来源 */
  const src = chart("chart-source");
  src && src.setOption(chartBase({
    tooltip: { trigger: "item", backgroundColor: "#0f1419", borderWidth: 0, textStyle: { color: "#f7f9f9", fontSize: 12 }, formatter: p => `${p.name}：${p.value} 人（${p.percent}%）` },
    toolbox: { show: false },
    series: [{ type: "pie", radius: ["30%", "68%"], roseType: s.by_source.length > 3 ? "radius" : false, data: s.by_source,
      label: { color: "#38444d", fontSize: 11, formatter: "{b}\n{c}人" }, labelLine: { length: 8, length2: 8 },
      itemStyle: { borderRadius: 5, borderColor: "#fff", borderWidth: 2 } }],
  }), true);

  /* 最近添加 */
  $("#recent-list").innerHTML = s.recent.length ? s.recent.map(t => `
    <div class="recent-item" data-id="${t.id}">
      <span class="avatar" style="background:${avatarColor(t.name || t.id)};width:32px;height:32px;border-radius:8px;font-size:13px">${esc((t.name || "?").slice(0, 1))}</span>
      <span class="ri-name">${esc(t.name || "未命名")}</span>
      ${ratingBadge(t.rating)} ${statusPill(t.status)}
      <span class="ri-meta">${esc([t.current_title || t.expected_title, t.current_company, t.education].filter(Boolean).join(" · ") || "字段待完善")}</span>
      <span class="ri-time">${esc(shortDate(t.created_at))}</span>
    </div>`).join("")
    : `<div class="empty"><p>暂无人才</p><p class="sub">先到「导入中心」上传简历试试自动解析</p></div>`;
  $$("#recent-list .recent-item").forEach(el => el.addEventListener("click", () => openDrawer(Number(el.dataset.id))));
}

/* 图表自适应：窗口 + 容器尺寸变化都重绘。
   先清掉 echarts 初始化时写死在容器上的内联宽高，否则窄屏时 resize() 读到旧内联值，
   容器被 canvas 撑住形成循环锁定（实测 375px 下卡片被撑到 569px 溢出）。 */
const resizeCharts = debounce(() => Object.values(state.charts).forEach(c => {
  const el = c.getDom();
  el.style.width = "";
  el.style.height = "";
  c.resize();
}), 120);
window.addEventListener("resize", resizeCharts);
if (window.ResizeObserver) {
  const ro = new ResizeObserver(resizeCharts);
  document.addEventListener("DOMContentLoaded", () => $$(".chart").forEach(el => ro.observe(el)));
}

/* ================= 人才库 ================= */
function querystring(extra = {}) {
  const q = { ...state.query, ...extra };
  return new URLSearchParams(Object.entries(q).filter(([, v]) => v !== "" && v != null)).toString();
}

let talentsReqSeq = 0;  // 请求序号守卫：慢的旧响应不得覆盖新请求结果
async function loadTalents() {
  const seq = ++talentsReqSeq;
  state.selected.clear();
  updateBatchBar();
  let data;
  try { data = await api(`/api/talents?${querystring()}`); }
  catch (e) { toast(e.message, "err", "加载失败"); return; }
  if (seq !== talentsReqSeq) return;  // 已有更新的请求发出，丢弃本次过期结果
  const tbody = $("#talent-tbody");
  $("#table-empty").hidden = data.total > 0;
  tbody.innerHTML = data.items.map(t => `
    <tr data-id="${t.id}">
      <td><input type="checkbox" class="row-check" data-id="${t.id}" aria-label="选择 ${esc(t.name)}"></td>
      <td><div class="person" data-open="${t.id}">
        <span class="avatar" style="background:${avatarColor(t.name || t.id)}">${esc((t.name || "?").slice(0, 1))}</span>
        <span><span class="p-name">${esc(t.name || "未命名")}</span><br>
        <span class="p-meta">${esc([t.gender, t.age ? t.age + "岁" : "", t.city].filter(Boolean).join(" · ") || t.current_title || t.expected_title || "")}</span></span>
        ${hoverCard(t)}
      </div></td>
      <td><div class="contact-line">${esc(t.phone || "—")}${t.email ? `<br><span class="sub">${esc(t.email)}</span>` : ""}${t.wechat ? `<br><span class="sub">微信 ${esc(t.wechat)}</span>` : ""}</div></td>
      <td>${esc(t.education || "—")}<br><span class="muted" style="font-size:11.5px">${esc(t.school || "")}${t.graduate_year ? " · " + esc(t.graduate_year) : ""}</span></td>
      <td class="num">${t.work_years != null ? esc(t.work_years) + " 年" : "—"}</td>
      <td>${skillTags(t.skills)}</td>
      <td><select class="status-select st-${esc(t.status)}" data-status-id="${t.id}" aria-label="状态">
        ${STATUSES.map(s => `<option ${s === t.status ? "selected" : ""}>${s}</option>`).join("")}${STATUSES.includes(t.status) || !t.status ? "" : `<option selected>${esc(t.status)}</option>`}</select></td>
      <td>${ratingBadge(t.rating)}</td>
      <td class="muted" style="font-size:12px">${esc(t.source || "—")}</td>
      <td class="muted" style="font-size:12px;font-variant-numeric:tabular-nums">${esc(shortDate(t.created_at))}</td>
      <td><div class="ops">
        <button class="icon-btn" data-open="${t.id}" title="详情 / 编辑">✎</button>
        ${t.stored_path ? `<button class="icon-btn" data-preview-file="${t.id}" title="在线预览简历">👁</button>` : ""}
        ${t.stored_path ? `<a class="icon-btn" href="/api/files/${t.id}" title="下载原件" download>⤓</a>` : ""}
        <button class="icon-btn danger" data-del="${t.id}" data-name="${esc(t.name)}" title="删除">🗑</button>
      </div></td>
    </tr>`).join("");
  renderPager(data);
}

/* 行悬停预览卡：不点开抽屉也能看到关键信息 */
function hoverCard(t) {
  const rows = [
    ["当前", [t.current_company, t.current_title].filter(Boolean).join(" · ")],
    ["期望", [t.expected_title, t.expected_salary].filter(Boolean).join(" · ")],
    ["教育", [t.school, t.major, t.education].filter(Boolean).join(" · ")],
    ["证书", t.certificates], ["语言", t.languages], ["标签", t.tags],
    ["简介", t.summary],
  ].filter(([, v]) => v);
  if (!rows.length) return "";
  return `<div class="hover-card">${rows.map(([k, v]) =>
    `<div class="hc-row"><span class="hc-k">${k}</span><span class="hc-v">${esc(v)}</span></div>`).join("")}</div>`;
}

function renderPager(data) {
  const pages = Math.max(1, Math.ceil(data.total / data.size));
  const cur = data.page;
  const win = [];
  for (let p = 1; p <= pages; p++) {
    if (p === 1 || p === pages || Math.abs(p - cur) <= 2) win.push(p);
    else if (win[win.length - 1] !== "…") win.push("…");
  }
  $("#pager").innerHTML = `
    <span>共 <b style="color:var(--ink)">${data.total}</b> 人 · 第 ${cur}/${pages} 页</span>
    <span class="pages">
      <button ${cur <= 1 ? "disabled" : ""} data-page="${cur - 1}">‹</button>
      ${win.map(p => p === "…" ? `<span>…</span>` : `<button class="${p === cur ? "cur" : ""}" data-page="${p}">${p}</button>`).join("")}
      <button ${cur >= pages ? "disabled" : ""} data-page="${cur + 1}">›</button>
    </span>`;
  $$("#pager button[data-page]").forEach(b => b.addEventListener("click", () => {
    state.query.page = Number(b.dataset.page); loadTalents();
  }));
}

function updateBatchBar() {
  const n = state.selected.size;
  $("#batch-bar").hidden = n === 0;
  $("#batch-count").textContent = `已选 ${n} 人`;
}

async function batchOp(action, value) {
  const ids = [...state.selected];
  if (!ids.length) return;
  try {
    const r = await api("/api/talents/batch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids, action, value }) });
    toast(`批量操作完成，影响 ${r.affected} 条`);
    loadTalents();
  } catch (e) { toast(e.message, "err"); }
}

/* 人才表事件（委托） */
$("#talent-tbody").addEventListener("click", async e => {
  const pv = e.target.closest("[data-preview-file]");
  if (pv) { openFilePreview(`/api/files/${pv.dataset.previewFile}?inline=1`, "简历预览"); return; }
  const open = e.target.closest("[data-open]");
  if (open) { openDrawer(Number(open.dataset.open)); return; }
  const del = e.target.closest("[data-del]");
  if (del) {
    const ok = await confirmBox("删除确认", `确定删除 <b>${del.dataset.name || "该记录"}</b> 吗？此操作不可恢复。`, "删除");
    if (!ok) return;
    try { await api(`/api/talents/${del.dataset.del}`, { method: "DELETE" }); toast("已删除"); loadTalents(); }
    catch (err) { toast(err.message, "err"); }
  }
});
$("#talent-tbody").addEventListener("change", async e => {
  const chk = e.target.closest(".row-check");
  if (chk) {
    chk.checked ? state.selected.add(Number(chk.dataset.id)) : state.selected.delete(Number(chk.dataset.id));
    chk.closest("tr").classList.toggle("selected", chk.checked);
    updateBatchBar();
    return;
  }
  const st = e.target.closest("[data-status-id]");
  if (st) {
    try {
      await api(`/api/talents/${st.dataset.statusId}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ data: { status: st.value } }) });
      st.className = `status-select st-${st.value}`;
      toast(`状态已改为「${st.value}」`);
    } catch (err) { toast(err.message, "err"); loadTalents(); }
  }
});
$("#check-all").addEventListener("change", e => {
  $$("#talent-tbody .row-check").forEach(c => {
    c.checked = e.target.checked;
    c.closest("tr").classList.toggle("selected", c.checked);
    Number(c.dataset.id) && (c.checked ? state.selected.add(Number(c.dataset.id)) : state.selected.delete(Number(c.dataset.id)));
  });
  updateBatchBar();
});
$("#f-status").addEventListener("change", e => { state.query.status = e.target.value; state.query.page = 1; loadTalents(); });
$("#f-rating").addEventListener("change", e => {
  state.query.rating = e.target.value === "_none" ? "" : e.target.value;
  state.query._rating_none = e.target.value === "_none";
  state.query.page = 1; loadTalents();
});
$("#f-edu").addEventListener("change", e => { state.query.education = e.target.value; state.query.page = 1; loadTalents(); });
$("#f-skill").addEventListener("change", e => { state.query.skill = e.target.value; state.query.page = 1; loadTalents(); });
$("#f-sort").addEventListener("change", e => {
  const [sort, order] = e.target.value.split(":");
  state.query.sort = sort; state.query.order = order; loadTalents();
});
$("#btn-export-filtered").addEventListener("click", () => {
  window.location = `/api/export/excel?${querystring({ page: undefined, size: undefined })}`;
  toast("已按当前筛选条件导出 Excel");
});
$("#batch-apply-status").addEventListener("click", () => batchOp("status", $("#batch-status").value));
$("#batch-apply-rating").addEventListener("click", () => batchOp("rating", $("#batch-rating").value));
$("#batch-delete").addEventListener("click", async () => {
  const ok = await confirmBox("批量删除", `确定删除选中的 <b>${state.selected.size}</b> 条记录吗？不可恢复。`, "删除");
  if (ok) batchOp("delete");
});
$("#batch-tag").addEventListener("click", async () => {
  const tag = window.prompt("输入要添加的标签：");
  if (tag && tag.trim()) batchOp("tag_add", tag.trim());
});
$("#batch-clear").addEventListener("click", () => {
  state.selected.clear();
  $$("#talent-tbody .row-check").forEach(c => { c.checked = false; c.closest("tr").classList.remove("selected"); });
  $("#check-all").checked = false;
  updateBatchBar();
});

/* ================= 详情抽屉 ================= */
async function openDrawer(id) {
  let t;
  try { t = await api(`/api/talents/${id}`); } catch (e) { toast(e.message, "err"); return; }
  state.currentTalent = t;
  $("#d-avatar").textContent = (t.name || "?").slice(0, 1);
  $("#d-avatar").style.background = avatarColor(t.name || t.id);
  $("#d-title").textContent = t.name || "未命名";
  $("#d-subtitle").textContent = [t.current_title || t.expected_title, t.current_company, t.city].filter(Boolean).join(" · ") || "字段待完善";
  buildForm($("#d-form"), { ...t, work_history: whToText(t.work_history) });
  $("#d-note-count").textContent = (t.notes || []).length || "";
  $("#d-notes").innerHTML = (t.notes || []).slice().reverse().map(n =>
    `<div class="note-item"><div class="n-time">${esc(n.time)}</div><div class="n-text">${esc(n.text)}</div></div>`).join("")
    || `<p class="muted" style="padding:14px 0">暂无跟进记录</p>`;
  $("#d-raw").textContent = t.raw_text || "（无原文，可能为手动录入）";
  const fileBtn = $("#d-file");
  if (t.stored_path) { fileBtn.hidden = false; fileBtn.href = `/api/files/${t.id}`; fileBtn.textContent = `下载原件：${t.source_file || ""}`; }
  else fileBtn.hidden = true;
  const mask = $("#drawer-mask"), drawer = $("#drawer");
  mask.hidden = drawer.hidden = false;
  requestAnimationFrame(() => { mask.classList.add("show"); drawer.classList.add("show"); });
  switchDtab("info");
}

function closeDrawer() {
  const mask = $("#drawer-mask"), drawer = $("#drawer");
  mask.classList.remove("show"); drawer.classList.remove("show");
  setTimeout(() => { mask.hidden = drawer.hidden = true; }, 240);
  state.currentTalent = null;
}
$("#d-close").addEventListener("click", closeDrawer);
$("#drawer-mask").addEventListener("click", closeDrawer);
document.addEventListener("keydown", e => { if (e.key === "Escape") { closeDrawer(); closeModal(); closeFilePreview(); } });

function switchDtab(name) {
  $$(".dtab").forEach(b => b.classList.toggle("active", b.dataset.dtab === name));
  $$(".dtab-panel").forEach(p => { p.hidden = p.id !== `dtab-${name}`; });
  if (name === "preview") renderDrawerPreview();
}
$$(".dtab").forEach(b => b.addEventListener("click", () => switchDtab(b.dataset.dtab)));

/* 抽屉「简历预览」页签：PDF/图片在线预览，其它格式给下载引导 */
function renderDrawerPreview() {
  const t = state.currentTalent;
  const box = $("#d-preview");
  if (!t || !t.stored_path) {
    box.innerHTML = `<div class="preview-fallback">该记录没有关联简历原件。<br>上传的简历文件会自动保留原件（可在「系统设置」开启），手动录入的记录无原件可预览。</div>`;
    return;
  }
  box.innerHTML = filePreviewHtml(`/api/files/${t.id}?inline=1`, t.source_file || "");
}

/* 通用文件在线预览：PDF → iframe；图片 → img；其它 → 原文/下载引导 */
function filePreviewHtml(url, fname) {
  const ext = (fname.split(".").pop() || "").toLowerCase();
  if (ext === "pdf") return `<iframe src="${esc(url)}" title="简历预览"></iframe>`;
  if (["png", "jpg", "jpeg", "bmp", "webp", "gif"].includes(ext))
    return `<img src="${esc(url)}" alt="简历图片预览">`;
  return `<div class="preview-fallback">「${esc(fname)}」暂不支持窗口内预览（Word/TXT 请看「解析原文」页签）。<br><a class="btn btn-ghost btn-sm" href="${esc(url.replace("?inline=1", ""))}" download>⤓ 下载原件查看</a></div>`;
}

/* 通用预览弹窗（人才表 👁 / 投递审核共用） */
let filePreviewEls = null;
function openFilePreview(url, title) {
  if (!filePreviewEls) {
    const mask = document.createElement("div");
    mask.className = "drawer-mask"; mask.id = "fp-mask";
    const box = document.createElement("div");
    box.className = "modal"; box.id = "fp-box";
    box.style.cssText = "width:min(900px,94vw);height:86vh";
    box.innerHTML = `<div class="modal-head"><h2 id="fp-title"></h2><button id="fp-close" class="icon-btn" aria-label="关闭">✕</button></div>
      <div id="fp-body" class="preview-frame" style="flex:1;min-height:0"></div>`;
    document.body.append(mask, box);
    $("#fp-close").addEventListener("click", closeFilePreview);
    mask.addEventListener("click", closeFilePreview);
    filePreviewEls = { mask, box };
  }
  $("#fp-title").textContent = title || "文件预览";
  $("#fp-body").innerHTML = `<iframe src="${esc(url)}" title="预览"></iframe>`;
  filePreviewEls.mask.hidden = filePreviewEls.box.hidden = false;
  requestAnimationFrame(() => filePreviewEls.mask.classList.add("show"));
}
function closeFilePreview() {
  if (!filePreviewEls) return;
  filePreviewEls.mask.classList.remove("show");
  setTimeout(() => {
    filePreviewEls.mask.hidden = filePreviewEls.box.hidden = true;
    $("#fp-body").innerHTML = "";   // 停止 iframe 加载
  }, 200);
}

$("#d-save").addEventListener("click", async () => {
  const t = state.currentTalent; if (!t) return;
  const data = collectForm($("#d-form"));
  if (!data.name) { toast("姓名不能为空", "err"); return; }
  try {
    const updated = await api(`/api/talents/${t.id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ data }) });
    state.currentTalent = updated;
    toast("已保存");
    closeDrawer();
    if (state.view === "talents") loadTalents(); else loadDashboard();
  } catch (e) { toast(e.message, "err"); }
});
$("#d-delete").addEventListener("click", async () => {
  const t = state.currentTalent; if (!t) return;
  const ok = await confirmBox("删除确认", `确定删除 <b>${esc(t.name)}</b> 吗？`, "删除");
  if (!ok) return;
  try { await api(`/api/talents/${t.id}`, { method: "DELETE" }); toast("已删除"); closeDrawer(); loadTalents(); }
  catch (e) { toast(e.message, "err"); }
});
$("#d-note-add").addEventListener("click", async () => {
  const t = state.currentTalent; if (!t) return;
  const text = $("#d-note-input").value.trim();
  if (!text) { toast("请输入跟进内容", "warn"); return; }
  try {
    const updated = await api(`/api/talents/${t.id}/notes`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
    state.currentTalent = updated;
    $("#d-note-input").value = "";
    $("#d-note-count").textContent = (updated.notes || []).length;
    $("#d-notes").innerHTML = updated.notes.slice().reverse().map(n =>
      `<div class="note-item"><div class="n-time">${esc(n.time)}</div><div class="n-text">${esc(n.text)}</div></div>`).join("");
    toast("跟进记录已添加");
  } catch (e) { toast(e.message, "err"); }
});

/* ================= 手动新增 ================= */
function openModal() {
  buildForm($("#m-form"), { status: STATUSES[0], source: "手动录入" });
  $("#modal-mask").hidden = $("#modal-add").hidden = false;
  requestAnimationFrame(() => $("#modal-mask").classList.add("show"));
  $("#m-form [data-key='name']").focus();
}
function closeModal() {
  const mask = $("#modal-mask"), m = $("#modal-add");
  if (m.hidden) return;
  mask.classList.remove("show");
  setTimeout(() => { mask.hidden = m.hidden = true; }, 200);
}
$("#btn-add-talent").addEventListener("click", openModal);
$("#m-close").addEventListener("click", closeModal);
$("#m-cancel").addEventListener("click", closeModal);
$("#modal-mask").addEventListener("click", closeModal);
$("#m-save").addEventListener("click", async () => {
  const data = collectForm($("#m-form"));
  if (!data.name) { toast("姓名不能为空", "err"); return; }
  try {
    await api("/api/talents", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ data }) });
    toast(`已新增「${data.name}」`); closeModal(); loadTalents();
  } catch (e) { toast(e.message, "err"); }
});

/* ================= 投递审核 ================= */
const appState = { status: "", page: 1, size: 15, items: [] };

async function loadApplications() {
  let r;
  try {
    r = await api(`/api/applications?status=${encodeURIComponent(appState.status)}&page=${appState.page}&size=${appState.size}`);
    const sys = await api("/api/system");
    if (sys.apply_urls && sys.apply_urls.length) {
      $("#apply-portal-url").textContent = `投递页：${sys.apply_urls[sys.apply_urls.length - 1]}`;
      $("#btn-open-portal").href = sys.apply_urls[sys.apply_urls.length - 1];
      $("#btn-open-portal").hidden = false;
    } else {
      $("#apply-portal-url").textContent = "投递门户未启用（可在系统设置开启）";
      $("#btn-open-portal").hidden = true;
    }
    updateAppBadge(r.pending);
  } catch (e) { toast(e.message, "err", "投递列表加载失败"); return; }
  appState.items = r.items;
  $("#app-empty").hidden = r.items.length > 0;
  $("#app-tbody").innerHTML = r.items.map(a => `
    <tr data-id="${a.id}">
      <td><div class="person">
        <span class="avatar" style="background:${avatarColor(a.name || a.id)}">${esc((a.name || "?").slice(0, 1))}</span>
        <span><span class="p-name">${esc(a.name)}</span><br>
        <span class="p-meta">${esc(a.cover_letter ? a.cover_letter.slice(0, 26) : "")}</span></span>
      </div></td>
      <td><div class="contact-line">${esc(a.phone)}${a.email ? `<br><span class="sub">${esc(a.email)}</span>` : ""}</div></td>
      <td>${esc(a.expected_title || "—")}</td>
      <td>${a.referrer ? `<span class="pill st-已邀约">${esc(a.referrer)}</span>` : `<span class="muted">—</span>`}</td>
      <td>${a.file_name
        ? `<button class="btn btn-sm btn-ghost" data-app-preview="${a.id}" title="${esc(a.file_name)}">👁 ${esc(a.file_name.length > 12 ? a.file_name.slice(0, 12) + "…" : a.file_name)}</button>`
        : `<span class="muted">无附件</span>`}</td>
      <td><span class="pill st-${esc(a.status)}">${esc(a.status)}</span>
        ${a.status === "已拒绝" && a.reject_reason ? `<br><span class="muted" style="font-size:11.5px">${esc(a.reject_reason)}</span>` : ""}
        ${a.talent_id ? `<br><a class="link" data-goto-talent="${a.talent_id}" style="font-size:11.5px">已入库 #${a.talent_id}</a>` : ""}</td>
      <td class="muted" style="font-size:12px;font-variant-numeric:tabular-nums">${esc(a.created_at || "")}</td>
      <td><div class="ops">
        ${a.status === "待审核" ? `
          <button class="btn btn-sm btn-primary" data-app-approve="${a.id}">同意入选</button>
          <button class="btn btn-sm btn-danger" data-app-reject="${a.id}">拒绝</button>` : `
          <span class="muted">已处理</span>`}
      </div></td>
    </tr>`).join("");
  renderAppPager(r);
}

function renderAppPager(data) {
  const pages = Math.max(1, Math.ceil(data.total / data.size));
  $("#app-pager").innerHTML = `<span>共 <b>${data.total}</b> 条投递 · 待审核 <b>${data.pending}</b> 条 · 第 ${data.page}/${pages} 页</span>
    <span class="pages">
      <button ${data.page <= 1 ? "disabled" : ""} data-apage="${data.page - 1}">‹</button>
      <button ${data.page >= pages ? "disabled" : ""} data-apage="${data.page + 1}">›</button>
    </span>`;
  $$("#app-pager button[data-apage]").forEach(b => b.addEventListener("click", () => {
    appState.page = Number(b.dataset.apage); loadApplications();
  }));
}

function updateAppBadge(pending) {
  const el = $("#nav-app-badge");
  el.hidden = !pending;
  el.textContent = pending > 99 ? "99+" : pending;
}

$("#app-tbody").addEventListener("click", async e => {
  const pv = e.target.closest("[data-app-preview]");
  if (pv) { openFilePreview(`/api/applications/${pv.dataset.appPreview}/file?inline=1`, "投递简历预览"); return; }
  const gt = e.target.closest("[data-goto-talent]");
  if (gt) { openDrawer(Number(gt.dataset.gotoTalent)); return; }
  const ap = e.target.closest("[data-app-approve]");
  if (ap) {
    const aid = Number(ap.dataset.appApprove);
    const ok = await confirmBox("同意入选", "同意后该投递可一键转入人才库（自动解析其简历建档）。继续？", "同意并入库");
    if (!ok) return;
    try {
      await api(`/api/applications/${aid}/review`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "approve" }) });
      const t = await api(`/api/applications/${aid}/to-talent`, { method: "POST" });
      const warns = (t._apply_warnings || []);
      toast(`已同意并转入人才库（#${t.id} ${t.name}）${warns.length ? "；" + warns.join("；") : ""}`, warns.length ? "warn" : "ok");
      loadApplications();
    } catch (err) { toast(err.message, "err"); }
    return;
  }
  const rj = e.target.closest("[data-app-reject]");
  if (rj) {
    const aid = Number(rj.dataset.appReject);
    const reason = window.prompt("拒绝原因（投递人查询状态时可见，可留空）：", "暂不匹配");
    if (reason === null) return;
    try {
      await api(`/api/applications/${aid}/review`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "reject", reason }) });
      toast("已拒绝该投递");
      loadApplications();
    } catch (err) { toast(err.message, "err"); }
  }
});
$("#a-status").addEventListener("change", e => { appState.status = e.target.value; appState.page = 1; loadApplications(); });
$("#btn-apply-refresh").addEventListener("click", loadApplications);


/* ================= AI 助手（流式对话） ================= */
const chatState = { messages: [], sending: false, sessionTokens: 0 };

async function loadAiView() {
  try {
    const st = await api("/api/llm/status");
    $("#chat-engine").textContent = st.enabled
      ? `当前引擎：${st.mode === "api" ? "外部 API（" + (st.api_model || "") + "）" : "本地模型（" + (st.model_name || "") + "）"}`
      : "⚠ AI 引擎未就绪——请到「系统设置」下载模型或配置外部 API";
  } catch (_) { $("#chat-engine").textContent = ""; }
}

function appendMsg(role, text) {
  const wrap = document.createElement("div");
  wrap.className = `chat-msg ${role}`;
  wrap.innerHTML = `<span class="cm-avatar">${role === "user" ? "我" : "AI"}</span>
    <div class="cm-bubble"></div>`;
  wrap.querySelector(".cm-bubble").textContent = text;
  const log = $("#chat-log");
  const welcome = log.querySelector(".chat-welcome");
  if (welcome) welcome.remove();
  log.appendChild(wrap);
  log.scrollTop = log.scrollHeight;
  return wrap.querySelector(".cm-bubble");
}

async function sendChat() {
  const input = $("#chat-input");
  const text = input.value.trim();
  if (!text || chatState.sending) return;
  chatState.sending = true;
  $("#btn-chat-send").disabled = true;
  input.value = "";
  appendMsg("user", text);
  chatState.messages.push({ role: "user", content: text });
  const bubble = appendMsg("ai", "");
  bubble.innerHTML = `<span class="cursor"></span>`;
  let acc = "";
  try {
    const token = sessionStorage.getItem("th_token");
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify({ messages: [{ role: "system", content: "你是 TalentHub 企业人才库的 AI 助手，帮助 HR 处理招聘相关工作。回答简洁、专业、友好。" }, ...chatState.messages.slice(-16)] }),
    });
    if (resp.status === 401) { sessionStorage.removeItem("th_token"); showLockScreen(); throw new Error("已锁定"); }
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try { detail = (await resp.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop();
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data:")) continue;
        let obj;
        try { obj = JSON.parse(line.slice(5).trim()); } catch (_) { continue; }
        if (obj.delta) { acc += obj.delta; bubble.textContent = acc; $("#chat-log").scrollTop = $("#chat-log").scrollHeight; }
        else if (obj.usage) {
          const u = obj.usage;
          const n = (u.total_tokens || ((u.prompt_tokens || 0) + (u.completion_tokens || 0)));
          chatState.sessionTokens += n;
          $("#chat-usage").textContent = `本次 ${n} tokens${u.estimated ? "（估算）" : ""} · 会话累计 ${chatState.sessionTokens}`;
        }
        else if (obj.error) { bubble.classList.add("err"); bubble.textContent = `⚠ ${obj.error}`; }
      }
    }
    if (acc) chatState.messages.push({ role: "assistant", content: acc });
  } catch (e) {
    bubble.classList.add("err");
    bubble.textContent = `⚠ ${e.message}`;
    chatState.messages.pop();   // 失败的用户消息保留在输入历史外，回退上下文
  } finally {
    chatState.sending = false;
    $("#btn-chat-send").disabled = false;
    $("#chat-input").focus();
  }
}
$("#btn-chat-send").addEventListener("click", sendChat);
$("#chat-input").addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); }
});
$("#btn-chat-clear").addEventListener("click", () => {
  chatState.messages = []; chatState.sessionTokens = 0;
  $("#chat-usage").textContent = "";
  $("#chat-log").innerHTML = `<div class="chat-welcome"><p>👋 对话已清空，随时开始新话题。</p></div>`;
});

/* ================= 导入中心 ================= */
$$(".tab").forEach(tab => tab.addEventListener("click", () => {
  $$(".tab").forEach(t => t.classList.toggle("active", t === tab));
  $("#tab-resume").hidden = tab.dataset.tab !== "resume";
  $("#tab-table").hidden = tab.dataset.tab !== "table";
}));

function bindDropzone(zoneSel, inputSel, onFiles) {
  const zone = $(zoneSel), input = $(inputSel);
  ["dragover", "dragenter"].forEach(ev => zone.addEventListener(ev, e => { e.preventDefault(); zone.classList.add("dragover"); }));
  ["dragleave", "drop"].forEach(ev => zone.addEventListener(ev, e => { e.preventDefault(); zone.classList.remove("dragover"); }));
  zone.addEventListener("drop", e => { const f = [...e.dataTransfer.files]; if (f.length) onFiles(f); });
  zone.addEventListener("click", e => { if (e.target === input || e.target.closest("label")) return; input.click(); });
  input.addEventListener("change", () => { const f = [...input.files]; if (f.length) onFiles(f); input.value = ""; });
}

function setProgress(sel, on, text = "") {
  const el = $(sel); el.hidden = !on;
  if (text) $("span", el).textContent = text;
}

/* ---- 简历导入 ---- */
bindDropzone("#dropzone-resume", "#file-resume", uploadResumes);

async function uploadResumes(files) {
  const forceLlm = $("#force-llm") && $("#force-llm").checked;
  setProgress("#resume-progress", true, forceLlm
    ? `正在 AI 增强解析 ${files.length} 份文件（每份需本地模型推理，请耐心等待）…`
    : `正在解析 ${files.length} 份文件（扫描版走 OCR，请稍候）…`);
  const fd = new FormData();
  files.forEach(f => fd.append("files", f));
  fd.append("force_llm", forceLlm ? "1" : "0");
  let r;
  try { r = await api("/api/import/resumes", { method: "POST", body: fd }); }
  catch (e) { setProgress("#resume-progress", false); toast(e.message, "err", "上传失败"); return; }
  setProgress("#resume-progress", false);

  const okResults = r.results.filter(x => x.ok);
  const failResults = r.results.filter(x => !x.ok);
  failResults.forEach(f => toast(`${f.filename}: ${f.error}`, "err", "解析失败"));
  if (!okResults.length) { if (failResults.length) toast("全部文件解析失败，请检查文件格式", "err"); return; }

  state.drafts = okResults.map(x => ({ ...x.draft, _file: x.filename, _method: x.method, _llm: !!x.llm_used, _conf: x.draft.parse_confidence || 0, _warnings: x.warnings || [] }));
  renderDrafts();
  const llmNote = r.llm_used_count ? `，其中 ${r.llm_used_count} 份经本地 AI 增强` : "";
  toast(`成功解析 ${okResults.length}/${r.total} 份${llmNote}`, "ok", "解析完成");
}

function methodBadge(m, llm) {
  let b;
  if (m === "pdf_ocr" || m === "image_ocr") b = `<span class="method-badge mb-ocr" title="扫描版走内置 OCR 识别">OCR</span>`;
  else if (m === "docx") b = `<span class="method-badge mb-docx" title="Word 文档文本层">Word</span>`;
  else b = `<span class="method-badge mb-text" title="PDF 文字层直读">文本层</span>`;
  if (llm) b += ` <span class="method-badge mb-ai" title="本地大模型增强解析">AI</span>`;
  return b;
}

function renderDrafts() {
  $("#draft-zone").hidden = false;
  $("#draft-count").textContent = `共 ${state.drafts.length} 份待确认`;
  $("#draft-tbody").innerHTML = state.drafts.map((d, i) => `
    <tr data-i="${i}" class="${d._conf < 0.6 ? "low-conf" : ""}">
      <td class="wrap" style="max-width:190px;overflow:hidden;text-overflow:ellipsis" title="${esc(d._file + (d._warnings.length ? "\n⚠ " + d._warnings.join("\n⚠ ") : ""))}">${esc(d._file)}${d._warnings.length ? " ⚠" : ""}</td>
      <td>${methodBadge(d._method, d._llm)}</td>
      <td>${confBar(d._conf)}</td>
      <td><input data-f="name" value="${esc(d.name)}" placeholder="必填" aria-label="姓名"></td>
      <td><input data-f="phone" value="${esc(d.phone)}" aria-label="电话"></td>
      <td><select data-f="education" aria-label="学历"><option value=""></option>${EDU_OPTIONS.map(o => `<option ${d.education === o ? "selected" : ""}>${o}</option>`).join("")}</select></td>
      <td><input data-f="expected_title" value="${esc(d.expected_title || d.current_title)}" aria-label="职位"></td>
      <td><select data-f="rating" aria-label="评级"><option value="">—</option>${RATINGS.map(o => `<option ${d.rating === o ? "selected" : ""}>${o}</option>`).join("")}</select></td>
      <td><select data-f="status" aria-label="状态">${STATUSES.map(o => `<option ${(d.status || STATUSES[0]) === o ? "selected" : ""}>${o}</option>`).join("")}</select></td>
      <td><button class="icon-btn" data-preview="${i}" title="查看解析原文">👁</button></td>
    </tr>`).join("");
  $("#draft-zone").scrollIntoView({ behavior: "smooth", block: "start" });
}

$("#draft-tbody").addEventListener("input", e => {
  const tr = e.target.closest("tr"); if (!tr) return;
  const d = state.drafts[Number(tr.dataset.i)];
  d[e.target.dataset.f] = e.target.value;
});
$("#draft-tbody").addEventListener("click", e => {
  const btn = e.target.closest("[data-preview]");
  if (!btn) return;
  const d = state.drafts[Number(btn.dataset.preview)];
  alert(`【${d._file}】解析原文（前 1500 字）：\n\n${(d.raw_text || "").slice(0, 1500)}`);
});
$("#btn-draft-commit").addEventListener("click", async () => {
  const unnamed = state.drafts.filter(d => !d.name || !d.name.trim());
  if (unnamed.length) { toast(`还有 ${unnamed.length} 行没有姓名，请补齐后再入库`, "err"); return; }
  const strategy = $("#draft-strategy").value;
  const btn = $("#btn-draft-commit");
  btn.disabled = true; btn.textContent = "入库中…";
  try {
    const r = await api("/api/talents/bulk", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ drafts: state.drafts, strategy }) });
    toast(`新增 ${r.inserted} · 合并更新 ${r.updated} · 跳过重复 ${r.skipped}${r.errors.length ? ` · 失败 ${r.errors.length}` : ""}`, "ok", "入库完成");
    state.drafts = [];
    $("#draft-zone").hidden = true;
    location.hash = "#/talents"; loadTalents();
  } catch (err) { toast(err.message, "err"); }
  finally { btn.disabled = false; btn.textContent = "确认入库"; }
});
$("#btn-draft-cancel").addEventListener("click", () => { state.drafts = []; $("#draft-zone").hidden = true; });

/* ---- 表格导入 / 合并 ---- */
bindDropzone("#dropzone-table", "#file-table", uploadTable);

async function uploadTable(files) {
  const file = files[0];
  setProgress("#table-progress", true, `正在解析 ${file.name} …`);
  const fd = new FormData();
  fd.append("up", file);
  let r;
  try { r = await api("/api/import/table/preview", { method: "POST", body: fd }); }
  catch (e) { setProgress("#table-progress", false); toast(e.message, "err", "表格解析失败"); return; }
  setProgress("#table-progress", false);
  state.tablePreview = r;
  $("#table-preview-zone").hidden = false;
  $("#tp-summary").innerHTML = `共 <b>${r.rows.length}</b> 行数据：<b style="color:var(--ok)">${r.new_count} 新记录</b> · <b style="color:var(--accent)">${r.duplicate_count} 疑似重复</b>${r.skipped_empty ? ` · ${r.skipped_empty} 空行已忽略` : ""}`;
  $("#tp-warnings").innerHTML = (r.warnings || []).map(w => `<div class="warn-item">⚠ ${esc(w)}</div>`).join("");
  $("#tp-tbody").innerHTML = r.rows.map((row, i) => {
    const dup = r.duplicates[i] || {};
    return `<tr>
      <td><input type="checkbox" class="tp-check" data-i="${i}" checked aria-label="选择第 ${i + 1} 行"></td>
      <td class="muted">${i + 1}</td>
      <td><b>${esc(row.name || "—")}</b></td>
      <td style="font-variant-numeric:tabular-nums">${esc(row.phone || "—")}</td>
      <td>${esc(row.email || "—")}</td>
      <td>${esc(row.education || "—")}</td>
      <td>${esc(row.current_title || row.expected_title || "—")}</td>
      <td>${skillTags(row.skills, 2)}</td>
      <td>${dup.duplicate_of != null ? `<span class="dup-badge dup-yes" title="${dup.duplicate_of > 0 ? "与库中记录 #" + dup.duplicate_of + " 重复" : "同一文件内的重复行"}">重复（按${esc(dup.matched_by)}）</span>` : `<span class="dup-badge dup-no">新记录</span>`}</td>
    </tr>`;
  }).join("");
  $("#table-preview-zone").scrollIntoView({ behavior: "smooth" });
  toast(`识别到 ${r.rows.length} 行，其中 ${r.duplicate_count} 行与现有数据重复`);
}
$("#tp-check-all").addEventListener("change", e => $$(".tp-check").forEach(c => { c.checked = e.target.checked; }));
$("#btn-table-commit").addEventListener("click", async () => {
  const r = state.tablePreview; if (!r) return;
  const selected = $$(".tp-check").filter(c => c.checked).map(c => Number(c.dataset.i));
  if (!selected.length) { toast("未勾选任何行", "warn"); return; }
  const strategy = ($("input[name='tp-strategy']:checked") || {}).value || "fill";
  const btn = $("#btn-table-commit");
  btn.disabled = true; btn.textContent = "导入中…";
  try {
    const res = await api("/api/import/table/commit", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rows: r.rows, strategy, selected }) });
    toast(`新增 ${res.inserted} · 合并更新 ${res.updated} · 跳过 ${res.skipped}${res.errors.length ? ` · 失败 ${res.errors.length}` : ""}`, "ok", "导入完成");
    state.tablePreview = null;
    $("#table-preview-zone").hidden = true;
  } catch (e) { toast(e.message, "err"); }
  finally { btn.disabled = false; btn.textContent = "执行导入"; }
});
$("#btn-table-cancel").addEventListener("click", () => { state.tablePreview = null; $("#table-preview-zone").hidden = true; });

/* ================= 数据管理 ================= */
async function loadDataView() {
  try {
    const sys = await api("/api/system");
    $("#db-info").innerHTML = `数据库：<code>${esc(sys.db_path)}</code><br>大小：${sys.db_size_kb} KB · 上传目录：<code>${esc(sys.upload_dir)}</code>`;
  } catch (e) { /* 信息面板失败不打断页面 */ }
  try {
    const ev = await api("/api/events?limit=50");
    $("#event-tbody").innerHTML = ev.items.map(x =>
      `<tr><td class="muted" style="font-variant-numeric:tabular-nums">${esc(x.time)}</td>
       <td><span class="pill st-跟进中">${esc(x.action)}</span></td><td class="wrap">${esc(x.detail)}</td></tr>`).join("")
      || `<tr><td colspan="3" class="muted">暂无操作记录</td></tr>`;
  } catch (e) { toast(e.message, "err", "日志加载失败"); }
}
$("#btn-export-all-xlsx").addEventListener("click", () => { window.location = "/api/export/excel"; });
$("#btn-export-all-csv").addEventListener("click", () => { window.location = "/api/export/csv"; });
$("#btn-download-template").addEventListener("click", () => { window.location = "/api/export/template"; });
$("#btn-export-json").addEventListener("click", () => { window.location = "/api/export/json"; });
$("#btn-clear-events").addEventListener("click", async () => {
  const ok = await confirmBox("清空操作日志", "将删除全部操作日志记录（人才数据不受影响）。继续？", "清空");
  if (!ok) return;
  try {
    const r = await api("/api/events", { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) });
    toast(`已删除 ${r.deleted} 条日志`);
    loadDataView();
  } catch (e) { toast(e.message, "err"); }
});
$("#file-json").addEventListener("change", async e => {
  const file = e.target.files[0]; e.target.value = "";
  if (!file) return;
  const strategy = $("#json-strategy").value;
  const fd = new FormData(); fd.append("up", file);
  $("#json-result").textContent = "合并中…";
  try {
    const r = await api(`/api/import/json?strategy=${strategy}`, { method: "POST", body: fd });
    $("#json-result").innerHTML = `✓ 合并完成：共 ${r.total} 条，新增 <b>${r.inserted}</b> · 更新 <b>${r.updated}</b> · 跳过 <b>${r.skipped}</b>${r.errors.length ? ` · 失败 ${r.errors.length}` : ""}`;
    toast("JSON 备份合并完成");
  } catch (err) { $("#json-result").textContent = ""; toast(err.message, "err", "导入失败"); }
});

/* ================= 系统设置 ================= */
async function loadSettings() {
  try {
    const [cfg, sys] = await Promise.all([api("/api/settings"), api("/api/system")]);
    const form = $("#settings-form");
    $$("input[name='host']", form).forEach(r => { r.checked = r.value === cfg.host; });
    form.port.value = cfg.port;
    form.apply_port.value = cfg.apply_port ?? 8301;
    form.apply_enabled.checked = !!cfg.apply_enabled;
    form.apply_captcha_disabled.checked = !!cfg.apply_captcha_disabled;
    form.apply_company_name.value = cfg.apply_company_name || "";
    form.custom_statuses.value = cfg.custom_statuses || "";
    form.auto_open_browser.checked = !!cfg.auto_open_browser;
    form.ocr_enabled.checked = !!cfg.ocr_enabled;
    form.ocr_max_pages.value = cfg.ocr_max_pages;
    form.upload_keep_original.checked = !!cfg.upload_keep_original;
    $("#sysinfo").innerHTML = `
      <div class="si-row"><span class="si-k">版本</span><span class="si-v">TalentHub v${esc(sys.version)}</span></div>
      <div class="si-row"><span class="si-k">运行环境</span><span class="si-v">Python ${esc(sys.python)} · ${esc(sys.os)}</span></div>
      <div class="si-row"><span class="si-k">OCR 引擎</span><span class="si-v">${esc(sys.ocr.engine)}<br>
        ${sys.ocr.installed ? `<span style="color:var(--ok)">● 已安装${sys.ocr.loaded ? "并加载" : "（首次识别时自动加载）"}</span>` : `<span style="color:var(--danger)">● 未安装，扫描版文件将无法识别</span>`}
        ${sys.ocr.load_error ? `<br><span style="color:var(--danger)">${esc(sys.ocr.load_error)}</span>` : ""}</span></div>
      <div class="si-row"><span class="si-k">监听</span><span class="si-v">${esc(sys.host)}:${sys.port} ${sys.open_to_lan ? '<span class="pill st-已录用">端口已开放</span>' : '<span class="pill st-潜在">仅本机</span>'}</span></div>`;
    $("#sys-urls").innerHTML = sys.urls.map((u, i) =>
      `<div class="si-row"><span class="si-k">${i === 0 ? "本机访问" : "局域网访问"}</span><span class="si-v"><a href="${esc(u)}" target="_blank" rel="noopener">${esc(u)}</a></span></div>`).join("")
      + (sys.apply_urls || []).map((u, i) =>
      `<div class="si-row"><span class="si-k">${i === 0 ? "投递页(本机)" : "投递页(局域网)"}</span><span class="si-v"><a href="${esc(u)}" target="_blank" rel="noopener">${esc(u)}</a></span></div>`).join("");
    /* 版权信息 */
    const cr = sys.copyright || {};
    $("#about-box").innerHTML = `<b>${esc(cr.owner || "")}</b> · ${esc(cr.author || "")} 开发<br>
      ${esc(cr.notice || "")}<br>
      联系：📞 ${esc(cr.phone || "")} · ✉️ <a href="mailto:${esc(cr.email || "")}">${esc(cr.email || "")}</a><br>
      商业使用请先书面申请授权。版本 v${esc(sys.version)}`;
    /* 锁屏面板 + 审计 + token 用量 */
    loadLockPanel();
    loadLockAudit();
    loadUsage();
    /* AI 面板 */
    await loadAiPanel(cfg, sys);
  } catch (e) { toast(e.message, "err", "设置加载失败"); }
}

/* ---- 锁屏管理面板 ---- */
async function loadLockPanel() {
  try {
    const st = await api("/api/auth/status");
    $("#lock-badge").innerHTML = st.enabled
      ? `<span class="llm-on">● 已开启保护</span>` : `<span class="llm-off">● 未开启</span>`;
    $("#lock-setup-box").hidden = st.set;
    $("#lock-manage-box").hidden = !st.set;
    $("#btn-lock-top").hidden = !st.enabled;
    if (st.set) {
      $("#lock-auto2").value = st.auto_lock_minutes;
      $("#lock-forgot-hint").textContent = "💡 " + (st.forgot_hint || "");
    }
    $("#lock-forgot").textContent = "💡 " + (st.forgot_hint || "");
  } catch (_) { /* 状态查询失败不打断设置页 */ }
}

$("#btn-lock-setup").addEventListener("click", async () => {
  const p1 = $("#lock-p1").value, p2 = $("#lock-p2").value;
  if (p1.length < 6) { toast("密码至少 6 位", "err"); return; }
  if (p1 !== p2) { toast("两次输入的密码不一致", "err"); return; }
  try {
    await api("/api/auth/setup", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: p1, auto_lock_minutes: Number($("#lock-auto").value) || 30 }) });
    toast("锁屏已启用，数据已受密码保护");
    $("#lock-p1").value = $("#lock-p2").value = "";
    loadLockPanel();
  } catch (e) { toast(e.message, "err"); }
});
$("#btn-lock-change").addEventListener("click", async () => {
  const oldp = $("#lock-old").value, newp = $("#lock-new").value;
  if (!oldp) { toast("请输入原密码", "err"); return; }
  if (newp && newp.length < 6) { toast("新密码至少 6 位", "err"); return; }
  try {
    await api("/api/auth/change", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ old_password: oldp, new_password: newp || oldp, auto_lock_minutes: Number($("#lock-auto2").value) }) });
    toast("锁屏设置已更新");
    $("#lock-old").value = $("#lock-new").value = "";
    loadLockPanel();
  } catch (e) { toast(e.message, "err"); }
});
$("#btn-lock-disable").addEventListener("click", async () => {
  const p = $("#lock-old").value || window.prompt("请输入当前锁屏密码以关闭：");
  if (!p) return;
  try {
    await api("/api/auth/disable", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: p }) });
    toast("锁屏已关闭");
    $("#lock-old").value = "";
    loadLockPanel();
  } catch (e) { toast(e.message, "err"); }
});

/* ---- 锁屏覆盖层 + 自动锁定 ---- */
function startFreezeCountdown(sec) {
  const errEl = $("#lock-err"), input = $("#lock-pass"), btn = $("#lock-unlock");
  let left = sec;
  input.disabled = btn.disabled = true;
  if (freezeTimer) clearInterval(freezeTimer);
  const tick = () => {
    errEl.textContent = `⚠ 密码错误次数过多，已冻结保护。请 ${left} 秒后再试（本次尝试已记录来源地址）`;
    if (left <= 0) {
      clearInterval(freezeTimer); freezeTimer = null;
      input.disabled = btn.disabled = false;
      errEl.textContent = ""; input.value = "";
    }
    left--;
  };
  tick();
  freezeTimer = setInterval(tick, 1000);
}

function showLockScreen() {
  $("#lock-screen").hidden = false;
  $("#lock-err").textContent = "";
  $("#lock-pass").value = "";
  setTimeout(() => $("#lock-pass").focus(), 60);
}
let freezeTimer = null;
async function doUnlock() {
  const p = $("#lock-pass").value;
  if (!p) { $("#lock-err").textContent = "请输入密码"; return; }
  try {
    const r = await fetch("/api/auth/unlock", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: p }) });
    const body = await r.json();
    if (r.status === 429) {   // 防破解冻结：倒计时提示，期间禁用输入
      startFreezeCountdown(body.retry_after || 60);
      return;
    }
    if (!r.ok) { $("#lock-err").textContent = body.detail || "解锁失败"; return; }
    sessionStorage.setItem("th_token", body.token);
    $("#lock-screen").hidden = true;
    startAutoLock(body.auto_lock_minutes);
    toast("已解锁");
    route();   // 重新拉取当前视图数据
  } catch (e) { $("#lock-err").textContent = e.message; }
}
$("#lock-unlock").addEventListener("click", doUnlock);
$("#lock-pass").addEventListener("keydown", e => { if (e.key === "Enter") doUnlock(); });
$("#btn-lock-top").addEventListener("click", () => {
  sessionStorage.removeItem("th_token");
  showLockScreen();
});

let autoLockTimer = null;
function startAutoLock(minutes) {
  if (autoLockTimer) { clearTimeout(autoLockTimer); autoLockTimer = null; }
  if (!minutes || minutes <= 0) return;
  const reset = () => {
    if (autoLockTimer) clearTimeout(autoLockTimer);
    autoLockTimer = setTimeout(() => {
      if (!$("#lock-screen").hidden) return;
      sessionStorage.removeItem("th_token");
      showLockScreen();
      toast(`${minutes} 分钟无操作，屏幕已自动锁定`, "warn");
    }, minutes * 60_000);
  };
  ["click", "keydown", "pointerdown", "scroll"].forEach(ev => document.addEventListener(ev, reset, { passive: true }));
  reset();
}

/* ---- AI 面板（本地模型 / 外部 API 双引擎） ---- */
let aiCfgCache = null;
async function loadAiPanel(cfg, sys) {
  aiCfgCache = cfg;
  const llmForm = $("#llm-form");
  const mode = cfg.llm_engine === "api" ? "api" : "local";
  switchEngineUi(mode);
  llmForm.llm_enabled.checked = !!cfg.llm_enabled;
  llmForm.llm_enabled_api.checked = !!cfg.llm_enabled;
  llmForm.llm_min_confidence.value = cfg.llm_min_confidence ?? 0.75;
  llmForm.llm_gpu_layers.value = cfg.llm_gpu_layers ?? 0;
  llmForm.llm_api_base.value = cfg.llm_api_base || "";
  llmForm.llm_api_key.value = cfg.llm_api_key || "";
  llmForm.llm_api_model.value = cfg.llm_api_model || "";
  const L = sys.llm || {};
  $("#llm-badge").innerHTML = L.enabled
    ? `<span class="llm-on">● AI 就绪（${L.mode === "api" ? "外部API" : "本地模型"}）</span>`
    : `<span class="llm-off">● AI 未启用</span>`;
  if (L.mode === "api") {
    $("#llm-status").innerHTML = `引擎：${esc(L.engine || "-")}<br>状态：${L.enabled ? `<span style="color:var(--ok)">可用</span>` : `<span style="color:var(--danger)">配置不完整</span>`}`;
  } else {
    let why = "";
    if (!L.installed) why = "llama-cpp-python 未安装（见 README「AI 智能解析」）";
    else if (!L.model_found) why = "未找到模型文件——在下方选择模型下载，或手动放入模型目录";
    $("#llm-status").innerHTML = `
      引擎：${esc(L.engine || "-")}<br>
      状态：${L.enabled ? `<span style="color:var(--ok)">可用</span>` : `<span style="color:var(--muted)">不可用</span>`}${why ? `（${esc(why)}）` : ""}<br>
      模型：${esc(L.model_name || "（未安装）")}${L.load_error ? `<br><span style="color:var(--danger)">${esc(L.load_error)}</span>` : ""}`;
  }
  /* 内存/GPU 建议 */
  try {
    const res = await api("/api/system/resources");
    const ramGb = (res.ram.total_mb / 1024).toFixed(1);
    const availGb = (res.ram.available_mb / 1024).toFixed(1);
    const gpuHtml = res.gpu.available
      ? `<span class="gpu-badge">GPU ${esc(res.gpu.name)} · ${Math.round(res.gpu.vram_mb / 1024)}GB 显存</span>`
      : "";
    $("#ram-hint").innerHTML = `📊 本机内存 <b>${ramGb}GB</b>（当前可用 <b>${availGb}GB</b>）${gpuHtml}<br>💡 ${esc(res.recommend.reason)}${res.recommend.model_id ? "（下方下载已默认为你选好推荐档）" : ""}<br>
      <span style="opacity:.8">${esc(res.gpu.hint || "")}</span>`;
    if (res.recommend.model_id) {
      const sel = $("#dl-model");
      if (sel && [...sel.options].some(o => o.value === res.recommend.model_id && !o.disabled)) sel.value = res.recommend.model_id;
      fillSourceSelect();
    }
  } catch (_) { $("#ram-hint").textContent = ""; }
  loadModelPanel();
}

function switchEngineUi(mode) {
  $("#engine-local").classList.toggle("on", mode === "local");
  $("#engine-api").classList.toggle("on", mode === "api");
  $("#llm-local-box").hidden = mode !== "local";
  $("#llm-api-box").hidden = mode !== "api";
  $("#dl-model").closest(".card").style.opacity = mode === "local" ? "1" : ".45";
}
let curEngine = "local";
$("#engine-local").addEventListener("click", () => { curEngine = "local"; switchEngineUi("local"); });
$("#engine-api").addEventListener("click", () => { curEngine = "api"; switchEngineUi("api"); });

$("#btn-save-llm").addEventListener("click", async () => {
  const f = $("#llm-form");
  const mode = $("#engine-api").classList.contains("on") ? "api" : "local";
  const body = {
    llm_engine: mode,
    llm_enabled: mode === "api" ? f.llm_enabled_api.checked : f.llm_enabled.checked,
    llm_min_confidence: Number(f.llm_min_confidence.value),
  };
  if (mode === "api") {
    body.llm_api_base = f.llm_api_base.value.trim();
    body.llm_api_model = f.llm_api_model.value.trim();
    if (f.llm_api_key.value && f.llm_api_key.value !== "******") body.llm_api_key = f.llm_api_key.value.trim();
    if (!body.llm_api_base || !body.llm_api_model) { toast("请填写 API 地址与模型名", "err"); return; }
  } else {
    body.llm_gpu_layers = Number(f.llm_gpu_layers.value || 0);
  }
  try {
    await api("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    toast("AI 设置已保存"); loadSettings();
  } catch (e) { toast(e.message, "err", "保存失败"); }
});

$("#btn-test-llm").addEventListener("click", async () => {
  const btn = $("#btn-test-llm");
  btn.disabled = true; btn.textContent = "解析中…（本地模型约需 30 秒）";
  try {
    const fd = new FormData();
    const resp = await fetch("samples/张伟-Java开发工程师-8年.pdf");
    if (!resp.ok) throw new Error("样本文件获取失败");
    fd.append("files", await resp.blob(), "张伟-Java开发工程师-8年.pdf");
    fd.append("force_llm", "1");
    const r = await api("/api/import/resumes", { method: "POST", body: fd });
    const it = (r.results || [])[0] || {};
    if (!it.ok) throw new Error(it.error || "解析失败");
    const d = it.draft;
    toast(`姓名=${d.name} 电话=${d.phone} 学校=${d.school} 专业=${d.major}（AI增强=${it.llm_used ? "是" : "否"}，置信度 ${Math.round(d.parse_confidence * 100)}%）`, "ok", "AI 解析测试成功");
  } catch (e) { toast(e.message, "err", "AI 解析测试失败"); }
  finally { btn.disabled = false; btn.textContent = "测试解析一份样本"; }
});

/* ---- AI 模型下载面板 ---- */
let llmModels = [];
async function loadModelPanel() {
  try {
    const r = await api("/api/llm/models");
    llmModels = r.models;
    $("#llm-dir").textContent = r.models_dir;
    const sel = $("#dl-model");
    sel.innerHTML = llmModels.map(m =>
      `<option value="${esc(m.id)}" ${m.downloaded ? "disabled" : ""}>${esc(m.label)} · ~${m.size_mb}MB${m.downloaded ? "（已下载 ✓）" : ""}</option>`).join("");
    const firstAvail = llmModels.find(m => !m.downloaded);
    if (firstAvail) sel.value = firstAvail.id;
    fillSourceSelect();
    if (r.download && (r.download.active || r.download.error)) renderDlProgress(r.download);
  } catch (e) { toast(e.message, "err", "模型列表加载失败"); }
}
function fillSourceSelect() {
  const m = llmModels.find(x => x.id === $("#dl-model").value);
  $("#dl-source").innerHTML = m ? Object.keys(m.urls).map(s => `<option>${esc(s)}</option>`).join("") : "";
}
$("#dl-model").addEventListener("change", fillSourceSelect);

let dlTimer = null;
function renderDlProgress(p) {
  const box = $("#dl-progress");
  if (!p || (!p.active && !p.error && !p.done_file)) { box.hidden = true; return; }
  box.hidden = false;
  if (p.error) {
    $("#dl-bar-fill").style.width = "100%";
    $("#dl-bar-fill").style.background = "var(--danger)";
    $("#dl-text").innerHTML = `<span style="color:var(--danger)">下载失败：${esc(p.error)}</span>`;
    stopDlPoll();
    return;
  }
  $("#dl-bar-fill").style.background = "";
  $("#dl-bar-fill").style.width = `${p.percent || 0}%`;
  $("#dl-text").textContent = p.active
    ? `${p.file} — ${p.percent}%（${p.downloaded_mb}/${p.total_mb} MB，${p.speed_mbps} MB/s）`
    : `✓ 下载完成：${p.done_file}`;
  if (!p.active && p.done_file) { stopDlPoll(); loadModelPanel(); loadSettings(); }
}
function stopDlPoll() { if (dlTimer) { clearInterval(dlTimer); dlTimer = null; } }

$("#btn-dl-model").addEventListener("click", async () => {
  const model_id = $("#dl-model").value, source = $("#dl-source").value;
  if (!model_id || !source) { toast("请选择模型与源站", "warn"); return; }
  const btn = $("#btn-dl-model");
  btn.disabled = true;
  try {
    await api("/api/llm/download", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model_id, source }) });
    toast("开始下载，大文件需数分钟，可离开本页稍后回来看进度");
    $("#dl-progress").hidden = false;
    stopDlPoll();
    dlTimer = setInterval(async () => {
      try { renderDlProgress(await api("/api/llm/download")); }
      catch (_) { stopDlPoll(); }
    }, 1500);
  } catch (e) { toast(e.message, "err", "下载启动失败"); }
  finally { btn.disabled = false; }
});

/* ---- 自定义链接下载（多线程分段由后端自动启用） ---- */
$("#btn-dl-url").addEventListener("click", async () => {
  const url = $("#dl-custom-url").value.trim();
  if (!/^https?:\/\//.test(url)) { toast("请粘贴 http(s) 的 GGUF 直链", "warn"); return; }
  const btn = $("#btn-dl-url");
  btn.disabled = true;
  try {
    await api("/api/llm/download-url", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url }) });
    toast("开始下载（支持分段时自动 4 线程加速），大文件需数分钟");
    $("#dl-progress").hidden = false;
    stopDlPoll();
    dlTimer = setInterval(async () => {
      try { renderDlProgress(await api("/api/llm/download")); }
      catch (_) { stopDlPoll(); }
    }, 1500);
  } catch (e) { toast(e.message, "err", "链接下载失败"); }
  finally { btn.disabled = false; }
});

/* ---- 导入本地模型文件（流式上传，大文件耐心等） ---- */
$("#file-model").addEventListener("change", async e => {
  const f = e.target.files[0]; e.target.value = "";
  if (!f) return;
  if (!f.name.toLowerCase().endsWith(".gguf")) { toast("请选择 .gguf 模型文件", "err"); return; }
  const ok = await confirmBox("导入模型", `将导入 <b>${esc(f.name)}</b>（${(f.size / 1073741824).toFixed(2)}GB）到模型目录，大文件上传需要一些时间。继续？`, "开始导入");
  if (!ok) return;
  const fd = new FormData();
  fd.append("up", f);
  toast("上传中…请勿关闭页面", "warn");
  try {
    const token = sessionStorage.getItem("th_token");
    const resp = await fetch("/api/llm/import", { method: "POST", body: fd, headers: token ? { Authorization: `Bearer ${token}` } : {} });
    const r = await resp.json();
    if (!resp.ok) throw new Error(r.detail || `HTTP ${resp.status}`);
    toast(`模型已导入：${r.file}（${r.size_mb}MB），下次解析自动可用`);
    loadModelPanel();
  } catch (err) { toast(err.message, "err", "导入失败"); }
});

/* ---- 外部 API：自动拉取模型列表 ---- */
$("#btn-fetch-models").addEventListener("click", async () => {
  const btn = $("#btn-fetch-models");
  const hint = $("#api-models-hint");
  btn.disabled = true; hint.textContent = "拉取中…";
  try {
    const base = $("#api-base").value.trim();
    const key = $("#api-key").value.trim();
    if (!base) throw new Error("请先填写 API 地址");
    const qs = new URLSearchParams({ base, use_saved: key && key !== "******" ? "0" : "1" });
    if (key && key !== "******") qs.set("key", key);
    const r = await api(`/api/llm/api-models?${qs}`);
    const dl = $("#api-model-list");
    dl.innerHTML = r.models.map(m => `<option value="${esc(m)}">`).join("");
    hint.innerHTML = `✓ 获取到 <b>${r.models.length}</b> 个模型：${r.models.slice(0, 8).map(esc).join("、")}${r.models.length > 8 ? " …" : ""}（在「模型名」输入框聚焦可见下拉建议，也可直接手填）`;
    if (r.models.length && !$("#api-model").value) $("#api-model").value = r.models[0];
  } catch (e) { hint.textContent = ""; toast(e.message, "err", "拉取模型列表失败"); }
  finally { btn.disabled = false; }
});

/* ---- token 用量统计 ---- */
async function loadUsage() {
  try {
    const r = await api("/api/llm/usage");
    const t = r.total;
    $("#usage-box").innerHTML = t.calls === 0 ? "暂无 AI 调用记录。简历 AI 增强与 AI 助手对话都会在这里累计 token 用量。" : `
      累计调用 <b>${t.calls}</b> 次 · prompt <b>${(t.prompt_tokens / 1000).toFixed(1)}k</b> + completion <b>${(t.completion_tokens / 1000).toFixed(1)}k</b> = <b>${(t.total_tokens / 1000).toFixed(1)}k tokens</b> · 平均耗时 ${(t.avg_elapsed_ms / 1000).toFixed(1)}s<br>
      按引擎：${r.by_engine.map(x => `${esc(x.engine)} ${x.calls}次/${(x.tokens / 1000).toFixed(1)}k`).join(" · ") || "—"}<br>
      按场景：${r.by_scene.map(x => `${x.scene === "chat" ? "对话" : "简历解析"} ${x.calls}次/${(x.tokens / 1000).toFixed(1)}k`).join(" · ") || "—"}`;
    $("#usage-detail").innerHTML = r.recent.length ? `<table class="data-table"><thead>
      <tr><th>时间</th><th>引擎</th><th>模型</th><th>场景</th><th class="num">tokens</th><th class="num">耗时</th></tr></thead><tbody>
      ${r.recent.map(x => `<tr><td class="muted" style="font-size:11.5px">${esc(x.time)}</td>
        <td>${esc(x.engine)}</td><td class="muted" style="font-size:11.5px;max-width:180px;overflow:hidden;text-overflow:ellipsis">${esc(x.model)}</td>
        <td>${x.scene === "chat" ? "对话" : "简历解析"}</td>
        <td class="num">${(x.total_tokens / 1000).toFixed(1)}k</td><td class="num">${(x.elapsed_ms / 1000).toFixed(1)}s</td></tr>`).join("")}
      </tbody></table>` : "";
  } catch (_) { $("#usage-box").textContent = "用量加载失败"; }
}
$("#btn-usage-refresh").addEventListener("click", loadUsage);

/* ---- 锁屏审计（失败警告 + IP） ---- */
async function loadLockAudit() {
  try {
    const r = await api("/api/auth/audit");
    const zone = $("#lock-audit-zone");
    if (!r.items.length) { zone.hidden = true; return; }
    zone.hidden = false;
    $("#lock-audit").innerHTML = r.items.slice(0, 20).map(x =>
      `${x.ok ? "✅" : "⚠️"} <code>${esc(x.time)}</code> ${esc(x.ip || "本机")} ${esc(x.detail || "")}`).join("<br>");
  } catch (_) { /* 未启用锁时 401/404 静默 */ }
}

$("#btn-save-settings").addEventListener("click", saveSettings);
$("#btn-restart").addEventListener("click", async () => {
  await saveSettings(true);
});
async function saveSettings(silent = false) {
  const form = $("#settings-form");
  const host = ($("input[name='host']:checked") || {}).value || "127.0.0.1";
  const body = {
    host,
    port: Number(form.port.value),
    apply_port: Number(form.apply_port.value),
    apply_enabled: form.apply_enabled.checked,
    apply_captcha_disabled: form.apply_captcha_disabled.checked,
    apply_company_name: form.apply_company_name.value.trim(),
    custom_statuses: form.custom_statuses.value.trim(),
    auto_open_browser: form.auto_open_browser.checked,
    ocr_enabled: form.ocr_enabled.checked,
    ocr_max_pages: Number(form.ocr_max_pages.value),
    upload_keep_original: form.upload_keep_original.checked,
  };
  try {
    const r = await api("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    /* 自定义状态生效：刷新全局 STATUSES 与所有下拉 */
    if ("custom_statuses" in body) await refreshStatuses();
    const needRestart = (r && r.need_restart || []).length > 0;
    if (!silent) toast(needRestart ? "设置已保存，端口/投递门户变更需点「保存并重启服务」生效" : "设置已保存");
    return true;
  } catch (e) { toast(e.message, "err", "保存失败"); return false; }
}
$("#btn-restart").onclick = async () => {
  const ok = await confirmBox("重启服务", "将按新配置重启服务进程，期间页面会短暂断开约 3 秒。继续？", "重启");
  if (!ok) return;
  if (!(await saveSettings(true))) return;
  try { await api("/api/settings/restart", { method: "POST" }); } catch (_) { /* 重启会掐断连接，属预期 */ }
  toast("服务重启中，页面将自动恢复…", "warn");
  let tries = 0;
  const timer = setInterval(async () => {
    tries++;
    try { await api("/api/health"); clearInterval(timer); location.reload(); }
    catch (_) { if (tries > 40) { clearInterval(timer); toast("服务未恢复，请手动检查终端输出", "err"); } }
  }, 700);
};

/* ================= 顶栏 / 全局 ================= */
$("#global-search").addEventListener("input", debounce(e => {
  state.query.q = e.target.value.trim();
  state.query.page = 1;
  if (state.view !== "talents") location.hash = "#/talents";
  else loadTalents();
}, 380));
$("#global-search").addEventListener("keydown", e => {
  if (e.key === "Enter") {
    state.query.q = e.target.value.trim(); state.query.page = 1;
    location.hash = "#/talents"; loadTalents();
  }
});
$("#btn-upload-top").addEventListener("click", () => {
  location.hash = "#/import";
  setTimeout(() => $("#file-resume").click(), 120);
});
const batchUpBtn = $("#btn-batch-upload");
if (batchUpBtn) {
  batchUpBtn.addEventListener("click", () => {
    location.hash = "#/import";
    setTimeout(() => $("#file-resume").click(), 120);
  });
}

/* 动态状态列表：自定义状态在设置页保存后全局生效 */
async function refreshStatuses() {
  try {
    const r = await api("/api/statuses");
    if (Array.isArray(r.statuses) && r.statuses.length) STATUSES = r.statuses;
  } catch (_) { /* 保持默认 */ }
  const opts = STATUSES.map(s => `<option>${esc(s)}</option>`).join("");
  const f = $("#f-status");
  const cur = f.value;
  f.innerHTML = `<option value="">全部状态</option>` + opts;
  f.value = cur;
  $("#batch-status").innerHTML = opts;
}

async function refreshSkillOptions() {
  try {
    const st = await api("/api/stats");
    const skills = (st.top_skills || []).map(x => x.name);
    const sel = $("#f-skill");
    const cur = sel.value;
    sel.innerHTML = `<option value="">全部技能</option>` + skills.map(k => `<option>${esc(k)}</option>`).join("");
    sel.value = skills.includes(cur) ? cur : "";
  } catch (_) { /* 锁定或空库时静默 */ }
}

async function initShell() {
  /* 下拉初始化（状态列表动态拉取，支持自定义状态） */
  await refreshStatuses();
  $("#f-edu").insertAdjacentHTML("beforeend", EDU_OPTIONS.map(s => `<option>${s}</option>`).join(""));
  refreshSkillOptions();
  /* 版权小眼睛：显隐状态存 localStorage */
  const crEl = $("#sidebar-copyright"), eyeBtn = $("#btn-cr-eye");
  if (localStorage.getItem("th_cr_hidden") === "1") { crEl.classList.add("cr-hidden"); eyeBtn.textContent = "👁‍🗨"; }
  eyeBtn.addEventListener("click", () => {
    const hidden = crEl.classList.toggle("cr-hidden");
    eyeBtn.textContent = hidden ? "👁‍🗨" : "👁";
    eyeBtn.title = hidden ? "显示版权信息" : "隐藏版权信息";
    localStorage.setItem("th_cr_hidden", hidden ? "1" : "0");
  });
  try {
    const [sys, about] = await Promise.all([api("/api/system"), api("/api/about")]);
    $("#server-text").textContent = `服务运行中 v${sys.version}`;
    $("#server-dot .dot").classList.remove("off");
    $("#sidebar-port").textContent = sys.open_to_lan ? `端口 ${sys.port} · 局域网开放` : `端口 ${sys.port} · 仅本机`;
    const cr = about.copyright || {};
    $("#sidebar-copyright").innerHTML = `© ${esc(cr.owner || "")}<br>${esc(cr.author || "")} 开发 · 商用需书面授权<br>
      <a href="mailto:${esc(cr.email || "")}">${esc(cr.email || "")}</a> · ${esc(cr.phone || "")}`;
  } catch (e) {
    $("#server-text").textContent = "无法连接服务";
    $("#server-dot .dot").classList.add("off");
  }
  /* 锁屏：已启用且本地无有效令牌 → 先锁；有令牌 → 启动自动锁定计时 */
  try {
    const st = await api("/api/auth/status");
    $("#btn-lock-top").hidden = !st.enabled;
    $("#lock-forgot").textContent = "💡 " + (st.forgot_hint || "");
    if (st.enabled) {
      const token = sessionStorage.getItem("th_token");
      if (!token) { showLockScreen(); return; }
      startAutoLock(st.auto_lock_minutes);
    }
  } catch (_) { /* 锁状态查询失败不阻塞页面 */ }
  route();
  /* 待审核投递徽标（首屏 + 每 30s 刷新） */
  const refreshBadge = async () => {
    try { const r = await api("/api/applications?size=1"); updateAppBadge(r.pending); }
    catch (_) { /* 锁定或未启用时静默 */ }
  };
  refreshBadge();
  setInterval(refreshBadge, 30_000);
}
document.addEventListener("DOMContentLoaded", initShell);
if (document.readyState !== "loading") initShell();
