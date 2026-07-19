const state = {
  sessions: [],
  selectedId: null,
  query: "",
  total: 0,
  shown: 0,
  emptyCount: 0,
  hideEmpty: false,
  collapsedProjects: loadCollapsedProjects(),
};

const COLLAPSED_KEY = "vesper-hub-collapsed-projects";

const $ = (sel) => document.querySelector(sel);
const listEl = $("#session-list");
const detailEl = $("#detail");
const searchEl = $("#search");
const toastEl = $("#toast");
const offlineBanner = $("#offline-banner");

let toastTimer = null;
let serverOnline = true;

function loadCollapsedProjects() {
  try {
    return new Set(JSON.parse(localStorage.getItem(COLLAPSED_KEY) || "[]"));
  } catch {
    return new Set();
  }
}

function saveCollapsedProjects() {
  localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...state.collapsedProjects]));
}

function projectKey(cwd) {
  return cwd || "(unknown)";
}

function setServerOnline(online) {
  serverOnline = online;
  if (offlineBanner) offlineBanner.classList.toggle("hidden", online);
}

function showToast(msg, type = "") {
  toastEl.textContent = msg;
  toastEl.className = `toast ${type}`;
  clearTimeout(toastTimer);
  const ms = type === "error" || (msg && msg.length > 80) ? 8000 : 4000;
  toastTimer = setTimeout(() => toastEl.classList.add("hidden"), ms);
}

async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
  } catch (e) {
    setServerOnline(false);
    throw new Error(
      "Forge server offline — PC再起動後は vesper-forge を実行するか autostart を待つ"
    );
  }
  setServerOnline(true);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

function filterSessions(sessions, query) {
  if (!query.trim()) return sessions;
  const q = query.trim().toLowerCase();
  return sessions.filter((s) => {
    const hay = [
      s.display_name,
      s.title,
      s.alias,
      s.id,
      s.short_id,
      s.project,
      s.project_display,
      s.cwd,
    ]
      .join(" ")
      .toLowerCase();
    return hay.includes(q);
  });
}

function buildProjectGroups(sessions) {
  const groups = new Map();
  for (const s of sessions) {
    const key = projectKey(s.cwd);
    if (!groups.has(key)) {
      groups.set(key, {
        cwd: s.cwd || "",
        key,
        label: s.project_display || s.project || key,
        path: s.project || key,
        sessions: [],
        latest: s.updated,
      });
    }
    const group = groups.get(key);
    group.sessions.push(s);
    if (s.updated > group.latest) group.latest = s.updated;
  }

  for (const group of groups.values()) {
    group.sessions.sort((a, b) => b.updated.localeCompare(a.updated));
  }

  return [...groups.values()].sort((a, b) => b.latest.localeCompare(a.latest));
}

function isProjectCollapsed(key, searching) {
  if (searching) return false;
  return state.collapsedProjects.has(key);
}

function ensureProjectExpanded(cwd) {
  const key = projectKey(cwd);
  if (state.collapsedProjects.delete(key)) {
    saveCollapsedProjects();
  }
}

function toggleProject(key) {
  if (state.collapsedProjects.has(key)) {
    state.collapsedProjects.delete(key);
  } else {
    state.collapsedProjects.add(key);
  }
  saveCollapsedProjects();
  renderList();
}

function projectGroupHtml(group, searching) {
  const collapsed = isProjectCollapsed(group.key, searching);
  const title = group.cwd ? `${group.label} — ${group.cwd}` : group.label;
  const pathHtml =
    group.label !== group.path
      ? `<span class="project-path">${esc(group.path)}</span>`
      : "";

  return `
    <div class="project-group${collapsed ? " collapsed" : ""}" data-project="${escAttr(group.key)}">
      <button class="project-header" type="button" title="${escAttr(title)} — double-click to rename">
        <span class="project-chevron" aria-hidden="true">▼</span>
        <span class="project-icon" aria-hidden="true"><svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor"><path d="M1.5 3.5A1.5 1.5 0 0 1 3 2h3.17a1.5 1.5 0 0 1 1.06.44L8.5 4H13a1.5 1.5 0 0 1 1.5 1.5v7A1.5 1.5 0 0 1 13 14H3a1.5 1.5 0 0 1-1.5-1.5v-9z"/></svg></span>
        <span class="project-label">${esc(group.label)}</span>
        ${pathHtml}
        <span class="project-count">${group.sessions.length}</span>
      </button>
      <div class="project-sessions">
        ${group.sessions.map((s) => sessionItemHtml(s, true)).join("")}
      </div>
    </div>`;
}

