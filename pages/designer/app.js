// DayFlow 工作台 WebUI 前端逻辑
// 对接 core/page_api.py 的路由
// 5 个 tab：日程 / 设计 / 优秀库 / 提示词 / 概览
//
// 重要：Bridge SDK 会自动解包响应
// - 成功时：bridge.apiGet/apiPost 返回的就是后端 _ok(data) 中的 data 字段内容
// - 失败时：bridge 抛出 Error(message)，不会返回 {status, error}
// 因此前端直接使用返回值，错误用 try/catch 处理

// ── API Client ────────────────────────────────────────────────
class ApiClient {
  constructor() {
    this.bridge = window.AstrBotPluginPage;
  }

  async ready() {
    if (!this.bridge) {
      throw new Error("Bridge 不可用：window.AstrBotPluginPage 未注入");
    }
    if (this.bridge.ready) {
      try {
        await this.bridge.ready();
      } catch (e) {
        console.warn("Bridge ready 警告:", e);
      }
    }
  }

  async get(endpoint, params = {}) {
    if (!this.bridge || !this.bridge.apiGet) {
      throw new Error("Bridge apiGet 不可用");
    }
    const path = endpoint.startsWith("page/") ? endpoint : `page/${endpoint}`;
    return await this.bridge.apiGet(path, params);
  }

  async post(endpoint, body = {}) {
    if (!this.bridge || !this.bridge.apiPost) {
      throw new Error("Bridge apiPost 不可用");
    }
    const path = endpoint.startsWith("page/") ? endpoint : `page/${endpoint}`;
    return await this.bridge.apiPost(path, body);
  }
}

const api = new ApiClient();

// ── 全局状态 ──────────────────────────────────────────────────
const state = {
  currentPage: "schedule",
  theme: "light",
  // 日程 tab
  schedulePersonas: [],
  schedulePersonasLoaded: false,
  scheduleSelectedPersona: "",
  scheduleSelectedDate: "",
  scheduleCurrentData: null,
  scheduleHistory: [],
  scheduleEditMode: false,
  scheduleEditData: null,
  scheduleLoading: false,
  scheduleGenPolling: false,
  scheduleGenPollTimer: null,
  scheduleTomorrowReq: "",
  // 设计 tab
  styles: [],                // /styles 返回的列表
  stylesLoaded: false,
  selectedStyleName: "",     // 当前选中的风格名
  styleSearchText: "",       // 搜索过滤文本
  designSession: null,       // { styleName, userInput, history: [{role, name, description, userFeedback?}] }
  designLoading: false,
  reviewLoading: false,
  // 优秀库 tab
  libraryData: null,         // /outfits 返回的全部数据
  libraryFilter: "",         // 选中的风格筛选；空字符串表示全部
  libraryLoading: false,
  // 提示词 tab
  promptsData: null,
  promptsLoading: false,
  // 概览 tab
  overviewData: null,
  overviewLoading: false,
};

// 主题初始化（sandbox 中 localStorage 可能不可用，需 try/catch 保护）
try {
  state.theme = localStorage.getItem("dayflow-designer-theme") || "light";
} catch (e) {
  state.theme = "light";
}

// ── 工具函数 ──────────────────────────────────────────────────
function $(id) {
  return document.getElementById(id);
}

function esc(text) {
  if (text == null) return "";
  const div = document.createElement("div");
  div.textContent = String(text);
  return div.innerHTML;
}

function formatDateTime(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  } catch {
    return iso;
  }
}

function toast(message, type = "info") {
  const container = $("toast-container");
  if (!container) return;
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = String(message || "");
  container.appendChild(el);
  const duration = type === "error" ? 5000 : 3000;
  setTimeout(() => {
    el.classList.add("hiding");
    setTimeout(() => el.remove(), 250);
  }, duration);
}

function showLoading(target, text = "加载中...") {
  if (typeof target === "string") target = $(target);
  if (!target) return;
  target.innerHTML = `<div class="loading"><span class="spinner"></span>${esc(text)}</div>`;
}

function showEmpty(target, text = "暂无数据") {
  if (typeof target === "string") target = $(target);
  if (!target) return;
  target.innerHTML = `<div class="empty-state"><div>∅</div><div>${esc(text)}</div></div>`;
}

// ── Modal 对话框 ──────────────────────────────────────────────
function showModal({ title, bodyHtml, footerHtml, onMount, onClose }) {
  const container = $("modal-container");
  if (!container) return null;
  container.innerHTML = `
    <div class="modal-backdrop">
      <div class="modal-dialog">
        <div class="modal-header">
          <div class="modal-title">${esc(title || "")}</div>
          <button class="btn btn-ghost btn-sm" data-modal-close>×</button>
        </div>
        <div class="modal-body">${bodyHtml || ""}</div>
        ${footerHtml ? `<div class="modal-footer">${footerHtml}</div>` : ""}
      </div>
    </div>
  `;
  const backdrop = container.querySelector(".modal-backdrop");
  container.classList.add("active");

  const close = () => {
    container.innerHTML = "";
    container.classList.remove("active");
    if (typeof onClose === "function") onClose();
  };
  backdrop.addEventListener("click", (e) => {
    if (e.target === backdrop || e.target.matches("[data-modal-close]")) {
      close();
    }
  });
  const escHandler = (e) => {
    if (e.key === "Escape") {
      close();
      document.removeEventListener("keydown", escHandler);
    }
  };
  document.addEventListener("keydown", escHandler);
  if (typeof onMount === "function") {
    onMount(container, close);
  }
  return { close, container };
}

function confirmModal({ title, message, confirmText = "确认", cancelText = "取消", danger = false, onConfirm }) {
  const footer = `
    <button class="btn btn-secondary" data-modal-cancel>${esc(cancelText)}</button>
    <button class="btn ${danger ? "btn-danger" : "btn-primary"}" data-modal-confirm>${esc(confirmText)}</button>
  `;
  const body = `<div style="line-height:1.6;color:var(--text-primary)">${esc(message)}</div>`;
  showModal({
    title,
    bodyHtml: body,
    footerHtml: footer,
    onMount: (container, close) => {
      container.querySelector("[data-modal-cancel]").addEventListener("click", close);
      container.querySelector("[data-modal-confirm]").addEventListener("click", async () => {
        const btn = container.querySelector("[data-modal-confirm]");
        btn.disabled = true;
        btn.textContent = "处理中...";
        try {
          await onConfirm(close);
        } catch (e) {
          toast(`操作失败: ${e.message}`, "error");
          btn.disabled = false;
          btn.textContent = confirmText;
        }
      });
    },
  });
}

// ── 主题切换 ──────────────────────────────────────────────────
function applyTheme(theme) {
  state.theme = theme;
  document.documentElement.setAttribute("data-theme", theme);
  try {
    localStorage.setItem("dayflow-designer-theme", theme);
  } catch (e) {}
  const iconDark = $("theme-icon-dark");
  const iconLight = $("theme-icon-light");
  if (iconDark && iconLight) {
    if (theme === "dark") {
      iconDark.classList.add("hidden");
      iconLight.classList.remove("hidden");
    } else {
      iconDark.classList.remove("hidden");
      iconLight.classList.add("hidden");
    }
  }
}

function toggleTheme() {
  applyTheme(state.theme === "dark" ? "light" : "dark");
}

// ── 页面切换 ──────────────────────────────────────────────────
function switchPage(pageName) {
  state.currentPage = pageName;
  document.querySelectorAll(".nav-item[data-page]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.page === pageName);
  });
  document.querySelectorAll(".page").forEach((page) => {
    page.classList.toggle("active", page.id === `page-${pageName}`);
  });
  if (pageName === "schedule" && !state.schedulePersonasLoaded) loadSchedulePersonas();
  if (pageName === "design" && !state.stylesLoaded) loadStyles();
  if (pageName === "library" && !state.libraryData) loadLibrary();
  if (pageName === "prompts" && !state.promptsData) loadPrompts();
  if (pageName === "overview" && !state.overviewData) loadOverview();
}

// ── 日程 Tab ─────────────────────────────────────────────────

function todayStr() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function formatRelativeTime(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    const now = new Date();
    const diff = Math.floor((now - d) / 1000);
    if (diff < 60) return "刚刚";
    if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
    return formatDateTime(iso);
  } catch {
    return iso;
  }
}

async function loadSchedulePersonas() {
  const select = $("schedule-persona-select");
  if (select) select.innerHTML = '<option value="">加载中...</option>';
  try {
    const res = await api.get("schedule/personas");
    state.schedulePersonas = (res && res.personas) || [];
    state.schedulePersonasLoaded = true;
    renderSchedulePersonaSelect();
  } catch (e) {
    if (select) select.innerHTML = `<option value="">加载失败</option>`;
    toast(`加载人格列表失败: ${e.message}`, "error");
  }
}

function renderSchedulePersonaSelect() {
  const select = $("schedule-persona-select");
  if (!select) return;
  if (state.schedulePersonas.length === 0) {
    select.innerHTML = '<option value="">未配置人格</option>';
    return;
  }
  select.innerHTML = state.schedulePersonas
    .map((p) => {
      const genMark = p.is_generating ? " (生成中)" : "";
      const todayMark = p.has_today_schedule ? " ✓" : "";
      return `<option value="${esc(p.name)}">${esc(p.name)}${genMark}${todayMark}</option>`;
    })
    .join("");

  // 自动选中第一个有人格的
  if (!state.scheduleSelectedPersona && state.schedulePersonas.length > 0) {
    const withToday = state.schedulePersonas.find((p) => p.has_today_schedule);
    state.scheduleSelectedPersona = (withToday || state.schedulePersonas[0]).name;
    select.value = state.scheduleSelectedPersona;
    onSchedulePersonaChange();
  } else if (state.scheduleSelectedPersona) {
    select.value = state.scheduleSelectedPersona;
    onSchedulePersonaChange();
  }
}

function onSchedulePersonaChange() {
  const select = $("schedule-persona-select");
  if (!select) return;
  state.scheduleSelectedPersona = select.value;

  // 显示人格元信息
  const metaDiv = $("schedule-persona-meta");
  const persona = state.schedulePersonas.find((p) => p.name === state.scheduleSelectedPersona);
  if (metaDiv && persona) {
    const tags = [];
    tags.push(`<span class="schedule-meta-tag">生成时间: ${esc(persona.generate_time || "—")}</span>`);
    tags.push(`<span class="schedule-meta-tag">变化等级: ${esc(persona.variation_level || "—")}</span>`);
    if (persona.has_today_schedule) tags.push(`<span class="schedule-meta-tag active">今日有日程</span>`);
    else tags.push(`<span class="schedule-meta-tag">今日无日程</span>`);
    if (persona.is_generating) tags.push(`<span class="schedule-meta-tag generating">生成中</span>`);
    if (persona.has_tomorrow_request) tags.push(`<span class="schedule-meta-tag active">明日已定制</span>`);
    if (persona.enable_subdivision) tags.push(`<span class="schedule-meta-tag">细分已启用</span>`);
    if (persona.enable_style_review) tags.push(`<span class="schedule-meta-tag">风格审查已启用</span>`);
    metaDiv.innerHTML = tags.join("");
  }

  // 更新导航栏生成徽章
  updateScheduleGenBadge(persona);

  // 加载明日定制要求
  loadTomorrowRequest();

  // 加载历史
  loadScheduleHistory();
}

