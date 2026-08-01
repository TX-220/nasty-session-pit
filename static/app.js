const state = {
  sessions: [],
  selectedId: null,
  query: "",
  total: 0,
  shown: 0,
  emptyCount: 0,
  hideEmpty: false,
  collapsedProjects: loadCollapsedProjects(),
  genres: [],
  genreColors: {},
  uncategorizedColor: "#6b6b78",
  // Direct session genre filter only
  genreFilter: loadGenreFilter(), // "" = all, genre label, or "Uncategorized"
};

const COLLAPSED_KEY = "vesper-hub-collapsed-projects";
const GENRE_FILTER_KEY = "vesper-hub-genre-filter";
const UNCATEGORIZED = "Uncategorized";
const DEFAULT_GENRES = [
  "Information Dominance",
  "Trade Bot",
  "Game Dev",
  "Security",
  "Infra & Environment",
  "Other",
];
const DEFAULT_GENRE_COLORS = {
  "Information Dominance": "#7c6cf0",
  "Trade Bot": "#4ade80",
  "Game Dev": "#f0c040",
  Security: "#f87171",
  "Infra & Environment": "#38bdf8",
  Other: "#888894",
};

const LEGACY_GENRE_FILTER = {
  未分類: "Uncategorized",
  情報制圧: "Information Dominance",
  トレードBOT: "Trade Bot",
  ゲーム開発: "Game Dev",
  セキュリティ: "Security",
  インフラ・環境: "Infra & Environment",
  その他: "Other",
};

function loadGenreFilter() {
  try {
    const v = localStorage.getItem(GENRE_FILTER_KEY);
    if (v === null) return "";
    return LEGACY_GENRE_FILTER[v] || v;
  } catch {
    return "";
  }
}

function saveGenreFilter() {
  try {
    localStorage.setItem(GENRE_FILTER_KEY, state.genreFilter || "");
  } catch {
    /* ignore */
  }
}

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
      "Forge server offline — run vesper-forge after reboot, or wait for autostart"
    );
  }
  setServerOnline(true);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

/**
 * Effective genre for filter/display: direct session.genre only, else Uncategorized.
 */
function sessionGenres(session) {
  if (!session) return [UNCATEGORIZED];
  const direct = (session.genre || "").trim();
  return direct ? [direct] : [UNCATEGORIZED];
}

function genreColor(name) {
  const g = (name || "").trim();
  if (!g) return state.uncategorizedColor || "#6b6b78";
  if (g === UNCATEGORIZED) return state.uncategorizedColor || "#6b6b78";
  return (
    state.genreColors[g] ||
    DEFAULT_GENRE_COLORS[g] ||
    state.uncategorizedColor ||
    "#888894"
  );
}

/** Soft tint styles for badges/selects from a hex color. */
function genreStyle(hex) {
  const c = hex || "#888894";
  return `color:${c};border-color:${c}66;background:${c}22`;
}

function genreOptionsHtml(selected, { includeUnset } = { includeUnset: true }) {
  const genres = state.genres.length ? state.genres : DEFAULT_GENRES;
  const sel = (selected || "").trim();
  const parts = [];
  if (includeUnset) {
    parts.push(
      `<option value=""${!sel ? " selected" : ""}>Unset</option>`
    );
  }
  for (const g of genres) {
    parts.push(
      `<option value="${escAttr(g)}"${g === sel ? " selected" : ""}>${esc(g)}</option>`
    );
  }
  return parts.join("");
}

function paintGenreSelect(sel, genreValue) {
  if (!sel) return;
  const g = (genreValue != null ? genreValue : sel.value || "").trim();
  sel.style.cssText = genreStyle(g ? genreColor(g) : state.uncategorizedColor);
}

function paintAllGenreSelects(root) {
  const scope = root || document;
  scope.querySelectorAll(".session-genre-select").forEach((sel) => {
    paintGenreSelect(sel, sel.value);
  });
  const detail = scope.querySelector ? scope.querySelector("#detail-genre") : null;
  if (detail) paintGenreSelect(detail, detail.value);
  paintGenreFilterSelect();
}

function paintGenreFilterSelect() {
  const sel = $("#genre-filter");
  if (!sel) return;
  const v = sel.value || "";
  if (!v) {
    sel.style.cssText = "";
    return;
  }
  if (v === UNCATEGORIZED) {
    sel.style.cssText = genreStyle(state.uncategorizedColor);
    return;
  }
  sel.style.cssText = genreStyle(genreColor(v));
}