function renderList() {
  const searching = Boolean(state.query.trim());
  const filtered = filterSessions(state.sessions, state.query);
  const pinned = filtered.filter((s) => s.pinned);
  const unpinned = filtered.filter((s) => !s.pinned);
  const groups = buildProjectGroups(unpinned);

  let html = "";

  if (pinned.length) {
    html += `<div class="section-label">★ Pinned</div>`;
    html += pinned.map((s) => sessionItemHtml(s, false)).join("");
  }

  if (groups.length) {
    html += `<div class="section-label">Projects</div>`;
    html += groups.map((g) => projectGroupHtml(g, searching)).join("");
  }

  if (!filtered.length) {
    html = `<div class="section-label empty-list">No sessions found</div>`;
  }

  listEl.innerHTML = html;
  bindListEvents();
}

function bindListEvents() {
  listEl.querySelectorAll(".session-item").forEach((el) => {
    el.addEventListener("click", (e) => {
      if (e.target.closest(".pin-btn")) return;
      selectSession(el.dataset.id);
    });
  });

  listEl.querySelectorAll(".pin-btn").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      await togglePin(btn.dataset.id);
    });
  });

  listEl.querySelectorAll(".project-header").forEach((btn) => {
    btn.addEventListener("click", () => {
      const group = btn.closest(".project-group");
      if (!group) return;
      toggleProject(group.dataset.project);
    });

    btn.addEventListener("dblclick", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      const group = btn.closest(".project-group");
      if (!group) return;
      const key = group.dataset.project;
      const session = state.sessions.find((s) => projectKey(s.cwd) === key);
      if (!session) return;
      await renameProject(session.cwd, session.project_display || session.project);
    });
  });
}

function sessionItemHtml(s, nested) {
  const active = s.id === state.selectedId ? " active" : "";
  const pinClass = s.pinned ? " pinned" : "";
  const emptyClass = s.is_empty ? " empty" : "";
  const nestedClass = nested ? " nested" : "";
  return `
    <div class="session-item${active}${emptyClass}${nestedClass}" data-id="${s.id}">
      <button class="btn-icon pin-btn${pinClass}" data-id="${s.id}" title="Pin/unpin">★</button>
      <div class="session-item-body">
        <div class="session-item-name">${esc(s.display_name)}</div>
        <div class="session-item-meta">${esc(s.updated_display)} · ${esc(s.short_id)}</div>
      </div>
    </div>`;
}

function renderDetail(session) {
  if (!session) {
    detailEl.innerHTML = `<div class="empty-state"><p>Select a session from the sidebar</p></div>`;
    return;
  }

  const obsBtn = session.obsidian_path
    ? `<button class="btn btn-ghost" id="btn-obsidian" title="Open synced markdown note in Obsidian">Open note</button>`
    : `<span class="detail-hint">No Obsidian note — use Sync above</span>`;

  detailEl.innerHTML = `
    <div class="detail-content">
      <div class="detail-header">
        <div class="detail-title-row">
          <input class="detail-title" id="detail-title" value="${escAttr(session.display_name)}" title="Click to rename">
        </div>
        <div class="detail-meta">
          <span>Updated: ${esc(session.updated_display)}</span>
          <span>Messages: ${session.num_messages}</span>
          <span>Project: ${esc(session.project_display || session.project || "~")}</span>
          <span>Sandbox: ${esc(session.sandbox_profile || "off")}</span>
          <span class="mono">ID: ${esc(session.short_id)}…</span>
        </div>
      </div>
      <div class="detail-summary-block">
        <div class="detail-summary-label">要約</div>
        <div class="detail-summary">${esc(session.summary || session.preview || "")}</div>
      </div>
      <div class="detail-actions">
        <button class="btn btn-primary" id="btn-open">Open in tmux</button>
        <button class="btn btn-secondary" id="btn-copy">Copy command</button>
        ${obsBtn}
        <button class="btn btn-danger" id="btn-delete" title="Permanently delete this session (grok sessions delete)">Delete</button>
      </div>
    </div>`;

  $("#detail-title").addEventListener("blur", () => saveAlias(session.id));
  $("#detail-title").addEventListener("keydown", (e) => {
    if (e.key === "Enter") e.target.blur();
    if (e.key === "Escape") {
      e.target.value = session.display_name;
      e.target.blur();
    }
  });

  $("#btn-open").addEventListener("click", () => openSession(session.id));
  $("#btn-copy").addEventListener("click", () => copyCommand(session.command));

  const obsBtnEl = $("#btn-obsidian");
  if (obsBtnEl) {
    obsBtnEl.addEventListener("click", () => openObsidianNote(session.id));
  }

  $("#btn-delete")?.addEventListener("click", () => deleteSession(session));
}

function selectSession(id) {
  const session = state.sessions.find((s) => s.id === id);
  if (session) ensureProjectExpanded(session.cwd);
  state.selectedId = id;
  renderList();
  renderDetail(session);
}