function updateScheduleGenBadge(persona) {
  const badge = $("badge-schedule-gen");
  if (!badge) return;
  const isGen = persona && persona.is_generating;
  badge.style.display = isGen ? "" : "none";
}

async function loadSchedule(showHistory = true) {
  const persona = state.scheduleSelectedPersona;
  const date = state.scheduleSelectedDate || todayStr();
  if (!persona) {
    toast("请先选择人格", "warning");
    return;
  }

  state.scheduleLoading = true;
  state.scheduleEditMode = false;
  const area = $("schedule-content-area");
  if (area) showLoading(area, "加载日程中...");

  try {
    const data = await api.get("schedule/today", { persona, date });
    state.scheduleCurrentData = data;
    renderScheduleContent(data);
    if (showHistory) loadScheduleHistory();
    // 如果正在生成，开始轮询
    checkAndPollGeneration();
  } catch (e) {
    if (area) {
      area.innerHTML = `
        <div class="schedule-missing">
          <div class="schedule-missing-text">加载失败: ${esc(e.message)}</div>
          <div class="schedule-missing-actions">
            <button class="btn btn-secondary btn-sm" onclick="loadSchedule()">重试</button>
          </div>
        </div>`;
    }
  } finally {
    state.scheduleLoading = false;
  }
}

async function loadScheduleHistory() {
  const persona = state.scheduleSelectedPersona;
  if (!persona) return;
  try {
    const res = await api.get("schedule/history", { persona });
    state.scheduleHistory = (res && res.history) || [];
  } catch (e) {
    state.scheduleHistory = [];
  }
}

function renderScheduleContent(data) {
  const area = $("schedule-content-area");
  if (!area) return;

  if (!data) {
    area.innerHTML = `
      <div class="empty-state">
        <div>选择人格与日期后查看日程</div>
      </div>`;
    return;
  }

  const meta = data.meta || {};
  const isMissing = meta.error || meta.fallback;
  const fallbackReason = meta.fallback_reason || meta.error || "";

  // 如果是缺失/回退
  if (isMissing && !data.timeline) {
    area.innerHTML = `
      <div class="schedule-missing">
        <svg class="schedule-missing-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10"/>
          <line x1="12" y1="8" x2="12" y2="12"/>
          <line x1="12" y1="16" x2="12.01" y2="16"/>
        </svg>
        <div class="schedule-missing-text">${esc(fallbackReason || "该日期尚无日程记录")}</div>
        <div class="schedule-missing-actions">
          <button class="btn btn-primary btn-sm" id="btn-schedule-gen-missing">生成日程</button>
          <button class="btn btn-secondary btn-sm" id="btn-schedule-custom-missing">定制生成</button>
        </div>
      </div>`;
    const genBtn = $("btn-schedule-gen-missing");
    if (genBtn) genBtn.addEventListener("click", () => regenerateSchedule());
    const customBtn = $("btn-schedule-custom-missing");
    if (customBtn) customBtn.addEventListener("click", () => openCustomScheduleModal());
    return;
  }

  const outfit = data.outfit || "";
  const summary = data.summary || "";
  const outfitStyle = data.outfit_style || "";
  const weather = data.weather || "";
  const timeline = data.timeline || [];
  const dateStr = meta.date || state.scheduleSelectedDate || todayStr();

  const tags = [];
  if (meta.edited) tags.push(`<span class="schedule-tag edited">已编辑</span>`);
  if (meta.fallback) tags.push(`<span class="schedule-tag fallback">回退</span>`);
  if (weather) tags.push(`<span class="schedule-tag weather">${esc(weather)}</span>`);

  const timelineHtml = timeline.map((item, idx) => {
    const hasChange = !!item.outfit_change;
    return `
      <div class="schedule-timeline-item${hasChange ? " has-change" : ""}">
        <div class="schedule-timeline-header">
          <span class="schedule-timeline-time">${esc(item.time_start || "")}-${esc(item.time_end || "")}</span>
          <span class="schedule-timeline-title">${esc(item.title || "")}</span>
        </div>
        <div class="schedule-timeline-detail">${esc(item.detail || "")}</div>
        ${hasChange ? `
          <div class="schedule-timeline-change collapsible-block">
            <span class="schedule-timeline-change-label">换装 · 午后第二套</span>
            <div class="schedule-timeline-change-text">${esc(item.outfit_change)}</div>
            <div class="collapse-mask"></div>
          </div>` : ""}
      </div>`;
  }).join("");

  area.innerHTML = `
    <div class="schedule-detail">
      <div class="schedule-header-card">
        <div class="schedule-header-top">
          <div>
            <div class="schedule-date-display">${esc(dateStr)}</div>
            <div class="schedule-date-sub">${esc(personaLabel(state.scheduleSelectedPersona))}${tags.length ? " · " + tags.join(" ") : ""}</div>
          </div>
          ${outfitStyle ? `<span class="schedule-style-badge">${esc(outfitStyle)}</span>` : ""}
        </div>
        ${summary ? `<div class="schedule-summary">${esc(summary)}</div>` : ""}
        ${outfit ? `
          <div class="schedule-outfit-block collapsible-block">
            <div class="schedule-outfit-label">今日穿搭</div>
            <div class="schedule-outfit-text">${esc(outfit)}</div>
            <div class="collapse-mask"></div>
          </div>` : ""}
        <div class="schedule-actions">
          <button class="btn btn-secondary btn-sm" id="btn-schedule-edit">编辑</button>
          <button class="btn btn-secondary btn-sm" id="btn-schedule-regenerate">重生成</button>
          <button class="btn btn-secondary btn-sm" id="btn-schedule-custom">定制生成</button>
        </div>
      </div>
      ${timeline.length > 0 ? `
        <div class="card">
          <div class="card-title">时间线</div>
          <div class="schedule-timeline">
            ${timelineHtml}
          </div>
        </div>` : ""}
      ${renderHistorySection()}
      ${renderTomorrowSection()}
    </div>`;

  // 绑定事件
  const editBtn = $("btn-schedule-edit");
  if (editBtn) editBtn.addEventListener("click", () => enterScheduleEditMode());
  const regenBtn = $("btn-schedule-regenerate");
  if (regenBtn) regenBtn.addEventListener("click", () => regenerateSchedule());
  const customBtn = $("btn-schedule-custom");
  if (customBtn) customBtn.addEventListener("click", () => openCustomScheduleModal());

  bindHistoryItems();
  bindTomorrowSection();

  // 可折叠文本块（穿搭/换装超长时自动折叠）
  area.querySelectorAll(".collapsible-block").forEach((el) => {
    if (el.scrollHeight > el.clientHeight + 4) {
      el.classList.add("can-collapse");
    }
    el.addEventListener("click", () => {
      if (el.classList.contains("can-collapse")) {
        el.classList.toggle("expanded");
      }
    });
  });
}

function personaLabel(name) {
  return name || "—";
}

function renderHistorySection() {
  if (state.scheduleHistory.length === 0) return "";
  const currentDate = state.scheduleSelectedDate || todayStr();
  const items = state.scheduleHistory.map((h) => {
    const isCurrent = h.date === currentDate;
    return `
      <div class="schedule-history-item${isCurrent ? " current" : ""}" data-date="${esc(h.date)}">
        <span class="schedule-history-date">${esc(h.date)}</span>
        <span class="schedule-history-summary">${esc(h.summary || "（无摘要）")}</span>
        ${h.outfit_style ? `<span class="schedule-history-style">${esc(h.outfit_style)}</span>` : ""}
        ${h.is_fallback ? `<span class="schedule-history-fallback">回退</span>` : ""}
      </div>`;
  }).join("");
  return `
    <div class="schedule-history-section">
      <details class="default-prompt-collapse">
        <summary>历史日程 (${state.scheduleHistory.length})</summary>
        <div class="schedule-history-list">
          ${items}
        </div>
      </details>
    </div>`;
}

function bindHistoryItems() {
  document.querySelectorAll(".schedule-history-item[data-date]").forEach((item) => {
    item.addEventListener("click", () => {
      const date = item.dataset.date;
      const dateInput = $("schedule-date-input");
      if (dateInput) dateInput.value = date;
      state.scheduleSelectedDate = date;
      loadSchedule(false);
    });
  });
}

function renderTomorrowSection() {
  const req = state.scheduleTomorrowReq || "";
  return `
    <div class="schedule-tomorrow-section">
      <div class="schedule-tomorrow-title">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="23 4 23 10 17 10"/>
          <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
        </svg>
        明日定制要求
      </div>
      <div class="schedule-tomorrow-content">
        <div class="form-group schedule-tomorrow-input" style="margin-bottom:0">
          <input type="text" class="form-input" id="tomorrow-req-input" placeholder="例如：明天穿洛丽塔风格 / 明天是雨天，安排室内活动..." value="${esc(req)}" />
        </div>
        <button class="btn btn-primary btn-sm" id="btn-tomorrow-save">设置</button>
        ${req ? `<button class="btn btn-secondary btn-sm" id="btn-tomorrow-cancel">取消定制</button>` : ""}
      </div>
      <div class="schedule-tomorrow-status${req ? " set" : ""}" id="tomorrow-req-status">
        ${req ? `已设置：${esc(req)}` : "未设置明日定制要求。设置后，明日自动生成时会参考此要求。"}
      </div>
    </div>`;
}

function bindTomorrowSection() {
  const saveBtn = $("btn-tomorrow-save");
  if (saveBtn) saveBtn.addEventListener("click", () => saveTomorrowRequest());
  const cancelBtn = $("btn-tomorrow-cancel");
  if (cancelBtn) cancelBtn.addEventListener("click", () => cancelTomorrowRequest());
}

async function loadTomorrowRequest() {
  const persona = state.scheduleSelectedPersona;
  if (!persona) return;
  try {
    const res = await api.get("schedule/tomorrow", { persona });
    state.scheduleTomorrowReq = (res && res.requirement) || "";
  } catch (e) {
    state.scheduleTomorrowReq = "";
  }
}