function filterSessions(sessions, query) {
  let rows = sessions;
  if (state.genreFilter) {
    const g = state.genreFilter;
    rows = rows.filter((s) => sessionGenres(s).includes(g));
  }
  if (!query.trim()) return rows;
  const q = query.trim().toLowerCase();
  return rows.filter((s) => {
    const hay = [
      s.display_name,
      s.title,
      s.alias,
      s.id,
      s.short_id,
      s.project,
      s.project_display,
      s.cwd,
      ...sessionGenres(s),
    ]
      .join(" ")
      .toLowerCase();
    return hay.includes(q);
  });
}

function populateGenreFilterSelect() {
  const sel = $("#genre-filter");
  if (!sel) return;
  const genres = state.genres.length ? state.genres : DEFAULT_GENRES;
  const current = state.genreFilter;
  const opts = [
    `<option value="">All genres</option>`,
    ...genres.map(
      (g) =>
        `<option value="${escAttr(g)}"${g === current ? " selected" : ""}>● ${esc(g)}</option>`
    ),
    `<option value="${escAttr(UNCATEGORIZED)}"${current === UNCATEGORIZED ? " selected" : ""}>● ${esc(UNCATEGORIZED)}</option>`,
  ];
  sel.innerHTML = opts.join("");
  // Keep invalid saved filter from showing empty selection
  if (current && ![...genres, UNCATEGORIZED].includes(current)) {
    state.genreFilter = "";
    saveGenreFilter();
    sel.value = "";
  } else {
    sel.value = current || "";
  }
  paintGenreFilterSelect();
}

