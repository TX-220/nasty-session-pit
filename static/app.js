const state = {
  sessions: [],
  selectedId: null,
  query: "",
  total: 0,
  shown: 0,
  emptyCount: 0,
  hideEmpty: false,
  collapsedGenres: loadCollapsedGenres(),
  genres: [],
  genreColors: {},
  uncategorizedColor: "#6b6b78",
  sandbox: null,
};

/** Forge UI sandbox modes (must match backend UI_PROFILES). Fortress-first default. */
const SANDBOX_MODES = ["maxsec", "coding", "off"];
const DEFAULT_SANDBOX = "maxsec";

function buildNewCommand(profile) {
  const p = (profile || DEFAULT_SANDBOX).trim() || DEFAULT_SANDBOX;
  return `GROK_SANDBOX=${p} grok --sandbox ${p}`;
}

function buildResumeCommand(sessionId, profile) {
  const p = (profile || DEFAULT_SANDBOX).trim() || DEFAULT_SANDBOX;
  return `GROK_SANDBOX=${p} grok -r ${sessionId} --sandbox ${p}`;
}

/** Session launch sandbox from summary.json (read-only; fixed at launch). */
function sessionSandbox(session) {
  const p = (session?.sandbox_profile || DEFAULT_SANDBOX).trim() || DEFAULT_SANDBOX;
  return p;
}

function sandboxBadgeHtml(profile, { title, extraClass } = {}) {
  const p = (profile || DEFAULT_SANDBOX).trim() || DEFAULT_SANDBOX;
  const label = p === "off" ? "OFF" : p;
  const t =
    title ||
    `Sandbox fixed at launch: ${p} (cannot change on an existing session)`;
  const cls = extraClass ? ` ${extraClass}` : "";
  return `<span class="sandbox-badge sandbox-${escAttr(p)}${cls}" title="${escAttr(t)}" data-sandbox="${escAttr(p)}">${esc(label)}</span>`;
}

const COLLAPSED_GENRES_KEY = "vesper-hub-collapsed-genres";
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

const $ = (sel) => document.querySelector(sel);
const listEl = $("#session-list");
const detailEl = $("#detail");
const searchEl = $("#search");
const toastEl = $("#toast");
const offlineBanner = $("#offline-banner");

let toastTimer = null;
let serverOnline = true;

function loadCollapsedGenres() {
  try {
    return new Set(JSON.parse(localStorage.getItem(COLLAPSED_GENRES_KEY) || "[]"));
  } catch {
    return new Set();
  }
}

function saveCollapsedGenres() {
  localStorage.setItem(COLLAPSED_GENRES_KEY, JSON.stringify([...state.collapsedGenres]));
}

/** Direct session genre label, or Uncategorized when unset. */
function sessionGenreKey(session) {
  const direct = (session?.genre || "").trim();
  return direct || UNCATEGORIZED;
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
      "Nasty Session Pit offline — run vesper-forge after reboot, or wait for autostart"
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
      ...sessionGenres(s),
    ]
      .join(" ")
      .toLowerCase();
    return hay.includes(q);
  });
}

/**
 * Group sessions by direct genre. Order: catalog → extra used genres → Uncategorized.
 * Empty genres (0 sessions) are omitted.
 */
function buildGenreGroups(sessions) {
  const groups = new Map();
  for (const s of sessions) {
    const key = sessionGenreKey(s);
    if (!groups.has(key)) {
      groups.set(key, { key, label: key, sessions: [] });
    }
    groups.get(key).sessions.push(s);
  }

  for (const group of groups.values()) {
    group.sessions.sort((a, b) => b.updated.localeCompare(a.updated));
  }

  const catalog = state.genres.length ? state.genres : DEFAULT_GENRES;
  const catalogSet = new Set(catalog);
  const ordered = [];

  for (const g of catalog) {
    if (groups.has(g)) ordered.push(groups.get(g));
  }

  const extras = [...groups.keys()]
    .filter((k) => k !== UNCATEGORIZED && !catalogSet.has(k))
    .sort((a, b) => a.localeCompare(b));
  for (const k of extras) ordered.push(groups.get(k));

  if (groups.has(UNCATEGORIZED)) ordered.push(groups.get(UNCATEGORIZED));

  return ordered;
}

function isGenreCollapsed(key, searching) {
  if (searching) return false;
  return state.collapsedGenres.has(key);
}

function ensureGenreExpanded(genreKey) {
  const key = genreKey || UNCATEGORIZED;
  if (state.collapsedGenres.delete(key)) {
    saveCollapsedGenres();
  }
}

function toggleGenre(key) {
  if (state.collapsedGenres.has(key)) {
    state.collapsedGenres.delete(key);
  } else {
    state.collapsedGenres.add(key);
  }
  saveCollapsedGenres();
  renderList();
}