async function saveTomorrowRequest() {
  const persona = state.scheduleSelectedPersona;
  if (!persona) return;
  const input = $("tomorrow-req-input");
  const requirement = input ? input.value.trim() : "";
  if (!requirement) {
    toast("请输入定制要求", "warning");
    return;
  }
  try {
    await api.post("schedule/tomorrow", { persona, requirement });
    state.scheduleTomorrowReq = requirement;
    toast("明日定制要求已设置", "success");
    renderScheduleContent(state.scheduleCurrentData);
  } catch (e) {
    toast(`设置失败: ${e.message}`, "error");
  }
}

async function cancelTomorrowRequest() {
  const persona = state.scheduleSelectedPersona;
  if (!persona) return;
  try {
    await api.post("schedule/tomorrow/cancel", { persona });
    state.scheduleTomorrowReq = "";
    toast("明日定制要求已取消", "success");
    renderScheduleContent(state.scheduleCurrentData);
  } catch (e) {
    toast(`取消失败: ${e.message}`, "error");
  }
}

// ── 日程编辑模式 ──

function enterScheduleEditMode() {
  if (!state.scheduleCurrentData) {
    toast("无日程数据可编辑", "warning");
    return;
  }
  state.scheduleEditMode = true;
  state.scheduleEditData = cloneScheduleForEdit(state.scheduleCurrentData);
  renderScheduleEditForm();
}

function cloneScheduleForEdit(data) {
  return {
    outfit: data.outfit || "",
    summary: data.summary || "",
    outfit_style: data.outfit_style || "",
    weather: data.weather || "",
    timeline: (data.timeline || []).map((item) => ({
      time_start: item.time_start || "",
      time_end: item.time_end || "",
      title: item.title || "",
      detail: item.detail || "",
      outfit_change: item.outfit_change || "",
    })),
  };
}

function renderScheduleEditForm() {
  const area = $("schedule-content-area");
  if (!area) return;
  const d = state.scheduleEditData;

  const timelineHtml = d.timeline.map((item, idx) => {
    const hasOutfitChange = !!(item.outfit_change && item.outfit_change.trim());
    return `
    <div class="schedule-edit-timeline-item" data-idx="${idx}">
      <div class="schedule-edit-timeline-row">
        <span class="schedule-edit-timeline-item-index">#${idx + 1}</span>
        <input type="time" class="form-input" data-field="time_start" value="${esc(item.time_start)}" />
        <span style="color:var(--text-tertiary);font-size:11px">→</span>
        <input type="time" class="form-input" data-field="time_end" value="${esc(item.time_end)}" />
        <input type="text" class="form-input title-input" data-field="title" placeholder="标题" value="${esc(item.title)}" />
        <button class="btn btn-ghost btn-sm btn-remove-timeline" data-idx="${idx}" title="删除">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="3 6 5 6 21 6"/>
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
          </svg>
        </button>
      </div>
      <textarea class="form-textarea detail-textarea" data-field="detail" placeholder="详细描述">${esc(item.detail)}</textarea>
      ${hasOutfitChange ? `
        <div class="schedule-edit-outfit-change">
          <div class="schedule-edit-outfit-change-header">
            <span class="schedule-edit-outfit-change-label">换装</span>
            <button class="btn btn-ghost btn-sm btn-clear-outfit" data-idx="${idx}" title="清除换装">清除</button>
          </div>
          <textarea class="form-textarea outfit-change-textarea" data-field="outfit_change" placeholder="换装描述">${esc(item.outfit_change)}</textarea>
        </div>` : `
        <button class="btn-add-outfit-toggle" data-idx="${idx}">+ 换装</button>`}
    </div>`;
  }).join("");

  area.innerHTML = `
    <div class="card">
      <div class="card-title-row">
        <div class="card-title">编辑日程</div>
        <div class="page-actions">
          <button class="btn btn-secondary btn-sm" id="btn-schedule-edit-cancel">取消</button>
          <button class="btn btn-success btn-sm" id="btn-schedule-edit-save">保存</button>
        </div>
      </div>
      <div class="schedule-edit-form">
        <div class="schedule-edit-field">
          <label class="schedule-edit-field-label">穿搭风格</label>
          <input type="text" class="form-input" id="edit-outfit-style" value="${esc(d.outfit_style)}" />
        </div>
        <div class="schedule-edit-field">
          <label class="schedule-edit-field-label">天气</label>
          <input type="text" class="form-input" id="edit-weather" value="${esc(d.weather)}" />
        </div>
        <div class="schedule-edit-field">
          <label class="schedule-edit-field-label">摘要</label>
          <input type="text" class="form-input" id="edit-summary" value="${esc(d.summary)}" />
        </div>
        <div class="schedule-edit-field">
          <label class="schedule-edit-field-label">穿搭描述</label>
          <textarea class="form-textarea" id="edit-outfit" rows="6">${esc(d.outfit)}</textarea>
        </div>
        <div class="schedule-edit-field">
          <div class="schedule-edit-field-label" style="display:flex;justify-content:space-between;align-items:center">
            <span>时间线</span>
            <button class="btn btn-ghost btn-sm" id="btn-add-timeline-item">+ 添加时段</button>
          </div>
          <div class="schedule-edit-timeline" id="edit-timeline-list">
            ${timelineHtml}
          </div>
        </div>
        <div class="schedule-actions">
          <button class="btn btn-secondary btn-sm" id="btn-schedule-edit-cancel-2">取消</button>
          <button class="btn btn-success" id="btn-schedule-edit-save-2">保存日程</button>
        </div>
      </div>
    </div>`;

  // 绑定事件
  const cancelBtn = $("btn-schedule-edit-cancel");
  if (cancelBtn) cancelBtn.addEventListener("click", () => exitScheduleEditMode());
  const cancelBtn2 = $("btn-schedule-edit-cancel-2");
  if (cancelBtn2) cancelBtn2.addEventListener("click", () => exitScheduleEditMode());
  const saveBtn = $("btn-schedule-edit-save");
  if (saveBtn) saveBtn.addEventListener("click", () => saveScheduleEdit());
  const saveBtn2 = $("btn-schedule-edit-save-2");
  if (saveBtn2) saveBtn2.addEventListener("click", () => saveScheduleEdit());
  const addBtn = $("btn-add-timeline-item");
  if (addBtn) addBtn.addEventListener("click", () => addTimelineItem());

  // 绑定删除按钮
  area.querySelectorAll(".btn-remove-timeline").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = parseInt(btn.dataset.idx, 10);
      if (!isNaN(idx)) removeTimelineItem(idx);
    });
  });

  // 绑定 +换装 按钮
  area.querySelectorAll(".btn-add-outfit-toggle").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = parseInt(btn.dataset.idx, 10);
      if (!isNaN(idx) && state.scheduleEditData.timeline[idx]) {
        state.scheduleEditData.timeline[idx].outfit_change = " ";
        renderScheduleEditForm();
        setTimeout(() => {
          const ta = area.querySelector(`.schedule-edit-timeline-item[data-idx="${idx}"] .outfit-change-textarea`);
          if (ta) { ta.focus(); ta.select(); }
        }, 0);
      }
    });
  });

  // 绑定 清除换装 按钮
  area.querySelectorAll(".btn-clear-outfit").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = parseInt(btn.dataset.idx, 10);
      if (!isNaN(idx) && state.scheduleEditData.timeline[idx]) {
        state.scheduleEditData.timeline[idx].outfit_change = "";
        renderScheduleEditForm();
      }
    });
  });

  // 绑定字段变更
  bindEditTimelineFields();
}

function bindEditTimelineFields() {
  const list = $("edit-timeline-list");
  if (!list) return;
  list.querySelectorAll(".schedule-edit-timeline-item").forEach((itemEl) => {
    const idx = parseInt(itemEl.dataset.idx, 10);
    if (isNaN(idx)) return;
    itemEl.querySelectorAll("[data-field]").forEach((input) => {
      const field = input.dataset.field;
      input.addEventListener("input", () => {
        if (state.scheduleEditData.timeline[idx]) {
          state.scheduleEditData.timeline[idx][field] = input.value;
        }
      });
    });
  });
}

function addTimelineItem() {
  state.scheduleEditData.timeline.push({
    time_start: "",
    time_end: "",
    title: "",
    detail: "",
    outfit_change: "",
  });
  renderScheduleEditForm();
}

function removeTimelineItem(idx) {
  state.scheduleEditData.timeline.splice(idx, 1);
  renderScheduleEditForm();
}

function exitScheduleEditMode() {
  state.scheduleEditMode = false;
  state.scheduleEditData = null;
  renderScheduleContent(state.scheduleCurrentData);
}

async function saveScheduleEdit() {
  const persona = state.scheduleSelectedPersona;
  const date = state.scheduleSelectedDate || todayStr();
  if (!persona) {
    toast("请先选择人格", "warning");
    return;
  }

  // 从 DOM 读取最终值（确保最新）
  const d = state.scheduleEditData;
  const outfitStyleEl = $("edit-outfit-style");
  const weatherEl = $("edit-weather");
  const summaryEl = $("edit-summary");
  const outfitEl = $("edit-outfit");

  const payload = {
    persona,
    date,
    outfit_style: outfitStyleEl ? outfitStyleEl.value : d.outfit_style,
    weather: weatherEl ? weatherEl.value : d.weather,
    summary: summaryEl ? summaryEl.value : d.summary,
    outfit: outfitEl ? outfitEl.value : d.outfit,
    timeline: d.timeline.map((item) => ({
      time_start: item.time_start || "",
      time_end: item.time_end || "",
      title: item.title || "",
      detail: item.detail || "",
      outfit_change: item.outfit_change || "",
    })),
  };

  if (!payload.outfit.trim()) {
    toast("穿搭描述不能为空", "warning");
    return;
  }
  if (payload.timeline.length === 0) {
    toast("时间线不能为空", "warning");
    return;
  }

  try {
    await api.post("schedule/save", payload);
    toast("日程已保存", "success");
    state.scheduleEditMode = false;
    state.scheduleEditData = null;
    await loadSchedule(false);
  } catch (e) {
    toast(`保存失败: ${e.message}`, "error");
  }
}

// ── 日程生成 ──

function regenerateSchedule() {
  const persona = state.scheduleSelectedPersona;
  const date = state.scheduleSelectedDate || todayStr();
  if (!persona) {
    toast("请先选择人格", "warning");
    return;
  }

  confirmModal({
    title: "重生成日程",
    message: `确认为 ${persona} 在 ${date} 重新生成日程？\n这将覆盖当前日程（如有）。`,
    confirmText: "重生成",
    danger: true,
    onConfirm: async (close) => {
      try {
        const result = await api.post("schedule/regenerate", { persona, date });
        if (result && result.started) {
          toast("日程生成已启动，请稍候...", "success");
          close();
          startGenerationPolling(persona);
        } else {
          toast(result?.error || "无法启动生成", "error");
        }
      } catch (e) {
        toast(`触发生成失败: ${e.message}`, "error");
      }
    },
  });
}