function updateSessionCount() {
  const el = $("#session-count");
  const hideEl = $("#hide-empty");
  const purgeBtn = $("#btn-purge-empty");
  if (!el) return;
  const projectCount = buildProjectGroups(state.sessions.filter((s) => !s.pinned)).length;
  if (state.shown === state.total) {
    el.textContent = `${state.total} sessions · ${projectCount} projects`;
  } else {
    el.textContent = `${state.shown}/${state.total} · ${projectCount} projects`;
  }
  if (hideEl) hideEl.checked = state.hideEmpty;
  if (purgeBtn) {
    const n = state.emptyCount;
    purgeBtn.disabled = n === 0;
    purgeBtn.textContent = n ? `Purge empty (${n})` : "Purge empty";
  }
}

async function loadSessions() {
  const data = await api("/api/sessions");
  state.sessions = data.sessions;
  state.total = data.total;
  state.shown = data.shown;
  state.emptyCount = data.empty_count ?? 0;
  state.hideEmpty = data.hide_empty;
  renderList();
  updateSessionCount();
  if (state.selectedId) {
    const session = state.sessions.find((s) => s.id === state.selectedId);
    if (session) {
      renderDetail(session);
    } else {
      state.selectedId = null;
      renderDetail(null);
    }
  }
}

async function renameProject(cwd, currentLabel) {
  const alias = prompt("Project display name:", currentLabel || "");
  if (alias === null) return;
  try {
    const result = await api("/api/projects/alias", {
      method: "PATCH",
      body: JSON.stringify({ cwd, alias: alias.trim() }),
    });
    state.sessions = result.sessions || state.sessions;
    renderList();
    updateSessionCount();
    if (state.selectedId) {
      const session = state.sessions.find((s) => s.id === state.selectedId);
      if (session) renderDetail(session);
    }
    showToast("Project name saved", "success");
  } catch (e) {
    showToast(`Project rename failed: ${e.message}`, "error");
  }
}

async function refreshSessions() {
  try {
    const result = await api("/api/refresh", { method: "POST" });
    await loadSessions();
    showToast(result.message, result.ok ? "success" : "error");
  } catch (e) {
    showToast(`Refresh failed: ${e.message}`, "error");
  }
}

async function deleteSession(session) {
  const label = session.display_name || session.short_id;
  const ok = confirm(
    `Delete session "${label}"?\n\nThis uses grok sessions delete and cannot be undone.`
  );
  if (!ok) return;

  try {
    const result = await api(`/api/sessions/${session.id}`, { method: "DELETE" });
    if (state.selectedId === session.id) {
      state.selectedId = null;
    }
    await loadSessions();
    renderDetail(null);
    showToast(result.message, result.ok ? "success" : "error");
  } catch (e) {
    showToast(`Delete failed: ${e.message}`, "error");
  }
}

async function purgeEmptySessions() {
  const n = state.emptyCount;
  if (!n) {
    showToast("No empty sessions on disk — use Delete on individual sessions", "success");
    return;
  }
  const ok = confirm(
    `Delete ${n} empty session(s) from ~/.grok/sessions?\n\nThis uses grok sessions delete and cannot be undone.`
  );
  if (!ok) return;

  const btn = $("#btn-purge-empty");
  if (btn) btn.disabled = true;

  try {
    const result = await api("/api/sessions/purge-empty", { method: "POST" });
    await loadSessions();
    showToast(result.message, result.ok ? "success" : "error");
  } catch (e) {
    showToast(`Purge failed: ${e.message}`, "error");
    updateSessionCount();
  }
}

async function toggleHideEmpty(checked) {
  try {
    await api("/api/settings", {
      method: "PATCH",
      body: JSON.stringify({ hide_empty: checked }),
    });
    await loadSessions();
  } catch (e) {
    showToast(`Settings failed: ${e.message}`, "error");
  }
}

