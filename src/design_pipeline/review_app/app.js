const state = { status: null, artifacts: [], selected: null, currentMockup: 0, projectId: "default", projects: [] };
let fullscreenMockupOpen = false;
const $ = (selector) => document.querySelector(selector);
// Paths that should NOT be auto-prefixed with the current project scope
// (they either target the project registry itself, or are UI assets).
const UNSCOPED_PATHS = /^\/(projects|review-assets)(?:$|\/|\?)/;
function scopedPath(path) {
  if (!path.startsWith("/")) return path;
  if (UNSCOPED_PATHS.test(path)) return path;
  return `/projects/${encodeURIComponent(state.projectId)}${path}`;
}
const api = async (path, options = {}) => {
  const url = scopedPath(path);
  const response = await fetch(url, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
  if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.detail || `Request failed (${response.status})`); }
  return response.status === 204 ? null : response.json();
};
const titleCase = (value = "") => value.replaceAll("-", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());

// Curated, text/tool-calling-capable models per live provider -- every `id`
// is EXACTLY the string that provider's API expects in the "model" request
// field (verified against each provider's own docs, Sept 2026), so picking
// one from this dropdown always works with no env-var editing. Image/audio/
// video/embedding/moderation-only models are deliberately excluded: this app
// only ever does structured JSON + tool-calling text generation, and letting
// someone select e.g. an image model here would just produce a confusing
// live-provider error. Update this list if a provider ships new IDs.
const MODEL_CATALOG = {
  openai: [
    { group: "Flagship", models: [
      ["gpt-6-astra", "GPT-6 Astra"], ["gpt-5.6-sol", "GPT-5.6 Sol"], ["gpt-5.6-terra", "GPT-5.6 Terra"], ["gpt-5.6-luna", "GPT-5.6 Luna"],
      ["gpt-5.5-pro", "GPT-5.5 Pro"], ["gpt-5.5", "GPT-5.5"],
      ["gpt-5.4-pro", "GPT-5.4 Pro"], ["gpt-5.4", "GPT-5.4"], ["gpt-5.4-mini", "GPT-5.4 Mini"], ["gpt-5.4-nano", "GPT-5.4 Nano"],
      ["gpt-5.2-pro", "GPT-5.2 Pro"], ["gpt-5.2", "GPT-5.2"], ["gpt-5.1", "GPT-5.1"],
      ["gpt-5-pro", "GPT-5 Pro"], ["gpt-5", "GPT-5"], ["gpt-5-mini", "GPT-5 Mini"], ["gpt-5-nano", "GPT-5 Nano"],
    ] },
    { group: "Reasoning (o-series, legacy)", models: [
      ["o3-pro", "o3 Pro"], ["o3", "o3"], ["o3-mini", "o3 Mini"], ["o4-mini", "o4 Mini"], ["o1-pro", "o1 Pro"], ["o1", "o1"],
    ] },
    { group: "GPT-4.1 / GPT-4o (legacy)", models: [
      ["gpt-4.1", "GPT-4.1"], ["gpt-4.1-mini", "GPT-4.1 Mini"], ["gpt-4.1-nano", "GPT-4.1 Nano"], ["gpt-4o", "GPT-4o"], ["gpt-4o-mini", "GPT-4o Mini"],
    ] },
  ],
  anthropic: [
    { group: "Current", models: [
      ["claude-opus-5", "Claude Opus 5"], ["claude-fable-5-1", "Claude Fable 5.1"], ["claude-sonnet-5", "Claude Sonnet 5"], ["claude-haiku-4-5-20251001", "Claude Haiku 4.5"],
    ] },
    { group: "Legacy", models: [
      ["claude-fable-5", "Claude Fable 5"], ["claude-opus-4-8", "Claude Opus 4.8"], ["claude-opus-4-7", "Claude Opus 4.7"], ["claude-opus-4-6", "Claude Opus 4.6"],
      ["claude-sonnet-4-6", "Claude Sonnet 4.6"], ["claude-sonnet-4-5", "Claude Sonnet 4.5"],
    ] },
  ],
  gemini: [
    { group: "Gemini 3.x", models: [
      ["gemini-3.8-flash", "Gemini 3.8 Flash"], ["gemini-3.7-flash", "Gemini 3.7 Flash"], ["gemini-3.6-flash", "Gemini 3.6 Flash"],
      ["gemini-3.5-flash", "Gemini 3.5 Flash"], ["gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite"], ["gemini-3.1-flash-lite", "Gemini 3.1 Flash-Lite"],
      ["gemini-3.1-pro-preview", "Gemini 3.1 Pro (Preview)"], ["gemini-3-flash-preview", "Gemini 3 Flash (Preview)"],
    ] },
    { group: "Gemini 2.5 (legacy)", models: [
      ["gemini-2.5-pro", "Gemini 2.5 Pro"], ["gemini-2.5-flash", "Gemini 2.5 Flash"], ["gemini-2.5-flash-lite", "Gemini 2.5 Flash-Lite"],
    ] },
  ],
};

// Rebuilds #model-select's options for `provider`, selecting `currentModel`.
// A model already configured (e.g. via .env before this dropdown existed)
// that isn't in our curated list is never silently dropped -- it's shown as
// a synthetic "Custom: <id>" option instead, so switching provider dropdowns
// can't quietly change what's actually configured.
function populateModelSelect(provider, currentModel) {
  const select = $("#model-select");
  if (!select) return;
  select.innerHTML = "";
  if (provider === "stub" || !MODEL_CATALOG[provider]) {
    select.disabled = true;
    select.innerHTML = `<option value="">n/a for stub</option>`;
    return;
  }
  select.disabled = false;
  const groups = MODEL_CATALOG[provider];
  let found = false;
  const optgroupsHtml = groups.map(({ group, models }) => {
    const options = models.map(([id, label]) => {
      if (id === currentModel) found = true;
      return `<option value="${escapeHtml(id)}" ${id === currentModel ? "selected" : ""}>${escapeHtml(label)}</option>`;
    }).join("");
    return `<optgroup label="${escapeHtml(group)}">${options}</optgroup>`;
  }).join("");
  const customOption = (currentModel && !found) ? `<option value="${escapeHtml(currentModel)}" selected>Custom: ${escapeHtml(currentModel)}</option>` : "";
  select.innerHTML = customOption + optgroupsHtml;
}
// Downloads a file-returning GET endpoint (a .zip export, so far) as a real
// browser download. Deliberately not a plain `location.href = url` link:
// on a 404 (e.g. exporting before a data model/mockups exist) that would
// navigate the whole SPA away to a raw JSON error page instead of just
// showing a notice -- fetch + blob keeps the app in place either way.
async function downloadExport(path, fallbackName) {
  try {
    const response = await fetch(scopedPath(path));
    if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.detail || `Export failed (${response.status})`); }
    const blob = await response.blob();
    const filename = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") || "")?.[1] || fallbackName;
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url; link.download = filename;
    document.body.appendChild(link); link.click(); link.remove();
    URL.revokeObjectURL(url);
  } catch (error) { showNotice(error.message, true); }
}
// escapeHtml, screenName, screenId, buildScreenList now live in
// screen_tree.js (loaded before this file) -- pulled out into their own
// DOM-free module so buildScreenList's navigation-tree logic is unit-
// testable in plain Node. See that file's own header comment.
let noticeTimeout = null;
const showNotice = (message, error = false, loading = false) => {
  const notice = $("#notice");
  if (!notice) return;
  if (noticeTimeout) { clearTimeout(noticeTimeout); noticeTimeout = null; }
  notice.textContent = message;
  notice.className = `notice${error ? " error" : loading ? " loading" : ""}`;
  if (!loading) noticeTimeout = setTimeout(() => notice.classList.add("hidden"), 5200);
};
const empty = (target, message) => { target.className = `${target.className.split(" ").filter((name) => name !== "empty-state").join(" ")} empty-state`; target.textContent = message; };

// Skeleton placeholders for the brief window between navigating into a
// project (or the Projects picker) and its data actually arriving --
// previously that window showed nothing (Overview: a blank #stats, plus
// whatever #artifact-cards happened to still say from the last project
// you were looking at) or a plain "Loading..." string (Projects picker).
// Deliberately NOT wired into refresh() itself: refresh() also runs
// after in-place updates (approve, retry, provider switch, upload...)
// where flashing a full skeleton over already-live data on every click
// would be worse than the brief staleness it replaces -- only the two
// "just switched into a project/the picker" call sites seed these, once,
// right before their fetch.
const skeletonLine = (height, width, marginTop = 0) => `<span class="skeleton-block" style="height:${height}px;width:${width};${marginTop ? `margin-top:${marginTop}px;` : ""}"></span>`;
const skeletonCards = (shapeClass, count, lines) => Array.from({ length: count }, () => `<div class="${shapeClass} skeleton-card">${lines.map((line) => skeletonLine(...line)).join("")}</div>`).join("");
function renderProjectCardsSkeleton() {
  const grid = $("#project-cards");
  grid.className = "project-card-grid";
  grid.innerHTML = skeletonCards("project-card", 3, [[16, "65%"], [11, "40%"]]);
}
function renderOverviewSkeleton() {
  $("#stats").innerHTML = skeletonCards("stat-card", 5, [[12, "55%"], [24, "45%", 9]]);
  const target = $("#artifact-cards");
  target.className = "artifact-grid";
  target.innerHTML = skeletonCards("artifact-card", 6, [[11, "35%"], [18, "70%", 12], [11, "55%", 8], [20, "75px", 14]]);
}

// Styled replacements for window.confirm / window.prompt so the app never
// falls back to the browser's native chrome. Both return a Promise:
// appConfirm -> boolean, appPrompt -> string|null (null on cancel).
function _openModal({ title, body, showInput, inputValue = "", inputPlaceholder = "", confirmLabel = "Confirm", danger = false }) {
  return new Promise((resolve) => {
    const modal = $("#app-modal");
    $("#app-modal-title").textContent = title || "";
    $("#app-modal-body").textContent = body || "";
    $("#app-modal-body").classList.toggle("hidden", !body);
    const input = $("#app-modal-input");
    input.classList.toggle("hidden", !showInput);
    input.value = inputValue;
    input.placeholder = inputPlaceholder;
    const confirmBtn = $("#app-modal-confirm");
    const cancelBtn = $("#app-modal-cancel");
    confirmBtn.textContent = confirmLabel;
    confirmBtn.classList.toggle("danger", danger);

    // Resolve deterministically from OUR OWN click handlers -- do not
    // depend on the dialog's native `close` event to drive resolution.
    // Verified live: `<dialog>.close()`, even called directly outside any
    // event handler, updates `.open`/`.returnValue` correctly but the
    // `close` EVENT itself never fires in this environment -- so a
    // Promise that only resolves from a `close` listener hangs forever,
    // silently dropping every confirm/prompt action (add/edit/remove,
    // new project, regenerate confirmations, all of it). `modal.close()`
    // is still called for its visual effect, best-effort; a `close`
    // listener stays as a fallback for Escape-key dismissal in case that
    // path fires correctly in a given browser.
    let settled = false;
    const finish = (action) => {
      if (settled) return;
      settled = true;
      confirmBtn.removeEventListener("click", onConfirmClick);
      cancelBtn.removeEventListener("click", onCancelClick);
      input.removeEventListener("keydown", onKeydown);
      modal.removeEventListener("close", onNativeClose);
      if (modal.open) { try { modal.close(action); } catch (_) { /* already closing */ } }
      resolve({ action, value: input.value });
    };
    const onConfirmClick = (event) => { event.preventDefault(); finish("confirm"); };
    const onCancelClick = (event) => { event.preventDefault(); finish("cancel"); };
    const onKeydown = (event) => { if (event.key === "Enter") { event.preventDefault(); finish("confirm"); } };
    const onNativeClose = () => finish(modal.returnValue || "cancel");

    confirmBtn.addEventListener("click", onConfirmClick);
    cancelBtn.addEventListener("click", onCancelClick);
    if (showInput) input.addEventListener("keydown", onKeydown);
    modal.addEventListener("close", onNativeClose);
    modal.showModal();
    if (showInput) setTimeout(() => { input.focus(); input.select(); }, 40);
  });
}
async function appConfirm(body, { title = "Please confirm", confirmLabel = "Confirm", danger = false } = {}) {
  const { action } = await _openModal({ title, body, confirmLabel, danger });
  return action === "confirm";
}
async function appPrompt(body, { title = "Enter a value", placeholder = "", value = "", confirmLabel = "Create" } = {}) {
  const { action, value: entered } = await _openModal({ title, body, showInput: true, inputPlaceholder: placeholder, inputValue: value, confirmLabel });
  return action === "confirm" ? entered.trim() : null;
}