function openCustomScheduleModal() {
  const persona = state.scheduleSelectedPersona;
  if (!persona) {
    toast("请先选择人格", "warning");
    return;
  }
  const date = state.scheduleSelectedDate || todayStr();
  showModal({
    title: "定制生成日程",
    bodyHtml: `
      <div class="schedule-custom-form">
        <div class="form-hint">
          定制生成会根据你的额外要求生成日程。例如：<br/>
          • "今天穿洛丽塔风格，下午去咖啡店"<br/>
          • "安排一次户外摄影活动"<br/>
          • "今天心情低落，安排一些治愈的活动"
        </div>
        <div class="form-group">
          <label class="form-label">人格</label>
          <input type="text" class="form-input" value="${esc(persona)}" disabled />
        </div>
        <div class="form-group">
          <label class="form-label">日期</label>
          <input type="text" class="form-input" value="${esc(date)}" disabled />
        </div>
        <div class="form-group">
          <label class="form-label">额外要求</label>
          <textarea class="form-textarea" id="custom-req-input" rows="5" placeholder="描述你对日程的特殊要求..."></textarea>
        </div>
      </div>`,
    footerHtml: `
      <button class="btn btn-secondary" id="modal-cancel">取消</button>
      <button class="btn btn-primary" id="modal-confirm-custom">开始生成</button>`,
    onMount: (container, close) => {
      const cancelBtn = $("modal-cancel");
      if (cancelBtn) cancelBtn.addEventListener("click", close);
      const confirmBtn = $("modal-confirm-custom");
      if (confirmBtn) confirmBtn.addEventListener("click", () => {
        const input = $("custom-req-input");
        const req = input ? input.value.trim() : "";
        if (!req) {
          toast("请输入额外要求", "warning");
          return;
        }
        close();
        customSchedule(req);
      });
    },
  });
}

async function customSchedule(extraRequirement) {
  const persona = state.scheduleSelectedPersona;
  const date = state.scheduleSelectedDate || todayStr();
  if (!persona) return;

  try {
    const result = await api.post("schedule/custom", {
      persona,
      date,
      extra_requirement: extraRequirement,
    });
    if (result && result.started) {
      toast("定制生成已启动，请稍候...", "success");
      startGenerationPolling(persona);
    }
  } catch (e) {
    toast(`触发定制生成失败: ${e.message}`, "error");
  }
}

// ── 生成状态轮询 ──

function checkAndPollGeneration() {
  const persona = state.scheduleSelectedPersona;
  if (!persona) return;
  const personaInfo = state.schedulePersonas.find((p) => p.name === persona);
  if (personaInfo && personaInfo.is_generating) {
    startGenerationPolling(persona);
  }
}

function startGenerationPolling(persona) {
  if (state.scheduleGenPolling) return;
  state.scheduleGenPolling = true;
  updateScheduleGenBadge({ is_generating: true });
  showGenerationStatus("generating", "日程生成中...", null);
  pollGenerationStatus(persona);
}

async function pollGenerationStatus(persona) {
  if (state.scheduleGenPollTimer) {
    clearTimeout(state.scheduleGenPollTimer);
    state.scheduleGenPollTimer = null;
  }

  try {
    const status = await api.get("schedule/status", { persona });
    if (status && status.generating) {
      const startTime = status.started_at ? formatRelativeTime(status.started_at) : "";
      showGenerationStatus("generating", "日程生成中...", startTime);
      // 5 秒后再次轮询
      state.scheduleGenPollTimer = setTimeout(() => pollGenerationStatus(persona), 5000);
    } else {
      // 生成结束
      state.scheduleGenPolling = false;
      updateScheduleGenBadge({ is_generating: false });

      const result = status.last_result;
      const updateTime = status.last_updated ? formatRelativeTime(status.last_updated) : "";
      if (result === "success") {
        showGenerationStatus("success", "日程生成成功！", updateTime);
        toast("日程生成成功", "success");
        // 自动重新加载日程
        await loadSchedule(false);
        // 刷新人格列表状态
        await loadSchedulePersonas();
        // 3 秒后隐藏状态条
        setTimeout(() => hideGenerationStatus(), 3000);
      } else if (result === "error") {
        const errMsg = status.last_error || "生成失败";
        showGenerationStatus("error", `生成失败: ${errMsg}`, updateTime);
        toast(`生成失败: ${errMsg}`, "error");
        await loadSchedulePersonas();
      } else {
        hideGenerationStatus();
      }
    }
  } catch (e) {
    // 网络错误，10 秒后重试
    state.scheduleGenPollTimer = setTimeout(() => pollGenerationStatus(persona), 10000);
  }
}

function showGenerationStatus(type, message, timeStr) {
  const bar = $("schedule-gen-status");
  if (!bar) return;
  bar.style.display = "";
  bar.className = `schedule-gen-status${type === "success" ? " success" : type === "error" ? " error" : ""}`;
  const spinner = type === "generating" ? `<span class="spinner"></span>` : "";
  const timeHtml = timeStr ? `<span class="schedule-gen-status-time">${esc(timeStr)}</span>` : "";
  bar.innerHTML = `${spinner}<span class="schedule-gen-status-text">${esc(message)}</span>${timeHtml}`;
}

function hideGenerationStatus() {
  const bar = $("schedule-gen-status");
  if (bar) bar.style.display = "none";
}

// ── 设计 Tab：风格网格 ────────────────────────────────────────

async function loadStyles() {
  const grid = $("design-style-grid");
  if (grid) showLoading(grid, "加载风格中...");
  try {
    const res = await api.get("styles");
    // Bridge 自动解包：res 直接就是 {styles: [...], total: N}
    state.styles = (res && res.styles) || [];
    state.stylesLoaded = true;
    renderStyleGrid();
    updateLibraryBadge();
  } catch (e) {
    if (grid) grid.innerHTML = `<div class="style-grid-empty">加载失败: ${esc(e.message)}</div>`;
  }
}

function renderStyleGrid() {
  const grid = $("design-style-grid");
  if (!grid) return;
  const filter = state.styleSearchText.trim().toLowerCase();
  const filtered = filter
    ? state.styles.filter((s) => s.name.toLowerCase().includes(filter))
    : state.styles;
  if (filtered.length === 0) {
    grid.innerHTML = `<div class="style-grid-empty">${filter ? "无匹配风格" : "暂无可用风格"}</div>`;
    return;
  }
  grid.innerHTML = filtered
    .map((s) => {
      const selected = s.name === state.selectedStyleName ? " selected" : "";
      const tags = (s.sources || [])
        .map((src) => `<span class="source-tag ${esc(src)}">${esc(src)}</span>`)
        .join("");
      const curatedBadge = s.has_curated
        ? `<span class="source-tag curated">已入库 ${s.curated_count || 0}</span>`
        : "";
      return `
        <div class="style-card${selected}" data-style-name="${esc(s.name)}">
          <div class="style-card-name">${esc(s.name)}</div>
          <div class="style-card-meta">${tags}${curatedBadge}</div>
        </div>
      `;
    })
    .join("");
  grid.querySelectorAll(".style-card[data-style-name]").forEach((card) => {
    card.addEventListener("click", () => {
      state.selectedStyleName = card.dataset.styleName;
      renderStyleGrid();
      updateDesignButtonState();
    });
  });
}

function updateDesignButtonState() {
  const btn = $("btn-design");
  if (!btn) return;
  const hasStyle = state.selectedStyleName.length > 0;
  btn.disabled = !hasStyle || state.designLoading;
}

async function designOutfit() {
  const styleName = state.selectedStyleName;
  if (!styleName) {
    toast("请先选择一个风格", "warning");
    return;
  }
  const userInputEl = $("design-user-input");
  const userInput = userInputEl ? userInputEl.value.trim() : "";
  state.designSession = {
    styleName,
    userInput,
    history: [],
  };
  state.designLoading = true;
  updateDesignButtonState();
  setDesignButtonLoading(true, "设计中...");
  $("design-result-area").innerHTML = "";
  $("iteration-history-area").innerHTML = "";
  try {
    const res = await api.post("design", { style_name: styleName, user_input: userInput || null });
    // Bridge 自动解包：res 直接就是 {name, description, success?}
    if (res && res.name) {
      state.designSession.history.push({
        role: "designer",
        name: res.name || "",
        description: res.description || "",
      });
      renderDesignResult();
      renderIterationHistory();
      toast("设计完成", "success");
    } else {
      toast("设计返回异常：" + JSON.stringify(res), "error");
      state.designSession = null;
    }
  } catch (e) {
    toast(`设计失败: ${e.message}`, "error");
    state.designSession = null;
  } finally {
    state.designLoading = false;
    updateDesignButtonState();
    setDesignButtonLoading(false);
  }
}

function setDesignButtonLoading(loading, text) {
  const btn = $("btn-design");
  if (!btn) return;
  const textEl = btn.querySelector(".btn-text");
  if (loading) {
    btn.disabled = true;
    if (textEl) textEl.textContent = text || "处理中...";
  } else {
    btn.disabled = false;
    if (textEl) textEl.textContent = "让设计师设计";
  }
}

function getCurrentDesignEntry() {
  if (!state.designSession || !state.designSession.history.length) return null;
  return state.designSession.history[state.designSession.history.length - 1];
}

function renderDesignResult() {
  const area = $("design-result-area");
  if (!area) return;
  const current = getCurrentDesignEntry();
  if (!current) {
    area.innerHTML = "";
    return;
  }
  const isReviewer = current.role === "reviewer";
  const critiqueHtml = (isReviewer && current.critique)
    ? `<div class="design-result-critique">
         <div class="critique-label">审核师批评理由</div>
         <div class="critique-text">${esc(current.critique)}</div>
       </div>`
    : "";
  area.innerHTML = `
    <div class="card">
      <div class="card-title">${isReviewer ? "审核师修改版" : "设计师初版"}</div>
      <div class="design-result">
        <div class="design-result-name">${esc(current.name)}</div>
        ${critiqueHtml}
        <div class="design-result-description">${esc(current.description)}</div>
        <div class="design-result-actions">
          <button class="btn btn-success btn-sm" id="btn-approve">通过 — 入库</button>
          <button class="btn btn-warning btn-sm" id="btn-iterate">迭代 — 交审核师</button>
          <button class="btn btn-danger btn-sm" id="btn-discard">废弃</button>
        </div>
      </div>
    </div>
  `;
  $("btn-approve").addEventListener("click", approveDesign);
  $("btn-iterate").addEventListener("click", () => openIterationModal());
  $("btn-discard").addEventListener("click", discardDesign);
}

