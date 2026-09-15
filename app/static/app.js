/* MagicerRAG 前端交互（多知识库 + 权限版） */
(() => {
  "use strict";

  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function getCookie(name) { const m = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)")); return m ? decodeURIComponent(m[1]) : ""; }

  /* ---------- Markdown 富文本渲染 + 内联来源引用 ---------- */
  // 先把正文里的 [n]（n<=命中数）替换为不会被 marked 误解的占位符，渲染后再还原成可点击上标
  function citePlaceholder(raw, hitsCount) {
    return String(raw).replace(/\[(\d{1,2})\]/g, (m, n) => {
      const i = parseInt(n, 10);
      return (i >= 1 && i <= hitsCount) ? `§magicerCite§${i}§` : m;
    });
  }
  function renderMd(raw, hitsCount) {
    let html;
    try { html = marked.parse(citePlaceholder(raw, hitsCount)); }
    catch (_) { html = esc(raw); }
    html = html.replace(/§magicerCite§(\d+)§/g, (_, i) =>
      `<sup class="cite" data-i="${i - 1}" title="点击查看来源 ${i}">${i}</sup>`);
    if (window.DOMPurify) html = DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
    return html;
  }
  function applyMd(el, raw, hitsCount) {
    el.innerHTML = renderMd(raw, hitsCount);
    if (window.hljs) $$("pre code", el).forEach((b) => { try { hljs.highlightElement(b); } catch (_) {} });
  }

  const state = {
    token: "",   // v0.5: 会话令牌改用 httpOnly Cookie，前端不再持有明文 token
    csrf: "",    // CSRF 双提交令牌（非 HttpOnly Cookie 值）
    username: localStorage.getItem("mr_user") || "",
    isAdmin: localStorage.getItem("mr_admin") === "1",
    sessions: [],
    current: null,       // 当前会话 id
    streaming: false,
    abortCtrl: null,     // 当前流式请求的 AbortController（停止生成用）
    query: "",           // 侧边栏会话搜索关键词
    theme: "dark",
    // 知识库
    datasets: [],
    currentDs: null,     // 选中的数据集 id
    files: [],           // 当前数据集的文件列表
    pendingDs: null,     // 新会话（未开始）时待绑定的知识库 id；null=全部有权限库
    refMap: new Map(),   // W3：会话消息 id -> 命中资料（供「参考资料」全局抽屉读取）
  };

  const DS_ALL = ""; // ds-select 全选标记
  let pendingHomeAction = null; // 首页动作 -> 上传后是否自动提问（upload | summarize）
  let selectedFiles = new Set(); // 文件名多选批量删除：选中的文件 id 集合
  const VIEW_KEY = "mr_view";

  const roleLabel = { owner: "所有者", manager: "管理者", member: "成员" };
  const roleShort = { owner: "主", manager: "管", member: "员" };

  /* 持久化当前界面状态，刷新后恢复到原视图 */
  function saveView() {
    let page = "main";
    if (!$("#app-view").classList.contains("hidden")) page = "main";
    else if (!$("#kb-page").classList.contains("hidden")) page = "kb";
    else if (!$("#admin-page").classList.contains("hidden")) page = "admin";
    const v = { page, session: state.current || null, ds: state.currentDs || null, adm: state.admTab || null };
    localStorage.setItem(VIEW_KEY, JSON.stringify(v));
  }
  function loadView() {
    try { return JSON.parse(localStorage.getItem(VIEW_KEY) || "null"); }
    catch (_) { return null; }
  }
  function clearView() { localStorage.removeItem(VIEW_KEY); }

  /* 图标水化：刷新页面中所有 [data-lucide] 元素（静态与动态插入后调用） */
  function hydrateIcons() {
    if (window.lucide) { try { window.lucide.createIcons(); } catch (_) {} }
  }

  /* ---------- 主题 ---------- */
  function applyTheme(t) {
    state.theme = t;
    document.documentElement.setAttribute("data-theme", t);
    localStorage.setItem("mr_theme", t);
    $("#btn-theme").innerHTML = `<i data-lucide="${t === "dark" ? "sun" : "moon"}"></i>`;
    hydrateIcons();
  }
  function initTheme() {
    const saved = localStorage.getItem("mr_theme");
    const sys = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
    state.theme = saved || sys;
    applyTheme(state.theme);
  }

  /* ---------- API ---------- */
  async function api(path, opts = {}) {
    const headers = { ...(opts.headers || {}) };
    if (state.token) headers["Authorization"] = "Bearer " + state.token;
    if (opts.body && !(opts.body instanceof FormData)) headers["Content-Type"] = "application/json";
    const method = (opts.method || "GET").toUpperCase();
    if (method !== "GET" && method !== "HEAD" && state.csrf) headers["X-CSRF-Token"] = state.csrf;
    const res = await fetch(path, { ...opts, headers });
    if (res.status === 401) { logout(); throw new Error("登录已失效"); }
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    return res;
  }
  async function apiJson(path, opts = {}) {
    const res = await api(path, opts);
    return res.json();
  }

  /* ---------- 视图 ---------- */
  function setIdentity(isAdmin) {
    state.isAdmin = !!isAdmin;
    localStorage.setItem("mr_admin", state.isAdmin ? "1" : "0");
    $("#btn-admin").classList.toggle("hidden", !state.isAdmin);
    $("#admin-badge").classList.toggle("hidden", !state.isAdmin);
  }
  function showAuth() {
    $("#auth-view").classList.remove("hidden");
    $("#app-view").classList.add("hidden");
    hideSubPages();
  }
  function showApp() {
    $("#auth-view").classList.add("hidden");
    $("#app-view").classList.remove("hidden");
    hideSubPages();
    $("#user-name").textContent = state.username;
    $("#user-ava").textContent = (state.username || "U")[0].toUpperCase();
    setIdentity(state.isAdmin);
    const saved = loadView();
    if (saved && saved.page === "kb") { if (saved.ds) state.currentDs = saved.ds; openKbPage(); }
    else if (saved && saved.page === "admin") { openAdminPage(); if (saved.adm) switchAdm(saved.adm); }
    else { if (saved && saved.session) { state.current = saved.session; state._restore = true; } loadSessions(); }
    loadDescriptors();
  }
  function hideSubPages() {
    $("#kb-page").classList.add("hidden");
    $("#admin-page").classList.add("hidden");
  }
  function logout() {
    if (state.abortCtrl) state.abortCtrl.abort();
    state.token = ""; state.username = ""; state.isAdmin = false;
    state.csrf = "";
    localStorage.removeItem("mr_user"); localStorage.removeItem("mr_admin");
    // Cookie 会话：登出由服务端撤销并清除 Cookie
    fetch("/api/auth/logout", { method: "POST", credentials: "same-origin", headers: state.csrf ? { "X-CSRF-Token": state.csrf } : {} }).catch(() => {});
    clearView();               // 不同用户不共享界面状态
    selectedFiles.clear();
    resetChat();               // 清空消息区，避免下一用户登录后看到上一位的对话
    showAuth();
  }
  function setSession(data) {
    state.username = data.user.username;
    state.csrf = getCookie("mr_csrf");
    localStorage.setItem("mr_user", data.user.username);
    setIdentity(data.user.is_admin);
  }

  /* ---------- 认证 ---------- */
  const authErr = () => $("#auth-error");
  $$(".auth-tab").forEach((tab) => tab.addEventListener("click", () => {
    $$(".auth-tab").forEach((t) => t.classList.toggle("active", t === tab));
    const mode = tab.dataset.tab;
    $("#login-form").classList.toggle("hidden", mode !== "login");
    $("#register-form").classList.toggle("hidden", mode !== "register");
    authErr().textContent = "";
  }));

  /* 密码可见性切换：点击眼睛图标在 password/text 间切换 */
  $$(".pw-wrap").forEach((wrap) => {
    const inp = wrap.querySelector("input");
    const btn = wrap.querySelector(".pw-toggle");
    if (!inp || !btn) return;
    btn.addEventListener("click", () => {
      const show = inp.type === "password";
      inp.type = show ? "text" : "password";
      btn.title = show ? "隐藏密码" : "显示密码";
      btn.setAttribute("aria-label", btn.title);
      btn.innerHTML = `<i data-lucide="${show ? "eye-off" : "eye"}"></i>`;
      hydrateIcons();
    });
  });

  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    authErr().textContent = "";
    const fd = new FormData(e.target);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: fd.get("username"), password: fd.get("password") }),
      });
      if (!res.ok) { authErr().textContent = (await res.json()).detail; return; }
      setSession(await res.json());
      showApp();
    } catch (err) { authErr().textContent = err.message; }
  });

  $("#register-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    authErr().textContent = "";
    const fd = new FormData(e.target);
    try {
      const res = await fetch("/api/auth/register", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: fd.get("username"), password: fd.get("password"), invite_code: fd.get("invite") }),
      });
      if (!res.ok) { authErr().textContent = (await res.json()).detail; return; }
      setSession(await res.json());
      showApp();
    } catch (err) { authErr().textContent = err.message; }
  });

  /* ---------- 会话 ---------- */
  async function loadSessions() {
    try {
      // N9：会话分组名依赖知识库名称 —— 先确保 datasets 就绪，首屏即显示正确名而非"知识库 #id"
      if (state.datasets.length === 0) await loadDescriptors();
      const res = await api("/api/chats/sessions");
      state.sessions = await res.json();
      renderSessions();
    } catch (err) { toast(err.message, true); }
  }

  /* 全局统一 UTC+8：后端存的是东八区无时区时间戳，这里按 +08:00 解析并取东八区墙钟（不受浏览器时区影响） */
  function utc8Parts(t) {
    const shift = new Date(t.getTime() + 8 * 3600 * 1000);
    const two = (n) => String(n).padStart(2, "0");
    return {
      iso: `${shift.getUTCFullYear()}-${two(shift.getUTCMonth() + 1)}-${two(shift.getUTCDate())}`,
      mo: shift.getUTCMonth() + 1, da: shift.getUTCDate(),
      hh: two(shift.getUTCHours()), mm: two(shift.getUTCMinutes()),
    };
  }
  function utc8Now() { return utc8Parts(new Date()); }
  function fmtUtc8(raw, withYear) {
    if (!raw) return "";
    const d = new Date(raw + "+08:00");
    if (isNaN(d)) return "";
    const p = utc8Parts(d);
    return withYear ? `${p.iso} ${p.hh}:${p.mm}` : `${String(p.mo).padStart(2, "0")}/${String(p.da).padStart(2, "0")} ${p.hh}:${p.mm}`;
  }

  function timeLabel(s) {
    const raw = (s && (s.created_at || s.uploaded_at || s.last_active)) || "";
    if (!raw) return "";
    const d = new Date(raw + "+08:00");
    if (isNaN(d)) return "";
    const now = utc8Now();
    const p = utc8Parts(d);
    const two = (n) => String(n).padStart(2, "0");
    if (p.iso === now.iso) return `${p.hh}:${p.mm}`;
    const yest = new Date(Date.now() - 86400000);
    if (p.iso === utc8Parts(yest).iso) return "昨天";
    return `${two(p.mo)}/${two(p.da)}`;
  }

  // N8：知识库名字映射（个人库显示"我的知识库"；未绑定=全部知识库）
  function dsName(did) {
    if (did == null) return "全部知识库";
    const d = (state.datasets || []).find((x) => x.id === did);
    if (!d) return "知识库 #" + did;
    return d.type === "personal" ? "我的知识库" : d.name;
  }
  function dsIco(did) {
    if (did == null) return "layers";
    const d = (state.datasets || []).find((x) => x.id === did);
    return d && d.type === "team" ? "users" : "database";
  }
  // 单个会话条目（供分组内渲染）
  function sessionItemNode(s) {
    const el = document.createElement("div");
    el.className = "session-item" + (s.id === state.current ? " active" : "");
    el.dataset.id = s.id;
    el.innerHTML = `<span class="s-title">${esc(s.title)}</span><span class="s-time">${esc(timeLabel(s))}</span><span class="s-actions"><button class="s-ren" title="重命名" data-ren="${s.id}"><i data-lucide="pencil"></i></button><button class="s-del" title="删除"><i data-lucide="x"></i></button></span>`;
    el.addEventListener("click", (e) => { if (!e.target.closest(".s-ren") && !e.target.closest(".s-del")) openSession(s.id); });
    el.querySelector(".s-ren").addEventListener("click", (e) => { e.stopPropagation(); startRename(el, s); });
    el.querySelector(".s-del").addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm("删除该会话及其全部消息？")) return;
      await api("/api/chats/sessions/" + s.id, { method: "DELETE" });
      if (state.current === s.id) { state.current = null; resetChat(); }
      loadSessions();
    });
    return el;
  }
  function renderSessions() {
    const list = $("#session-list");
    list.innerHTML = "";
    const kw = state.query.trim().toLowerCase();
    const items = kw
      ? state.sessions.filter((s) => s.title.toLowerCase().includes(kw))
      : state.sessions;
    if (!kw) {
      // N8：按会话绑定的知识库分组（两级：知识库 → 会话）
      const groups = new Map(); // did(null=全部) -> {label, ico, items[]}
      items.forEach((s) => {
        const k = s.dataset_id ?? "__all__";
        if (!groups.has(k)) groups.set(k, { label: dsName(s.dataset_id), ico: dsIco(s.dataset_id), items: [] });
        groups.get(k).items.push(s);
      });
      groups.forEach((g) => {
        const groupEl = document.createElement("div");
        groupEl.className = "session-group";
        groupEl.innerHTML = `<div class="session-group-head"><i data-lucide="${g.ico}"></i><span class="g-label">${esc(g.label)}</span><span class="g-count">${g.items.length}</span></div><div class="session-group-body"></div>`;
        const body = groupEl.querySelector(".session-group-body");
        g.items.forEach((s) => body.appendChild(sessionItemNode(s)));
        list.appendChild(groupEl);
      });
    } else {
      items.forEach((s) => list.appendChild(sessionItemNode(s)));
    }
    hydrateIcons();
    if (state._restore) {
      state._restore = false;
      if (state.current && state.sessions.some((s) => s.id === state.current)) openSession(state.current);
      else if (state.sessions.length) { state.current = null; openSession(state.sessions[0].id); }
    } else if (!state.current && state.sessions.length) { openSession(state.sessions[0].id); }
  }

  // 行内重命名：标题原地变为输入框，Enter 保存 / Esc 取消 / 失焦保存
  async function startRename(el, s) {
    const titleEl = el.querySelector(".s-title");
    const input = document.createElement("input");
    input.className = "session-rename-input";
    input.value = s.title;
    titleEl.replaceWith(input);
    input.focus(); input.select();
    const commit = async (save) => {
      if (input.dataset.done) return;
      input.dataset.done = "1";
      const v = input.value.trim();
      if (save && v && v !== s.title) {
        try {
          await api(`/api/chats/sessions/${s.id}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title: v }),
          });
          s.title = v;
          if (state.current === s.id) $("#session-title").textContent = v;
        } catch (err) { toast(err.message, true); }
      }
      loadSessions();
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") commit(true);
      else if (e.key === "Escape") commit(false);
    });
    input.addEventListener("blur", () => commit(true));
  }

  $("#btn-new-session").addEventListener("click", () => {
    if (state.streaming) return;
    state.current = null;       // 不立即新建会话，先回到问答主页
    resetChat();                // 清空消息区，回到起始态
    updateComposerDs();         // N9：新话题 → 重新可选知识库
    renderSessionsHighlights(); // 取消侧边栏高亮
    saveView();
  });

  const sessionSearch = $("#session-search");
  if (sessionSearch) sessionSearch.addEventListener("input", (e) => { state.query = e.target.value; renderSessions(); });

  async function openSession(id) {
    if (state.abortCtrl) state.abortCtrl.abort();
    state.current = id;
    saveView();
    renderSessionsHighlights();
    const s = state.sessions.find((x) => x.id === id);
    // N9：进入会话 → 显示锁定的绑定库标签（不重新选择）
    updateComposerDs();
    $("#session-title").textContent = s ? s.title : "会话";
    setSessionTools(true);
    await renderMessages(id);
  }
  function renderSessionsHighlights() {
    $$(".session-item").forEach((el) => el.classList.toggle("active", Number(el.dataset.id) === state.current));
  }

  async function renderMessages(id) {
    const list = $("#message-list");
    list.innerHTML = "";
    try {
      const msgs = await apiJson(`/api/chats/sessions/${id}/messages`);
      if (!msgs.length) { list.appendChild(emptyState()); hydrateIcons(); return; }
      msgs.forEach((m) => list.appendChild(renderMessage(m)));
      hydrateIcons();
      scrollBottom();
    } catch (err) { list.appendChild(emptyState()); hydrateIcons(); }
  }
  function resetChat() {
    const list = $("#message-list");
    list.innerHTML = "";
    list.appendChild(emptyState());
    hydrateIcons();
    $("#session-title").textContent = "新会话";
    setSessionTools(false);
  }

  /* R-09：会话导出 / 只读分享 */
  function setSessionTools(show) {
    $("#btn-export-session").classList.toggle("hidden", !show);
    $("#btn-share-session").classList.toggle("hidden", !show);
  }
  async function exportSession() {
    if (!state.current) return;
    try {
      const res = await api(`/api/chats/sessions/${state.current}/export`);
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = (($("#session-title").textContent || "session").replace(/[\\/:*?"<>|]/g, "_")) + ".md";
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 2000);
      toast("会话已导出为 Markdown");
    } catch (err) { toast("导出失败：" + err.message, true); }
  }
  async function shareSession() {
    if (!state.current) return;
    try {
      const d = await apiJson(`/api/chats/sessions/${state.current}/share`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ days: 7 }),
      });
      const url = location.origin + d.url;
      if (navigator.clipboard) navigator.clipboard.writeText(url).then(() => toast("分享链接已复制")).catch(() => prompt("分享链接（也可手动复制）：", url));
      else prompt("分享链接（也可手动复制）：", url);
    } catch (err) { toast("生成分享链接失败：" + err.message, true); }
  }
  $("#btn-export-session").addEventListener("click", exportSession);
  $("#btn-share-session").addEventListener("click", shareSession);

  function dsPickerHTML() {
    const chip = (val, label, ico) => {
      const isAll = val === "";
      const active = state.pendingDs == null
        ? isAll
        : (!isAll && state.pendingDs === Number(val));
      return `<button type="button" class="ds-chip${active ? " active" : ""}" data-ds="${val}" title="${esc(label)}"><i data-lucide="${ico}"></i><span>${esc(label)}</span></button>`;
    };
    const list = [chip("", "全部知识库", "layers")];
    state.datasets.forEach((d) => {
      list.push(chip(String(d.id), d.type === "personal" ? "我的知识库" : d.name, d.type === "personal" ? "book-open" : "users"));
    });
    return `<div class="ds-pick"><div class="ds-pick-label">从哪个知识库开始对话？</div><div class="ds-pick-chips">${list.join("")}</div></div>`;
  }

  function renderDsSelectedState() {
    $$("#message-list .ds-chip").forEach((c) => {
      const v = c.dataset.ds === "" ? null : Number(c.dataset.ds);
      const cur = state.pendingDs == null ? null : state.pendingDs;
      c.classList.toggle("active", cur === v);
    });
  }

  // W1：空会话选中知识库后，基于库名生成针对性引导问题（点击即发问）
  function fillStarters(host, dsId) {
    if (!host) return;
    const d = state.datasets.find((x) => x.id === dsId);
    const name = d ? (d.type === "personal" ? "我的知识库" : d.name) : "全部知识库";
    const qs = [
      `${name}里有哪些资料？`,
      `帮我总结一下${name}的主要内容`,
      `根据${name}的资料，我该重点关注什么？`,
    ];
    host.classList.remove("hidden");
    host.innerHTML = `<div class="recs-label">可以试试问：</div>` +
      qs.map((q) => `<button class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("");
    hydrateIcons();
  }

  function emptyState() {
    const el = document.createElement("div");
    el.className = "empty-state";
    el.innerHTML = `<div class="empty-logo"><i data-lucide="sparkles"></i></div><p class="empty-title">你好 👋 今天想让 MagicerRAG 帮你做什么？</p><p class="empty-sub">上传你的资料，让 AI 帮你记住、理解、整理和使用</p><div class="home-actions"><button class="home-action" data-act="upload"><span class="ha-ico"><i data-lucide="file-up"></i></span><span>上传资料</span></button><button class="home-action" data-act="ask"><span class="ha-ico"><i data-lucide="messages-square"></i></span><span>问我的资料</span></button><button class="home-action" data-act="summarize"><span class="ha-ico"><i data-lucide="file-text"></i></span><span>总结文档</span></button><button class="home-action" data-act="search"><span class="ha-ico"><i data-lucide="search"></i></span><span>搜索资料</span></button></div><div class="home-recent" id="home-recent"></div>`;
    // N9：新话题（尚未进入会话）时，在空状态中央展示知识库选择卡片
    if (!state.current) {
      const wrap = document.createElement("div");
      wrap.innerHTML = dsPickerHTML();
      const node = wrap.firstElementChild;
      const starter = document.createElement("div");
      starter.className = "empty-starters hidden";
      node.addEventListener("click", (e) => {
        const c = e.target.closest(".ds-chip");
        if (!c) return;
        state.pendingDs = c.dataset.ds === "" ? null : Number(c.dataset.ds);
        const sel = $("#ds-select");
        if (sel) sel.value = state.pendingDs == null ? DS_ALL : String(state.pendingDs);
        renderDsSelectedState();
        fillStarters(starter, state.pendingDs); // W1：选中库后给出针对性引导问题
      });
      el.appendChild(node);
      el.appendChild(starter);
    }
    populateHomeRecent(el);
    return el;
  }

  async function populateHomeRecent(host) {
    const box = host.querySelector(".home-recent");
    if (!box) return;
    const parts = [];
    for (const d of state.datasets.slice(0, 3)) {
      try {
        const files = await apiJson(`/api/kb/datasets/${d.id}/files`);
        files.slice(0, 2).forEach((f) => parts.push({ kind: "file", id: f.id, title: f.filename, icon: "file-text" }));
      } catch (_) {}
    }
    (state.sessions || []).slice(0, 3).forEach((s) => parts.push({ kind: "session", id: s.id, title: s.title, icon: "message-square-text" }));
    if (!parts.length) {
      box.innerHTML = `<div class="recent-empty">上传资料后，这里会展示你的最近使用</div>`;
      return;
    }
    const seen = new Set(), items = [];
    for (const p of parts) { if (!seen.has(p.title)) { seen.add(p.title); items.push(p); } }
    box.innerHTML = `<div class="recent-label">最近使用</div>` + items.slice(0, 6).map((p) => `<button class="recent-item" data-kind="${p.kind}" data-id="${p.id}" data-title="${esc(p.title)}"><i data-lucide="${p.icon}"></i><span>${esc(p.title)}</span></button>`).join("");
    hydrateIcons();
  }

  async function uploadToMyKnowledge(files, thenAsk) {
    if (!files || !files.length) { toast("请选择文件", true); return; }
    if (!state.datasets.length) { try { await loadDescriptors(); } catch (_) {} }
    const personal = state.datasets.find((d) => d.type === "personal");
    if (!personal) { toast("请先进入「我的知识」", true); return; }
    let ok = 0;
    for (const f of Array.from(files)) {
      const fd = new FormData();
      fd.append("file", f); fd.append("dataset_id", String(personal.id));
      try { await api("/api/kb/upload", { method: "POST", body: fd }); ok++; }
      catch (err) { toast(`${f.name} 上传失败：${err.message}`, true); }
    }
    if (ok > 0) {
      try { const data = await apiJson(`/api/kb/datasets/${personal.id}/rebuild`, { method: "POST" }); toast(`已上传 ${ok} 个文件并完成阅读，你可以问我任何关于这份资料的问题`); }
      catch (err) { toast(`文件已上传，但阅读失败：${err.message}`, true); }
      loadDsIndicator();
      if (thenAsk) { $("#input").value = "请总结这份文档"; $("#input").dispatchEvent(new Event("input")); send(); }
    } else {
      toast("没有文件上传成功", true);
    }
  }

  function renderMessage(m) {
    const el = document.createElement("div");
    el.className = "msg " + m.role;
    el.dataset.id = m.id;
    const isUser = m.role === "user";
    const avatar = isUser ? (state.username ? state.username[0].toUpperCase() : "U") : "AI";
    const think = (m.role === "assistant" && m.reasoning) ? thinkHtml(m.reasoning) : "";
    let inner = `<div class="msg-avatar">${avatar}</div><div class="msg-body">${think}<div class="msg-content"></div>`;
    if (m.role === "assistant") inner += msgActionsHtml();
    // W3：来源不再内嵌，改为「参考资料 N」触发按钮打开右侧抽屉
    if (m.role === "assistant" && m.hits && m.hits.length) {
      _registerRefs(m.id, m.hits);
      inner += _refTriggerHtml(_filesCount(m.hits));
    }
    if (m.role === "assistant") inner += followUpsHtml();
    inner += `</div>`;
    el.innerHTML = inner;
    const contentEl = $(".msg-content", el);
    if (isUser) contentEl.textContent = m.content || "";
    else applyMd(contentEl, m.content || "", (m.hits || []).length);
    return el;
  }

  function thinkHtml(text) {
    // 推理型模型（deepseek-flash）：思考链折叠展示，与正文分离（WeKnora 风格）
    const body = text ? esc(text).replace(/\n/g, "<br>") : "";
    return `<details class="think"><summary><i data-lucide="brain"></i> 思考过程</summary><div class="think-body">${body}</div></details>`;
  }

  function msgActionsHtml() {
    return `<div class="msg-actions"><button class="ma-btn" data-act="up" title="赞"><i data-lucide="thumbs-up"></i></button><button class="ma-btn" data-act="down" title="踩"><i data-lucide="thumbs-down"></i></button><button class="ma-btn" data-act="copy" title="复制"><i data-lucide="copy"></i></button></div>`;
  }

  function followUpsHtml(questions) {
    // W1：动态推荐追问优先；无（历史消息/被关闭）时回退硬编码三问
    const qs = Array.isArray(questions) && questions.length ? questions.slice(0, 3) : null;
    const chips = qs
      ? qs.map((q) => `<button class="chip" data-q="${esc(String(q))}">${esc(String(q))}</button>`).join("")
      : `<button class="chip" data-q="帮我总结一下这份资料">帮我总结一下</button><button class="chip" data-q="我该重点关注哪些内容？">该重点看什么？</button><button class="chip" data-q="根据这些资料生成一份学习笔记">生成学习笔记</button>`;
    return `<div class="recs"><span class="recs-label">你还可以问：</span>${chips}</div>`;
  }

  function hitsHtml(hits) {
    // W3：来源卡改为全局「参考资料」抽屉填充；此函数生成抽屉内聚合卡片（含命中高亮定位所需的 data-hit）
    const byFile = new Map();
    const segCount = {};
    hits.forEach((hit, i) => {
      const k = hit.source || "来源";
      if (!byFile.has(k)) { byFile.set(k, []); segCount[k] = 0; }
      segCount[k] += 1;
      byFile.get(k).push({ i, hit });
    });
    let h = `<div class="hits">`;
    byFile.forEach((segs, source) => {
      const first = segs[0].i;
      const hit = segs[0].hit;
      h += `<div class="hit-card" data-i="${first}"><div class="hit-head">`;
      h += `<span class="hit-doc"><i data-lucide="file-text"></i></span><span class="hit-file">${esc(source)}</span>`;
      if (segs.length > 1) h += `<span class="hit-count">${segs.length} 段</span>`;
      h += `<span class="hit-score">${(hit.score * 100).toFixed(1)}% <i data-lucide="chevron-right" class="hit-chev"></i></span>`;
      h += `</div><div class="hit-body">`;
      segs.forEach(({ i, hit: s }) => {
        h += `<div class="hit-seg" data-hit="${i}">${esc(s.content || "")}</div>`;
      });
      h += `</div></div>`;
    });
    h += `</div>`;
    return h;
  }

  // W3：汇总命中按文件去重后的来源数量
  function _filesCount(hits) {
    return new Set((hits || []).map((x) => x.source)).size;
  }

  // W3：消息里的「参考资料 N」触发按钮（点击打开右侧抽屉）
  function _refTriggerHtml(n) {
    return `<button type="button" class="cite-trigger" data-ref-open><i data-lucide="message-square-quote"></i> 参考资料 ${n || 0}</button>`;
  }

  function _registerRefs(id, hits) {
    if (id != null && hits && hits.length) state.refMap.set(String(id), hits);
  }

  // W3：打开右侧「参考资料」抽屉；hitIdx 为可选的命中下标（内联 [n] 点击时传入）
  function openRef(id, hitIdx, query) {
    const hits = state.refMap.get(String(id));
    if (!hits || !hits.length) return;
    const body = $("#ref-body"), drawer = $("#ref-drawer");
    if (!body || !drawer) return;
    body.innerHTML = hitsHtml(hits);
    hydrateIcons();
    $("#ref-title").textContent = `参考资料（${_filesCount(hits)}）`;
    drawer.classList.add("open"); $("#ref-overlay").classList.remove("hidden");
    document.body.classList.add("no-scroll");
    if (hitIdx != null) {
      const seg = body.querySelector(`.hit-seg[data-hit="${hitIdx}"]`);
      const card = (seg && seg.closest(".hit-card")) || body.querySelector(`.hit-card[data-i="${hitIdx}"]`);
      if (card) card.classList.add("open");
      if (seg) {
        if (query) highlightHit(seg, query);
        seg.scrollIntoView({ behavior: "smooth", block: "nearest" });
        setTimeout(() => { try { seg.scrollIntoView({ behavior: "smooth", block: "nearest" }); } catch (_) {} }, 60);
      }
    }
  }

  function closeRef() {
    const drawer = $("#ref-drawer"), overlay = $("#ref-overlay");
    if (drawer) drawer.classList.remove("open");
    if (overlay) overlay.classList.add("hidden");
    document.body.classList.remove("no-scroll");
  }

  // W2：RAG 分阶段进度条。st ∈ retrieve|generate|error，按检索→生成逐段点亮
  const PIPELINE_STEPS = ["retrieve", "generate"];
  function showPipelineBar(st) {
    const bar = $("#pipeline-bar");
    if (!bar) return;
    bar.classList.remove("hidden");
    if (st === "error") { bar.classList.add("error"); return; }
    bar.classList.remove("error");
    const cur = st === "generate" ? 1 : 0; // 0=检索中, 1=生成中
    bar.querySelectorAll(".pipe-step").forEach((s) => {
      const i = PIPELINE_STEPS.indexOf(s.dataset.st);
      s.classList.toggle("done", i < cur);   // 前置阶段已完成
      s.classList.toggle("active", i === cur); // 当前阶段点亮
    });
  }

  function hidePipelineBar() {
    const bar = $("#pipeline-bar");
    if (bar) bar.classList.add("hidden", "error");
  }

  function _tokenize(t) {
    return (t.toLowerCase().match(/[\w\u4e00-\u9fff]+/g) || []).filter(Boolean);
  }

  // P1-2：在来源卡片内定位与当前问题最相关的一段，用于段落级高亮
  function _bestSpan(text, query) {
    const qterms = new Set(_tokenize(query || ""));
    if (!qterms.size) return null;
    const parts = text.split(/(?<=[。！？!?；;.\n])\s*/);
    let best = null, bestScore = -1;
    for (const sent of parts) {
      const terms = _tokenize(sent);
      if (!terms.length) continue;
      let score = 0;
      for (const t of terms) if (qterms.has(t)) score += 1 + (t.length > 1 ? 1 : 0);
      if (score > bestScore) { bestScore = score; best = sent; }
    }
    if (bestScore <= 0) return null;
    const start = text.indexOf(best);
    return { start, end: start + best.length };
  }

  function highlightHit(seg, query) {
    seg.querySelectorAll("mark.cite-hit").forEach((m) => m.replaceWith(document.createTextNode(m.textContent)));
    seg.normalize();
    const text = seg.textContent || "";
    const span = _bestSpan(text, query);
    if (!span) return;
    const mark = document.createElement("mark");
    mark.className = "cite-hit";
    mark.textContent = text.slice(span.start, span.end);
    seg.replaceChildren(document.createTextNode(text.slice(0, span.start)), mark, document.createTextNode(text.slice(span.end)));
    mark.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  $("#message-list").addEventListener("click", (e) => {
    // W3：「参考资料 N」按钮 → 打开该消息的右侧抽屉
    const openBtn = e.target.closest("[data-ref-open]");
    if (openBtn) {
      const msg = openBtn.closest(".msg");
      if (msg && msg.dataset.id) openRef(msg.dataset.id, null, "");
      return;
    }
    const cite = e.target.closest(".cite");
    if (cite) {
      // W3：点击正文内联引用 → 打开该消息抽屉并定位到命中片段
      const msg = cite.closest(".msg");
      const i = cite.dataset.i;
      const userMsg = msg && msg.previousElementSibling;
      const qel = userMsg && userMsg.matches(".msg.user") ? userMsg.querySelector(".msg-content") : null;
      if (msg && msg.dataset.id) openRef(msg.dataset.id, Number(i), qel ? qel.textContent : "");
    }
  });

  // W3：抽屉内来源卡展开 / 关闭
  $("#ref-body").addEventListener("click", (e) => {
    const head = e.target.closest(".hit-head");
    if (head) { const card = head.closest(".hit-card"); card.classList.toggle("open"); }
  });
  $("#ref-drawer").addEventListener("click", (e) => {
    if (e.target.closest("[data-ref-close]")) closeRef();
  });
  $("#ref-overlay").addEventListener("click", closeRef);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeRef(); });

  // 消息操作：点赞 / 点踩 / 复制
  $("#message-list").addEventListener("click", (e) => {
    const btn = e.target.closest(".ma-btn");
    if (!btn) return;
    const act = btn.dataset.act;
    const contentEl = btn.closest(".msg")?.querySelector(".msg-content");
    if (act === "copy") {
      const text = contentEl ? contentEl.innerText : "";
      navigator.clipboard && navigator.clipboard.writeText(text).then(() => toast("已复制")).catch(() => {});
    } else {
      const on = btn.classList.toggle("on");
      toast(act === "up" ? (on ? "已点赞" : "已取消") : (on ? "已点踩" : "已取消"));
    }
  });

  // 侧栏折叠
  $("#btn-collapse").addEventListener("click", () => {
    const collapsed = $("#app-view").classList.toggle("sidebar-collapsed");
    $("#btn-collapse").innerHTML = `<i data-lucide="${collapsed ? "panel-left-open" : "panel-left-close"}"></i>`;
    hydrateIcons();
  });

  /* ---------- 输入与流式问答 ---------- */
  const input = $("#input");
  const sendBtn = $("#btn-send");

  function stopGeneration() { if (state.abortCtrl) state.abortCtrl.abort(); }
  sendBtn.addEventListener("click", () => {
    if (state.streaming) stopGeneration();
    else send();
  });

  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 180) + "px";
    sendBtn.disabled = !input.value.trim() || state.streaming;
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  });

  // 首页建议词：点击填入并直接发送
  document.addEventListener("click", (e) => {
    const chip = e.target.closest(".chip[data-q]");
    if (!chip) return;
    const q = (chip.dataset.q || "").trim();
    if (!q) return;
    $("#input").value = q;
    $("#input").dispatchEvent(new Event("input"));
    send();
  });

  // 首页快捷功能 + 最近使用
  $("#btn-attach").addEventListener("click", () => { pendingHomeAction = "upload"; $("#home-file-input").click(); });
  $("#home-file-input").addEventListener("change", () => {
    uploadToMyKnowledge($("#home-file-input").files, pendingHomeAction === "summarize");
    $("#home-file-input").value = "";
    pendingHomeAction = null;
  });
  document.addEventListener("click", (e) => {
    const act = e.target.closest(".home-action");
    if (act) {
      const a = act.dataset.act;
      if (a === "upload" || a === "summarize") { pendingHomeAction = a; $("#home-file-input").click(); }
      else { $("#input").focus(); if (a === "ask") $("#input").placeholder = "问问你的资料…"; }
      return;
    }
    const item = e.target.closest(".recent-item");
    if (item) {
      if (item.dataset.kind === "session" && item.dataset.id) { openSession(Number(item.dataset.id)); }
      else { $("#input").value = `请介绍一下《${item.dataset.title || ""}》`; $("#input").dispatchEvent(new Event("input")); send(); }
    }
  });

  /* 从问题首句话提取会话标题核心内容 */
  function deriveTitle(q) {
    const first = String(q).split(/[。！？!?\n]/, 1)[0].trim();
    const t = first.length > 20 ? first.slice(0, 20) + "…" : first;
    return t || "新会话";
  }

  async function send() {
    const q = input.value.trim();
    if (!q || state.streaming) return;
    input.value = ""; input.style.height = "auto"; sendBtn.disabled = true;

    // N9：仅新建会话时选知识库 —— 绑定待选 pendingDs（空=全部有权限库）
    const sel = $("#ds-select");
    if (!state.current && sel) {
      const v = sel.value;
      state.pendingDs = v !== DS_ALL ? Number(v) : null;
    }
    const bindD = state.pendingDs;

    if (!state.current) {
      try {
        const s = await apiJson("/api/chats/sessions", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: deriveTitle(q), dataset_id: bindD }),
        });
        state.sessions.unshift(s);
        state.current = s.id;
        setSessionTools(true);
        renderSessions();
        updateComposerDs();       // N9：进入会话 → 锁定为该库
        $("#session-title").textContent = s.title;
      } catch (err) {
        input.value = q;
        sendBtn.disabled = false;
        sendBtn.classList.remove("loading");
        toast(err.message || "创建会话失败", true);
        return;
      }
    }

    const list = $("#message-list");
    if ($(".empty-state", list)) list.innerHTML = "";
    list.appendChild(renderMessage({ role: "user", content: q }));

    const ans = document.createElement("div");
    ans.className = "msg assistant";
    ans.innerHTML = `<div class="msg-avatar">AI</div><div class="msg-body"><div class="msg-content"><span class="typing"><span></span><span></span><span></span></span></div></div>`;
    list.appendChild(ans);
    hydrateIcons();
    scrollBottom();

    const contentEl = $(".msg-content", ans);
    state.streaming = true;
    sendBtn.classList.add("loading");
    sendBtn.innerHTML = `<i data-lucide="square"></i>`; // 发送→停止
    hydrateIcons();
    state.abortCtrl = new AbortController();

    // N8：续聊不再传 dataset —— 后端按会话绑定库检索
    const payload = { session_id: state.current, question: q };

    try {
      const res = await fetch("/api/chats/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf },
        body: JSON.stringify(payload),
        signal: state.abortCtrl.signal,
        credentials: "same-origin",   // Cookie 会话自动携带
      });
      if (!res.ok) {
        if (res.status === 401) { logout(); return; }
        const detail = (await res.json()).detail || "请求失败";
        contentEl.textContent = "请求失败：" + detail;
        toast(detail, true);
        return;
      }
      await consumeSSE(res.body, contentEl, ans);
    } catch (err) {
      if (err.name === "AbortError") {
        contentEl.textContent = (contentEl.textContent || "") + "\n\n[已停止生成]";
      } else {
        contentEl.textContent = "发生了错误：" + err.message;
        toast(err.message, true);
      }
    } finally {
      state.streaming = false;
      state.abortCtrl = null;
      sendBtn.classList.remove("loading");
      sendBtn.innerHTML = `<i data-lucide="send"></i>`;
      hydrateIcons();
      sendBtn.disabled = !input.value.trim();
      scrollBottom();
    }
  }

  async function consumeSSE(body, contentEl, ansEl) {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let answer = "";
    let hits = [];
    let hitsShown = false;
    let suggestion = null; // W1:suggest 事件带来的推荐追问（done 渲染时优先使用）
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const events = buf.split("\n\n");
      buf = events.pop();
      for (const block of events) {
        const ev = parseSSE(block);
        if (!ev) continue;
        if (ev.event === "retrieval") {
          hits = ev.data.hits || [];
          if (!hitsShown) {
            hitsShown = true;
            // W3：命中后不内嵌来源，改为插入「参考资料 N」触发器
            if (hits.length) {
              const body = $(".msg-body", ansEl);
              body.insertAdjacentHTML("beforeend", _refTriggerHtml(_filesCount(hits)));
              hydrateIcons();
            }
          }
        } else if (ev.event === "reasoning") {
          // 阶段20：推理型模型的思考链单独折叠展示，与正文分离
          const body = $(".msg-body", ansEl);
          if (body) {
            let tb = body.querySelector(".think");
            if (!tb) {
              body.insertAdjacentHTML("afterbegin", thinkHtml(""));
              tb = body.querySelector(".think");
              hydrateIcons();
            }
            const tbody = tb.querySelector(".think-body");
            tbody.textContent = (tbody.textContent || "") + (ev.data.text || "");
            scrollBottom(true);
          }
        } else if (ev.event === "delta") {
          answer += ev.data.text || "";
          contentEl.textContent = cursorSuffix(answer);
          scrollBottom(true);
        } else if (ev.event === "stage") {
          const st = ev.data.stage;
          if (st === "error") { showPipelineBar("error"); setTimeout(hidePipelineBar, 2600); }
          else showPipelineBar(st); // W2：检索中 / 生成中
        } else if (ev.event === "suggest") {
          suggestion = ev.data.questions || null;
        } else if (ev.event === "done") {
          answer = ev.data.answer || answer;
          setTimeout(hidePipelineBar, 300); // W2：回答完成收起阶段条
          if (ev.data.message_id != null) {
            ansEl.dataset.id = ev.data.message_id;
            _registerRefs(ev.data.message_id, hits);
          }
          applyMd(contentEl, answer, hits.length);
          // 流式结束补上操作行（与历史消息一致）
          if (!ansEl.querySelector(".msg-actions")) {
            contentEl.insertAdjacentHTML("afterend", msgActionsHtml());
            hydrateIcons();
          }
          if (!ansEl.querySelector(".recs")) {
            ansEl.querySelector(".msg-body").insertAdjacentHTML("beforeend", followUpsHtml(suggestion));
            hydrateIcons();
          }
        }
      }
    }
  }
  function cursorSuffix(text) { return text + "▍"; }
  function parseSSE(block) {
    let event = "message"; const data = {};
    block.split("\n").forEach((line) => {
      if (!line) return;
      const idx = line.indexOf(":");
      if (idx <= 0) return;
      const k = line.slice(0, idx).trim();
      let v = line.slice(idx + 1);
      if (v.startsWith(" ")) v = v.slice(1);
      if (k === "event") event = v;
      else if (k === "data") { try { Object.assign(data, JSON.parse(v)); } catch (_) { data["raw"] = v; } }
    });
    return { event, data };
  }
  function scrollBottom(smooth) {
    const list = $("#message-list");
    list.scrollTo({ top: list.scrollHeight, behavior: smooth ? "auto" : "smooth" });
  }

  /* ---------- 知识库页面（参考 file-manager 布局） ---------- */
  // 文件类型 -> Lucide 图标 + 品牌配色（用于快捷访问卡片与文件列表的彩色图标）
  const extMeta = {
    pdf:  { icon: "file-text",    color: "#f04f6e", bg: "rgba(240,79,110,.14)" },
    doc:  { icon: "file-text",    color: "#3b82f6", bg: "rgba(59,130,246,.14)" },
    docx: { icon: "file-text",    color: "#3b82f6", bg: "rgba(59,130,246,.14)" },
    xls:  { icon: "table",        color: "#16a34a", bg: "rgba(22,163,74,.14)" },
    xlsx: { icon: "table",        color: "#16a34a", bg: "rgba(22,163,74,.14)" },
    ppt:  { icon: "presentation", color: "#f59e0b", bg: "rgba(245,158,11,.14)" },
    pptx: { icon: "presentation", color: "#f59e0b", bg: "rgba(245,158,11,.14)" },
    txt:  { icon: "file-text",    color: "#64748b", bg: "rgba(100,116,139,.16)" },
    md:   { icon: "file-text",    color: "#64748b", bg: "rgba(100,116,139,.16)" },
    html: { icon: "code",         color: "#8b5cf6", bg: "rgba(139,92,246,.14)" },
    png:  { icon: "image",        color: "#f97316", bg: "rgba(249,115,22,.14)" },
    jpg:  { icon: "image",        color: "#f97316", bg: "rgba(249,115,22,.14)" },
    jpeg: { icon: "image",        color: "#f97316", bg: "rgba(249,115,22,.14)" },
    gif:  { icon: "image",        color: "#f97316", bg: "rgba(249,115,22,.14)" },
    bmp:  { icon: "image",        color: "#f97316", bg: "rgba(249,115,22,.14)" },
    webp: { icon: "image",        color: "#f97316", bg: "rgba(249,115,22,.14)" },
  };
  function fileMeta(name) {
    const ext = (String(name).split(".").pop() || "").toLowerCase();
    return extMeta[ext] || { icon: "file", color: "#64748b", bg: "rgba(100,116,139,.16)" };
  }
  function fmtSize(n) {
    if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + " MB";
    return Math.max(1, Math.round(n / 1024)) + " KB";
  }
  function openKbPage() {
    if (state.abortCtrl) state.abortCtrl.abort();
    $("#kb-page").classList.remove("hidden");
    $("#app-view").classList.add("hidden");
    $("#kb-user-name").textContent = state.username || "个人";
    loadKb();
    saveView();
  }
  function closeKbPage() {
    $("#kb-page").classList.add("hidden");
    $("#app-view").classList.remove("hidden");
    loadDsIndicator(); // 刷新问答页的知识库状态提示
    saveView();
  }

  $("#btn-kb").addEventListener("click", openKbPage);
  $("#btn-back-kb").addEventListener("click", closeKbPage);

  async function loadKb() {
    try {
      const res = await api("/api/kb/datasets");
      state.datasets = await res.json();
      if (!state.currentDs || !state.datasets.some((d) => d.id === state.currentDs)) {
        state.currentDs = state.datasets[0] ? state.datasets[0].id : null;
      }
      renderDsList();      // 列表渲染（代替原 Tabs）
      renderDsSelect();
      updateComposerDs();  // N9：恢复会话视图 / 新话题视图
      loadDsIndicator();
      loadDsDetail();       // 加载选中知识库的文件/成员详情
    } catch (err) { toast(err.message, true); }
  }
  // 会话区头部知识库状态提示 + 数据集下拉
  let _descPromise = null; // N9：datasets 加载完成前防重复请求，并确保首屏分类名称就绪
  async function loadDescriptors() {
    if (_descPromise) return _descPromise;
    _descPromise = (async () => {
      try {
        state.datasets = await apiJson("/api/kb/datasets");
        renderDsSelect();
        updateComposerDs();  // N9：同步新话题/会话视图
        loadDsIndicator();
        renderSessions();    // N9：会话分组名依赖知识库名 —— datasets 就绪后重绘，避免显示"知识库 #id"
      } catch (err) { /* 忽略 */ }
    })();
    _descPromise.then(() => { _descPromise = null; }).catch(() => { _descPromise = null; });
    return _descPromise;
  }

  function renderDsList() {
    const list = $("#ds-list");
    list.innerHTML = "";
    state.datasets.forEach((d) => {
      const li = document.createElement("li");
      li.className = "ds-item" + (d.id === state.currentDs ? " active" : "");
      const icon = d.type === "personal" ? "home" : "users";
      const m = d.type === "personal"
        ? { color: "#3b82f6", bg: "rgba(59,130,246,.14)" }
        : { color: "#f59e0b", bg: "rgba(245,158,11,.14)" };
      const roleTag = d.type === "team" ? `<span class="ds-role-tag">${roleShort[d.my_role] || "员"}</span>` : "";
      li.innerHTML = `<span class="ds-ico" style="background:${m.bg};color:${m.color}"><i data-lucide="${icon}"></i></span><span class="ds-label">${esc(d.name)}</span>${roleTag}`;
      li.addEventListener("click", () => { state.currentDs = d.id; renderDsList(); loadDsDetail(); saveView(); });
      list.appendChild(li);
    });
    hydrateIcons();
    const hasDs = state.datasets.length > 0;
    if (state.currentDs && hasDs) {
      $("#ds-empty").classList.add("hidden");
      $("#ds-content").classList.remove("hidden");
    } else {
      $("#ds-empty").classList.remove("hidden");
      $("#ds-content").classList.add("hidden");
    }
  }

  function currentDs() { return state.datasets.find((d) => d.id === state.currentDs); }

  // 当前用户在选中知识库中的有效角色（超管/个人库视为所有者）
  function currentRole() {
    const ds = currentDs();
    if (!ds) return null;
    if (state.isAdmin) return "owner";
    if (ds.type === "personal") return "owner";
    return ds.my_role || "member";
  }
  function canManageContent() { const r = currentRole(); return r === "owner" || r === "manager"; }
  function canManageMembers() { return currentRole() === "owner"; }

  async function loadDsDetail() {
    const ds = currentDs();
    if (!ds) return;
    const role = currentRole();
    $("#ds-name").textContent = ds.name;
    $("#ds-meta").textContent = ds.type === "personal"
      ? "个人知识库 · 专属私有"
      : `团队知识库 · ${roleLabel[role] || "成员"}`;
    const canManage = canManageContent();
    const canOwn = canManageMembers();
    // 内容操作（上传/重建/删除）仅对管理者及以上开放
    $("#ds-content-actions").classList.toggle("hidden", !canManage);
    // 成员管理（邀请/改角色/移除）仅所有者开放
    $("#ds-members").classList.toggle("hidden", !(ds.type === "team" && canOwn));
    if (ds.type === "team" && canOwn) loadMembers();
    const search = $("#kb-file-search"); if (search) search.value = "";
    selectedFiles.clear(); const allCk = $("#kb-check-all"); if (allCk) allCk.checked = false; updateBatchDel();
    await loadFiles();
  }

  async function loadFiles() {
    if (!state.currentDs) { $("#kb-count").textContent = "0 个文件"; state.files = []; renderKb([]); return; }
    try {
      const files = await apiJson(`/api/kb/datasets/${state.currentDs}/files`);
      state.files = files;
      renderKb(files);
    } catch (err) { toast(err.message, true); }
  }
  async function loadDsIndicator() {
    let total = 0, indexed = 0;
    for (const d of state.datasets) {
      try {
        const files = await apiJson(`/api/kb/datasets/${d.id}/files`);
        total += files.length; indexed += files.filter((f) => f.status === "indexed").length;
      } catch (_) {}
    }
    $("#kb-indicator").textContent = indexed > 0
      ? `（知识库 ${total} 文件 · ${indexed} 已索引）`
      : "（知识库为空，请上传并重建索引）";
    const label = $("#kb-meter-label"), sub = $("#kb-meter-sub"), fill = $("#kb-meter-fill");
    if (label) label.textContent = `${total} 个文件`;
    if (sub) sub.textContent = `${indexed} 已索引`;
    if (fill) fill.style.width = (total ? Math.min(100, Math.round(indexed / total * 100)) : 0) + "%";
  }
  function renderDsSelect() {
    const sel = $("#ds-select");
    sel.innerHTML = "";
    const all = document.createElement("option");
    all.value = DS_ALL; all.textContent = "一切有权限的知识库";
    sel.appendChild(all);
    state.datasets.forEach((d) => {
      const o = document.createElement("option");
      o.value = String(d.id);
      o.textContent = (d.type === "personal" ? "·  我的知识库" : `·  ${d.name}`) + (d.type === "team" ? `（${roleShort[d.my_role] || "员"}）` : "");
      sel.appendChild(o);
    });
    const want = state.pendingDs == null ? DS_ALL : String(state.pendingDs);
    sel.value = [...sel.options].some((o) => o.value === want) ? want : DS_ALL;
  }

  // N9：仅「新话题」（未进入会话）可选知识库；进入会话后锁定为该会话绑定库，不可再改
  function updateComposerDs() {
    const newWrap = $("#ds-tag-new");
    const fixed = $("#ds-tag-fixed");
    if (!fixed) return;
    if (!state.current) {
      if (newWrap) newWrap.classList.remove("hidden");
      fixed.classList.add("hidden");
      $("#ds-select").value = state.pendingDs == null ? DS_ALL : String(state.pendingDs);
    } else {
      if (newWrap) newWrap.classList.add("hidden");
      fixed.classList.remove("hidden");
      const s = state.sessions.find((x) => x.id === state.current);
      $("#ds-fixed-label").textContent = dsName(s ? s.dataset_id : null);
      hydrateIcons();
    }
  }

  $("#ds-select").addEventListener("change", () => {
    const v = $("#ds-select").value;
    state.pendingDs = v !== DS_ALL ? Number(v) : null;
    renderDsSelectedState();
  });

  function renderKb(files) {
    $("#kb-count").textContent = files.length + " 个文件";
    const q = ($("#kb-file-search").value || "").trim().toLowerCase();
    const rows = q ? files.filter((f) => (f.filename || "").toLowerCase().includes(q)) : files;

    // 快速访问：最近若干文件，按类型着色卡片
    const quick = $("#kb-quick");
    quick.innerHTML = "";
    const quickFiles = rows.slice(0, 6);
    if (quickFiles.length) {
      quickFiles.forEach((f) => {
        const m = fileMeta(f.filename);
        const card = document.createElement("div");
        card.className = "kb-quick-card";
        card.title = f.filename;
        card.innerHTML = `<span class="q-ico" style="background:${m.bg};color:${m.color}"><i data-lucide="${m.icon}"></i></span><span class="q-name">${esc(f.filename)}</span><span class="q-size">${fmtSize(f.size)}</span>`;
        card.addEventListener("click", () => openPreview(f));
        quick.appendChild(card);
      });
    } else {
      quick.innerHTML = `<div class="kb-empty-hint">暂无文件，点击「上传文档」加入资料，记得「重建索引」</div>`;
    }

    // 最近文件表
    const list = $("#kb-list");
    list.innerHTML = "";
    // 全选框与可见行保持同步（切换搜索/换库后不残留勾选态）
    const selAll = $("#kb-check-all");
    if (!rows.length) {
      if (selAll) selAll.checked = false;
      const li = document.createElement("li");
      li.className = "kb-file empty";
      li.innerHTML = `<span class="kb-col-select"></span><span class="f-main"><span class="f-name" style="color:var(--text-muted)">暂无文件，点击「上传文档」加入资料，记得「重建索引」</span></span><span></span><span></span><span></span><span></span>`;
      list.appendChild(li);
      hydrateIcons();
      updateQuickToggle();
      return;
    }
    rows.forEach((f) => {
      const m = fileMeta(f.filename);
      const li = document.createElement("li");
      li.className = "kb-file" + (selectedFiles.has(f.id) ? " kb-row-selected" : "");
      const t = (f.uploaded_at || "").slice(0, 16).replace("T", " ");
      const parseMap = { ok: "", empty: "空内容", unsupported: "不支持解析", pending: "待解析" };
      const parseLabel = parseMap[f.parse_status] || "";
      const tagClass = f.parse_status === "unsupported" ? "unsupported"
        : f.parse_status === "empty" ? "empty"
        : f.status === "indexed" ? "indexed" : "pending";
      const tagText = parseLabel || (f.status === "indexed" ? "已索引" : "待索引");
      const cbHtml = canManageContent()
        ? `<input type="checkbox" class="kb-file-check" data-id="${f.id}" title="选择以批量删除" ${selectedFiles.has(f.id) ? "checked" : ""}>`
        : "";
      li.innerHTML = `<span class="kb-col-select">${cbHtml}</span><span class="f-main"><span class="file-badge" style="background:${m.bg};color:${m.color}"><i data-lucide="${m.icon}"></i></span><span class="f-name" title="${esc(f.filename)}">${esc(f.filename)}</span></span><span class="f-time">${esc(t)}</span><span class="f-size">${fmtSize(f.size)}</span><span class="tag ${tagClass}" title="${esc(parseLabel || (f.status === "indexed" ? "已建立索引" : "尚未建立索引"))}">${tagText}</span><span class="f-actions"></span>`;
      const actions = li.querySelector(".f-actions");
      actions.appendChild(mkFileBtn("eye", "预览", () => openPreview(f)));
      actions.appendChild(mkFileBtn("download", "下载", () => downloadFile(f)));
      if (canManageContent()) actions.appendChild(mkFileBtn("trash-2", "删除", () => deleteFile(f), true));
      const cbe = li.querySelector(".kb-file-check");
      if (cbe) cbe.addEventListener("change", (e) => {
        if (e.target.checked) selectedFiles.add(f.id); else selectedFiles.delete(f.id);
        li.classList.toggle("kb-row-selected", e.target.checked);
        updateBatchDel();
      });
      list.appendChild(li);
    });
    if (selAll) selAll.checked = rows.length > 0 && rows.every((r) => selectedFiles.has(r.id));
    hydrateIcons();
    updateQuickToggle();
  }

  function updateQuickToggle() {
    const q = $("#kb-quick"), btn = document.querySelector('.kb-sec-toggle[data-target="kb-quick"]');
    if (!btn) return;
    btn.style.display = q && q.querySelector(".kb-quick-card") ? "inline-flex" : "none";
  }

  // 批量删除：顶部按钮随选中数量显隐
  function updateBatchDel() {
    const btn = $("#btn-batch-del");
    if (!btn) return;
    const n = selectedFiles.size;
    btn.classList.toggle("hidden", n === 0);
    btn.textContent = n ? `删除选中（${n}）` : "删除选中";
  }

  async function batchDeleteFiles() {
    const ids = [...selectedFiles];
    if (!ids.length || !state.currentDs) return;
    if (!confirm(`确认删除选中的 ${ids.length} 个文件？删除后需重新上传并重建索引。`)) return;
    try {
      await api(`/api/kb/datasets/${state.currentDs}/files/batch-delete`, {
        method: "POST", body: JSON.stringify({ ids }), headers: { "Content-Type": "application/json" },
      });
      selectedFiles.clear(); updateBatchDel();
      const allCk = $("#kb-check-all"); if (allCk) allCk.checked = false;
      toast(`已删除 ${ids.length} 个文件`);
      loadFiles(); loadDsIndicator();
    } catch (err) { toast("删除失败：" + err.message, true); }
  }

  // 全选框：切换页面上所有可见行的勾选状态
  function bindCheckAll() {
    const allCk = $("#kb-check-all");
    if (!allCk) return;
    allCk.addEventListener("change", () => {
      document.querySelectorAll("#kb-list .kb-file-check").forEach((cb) => {
        const id = Number(cb.dataset.id);
        cb.checked = allCk.checked;
        if (allCk.checked) selectedFiles.add(id); else selectedFiles.delete(id);
        cb.closest(".kb-file").classList.toggle("kb-row-selected", allCk.checked);
      });
      updateBatchDel();
    });
  }
  bindCheckAll();
  const btnBatchDel = $("#btn-batch-del");
  if (btnBatchDel) btnBatchDel.addEventListener("click", () => batchDeleteFiles().catch(() => {}));

  function mkFileBtn(icon, title, fn, danger) {
    const b = document.createElement("button");
    b.className = "f-btn" + (danger ? " danger" : "");
    b.title = title; b.innerHTML = `<i data-lucide="${icon}"></i>`;
    b.addEventListener("click", fn);
    return b;
  }

  async function openPreview(f) {
    if (!state.currentDs) return;
    $("#preview-title").textContent = f.filename;
    previewView("render");
    const box = $("#preview-render"); box.innerHTML = `<div class="kb-preview-empty">正在加载解析内容…</div>`;
    $("#preview-stats").innerHTML = "";
    $("#preview-modal").classList.remove("hidden");
    try {
      const data = await apiJson(`/api/kb/datasets/${state.currentDs}/files/${f.id}/content`);
      const text = data.content || "";
      renderPreviewStats(f, text);
      box.innerHTML = renderParse(text);
      $("#preview-body").textContent = text;
    } catch (err) {
      box.innerHTML = `<div class="kb-preview-empty">加载失败：${esc(err.message)}</div>`;
    }
    hydrateIcons();
  }

  // 预览视图切换：渲染 / 原文
  function previewView(v) {
    $("#preview-render").hidden = v !== "render";
    $("#preview-body").hidden = v !== "raw";
    $$("#preview-tabs .preview-tab").forEach((b) => b.classList.toggle("active", b.dataset.view === v));
  }
  $$("#preview-tabs .preview-tab").forEach((b) => b.addEventListener("click", () => previewView(b.dataset.view)));

  // 解析统计栏：字符/行/分节 + 解析状态标签
  function renderPreviewStats(f, text) {
    const wrap = $("#preview-stats");
    const chips = [];
    const parseMap = { ok: "已解析", empty: "空内容", unsupported: "不支持解析", pending: "待解析" };
    const pCls = f.parse_status === "ok" ? "stat-ok" : f.parse_status === "empty" ? "stat-warn" : f.parse_status === "unsupported" ? "stat-bad" : "stat-warn";
    chips.push(`<span class="preview-stat ${pCls}">解析：${esc(parseMap[f.parse_status] || f.parse_status || "未知")}</span>`);
    if (f.size) chips.push(`<span class="preview-stat">文件 ${fmtSize(f.size)}</span>`);
    const chars = text.length, lines = text ? text.split("\n").length : 0;
    chips.push(`<span class="preview-stat"><b>${chars}</b> 字符 · <b>${lines}</b> 行</span>`);
    const s = structureCounts(text);
    if (s.sheets) chips.push(`<span class="preview-stat"><b>${s.sheets}</b> 工作表</span>`);
    if (s.slides) chips.push(`<span class="preview-stat"><b>${s.slides}</b> 幻灯片</span>`);
    if (s.tables) chips.push(`<span class="preview-stat"><b>${s.tables}</b> 表格</span>`);
    if (s.figs) chips.push(`<span class="preview-stat"><b>${s.figs}</b> 图片/图注</span>`);
    wrap.innerHTML = chips.join("");
  }

  // 统计结构化节数量
  function structureCounts(text) {
    const c = { sheets: 0, slides: 0, tables: 0, figs: 0 };
    lines(text).forEach((ln) => {
      if (/^\[工作表：/.test(ln)) c.sheets++;
      else if (/^\[幻灯片\s*\d+\]/.test(ln)) c.slides++;
      else if (/^\[表格\]/.test(ln)) c.tables++;
      else if (/^\[(图|图片)\s*\d+/.test(ln)) c.figs++;
    });
    return c;
  }
  function lines(text) { return String(text).split("\n"); }

  // 结构化解析标记 → 可读 Markdown（工作表/幻灯片/表格/图注 作为分层标题）
  function renderParse(text) {
    const md = String(text).replace(/^\[工作表：([^\]]+)\]\s*$/gm, "\n##### 工作表：$1\n")
      .replace(/^\[幻灯片\s*(\d+)\]\s*$/gm, "\n##### 幻灯片 $1\n")
      .replace(/^\[表格\]\s*$/gm, "\n##### 表格\n")
      .replace(/^\[(图|图片)\s*(\d+)(：[^\]]*)?\]\s*$/gm, "\n> **$1 $2$3**\n");
    return renderMd(md, 0) || `<div class="kb-preview-empty">（文件解析内容为空）</div>`;
  }
  $("#btn-close-preview").addEventListener("click", () => $("#preview-modal").classList.add("hidden"));

  async function downloadFile(f) {
    if (!state.currentDs) return;
    try {
      const res = await api(`/api/kb/datasets/${state.currentDs}/files/${f.id}/download`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = f.filename;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
      toast(`已开始下载「${f.filename}」`);
    } catch (err) { toast("下载失败：" + err.message, true); }
  }

  async function deleteFile(f) {
    if (!confirm(`删除文件「${f.filename}」？删除后需重新上传并重建索引。`)) return;
    try {
      await api(`/api/kb/datasets/${state.currentDs}/files/${f.id}`, { method: "DELETE" });
      toast("文件已删除");
      loadFiles(); loadDsIndicator();
    } catch (err) { toast("删除失败：" + err.message, true); }
  }

  // R-06：重建索引改为后台任务 + 轮询进度，避免大库阻塞请求
  async function runRebuild(dsId) {
    const btn = $("#btn-rebuild");
    const prog = $("#rebuild-progress");
    if (btn) { btn.disabled = true; btn.innerHTML = "重建中…"; }
    if (prog) prog.textContent = "0%";
    try {
      const t = await apiJson(`/api/kb/datasets/${dsId}/rebuild`, { method: "POST" });
      let last = await apiJson(`/api/kb/tasks/${t.task_id}`);
      while (last && (last.status === "queued" || last.status === "running")) {
        await new Promise((r) => setTimeout(r, 800));
        last = await apiJson(`/api/kb/tasks/${t.task_id}`);
        if (prog && last) prog.textContent = `${last.progress}%`;
      }
      if (!last || last.status !== "done") throw new Error((last && last.detail) || "重建失败");
      toast(`重建完成：${last.detail || "索引已更新"}`);
    } catch (err) {
      toast("重建失败：" + err.message, true);
      throw err;
    } finally {
      if (btn) { btn.disabled = false; btn.innerHTML = `<i data-lucide="refresh-cw"></i> 重建索引`; hydrateIcons(); }
      if (prog) prog.textContent = "";
      loadFiles(); loadDsIndicator();
    }
  }
  $("#btn-rebuild").addEventListener("click", () => { if (!state.currentDs) return; runRebuild(state.currentDs).catch(() => {}); });

  // ---- P0-1 内容分块设置与预览 ----
  const splitLabels = { markdown: "标题感知（默认）", recursive: "递归切分", whole: "大段不拆" };
  document.addEventListener("click", (e) => {
    const panel = e.target.closest(".kb-panel");
    if (!panel || !["kb-config-modal", "kb-preview-modal"].includes(panel.id)) return;
    if (e.target.closest("[data-close='true']")) { panel.classList.add("hidden"); return; }
    const ov = e.target.closest(".kb-overlay");
    if (ov && ov.dataset.close === "true") panel.classList.add("hidden");
  });

  function openChunkConfig() {
    if (!state.currentDs) { toast("请先选择一个知识库", true); return; }
    $("#kv-split-mode").value = "markdown";
    $("#kv-chunk-size").value = ""; $("#kv-overlap").value = "";
    $("#kb-config-modal").classList.remove("hidden");
    apiJson(`/api/kb/datasets/${state.currentDs}/params`).then((p) => {
      if (p.split_mode) $("#kv-split-mode").value = p.split_mode;
      if (p.chunk_size) $("#kv-chunk-size").value = p.chunk_size;
      if (p.overlap != null && p.overlap !== "") $("#kv-overlap").value = p.overlap;
      const whole = p.split_mode === "whole";
      $("#kv-chunk-size").disabled = whole; $("#kv-overlap").disabled = whole;
      hydrateIcons();
    }).catch((err) => toast("加载分块参数失败：" + err.message, true));
  }
  $("#btn-chunk-config").addEventListener("click", openChunkConfig);

  $("#kv-split-mode").addEventListener("change", (e) => {
    const whole = e.target.value === "whole";
    $("#kv-chunk-size").disabled = whole; $("#kv-overlap").disabled = whole;
  });

  $("#btn-save-split").addEventListener("click", async () => {
    if (!state.currentDs) return;
    const mode = $("#kv-split-mode").value;
    const body = { split_mode: mode };
    if (mode !== "whole") {
      const cs = $("#kv-chunk-size").value.trim();
      const ov = $("#kv-overlap").value.trim();
      if (cs) body.chunk_size = parseInt(cs, 10);
      if (ov !== "") body.overlap = parseInt(ov, 10);
    }
    try {
      await apiJson(`/api/kb/datasets/${state.currentDs}/params`, {
        method: "PUT", body: JSON.stringify(body),
      });
      $("#kb-config-modal").classList.add("hidden");
      toast(`分块模式已设为「${splitLabels[mode]}」，正在重建索引…`);
      runRebuild(state.currentDs).catch(() => {});
    } catch (err) { toast("保存分块参数失败：" + err.message, true); }
  });

  async function openChunkPreview() {
    if (!state.currentDs) { toast("请先选择一个知识库", true); return; }
    const list = $("#kb-preview-list");
    list.innerHTML = `<div class="kb-preview-empty">正在读取分块结果…</div>`;
    $("#kb-preview-modal").classList.remove("hidden");
    try {
      const data = await apiJson(`/api/kb/datasets/${state.currentDs}/chunks-preview`);
      const eff = data.effective;
      list.innerHTML = "";
      const head = document.createElement("div");
      head.className = "kb-preview-head";
      head.textContent = eff
        ? `共 ${data.total} 个区块 · ${splitLabels[eff.split_mode] || eff.split_mode} · 长度 ${eff.chunk_size} · 重叠 ${eff.overlap}`
        : "（知识库暂无文件可预览）";
      list.appendChild(head);
      (data.chunks || []).forEach((c) => {
        const item = document.createElement("div");
        item.className = "kb-preview-item";
        const src = c.section ? `${c.source} › ${c.section}` : c.source;
        const body = c.content || "";
        const isEmpty = body.trim().length === 0;
        const chunkSize = eff ? eff.chunk_size : 500;
        const isLong = !isEmpty && c.length > Math.round(chunkSize * 1.6);
        const warn = isEmpty ? `<span class="kp-warn bad" title="该块内容为空，建议调整分块方式">空块</span>`
          : (isLong ? `<span class="kp-warn" title="长度 ${c.length} 字符，超目标区块长度上限">超长 ${c.length}</span>` : "");
        if (isEmpty) item.classList.add("kp-empty");
        else if (isLong) item.classList.add("kp-long");
        item.innerHTML = `<span class="kp-index">#${c.index}</span><div class="kp-body"><div class="kp-src">${esc(src)}${warn}</div><div class="kp-text">${esc(body)}</div></div>`;
        list.appendChild(item);
      });
    } catch (err) {
      list.innerHTML = `<div class="kb-preview-empty">预览失败：${esc(err.message)}</div>`;
    }
    hydrateIcons();
  }
  $("#btn-chunk-preview").addEventListener("click", openChunkPreview);

  // 上传到当前数据集
  // 上传进度条：XHR 才能拿到 upload.onprogress（fetch 无上传进度事件）
  function uploadOne(fd) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/kb/upload");
      if (state.token) xhr.setRequestHeader("Authorization", "Bearer " + state.token);
      if (state.csrf) xhr.setRequestHeader("X-CSRF-Token", state.csrf);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && progUpdate) typeof progUpdate === "function" && progUpdate(e.loaded / e.total);
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) { resolve(); return; }
        let detail = xhr.statusText;
        try { detail = JSON.parse(xhr.responseText).detail || detail; } catch (_) {}
        if (xhr.status === 401) { logout(); reject(new Error("登录已失效")); }
        else reject(new Error(detail));
      };
      xhr.onerror = () => reject(new Error("网络错误"));
      xhr.send(fd);
    });
  }

  const dropzone = $("#dropzone");
  const fileInput = $("#file-input");
  let progUpdate = null; // 当前文件上传进度回调（0..1）
  async function handleFiles(files) {
    if (!files || !files.length) { toast("请先在知识库中选择要上传的库", true); return; }
    if (!state.currentDs) { toast("请先选择一个知识库", true); return; }
    const wrap = $("#upload-progress"), fill = $("#upload-progress-fill"), txt = $("#upload-progress-text");
    const total = files.length;
    const setProg = (ratio) => {
      if (fill) fill.style.width = Math.round(ratio * 100) + "%";
      if (txt) txt.textContent = Math.round(ratio * 100) + "%";
    };
    if (wrap) { wrap.classList.remove("hidden"); setProg(0); }
    let ok = 0, done = 0;
    const filesArr = Array.from(files);
    for (const f of filesArr) {
      const fd = new FormData();
      fd.append("file", f);
      fd.append("dataset_id", String(state.currentDs));
      progUpdate = (r) => { if (wrap) setProg((done + r) / total); };
      try { await uploadOne(fd); ok++; }
      catch (err) { toast(`${f.name} 上传失败：${err.message}`, true); }
      done++; progUpdate = null;
      if (wrap) setProg(done / total);
    }
    if (ok > 0) {
      // 上传后自动重建索引，保证立即可检索（R-06：后台任务 + 进度）
      toast(`已上传 ${ok} 个文件，开始重建索引…`);
      await runRebuild(state.currentDs).catch(() => {});
    } else {
      toast("没有文件上传成功", true);
    }
    if (wrap) setTimeout(() => wrap.classList.add("hidden"), 500);
    loadFiles(); loadDsIndicator();
  }
  fileInput.addEventListener("change", () => { handleFiles(fileInput.files); fileInput.value = ""; });
  ["dragover", "dragenter"].forEach((ev) => dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.style.borderColor = "var(--brand)"; }));
  ["dragleave", "drop"].forEach((ev) => dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.style.borderColor = ""; }));
  dropzone.addEventListener("drop", (e) => handleFiles(e.dataTransfer.files));

  // 文件夹上传（webkitdirectory 一次选取整棵目录树）
  const folderInput = $("#folder-input");
  const btnFolder = $("#btn-upload-folder");
  btnFolder.addEventListener("click", () => folderInput.click());
  folderInput.addEventListener("change", () => { handleFiles(folderInput.files); folderInput.value = ""; });

  // 新建团队库
  function openTeamModal() { $("#team-modal").classList.remove("hidden"); $("#team-name").value = ""; $("#team-name").focus(); }
  function closeTeamModal() { $("#team-modal").classList.add("hidden"); }
  $("#btn-new-team").addEventListener("click", openTeamModal);
  $("#btn-close-team").addEventListener("click", closeTeamModal);
  $("#btn-cancel-team").addEventListener("click", closeTeamModal);
  $("#btn-confirm-team").addEventListener("click", async () => {
    const name = $("#team-name").value.trim();
    if (!name) { toast("请输入团队库名称", true); return; }
    try {
      const d = await apiJson("/api/kb/datasets/team", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
      closeTeamModal();
      state.currentDs = d.id;
      await loadKb();
      toast(`已创建团队库「${d.name}」`);
    } catch (err) { toast("创建失败：" + err.message, true); }
  });

  // 成员管理
  async function loadMembers() {
    if (!state.currentDs) return;
    const canOwn = canManageMembers();
    try {
      const ms = await apiJson(`/api/kb/datasets/${state.currentDs}/members`);
      const list = $("#member-list");
      list.innerHTML = "";
      ms.forEach((m) => {
        const row = document.createElement("div");
        row.className = "member-row";
        const isSelf = m.username === state.username;
        row.innerHTML = `<span class="m-name">${esc(m.username)}${isSelf ? "<span class=\"tag owner\" style=\"margin-left:6px\">我</span>" : ""}</span><span class="tag ${m.role}">${roleLabel[m.role] || "成员"}</span>`;
        // 角色下拉：所有者可为其他成员调整角色
        if (canOwn && !isSelf) {
          const sel = document.createElement("select");
          sel.className = "member-role";
          ["owner", "manager", "member"].forEach((r) => {
            const o = document.createElement("option");
            o.value = r; o.textContent = roleLabel[r];
            o.selected = m.role === r;
            sel.appendChild(o);
          });
          sel.addEventListener("change", async () => {
            try {
              await apiJson(`/api/kb/datasets/${state.currentDs}/members/${m.id}/role`, {
                method: "PATCH", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ role: sel.value }),
              });
              toast(`已将 ${m.username} 设为${roleLabel[sel.value]}`);
              loadMembers();
            } catch (err) { toast(err.message, true); loadMembers(); }
          });
          row.appendChild(sel);
        }
        if (canOwn && !isSelf) {
          const del = document.createElement("button");
          del.className = "m-kick"; del.textContent = "移除";
          del.addEventListener("click", async () => {
            if (!confirm(`将 ${m.username} 移出该团队库？`)) return;
            try { await api(`/api/kb/datasets/${state.currentDs}/members/${m.id}`, { method: "DELETE" }); loadMembers(); }
            catch (err) { toast(err.message, true); }
          });
          row.appendChild(del);
        }
        list.appendChild(row);
      });
      hydrateIcons();
      $("#member-add").classList.toggle("hidden", !canOwn);
    } catch (err) { toast(err.message, true); }
  }
  $("#btn-add-member").addEventListener("click", async () => {
    const name = $("#member-name").value.trim();
    const role = $("#member-role").value;
    if (!name) return;
    try {
      await apiJson(`/api/kb/datasets/${state.currentDs}/members`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: name, role }),
      });
      $("#member-name").value = "";
      toast("已邀请成员");
      loadMembers();
    } catch (err) { toast("邀请失败：" + err.message, true); }
  });

  // 文件搜索 + 快速访问折叠
  $("#kb-file-search").addEventListener("input", () => renderKb(state.files || []));
  document.querySelectorAll(".kb-sec-toggle").forEach((b) => b.addEventListener("click", () => {
    const body = document.getElementById(b.dataset.target);
    if (!body) return;
    body.classList.toggle("collapsed");
    b.setAttribute("aria-expanded", String(!body.classList.contains("collapsed")));
  }));

  /* ---------- 管理中心（概览 / 人员 / 模型 / 资源 / 邀请码） ---------- */
  function openAdminPage() {
    $("#admin-page").classList.remove("hidden");
    $("#app-view").classList.add("hidden");
    switchAdm("overview");
    saveView();
  }
  function closeAdminPage() {
    $("#admin-page").classList.add("hidden");
    $("#app-view").classList.remove("hidden");
    saveView();
  }
  $("#btn-admin").addEventListener("click", openAdminPage);
  $("#btn-back-admin").addEventListener("click", closeAdminPage);

  function switchAdm(name) {
    state.admTab = name;
    document.querySelectorAll(".adm-nav-item").forEach((b) => b.classList.toggle("active", b.dataset.adm === name));
    document.querySelectorAll(".adm-page").forEach((p) => p.classList.toggle("hidden", p.dataset.page !== name));
    if (name === "overview") loadOverview();
    else if (name === "users") loadUsers();
    else if (name === "usage") loadUsage();
    else if (name === "models") loadModelForm();
    else if (name === "quota") loadQuotaList();
    else if (name === "invite") loadInvite();
  }
  document.querySelectorAll(".adm-nav-item").forEach((b) => b.addEventListener("click", () => switchAdm(b.dataset.adm)));

  async function loadOverview() {
    try {
      const d = await apiJson("/api/admin/dashboard");
      const t = d.totals, u = d.users;
      const stats = [
        { icon: "users", label: "用户", value: t.users },
        { icon: "user-check", label: "启用", value: t.active_users },
        { icon: "messages-square", label: "会话", value: t.sessions },
        { icon: "file-text", label: "消息", value: t.messages },
        { icon: "folder", label: "知识库", value: t.datasets },
        { icon: "database", label: "文件", value: t.files },
        { icon: "hard-drive", label: "存储", value: fmtSize(t.bytes) },
      ];
      $("#adm-stats").innerHTML = stats.map((s) => `<div class="adm-stat"><span class="adm-stat-ico"><i data-lucide="${s.icon}"></i></span><div><div class="adm-stat-val">${s.value}</div><div class="adm-stat-label">${esc(s.label)}</div></div></div>`).join("");
      $("#adm-usage-list").innerHTML = u.map((x) => `<div class="adm-usage-row"><span class="m-name">${esc(x.username)}</span><span class="adm-usage-meta">会话 ${x.sessions} · 文件 ${x.files} · ${fmtSize(x.bytes)}</span><span class="tag ${x.is_active ? "indexed" : "pending"}">${x.is_active ? "启用" : "停用"}</span></div>`).join("");
      hydrateIcons();
    } catch (err) { toast(err.message, true); }
  }

  function fmtActive(iso) {
    if (!iso) return "—";
    const d = new Date(iso + "+08:00");
    if (isNaN(d)) return "—";
    const p = utc8Parts(d), now = utc8Now();
    return p.iso === now.iso ? `今天 ${p.hh}:${p.mm}`
      : `${String(p.mo).padStart(2, "0")}-${String(p.da).padStart(2, "0")} ${p.hh}:${p.mm}`;
  }

  async function loadUsage() {
    try {
      const daily = await apiJson("/api/admin/usage/daily?days=14");
      const dash = await apiJson("/api/admin/dashboard");
      const items = daily.items || [];
      const max = Math.max(1, ...items.map((x) => x.questions));
      $("#adm-usage-chart").innerHTML = items.map((x) => {
        const h = Math.max(2, Math.round((x.questions / max) * 100));
        const day = (x.date || "").slice(5);
        return `<div class="uc-col"><span class="uc-plot" title="${x.date} 提问 ${x.questions} · 新会话 ${x.new_sessions} · 活跃 ${x.active_users}"><i style="height:${h}%"></i></span><span class="uc-val">${x.questions}</span><span class="uc-day">${day}</span></div>`;
      }).join("");
      const rows = dash.users || [];
      const head = `<div class="ut-row ut-head"><span>用户</span><span>会话</span><span>提问</span><span>消息</span><span>知识库</span><span>文件</span><span>存储</span><span>最近活跃</span></div>`;
      $("#adm-usage-table").innerHTML = head + rows.map((u) => `<div class="ut-row"><span class="ut-name">${esc(u.username)}</span><span>${u.sessions}</span><span>${u.questions}</span><span>${u.messages}</span><span>${u.datasets}</span><span>${u.files}</span><span>${fmtSize(u.bytes)}</span><span class="ut-active">${fmtActive(u.last_active)}</span></div>`).join("");
      hydrateIcons();
    } catch (err) { toast(err.message, true); }
  }

  async function loadUsers() {
    try {
      const users = await apiJson("/api/auth/admin/users");
      const list = $("#user-list");
      list.innerHTML = "";
      users.forEach((u) => {
        const row = document.createElement("div");
        row.className = "member-row";
        const isSelf = u.username === state.username;
        const isAdminSelf = u.is_admin;
        row.innerHTML = `<span class="m-name">${esc(u.username)}${u.is_admin ? ' <span class="tag owner">超管</span>' : ""}</span>
          <span class="tag ${u.is_active ? "indexed" : "pending"}">${u.is_active ? "启用" : "停用"}</span>`;
        if (!isAdminSelf && !isSelf) {
          const toggle = document.createElement("button");
          toggle.className = "m-kick";
          toggle.textContent = u.is_active ? "停用" : "启用";
          toggle.addEventListener("click", async () => {
            try { await apiJson(`/api/auth/admin/users/${u.id}/active`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ is_active: !u.is_active }) }); loadUsers(); }
            catch (err) { toast(err.message, true); }
          });
          const del = document.createElement("button");
          del.className = "m-kick danger";
          del.textContent = "删除";
          del.addEventListener("click", async () => {
            if (!confirm(`确认删除用户 ${u.username}？该操作不可恢复。`)) return;
            try { await api(`/api/auth/admin/users/${u.id}`, { method: "DELETE" }); loadUsers(); }
            catch (err) { toast(err.message, true); }
          });
          row.appendChild(toggle); row.appendChild(del);
        }
        list.appendChild(row);
      });
      hydrateIcons();
    } catch (err) { toast(err.message, true); }
  }

  async function loadModelForm() {
    try {
      const d = await apiJson("/api/admin/model");
      const html = `
        <div class="model-note">${esc(d.note)}</div>
        <div class="model-section-title">已配置模型</div>
        <div id="model-list" class="model-list"></div>
        <div class="model-section-title">添加新模型</div>
        <div class="model-add-form">
          <div class="model-row">
            <div class="model-col">
              <label>显示名称</label>
              <input id="nm-display" class="member-input" placeholder="可选，如 DeepSeek-V3 生产">
            </div>
            <div class="model-col">
              <label>供应商</label>
              <input id="nm-provider" class="member-input" placeholder="如 DeepSeek / 阿里云百炼">
            </div>
          </div>
          <div class="model-row">
            <div class="model-col">
              <label>模型标识 <span style="color: var(--danger)">*</span></label>
              <select id="nm-select" class="member-input">
                <option value="">自定义（手动填写）…</option>
                ${(d.presets || []).map(p => `<option value="${esc(p.model)}" data-url="${esc(p.base_url)}" data-provider="${esc(p.provider)}">${esc(p.provider)} · ${esc(p.name)}（${esc(p.model)}）</option>`).join("\n                ")}
              </select>
              <input id="nm-model" class="member-input" placeholder="如 deepseek-chat">
            </div>
            <div class="model-col">
              <label>API 地址 <span style="color: var(--danger)">*</span></label>
              <input id="nm-base" class="member-input" placeholder="https://api.deepseek.com/v1">
            </div>
          </div>
          <div class="model-row">
            <div class="model-col full">
              <label>API Key</label>
              <input id="nm-key" type="password" class="member-input" placeholder="sk-xxx（仅保存在本服务数据库，不会泄露）">
            </div>
          </div>
          <div class="model-add-actions">
            <button id="btn-add-model" class="btn btn-primary btn-sm">添加模型</button>
            <button id="btn-test-new" class="btn btn-ghost btn-sm">先测试连通性</button>
            <span id="nm-test-msg" class="model-test-msg"></span>
          </div>
        </div>
        <div class="model-section-divider"></div>
        <div class="model-section-title">生成/检索参数</div>
        <div class="model-params">
          <div class="model-row">
            <div class="model-col">
              <label>温度</label>
              <input id="p-temp" type="number" step="0.1" min="0" max="2" class="member-input" value="${esc(d.params.llm_temperature)}">
            </div>
            <div class="model-col">
              <label>Top-K</label>
              <input id="p-topk" type="number" min="1" max="50" class="member-input" value="${esc(d.params.top_k)}">
            </div>
            <div class="model-col">
              <label>相关度阈值</label>
              <input id="p-threshold" type="number" step="0.05" min="0" max="1" class="member-input" value="${esc(d.params.score_threshold)}">
            </div>
          </div>
          <div class="model-row">
            <div class="model-col">
              <label>多轮历史轮数</label>
              <input id="p-history" type="number" min="1" max="20" class="member-input" value="${esc(d.params.history_turns)}">
            </div>
            <div class="model-col">
              <label>上下文 Token 预算</label>
              <input id="p-context" type="number" min="500" max="60000" step="100" class="member-input" value="${esc(d.params.context_token_budget)}">
            </div>
            <div class="model-col">
              <label>最大来源数</label>
              <input id="p-maxhits" type="number" min="1" max="50" class="member-input" value="${esc(d.params.max_context_hits)}">
            </div>
          </div>
          <div class="model-row">
            <div class="model-col">
              <label class="model-check"><input id="p-rewrite" type="checkbox" ${d.params.query_rewrite === "1" ? "checked" : ""}> 查询改写（追问检索）</label>
            </div>
            <div class="model-col">
              <label class="model-check"><input id="p-suggest" type="checkbox" ${d.params.suggest_questions === "1" ? "checked" : ""}> 建议追问（回答后推荐问题）</label>
            </div>
            <div class="model-col">
              <label class="model-check"><input id="p-mmr" type="checkbox" ${d.params.mmr_enabled === "1" ? "checked" : ""}> 多样性（MMR）</label>
            </div>
            <div class="model-col">
              <label>MMR λ</label>
              <input id="p-mmrl" type="number" step="0.1" min="0" max="1" class="member-input" value="${esc(d.params.mmr_lambda)}">
            </div>
          </div>
          <div class="model-row">
            <div class="model-col narrow">
              <label class="model-check"><input id="p-rerank" type="checkbox" ${d.params.rerank_enabled === "1" ? "checked" : ""}> 重排序（Rerank）</label>
            </div>
            <div class="model-col">
              <label>重排通道</label>
              <select id="p-rerankchannel" class="member-input">
                <option value="local" ${d.params.rerank_channel === "local" ? "selected" : ""}>本机 CrossEncoder</option>
                <option value="external" ${d.params.rerank_channel !== "local" ? "selected" : ""}>外部 API（/v1/rerank）</option>
              </select>
            </div>
            <div class="model-col full">
              <label id="lbl-rerankmodel">重排序模型（需本机安装 sentence-transformers）</label>
              <input id="p-rerankmodel" class="member-input" value="${esc(d.params.rerank_model)}">
            </div>
          </div>
          <div class="model-row" id="row-rerankext">
            <div class="model-col">
              <label>外部重排 API 地址</label>
              <input id="p-rerankurl" class="member-input" placeholder="https://your-rerank-service/v1" value="${esc(d.params.rerank_external_url)}">
            </div>
            <div class="model-col">
              <label>外部重排 API Key <span class="muted">${d.params.rerank_has_key ? "（已配置，留空保持不变）" : "（未配置）"}</span></label>
              <input id="p-rerankkey" class="member-input" type="password" placeholder="${d.params.rerank_has_key ? "已配置，输入新值可覆盖" : "可选"}">
            </div>
          </div>
          <div class="model-row">
            <div class="model-col">
              <label class="model-check"><input id="p-hybrid" type="checkbox" ${d.params.hybrid_enabled === "1" ? "checked" : ""}> 混合检索（稠密+BM25）</label>
            </div>
            <div class="model-col">
              <label>融合方式</label>
              <select id="p-hybridmethod" class="member-input">
                <option value="weighted" ${d.params.hybrid_method === "weighted" ? "selected" : ""}>加权（α 权重）</option>
                <option value="rrf" ${d.params.hybrid_method === "rrf" ? "selected" : ""}>RRF 排名</option>
              </select>
            </div>
            <div class="model-col">
              <label>混合权重 α（偏语义）</label>
              <input id="p-hybrida" type="number" step="0.05" min="0" max="1" class="member-input" value="${esc(d.params.hybrid_alpha)}">
            </div>
          </div>
          <div class="model-row">
            <div class="model-col narrow">
              <label class="model-check"><input id="p-longsum" type="checkbox" ${d.params.long_doc_summary === "1" ? "checked" : ""}> 超长文档摘要（重建索引时）</label>
            </div>
            <div class="model-col full">
              <label>长文档阈值（字符）</label>
              <input id="p-longchars" type="number" step="100" min="1000" max="2000000" class="member-input" value="${esc(d.params.long_doc_chars)}">
            </div>
          </div>
          <div class="model-param-actions">
            <button id="btn-save-params" class="btn btn-ghost btn-sm">保存参数</button>
          </div>
          <div class="model-emb-info">
            <p>Embedding 模型：${esc(d.embedding.model)} @ ${esc(d.embedding.base_url)} · API Key ${d.embedding.has_key ? "已配置" : "未配置"}</p>
            <div id="emb-status"></div>
            <div class="model-emb-actions">
              <button id="btn-rebuild-all" class="btn btn-ghost btn-sm"><i data-lucide="refresh-cw"></i> 重建全部索引</button>
              <span id="emb-rebuild-progress" class="rebuild-progress"></span>
            </div>
            <p class="muted">Embedding 变更会影响向量索引：需在 .env 修改后重启服务，然后点上方「重建全部索引」一次以生效。</p>
          </div>
        </div>
      `;
      $("#model-form").innerHTML = html;
      renderModelList(d.models, d.active_id, d.boot_default);
      hydrateIcons();
      bindNewModelSelect();
      $("#btn-add-model").addEventListener("click", addModel);
      $("#btn-test-new").addEventListener("click", () => testNewModel());
      $("#btn-save-params").addEventListener("click", saveParams);
      // N4：通道切换动态显示外部重排字段
      function toggleRerankExt() {
        const channel = $("#p-rerankchannel").value;
        $("#row-rerankext").classList.toggle("hidden", channel !== "external");
        $("#lbl-rerankmodel").textContent = channel === "local" ?
          "重排序模型（需本机安装 sentence-transformers）" : "重排序模型名称";
      }
      $("#p-rerankchannel").addEventListener("change", toggleRerankExt);
      toggleRerankExt();
      loadEmbeddingStatus();
      const ra = $("#btn-rebuild-all");
      if (ra) ra.addEventListener("click", () => rebuildAll());
    } catch (err) { toast(err.message, true); }
  }
  function renderModelList(models, activeId, bootDefault) {
    const list = $("#model-list");
    if (!list) return;
    if (!models || !models.length) {
      if (bootDefault) {
        list.innerHTML = `
          <div class="model-empty">
            <span class="model-empty-hint">当前使用系统默认模型（未加入列表，来自 .env）：<b>${esc(bootDefault.model)}</b> @ ${esc(bootDefault.base_url)} · API Key ${bootDefault.has_key ? "已配置" : "未配置"}</span>
            <button id="btn-import-default" class="btn btn-ghost btn-sm">导入为可管理模型</button>
            <span class="model-empty-sub">导入后即可在下方编辑它的 Key、设为当前使用或删除改配。</span>
          </div>`;
        const btn = $("#btn-import-default");
        if (btn) btn.addEventListener("click", () => importBootDefault(bootDefault));
        return;
      }
      list.innerHTML = '<div class="model-empty">暂无配置，添加第一个模型开始…</div>';
      return;
    }
    list.innerHTML = models.map(m => {
      const active = m.id === activeId;
      const badge = active ? '<span class="tag owner">当前激活</span>' : '<span class="tag">已配置</span>';
      const keyBadge = m.has_key ? '<span class="tag indexed">Key 已填</span>' : '<span class="tag pending">Key 待填</span>';
      const provider = m.provider ? `${esc(m.provider)} · ` : '';
      const name = m.display_name ? esc(m.display_name) : `${provider}${esc(m.model)}`;
      return `
        <div class="model-item ${active ? 'active' : ''}" data-id="${m.id}">
          <div class="model-item-head">
            <span class="model-item-name">${name} ${badge}</span>
            <span class="model-item-badges">${keyBadge}</span>
          </div>
          <div class="model-item-meta">
            <span class="model-id">${provider}${esc(m.model)} @ ${esc(m.base_url)}</span>
          </div>
          <div class="model-item-form hidden">
            <div class="model-row">
              <div class="model-col">
                <label>显示名称</label>
                <input class="e-display member-input" placeholder="可选" value="${esc(m.display_name || '')}">
              </div>
              <div class="model-col">
                <label>供应商</label>
                <input class="e-provider member-input" placeholder="如 DeepSeek" value="${esc(m.provider || '')}">
              </div>
            </div>
            <div class="model-row">
              <div class="model-col">
                <label>模型标识</label>
                <input class="e-model member-input" value="${esc(m.model)}">
              </div>
              <div class="model-col">
                <label>API 地址</label>
                <input class="e-base member-input" value="${esc(m.base_url)}">
              </div>
            </div>
            <div class="model-row">
              <div class="model-col full">
                <label>修改 API Key（留空保持不变）</label>
                <input type="password" class="e-key member-input" placeholder="sk-xxx">
              </div>
            </div>
            <div class="model-item-actions">
              ${ !active ? `<button class="btn btn-primary btn-sm e-activate">设为当前使用</button>` : '' }
              <button class="btn btn-ghost btn-sm e-save">保存修改</button>
              <button class="btn btn-ghost btn-sm e-test">测试连接</button>
              <button class="btn danger btn-sm e-delete">删除</button>
              <span class="e-test-msg model-test-msg"></span>
            </div>
          </div>
          <div class="model-item-toggle">
            <button class="btn icon-btn e-toggle"><i data-lucide="chevron-down"></i></button>
          </div>
        </div>
      `;
    }).join("");
    hydrateIcons();
    models.forEach(m => {
      const item = document.querySelector(`.model-item[data-id="${m.id}"]`);
      if (!item) return;
      item.querySelector(".e-toggle").addEventListener("click", () => toggleEdit(item));
      if (!m.is_active) {
        const act = item.querySelector(".e-activate");
        if (act) act.addEventListener("click", () => activateModel(m.id));
      }
      item.querySelector(".e-save").addEventListener("click", () => updateModel(m.id, item));
      item.querySelector(".e-test").addEventListener("click", () => testModelItem(m.id, item));
      item.querySelector(".e-delete").addEventListener("click", () => deleteModel(m.id));
    });
  }
  function toggleEdit(item) {
    const form = item.querySelector(".model-item-form");
    const icon = item.querySelector(".e-toggle i");
    const open = !form.classList.contains("hidden");
    form.classList.toggle("hidden");
    if (icon) icon.setAttribute("data-lucide", open ? "chevron-right" : "chevron-down");
    hydrateIcons();
  }
  async function importBootDefault(b) {
    try {
      const body = {
        display_name: "环境默认（.env）",
        provider: "env",
        model: (b.model || "").trim(),
        base_url: (b.base_url || "").trim(),
        api_key: "",
        is_active: true,
      };
      const r = await apiJson("/api/admin/model", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      renderModelList(r.models, r.active_id, null);
      toast("已将环境默认模型导入列表，可在此编辑");
    } catch (err) { toast(err.message, true); }
  }
  function bindNewModelSelect() {
    const sel = $("#nm-select");
    if (!sel) return;
    sel.addEventListener("change", () => {
      const opt = sel.options[sel.selectedIndex];
      if (sel.value) {
        $("#nm-model").value = sel.value;
        $("#nm-provider").value = opt.dataset.provider || "";
        if (opt.dataset.url) $("#nm-base").value = opt.dataset.url;
      }
    });
  }
  async function addModel() {
    const body = {
      display_name: ($("#nm-display").value || "").trim(),
      provider: ($("#nm-provider").value || "").trim(),
      model: ($("#nm-model").value || "").trim(),
      base_url: ($("#nm-base").value || "").trim(),
      api_key: ($("#nm-key").value || "").trim(),
    };
    try {
      const r = await apiJson("/api/admin/model", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      renderModelList(r.models, r.active_id);
      $("#nm-display").value = ""; $("#nm-provider").value = "";
      $("#nm-model").value = ""; $("#nm-base").value = ""; $("#nm-key").value = "";
      toast("添加成功");
    } catch (err) { toast(err.message, true); }
  }
  async function testNewModel() {
    const btn = $("#btn-test-new"), msg = $("#nm-test-msg");
    const body = {
      model: ($("#nm-model").value || "").trim(),
      base_url: ($("#nm-base").value || "").trim(),
      api_key: ($("#nm-key").value || "").trim(),
    };
    if (!btn || !msg) return;
    btn.disabled = true;
    msg.className = "model-test-msg";
    msg.textContent = "测试中…";
    try {
      const r = await apiJson("/api/admin/model/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (r.ok) { msg.className = "model-test-msg ok"; msg.textContent = `连接成功 · ${r.model} · ${r.latency_ms}ms`; }
      else { msg.className = "model-test-msg err"; msg.textContent = r.error || "连接失败"; }
    } catch (err) { msg.className = "model-test-msg err"; msg.textContent = err.message || "连接失败"; }
    finally { if (btn) btn.disabled = false; }
  }
  async function activateModel(id) {
    try {
      const r = await apiJson(`/api/admin/model/${id}/activate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      renderModelList(r.models, r.active_id);
      toast("已激活，对话立即使用新模型");
    } catch (err) { toast(err.message, true); }
  }
  async function updateModel(id, item) {
    const val = (s) => (item.querySelector(s).value || "").trim();
    const body = {
      display_name: val(".e-display") || null,
      provider: val(".e-provider") || null,
      model: val(".e-model") || null,
      base_url: val(".e-base") || null,
      api_key: val(".e-key") || null,
    };
    try {
      const r = await apiJson(`/api/admin/model/${id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      renderModelList(r.models, r.active_id);
      toast("修改已保存");
    } catch (err) { toast(err.message, true); }
  }
  async function testModelItem(id, item) {
    const btn = item.querySelector(".e-test"), msg = item.querySelector(".e-test-msg");
    const val = (s) => (item.querySelector(s).value || "").trim();
    const body = {
      model: val(".e-model") || null,
      base_url: val(".e-base") || null,
      api_key: val(".e-key") || null,
    };
    if (!btn || !msg) return;
    btn.disabled = true;
    msg.className = "model-test-msg";
    msg.textContent = "测试中…";
    try {
      const r = await apiJson("/api/admin/model/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (r.ok) { msg.className = "model-test-msg ok"; msg.textContent = `连接成功 · ${r.model} · ${r.latency_ms}ms`; }
      else { msg.className = "model-test-msg err"; msg.textContent = r.error || "连接失败"; }
    } catch (err) { msg.className = "model-test-msg err"; msg.textContent = err.message || "连接失败"; }
    finally { if (btn) btn.disabled = false; }
  }
  async function deleteModel(id) {
    if (!confirm("确认删除该模型配置？删除后不可恢复。")) return;
    try {
      const r = await apiJson(`/api/admin/model/${id}`, { method: "DELETE" });
      loadModelForm();
      toast("已删除");
    } catch (err) { toast(err.message, true); }
  }
  async function saveParams() {
    const body = {
      llm_temperature: parseFloat($("#p-temp").value),
      top_k: parseInt($("#p-topk").value, 10),
      score_threshold: parseFloat($("#p-threshold").value),
      history_turns: parseInt($("#p-history").value, 10),
      context_token_budget: parseInt($("#p-context").value, 10),
      max_context_hits: parseInt($("#p-maxhits").value, 10),
      query_rewrite: $("#p-rewrite").checked,
      suggest_questions: $("#p-suggest").checked, // W1：建议追问开关
      mmr_enabled: $("#p-mmr").checked,
      mmr_lambda: parseFloat($("#p-mmrl").value),
      rerank_enabled: $("#p-rerank").checked,
      rerank_model: ($("#p-rerankmodel").value || "").trim(),
      rerank_channel: $("#p-rerankchannel").value,
      rerank_external_url: ($("#p-rerankurl").value || "").trim(),
      rerank_api_key: ($("#p-rerankkey").value || "").trim(),
      hybrid_enabled: $("#p-hybrid").checked,
      hybrid_alpha: parseFloat($("#p-hybrida").value),
      hybrid_method: $("#p-hybridmethod").value,
      long_doc_summary: $("#p-longsum").checked,
      long_doc_chars: parseInt($("#p-longchars").value, 10),
    };
    try {
      await apiJson("/api/admin/model", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      toast("参数已保存");
    } catch (err) { toast(err.message, true); }
  }

  // R-07：Embedding 一致性检测与一键全量重建
  async function loadEmbeddingStatus() {
    const el = $("#emb-status");
    if (!el) return;
    try {
      const d = await apiJson("/api/admin/embedding-status");
      el.innerHTML = d.needs_rebuild
        ? `<div class="emb-warn"><i data-lucide="alert-triangle"></i> 当前 Embedding（${esc(d.current)}）与最近一次建索引所用（${esc(d.index_model || "未知")}）不一致，请重建全部索引。</div>`
        : `<div class="emb-ok">索引模型与 Embedding 一致（${esc(d.index_model || d.current)}）。</div>`;
      hydrateIcons();
    } catch (err) { /* 权限或暂时不可用则忽略 */ }
  }
  async function rebuildAll() {
    if (!confirm("将重建所有知识库的索引，期间检索可能较慢。继续？")) return;
    const btn = $("#btn-rebuild-all"), prog = $("#emb-rebuild-progress");
    if (btn) { btn.disabled = true; btn.innerHTML = "重建中…"; }
    if (prog) prog.textContent = "0%";
    try {
      const t = await apiJson("/api/admin/rebuild-all", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      let last = await apiJson(`/api/kb/tasks/${t.task_id}`);
      while (last && (last.status === "queued" || last.status === "running")) {
        await new Promise((r) => setTimeout(r, 800));
        last = await apiJson(`/api/kb/tasks/${t.task_id}`);
        if (prog && last) prog.textContent = `${last.progress}%`;
      }
      if (!last || last.status !== "done") throw new Error((last && last.detail) || "重建失败");
      toast("全部索引已重建：" + (last.detail || ""));
    } catch (err) { toast("重建全部索引失败：" + err.message, true); }
    finally {
      if (btn) { btn.disabled = false; btn.innerHTML = `<i data-lucide="refresh-cw"></i> 重建全部索引`; hydrateIcons(); }
      if (prog) prog.textContent = "";
      loadEmbeddingStatus();
    }
  }

  async function loadQuotaList() {
    try {
      const qs = await apiJson("/api/admin/quotas");
      const list = $("#quota-list");
      list.innerHTML = "";
      qs.forEach((q) => {
        const row = document.createElement("div");
        row.className = "member-row";
        row.innerHTML = `<span class="m-name">${esc(q.username)}</span><label class="q-in">会话上限 <input class="qn" data-k="max_sessions" type="number" min="1" value="${q.max_sessions}"></label><label class="q-in">容量MB <input class="qn" data-k="max_kb_mb" type="number" min="1" value="${q.max_kb_mb}"></label><button class="m-kick" data-save="${q.id}">保存</button>`;
        row.querySelector("[data-save]").addEventListener("click", async () => {
          const sessions = parseInt(row.querySelector('[data-k="max_sessions"]').value, 10);
          const mb = parseInt(row.querySelector('[data-k="max_kb_mb"]').value, 10);
          try { await apiJson(`/api/admin/quotas/${q.id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ max_sessions: sessions, max_kb_mb: mb }) }); toast(`已更新 ${q.username} 配额`); }
          catch (err) { toast(err.message, true); }
        });
        list.appendChild(row);
      });
    } catch (err) { toast(err.message, true); }
  }

  async function loadInvite() {
    try { $("#invite-code").value = (await apiJson("/api/auth/invite-code")).invite_code; }
    catch (err) { toast(err.message, true); }
  }
  $("#btn-save-invite").addEventListener("click", async () => {
    const code = $("#invite-code").value.trim();
    if (!code) return;
    try {
      await apiJson("/api/auth/invite-code", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ invite_code: code }) });
      toast("邀请码已更新");
    } catch (err) { toast(err.message, true); }
  });

  /* ---------- 其它 ---------- */
  $("#btn-logout").addEventListener("click", () => { if (confirm("确定退出登录？")) logout(); });
  $("#btn-theme").addEventListener("click", () => applyTheme(state.theme === "dark" ? "light" : "dark"));

  let toastTimer = null;
  function toast(msg, isError) {
    const t = $("#toast");
    t.textContent = msg;
    t.className = "toast show" + (isError ? " error" : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.className = "toast"; }, 3200);
  }

  /* ---------- 启动 ---------- */
  function boot() {
    initTheme();
    hydrateIcons();
    // Cookie 会话：无论是否登录都探测一次，未登录 / 过期则回登录页
    api("/api/auth/me").then((r) => r.json()).then((d) => {
      setIdentity(d.user.is_admin);
      $("#user-name").textContent = d.user.username;
      state.csrf = getCookie("mr_csrf");
      showApp();
      hydrateIcons();
    }).catch(() => { showAuth(); });
  }
  boot();
})();
