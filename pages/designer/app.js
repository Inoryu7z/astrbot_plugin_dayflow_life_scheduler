// 优秀穿搭库 WebUI 前端逻辑
// 对接 core/page_api.py 的 13 个路由
// 4 个 tab：设计 / 优秀库 / 提示词 / 概览
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
  currentPage: "design",
  theme: "light",
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
  if (pageName === "library" && !state.libraryData) loadLibrary();
  if (pageName === "prompts" && !state.promptsData) loadPrompts();
  if (pageName === "overview" && !state.overviewData) loadOverview();
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
  area.innerHTML = `
    <div class="card">
      <div class="card-title">${isReviewer ? "审核师修改版" : "设计师初版"}</div>
      <div class="design-result">
        <div class="design-result-name">${esc(current.name)}</div>
        <div class="design-result-description">${esc(current.description)}</div>
        <div class="design-result-actions">
          <button class="btn btn-success btn-sm" id="btn-approve">通过 — 入库</button>
          <button class="btn btn-warning btn-sm" id="btn-iterate">迭代 — 交审核师</button>
        </div>
      </div>
    </div>
  `;
  $("btn-approve").addEventListener("click", approveDesign);
  $("btn-iterate").addEventListener("click", () => openIterationModal());
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
      return `
        <div class="iteration-entry">
          <div class="iteration-entry-role ${roleClass}">${roleLabel} #${idx + 1}</div>
          <div class="iteration-entry-name">${esc(entry.name)}</div>
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
  return `
    <div class="outfit-item">
      <div class="outfit-item-header">
        <div class="outfit-name">${esc(item.name)}</div>
        <div class="outfit-meta">
          <span class="outfit-style-badge">${esc(item.style)}</span>
          <span class="outfit-use-count">使用 ${useCount} 次</span>
          ${iterations > 0 ? `<span class="text-small text-muted">迭代 ${iterations} 次</span>` : ""}
          <span class="text-small text-muted">${esc(formatDateTime(item.created_at))}</span>
        </div>
      </div>
      <div class="outfit-description">${esc(item.description)}</div>
      <div class="outfit-actions">
        <button class="btn btn-secondary btn-sm" data-action="edit" data-style="${esc(item.style)}" data-name="${esc(item.name)}">编辑</button>
        <button class="btn btn-secondary btn-sm" data-action="use-count" data-style="${esc(item.style)}" data-name="${esc(item.name)}">使用计数</button>
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
  try {
    const res = await api.get("export");
    const json = JSON.stringify(res, null, 2);
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `dayflow_curated_outfits_${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    toast("已导出", "success");
  } catch (e) {
    toast(`导出失败: ${e.message}`, "error");
  }
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
    state.promptsData = res || { designer: "", reviewer: "" };
    const designerEl = $("prompt-designer");
    const reviewerEl = $("prompt-reviewer");
    if (designerEl) designerEl.value = state.promptsData.designer || "";
    if (reviewerEl) reviewerEl.value = state.promptsData.reviewer || "";
  } catch (e) {
    toast(`加载提示词失败: ${e.message}`, "error");
  } finally {
    state.promptsLoading = false;
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
    state.promptsData = res || { designer, reviewer };
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
  if (footer) footer.textContent = "Dayflow Designer v1.0";

  // 等待 Bridge 就绪（失败不阻断，只警告）
  try {
    await api.ready();
  } catch (e) {
    console.warn("[DayflowDesigner] Bridge 初始化警告:", e);
    if (footer) footer.textContent = "Bridge 不可用";
  }

  // 加载首页数据
  try {
    await loadStyles();
  } catch (e) {
    console.error("[DayflowDesigner] 加载风格失败:", e);
  }
}

// 捕获 init 的未处理异常，避免静默卡在"加载中"
init().catch((e) => {
  console.error("[DayflowDesigner] 初始化失败:", e);
  const grid = $("design-style-grid");
  if (grid) {
    grid.innerHTML = `<div class="style-grid-empty">初始化失败: ${esc(e.message || String(e))}</div>`;
  }
});