async function saveAlias(id) {
  const input = $("#detail-title");
  if (!input) return;
  const alias = input.value.trim();
  try {
    const updated = await api(`/api/sessions/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ alias }),
    });
    const idx = state.sessions.findIndex((s) => s.id === id);
    if (idx >= 0) state.sessions[idx] = updated;
    renderList();
    renderDetail(updated);
    showToast("Name saved", "success");
  } catch (e) {
    showToast(`Save failed: ${e.message}`, "error");
  }
}

async function togglePin(id) {
  try {
    const updated = await api(`/api/sessions/${id}/pin`, { method: "POST" });
    const idx = state.sessions.findIndex((s) => s.id === id);
    if (idx >= 0) state.sessions[idx] = updated;
    renderList();
    if (state.selectedId === id) renderDetail(updated);
  } catch (e) {
    showToast(`Pin failed: ${e.message}`, "error");
  }
}

async function openSession(id) {
  try {
    const result = await api(`/api/sessions/${id}/open`, { method: "POST" });
    if (result.ok) {
      showToast(result.message, "success");
    } else {
      showToast(result.message, "error");
      copyCommand(result.command);
    }
  } catch (e) {
    showToast(`Open failed: ${e.message}`, "error");
  }
}

async function newSession() {
  try {
    const result = await api("/api/sessions/new", { method: "POST" });
    if (result.ok) {
      showToast(result.message, "success");
    } else {
      showToast(result.message, "error");
      copyCommand(result.command);
    }
  } catch (e) {
    showToast(`New session failed: ${e.message}`, "error");
  }
}

async function openObsidianNote(id) {
  try {
    const result = await api(`/api/sessions/${id}/obsidian`, { method: "POST" });
    showToast(result.message, result.ok ? "success" : "error");
  } catch (e) {
    showToast(`Open note failed: ${e.message}`, "error");
  }
}

async function syncObsidian() {
  const btn = $("#btn-sync");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Syncing…";
  }
  try {
    const result = await api("/api/sync", { method: "POST" });
    showToast(result.message, result.ok ? "success" : "error");
    if (result.ok) {
      // Refresh list so "Open note" appears for newly exported sessions
      await loadSessions();
    }
  } catch (e) {
    showToast(`Sync failed: ${e.message}`, "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Sync to Obsidian";
    }
  }
}

function copyCommand(cmd) {
  navigator.clipboard.writeText(cmd).then(
    () => showToast(`Copied: ${cmd}`, "success"),
    () => showToast("Clipboard copy failed", "error")
  );
}

function esc(str) {
  const d = document.createElement("div");
  d.textContent = str || "";
  return d.innerHTML;
}

function escAttr(str) {
  return (str || "").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

searchEl.addEventListener("input", () => {
  state.query = searchEl.value;
  renderList();
});

$("#btn-new").addEventListener("click", newSession);
$("#btn-sync").addEventListener("click", syncObsidian);
$("#btn-refresh").addEventListener("click", refreshSessions);
$("#hide-empty")?.addEventListener("change", (e) => toggleHideEmpty(e.target.checked));
$("#btn-purge-empty")?.addEventListener("click", purgeEmptySessions);

function initSidebarResize() {
  const panel = $("#sidebar-panel");
  const resizer = $("#sidebar-resizer");
  if (!panel || !resizer) return;

  const MIN = 180;
  const DEFAULT = 300;
  const STORAGE_KEY = "vesper-hub-sidebar-w";

  const detailMin = () => {
    const v = getComputedStyle(document.documentElement).getPropertyValue("--detail-min");
    const n = parseInt(v, 10);
    return Number.isFinite(n) ? n : 120;
  };

  const maxWidth = () => {
    const main = panel.parentElement;
    if (!main) return window.innerWidth - detailMin();
    return Math.max(MIN, main.getBoundingClientRect().width - detailMin());
  };

  const clamp = (w) => Math.min(maxWidth(), Math.max(MIN, w));

  const setWidth = (w) => {
    panel.style.width = `${clamp(w)}px`;
  };

  const saved = Number(localStorage.getItem(STORAGE_KEY));
  if (saved >= MIN) setWidth(saved);

  window.addEventListener("resize", () => {
    setWidth(panel.offsetWidth);
  });

  let dragging = false;

  const stop = () => {
    if (!dragging) return;
    dragging = false;
    resizer.classList.remove("active");
    document.body.classList.remove("sidebar-resizing");
    localStorage.setItem(STORAGE_KEY, String(panel.offsetWidth));
  };

  resizer.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    dragging = true;
    resizer.classList.add("active");
    document.body.classList.add("sidebar-resizing");
    e.preventDefault();
  });

  document.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const main = panel.parentElement;
    if (!main) return;
    setWidth(e.clientX - main.getBoundingClientRect().left);
  });

  document.addEventListener("mouseup", stop);
  window.addEventListener("blur", stop);

  resizer.addEventListener("dblclick", () => {
    setWidth(DEFAULT);
    localStorage.setItem(STORAGE_KEY, String(DEFAULT));
  });
}

async function checkServerHealth() {
  const wasOnline = serverOnline;
  try {
    const res = await fetch("/api/sessions");
    setServerOnline(res.ok);
    if (res.ok && !wasOnline) await loadSessions();
  } catch {
    setServerOnline(false);
  }
}

initSidebarResize();
loadSessions().catch((e) => showToast(`Load failed: ${e.message}`, "error"));
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") checkServerHealth();
});
setInterval(checkServerHealth, 15000);