function genreGroupHtml(group, searching) {
  const collapsed = isGenreCollapsed(group.key, searching);
  const color = genreColor(group.key);
  return `
    <div class="genre-group${collapsed ? " collapsed" : ""}" data-genre="${escAttr(group.key)}">
      <button class="genre-header" type="button" title="${escAttr(group.label)}">
        <span class="genre-chevron" aria-hidden="true">▼</span>
        <span class="genre-dot" style="background:${escAttr(color)}" aria-hidden="true"></span>
        <span class="genre-label">${esc(group.label)}</span>
        <span class="genre-count">${group.sessions.length}</span>
      </button>
      <div class="genre-sessions">
        ${group.sessions.map((s) => sessionItemHtml(s, true)).join("")}
      </div>
    </div>`;
}

function renderList() {
  const searching = Boolean(state.query.trim());
  const filtered = filterSessions(state.sessions, state.query);
  const groups = buildGenreGroups(filtered);

  let html = "";

  if (groups.length) {
    html = groups.map((g) => genreGroupHtml(g, searching)).join("");
  } else {
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

  listEl.querySelectorAll(".genre-header").forEach((btn) => {
    btn.addEventListener("click", () => {
      const group = btn.closest(".genre-group");
      if (!group) return;
      toggleGenre(group.dataset.genre);
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
  const sb = sessionSandbox(s);
  const sandboxPill = sandboxBadgeHtml(sb, {
    title: `Launch sandbox: ${sb} (fixed — cannot change on this session)`,
    extraClass: "sandbox-badge-list",
  });
  return `
    <div class="session-item${active}${emptyClass}${nestedClass}" data-id="${s.id}">
      <button class="btn-icon pin-btn${pinClass}" data-id="${s.id}" title="Pin/unpin">★</button>
      <div class="session-item-body">
        <div class="session-item-name">${esc(s.display_name)}</div>
        <div class="session-item-meta">
          ${esc(s.updated_display)} · ${esc(s.short_id)}
          ${sandboxPill}
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

  const sb = sessionSandbox(session);
  const sbLabel = sb === "off" ? "OFF" : sb;
  // Include non-UI launch profiles (e.g. lilith) so ★ can still mark the original.
  const resumeModes = SANDBOX_MODES.includes(sb) ? SANDBOX_MODES : [sb, ...SANDBOX_MODES];
  const resumeButtons = resumeModes.map((p) => {
    const isOrig = p === sb;
    const label = p === "off" ? "off" : p;
    const mark = isOrig ? " ★" : "";
    const cls = isOrig ? "btn-copy-cmd is-session-sandbox" : "btn-copy-cmd";
    const tip = isOrig
      ? `Copy resume with this session’s launch sandbox (${p}) — recommended\n${buildResumeCommand(session.id, p)}`
      : `Copy resume using ${p} for the NEW process only (does not rewrite this session’s recorded sandbox)\n${buildResumeCommand(session.id, p)}`;
    return `<button type="button" class="${cls}" data-copy-resume="${escAttr(p)}" title="${escAttr(tip)}">${esc(label)}${mark}</button>`;
  }).join("");

  detailEl.innerHTML = `
    <div class="detail-content">
      <div class="detail-header">
        <div class="detail-title-row">
          <input class="detail-title" id="detail-title" value="${escAttr(session.display_name)}" title="Click to rename">
          <div class="session-sandbox-pill" title="Recorded at launch from summary.json — cannot be changed on this session">
            <span class="session-sandbox-pill-label">Session sandbox</span>
            ${sandboxBadgeHtml(sb, { extraClass: "sandbox-badge-lg" })}
            <span class="session-sandbox-pill-note">fixed at launch</span>
          </div>
        </div>
        <div class="detail-meta">
          <span>Updated: ${esc(session.updated_display)}</span>
          <span>Messages: ${session.num_messages}</span>
          <span>Project: ${esc(session.project_display || session.project || "~")}</span>
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
        ${obsBtn}
        <button class="btn btn-danger" id="btn-delete" title="Permanently delete this session (grok sessions delete)">Delete</button>
      </div>
      <div class="detail-copy-block">
        <div class="detail-copy-session-sb">
          <span class="detail-copy-session-sb-label">This session launched as</span>
          ${sandboxBadgeHtml(sb, { extraClass: "sandbox-badge-lg" })}
          <span class="detail-copy-session-sb-note">read-only · ★ = match launch sandbox</span>
        </div>
        <p class="detail-copy-hint">
          <strong>Copy resume</strong> starts a <em>new process</em>. Buttons choose sandbox for that process only —
          they do <strong>not</strong> change this session’s recorded sandbox (${esc(sbLabel)}).
        </p>
        <div class="detail-copy-row">
          <div class="copy-cmd-group" role="group" aria-label="Copy resume commands (sandbox for new process)">
            <span class="copy-cmd-label">Copy resume</span>
            ${resumeButtons}
          </div>
        </div>
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

  detailEl.querySelectorAll("[data-copy-resume]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const profile = btn.dataset.copyResume;
      copyCommand(buildResumeCommand(session.id, profile), `resume · ${profile}`);
    });
  });

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
  if (session) ensureGenreExpanded(sessionGenreKey(session));
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
  const genreCount = buildGenreGroups(filtered).length;
  if (state.query.trim()) {
    el.textContent = `${filtered.length} shown · ${state.shown}/${state.total}`;
  } else if (state.shown === state.total) {
    el.textContent = `${state.total} sessions · ${genreCount} genres`;
  } else {
    el.textContent = `${state.shown}/${state.total} · ${genreCount} genres`;
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
    if (state.collapsedGenres.has(oldName)) {
      state.collapsedGenres.delete(oldName);
      state.collapsedGenres.add(result.name || newName);
      saveCollapsedGenres();
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
    if (state.collapsedGenres.delete(name)) {
      saveCollapsedGenres();
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

function paintSandboxModes(status) {
  const group = $("#sandbox-modes");
  if (!group || !status) return;
  state.sandbox = status;
  const profile = status.profile || DEFAULT_SANDBOX;
  group.classList.toggle("is-mismatch", status.sources_match === false);
  group.querySelectorAll(".btn-sandbox-mode").forEach((btn) => {
    const id = btn.dataset.sandbox;
    const active = id === profile;
    btn.classList.toggle("is-active", active);
    btn.setAttribute("aria-pressed", active ? "true" : "false");
  });
  const mismatch =
    status.sources_match === false
      ? ` Config=${status.config_profile} · bashrc=${status.bashrc_profile}.`
      : "";
  group.title =
    `NEW DEFAULT for plain \`grok\` (writes config.toml; ~/bin/grok applies it immediately).` +
    ` Does not change existing sessions.` +
    ` Effective default: ${profile}.${mismatch}` +
    ` One-shot override: grok --sandbox off|coding|maxsec.` +
    ` Copy new still copies an explicit command.`;
}

async function loadSandboxStatus() {
  try {
    const status = await api("/api/sandbox");
    paintSandboxModes(status);
  } catch (e) {
    const group = $("#sandbox-modes");
    if (group) group.classList.add("is-mismatch");
  }
}

async function setSandboxProfile(profile) {
  const group = $("#sandbox-modes");
  const buttons = group ? [...group.querySelectorAll(".btn-sandbox-mode")] : [];
  buttons.forEach((b) => {
    b.disabled = true;
  });
  try {
    const status = await api("/api/sandbox", {
      method: "PATCH",
      body: JSON.stringify({ profile }),
    });
    paintSandboxModes(status);
    showToast(
      status.message || `New-session default → ${status.profile}`,
      "success"
    );
    // Existing sessions keep launch sandbox — no list reload needed.
  } catch (e) {
    showToast(`Sandbox set failed: ${e.message}`, "error");
  } finally {
    buttons.forEach((b) => {
      b.disabled = false;
    });
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

function copyCommand(cmd, label = "") {
  const toastLabel = label ? `${label}: ${cmd}` : cmd;
  navigator.clipboard.writeText(cmd).then(
    () => showToast(`Copied: ${toastLabel}`, "success"),
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

$("#btn-sync").addEventListener("click", syncObsidian);
$("#btn-all-vesper-canvas")?.addEventListener("click", () => exportAllOverviewCanvas("vesper"));
$("#btn-all-lilith-canvas")?.addEventListener("click", () => exportAllOverviewCanvas("lilith"));
$("#btn-refresh").addEventListener("click", refreshSessions);
$("#sandbox-modes")?.addEventListener("click", (e) => {
  const btn = e.target.closest(".btn-sandbox-mode");
  if (!btn || btn.disabled) return;
  const profile = btn.dataset.sandbox;
  if (!profile) return;
  if (state.sandbox && state.sandbox.profile === profile) return;
  setSandboxProfile(profile);
});

$("#copy-new-cmds")?.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-copy-new]");
  if (!btn) return;
  const profile = btn.dataset.copyNew;
  if (!profile) return;
  copyCommand(buildNewCommand(profile), `new · ${profile}`);
});
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
Promise.all([loadGenres(), loadSessions(), loadSandboxStatus()]).catch((e) =>
  showToast(`Load failed: ${e.message}`, "error")
);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") checkServerHealth();
});
setInterval(checkServerHealth, 15000);