async function ensureProject() {
  try { return await api("/status"); } catch (error) { if (!String(error.message).includes("not initialized")) throw error; return api("/initialize", { method: "POST" }); }
}
function setWorkflowStatus(status) { const item = $("#workflow-status"); item.textContent = titleCase(status || "not started"); item.className = `status-pill ${status || "muted"}`; }
// The run button previously always said "Generate / resume" no matter what
// state the workflow was actually in -- true for "nothing started yet" and
// "paused, waiting for you to continue after an approval," but misleading
// once the design was finished (nothing left to run) or a step had failed
// (this click is really a retry there, not a fresh "generate"). Label and
// enabled-state now reflect state.status.workflow_status; called from
// refresh(), from pollWorkflowUntilDone on every tick, and after a click
// finishes (instead of blindly restoring the pre-click label, which would
// otherwise clobber whatever this just set).
function updateRunButton() {
  const button = $("#run-button");
  if (!button || button.dataset.busy === "true") return; // a click is actively running -- its own spinner owns the label
  const status = state.status?.workflow_status;
  const map = {
    running: { label: "⏳ Running…", disabled: true },
    completed: { label: "✓ Completed", disabled: true },
    failed: { label: "↻ Retry", disabled: false },
    paused: { label: "▶ Resume", disabled: false },
  };
  const { label, disabled } = map[status] || { label: "▶ Generate", disabled: false };
  button.innerHTML = label;
  button.disabled = disabled;
}
function renderStats() {
  // Kept lean and relevant: overall status + the counts that tell you whether
  // anything needs you. "Agent mode" was dropped -- the provider is already
  // shown and controlled by the selector in the project header.
  const workflow = state.status?.workflow_status || "not_started";
  const reviewCount = state.artifacts.filter((artifact) => artifact.status === "awaiting_review").length;
  const approved = state.artifacts.filter((artifact) => artifact.status === "approved").length;
  $("#stats").innerHTML = [["Workflow", titleCase(workflow)], ["Artifacts", state.artifacts.length], ["Awaiting review", reviewCount], ["Approved", approved]].map(([label, value]) => `<div class="stat-card"><span>${label}</span><strong>${value}</strong></div>`).join("");
}
// The Design Trace renders the pipeline as a live DAG of nodes: each stage's
// output artifacts are node cards, chained left-to-right by SVG connectors,
// coloured by live state (pending -> generating -> generated/needs-review ->
// approved), with human-approval gates shown between stages. It updates in
// real time while a run is in flight (see pollWorkflowUntilDone) and each node
// opens its artifact's details on click.
//
// This descriptor MIRRORS src/design_pipeline/runtime.py's DEFAULT_WORKFLOW
// (step ids + their outputs, in dependency order). If that workflow changes,
// update this to match.
const TRACE_PIPELINE = [
  { kind: "step", step: "inspect-project", nodes: [{ id: "project-inspection", label: "Project inspection", icon: "🔍" }] },
  { kind: "step", step: "requirements", nodes: [{ id: "brd", label: "Requirements (BRD)", icon: "📄" }] },
  { kind: "step", step: "requirements-model", nodes: [
      { id: "business-model", label: "Business model", icon: "🏢" },
      { id: "solution-model", label: "Solution model", icon: "🧩" },
      { id: "system-model", label: "System model", icon: "🗺️" },
  ] },
  { kind: "gate", step: "requirements-approval", label: "Approve" },
  { kind: "step", step: "data-modeling", nodes: [{ id: "data-model", label: "Data model", icon: "🗄️" }] },
  { kind: "gate", step: "data-model-approval", label: "Approve" },
  { kind: "step", step: "architecture", nodes: [
      { id: "architecture-model", label: "Architecture", icon: "🏛️" },
      { id: "diagram-recommendations", label: "Diagram plan", icon: "📐" },
      { id: "diagrams", label: "Diagrams", icon: "📊" },
  ] },
  { kind: "gate", step: "architecture-approval", label: "Approve" },
  { kind: "step", step: "mockups", nodes: [
      { id: "mockup-spec", label: "Mockup spec", icon: "📱" },
      { id: "mockup-pages", label: "Mockup pages", icon: "🖼️" },
  ] },
];

function traceNodeState(nodeId, stepState) {
  const art = state.artifacts.find((a) => a.logical_id === nodeId);
  if (art) {
    if (art.status === "awaiting_review") return { cls: "review", meta: "Needs review" };
    if (art.status === "changes_requested") return { cls: "review", meta: "Changes requested" };
    if (art.status === "failed") return { cls: "failed", meta: "Failed" };
    if (art.status === "approved") return { cls: "done", meta: `v${art.version} · Approved` };
    return { cls: "done", meta: `v${art.version} · Generated` };
  }
  if (stepState === "running") return { cls: "running", meta: "Generating…" };
  if (stepState === "failed") return { cls: "failed", meta: "Failed" };
  return { cls: "pending", meta: "Pending" };
}

function traceGateState(stepId) {
  const s = state.status?.steps?.[stepId];
  if (s === "completed") return { cls: "done", icon: "✓", meta: "Approved" };
  if (s === "awaiting_review" || s === "paused") return { cls: "review", icon: "⏳", meta: "Awaiting approval" };
  if (s === "running") return { cls: "running", icon: "⏳", meta: "In progress" };
  return { cls: "pending", icon: "🔒", meta: "Locked" };
}

function renderDesignTrace() {
  const target = $("#artifact-cards");
  const started = state.status && state.status.workflow_status && state.status.workflow_status !== "not_started";
  if (!state.artifacts.length && !started) {
    return empty(target, "No artifacts yet. Upload a document and click Generate to watch the design trace build in real time.");
  }
  const steps = state.status?.steps || {};
  const cols = TRACE_PIPELINE.map((col) => {
    if (col.kind === "gate") {
      const g = traceGateState(col.step);
      return `<div class="trace-col trace-col-gate"><div class="trace-gate ${g.cls}" title="${escapeHtml(g.meta)}"><span class="trace-gate-icon">${g.icon}</span><span class="trace-gate-label">${escapeHtml(col.label)}</span></div></div>`;
    }
    const stepState = steps[col.step];
    const nodes = col.nodes.map((node) => {
      const st = traceNodeState(node.id, stepState);
      const clickable = st.cls !== "pending";
      return `<button class="trace-node ${st.cls}"${clickable ? ` data-artifact="${escapeHtml(node.id)}"` : " disabled"}>
        <span class="trace-node-icon">${node.icon}</span>
        <span class="trace-node-body"><span class="trace-node-title">${escapeHtml(node.label)}</span><span class="trace-node-meta">${escapeHtml(st.meta)}</span></span>
      </button>`;
    }).join("");
    return `<div class="trace-col">${nodes}</div>`;
  }).join("");
  target.className = "trace-graph";
  target.innerHTML = `<svg class="trace-edges" aria-hidden="true" xmlns="http://www.w3.org/2000/svg"></svg><div class="trace-cols">${cols}</div>`;
  target.querySelectorAll("[data-artifact]").forEach((button) => button.addEventListener("click", () => openArtifact(button.dataset.artifact)));
  scheduleTraceEdges(target);
}

// The connectors need real layout dimensions, which aren't ready the instant
// after innerHTML. Retry (via setTimeout, NOT requestAnimationFrame -- rAF is
// paused while the tab/preview pane is backgrounded, which would leave the
// graph with no edges until focus returned) until the columns have width.
function scheduleTraceEdges(target, tries = 0) {
  setTimeout(() => {
    const wrap = target.querySelector(".trace-cols");
    if (wrap && wrap.offsetWidth > 0) drawTraceEdges(target);
    else if (tries < 20) scheduleTraceEdges(target, tries + 1);
  }, tries === 0 ? 0 : 60);
}

// Draw the connector curves after layout, using offset coordinates (robust to
// horizontal scrolling, unlike getBoundingClientRect). Edges run from each
// stage's right edge to every node in the next stage, so a stage that produces
// several artifacts at once fans out visibly.
function drawTraceEdges(container) {
  if (!container || !container.classList.contains("trace-graph")) return;
  const svg = container.querySelector(".trace-edges");
  const wrap = container.querySelector(".trace-cols");
  const cols = [...container.querySelectorAll(".trace-col")];
  if (!svg || !wrap || cols.length < 2) { if (svg) svg.innerHTML = ""; return; }
  const w = wrap.offsetWidth, h = wrap.offsetHeight;
  if (!w || !h) { svg.innerHTML = ""; return; } // view hidden -- redrawn when shown
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.style.width = `${w}px`; svg.style.height = `${h}px`;
  const paths = [];
  for (let i = 0; i < cols.length - 1; i++) {
    const parent = cols[i], child = cols[i + 1];
    const px = parent.offsetLeft + parent.offsetWidth;
    const py = parent.offsetTop + parent.offsetHeight / 2;
    [...child.querySelectorAll(".trace-node, .trace-gate")].forEach((node) => {
      const nx = child.offsetLeft;
      const ny = node.offsetTop + node.offsetHeight / 2;
      const mid = px + (nx - px) / 2;
      const cls = node.classList;
      const reached = cls.contains("done") || cls.contains("review") || cls.contains("failed");
      const flowing = cls.contains("running");
      paths.push(`<path d="M ${px} ${py} C ${mid} ${py}, ${mid} ${ny}, ${nx} ${ny}" class="trace-edge${reached ? " on" : ""}${flowing ? " flow" : ""}" fill="none" />`);
    });
  }
  svg.innerHTML = paths.join("");
}