function renderIterationHistory() {
  const area = $("iteration-history-area");
  if (!area) return;
  const session = state.designSession;
  if (!session || !session.history.length) {
    area.innerHTML = "";
    return;
  }
  const entries = session.history
    .map((entry, idx) => {
      const roleLabel = entry.role === "designer" ? "设计师" : "审核师";
      const roleClass = entry.role === "reviewer" ? "reviewer" : "";
      const feedbackHtml = entry.userFeedback
        ? `<div class="text-small text-muted" style="margin-top:6px">用户意见：${esc(entry.userFeedback)}</div>`
        : "";
      const critiqueHtml = (entry.role === "reviewer" && entry.critique)
        ? `<div class="iteration-entry-critique">
             <span class="critique-tag">批评</span>
             <span class="critique-text-inline">${esc(entry.critique)}</span>
           </div>`
        : "";
      return `
        <div class="iteration-entry">
          <div class="iteration-entry-role ${roleClass}">${roleLabel} #${idx + 1}</div>
          <div class="iteration-entry-name">${esc(entry.name)}</div>
          ${critiqueHtml}
          <div class="design-result-description">${esc(entry.description)}</div>
          ${feedbackHtml}
        </div>
      `;
    })
    .join("");
  area.innerHTML = `
    <div class="card">
      <div class="card-title">迭代历史（${session.history.length} 步）</div>
      <div class="iteration-history">${entries}</div>
    </div>
  `;
}

function openIterationModal() {
  const current = getCurrentDesignEntry();
  if (!current) {
    toast("没有可迭代的设计", "warning");
    return;
  }
  const session = state.designSession;
  const bodyHtml = `
    <div class="form-group">
      <label class="form-label">当前设计</label>
      <div class="text-small text-muted" style="margin-bottom:8px">
        ${esc(session.styleName)} / ${esc(current.name)}
      </div>
      <div class="design-result-description" style="background:var(--bg-app);padding:12px;border-radius:6px;border:1px solid var(--border-light);max-height:160px;overflow-y:auto">${esc(current.description)}</div>
    </div>
    <div class="form-group">
      <label class="form-label">修改意见（必填，优先级最高）</label>
      <textarea
        id="modal-iteration-feedback"
        class="form-textarea"
        placeholder="例如：领口太低了，改成V领 / 颜色换成更暖的色调 / 整体太花哨，简化一下..."
        rows="5"
      ></textarea>
      <div class="form-hint">用户意见会作为最高优先级指令传给审核师。</div>
    </div>
  `;
  const footerHtml = `
    <button class="btn btn-secondary" data-modal-cancel>取消</button>
    <button class="btn btn-primary" data-modal-confirm>交给审核师</button>
  `;
  showModal({
    title: "迭代 — 交由审核师修改",
    bodyHtml,
    footerHtml,
    onMount: (container, close) => {
      container.querySelector("[data-modal-cancel]").addEventListener("click", close);
      const confirmBtn = container.querySelector("[data-modal-confirm]");
      const feedbackEl = container.querySelector("#modal-iteration-feedback");
      confirmBtn.addEventListener("click", async () => {
        const feedback = feedbackEl.value.trim();
        if (!feedback) {
          toast("请填写修改意见", "warning");
          return;
        }
        confirmBtn.disabled = true;
        confirmBtn.textContent = "审核中...";
        await runReview(feedback);
        close();
      });
    },
  });
}

async function runReview(userFeedback) {
  const session = state.designSession;
  if (!session) {
    toast("没有活跃的设计会话", "warning");
    return;
  }
  const current = getCurrentDesignEntry();
  if (!current) {
    toast("没有可迭代的设计", "warning");
    return;
  }
  state.reviewLoading = true;
  setDesignButtonLoading(true, "审核中...");
  try {
    const res = await api.post("review", {
      style_name: session.styleName,
      original_name: current.name,
      original_description: current.description,
      user_feedback: userFeedback,
    });
    // Bridge 自动解包
    if (res && res.name) {
      session.history.push({
        role: "reviewer",
        name: res.name || "",
        description: res.description || "",
        critique: res.critique || "",
        userFeedback,
      });
      renderDesignResult();
      renderIterationHistory();
      toast("审核师已产出修改版", "success");
    } else {
      toast("审核返回异常：" + JSON.stringify(res), "error");
    }
  } catch (e) {
    toast(`审核失败: ${e.message}`, "error");
  } finally {
    state.reviewLoading = false;
    setDesignButtonLoading(false);
  }
}

function discardDesign() {
  const session = state.designSession;
  if (!session) {
    toast("没有可废弃的方案", "warning");
    return;
  }
  state.designSession = null;
  state.selectedStyleName = "";
  $("design-result-area").innerHTML = "";
  $("iteration-history-area").innerHTML = "";
  renderStyleGrid();
  updateDesignButtonState();
  toast("已废弃当前方案", "info");
}

async function approveDesign() {
  const session = state.designSession;
  if (!session) {
    toast("没有可入库的设计", "warning");
    return;
  }
  const current = getCurrentDesignEntry();
  if (!current) {
    toast("没有可入库的设计", "warning");
    return;
  }
  const iterations = session.history.filter((h) => h.role === "reviewer").length;
  const bodyHtml = `
    <div class="form-group">
      <label class="form-label">风格</label>
      <div class="text-small" style="padding:8px 12px;background:var(--bg-app);border-radius:6px;border:1px solid var(--border-light)">${esc(session.styleName)}</div>
    </div>
    <div class="form-group">
      <label class="form-label">款式名称（可修改）</label>
      <input type="text" id="modal-approve-name" class="form-input" value="${esc(current.name)}" />
    </div>
    <div class="form-group">
      <label class="form-label">款式描述（可修改）</label>
      <textarea id="modal-approve-desc" class="form-textarea" rows="8">${esc(current.description)}</textarea>
    </div>
    <div class="form-group">
      <label class="form-label">分级</label>
      <select id="modal-approve-tier" class="form-input">
        <option value="normal" selected>经典款（允许审查微调）</option>
        <option value="starred">★ 标星收藏款（绝对正确，仅措辞可调）</option>
      </select>
      <div class="form-hint">标星款：二次审查不得修改外观，仅可润色措辞。仅在你完全信任该设计时使用。</div>
    </div>
    <div class="form-hint">迭代次数：${iterations} 次（入库后可在优秀库中继续编辑）</div>
  `;
  const footerHtml = `
    <button class="btn btn-secondary" data-modal-cancel>取消</button>
    <button class="btn btn-success" data-modal-confirm>确认入库</button>
  `;
  showModal({
    title: "通过 — 入库到优秀库",
    bodyHtml,
    footerHtml,
    onMount: (container, close) => {
      container.querySelector("[data-modal-cancel]").addEventListener("click", close);
      const confirmBtn = container.querySelector("[data-modal-confirm]");
      const nameEl = container.querySelector("#modal-approve-name");
      const descEl = container.querySelector("#modal-approve-desc");
      const tierEl = container.querySelector("#modal-approve-tier");
      confirmBtn.addEventListener("click", async () => {
        const name = nameEl.value.trim();
        const desc = descEl.value.trim();
        if (!name || !desc) {
          toast("名称与描述不能为空", "warning");
          return;
        }
        confirmBtn.disabled = true;
        confirmBtn.textContent = "入库中...";
        try {
          await api.post("outfits/add", {
            style_name: session.styleName,
            name,
            description: desc,
            iterations,
            tier: tierEl.value,
          });
          // Bridge 成功时不抛错；失败时抛错
          toast("已入库到优秀库", "success");
          state.designSession = null;
          $("design-result-area").innerHTML = "";
          $("iteration-history-area").innerHTML = "";
          close();
          loadStyles();
          loadLibrary();
          loadOverview();
        } catch (e) {
          toast(`入库失败: ${e.message}`, "error");
          confirmBtn.disabled = false;
          confirmBtn.textContent = "确认入库";
        }
      });
    },
  });
}

// ── 优秀库 Tab ────────────────────────────────────────────────

async function loadLibrary() {
  state.libraryLoading = true;
  showLoading("library-list", "加载优秀库...");
  try {
    const res = await api.get("outfits");
    state.libraryData = res || { styles: [], total: 0 };
    renderLibrary();
    updateLibraryBadge();
  } catch (e) {
    $("library-list").innerHTML = `<div class="empty-state">加载失败: ${esc(e.message)}</div>`;
  } finally {
    state.libraryLoading = false;
  }
}

function renderLibrary() {
  if (!state.libraryData) return;
  const data = state.libraryData;
  const styles = data.styles || [];
  renderLibraryFilters(styles);
  const listEl = $("library-list");
  if (!listEl) return;
  const filter = state.libraryFilter;
  const filteredStyles = filter
    ? styles.filter((s) => s.style === filter)
    : styles;
  if (filteredStyles.length === 0) {
    showEmpty(listEl, "优秀库为空，去「设计」tab 创建第一套吧");
    return;
  }
  const items = [];
  for (const styleGroup of filteredStyles) {
    for (const item of styleGroup.items || []) {
      items.push({ ...item, style: styleGroup.style, probability: styleGroup.probability });
    }
  }
  if (items.length === 0) {
    showEmpty(listEl, "该风格下暂无条目");
    return;
  }
  listEl.innerHTML = `<div class="outfit-list">${items.map((item) => renderOutfitItem(item)).join("")}</div>`;
  listEl.querySelectorAll("[data-action]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const action = btn.dataset.action;
      const style = btn.dataset.style;
      const name = btn.dataset.name;
      const item = items.find((i) => i.style === style && i.name === name);
      if (!item) return;
      if (action === "edit") openEditOutfitModal(item);
      else if (action === "delete") confirmDeleteOutfit(item);
      else if (action === "use-count") openUseCountModal(item);
      else if (action === "tier") toggleOutfitTier(item, btn.dataset.tier);
    });
  });
  listEl.querySelectorAll("[data-action-prob]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const style = btn.dataset.style;
      const prob = parseFloat(btn.dataset.prob) || 0;
      openProbabilityModal(style, prob);
    });
  });
}