function onGenreFilterChange(value) {
  state.genreFilter = value || "";
  saveGenreFilter();
  paintGenreFilterSelect();
  renderList();
  updateSessionCount();
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
      if (e.target.closest(".session-genre-select")) return;
      selectSession(el.dataset.id);
    });
  });

  listEl.querySelectorAll(".session-genre-select").forEach((sel) => {
    paintGenreSelect(sel, sel.value);
    sel.addEventListener("click", (e) => e.stopPropagation());
    sel.addEventListener("change", async (e) => {
      e.stopPropagation();
      const id = sel.dataset.genreId;
      if (!id) return;
      paintGenreSelect(sel, sel.value);
      await saveSessionGenre(id, sel.value || "");
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
  const direct = (s.genre || "").trim();
  const gColor = genreColor(direct || UNCATEGORIZED);
  const lilithN = s.related_lilith_count ?? (s.related_lilith || []).length;
  const lilithHint =
    lilithN > 0
      ? `<span class="session-lilith-count" title="Related Lilith sessions: ${lilithN}">L${lilithN}</span>`
      : "";
  const genreBadge = direct
    ? `<span class="session-genre-badge" style="${genreStyle(gColor)}" title="Genre: ${escAttr(direct)}">${esc(direct)}</span>`
    : `<span class="session-genre-badge unset" style="${genreStyle(state.uncategorizedColor)}" title="Unset">Unset</span>`;
  return `
    <div class="session-item${active}${emptyClass}${nestedClass}" data-id="${s.id}">
      <button class="btn-icon pin-btn${pinClass}" data-id="${s.id}" title="Pin/unpin">★</button>
      <div class="session-item-body">
        <div class="session-item-name">${esc(s.display_name)}</div>
        <div class="session-item-meta">
          ${esc(s.updated_display)} · ${esc(s.short_id)}
          ${lilithHint}
          ${genreBadge}
          <select class="session-genre-select" data-genre-id="${escAttr(s.id)}" title="Direct genre" onclick="event.stopPropagation()" style="${genreStyle(gColor)}">
            ${genreOptionsHtml(direct, { includeUnset: true })}
          </select>
        </div>
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

  const lilithRefs = session.related_lilith || [];
  const lilithRows = lilithRefs.length
    ? lilithRefs
        .map((ref) => {
          const title = ref.title || ref.id;
          return `
      <div class="link-row">
        <div class="link-row-body">
          <div class="link-row-title">${esc(title)}</div>
          <div class="link-row-meta mono">${esc(ref.id)}</div>
        </div>
        <button class="btn btn-ghost btn-sm" type="button" data-unlink-lilith="${escAttr(ref.id)}" title="Unlink">Unlink</button>
      </div>`;
        })
        .join("")
    : `<div class="link-empty">None yet — add a session ID or URL and optional display name</div>`;

  const genreNote = (session.genre || "").trim()
    ? "Set directly"
    : "Unset (shows as Uncategorized in filters)";

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
        <div class="detail-genre-row">
          <label for="detail-genre">Genre</label>
          <select id="detail-genre" title="Direct genre for this Vesper session (manual)">
            ${genreOptionsHtml(session.genre || "", { includeUnset: true })}
          </select>
          <span class="detail-hint detail-genre-hint">${esc(genreNote)}</span>
        </div>
      </div>
      <div class="detail-summary-block">
        <div class="detail-summary-label">Summary</div>
        <div class="detail-summary">${esc(session.summary || session.preview || "")}</div>
      </div>
      <div class="link-section related-lilith-block">
        <div class="link-section-header">
          <h3>Related Lilith Sessions (${lilithRefs.length})</h3>
        </div>
        <div class="link-list">
          ${lilithRows}
        </div>
        <div class="link-add">
          <input id="related-lilith-id" type="text" placeholder="Lilith Session ID / URL" autocomplete="off">
          <input id="related-lilith-title" type="text" placeholder="Display Name (optional)" autocomplete="off">
          <button class="btn btn-secondary btn-sm" type="button" id="btn-link-lilith">Add</button>
        </div>
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

  const detailGenre = $("#detail-genre");
  if (detailGenre) {
    paintGenreSelect(detailGenre, session.genre || "");
    detailGenre.addEventListener("change", (e) => {
      paintGenreSelect(detailGenre, e.target.value || "");
      saveSessionGenre(session.id, e.target.value || "");
    });
  }

  $("#btn-open").addEventListener("click", () => openSession(session.id));
  $("#btn-copy").addEventListener("click", () => copyCommand(session.command));

  const obsBtnEl = $("#btn-obsidian");
  if (obsBtnEl) {
    obsBtnEl.addEventListener("click", () => openObsidianNote(session.id));
  }

  $("#btn-delete")?.addEventListener("click", () => deleteSession(session));

  $("#btn-link-lilith")?.addEventListener("click", () => linkRelatedLilith(session.id));
  $("#related-lilith-id")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") linkRelatedLilith(session.id);
  });
  $("#related-lilith-title")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") linkRelatedLilith(session.id);
  });

  detailEl.querySelectorAll("[data-unlink-lilith]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        const updated = await api(
          `/api/sessions/${session.id}/lilith/${encodeURIComponent(btn.dataset.unlinkLilith)}`,
          { method: "DELETE" }
        );
        applySessionUpdate(updated);
        showToast("Lilith link removed", "success");
      } catch (e) {
        showToast(`Unlink failed: ${e.message}`, "error");
      }
    });
  });
}

function applySessionUpdate(updated) {
  if (!updated || !updated.id) return;
  const idx = state.sessions.findIndex((s) => s.id === updated.id);
  if (idx >= 0) state.sessions[idx] = updated;
  renderList();
  if (state.selectedId === updated.id) {
    renderDetail(updated);
  }
}

async function linkRelatedLilith(sessionId) {
  const id = $("#related-lilith-id")?.value?.trim() || "";
  const title = $("#related-lilith-title")?.value?.trim() || "";
  if (!id) {
    showToast("Enter a Lilith ID / URL", "error");
    return;
  }
  try {
    const updated = await api(`/api/sessions/${sessionId}/lilith`, {
      method: "POST",
      body: JSON.stringify({ id, title }),
    });
    applySessionUpdate(updated);
    showToast("Lilith session linked", "success");
  } catch (e) {
    showToast(`Link failed: ${e.message}`, "error");
  }
}

function downloadCanvasFile(filename, canvas) {
  const text = JSON.stringify(canvas, null, 2) + "\n";
  const blob = new Blob([text], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename || "overview.canvas";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}


async function exportAllOverviewCanvas(kind) {
  // kind: "vesper" | "lilith" — independent of logical-session linking
  const isVesper = kind === "vesper";
  const btn = $(isVesper ? "#btn-all-vesper-canvas" : "#btn-all-lilith-canvas");
  const endpoint = isVesper ? "/api/canvas/all-vesper" : "/api/canvas/all-lilith";
  const idleLabel = isVesper ? "All Vesper Canvas" : "All Lilith Canvas";
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Exporting…";
  }
  try {
    const result = await api(endpoint, {
      method: "POST",
      body: JSON.stringify({ save: true }),
    });
    if (result.canvas && result.filename) {
      downloadCanvasFile(result.filename, result.canvas);
    }
    const n = result.session_count != null ? ` (${result.session_count} sessions)` : "";
    showToast(
      (result.message || "Canvas exported") + n,
      result.ok === false ? "error" : "success"
    );
  } catch (e) {
    showToast(`Canvas export failed: ${e.message}`, "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = idleLabel;
    }
  }
}

async function selectSession(id) {
  const session = state.sessions.find((s) => s.id === id);
  if (session) ensureProjectExpanded(session.cwd);
  state.selectedId = id;
  renderList();
  renderDetail(session || null);
}

function updateSessionCount() {
  const el = $("#session-count");
  const hideEl = $("#hide-empty");
  const purgeBtn = $("#btn-purge-empty");
  if (!el) return;
  const filtered = filterSessions(state.sessions, state.query);
  const projectCount = buildProjectGroups(filtered.filter((s) => !s.pinned)).length;
  const filterNote = state.genreFilter ? ` · ${state.genreFilter}` : "";
  if (state.genreFilter || state.query.trim()) {
    el.textContent = `${filtered.length} shown · ${state.shown}/${state.total}${filterNote}`;
  } else if (state.shown === state.total) {
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
  populateGenreFilterSelect();
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

function ingestGenrePayload(data) {
  if (!data) return;
  if (Array.isArray(data.genres)) state.genres = data.genres;
  if (data.colors && typeof data.colors === "object") {
    state.genreColors = { ...data.colors };
  } else if (Array.isArray(data.items)) {
    const map = {};
    for (const it of data.items) {
      if (it && it.name) map[it.name] = it.color || DEFAULT_GENRE_COLORS[it.name] || "#888894";
    }
    state.genreColors = map;
  }
  if (data.uncategorized_color) {
    state.uncategorizedColor = data.uncategorized_color;
  }
}

async function loadGenres() {
  try {
    const data = await api("/api/genres");
    ingestGenrePayload(data);
    populateGenreFilterSelect();
    if (!$("#genre-modal")?.classList.contains("hidden")) {
      renderGenreManageList();
    }
  } catch {
    /* keep defaults */
  }
}

function applyGenreCatalog(dataOrNames) {
  if (Array.isArray(dataOrNames)) {
    state.genres = dataOrNames;
  } else if (dataOrNames && typeof dataOrNames === "object") {
    ingestGenrePayload(dataOrNames);
  }
  if (state.genreFilter && state.genreFilter !== UNCATEGORIZED) {
    if (!state.genres.includes(state.genreFilter)) {
      state.genreFilter = "";
      saveGenreFilter();
    }
  }
  populateGenreFilterSelect();
  renderList();
  updateSessionCount();
  if (state.selectedId) {
    const s = state.sessions.find((x) => x.id === state.selectedId);
    if (s) renderDetail(s);
  }
  renderGenreManageList();
}

function openGenreModal() {
  const modal = $("#genre-modal");
  if (!modal) return;
  modal.classList.remove("hidden");
  renderGenreManageList();
  const input = $("#genre-new-name");
  const color = $("#genre-new-color");
  if (color && !color.value) color.value = "#7c6cf0";
  if (input) {
    input.value = "";
    input.focus();
  }
}

function closeGenreModal() {
  $("#genre-modal")?.classList.add("hidden");
}

function renderGenreManageList() {
  const list = $("#genre-manage-list");
  if (!list) return;
  const genres = state.genres.length ? state.genres : DEFAULT_GENRES;
  if (!genres.length) {
    list.innerHTML = `<div class="genre-manage-empty">No genres yet — add one above.</div>`;
    return;
  }
  list.innerHTML = genres
    .map((g) => {
      const c = genreColor(g);
      return `
    <div class="genre-manage-row" data-genre="${escAttr(g)}">
      <input type="color" class="genre-color-input" value="${escAttr(c)}" data-genre-color="${escAttr(g)}" title="Color for ${escAttr(g)}" aria-label="Color for ${escAttr(g)}">
      <span class="genre-manage-swatch" style="background:${escAttr(c)}"></span>
      <span class="genre-manage-name" title="${escAttr(g)}">${esc(g)}</span>
      <div class="genre-manage-actions">
        <button type="button" class="btn btn-ghost btn-sm" data-genre-rename="${escAttr(g)}">Rename</button>
        <button type="button" class="btn btn-danger btn-sm" data-genre-delete="${escAttr(g)}">Delete</button>
      </div>
    </div>`;
    })
    .join("");

  list.querySelectorAll("[data-genre-rename]").forEach((btn) => {
    btn.addEventListener("click", () => renameGenre(btn.dataset.genreRename));
  });
  list.querySelectorAll("[data-genre-delete]").forEach((btn) => {
    btn.addEventListener("click", () => deleteGenre(btn.dataset.genreDelete));
  });
  list.querySelectorAll("[data-genre-color]").forEach((input) => {
    input.addEventListener("change", () =>
      setGenreColor(input.dataset.genreColor, input.value)
    );
  });
}

async function addGenre() {
  const input = $("#genre-new-name");
  const colorEl = $("#genre-new-color");
  const name = (input?.value || "").trim();
  const color = colorEl?.value || "";
  if (!name) {
    showToast("Enter a genre name", "error");
    return;
  }
  try {
    const result = await api("/api/genres", {
      method: "POST",
      body: JSON.stringify({ name, color: color || null }),
    });
    applyGenreCatalog(result);
    if (input) input.value = "";
    showToast(`Genre added: ${result.name}`, "success");
  } catch (e) {
    showToast(`Add genre failed: ${e.message}`, "error");
  }
}

async function setGenreColor(name, color) {
  if (!name || !color) return;
  try {
    const result = await api("/api/genres/color", {
      method: "PUT",
      body: JSON.stringify({ name, color }),
    });
    applyGenreCatalog(result);
    showToast(`Color updated: ${result.name}`, "success");
  } catch (e) {
    showToast(`Color update failed: ${e.message}`, "error");
    renderGenreManageList();
  }
}

async function renameGenre(oldName) {
  if (!oldName) return;
  const next = prompt("Rename genre:", oldName);
  if (next === null) return;
  const newName = next.trim();
  if (!newName || newName === oldName) return;
  try {
    const result = await api("/api/genres", {
      method: "PATCH",
      body: JSON.stringify({ old_name: oldName, new_name: newName }),
    });
    if (state.genreFilter === oldName) {
      state.genreFilter = result.name || newName;
      saveGenreFilter();
    }
    applyGenreCatalog(result);
    await loadSessions();
    const n = result.updated_sessions ?? 0;
    showToast(
      n ? `Renamed to ${result.name} (${n} session${n === 1 ? "" : "s"})` : `Renamed to ${result.name}`,
      "success"
    );
  } catch (e) {
    showToast(`Rename failed: ${e.message}`, "error");
  }
}

async function deleteGenre(name) {
  if (!name) return;
  const ok = confirm(
    `Delete genre "${name}"?\n\nSessions using it will become Unset (Uncategorized in filters).`
  );
  if (!ok) return;
  try {
    const result = await api("/api/genres", {
      method: "DELETE",
      body: JSON.stringify({ name }),
    });
    if (state.genreFilter === name) {
      state.genreFilter = "";
      saveGenreFilter();
    }
    applyGenreCatalog(result);
    await loadSessions();
    const n = result.cleared_sessions ?? 0;
    showToast(
      n
        ? `Deleted "${result.name}" · cleared ${n} session${n === 1 ? "" : "s"}`
        : `Deleted "${result.name}"`,
      "success"
    );
  } catch (e) {
    showToast(`Delete genre failed: ${e.message}`, "error");
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

async function saveSessionGenre(id, genre) {
  try {
    const updated = await api(`/api/sessions/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ genre: genre || "" }),
    });
    applySessionUpdate(updated);
    const label = (updated.genre || "").trim() || "Unset";
    showToast(`Genre → ${label}`, "success");
  } catch (e) {
    showToast(`Genre save failed: ${e.message}`, "error");
    // Revert selects by re-render
    renderList();
    if (state.selectedId === id) {
      const s = state.sessions.find((x) => x.id === id);
      if (s) renderDetail(s);
    }
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
  updateSessionCount();
});

$("#genre-filter")?.addEventListener("change", (e) => {
  onGenreFilterChange(e.target.value);
});

$("#btn-manage-genres")?.addEventListener("click", openGenreModal);
$("#btn-genre-modal-close")?.addEventListener("click", closeGenreModal);
$("#btn-genre-add")?.addEventListener("click", addGenre);
$("#genre-new-name")?.addEventListener("keydown", (e) => {
  if (e.key === "Enter") addGenre();
});
$("#genre-modal")?.addEventListener("click", (e) => {
  if (e.target === $("#genre-modal")) closeGenreModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#genre-modal")?.classList.contains("hidden")) {
    closeGenreModal();
  }
});

$("#btn-new").addEventListener("click", newSession);
$("#btn-sync").addEventListener("click", syncObsidian);
$("#btn-all-vesper-canvas")?.addEventListener("click", () => exportAllOverviewCanvas("vesper"));
$("#btn-all-lilith-canvas")?.addEventListener("click", () => exportAllOverviewCanvas("lilith"));
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
Promise.all([loadGenres(), loadSessions()]).catch((e) =>
  showToast(`Load failed: ${e.message}`, "error")
);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") checkServerHealth();
});
setInterval(checkServerHealth, 15000);