function redrawTraceEdges() {
  const target = document.getElementById("artifact-cards");
  if (target && target.classList.contains("trace-graph")) scheduleTraceEdges(target);
}
window.addEventListener("resize", redrawTraceEdges);
function list(items) { return items?.length ? `<ul class="list">${items.map((item) => `<li>${item}</li>`).join("")}</ul>` : "<p class=\"muted-copy\">No items recorded.</p>"; }
// Renders one system-model list field (requirements, system_capabilities,
// services, screens, business_workflows, ...) as a column of items that
// are each click-to-edit and removable, with an "+ Add" input at the
// bottom -- backed by add/edit/remove_system_model_item on the runtime.
function editableColumnHtml(field, values) {
  const items = (values || []).map((value, index) => `
    <div class="map-node" data-item-index="${index}">
      <span class="map-node-text" data-item-view>${escapeHtml(value)}</span>
      <input class="map-node-input hidden" data-item-edit value="${escapeHtml(value)}" />
      <button class="map-node-remove" data-item-remove title="Remove">×</button>
    </div>`).join("") || `<p class="muted-copy" style="margin:4px 0;">Nothing here yet.</p>`;
  return `${items}<div class="map-node-add"><input data-item-add-input placeholder="+ Add..." /></div>`;
}
function wireEditableColumns(target) {
  target.querySelectorAll("[data-model-field]").forEach((column) => {
    const field = column.dataset.modelField;

    const startEdit = (node) => {
      node.querySelector("[data-item-view]").classList.add("hidden");
      const input = node.querySelector("[data-item-edit]");
      input.classList.remove("hidden");
      input.focus();
      input.select();
    };
    const cancelEdit = (node) => {
      node.querySelector("[data-item-view]").classList.remove("hidden");
      node.querySelector("[data-item-edit]").classList.add("hidden");
    };
    const saveEdit = async (node) => {
      const index = Number(node.dataset.itemIndex);
      const input = node.querySelector("[data-item-edit]");
      const value = input.value.trim();
      const original = node.querySelector("[data-item-view]").textContent;
      if (!value || value === original) return cancelEdit(node);
      try {
        await api(`/system-model/fields/${field}/${index}`, { method: "PUT", body: JSON.stringify({ text: value }) });
        await renderSystemModel();
      } catch (error) { showNotice(error.message, true); cancelEdit(node); }
    };

    column.querySelectorAll("[data-item-view]").forEach((span) => span.addEventListener("click", () => startEdit(span.closest("[data-item-index]"))));
    column.querySelectorAll("[data-item-edit]").forEach((input) => {
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") { event.preventDefault(); saveEdit(input.closest("[data-item-index]")); }
        if (event.key === "Escape") cancelEdit(input.closest("[data-item-index]"));
      });
      input.addEventListener("blur", () => saveEdit(input.closest("[data-item-index]")));
    });
    column.querySelectorAll("[data-item-remove]").forEach((button) => button.addEventListener("click", async () => {
      const node = button.closest("[data-item-index]");
      const index = Number(node.dataset.itemIndex);
      const value = node.querySelector("[data-item-view]").textContent;
      if (!(await appConfirm(`Remove "${value}"?`, { title: "Remove item", confirmLabel: "Remove", danger: true }))) return;
      try {
        await api(`/system-model/fields/${field}/${index}`, { method: "DELETE" });
        await renderSystemModel();
      } catch (error) { showNotice(error.message, true); }
    }));
    const addInput = column.querySelector("[data-item-add-input]");
    addInput?.addEventListener("keydown", async (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      const value = addInput.value.trim();
      if (!value) return;
      try {
        await api(`/system-model/fields/${field}`, { method: "POST", body: JSON.stringify({ text: value }) });
        await renderSystemModel();
      } catch (error) { showNotice(error.message, true); }
    });
  });
}
async function renderSystemModel() {
  const target = $("#system-model"); const artifact = state.artifacts.find((item) => item.logical_id === "system-model");
  if (!artifact) return empty(target, "Generate the system model to see the traceability map.");
  const model = (await api(`/artifacts/system-model`)).content;
  target.className = "model-layout";
  const columns = [["Requirements", "requirements", model.requirements], ["Capabilities", "system_capabilities", model.system_capabilities], ["Services", "services", model.services], ["Screens", "screens", model.screens]];
  target.innerHTML = `<div class="panel"><h3>Traceability map</h3><p class="muted-copy" style="margin:-4px 0 14px;">Click any item to edit it, × to remove it, or use the + row to add one.</p><div class="model-map">${columns.map(([name, field, values]) => `<div class="map-column" data-model-field="${field}"><h4>${name}</h4>${editableColumnHtml(field, values)}</div>`).join("")}</div></div><div class="model-grid"><div class="panel"><h3>Business workflows</h3><div class="map-column editable-standalone" data-model-field="business_workflows">${editableColumnHtml("business_workflows", model.business_workflows)}</div></div><div class="panel"><h3>Permissions</h3>${Object.entries(model.permissions || {}).map(([role, rights]) => `<p><strong>${role}</strong><br><span class="muted-copy">${rights.join(", ")}</span></p>`).join("") || "<p class=\"muted-copy\">No permissions recorded.</p>"}</div></div>`;
  wireEditableColumns(target);
}
async function renderDataModel() {
  const target = $("#data-model");
  const artifact = state.artifacts.find((item) => item.logical_id === "data-model");
  if (!artifact) return empty(target, "Approve the system model to generate the data model.");
  const model = (await api(`/artifacts/data-model`)).content;
  const entities = model.entities || [];
  const relationships = model.relationships || [];
  target.className = "model-layout";

  const entityCards = entities.map((entity, entityIndex) => `
    <div class="entity-card" data-entity-index="${entityIndex}">
      <div class="entity-card-header">
        <input class="entity-name-input" value="${escapeHtml(entity.name || "")}" data-entity-name placeholder="entity_name" />
        <button class="text-button" data-entity-remove style="color:#be123c;">Remove entity</button>
      </div>
      <input class="entity-desc-input" value="${escapeHtml(entity.description || "")}" data-entity-description placeholder="What this entity represents..." />
      <div class="entity-fields">
        ${(entity.fields || []).map((field, fieldIndex) => `
          <div class="field-row" data-field-index="${fieldIndex}">
            <input value="${escapeHtml(field.name || "")}" data-field-name placeholder="field name" />
            <input value="${escapeHtml(field.type || "")}" data-field-type placeholder="type" />
            <input value="${escapeHtml(field.description || "")}" data-field-description placeholder="description" />
            <button data-field-remove title="Remove field">×</button>
          </div>`).join("") || `<p class="muted-copy" style="margin:4px 0;">No fields yet.</p>`}
        <div class="field-row field-row-add">
          <input data-field-add-name placeholder="+ field name" />
          <input data-field-add-type placeholder="type" />
          <input data-field-add-description placeholder="description" />
          <button data-field-add title="Add field">+</button>
        </div>
      </div>
    </div>`).join("") || `<p class="muted-copy">No entities yet -- add one below, or attach a supporting document and regenerate.</p>`;

  const relationshipRows = relationships.map((relationship, index) => `
    <div class="relationship-row" data-relationship-index="${index}">
      <input value="${escapeHtml(relationship.from_entity || "")}" data-rel-from placeholder="from_entity" />
      <select data-rel-cardinality>
        ${["one-to-one", "one-to-many", "many-to-many"].map((c) => `<option value="${c}" ${relationship.cardinality === c ? "selected" : ""}>${c}</option>`).join("")}
      </select>
      <input value="${escapeHtml(relationship.to_entity || "")}" data-rel-to placeholder="to_entity" />
      <input value="${escapeHtml(relationship.label || "")}" data-rel-label placeholder="label (e.g. contains)" />
      <button data-rel-remove title="Remove relationship">×</button>
    </div>`).join("") || `<p class="muted-copy" style="margin:4px 0;">No relationships yet.</p>`;

  target.innerHTML = `
    <div class="panel">
      <h3>Entities</h3>
      <p class="muted-copy" style="margin:-4px 0 14px;">Edit any field inline (saves on blur). Fields are nested per entity.</p>
      <div class="entity-cards">${entityCards}</div>
      <div class="entity-card entity-card-add">
        <input data-entity-add-name placeholder="+ new entity name" />
        <input data-entity-add-description placeholder="description" />
        <button class="secondary-button" data-entity-add>Add entity</button>
      </div>
    </div>
    <div class="panel">
      <h3>Relationships</h3>
      <div class="relationship-list">${relationshipRows}</div>
      <div class="relationship-row relationship-row-add">
        <input data-rel-add-from placeholder="+ from_entity" />
        <select data-rel-add-cardinality>
          ${["one-to-one", "one-to-many", "many-to-many"].map((c) => `<option value="${c}">${c}</option>`).join("")}
        </select>
        <input data-rel-add-to placeholder="to_entity" />
        <input data-rel-add-label placeholder="label" />
        <button class="secondary-button" data-rel-add>Add relationship</button>
      </div>
    </div>
    <div class="panel" style="grid-column:1 / -1;">
      <h3>ERD (derived automatically -- edit entities/relationships above to change it)</h3>
      <pre class="mermaid" id="data-model-erd"></pre>
    </div>`;

  wireDataModel(target);

  try {
    const { mermaid_source } = await api("/data-model/erd");
    const erd = $("#data-model-erd");
    erd.textContent = mermaid_source;
    erd.setAttribute("data-src", mermaid_source); // lets renderPendingDiagrams re-render it if it first drew while hidden
    await renderPendingDiagrams($("#data-model-view"));
  } catch (error) { /* ERD panel just stays as raw text if rendering fails */ }
}
function wireDataModel(target) {
  const onError = (error) => showNotice(error.message, true);
  target.querySelectorAll("[data-entity-index]").forEach((card) => {
    const entityIndex = Number(card.dataset.entityIndex);
    const saveEntity = async () => {
      const name = card.querySelector("[data-entity-name]").value.trim();
      const description = card.querySelector("[data-entity-description]").value.trim();
      if (!name) return;
      try { await api(`/data-model/entities/${entityIndex}`, { method: "PUT", body: JSON.stringify({ name, description }) }); await renderDataModel(); } catch (error) { onError(error); }
    };
    card.querySelector("[data-entity-name]").addEventListener("blur", saveEntity);
    card.querySelector("[data-entity-description]").addEventListener("blur", saveEntity);
    card.querySelector("[data-entity-remove]").addEventListener("click", async () => {
      if (!(await appConfirm(`Remove entity "${card.querySelector("[data-entity-name]").value}" and all its fields?`, { title: "Remove entity", confirmLabel: "Remove", danger: true }))) return;
      try { await api(`/data-model/entities/${entityIndex}`, { method: "DELETE" }); await renderDataModel(); } catch (error) { onError(error); }
    });
    card.querySelectorAll("[data-field-index]").forEach((row) => {
      const fieldIndex = Number(row.dataset.fieldIndex);
      const saveField = async () => {
        const name = row.querySelector("[data-field-name]").value.trim();
        const type = row.querySelector("[data-field-type]").value.trim();
        const description = row.querySelector("[data-field-description]").value.trim();
        if (!name || !type) return;
        try { await api(`/data-model/entities/${entityIndex}/fields/${fieldIndex}`, { method: "PUT", body: JSON.stringify({ name, type, description }) }); await renderDataModel(); } catch (error) { onError(error); }
      };
      row.querySelectorAll("input").forEach((input) => input.addEventListener("blur", saveField));
      row.querySelector("[data-field-remove]").addEventListener("click", async () => {
        try { await api(`/data-model/entities/${entityIndex}/fields/${fieldIndex}`, { method: "DELETE" }); await renderDataModel(); } catch (error) { onError(error); }
      });
    });
    card.querySelector("[data-field-add]")?.addEventListener("click", async () => {
      const name = card.querySelector("[data-field-add-name]").value.trim();
      const type = card.querySelector("[data-field-add-type]").value.trim();
      const description = card.querySelector("[data-field-add-description]").value.trim();
      if (!name || !type) return showNotice("Field name and type are required.", true);
      try { await api(`/data-model/entities/${entityIndex}/fields`, { method: "POST", body: JSON.stringify({ name, type, description }) }); await renderDataModel(); } catch (error) { onError(error); }
    });
  });
  target.querySelector("[data-entity-add]")?.addEventListener("click", async () => {
    const name = target.querySelector("[data-entity-add-name]").value.trim();
    const description = target.querySelector("[data-entity-add-description]").value.trim();
    if (!name) return showNotice("Entity name is required.", true);
    try { await api("/data-model/entities", { method: "POST", body: JSON.stringify({ name, description }) }); await renderDataModel(); } catch (error) { onError(error); }
  });
  target.querySelectorAll("[data-relationship-index]").forEach((row) => {
    const index = Number(row.dataset.relationshipIndex);
    const saveRelationship = async () => {
      const from_entity = row.querySelector("[data-rel-from]").value.trim();
      const to_entity = row.querySelector("[data-rel-to]").value.trim();
      const cardinality = row.querySelector("[data-rel-cardinality]").value;
      const label = row.querySelector("[data-rel-label]").value.trim();
      if (!from_entity || !to_entity) return;
      try { await api(`/data-model/relationships/${index}`, { method: "PUT", body: JSON.stringify({ from_entity, to_entity, cardinality, label }) }); await renderDataModel(); } catch (error) { onError(error); }
    };
    row.querySelectorAll("input").forEach((input) => input.addEventListener("blur", saveRelationship));
    row.querySelector("[data-rel-cardinality]").addEventListener("change", saveRelationship);
    row.querySelector("[data-rel-remove]").addEventListener("click", async () => {
      try { await api(`/data-model/relationships/${index}`, { method: "DELETE" }); await renderDataModel(); } catch (error) { onError(error); }
    });
  });
  target.querySelector("[data-rel-add]")?.addEventListener("click", async () => {
    const from_entity = target.querySelector("[data-rel-add-from]").value.trim();
    const to_entity = target.querySelector("[data-rel-add-to]").value.trim();
    const cardinality = target.querySelector("[data-rel-add-cardinality]").value;
    const label = target.querySelector("[data-rel-add-label]").value.trim();
    if (!from_entity || !to_entity) return showNotice("Both from_entity and to_entity are required.", true);
    try { await api("/data-model/relationships", { method: "POST", body: JSON.stringify({ from_entity, to_entity, cardinality, label }) }); await renderDataModel(); } catch (error) { onError(error); }
  });
}
async function renderArchitecture() {
  const target = $("#architecture-model"); const artifact = state.artifacts.find((item) => item.logical_id === "architecture-model");
  if (!artifact) return empty(target, "Approve the system model to generate architecture recommendations.");
  const architecture = (await api(`/artifacts/architecture-model`)).content; const recommendations = state.artifacts.find((item) => item.logical_id === "diagram-recommendations");
  let recommended = null; if (recommendations) recommended = (await api(`/artifacts/diagram-recommendations`)).content;
  const diagramsArtifact = state.artifacts.find((item) => item.logical_id === "diagrams");
  let diagrams = []; if (diagramsArtifact) diagrams = (await api(`/artifacts/diagrams`)).content || [];
  target.className = "model-layout";
  // Each panel only renders when the model actually produced content for it --
  // a live generation sometimes leaves style/rationale/components/boundaries/
  // recommended empty, and a bare "Architecture" box with nothing under it
  // (or a "Boundaries" card that only ever says "No items recorded.") is
  // noise, not information.
  const hasOverview = architecture.style || architecture.rationale || architecture.components?.length;
  const overviewPanel = hasOverview ? `<div class="panel">${architecture.style ? `<h3>${escapeHtml(architecture.style)}</h3>` : ""}${architecture.rationale ? `<p class="muted-copy">${escapeHtml(architecture.rationale)}</p>` : ""}${architecture.components?.length ? `<div class="component-flow">${architecture.components.map((component, index) => `<div class="component">${escapeHtml(component)}</div>${index < architecture.components.length - 1 ? "<span class=\"arrow\">→</span>" : ""}`).join("")}</div>` : ""}</div>` : "";
  const boundariesPanel = architecture.boundaries?.length ? `<div class="panel"><h3>Boundaries</h3>${list(architecture.boundaries)}</div>` : "";
  const recommendedPanel = recommended?.recommended?.length ? `<div class="panel"><h3>Recommended diagrams</h3>${list(recommended.recommended)}</div>` : "";
  // Only one of the two? Let it span the full row instead of sitting narrow
  // in a two-column grid built for a pair.
  const gridPanel = (boundariesPanel && recommendedPanel) ? `<div class="model-grid">${boundariesPanel}${recommendedPanel}</div>` : (boundariesPanel || recommendedPanel);
  target.innerHTML = `${overviewPanel}${gridPanel}${renderDiagrams(diagrams)}` || `<p class="empty-state">Architecture approved, but no overview, boundaries, or diagrams were generated for it.</p>`;
  await renderPendingDiagrams($("#architecture-view"));
  target.querySelectorAll("[data-comment-diagram]").forEach((button) => button.addEventListener("click", () => {
    const name = button.dataset.commentDiagram;
    addLocatedComment("diagrams", { kind: "diagram", diagram_name: name }, `Comment on the "${name}" diagram:`);
  }));
}
// Mermaid measures layout via getBBox(), which returns 0 inside a display:none
// view -- so a diagram rendered while its tab is hidden becomes a zero-width,
// broken SVG that mermaid then marks data-processed and refuses to re-render.
// refresh() builds every view's diagrams at once, so all but the active tab
// were born broken (only a full reload, landing directly on that tab, fixed
// them). This renders only diagrams whose view is actually on screen, and
// recovers any that first drew while hidden by resetting them from data-src.
// All calls are serialized so two mermaid.run()s never overlap.
let _diagramChain = Promise.resolve();
function renderPendingDiagrams(scope) {
  _diagramChain = _diagramChain.then(() => _renderPendingDiagramsNow(scope)).catch(() => {});
  return _diagramChain;
}
async function _renderPendingDiagramsNow(scope) {
  if (!window.mermaid) return;
  const root = scope || document.querySelector(".view.active");
  if (!root) return;
  const pending = [...root.querySelectorAll(".mermaid")].filter((el) => {
    if (el.offsetParent === null) return false; // view still hidden -- defer to when it's shown
    const svg = el.querySelector("svg");
    const broken = !svg || svg.getBoundingClientRect().width < 1;
    return !el.getAttribute("data-processed") || broken;
  });
  if (!pending.length) return;
  for (const el of pending) {
    // A broken/processed node won't be re-run by mermaid unless we reset it
    // back to its source text and clear the processed flag.
    const src = el.getAttribute("data-src");
    if (src != null && el.getAttribute("data-processed")) {
      el.removeAttribute("data-processed");
      el.textContent = src;
    }
  }
  try { await window.mermaid.run({ nodes: pending }); } catch (error) { /* raw source stays visible */ }
}
function renderDiagrams(diagrams) {
  if (!diagrams.length) return "";
  const blocks = diagrams.map((diagram, index) => {
    // The mermaid.render tool's own result always uses `mermaid_source`, but
    // a model restating a diagram in its final JSON answer sometimes reuses
    // the tool *argument* name (`mermaid_code`) instead -- accept either.
    const source = diagram.mermaid_source || diagram.mermaid_code || diagram.code;
    const detail = diagram.detail || diagram.description;
    const rawName = diagram.name || `Diagram ${index + 1}`;
    const name = escapeHtml(rawName);
    const chartLink = diagram.chart_url ? `<p class="muted-copy"><a href="${escapeHtml(diagram.chart_url)}" target="_blank" rel="noopener">Open in Mermaid Chart</a></p>` : "";
    const commentButton = `<button class="text-button" data-comment-diagram="${escapeHtml(rawName)}">💬 Comment</button>`;
    if (diagram.valid === false || !source) {
      return `<div class="diagram-block"><h4>${name} ${commentButton}</h4><p class="muted-copy">Not rendered: ${escapeHtml(detail || "invalid Mermaid syntax")}</p></div>`;
    }
    return `<div class="diagram-block"><h4>${name} ${commentButton}</h4><pre class="mermaid" id="diagram-${index}" data-src="${escapeHtml(source)}">${escapeHtml(source)}</pre>${chartLink}</div>`;
  }).join("");
  return `<div class="panel"><h3>Diagrams</h3>${blocks}</div>`;
}
// Trusted bridge injected into every mockup iframe: any element the model
// annotates with data-goto="<screen_id>" becomes a real cross-screen jump,
// posted up to the review workspace as a message. Model-authored JS still
// runs (sandboxed, no same-origin, no top-level navigation) for local
// interactivity within one screen, but only these annotated hooks can
// switch screens -- so the model can't hijack navigation or reach out.
// Bridge script injected into every mockup iframe. Handles cross-screen
// nav (data-goto -> postMessage to parent), element-scoped commenting
// (comment-mode: intercept click, capture a stable selector, postMessage
// back), and defence against model-generated code that would navigate the
// iframe away (unrecognised href, form submit).
//
// Wrapped in try/catch on the listener path because a thrown exception
// inside `selectorFor` (unusual DOM cases -- className that isn't a
// string, orphaned nodes, etc.) would otherwise silently swallow the
// whole event with no visible symptom in the parent, which was the
// behaviour the user reported as "clicking does nothing".
const MOCKUP_BRIDGE = `<script>
(function(){
var commentMode=false, lastHover=null, style;
function selectorFor(el, depth){
  depth=depth||0;
  if(!el||el===document.body||el===document.documentElement||depth>8) return 'body';
  if(el.id) return el.tagName.toLowerCase()+'#'+el.id;
  var parts=[el.tagName.toLowerCase()];
  try{
    var raw=(el.className&&el.className.baseVal!=null?el.className.baseVal:el.className)||'';
    var cls=String(raw).trim().split(/\\s+/).filter(Boolean).slice(0,3);
    if(cls.length) parts.push('.'+cls.join('.'));
  }catch(_){}
  var parent=el.parentElement;
  if(parent){
    try{
      var siblings=Array.prototype.filter.call(parent.children,function(c){return c.tagName===el.tagName;});
      if(siblings.length>1) parts.push(':nth-of-type('+(siblings.indexOf(el)+1)+')');
    }catch(_){}
    return selectorFor(parent, depth+1)+' > '+parts.join('');
  }
  return parts.join('');
}
function textSnippet(el){try{var t=(el.textContent||'').trim().replace(/\\s+/g,' ');return t.length>60?t.slice(0,57)+'...':t;}catch(_){return '';}}
function rectOf(el){try{var r=el.getBoundingClientRect();return {left:r.left,top:r.top,width:r.width,height:r.height};}catch(_){return null;}}
function queryOne(selector){
  // selectorFor builds a "tag.class > tag.class:nth-of-type(n)" chain that
  // isn't always valid CSS (classes can contain characters querySelector
  // chokes on) -- best-effort only, used just to reposition pins.
  try{ return document.querySelector(selector); }catch(_){ return null; }
}
window.addEventListener('message',function(e){
  try{
    if(e.data&&e.data.type==='mockup-comment-mode'){
      commentMode=!!e.data.enabled;
      if(commentMode&&!style){style=document.createElement('style');style.textContent='[data-mockup-hover]{outline:3px solid #017E84 !important;cursor:crosshair !important;}';document.head.appendChild(style);}
      document.body.style.cursor=commentMode?'crosshair':'';
      if(!commentMode&&lastHover){try{lastHover.removeAttribute('data-mockup-hover');}catch(_){}lastHover=null;}
    } else if(e.data&&e.data.type==='mockup-locate'){
      var rects={};
      (e.data.selectors||[]).forEach(function(sel){ var el=queryOne(sel); if(el) rects[sel]=rectOf(el); });
      parent.postMessage({type:'mockup-locate-result',rects:rects},'*');
    }
  }catch(err){parent.postMessage({type:'mockup-bridge-error',phase:'message',error:String(err)},'*');}
});
document.addEventListener('mouseover',function(e){
  if(!commentMode) return;
  try{
    if(lastHover) lastHover.removeAttribute('data-mockup-hover');
    lastHover=e.target; lastHover.setAttribute('data-mockup-hover','');
  }catch(_){}
},true);
document.addEventListener('click',function(e){
  try{
    if(commentMode){
      e.preventDefault(); e.stopPropagation();
      parent.postMessage({type:'mockup-comment-target',selector:selectorFor(e.target),text_snippet:textSnippet(e.target),rect:rectOf(e.target)},'*');
      return;
    }
    var t=e.target.closest&&e.target.closest('[data-goto]');
    if(t){e.preventDefault();parent.postMessage({type:'mockup-goto',screen_id:t.getAttribute('data-goto')},'*');return;}
    var a=e.target.closest&&e.target.closest('a[href]');
    if(a){var h=a.getAttribute('href');if(!h||h==='#'||h.charAt(0)==='#')e.preventDefault();else if(!/^(https?:|mailto:|tel:)/.test(h))e.preventDefault();}
  }catch(err){parent.postMessage({type:'mockup-bridge-error',phase:'click',error:String(err)},'*');}
},true);
document.addEventListener('submit',function(e){e.preventDefault();},true);
parent.postMessage({type:'mockup-bridge-ready'},'*');
})();
</script>`;
const pageHtml = (page) => {
  if (!page?.html) return null;
  // Strip model-authored <script> blocks that either navigate the iframe
  // away (location.href = 'x.html') or rebind data-goto elements to their
  // own handler -- both defeat the bridge and were observed live. Any
  // other model JS (tab switching within the page, expand/collapse, etc.)
  // is preserved. This is deliberately regex-narrow, not a full sanitiser:
  // we only kill the one specific pattern that keeps breaking navigation.
  const html = page.html.replace(/<script\b[^>]*>([\s\S]*?)<\/script>/gi, (match, body) => {
    if (/\blocation\s*\.\s*(?:href|assign|replace)\b/.test(body)) return "";
    if (/querySelectorAll\s*\(\s*['"]\[data-goto\]/.test(body)) return "";
    return match;
  });
  return html.includes("</body>") ? html.replace("</body>", MOCKUP_BRIDGE + "</body>") : html + MOCKUP_BRIDGE;
};
async function addLocatedComment(artifactId, location, targetLabel) {
  // Reuse the real artifact-preview dialog (styled textarea, dialog
  // shell, escape-to-close) instead of native window.prompt(). The
  // dialog's comment-form submit handler reads state.pendingCommentTarget
  // to know which artifact_id + location to attach the comment to.
  state.pendingCommentTarget = { artifact_id: artifactId, location, label: targetLabel };
  try {
    const artifact = await api(`/artifacts/${artifactId}`);
    state.selected = artifact;
  } catch (error) {
    return showNotice(error.message, true);
  }
  $("#dialog-title").textContent = "Add comment";
  $("#dialog-type").textContent = titleCase(artifactId);
  $("#dialog-meta").innerHTML = `<span>💬 ${escapeHtml(targetLabel)}</span>`;
  $("#dialog-content").textContent = "";
  $("#version-list").innerHTML = "";
  $("#comment-text").value = "";
  // This is a scoped, single comment -- not a full-artifact review, so the
  // three review actions don't apply here: Approve/Request changes act on
  // the whole mockup-pages artifact's status, and "Retry generation" hits
  // the FULL /artifacts/mockup-pages/retry (regenerates every screen, the
  // exact whole-set drift risk the per-screen "🎯 Regenerate this screen"
  // button exists to avoid) -- not the scoped per-screen retry. Showing
  // them here previously invited exactly that mix-up. The (empty) content
  // preview is hidden too, rather than left showing as an empty dark bar.
  $("#dialog-content").classList.add("hidden");
  $("#dialog-actions").classList.add("hidden");
  $("#version-section").classList.add("hidden");
  $("#artifact-dialog").showModal();
  setTimeout(() => $("#comment-text").focus(), 50);
}
function setCommentMode(iframe, enabled) {
  iframe?.contentWindow?.postMessage({ type: "mockup-comment-mode", enabled }, "*");
  state.mockupCommentMode = enabled;
  document.querySelector("#mockup-comment-toggle")?.classList.toggle("active", enabled);
}

// ---- Full-screen mockup viewer -------------------------------------------
// Takes over the whole viewport with just the mockup's own iframe -- no
// sidebar, toolbar, or app chrome around it -- so it reads like the real
// application rather than a preview embedded in a review tool. Reuses the
// same srcdoc + sandbox the inline preview uses; in-mockup navigation
// (data-goto) still posts "mockup-goto" up to the shared listener below,
// which re-renders this overlay's iframe in place so click-through keeps
// working without leaving full-screen.
function currentMockupPage() {
  const screens = state.currentMockupScreens || [];
  const pages = state.currentMockupPages || [];
  const active = screens[state.currentMockup];
  const activeId = active ? screenId(active) : null;
  const activeName = active ? screenName(active) : "";
  const page = pages.find((item) => item.screen_id === activeId || item.screen === activeName);
  return { activeId, activeName, html: pageHtml(page) };
}

function renderFullscreenMockup() {
  const overlay = document.querySelector("#mockup-fullscreen");
  if (!overlay) return;
  const { activeId, activeName, html } = currentMockupPage();
  overlay.querySelector(".fullscreen-mock-title").textContent = activeName;
  overlay.querySelector(".fullscreen-mock-iframe").setAttribute("srcdoc", html);
  overlay.querySelector(".fullscreen-mock-iframe").dataset.screenId = activeId || "";
}

function openFullscreenMockup() {
  if (document.querySelector("#mockup-fullscreen")) { renderFullscreenMockup(); return; }
  const { activeName, html } = currentMockupPage();
  const overlay = document.createElement("div");
  overlay.id = "mockup-fullscreen";
  overlay.innerHTML = `<div class="fullscreen-mock-bar"><span class="fullscreen-mock-title">${escapeHtml(activeName)}</span><button class="secondary-button" data-close-fullscreen>✕ Exit full screen</button></div><iframe class="fullscreen-mock-iframe" title="${escapeHtml(activeName)} full screen" sandbox="allow-scripts" srcdoc="${escapeHtml(html)}"></iframe>`;
  document.body.appendChild(overlay);
  fullscreenMockupOpen = true;
  overlay.querySelector("[data-close-fullscreen]").addEventListener("click", closeFullscreenMockup);
  document.addEventListener("keydown", closeFullscreenOnEscape);
}

function closeFullscreenOnEscape(event) { if (event.key === "Escape") closeFullscreenMockup(); }

function closeFullscreenMockup() {
  document.querySelector("#mockup-fullscreen")?.remove();
  document.removeEventListener("keydown", closeFullscreenOnEscape);
  fullscreenMockupOpen = false;
  renderMockups(); // pick up any in-overlay navigation once back in the review chrome
}
window.addEventListener("message", (event) => {
  if (event.data?.type === "mockup-bridge-error") {
    showNotice(`Mockup bridge error (${event.data.phase}): ${event.data.error}`, true);
    return;
  }
  if (event.data?.type === "mockup-goto") {
    const spec = state.currentMockupScreens || [];
    const index = spec.findIndex((screen) => screenId(screen) === event.data.screen_id);
    if (index >= 0) {
      state.currentMockup = index;
      if (fullscreenMockupOpen) renderFullscreenMockup();
      else renderMockups();
    }
  } else if (event.data?.type === "mockup-comment-target") {
    const screens = state.currentMockupScreens || [];
    const active = screens[state.currentMockup];
    const activeId = active ? screenId(active) : null;
    const { selector, text_snippet: textSnippet, rect } = event.data;
    // Pin the popover to the clicked element's own on-screen position
    // (reported by the bridge) instead of opening the big preview dialog --
    // stays anchored right where the user clicked, Figma-style.
    const anchor = document.createElement("span");
    anchor.style.position = "absolute";
    anchor.style.left = `${(rect?.left ?? 0) + (rect?.width ?? 0) / 2}px`;
    anchor.style.top = `${rect?.top ?? 0}px`;
    showPinPopover(anchor, null, async (text) => {
      try {
        await api("/artifacts/mockup-pages/comments", { method: "POST", body: JSON.stringify({ text, location: { kind: "element", screen_id: activeId, selector, text_snippet: textSnippet } }) });
        showNotice(`Comment pinned to "${active ? screenName(active) : activeId}" -- click "🎯 Regenerate this screen" there to apply it.`);
        loadPinsForScreen(activeId);
        renderScreenComments(activeId);
      } catch (error) { showNotice(error.message, true); }
    });
    setCommentMode(document.querySelector(".mock-frame-iframe"), false);
  }
});
async function renderMockups() {
  const target = $("#mockup-model"); const artifact = state.artifacts.find((item) => item.logical_id === "mockup-spec");
  if (!artifact) return empty(target, "Approve the architecture to generate the mockup specification.");
  const mockup = (await api(`/artifacts/mockup-spec`)).content; const screens = mockup.screens || [];
  state.currentMockupScreens = screens;
  const pagesArtifact = state.artifacts.find((item) => item.logical_id === "mockup-pages");
  let pages = []; if (pagesArtifact) pages = (await api(`/artifacts/mockup-pages`)).content || [];
  state.currentMockupPages = pages;
  state.currentMockup = Math.min(state.currentMockup, Math.max(screens.length - 1, 0));
  const active = screens[state.currentMockup]; const activeName = active ? screenName(active) : "Screen preview"; const activeId = active ? screenId(active) : null;
  const page = pages.find((item) => item.screen_id === activeId || item.screen === activeName);
  target.className = "mockup-layout";
  const html = pageHtml(page);
  // sandbox="allow-scripts" (deliberately NOT allow-same-origin): model-authored
  // JS runs for local interactivity, but stays fully isolated from this app.
  // Premium two-tier "control deck": a title row (screen identity + passive
  // review actions) over a distinct AI-edits band that elevates the four
  // generative, AI-powered actions. Every data-* hook and the
  // #mockup-comment-toggle id are preserved exactly so the handlers below
  // and setCommentMode() keep working unchanged.
  const toolbar = html
    ? `<div class="mock-deck">
        <div class="mock-deck-head">
          <div class="mock-deck-title">
            <span class="mock-deck-name">${escapeHtml(activeName)}</span>
            <span class="mock-deck-count">${state.currentMockup + 1} / ${screens.length}</span>
            ${active?.purpose ? `<span class="mock-deck-purpose">${escapeHtml(active.purpose)}</span>` : ""}
          </div>
          <div class="mock-deck-review">
            <button class="deck-ghost" data-fullscreen-screen title="Open this screen filling the whole page, like the real application">⛶ Full screen</button>
            <button class="deck-ghost" data-comment-screen title="Leave a review comment on this whole screen">💬 Comment</button>
            <button class="deck-ghost" id="mockup-comment-toggle" data-comment-element title="Click any element in the mockup to pin a comment right to it">📍 Pin element</button>
          </div>
        </div>
        <div class="mock-ai-band">
          <span class="ai-band-label">✦ AI edits</span>
          <div class="ai-actions">
            <button class="ai-action" data-retry-screen title="Regenerate only this screen -- every other screen stays exactly as-is">🎯 <span>Regenerate</span></button>
            <button class="ai-action" data-add-linked-screen title="Add a brand-new screen this one navigates to (e.g. a Create button that should open its own screen, not a modal) -- every other screen stays exactly as-is">➕ <span>Add screen</span></button>
            <button class="ai-action" data-split-screen title="Move part of this screen (e.g. a hardcoded record's detail) out into its own new linked screen, leaving a genuine list/summary here -- every other screen stays exactly as-is">🔀 <span>Split</span></button>
            <button class="ai-action ai-action-chat" data-mockup-chat title="Describe changes across multiple screens in plain language -- the system plans the operations for your confirmation before executing">💬 <span>Chat edits</span></button>
          </div>
        </div>
      </div>`
    : "";
  const frame = html
    ? `<div class="mock-frame-wrap"><iframe class="mock-frame-iframe" title="${escapeHtml(activeName)} mockup" sandbox="allow-scripts" srcdoc="${escapeHtml(html)}"></iframe><div class="mock-pin-layer"></div></div>`
    : `<div class="mock-frame"><header><div><p class="eyebrow">INTERACTIVE MOCKUP</p><h3>${escapeHtml(activeName)}</h3>${active?.purpose ? `<p class="muted-copy">${escapeHtml(active.purpose)}</p>` : ""}</div><span class="status-pill">Synthetic data</span></header><div class="mock-content"><div class="mock-block"><strong>Workflow status</strong><p class="muted-copy">Awaiting review</p></div><div class="mock-block"><strong>Linked artifacts</strong><p class="muted-copy">System model · Architecture</p></div><div class="mock-block"><strong>Actions</strong><p class="muted-copy">Approve · Request changes</p></div></div></div>`;
  const railLabel = html ? `<p class="screen-rail-label">Screens <span>${screens.length}</span></p>` : "";
  // #mock-comments starts hidden: renderScreenComments() below reveals it only
  // when this screen actually has open comments (and leaves it hidden on the
  // empty or fetch-error paths), so there's never a bare yellow strip.
  target.innerHTML = `<div class="screen-list">${railLabel}${buildScreenList(screens, state.currentMockup, pages)}</div><div class="mock-column">${toolbar}<div id="mock-comments" class="mock-comments hidden"></div>${frame}</div>`;
  target.querySelectorAll("[data-screen]").forEach((button) => button.addEventListener("click", () => { state.currentMockup = Number(button.dataset.screen); renderMockups(); }));
  target.querySelector("[data-fullscreen-screen]")?.addEventListener("click", openFullscreenMockup);
  target.querySelector("[data-comment-screen]")?.addEventListener("click", () => {
    addLocatedComment("mockup-pages", { kind: "screen", screen_id: activeId }, `Comment on the "${activeName}" screen:`);
  });
  if (html) renderScreenComments(activeId);
  target.querySelector("[data-comment-element]")?.addEventListener("click", (event) => {
    const iframe = document.querySelector(".mock-frame-iframe");
    setCommentMode(iframe, !state.mockupCommentMode);
  });
  target.querySelector("[data-retry-screen]")?.addEventListener("click", async (event) => {
    // Capture the button BEFORE the await -- event.currentTarget is reset
    // to null once synchronous event dispatch finishes, which happens
    // before this async handler resumes past the first await. Reading it
    // afterward threw a silent, uncaught TypeError here (nothing ever
    // reached the try block, no spinner, no notice, no retry request).
    const button = event.currentTarget;
    const originalHtml = button.innerHTML;
    button.disabled = true;
    button.innerHTML = `<span class="btn-spinner"></span> Regenerating...`;
    showNotice(`Regenerating "${activeName}"...`, false, true);
    try {
      await api(`/mockup-pages/screens/${encodeURIComponent(activeId)}/retry`, { method: "POST", body: JSON.stringify({}) });
      showNotice(`"${activeName}" regenerated.`);
      await renderMockups();
    } catch (error) { showNotice(error.message, true); button.innerHTML = originalHtml; button.disabled = false; }
  });
  target.querySelector("[data-add-linked-screen]")?.addEventListener("click", async (event) => {
    const button = event.currentTarget; // see note above on retry-screen's identical bug
    const description = await appPrompt(
      `Describe the new screen "${activeName}" should navigate to (what it's for, how it's reached -- e.g. "Create Audit Plan form, opened from the Create button").`,
      { title: "Add linked screen", placeholder: "e.g. Create Audit Plan form", confirmLabel: "Add screen" },
    );
    if (!description) return;
    button.disabled = true;
    const originalHtml = button.innerHTML;
    button.innerHTML = `<span class="btn-spinner"></span> Adding screen...`;
    showNotice(`Adding a new screen linked from "${activeName}"...`, false, true);
    try {
      await api(`/mockup-pages/screens/add`, { method: "POST", body: JSON.stringify({ description, link_from_screen_id: activeId }) });
      showNotice(`New screen added.`);
      await renderMockups();
    } catch (error) { showNotice(error.message, true); button.innerHTML = originalHtml; button.disabled = false; }
  });
  target.querySelector("[data-split-screen]")?.addEventListener("click", async (event) => {
    const button = event.currentTarget; // see note above on retry-screen's identical bug
    const description = await appPrompt(
      `What should move out of "${activeName}" into its own new screen? (e.g. "the projects table -- this screen should become a genuine list of plan years instead").`,
      { title: "Split this screen", placeholder: "e.g. the projects table for one plan year", confirmLabel: "Split screen" },
    );
    if (!description) return;
    if (!(await appConfirm(`"${activeName}" will be rewritten to remove that part and link out to a new screen instead. Every other screen stays exactly as-is.`, { title: "Split this screen", confirmLabel: "Split" }))) return;
    button.disabled = true;
    const originalHtml = button.innerHTML;
    button.innerHTML = `<span class="btn-spinner"></span> Splitting screen...`;
    showNotice(`Splitting "${activeName}"...`, false, true);
    try {
      await api(`/mockup-pages/screens/${encodeURIComponent(activeId)}/split`, { method: "POST", body: JSON.stringify({ extract_description: description }) });
      showNotice(`"${activeName}" split into two screens.`);
      await renderMockups();
    } catch (error) { showNotice(error.message, true); button.innerHTML = originalHtml; button.disabled = false; }
  });
  target.querySelector("[data-mockup-chat]")?.addEventListener("click", openMockupChat);
  if (html) {
    const iframe = target.querySelector(".mock-frame-iframe");
    iframe.addEventListener("load", () => loadPinsForScreen(activeId));
  }
}

// ---- Mockup Chat: plan-first multi-screen changes via free-text -----------
// The user describes changes across multiple screens; the system plans the
// needed operations (retry_screen, add_screen, split_screen), shows the plan
// for confirmation, then executes step by step in the background.

let _chatSessionId = null;

function _chatShowPhase(phase) {
  for (const id of ["chat-input-phase", "chat-plan-phase", "chat-exec-phase"]) {
    const el = document.getElementById(id);
    if (el) el.classList.toggle("hidden", id !== phase);
  }
}

const _OP_LABELS = { retry_screen: "🎯 Retry", add_screen: "➕ Add", split_screen: "🔀 Split" };

function openMockupChat() {
  _chatSessionId = null;
  _chatShowPhase("chat-input-phase");
  const textarea = document.getElementById("chat-instruction");
  textarea.value = "";
  const dialog = document.getElementById("mockup-chat-dialog");
  const planBtn = document.getElementById("chat-plan-btn");
  const execBtn = document.getElementById("chat-plan-execute");

  // Wire up the "Plan changes" button.
  const onPlan = async () => {
    const instruction = textarea.value.trim();
    if (!instruction) return;
    planBtn.disabled = true;
    planBtn.innerHTML = `<span class="btn-spinner"></span> Planning...`;
    try {
      const session = await api("/mockup-chat", { method: "POST", body: JSON.stringify({ instruction }) });
      _chatSessionId = session.session_id;
      // Render the plan in phase 2.
      document.getElementById("chat-plan-summary").textContent = session.plan.summary || "";
      const ol = document.getElementById("chat-plan-steps");
      ol.innerHTML = session.plan.steps.map((step, i) => {
        const badge = _OP_LABELS[step.operation] || step.operation;
        return `<li><strong>${badge}</strong> ${escapeHtml(step.description)}</li>`;
      }).join("");
      _chatShowPhase("chat-plan-phase");
    } catch (error) {
      showNotice(error.message, true);
    } finally {
      planBtn.disabled = false;
      planBtn.innerHTML = "Plan changes";
    }
  };
  planBtn.onclick = onPlan;

  // Wire up the "Execute plan" button.
  const onExecute = async () => {
    if (!_chatSessionId) return;
    execBtn.disabled = true;
    execBtn.innerHTML = `<span class="btn-spinner"></span> Starting...`;
    try {
      await api(`/mockup-chat/${encodeURIComponent(_chatSessionId)}/execute`, { method: "POST" });
      // Switch to execution phase and start polling.
      _chatShowPhase("chat-exec-phase");
      document.getElementById("chat-exec-close").classList.add("hidden");
      await _pollMockupChat(_chatSessionId);
    } catch (error) {
      showNotice(error.message, true);
      execBtn.disabled = false;
      execBtn.innerHTML = "Execute plan";
    }
  };
  execBtn.onclick = onExecute;

  dialog.showModal();
}

async function _pollMockupChat(sessionId) {
  const ol = document.getElementById("chat-exec-steps");
  const closeBtn = document.getElementById("chat-exec-close");
  while (true) {
    await new Promise((r) => setTimeout(r, 3000));
    let session;
    try { session = await api(`/mockup-chat/${encodeURIComponent(sessionId)}`); }
    catch (_) { break; }
    // Render step statuses.
    ol.innerHTML = session.steps.map((step) => {
      const badge = _OP_LABELS[step.operation] || step.operation;
      const icon = step.status === "completed" ? "✅"
        : step.status === "running" ? `<span class="btn-spinner" style="display:inline-block;width:14px;height:14px"></span>`
        : step.status === "failed" ? "❌"
        : "⏳";
      const err = step.error ? ` <span style="color:var(--danger,#dc2626)">${escapeHtml(step.error)}</span>` : "";
      const newId = step.new_screen_id ? ` → <em>${escapeHtml(step.new_screen_id)}</em>` : "";
      return `<li>${icon} <strong>${badge}</strong> ${escapeHtml(step.description)}${newId}${err}</li>`;
    }).join("");
    if (session.status !== "executing") {
      closeBtn.classList.remove("hidden");
      const ok = session.status === "completed";
      showNotice(ok ? "All mockup changes applied." : "Mockup chat execution failed -- see details.", !ok);
      await refresh();
      await renderMockups();
      break;
    }
  }
}

// ---- Element pins: Figma-style, stay anchored to the clicked element -----
// Pins render as small numbered badges positioned over the iframe at the
// element's on-screen rect (reported by the bridge via mockup-locate).
// Clicking a pin opens a compact inline popover right there -- no full
// dialog -- for both reading and adding element comments.
// Whole-screen comments (location.kind === "screen") had nowhere to show
// up in the UI before this -- only element-scoped comments got a pin.
// This lists EVERY comment for the active screen (both kinds) so leaving
// one is actually visible, not just silently saved.
async function renderScreenComments(screenId) {
  const container = document.getElementById("mock-comments");
  if (!container) return;
  let comments = [];
  try { comments = await api("/artifacts/mockup-pages/comments"); } catch (error) { return; }
  // Resolved comments (already applied by a successful retry) don't show
  // up here anymore -- they'd otherwise keep piling up indefinitely.
  const forScreen = comments.filter((c) => c.status === "open" && c.location?.screen_id === screenId);
  if (!forScreen.length) { container.innerHTML = ""; container.classList.add("hidden"); return; }
  container.classList.remove("hidden");
  container.innerHTML = `<p class="mock-comments-label">💬 ${forScreen.length} comment${forScreen.length === 1 ? "" : "s"} on this screen</p>` + forScreen.map((c) => {
    const scope = c.location?.kind === "element" ? `on <code>${escapeHtml(c.location.selector || "")}</code>` : "on the whole screen";
    return `<div class="mock-comment-row"><span class="mock-comment-scope">${scope}</span><span class="mock-comment-text">${escapeHtml(c.text)}</span></div>`;
  }).join("");
}
async function loadPinsForScreen(screenId) {
  const layer = document.querySelector(".mock-pin-layer");
  if (!layer) return;
  layer.innerHTML = "";
  let comments = [];
  try { comments = await api("/artifacts/mockup-pages/comments"); } catch (error) { return; }
  const elementComments = comments.filter((c) => c.status === "open" && c.location?.kind === "element" && c.location?.screen_id === screenId);
  if (!elementComments.length) return;
  const iframe = document.querySelector(".mock-frame-iframe");
  const selectors = [...new Set(elementComments.map((c) => c.location.selector))];
  const rects = await locateSelectors(iframe, selectors);
  elementComments.forEach((comment, index) => {
    const rect = rects[comment.location.selector];
    if (!rect) return;
    const pin = document.createElement("button");
    pin.className = "mock-pin";
    pin.style.left = `${rect.left + rect.width / 2}px`;
    pin.style.top = `${rect.top}px`;
    pin.textContent = String(index + 1);
    pin.title = comment.text;
    pin.addEventListener("click", (event) => { event.stopPropagation(); showPinPopover(pin, comment.text, null); });
    layer.appendChild(pin);
  });
}
function locateSelectors(iframe, selectors) {
  return new Promise((resolve) => {
    if (!iframe?.contentWindow || !selectors.length) return resolve({});
    const onMessage = (event) => {
      if (event.data?.type !== "mockup-locate-result") return;
      window.removeEventListener("message", onMessage);
      resolve(event.data.rects || {});
    };
    window.addEventListener("message", onMessage);
    iframe.contentWindow.postMessage({ type: "mockup-locate", selectors }, "*");
    setTimeout(() => { window.removeEventListener("message", onMessage); resolve({}); }, 800);
  });
}
function closePinPopover() { document.querySelector(".mock-pin-popover")?.remove(); }
function showPinPopover(anchorEl, existingText, onSave) {
  closePinPopover();
  const layer = document.querySelector(".mock-pin-layer");
  if (!layer) return;
  const popover = document.createElement("div");
  popover.className = "mock-pin-popover";
  popover.style.left = `${anchorEl.style.left}`;
  popover.style.top = `${parseFloat(anchorEl.style.top) + 22}px`;
  const readOnly = !onSave;
  popover.innerHTML = readOnly
    ? `<p class="pin-popover-text">${escapeHtml(existingText)}</p><button class="text-button" data-pin-close>Close</button>`
    : `<textarea placeholder="What should change here?"></textarea><div class="pin-popover-actions"><button class="text-button" data-pin-cancel>Cancel</button><button class="primary-button" data-pin-save>Save</button></div>`;
  layer.appendChild(popover);
  popover.querySelector("[data-pin-close], [data-pin-cancel]")?.addEventListener("click", closePinPopover);
  const textarea = popover.querySelector("textarea");
  if (textarea) setTimeout(() => textarea.focus(), 30);
  popover.querySelector("[data-pin-save]")?.addEventListener("click", () => {
    const text = textarea.value.trim();
    if (!text) return;
    onSave(text);
    closePinPopover();
  });
}
// Kept in sync with DocumentReader._SUPPORTED (documents.py) -- the file
// picker's own filter, not a substitute for the server-side check that
// actually enforces this (a user can still pick "All files" and try).
const REFERENCE_FILE_ACCEPT = ".docx,.md,.markdown,.txt,.rst,.pdf,.xlsx,.xlsm,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel.sheet.macroEnabled.12,text/plain,text/markdown";
// "system" is the underlying reference bucket the requirements-agent reads
// as extra source documents (see requirements.yaml's own prompt: "SUPPORTING
// REQUIREMENTS DOCUMENTS (system-references)") -- it's about requirements
// generation, not the System model page specifically, so it's labeled
// accordingly everywhere it's shown (Overview included).
const REFERENCE_STAGE_LABELS = { system: "requirements" };
async function renderReferencesStrip(stage) {
  // A stage's strip can now appear in more than one place (e.g. "system" on
  // both Overview and the System model page, so uploading extra requirements
  // documents is possible from either) -- render into every matching
  // container instead of just the first one querySelector would find.
  const strips = [...document.querySelectorAll(`[data-references-strip="${stage}"]`)];
  if (!strips.length) return;
  let entries = [];
  try { entries = await api(`/references/${stage}`); } catch (error) { /* stage may be empty */ }
  const label = REFERENCE_STAGE_LABELS[stage] || stage;
  // Filename is a toggle button: click expands an inline textarea with the
  // doc's current (plain-text, already-extracted) content for a quick edit
  // in place -- no delete-and-reupload for a one-line wording fix.
  const rows = (entries || []).map((entry) => `
    <div class="attachment-row" data-attachment-filename="${escapeHtml(entry.filename)}">
      <div class="attachment-row-header">
        <button class="attachment-name" data-reference-toggle>📎 ${escapeHtml(entry.filename)} <span class="muted-copy">(${escapeHtml(entry.media_type || "text")})</span></button>
        <button class="text-button" data-reference-remove="${escapeHtml(entry.filename)}" style="color:#be123c;">Remove</button>
      </div>
      <div class="attachment-edit hidden">
        <textarea data-reference-edit-text>${escapeHtml(entry.content || "")}</textarea>
        <div class="attachment-edit-actions">
          <button class="text-button" data-reference-edit-cancel>Cancel</button>
          <button class="primary-button" data-reference-edit-save>Save</button>
        </div>
      </div>
    </div>`).join("") || `<p class="muted-copy" style="margin:0;">No supporting documents yet. Add Word, Excel, PDF, Markdown, plain-text, or RST files here to give the ${label} agent more context.</p>`;
  strips.forEach((strip) => wireReferencesStrip(strip, stage, label, rows));
}
function wireReferencesStrip(strip, stage, label, rows) {
  strip.innerHTML = `<h4>Supporting documents (${label})</h4>${rows}<div class="attachment-uploader"><input type="file" data-reference-input accept="${REFERENCE_FILE_ACCEPT}" /><button class="secondary-button" data-reference-upload>Attach</button></div>`;
  strip.querySelector("[data-reference-upload]")?.addEventListener("click", async () => {
    const input = strip.querySelector("[data-reference-input]");
    const button = strip.querySelector("[data-reference-upload]");
    const file = input.files[0];
    if (!file) return showNotice("Choose a file first.", true);
    const originalHtml = button.innerHTML;
    button.disabled = true;
    button.innerHTML = `<span class="btn-spinner"></span> Attaching...`;
    showNotice(`Attaching ${file.name} to ${label}...`, false, true);
    try {
      const payload = await documentPayload(file);
      await api(`/references/${stage}`, { method: "POST", body: JSON.stringify(payload) });
      showNotice(`${file.name} attached to ${label}.`);
      await renderReferencesStrip(stage);
    } catch (error) { showNotice(error.message, true); }
    finally {
      button.innerHTML = originalHtml;
      button.disabled = false;
    }
  });
  strip.querySelectorAll("[data-reference-remove]").forEach((button) => button.addEventListener("click", async () => {
    if (!(await appConfirm(`Remove "${button.dataset.referenceRemove}" from ${label}'s supporting documents?`, { title: "Remove attachment", confirmLabel: "Remove", danger: true }))) return;
    try {
      await api(`/references/${stage}/${encodeURIComponent(button.dataset.referenceRemove)}`, { method: "DELETE" });
      await renderReferencesStrip(stage);
    } catch (error) { showNotice(error.message, true); }
  }));
  strip.querySelectorAll("[data-reference-toggle]").forEach((button) => button.addEventListener("click", () => {
    button.closest(".attachment-row").querySelector(".attachment-edit").classList.toggle("hidden");
  }));
  strip.querySelectorAll("[data-reference-edit-cancel]").forEach((button) => button.addEventListener("click", () => {
    button.closest(".attachment-edit").classList.add("hidden");
  }));
  strip.querySelectorAll("[data-reference-edit-save]").forEach((button) => button.addEventListener("click", async () => {
    const row = button.closest(".attachment-row");
    const filename = row.dataset.attachmentFilename;
    const text = row.querySelector("[data-reference-edit-text]").value;
    if (!text.trim()) return showNotice("Content can't be empty.", true);
    button.disabled = true;
    try {
      await api(`/references/${stage}/${encodeURIComponent(filename)}`, { method: "PUT", body: JSON.stringify({ text }) });
      showNotice(`${filename} updated.`);
      await renderReferencesStrip(stage);
    } catch (error) { showNotice(error.message, true); button.disabled = false; }
  }));
}
// The most recently uploaded document's name, shown permanently (unlike
// renderBrdStatus's banner below, which hides again once that upload is
// reflected in a generated `brd` artifact). Without this, once the
// banner's transient window passed there was no way at all -- not here,
// not in the History tab either (whose entries only ever showed
// artifact_id/step_id, never details.filename) -- to see which original
// file a project's requirements actually came from, on reload or after a
// fresh deployment.
function renderBrdSource(history) {
  const el = $("#brd-source");
  const lastIngest = history.filter((event) => event.event_type === "BRD_INGESTED").at(-1);
  if (!lastIngest) { el.classList.add("hidden"); el.textContent = ""; return; }
  el.classList.remove("hidden");
  el.textContent = `📄 Source document: ${lastIngest.details?.filename || "Document"} (uploaded ${new Date(lastIngest.timestamp).toLocaleString()})`;
}
// Uploading a BRD only stages it (see runtime.ingest_brd*) -- it doesn't
// create an artifact, so there was previously nothing on Overview to show
// for it after a reload: the upload toast is gone, the file input resets
// to "No file selected", and the artifact list still says "No artifacts
// yet", making an actually-successful upload look like it vanished. The
// BRD_INGESTED event it does append is persisted (survives reload), so
// surface that here instead of leaving it undiscoverable.
//
// "Any artifacts exist" is NOT the same as "this upload is already
// reflected" -- a project can generate its first pass from one BRD, then
// have a *newer* BRD uploaded on top of it later (revising requirements
// mid-project, or a follow-up doc). That newer upload is staged and real,
// but the naive "hide once artifacts.length > 0" check hid the banner for
// it anyway -- observed live: uploading a fresh AuditModule.docx onto an
// already-generated project showed nothing at all, no different from the
// upload silently failing. Compare timestamps instead: only hide once the
// current `brd` artifact was generated at or after this exact upload.
function renderBrdStatus(history) {
  const el = $("#brd-status");
  const lastIngest = history.filter((event) => event.event_type === "BRD_INGESTED").at(-1);
  if (!lastIngest) { el.classList.add("hidden"); el.textContent = ""; return; }
  const brdArtifact = state.artifacts.find((item) => item.logical_id === "brd");
  const generatedAt = brdArtifact?.generated_by?.generated_at;
  const alreadyReflected = generatedAt && new Date(generatedAt) >= new Date(lastIngest.timestamp);
  if (alreadyReflected) { el.classList.add("hidden"); el.textContent = ""; return; }
  el.classList.remove("hidden");
  // The run button's own label is now dynamic (see updateRunButton) --
  // "Generate", "Resume", or "Retry" depending on where the workflow is --
  // so this doesn't quote one fixed label that would be wrong half the time.
  el.textContent = `📄 ${lastIngest.details?.filename || "Document"} uploaded ${new Date(lastIngest.timestamp).toLocaleString()} -- click the button above to build your project from it.`;
}
async function refresh() {
  // The heading previously showed state.status.project_id verbatim -- the
  // internal URL-safe slug ("audit-module"), not the display name typed
  // into "+ New project" ("Audit Module"). The friendly name lives on the
  // matching entry in state.projects (populated by refreshProjects, which
  // always runs before this), not in /status at all.
  try {
    // /status already embeds the full artifact list (its own `.status`
    // property calls the exact same `store.artifacts.list_latest()`
    // `/artifacts` does) -- fetching /artifacts separately was a second,
    // fully redundant round trip (network + DB query, both non-trivial
    // with Render and Neon in different regions) for byte-identical data
    // on every single page load. Also run /history alongside /status
    // instead of after it -- neither depends on the other's result, so
    // there is no reason to pay their round trips one after another.
    const [status, history] = await Promise.all([api("/status"), api("/history")]);
    state.status = status;
    state.artifacts = status.artifacts;
    $("#project-header-name").textContent = state.projects.find((project) => project.id === state.status.project_id)?.name || state.status.project_id || "Project";
    setWorkflowStatus(state.status.workflow_status);
    updateRunButton();
    renderStats();
    renderDesignTrace();
    ["system-model", "data-model", "architecture-model"].forEach(updateStageStatus);
    if (state.status.provider?.provider) $("#provider-select").value = state.status.provider.provider;
    populateModelSelect(state.status.provider?.provider, state.status.provider?.model);
    if (state.status.provider?.mode === "live" && !state.status.provider.configured) showNotice(`${titleCase(state.status.provider.provider)} is selected but needs an API key and model in .env -- no restart needed once it's saved.`, true);
    renderBrdSource(history);
    renderBrdStatus(history);
    await Promise.all([renderSystemModel(), renderDataModel(), renderArchitecture(), renderMockups(), renderReferencesStrip("system"), renderReferencesStrip("data-model"), renderReferencesStrip("architecture"), renderReferencesStrip("mockup")]);
  } catch (error) { setWorkflowStatus("not_started"); showNotice(error.message, true); }
}
async function pollWorkflowUntilDone(intervalMs = 3000) {
  // /workflow/run and /workflow/restart now kick the run off in a background
  // thread and return immediately (see api.py's start_background) instead of
  // blocking the request until every step finishes -- a full run can take
  // several minutes across multiple LLM calls, which Render's edge proxy
  // times out on, returning a 503 to the browser while the backend keeps
  // going unaware. Poll /status (already exposes live workflow_status)
  // until it's no longer "running" instead of waiting on one long request.
  while (true) {
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
    const status = await api("/status");
    state.status = status;
    // /status embeds the live artifact list too -- update it so the Design
    // Trace graph advances in real time (nodes flip to generating -> green,
    // gates unlock) on every poll tick, not just once the run finishes.
    if (status.artifacts) state.artifacts = status.artifacts;
    setWorkflowStatus(status.workflow_status);
    renderStats();
    renderDesignTrace();
    if (status.workflow_status !== "running") break; // updateRunButton() runs once, after the loop -- see below
  }
  await refresh();
  const finalStatus = state.status.workflow_status;
  const message = finalStatus === "failed"
    ? "Workflow failed -- check artifact history for details."
    : finalStatus === "paused"
      ? "Workflow paused for approval."
      : "Workflow completed.";
  showNotice(message, finalStatus === "failed");
}
async function openArtifact(id) {
  try {
    const artifact = await api(`/artifacts/${id}`); state.selected = artifact;
    $("#dialog-title").textContent = titleCase(id); $("#dialog-type").textContent = titleCase(artifact.metadata.type);
    $("#dialog-meta").innerHTML = `<span>v${artifact.metadata.version}</span><span class="status-pill ${artifact.metadata.status}">${titleCase(artifact.metadata.status)}</span><span>${artifact.metadata.requirements.join(", ") || "No requirement IDs"}</span>`;
    $("#dialog-content").textContent = JSON.stringify(artifact.content, null, 2);
    const versions = await api(`/artifacts/${id}/versions`);
    $("#version-list").innerHTML = versions.map((version) => `<span class="version">v${version.version} · ${titleCase(version.status)}</span>`).join("");
    // Undo whatever addLocatedComment (the scoped mockup screen/element
    // comment flow) hid on this same dialog last time it was open --
    // this is the full-artifact-review path, where all three make sense.
    $("#dialog-content").classList.remove("hidden");
    $("#dialog-actions").classList.remove("hidden");
    $("#version-section").classList.remove("hidden");
    $("#artifact-dialog").showModal();
  } catch (error) { showNotice(error.message, true); }
}
async function action(path, body = {}, loadingNotice = null) {
  if (loadingNotice) showNotice(loadingNotice, false, true);
  $("#artifact-dialog").close();
  try {
    await api(path, { method: "POST", body: JSON.stringify(body) });
    showNotice("Action completed.");
    await refresh();
  } catch (error) { showNotice(error.message, true); }
}

// Hash routing: URL is #/projects/{project_id}/{tab}. Nav-link clicks
// update the hash; a hashchange listener applies the change. This keeps
// the active tab (and current project) across refreshes.
const VALID_TABS = new Set(["overview", "system", "data-model", "architecture", "mockups"]);
// A project-scoped URL always looks like #/projects/<id>/<tab>. Anything
// else -- the bare root, "#/projects" with no id, a stale/unknown project
// id -- lands on the Projects picker (projectId null, tab "projects")
// rather than guessing which project to open. Landing used to default to
// a hardcoded "default" project id that may not exist in a given
// deployment at all (observed live: every request 404'd, and the UI
// looked indistinguishable from a freshly-empty, uninitialized project).
// Showing the picker instead means the user always lands somewhere real.
function parseHash() {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const parts = raw.split("/").filter(Boolean);
  if (parts[0] === "projects" && parts[1]) {
    const projectId = decodeURIComponent(parts[1]);
    const tab = (parts[2] && VALID_TABS.has(parts[2])) ? parts[2] : "overview";
    return { projectId, tab };
  }
  return { projectId: null, tab: "projects" };
}
function updateHash(patch = {}) {
  const current = parseHash();
  const next = { ...current, ...patch };
  const target = next.projectId ? `#/projects/${encodeURIComponent(next.projectId)}/${next.tab}` : "#/projects";
  if (window.location.hash !== target) window.location.hash = target;
}
// Toggles which "level" is visible: the Projects picker (no project open),
// or an open project's own workspace -- its tab strip (.project-tabs) and
// name/run-controls bar (.project-header), both hidden on the picker.
function applyActiveTab(tab, projectId) {
  document.querySelectorAll(".nav-link").forEach((link) => link.classList.toggle("active", link.dataset.view === tab));
  document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.id === `${tab}-view`));
  const inProject = tab !== "projects" && !!projectId;
  document.querySelector(".project-tabs")?.classList.toggle("hidden", !inProject);
  document.querySelector(".project-header")?.classList.toggle("hidden", !inProject);
  // Inside a project the big app-level topbar is pure redundancy -- the
  // sidebar brand already carries the app identity and the project-header
  // carries the active project's -- so hide it there and give that vertical
  // space back to the actual work (mockups especially). It stays on the
  // Projects picker, where it's the page's own heading.
  document.querySelector(".topbar")?.classList.toggle("hidden", inProject);
  // Same redundancy in the sidebar: the "Projects" nav item duplicates the
  // header's "<- All projects" link once a project is open, so drop it there
  // and let the workspace tabs own the rail. It returns on the picker.
  document.querySelector('.nav-link[data-view="projects"]')?.classList.toggle("hidden", inProject);
}
document.addEventListener("click", (event) => { if (!event.target.closest(".mock-pin-popover, .mock-pin")) closePinPopover(); });
document.querySelectorAll(".nav-link").forEach((button) => button.addEventListener("click", () => {
  // The "Projects" link is the one nav-link that isn't scoped to the
  // currently-open project -- it must explicitly clear projectId (a plain
  // {tab: "projects"} patch would keep whatever project was already open,
  // producing a nonsensical #/projects/<id>/projects hash).
  if (button.dataset.view === "projects") updateHash({ projectId: null, tab: "projects" });
  else updateHash({ tab: button.dataset.view });
}));
async function onHashChange() {
  const { projectId, tab } = parseHash();
  applyActiveTab(tab, projectId);
  if (projectId !== state.projectId) {
    state.projectId = projectId;
    if (projectId) { renderOverviewSkeleton(); await refresh(); } else await renderProjectsView();
  }
  // The view just became visible -- render any of its diagrams that are still
  // unrendered or that drew broken (0-width) while the tab was hidden.
  renderPendingDiagrams();
  // The trace-graph's SVG connectors can't be measured while its view is
  // hidden (offsetWidth is 0), so redraw them now that Overview is on screen.
  if (tab === "overview") redrawTraceEdges();
}
window.addEventListener("hashchange", onHashChange);

async function refreshProjects() {
  try { state.projects = await api("/projects"); } catch (error) { state.projects = []; }
}
async function renderProjectsView() {
  renderProjectCardsSkeleton();
  await refreshProjects();
  const grid = $("#project-cards");
  // Card = a div holding two sibling buttons (never nested -- that's invalid
  // HTML): a full-bleed "open" button and a hover-revealed delete button.
  const cards = state.projects
    .map((project) => `<div class="project-card">
      <button class="project-card-open" data-project-card="${escapeHtml(project.id)}">
        <span class="project-card-name">${escapeHtml(project.name || project.id)}</span>
        <span class="project-card-id">${escapeHtml(project.id)}</span>
      </button>
      <button class="project-card-delete" data-project-delete="${escapeHtml(project.id)}" title="Delete this project" aria-label="Delete ${escapeHtml(project.name || project.id)}">✕</button>
    </div>`)
    .join("");
  grid.className = "project-card-grid";
  grid.innerHTML = cards || `<p class="empty-state">No projects yet. Click "+ New project" above to create one.</p>`;
  grid.querySelectorAll("[data-project-card]").forEach((card) => card.addEventListener("click", () => updateHash({ projectId: card.dataset.projectCard, tab: "overview" })));
  grid.querySelectorAll("[data-project-delete]").forEach((button) => button.addEventListener("click", async (event) => {
    event.stopPropagation();
    const id = button.dataset.projectDelete;
    const name = state.projects.find((project) => project.id === id)?.name || id;
    const confirmed = await appConfirm(
      `This permanently deletes "${name}" and everything in it -- every artifact, comment, approval, and version history. This cannot be undone.`,
      { title: "Delete project", confirmLabel: "Delete permanently", danger: true },
    );
    if (!confirmed) return;
    try {
      await api(`/projects/${encodeURIComponent(id)}`, { method: "DELETE" });
      showNotice(`"${name}" deleted.`);
      await renderProjectsView();
    } catch (error) { showNotice(error.message, true); }
  }));
}
async function createProjectFlow() {
  const name = await appPrompt("Name your project. Letters, digits, and dashes work best.", { title: "New project", placeholder: "e.g. Customer Portal Redesign", confirmLabel: "Create project" });
  if (!name) return;
  try {
    const project = await api("/projects", { method: "POST", body: JSON.stringify({ name: name.trim() }) });
    await refreshProjects();
    showNotice(`Project "${project.name}" created.`);
    updateHash({ projectId: project.id, tab: "overview" });
  } catch (error) { showNotice(error.message, true); }
}
$("#new-project-button")?.addEventListener("click", createProjectFlow);
$("#back-to-projects")?.addEventListener("click", () => updateHash({ projectId: null, tab: "projects" }));

// Inline rename: click the header name to swap it for a text input (same
// view/edit-swap pattern the data-model tab already uses for entity/field
// names), save on blur or Enter, discard on Escape or an empty/unchanged
// value.
function startProjectNameEdit() {
  const view = $("#project-header-name");
  const input = $("#project-header-name-input");
  input.value = view.textContent;
  view.classList.add("hidden");
  input.classList.remove("hidden");
  input.focus();
  input.select();
}
function cancelProjectNameEdit() {
  $("#project-header-name").classList.remove("hidden");
  $("#project-header-name-input").classList.add("hidden");
}
async function saveProjectNameEdit() {
  const view = $("#project-header-name");
  const input = $("#project-header-name-input");
  const value = input.value.trim();
  if (!value || value === view.textContent) return cancelProjectNameEdit();
  try {
    const updated = await api(`/projects/${encodeURIComponent(state.projectId)}`, { method: "PATCH", body: JSON.stringify({ name: value }) });
    await refreshProjects(); // keeps the Projects picker's cards in sync too
    view.textContent = updated.name;
    cancelProjectNameEdit();
  } catch (error) { showNotice(error.message, true); cancelProjectNameEdit(); }
}
$("#project-header-name")?.addEventListener("click", startProjectNameEdit);
$("#project-header-name-input")?.addEventListener("blur", saveProjectNameEdit);
$("#project-header-name-input")?.addEventListener("keydown", (event) => {
  if (event.key === "Enter") { event.preventDefault(); event.target.blur(); }
  else if (event.key === "Escape") { event.preventDefault(); cancelProjectNameEdit(); }
});

// Project deletion now lives on the project cards in the Projects picker
// (see renderProjectsView) -- the per-project header no longer carries a
// trash-can button.

$("#document-input").addEventListener("change", (event) => {
  const files = [...event.target.files];
  $("#file-name").textContent = !files.length ? "No file selected"
    : files.length === 1 ? files[0].name
    : `${files[0].name} + ${files.length - 1} more`;
});
$("#provider-select").addEventListener("change", async (event) => {
  const previous = state.status?.provider?.provider || "stub";
  try {
    const provider = await api("/provider", { method: "PUT", body: JSON.stringify({ provider: event.target.value }) });
    populateModelSelect(provider.provider, provider.model); // that provider's own remembered model, not the old one's
    showNotice(`Switched to ${titleCase(provider.provider)}${provider.model ? ` (${provider.model})` : ""}.`);
    await refresh();
  } catch (error) { event.target.value = previous; showNotice(error.message, true); }
});
$("#model-select").addEventListener("change", async (event) => {
  const provider = $("#provider-select").value;
  const previousModel = state.status?.provider?.model || "";
  try {
    const result = await api("/model", { method: "PUT", body: JSON.stringify({ provider, model: event.target.value }) });
    showNotice(`Now using ${result.model}.`);
    await refresh();
  } catch (error) { populateModelSelect(provider, previousModel); showNotice(error.message, true); }
});

// Per-tab status pill + Approve button: lets the user see an artifact's
// current review status and approve it right from its own stage tab,
// without needing to open the Overview artifact-card dialog.
function updateStageStatus(artifactId) {
  const pill = document.getElementById(`${artifactId}-status`);
  const approveButton = document.querySelector(`[data-stage-approve="${artifactId}"]`);
  if (!pill) return;
  const artifact = state.artifacts.find((item) => item.logical_id === artifactId);
  if (!artifact) { pill.textContent = ""; pill.className = "status-pill muted hidden"; if (approveButton) approveButton.classList.add("hidden"); return; }
  pill.textContent = titleCase(artifact.status);
  pill.className = `status-pill ${artifact.status}`;
  // approve() works from any not-yet-approved status (generated,
  // awaiting_review, changes_requested) -- it doesn't require the
  // workflow to have actually reached this artifact's approval gate
  // yet. Only hide the button once it's already approved.
  if (approveButton) approveButton.classList.toggle("hidden", artifact.status === "approved");
}
document.querySelectorAll("[data-stage-approve]").forEach((button) => button.addEventListener("click", async () => {
  const artifactId = button.dataset.stageApprove;
  const label = button.dataset.stageLabel || artifactId;
  try {
    await api(`/artifacts/${artifactId}/approve`, { method: "POST", body: JSON.stringify({}) });
    showNotice(`Approved the ${label}.`);
    await refresh();
  } catch (error) { showNotice(error.message, true); }
}));

// Per-tab regenerate buttons: retry the artifact for that stage; sibling-
// sync in the runtime handles co-generated outputs (e.g. clicking
// "Regenerate architecture" also regenerates diagrams).
document.querySelectorAll("[data-stage-retry]").forEach((button) => button.addEventListener("click", async () => {
  const artifactId = button.dataset.stageRetry;
  const label = button.dataset.stageLabel || artifactId;
  if (!(await appConfirm(`This creates a new version of the ${label}. The current version is kept in history and can still be viewed.`, { title: `Regenerate ${label}`, confirmLabel: "Regenerate" }))) return;
  const originalHtml = button.innerHTML;
  button.disabled = true;
  button.innerHTML = `<span class="btn-spinner"></span> Regenerating ${escapeHtml(label)}...`;
  const provider = titleCase(state.status?.provider?.provider || "AI");
  showNotice(`Regenerating ${label} with ${provider}... Please wait, this may take a moment.`, false, true);
  setWorkflowStatus("running");

  let inlineBanner = null;
  if (artifactId === "mockup-pages") {
    const mockColumn = document.querySelector(".mock-column");
    if (mockColumn) {
      inlineBanner = document.createElement("div");
      inlineBanner.className = "mock-loading-banner";
      inlineBanner.innerHTML = `<span class="stage-spinner"></span> Regenerating mockup screens with ${provider}... Please wait.`;
      mockColumn.prepend(inlineBanner);
    }
  } else if (artifactId === "architecture-model") {
    const archModel = document.querySelector("#architecture-model");
    if (archModel && !archModel.classList.contains("empty-state")) {
      inlineBanner = document.createElement("div");
      inlineBanner.className = "mock-loading-banner";
      inlineBanner.innerHTML = `<span class="stage-spinner"></span> Regenerating architecture & diagrams with ${provider}... Please wait.`;
      archModel.prepend(inlineBanner);
    }
  } else if (artifactId === "system-model") {
    const sysModel = document.querySelector("#system-model");
    if (sysModel && !sysModel.classList.contains("empty-state")) {
      inlineBanner = document.createElement("div");
      inlineBanner.className = "mock-loading-banner";
      inlineBanner.innerHTML = `<span class="stage-spinner"></span> Regenerating system model with ${provider}... Please wait.`;
      sysModel.prepend(inlineBanner);
    }
  }

  try {
    await api(`/artifacts/${artifactId}/retry`, { method: "POST", body: JSON.stringify({}) });
    showNotice(`Regenerated ${label}.`);
    await refresh();
  } catch (error) {
    if (inlineBanner) inlineBanner.remove();
    showNotice(error.message, true);
  } finally {
    button.innerHTML = originalHtml;
    button.disabled = false;
  }
}));
async function documentPayload(file) {
  const name = file.name.toLowerCase();
  const isBinaryFormat = [".docx", ".pdf", ".xlsx", ".xlsm"].some((ext) => name.endsWith(ext));
  if (isBinaryFormat) {
    // Binary formats must go through content_base64, not .text() below --
    // reading a zip-based binary file (docx/xlsx/xlsm are all zips; pdf
    // isn't but is equally not UTF-8 text) as text mangles its bytes
    // before it even leaves the browser.
    if (file.size > 10 * 1024 * 1024) {
      const typeLabel = name.endsWith(".docx") ? "Word documents" : name.endsWith(".pdf") ? "PDF documents" : "Excel documents";
      throw new Error(`${typeLabel} must be 10 MB or smaller.`);
    }
    const dataUrl = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error("Could not read the uploaded document."));
      reader.readAsDataURL(file);
    });
    return { filename: file.name, content_base64: String(dataUrl).split(",", 2)[1] };
  }
  return { filename: file.name, text: await file.text() };
}
$("#upload-button").addEventListener("click", async () => {
  const files = [...$("#document-input").files];
  if (!files.length) return showNotice("Choose a Word, PDF, Markdown, text, or RST file first.", true);
  const button = $("#upload-button");
  const originalHtml = button.innerHTML;
  button.disabled = true;
  try {
    await ensureProject();
    // The FIRST file becomes the primary BRD input, exactly as a single
    // upload always has. Any ADDITIONAL files selected at once go into the
    // same "system"-stage supporting-documents bucket the requirements
    // agent already reads as extra source material (see requirements.yaml's
    // "SUPPORTING REQUIREMENTS DOCUMENTS" instruction) -- so several
    // requirements documents can be uploaded together instead of only one.
    const [primary, ...extras] = files;
    button.innerHTML = `<span class="btn-spinner"></span> Uploading...`;
    showNotice(`Uploading and extracting text from ${primary.name}...`, false, true);
    await api("/documents/brd", { method: "POST", body: JSON.stringify(await documentPayload(primary)) });
    for (const file of extras) {
      showNotice(`Attaching ${file.name} as an additional requirements document...`, false, true);
      await api("/references/system", { method: "POST", body: JSON.stringify(await documentPayload(file)) });
    }
    showNotice(extras.length
      ? `${primary.name} uploaded as the BRD input, plus ${extras.length} additional requirements document${extras.length === 1 ? "" : "s"}.`
      : `${primary.name} uploaded as the BRD input.`);
    await refresh();
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    button.innerHTML = originalHtml;
    button.disabled = false;
  }
});
$("#run-button").addEventListener("click", async () => {
  const button = $("#run-button");
  button.dataset.busy = "true"; // tells updateRunButton() to leave our spinner alone while this owns the label
  button.disabled = true;
  button.innerHTML = `<span class="btn-spinner"></span> Running...`;
  showNotice("Running workflow... Please wait.", false, true);
  setWorkflowStatus("running");
  try {
    await ensureProject();
    await api("/workflow/run", { method: "POST" });
    await pollWorkflowUntilDone();
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    // Recompute from the now-current workflow_status instead of restoring
    // the pre-click label -- that would say "Generate" again even after the
    // run just finished (e.g. hide that it's now "Completed" or "Failed").
    delete button.dataset.busy;
    updateRunButton();
  }
});
$("#live-run-button").addEventListener("click", async () => {
  if (!(await appConfirm("This resets and regenerates the business model, solution model, system model, data model, architecture, diagrams, and mockups using the current live provider. Every prior version is kept in history.", { title: "Regenerate whole design", confirmLabel: "Regenerate everything", danger: true }))) return;
  const button = $("#live-run-button");
  const originalHtml = button.innerHTML;
  button.disabled = true;
  button.innerHTML = `<span class="btn-spinner"></span> Regenerating design...`;
  const provider = titleCase(state.status?.provider?.provider || "AI");
  showNotice(`Regenerating whole design with ${provider}... This may take up to a minute. Please wait.`, false, true);
  setWorkflowStatus("running");
  try {
    await ensureProject();
    await api("/workflow/restart", { method: "POST" });
    await pollWorkflowUntilDone();
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    button.innerHTML = originalHtml;
    button.disabled = false;
    updateRunButton(); // the main run button's own label/disabled-state also needs refreshing after this
  }
});
$("#export-data-model-button")?.addEventListener("click", () => downloadExport("/data-model/export", "data-model.zip"));
$("#export-mockups-button")?.addEventListener("click", () => downloadExport("/mockup-pages/export", "mockups.html"));
$("#refresh-button").addEventListener("click", refresh); $("#close-dialog").addEventListener("click", () => { state.pendingCommentTarget = null; $("#artifact-dialog").close(); });
$("#approve-button").addEventListener("click", () => state.selected && action(`/artifacts/${state.selected.metadata.logical_id}/approve`, {}, "Approving artifact..."));
$("#changes-button").addEventListener("click", () => state.selected && action(`/artifacts/${state.selected.metadata.logical_id}/request-changes`, { note: "Changes requested from the review workspace." }, "Submitting review decision..."));
$("#retry-button").addEventListener("click", () => state.selected && action(`/artifacts/${state.selected.metadata.logical_id}/retry`, { instruction: "Update this artifact using the open review comments." }, `Retrying ${state.selected?.metadata?.logical_id || "artifact"}... Please wait.`));
$("#comment-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = $("#comment-text").value.trim();
  if (!text) return;
  const target = state.pendingCommentTarget;
  const artifactId = target?.artifact_id || state.selected?.metadata?.logical_id;
  const location = target?.location || { surface: "review-workspace" };
  if (!artifactId) return;
  try {
    await action(`/artifacts/${artifactId}/comments`, { text, location });
    $("#comment-text").value = "";
    state.pendingCommentTarget = null;
    if (location.screen_id) {
      // Point at the exact button that applies THIS comment -- the
      // generic "retry the artifact" wording left it ambiguous whether
      // that meant this one screen or the full "Regenerate mockups" (which
      // also works, but re-sends every screen through the model at once).
      const screen = (state.currentMockupScreens || []).find((s) => screenId(s) === location.screen_id);
      const name = screen ? screenName(screen) : location.screen_id;
      showNotice(`Comment pinned to "${name}" -- click "🎯 Regenerate this screen" there to apply it.`);
    } else {
      showNotice("Comment saved -- retry the artifact to have the agent apply it.");
    }
  } catch (error) { showNotice(error.message, true); }
});
// Boot: resolve current project + tab from the URL hash, then render
// whichever level that resolves to -- the Projects picker (no hash, or no
// project in it) or an open project's own workspace.
(async () => {
  const { projectId, tab } = parseHash();
  state.projectId = projectId;
  applyActiveTab(tab, projectId);
  if (projectId) { renderOverviewSkeleton(); await refreshProjects(); await refresh(); }
  else await renderProjectsView();
  updateHash({ projectId, tab });
  renderPendingDiagrams(); // render the landing view's diagrams now that it's visible
  if (tab === "overview") redrawTraceEdges(); // ensure the trace connectors draw on first load
})();