function renderOutfitItem(item) {
  const useCount = Number(item.use_count || 0);
  const iterations = Number(item.iterations || 0);
  const isStarred = String(item.tier || "").toLowerCase() === "starred";
  const tierBadge = isStarred
    ? `<span class="outfit-tier-badge outfit-tier-starred" title="标星收藏款：绝对正确，审查时仅措辞可调，外观不得修改">★ 标星收藏款</span>`
    : `<span class="outfit-tier-badge outfit-tier-normal" title="经典款：审查时允许简单微调，大体不能改">经典款</span>`;
  const tierBtn = isStarred
    ? `<button class="btn btn-ghost btn-sm" data-action="tier" data-style="${esc(item.style)}" data-name="${esc(item.name)}" data-tier="normal">取消标星</button>`
    : `<button class="btn btn-ghost btn-sm" data-action="tier" data-style="${esc(item.style)}" data-name="${esc(item.name)}" data-tier="starred">★ 标为收藏款</button>`;
  return `
    <div class="outfit-item">
      <div class="outfit-item-header">
        <div class="outfit-name">${esc(item.name)}</div>
        <div class="outfit-meta">
          <span class="outfit-style-badge">${esc(item.style)}</span>
          ${tierBadge}
          <span class="outfit-use-count">使用 ${useCount} 次</span>
          ${iterations > 0 ? `<span class="text-small text-muted">迭代 ${iterations} 次</span>` : ""}
          <span class="text-small text-muted">${esc(formatDateTime(item.created_at))}</span>
        </div>
      </div>
      <div class="outfit-description">${esc(item.description)}</div>
      <div class="outfit-actions">
        <button class="btn btn-secondary btn-sm" data-action="edit" data-style="${esc(item.style)}" data-name="${esc(item.name)}">编辑</button>
        <button class="btn btn-secondary btn-sm" data-action="use-count" data-style="${esc(item.style)}" data-name="${esc(item.name)}">使用计数</button>
        ${tierBtn}
        <button class="btn btn-danger btn-sm" data-action="delete" data-style="${esc(item.style)}" data-name="${esc(item.name)}">删除</button>
        <button class="btn btn-ghost btn-sm" data-action-prob data-style="${esc(item.style)}" data-prob="${item.probability}">注入概率: ${(Number(item.probability || 0) * 100).toFixed(0)}%</button>
      </div>
    </div>
  `;
}

function renderLibraryFilters(styles) {
  const bar = $("library-filters");
  if (!bar) return;
  const totalCount = styles.reduce((sum, s) => sum + (s.count || 0), 0);
  const allBtn = `<button class="filter-btn ${state.libraryFilter === "" ? "active" : ""}" data-style="">全部 ${totalCount}</button>`;
  const styleBtns = styles
    .filter((s) => s.count > 0)
    .map((s) => `<button class="filter-btn ${state.libraryFilter === s.style ? "active" : ""}" data-style="${esc(s.style)}">${esc(s.style)} (${s.count})</button>`)
    .join("");
  bar.innerHTML = allBtn + styleBtns;
  bar.querySelectorAll(".filter-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.libraryFilter = btn.dataset.style || "";
      renderLibrary();
    });
  });
}

function openEditOutfitModal(item) {
  const bodyHtml = `
    <div class="form-group">
      <label class="form-label">风格</label>
      <div class="text-small text-muted">${esc(item.style)}</div>
    </div>
    <div class="form-group">
      <label class="form-label">款式名称</label>
      <input type="text" id="modal-edit-name" class="form-input" value="${esc(item.name)}" />
    </div>
    <div class="form-group">
      <label class="form-label">描述</label>
      <textarea id="modal-edit-desc" class="form-textarea" rows="8">${esc(item.description)}</textarea>
    </div>
  `;
  const footerHtml = `
    <button class="btn btn-secondary" data-modal-cancel>取消</button>
    <button class="btn btn-primary" data-modal-confirm>保存</button>
  `;
  showModal({
    title: "编辑条目",
    bodyHtml,
    footerHtml,
    onMount: (container, close) => {
      container.querySelector("[data-modal-cancel]").addEventListener("click", close);
      const confirmBtn = container.querySelector("[data-modal-confirm]");
      const nameEl = container.querySelector("#modal-edit-name");
      const descEl = container.querySelector("#modal-edit-desc");
      confirmBtn.addEventListener("click", async () => {
        const newName = nameEl.value.trim();
        const newDesc = descEl.value.trim();
        if (!newName || !newDesc) {
          toast("名称与描述不能为空", "warning");
          return;
        }
        confirmBtn.disabled = true;
        confirmBtn.textContent = "保存中...";
        try {
          await api.post("outfits/update", {
            style_name: item.style,
            old_name: item.name,
            new_name: newName,
            new_description: newDesc,
          });
          toast("已更新", "success");
          close();
          loadLibrary();
          loadOverview();
        } catch (e) {
          toast(`更新失败: ${e.message}`, "error");
          confirmBtn.disabled = false;
          confirmBtn.textContent = "保存";
        }
      });
    },
  });
}

function toggleOutfitTier(item, nextTier) {
  const targetTier = String(nextTier || "").toLowerCase() === "starred" ? "starred" : "normal";
  const label = targetTier === "starred" ? "标星收藏款" : "经典款";
  const message = targetTier === "starred"
    ? `将「${item.name}」标为收藏款。此后二次审查对该款基于研究师输出的方案仅允许措辞调整，不得修改外观。`
    : `将「${item.name}」降为经典款。二次审查将允许对该款方案做简单微调。`;
  confirmModal({
    title: `切换为${label}`,
    message,
    confirmText: "确认",
    onConfirm: async (close) => {
      try {
        await api.post("outfits/tier", {
          style_name: item.style,
          name: item.name,
          tier: targetTier,
        });
        toast(`已切换为${label}`, "success");
        close();
        loadLibrary();
      } catch (e) {
        toast(`切换失败: ${e.message}`, "error");
        throw e;
      }
    },
  });
}

function confirmDeleteOutfit(item) {
  confirmModal({
    title: "确认删除",
    message: `将删除风格「${item.style}」下的「${item.name}」。此操作不可撤销。`,
    confirmText: "删除",
    danger: true,
    onConfirm: async (close) => {
      try {
        await api.post("outfits/delete", {
          style_name: item.style,
          name: item.name,
        });
        toast("已删除", "success");
        close();
        loadLibrary();
        loadOverview();
        loadStyles();
      } catch (e) {
        toast(`删除失败: ${e.message}`, "error");
        throw e;
      }
    },
  });
}

function openUseCountModal(item) {
  const bodyHtml = `
    <div class="form-group">
      <label class="form-label">条目</label>
      <div class="text-small text-muted">${esc(item.style)} / ${esc(item.name)}</div>
      <div class="form-hint">当前使用计数：${item.use_count}。注入时优先使用计数低的设计。</div>
    </div>
    <div class="form-group">
      <label class="form-label">新计数（非负整数）</label>
      <input type="number" id="modal-use-count" class="form-input" value="${item.use_count}" min="0" step="1" />
    </div>
  `;
  const footerHtml = `
    <button class="btn btn-secondary" data-modal-cancel>取消</button>
    <button class="btn btn-primary" data-modal-confirm>保存</button>
  `;
  showModal({
    title: "调整使用计数",
    bodyHtml,
    footerHtml,
    onMount: (container, close) => {
      container.querySelector("[data-modal-cancel]").addEventListener("click", close);
      const confirmBtn = container.querySelector("[data-modal-confirm]");
      const countEl = container.querySelector("#modal-use-count");
      confirmBtn.addEventListener("click", async () => {
        const count = parseInt(countEl.value, 10);
        if (isNaN(count) || count < 0) {
          toast("计数必须是非负整数", "warning");
          return;
        }
        confirmBtn.disabled = true;
        confirmBtn.textContent = "保存中...";
        try {
          await api.post("outfits/use_count", {
            style_name: item.style,
            name: item.name,
            count,
          });
          toast("已更新", "success");
          close();
          loadLibrary();
          loadOverview();
        } catch (e) {
          toast(`更新失败: ${e.message}`, "error");
          confirmBtn.disabled = false;
          confirmBtn.textContent = "保存";
        }
      });
    },
  });
}

function openProbabilityModal(style, currentProb) {
  const isCosplay = style === "cosplay";
  const bodyHtml = `
    <div class="form-group">
      <label class="form-label">风格</label>
      <div class="text-small text-muted">${esc(style)}${isCosplay ? "（cosplay 默认 1.0）" : ""}</div>
      <div class="form-hint">运行时风格研究时，以此概率将该风格的优秀设计作为「指定经典款式」注入给 Grok。</div>
    </div>
    <div class="form-group">
      <label class="form-label">注入概率</label>
      <div class="probability-row">
        <input type="range" id="modal-prob-slider" class="probability-slider" min="0" max="1" step="0.05" value="${currentProb}" />
        <span class="probability-value" id="modal-prob-value">${(currentProb * 100).toFixed(0)}%</span>
      </div>
    </div>
  `;
  const footerHtml = `
    <button class="btn btn-secondary" data-modal-cancel>取消</button>
    <button class="btn btn-primary" data-modal-confirm>保存</button>
  `;
  showModal({
    title: "配置注入概率",
    bodyHtml,
    footerHtml,
    onMount: (container, close) => {
      container.querySelector("[data-modal-cancel]").addEventListener("click", close);
      const slider = container.querySelector("#modal-prob-slider");
      const valueEl = container.querySelector("#modal-prob-value");
      slider.addEventListener("input", () => {
        valueEl.textContent = `${(parseFloat(slider.value) * 100).toFixed(0)}%`;
      });
      const confirmBtn = container.querySelector("[data-modal-confirm]");
      confirmBtn.addEventListener("click", async () => {
        const probability = parseFloat(slider.value);
        confirmBtn.disabled = true;
        confirmBtn.textContent = "保存中...";
        try {
          await api.post("probability", { style, probability });
          toast("已更新", "success");
          close();
          loadLibrary();
          loadOverview();
        } catch (e) {
          toast(`更新失败: ${e.message}`, "error");
          confirmBtn.disabled = false;
          confirmBtn.textContent = "保存";
        }
      });
    },
  });
}

async function exportData() {
  // 确保数据已加载
  if (!state.libraryData) {
    try {
      const res = await api.get("outfits");
      state.libraryData = res || { styles: [], total: 0 };
    } catch (e) {
      toast(`加载数据失败: ${e.message}`, "error");
      return;
    }
  }
  const styles = ((state.libraryData && state.libraryData.styles) || []).filter(
    (s) => s.items && s.items.length > 0
  );
  if (styles.length === 0) {
    toast("优秀库为空，无可导出内容", "warning");
    return;
  }

  // 选中状态：Map<styleIndex, Set<itemIndex>>；默认全选
  const selected = new Map();
  styles.forEach((s, si) => {
    selected.set(si, new Set(s.items.map((_, ii) => ii)));
  });

  const bodyHtml = `
    <div class="export-toolbar">
      <div class="export-toolbar-left">
        <button class="btn btn-sm btn-secondary" id="export-select-all">全选</button>
        <button class="btn btn-sm btn-secondary" id="export-select-none">全不选</button>
      </div>
      <div class="export-toolbar-right">
        <label class="export-checkbox-label">
          <input type="checkbox" id="export-include-prompts" checked />
          <span>包含提示词配置</span>
        </label>
        <span class="export-stat" id="export-stat">已选 0 套</span>
      </div>
    </div>
    <div class="form-hint export-hint">
      勾选风格可一次选中该风格下全部条目；勾选具体条目则只导出选中项。导出文件由浏览器自动下载到默认下载目录。
    </div>
    <div class="export-tree" id="export-tree"></div>
  `;
  const footerHtml = `
    <button class="btn btn-secondary" data-modal-cancel>取消</button>
    <button class="btn btn-primary" data-modal-confirm>导出选中</button>
  `;

  showModal({
    title: "导出优秀库",
    bodyHtml,
    footerHtml,
    onMount: (container, close) => {
      container.querySelector("[data-modal-cancel]").addEventListener("click", close);

      const treeEl = container.querySelector("#export-tree");
      const statEl = container.querySelector("#export-stat");

      const countSelected = () => {
        let n = 0;
        selected.forEach((set) => (n += set.size));
        return n;
      };
      const updateStat = () => {
        if (statEl) statEl.textContent = `已选 ${countSelected()} 套`;
      };

      const renderTree = () => {
        treeEl.innerHTML = styles
          .map((s, si) => {
            const set = selected.get(si);
            const selCount = set ? set.size : 0;
            const isAll = selCount === s.items.length && selCount > 0;
            const itemsHtml = s.items
              .map((item, ii) => {
                const checked = set && set.has(ii) ? "checked" : "";
                const desc = String(item.description || "");
                const preview = desc.slice(0, 80);
                const ellipsis = desc.length > 80 ? "…" : "";
                return `
                <div class="export-item-row" data-style-idx="${si}" data-item-idx="${ii}">
                  <label class="export-checkbox-label export-item-label">
                    <input type="checkbox" class="export-item-checkbox" ${checked} />
                    <span class="export-item-name">${esc(item.name)}</span>
                  </label>
                  <div class="export-item-preview">${esc(preview)}${ellipsis}</div>
                </div>`;
              })
              .join("");
            return `
            <div class="export-style-group" data-style-idx="${si}">
              <div class="export-style-header">
                <label class="export-checkbox-label export-style-label">
                  <input type="checkbox" class="export-style-checkbox" ${isAll ? "checked" : ""} />
                  <span class="export-style-name">${esc(s.style)}</span>
                  <span class="export-style-count">${selCount}/${s.items.length}</span>
                </label>
              </div>
              <div class="export-items">${itemsHtml}</div>
            </div>`;
          })
          .join("");
        bindTreeEvents();
        // 设置 indeterminate 状态
        styles.forEach((s, si) => {
          const set = selected.get(si);
          const selCount = set ? set.size : 0;
          const groupEl = treeEl.querySelector(`.export-style-group[data-style-idx="${si}"]`);
          if (!groupEl) return;
          const cb = groupEl.querySelector(".export-style-checkbox");
          if (cb) cb.indeterminate = selCount > 0 && selCount < s.items.length;
        });
      };

      const bindTreeEvents = () => {
        // 风格级勾选
        treeEl.querySelectorAll(".export-style-group").forEach((groupEl) => {
          const si = Number(groupEl.dataset.styleIdx);
          const styleCheckbox = groupEl.querySelector(".export-style-checkbox");
          const countEl = groupEl.querySelector(".export-style-count");
          styleCheckbox.addEventListener("change", () => {
            const styleGroup = styles[si];
            if (!styleGroup) return;
            if (styleCheckbox.checked) {
              selected.set(si, new Set(styleGroup.items.map((_, ii) => ii)));
            } else {
              selected.delete(si);
            }
            const set = selected.get(si);
            const selCount = set ? set.size : 0;
            if (countEl) countEl.textContent = `${selCount}/${styleGroup.items.length}`;
            groupEl.querySelectorAll(".export-item-checkbox").forEach((cb) => {
              cb.checked = styleCheckbox.checked;
            });
            styleCheckbox.indeterminate = false;
            updateStat();
          });
        });
        // 条目级勾选
        treeEl.querySelectorAll(".export-item-row").forEach((itemEl) => {
          const si = Number(itemEl.dataset.styleIdx);
          const ii = Number(itemEl.dataset.itemIdx);
          const itemCheckbox = itemEl.querySelector(".export-item-checkbox");
          itemCheckbox.addEventListener("change", () => {
            if (!selected.has(si)) selected.set(si, new Set());
            const set = selected.get(si);
            if (itemCheckbox.checked) set.add(ii);
            else set.delete(ii);
            if (set.size === 0) selected.delete(si);
            // 更新风格级 checkbox 状态
            const styleGroup = styles[si];
            const groupEl = treeEl.querySelector(`.export-style-group[data-style-idx="${si}"]`);
            if (groupEl && styleGroup) {
              const styleCheckbox = groupEl.querySelector(".export-style-checkbox");
              const countEl = groupEl.querySelector(".export-style-count");
              const selCount = set ? set.size : 0;
              const total = styleGroup.items.length;
              if (countEl) countEl.textContent = `${selCount}/${total}`;
              if (styleCheckbox) {
                styleCheckbox.checked = selCount === total && selCount > 0;
                styleCheckbox.indeterminate = selCount > 0 && selCount < total;
              }
            }
            updateStat();
          });
        });
      };

      renderTree();
      updateStat();

      // 全选/全不选
      container.querySelector("#export-select-all").addEventListener("click", () => {
        selected.clear();
        styles.forEach((s, si) => {
          selected.set(si, new Set(s.items.map((_, ii) => ii)));
        });
        renderTree();
        updateStat();
      });
      container.querySelector("#export-select-none").addEventListener("click", () => {
        selected.clear();
        renderTree();
        updateStat();
      });

      // 导出按钮
      container.querySelector("[data-modal-confirm]").addEventListener("click", async () => {
        const includePrompts = container.querySelector("#export-include-prompts").checked;
        const outfits = {};
        const probabilities = {};
        let totalCount = 0;
        selected.forEach((itemIdxSet, si) => {
          const styleGroup = styles[si];
          if (!styleGroup || !styleGroup.items) return;
          const items = [];
          itemIdxSet.forEach((ii) => {
            const item = styleGroup.items[ii];
            if (item) items.push(item);
          });
          if (items.length === 0) return;
          outfits[styleGroup.style] = items;
          probabilities[styleGroup.style] = styleGroup.probability;
          totalCount += items.length;
        });
        if (totalCount === 0) {
          toast("未勾选任何条目", "warning");
          return;
        }
        const payload = { outfits, probabilities };
        if (includePrompts) {
          payload.prompts = state.promptsData || { designer: "", reviewer: "" };
        }
        const json = JSON.stringify(payload, null, 2);
        const styleCount = Object.keys(outfits).length;
        const filename = `dayflow_curated_outfits_${new Date().toISOString().slice(0, 10)}.json`;

        // 优先用 File System Access API 让用户选保存路径
        if (window.showSaveFilePicker) {
          try {
            const fileHandle = await window.showSaveFilePicker({
              suggestedName: filename,
              types: [
                {
                  description: "JSON 文件",
                  accept: { "application/json": [".json"] },
                },
              ],
            });
            const writable = await fileHandle.createWritable();
            await writable.write(json);
            await writable.close();
            toast(`已导出 ${totalCount} 套穿搭（${styleCount} 个风格）到所选路径`, "success");
            close();
            return;
          } catch (e) {
            if (e && e.name === "AbortError") {
              // 用户取消选择，不报错
              return;
            }
            // 其他错误降级到下面的展示模式
            console.warn("showSaveFilePicker 失败，降级到展示模式:", e);
          }
        }

        // 降级：在新 modal 中展示 JSON 内容，提供复制按钮和下载按钮
        close();
        showExportResultModal(json, filename, totalCount, styleCount);
      });
    },
  });
}

// 导出结果展示 modal（降级方案：showSaveFilePicker 不可用时使用）
function showExportResultModal(json, filename, totalCount, styleCount) {
  const bodyHtml = `
    <div class="form-hint export-hint">
      当前环境不支持文件保存对话框，请复制下方 JSON 内容保存到任意位置，或点击「下载」按钮触发浏览器下载。
    </div>
    <div class="export-result-actions" style="margin-bottom:12px;display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn btn-primary btn-sm" id="export-copy-btn">复制到剪贴板</button>
      <button class="btn btn-secondary btn-sm" id="export-download-btn">下载文件</button>
    </div>
    <textarea class="form-textarea form-textarea-large export-result-textarea" id="export-result-text" readonly>${esc(json)}</textarea>
    <div class="form-hint" style="margin-top:8px">
      已选 ${totalCount} 套穿搭（${styleCount} 个风格）。建议文件名：<code>${esc(filename)}</code>
    </div>
  `;
  const footerHtml = `
    <button class="btn btn-secondary" data-modal-close>关闭</button>
  `;
  showModal({
    title: "导出结果",
    bodyHtml,
    footerHtml,
    onMount: (container, closeModal) => {
      container.querySelector("[data-modal-close]").addEventListener("click", closeModal);

      // 复制到剪贴板
      container.querySelector("#export-copy-btn").addEventListener("click", async () => {
        const textEl = container.querySelector("#export-result-text");
        try {
          await navigator.clipboard.writeText(textEl.value);
          toast("已复制到剪贴板", "success");
        } catch (e) {
          // 降级：选中文本让用户手动复制
          textEl.select();
          try {
            document.execCommand("copy");
            toast("已复制到剪贴板", "success");
          } catch {
            toast("复制失败，请手动选择文本复制", "error");
          }
        }
      });

      // 下载文件（浏览器原生下载，可能保存到默认下载目录）
      container.querySelector("#export-download-btn").addEventListener("click", () => {
        const blob = new Blob([json], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        toast("已触发浏览器下载，请查看下载目录", "success");
      });
    },
  });
}

function openImportModal() {
  const bodyHtml = `
    <div class="form-group">
      <label class="form-label">导入模式</label>
      <select id="modal-import-mode" class="form-select">
        <option value="merge">merge — 同名跳过，追加新条目</option>
        <option value="overwrite">overwrite — 同名覆盖</option>
      </select>
    </div>
    <div class="form-group">
      <label class="form-label">从文件导入</label>
      <input type="file" id="modal-import-file" class="form-input" accept="application/json,.json" />
    </div>
    <div class="form-group">
      <label class="form-label">或粘贴 JSON</label>
      <textarea id="modal-import-text" class="form-textarea" rows="10" placeholder='{"outfits": {...}, "probabilities": {...}, "prompts": {...}}'></textarea>
    </div>
    <div class="form-hint">导入会同时合并 outfits / probabilities / prompts。</div>
  `;
  const footerHtml = `
    <button class="btn btn-secondary" data-modal-cancel>取消</button>
    <button class="btn btn-primary" data-modal-confirm>导入</button>
  `;
  showModal({
    title: "导入数据",
    bodyHtml,
    footerHtml,
    onMount: (container, close) => {
      container.querySelector("[data-modal-cancel]").addEventListener("click", close);
      const confirmBtn = container.querySelector("[data-modal-confirm]");
      const modeEl = container.querySelector("#modal-import-mode");
      const fileEl = container.querySelector("#modal-import-file");
      const textEl = container.querySelector("#modal-import-text");
      confirmBtn.addEventListener("click", async () => {
        let payload = null;
        const text = textEl.value.trim();
        if (text) {
          try {
            payload = JSON.parse(text);
          } catch (e) {
            toast(`JSON 解析失败: ${e.message}`, "error");
            return;
          }
        } else if (fileEl.files && fileEl.files[0]) {
          try {
            const fileText = await fileEl.files[0].text();
            payload = JSON.parse(fileText);
          } catch (e) {
            toast(`文件读取失败: ${e.message}`, "error");
            return;
          }
        }
        if (!payload) {
          toast("请提供文件或粘贴 JSON", "warning");
          return;
        }
        confirmBtn.disabled = true;
        confirmBtn.textContent = "导入中...";
        try {
          const res = await api.post("import", { data: payload, mode: modeEl.value });
          const d = res || {};
          toast(`导入完成：新增 ${d.added || 0}，跳过 ${d.skipped || 0}，覆盖 ${d.overwritten || 0}`, "success");
          close();
          loadLibrary();
          loadOverview();
          loadStyles();
          loadPrompts();
        } catch (e) {
          toast(`导入失败: ${e.message}`, "error");
          confirmBtn.disabled = false;
          confirmBtn.textContent = "导入";
        }
      });
    },
  });
}

function updateLibraryBadge() {
  const badge = $("badge-library");
  if (!badge) return;
  const total = state.libraryData
    ? (state.libraryData.styles || []).reduce((sum, s) => sum + (s.count || 0), 0)
    : 0;
  if (total > 0) {
    badge.textContent = String(total);
    badge.style.display = "";
  } else {
    badge.style.display = "none";
  }
}

// ── 提示词 Tab ────────────────────────────────────────────────

async function loadPrompts() {
  state.promptsLoading = true;
  try {
    const res = await api.get("prompts");
    state.promptsData = res || {};
    const designerEl = $("prompt-designer");
    const reviewerEl = $("prompt-reviewer");
    if (designerEl) designerEl.value = res.designer || "";
    if (reviewerEl) reviewerEl.value = res.reviewer || "";
    // 填充默认提示词展示区
    const defaultDesignerEl = $("default-designer-prompt");
    const defaultReviewerEl = $("default-reviewer-prompt");
    if (defaultDesignerEl) defaultDesignerEl.textContent = res.default_designer || "";
    if (defaultReviewerEl) defaultReviewerEl.textContent = res.default_reviewer || "";
    // 更新状态徽章
    updatePromptStatusBadges(res);
  } catch (e) {
    toast(`加载提示词失败: ${e.message}`, "error");
  } finally {
    state.promptsLoading = false;
  }
}

function updatePromptStatusBadges(data) {
  const designerBadge = $("badge-designer-status");
  const reviewerBadge = $("badge-reviewer-status");
  if (designerBadge) {
    if (data.designer_is_default) {
      designerBadge.textContent = "使用默认";
      designerBadge.className = "badge badge-default";
    } else {
      designerBadge.textContent = "已自定义";
      designerBadge.className = "badge badge-custom";
    }
  }
  if (reviewerBadge) {
    if (data.reviewer_is_default) {
      reviewerBadge.textContent = "使用默认";
      reviewerBadge.className = "badge badge-default";
    } else {
      reviewerBadge.textContent = "已自定义";
      reviewerBadge.className = "badge badge-custom";
    }
  }
}

async function savePrompts() {
  const designerEl = $("prompt-designer");
  const reviewerEl = $("prompt-reviewer");
  const designer = designerEl ? designerEl.value : "";
  const reviewer = reviewerEl ? reviewerEl.value : "";
  const btn = $("btn-save-prompts");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "保存中...";
  }
  try {
    const res = await api.post("prompts", { designer, reviewer });
    // res 包含 message + prompts + default_*  + *_is_default
    state.promptsData = res || {};
    // 更新默认提示词展示区与状态徽章
    const defaultDesignerEl = $("default-designer-prompt");
    const defaultReviewerEl = $("default-reviewer-prompt");
    if (defaultDesignerEl && res.default_designer) defaultDesignerEl.textContent = res.default_designer;
    if (defaultReviewerEl && res.default_reviewer) defaultReviewerEl.textContent = res.default_reviewer;
    updatePromptStatusBadges(res);
    toast("提示词已保存", "success");
  } catch (e) {
    toast(`保存失败: ${e.message}`, "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "保存";
    }
  }
}

// ── 概览 Tab ──────────────────────────────────────────────────

async function loadOverview() {
  state.overviewLoading = true;
  showLoading("overview-stats", "加载概览...");
  const tbody = $("overview-tbody");
  if (tbody) tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--text-tertiary);padding:40px">加载中...</td></tr>`;
  try {
    const res = await api.get("overview");
    state.overviewData = res || { styles: [], prompts_configured: {} };
    renderOverview();
  } catch (e) {
    $("overview-stats").innerHTML = `<div class="empty-state">加载失败: ${esc(e.message)}</div>`;
  } finally {
    state.overviewLoading = false;
  }
}

function renderOverview() {
  if (!state.overviewData) return;
  const data = state.overviewData;
  const styles = data.styles || [];
  const totalStyles = styles.length;
  const totalOutfits = styles.reduce((sum, s) => sum + (s.count || 0), 0);
  const totalUseCount = styles.reduce((sum, s) => sum + (s.avg_use_count * s.count || 0), 0);
  const promptsConfigured = data.prompts_configured || {};

  const statsEl = $("overview-stats");
  statsEl.innerHTML = `
    <div class="stat-card">
      <div class="stat-value">${totalStyles}</div>
      <div class="stat-label">风格数</div>
    </div>
    <div class="stat-card">
      <div class="stat-value">${totalOutfits}</div>
      <div class="stat-label">总条目数</div>
    </div>
    <div class="stat-card">
      <div class="stat-value">${Math.round(totalUseCount)}</div>
      <div class="stat-label">总注入次数</div>
    </div>
    <div class="stat-card">
      <div class="stat-value">${(promptsConfigured.designer ? 1 : 0) + (promptsConfigured.reviewer ? 1 : 0)}/2</div>
      <div class="stat-label">提示词已配置</div>
    </div>
  `;

  const tbody = $("overview-tbody");
  if (!tbody) return;
  if (styles.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--text-tertiary);padding:40px">优秀库为空</td></tr>`;
    return;
  }
  tbody.innerHTML = styles
    .map((s) => `
      <tr>
        <td>${esc(s.style)}</td>
        <td class="col-count">${s.count}</td>
        <td class="col-prob">${(Number(s.probability || 0) * 100).toFixed(0)}%</td>
        <td class="col-num">${s.avg_use_count}</td>
        <td class="col-num">${s.min_use_count}</td>
        <td class="col-num">${s.max_use_count}</td>
      </tr>
    `)
    .join("");
}

// ── 事件绑定 ──────────────────────────────────────────────────

function setupEventListeners() {
  // 导航
  document.querySelectorAll(".nav-item[data-page]").forEach((btn) => {
    btn.addEventListener("click", () => switchPage(btn.dataset.page));
  });

  // 主题切换
  const themeBtn = $("theme-toggle");
  if (themeBtn) themeBtn.addEventListener("click", toggleTheme);

  // 日程 tab
  const refreshScheduleBtn = $("btn-refresh-schedule");
  if (refreshScheduleBtn) refreshScheduleBtn.addEventListener("click", () => {
    loadSchedulePersonas();
    if (state.scheduleSelectedPersona) loadSchedule();
  });

  const personaSelect = $("schedule-persona-select");
  if (personaSelect) personaSelect.addEventListener("change", onSchedulePersonaChange);

  const dateInput = $("schedule-date-input");
  if (dateInput) {
    // 默认设为今天
    if (!dateInput.value) dateInput.value = todayStr();
    state.scheduleSelectedDate = dateInput.value;
    dateInput.addEventListener("change", () => {
      state.scheduleSelectedDate = dateInput.value;
    });
  }

  const loadBtn = $("btn-schedule-load");
  if (loadBtn) loadBtn.addEventListener("click", () => loadSchedule());

  // 设计 tab：风格搜索
  const searchInput = $("design-style-search");
  if (searchInput) {
    searchInput.addEventListener("input", () => {
      state.styleSearchText = searchInput.value;
      renderStyleGrid();
    });
  }

  // 设计按钮
  const designBtn = $("btn-design");
  if (designBtn) designBtn.addEventListener("click", designOutfit);

  const refreshStylesBtn = $("btn-refresh-styles-design");
  if (refreshStylesBtn) refreshStylesBtn.addEventListener("click", loadStyles);

  // 优秀库 tab
  const refreshLibraryBtn = $("btn-refresh-library");
  if (refreshLibraryBtn) refreshLibraryBtn.addEventListener("click", loadLibrary);
  const exportBtn = $("btn-export");
  if (exportBtn) exportBtn.addEventListener("click", exportData);
  const importBtn = $("btn-import");
  if (importBtn) importBtn.addEventListener("click", openImportModal);

  // 提示词 tab
  const refreshPromptsBtn = $("btn-refresh-prompts");
  if (refreshPromptsBtn) refreshPromptsBtn.addEventListener("click", loadPrompts);
  const savePromptsBtn = $("btn-save-prompts");
  if (savePromptsBtn) savePromptsBtn.addEventListener("click", savePrompts);

  // 概览 tab
  const refreshOverviewBtn = $("btn-refresh-overview");
  if (refreshOverviewBtn) refreshOverviewBtn.addEventListener("click", loadOverview);
}

// ── 启动 ──────────────────────────────────────────────────────

async function init() {
  applyTheme(state.theme);
  setupEventListeners();

  const footer = $("footer-info");
  if (footer) footer.textContent = "DayFlow Studio v1.0";

  // 等待 Bridge 就绪（失败不阻断，只警告）
  try {
    await api.ready();
  } catch (e) {
    console.warn("[DayflowDesigner] Bridge 初始化警告:", e);
    if (footer) footer.textContent = "Bridge 不可用";
  }

  // 加载首页数据（日程 tab 为默认页）
  try {
    await loadSchedulePersonas();
  } catch (e) {
    console.error("[DayflowDesigner] 加载日程人格失败:", e);
  }
}

// 捕获 init 的未处理异常，避免静默卡在"加载中"
init().catch((e) => {
  console.error("[DayflowDesigner] 初始化失败:", e);
  const area = $("schedule-content-area");
  if (area) {
    area.innerHTML = `<div class="schedule-missing"><div class="schedule-missing-text">初始化失败: ${esc(e.message || String(e))}</div></div>`;
  }
});
