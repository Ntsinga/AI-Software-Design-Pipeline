const state = { status: null, artifacts: [], selected: null, currentMockup: 0, projectId: "default", projects: [] };
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
const escapeHtml = (value = "") => String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
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
function renderStats() {
  const workflow = state.status?.workflow_status || "not_started";
  const reviewCount = state.artifacts.filter((artifact) => artifact.status === "awaiting_review").length;
  const approved = state.artifacts.filter((artifact) => artifact.status === "approved").length;
  const provider = state.status?.provider;
  const agentMode = provider ? `${titleCase(provider.provider)} · ${titleCase(provider.mode)}` : "Stub · Deterministic";
  $("#stats").innerHTML = [["Workflow", titleCase(workflow)], ["Agent mode", agentMode], ["Artifacts", state.artifacts.length], ["Awaiting review", reviewCount], ["Approved", approved]].map(([label, value]) => `<div class="stat-card"><span>${label}</span><strong>${value}</strong></div>`).join("");
}
function renderArtifacts() {
  const target = $("#artifact-cards");
  if (!state.artifacts.length) return empty(target, "No artifacts yet. Upload a document and start generation.");
  target.className = "artifact-grid";
  target.innerHTML = state.artifacts.map((artifact) => `<button class="artifact-card" data-artifact="${artifact.logical_id}"><p>${titleCase(artifact.type)}</p><h3>${titleCase(artifact.logical_id)}</h3><p>v${artifact.version} · ${artifact.requirements.length} linked requirement${artifact.requirements.length === 1 ? "" : "s"}</p><span class="status-pill ${artifact.status}">${titleCase(artifact.status)}</span></button>`).join("");
  target.querySelectorAll("[data-artifact]").forEach((button) => button.addEventListener("click", () => openArtifact(button.dataset.artifact)));
}
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
    $("#data-model-erd").textContent = mermaid_source;
    if (window.mermaid) await window.mermaid.run({ querySelector: "#data-model-erd" });
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
  target.innerHTML = `<div class="panel"><h3>${architecture.style || "Architecture"}</h3><p class="muted-copy">${architecture.rationale || ""}</p><div class="component-flow">${(architecture.components || []).map((component, index) => `<div class="component">${component}</div>${index < architecture.components.length - 1 ? "<span class=\"arrow\">→</span>" : ""}`).join("")}</div></div><div class="model-grid"><div class="panel"><h3>Boundaries</h3>${list(architecture.boundaries)}</div><div class="panel"><h3>Recommended diagrams</h3>${list(recommended?.recommended)}</div></div>${renderDiagrams(diagrams)}`;
  const renderable = diagrams.filter((diagram) => diagram.valid !== false && (diagram.mermaid_source || diagram.mermaid_code || diagram.code));
  if (renderable.length && window.mermaid) {
    try { await window.mermaid.run({ querySelector: "#architecture-model .mermaid" }); } catch (error) { /* raw Mermaid source stays visible if rendering fails */ }
  }
  target.querySelectorAll("[data-comment-diagram]").forEach((button) => button.addEventListener("click", () => {
    const name = button.dataset.commentDiagram;
    addLocatedComment("diagrams", { kind: "diagram", diagram_name: name }, `Comment on the "${name}" diagram:`);
  }));
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
    return `<div class="diagram-block"><h4>${name} ${commentButton}</h4><pre class="mermaid" id="diagram-${index}">${escapeHtml(source)}</pre>${chartLink}</div>`;
  }).join("");
  return `<div class="panel"><h3>Diagrams</h3>${blocks}</div>`;
}
// Screens were plain name strings before mockup-pages existed; tolerate both.
const screenName = (screen) => (typeof screen === "string" ? screen : screen.name);
const screenId = (screen) => (typeof screen === "string" ? screen : screen.id);
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
  $("#artifact-dialog").showModal();
  setTimeout(() => $("#comment-text").focus(), 50);
}
function setCommentMode(iframe, enabled) {
  iframe?.contentWindow?.postMessage({ type: "mockup-comment-mode", enabled }, "*");
  state.mockupCommentMode = enabled;
  document.querySelector("#mockup-comment-toggle")?.classList.toggle("active", enabled);
}
window.addEventListener("message", (event) => {
  if (event.data?.type === "mockup-bridge-error") {
    showNotice(`Mockup bridge error (${event.data.phase}): ${event.data.error}`, true);
    return;
  }
  if (event.data?.type === "mockup-goto") {
    const spec = state.currentMockupScreens || [];
    const index = spec.findIndex((screen) => screenId(screen) === event.data.screen_id);
    if (index >= 0) { state.currentMockup = index; renderMockups(); }
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
        showNotice("Comment pinned -- retry mockups to have the agent apply it.");
        loadPinsForScreen(activeId);
        renderScreenComments(activeId);
      } catch (error) { showNotice(error.message, true); }
    });
    setCommentMode(document.querySelector(".mock-frame-iframe"), false);
  }
});
function buildScreenList(screens, currentIndex, workflowsById) {
  // Screens organize by either workflow_id (workflow steps) or entity_id
  // (CRUD screens for a specific entity); a screen carrying both goes
  // under its workflow. Screens with neither drop into "Other". Older
  // mockups with plain-string screens render as the flat list.
  const hasGroups = screens.some((screen) => typeof screen === "object" && (screen?.workflow_id || screen?.entity_id));
  if (!hasGroups) return screens.map((screen, index) => `<button class="${index === currentIndex ? "active" : ""}" data-screen="${index}">${escapeHtml(screenName(screen))}</button>`).join("");
  const groups = new Map();
  const groupKey = (screen) => screen.workflow_id ? `w:${screen.workflow_id}` : (screen.entity_id ? `e:${screen.entity_id}` : "z:other");
  screens.forEach((screen, index) => {
    const key = groupKey(screen);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push({ screen, index });
  });
  // Stable ordering: landing first, workflows next (in architecture order), entities after, "other" last.
  const orderedKeys = [...groups.keys()].sort((a, b) => {
    const rank = (k) => k === "w:__landing__" ? 0 : k.startsWith("w:") ? 1 : k.startsWith("e:") ? 2 : 3;
    return rank(a) - rank(b) || a.localeCompare(b);
  });
  return orderedKeys.map((key) => {
    const entries = groups.get(key);
    let heading;
    if (key === "w:__landing__") heading = "Landing";
    else if (key.startsWith("w:")) { const id = key.slice(2); heading = workflowsById.get(id)?.name || id; }
    else if (key.startsWith("e:")) heading = `Entity: ${key.slice(2)}`;
    else heading = "Other";
    const buttons = entries.map(({ screen, index }) => `<button class="${index === currentIndex ? "active" : ""}" data-screen="${index}">${escapeHtml(screenName(screen))}</button>`).join("");
    return `<div class="screen-group"><p class="screen-group-label">${escapeHtml(heading)}</p>${buttons}</div>`;
  }).join("");
}
async function renderMockups() {
  const target = $("#mockup-model"); const artifact = state.artifacts.find((item) => item.logical_id === "mockup-spec");
  if (!artifact) return empty(target, "Approve the architecture to generate the mockup specification.");
  const mockup = (await api(`/artifacts/mockup-spec`)).content; const screens = mockup.screens || [];
  state.currentMockupScreens = screens;
  const pagesArtifact = state.artifacts.find((item) => item.logical_id === "mockup-pages");
  let pages = []; if (pagesArtifact) pages = (await api(`/artifacts/mockup-pages`)).content || [];
  // architecture-model.workflows may not exist for older projects -- fall back to empty.
  const workflowsById = new Map();
  const archArtifact = state.artifacts.find((item) => item.logical_id === "architecture-model");
  if (archArtifact) { const arch = (await api(`/artifacts/architecture-model`)).content; for (const w of arch?.workflows || []) workflowsById.set(w.id, w); }
  state.currentMockup = Math.min(state.currentMockup, Math.max(screens.length - 1, 0));
  const active = screens[state.currentMockup]; const activeName = active ? screenName(active) : "Screen preview"; const activeId = active ? screenId(active) : null;
  const page = pages.find((item) => item.screen_id === activeId || item.screen === activeName);
  target.className = "mockup-layout";
  const html = pageHtml(page);
  // sandbox="allow-scripts" (deliberately NOT allow-same-origin): model-authored
  // JS runs for local interactivity, but stays fully isolated from this app.
  const toolbar = html
    ? `<div class="mock-toolbar"><span>${escapeHtml(activeName)}</span><div class="toolbar-buttons"><button class="secondary-button" data-comment-screen>💬 Comment on screen</button><button class="secondary-button" id="mockup-comment-toggle" data-comment-element>📍 Comment on element</button><button class="secondary-button" data-retry-screen title="Regenerate only this screen -- every other screen stays exactly as-is">🎯 Regenerate this screen</button></div></div>`
    : "";
  const frame = html
    ? `<div class="mock-frame-wrap"><iframe class="mock-frame-iframe" title="${escapeHtml(activeName)} mockup" sandbox="allow-scripts" srcdoc="${escapeHtml(html)}"></iframe><div class="mock-pin-layer"></div></div>`
    : `<div class="mock-frame"><header><div><p class="eyebrow">INTERACTIVE MOCKUP</p><h3>${escapeHtml(activeName)}</h3>${active?.purpose ? `<p class="muted-copy">${escapeHtml(active.purpose)}</p>` : ""}</div><span class="status-pill">Synthetic data</span></header><div class="mock-content"><div class="mock-block"><strong>Workflow status</strong><p class="muted-copy">Awaiting review</p></div><div class="mock-block"><strong>Linked artifacts</strong><p class="muted-copy">System model · Architecture</p></div><div class="mock-block"><strong>Actions</strong><p class="muted-copy">Approve · Request changes</p></div></div></div>`;
  target.innerHTML = `<div class="screen-list">${buildScreenList(screens, state.currentMockup, workflowsById)}</div><div class="mock-column">${toolbar}<div id="mock-comments" class="mock-comments"></div>${frame}</div>`;
  target.querySelectorAll("[data-screen]").forEach((button) => button.addEventListener("click", () => { state.currentMockup = Number(button.dataset.screen); renderMockups(); }));
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
    if (!(await appConfirm(`Only the "${activeName}" screen will be regenerated -- every other screen stays exactly as it is now. Any comments pinned to this screen are applied.`, { title: "Regenerate this screen", confirmLabel: "Regenerate" }))) return;
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
  if (html) {
    const iframe = target.querySelector(".mock-frame-iframe");
    iframe.addEventListener("load", () => loadPinsForScreen(activeId));
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
async function renderReferencesStrip(stage) {
  const strip = document.querySelector(`[data-references-strip="${stage}"]`);
  if (!strip) return;
  let entries = [];
  try { entries = await api(`/references/${stage}`); } catch (error) { /* stage may be empty */ }
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
    </div>`).join("") || `<p class="muted-copy" style="margin:0;">No supporting documents yet. Add Word, PDF, Markdown, plain-text, or RST files here to give the ${stage} agent more context.</p>`;
  strip.innerHTML = `<h4>Supporting documents (${stage})</h4>${rows}<div class="attachment-uploader"><input type="file" data-reference-input accept=".docx,.md,.markdown,.txt,.rst,.pdf,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain,text/markdown" /><button class="secondary-button" data-reference-upload>Attach</button></div>`;
  strip.querySelector("[data-reference-upload]")?.addEventListener("click", async () => {
    const input = strip.querySelector("[data-reference-input]");
    const button = strip.querySelector("[data-reference-upload]");
    const file = input.files[0];
    if (!file) return showNotice("Choose a file first.", true);
    const originalHtml = button.innerHTML;
    button.disabled = true;
    button.innerHTML = `<span class="btn-spinner"></span> Attaching...`;
    showNotice(`Attaching ${file.name} to ${stage}...`, false, true);
    try {
      const payload = await documentPayload(file);
      await api(`/references/${stage}`, { method: "POST", body: JSON.stringify(payload) });
      showNotice(`${file.name} attached to ${stage}.`);
      await renderReferencesStrip(stage);
    } catch (error) { showNotice(error.message, true); }
    finally {
      button.innerHTML = originalHtml;
      button.disabled = false;
    }
  });
  strip.querySelectorAll("[data-reference-remove]").forEach((button) => button.addEventListener("click", async () => {
    if (!(await appConfirm(`Remove "${button.dataset.referenceRemove}" from this stage's supporting documents?`, { title: "Remove attachment", confirmLabel: "Remove", danger: true }))) return;
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
async function renderHistory() {
  const target = $("#history-list"); const history = await api("/history");
  if (!history.length) return empty(target, "No recorded events yet.");
  target.className = "history-list"; target.innerHTML = history.slice().reverse().map((event) => `<div class="history-item"><div><strong>${titleCase(event.event_type)}</strong><p>${event.artifact_id || event.step_id || "Project"}</p></div><p>${new Date(event.timestamp).toLocaleString()}</p></div>`).join("");
}
async function refresh() {
  try { state.status = await api("/status"); state.artifacts = await api("/artifacts"); $("#project-name").textContent = state.status.project_id || "Design Pipeline"; setWorkflowStatus(state.status.workflow_status); renderStats(); renderArtifacts(); ["system-model", "data-model", "architecture-model"].forEach(updateStageStatus); if (state.status.provider?.provider) $("#provider-select").value = state.status.provider.provider; if (state.status.provider?.mode === "live" && !state.status.provider.configured) showNotice(`${titleCase(state.status.provider.provider)} is selected but needs an API key and model in .env -- no restart needed once it's saved.`, true); await Promise.all([renderSystemModel(), renderDataModel(), renderArchitecture(), renderMockups(), renderHistory(), renderReferencesStrip("system"), renderReferencesStrip("data-model"), renderReferencesStrip("architecture"), renderReferencesStrip("mockup")]); } catch (error) { setWorkflowStatus("not_started"); showNotice(error.message, true); }
}
async function openArtifact(id) {
  try { const artifact = await api(`/artifacts/${id}`); state.selected = artifact; $("#dialog-title").textContent = titleCase(id); $("#dialog-type").textContent = titleCase(artifact.metadata.type); $("#dialog-meta").innerHTML = `<span>v${artifact.metadata.version}</span><span class="status-pill ${artifact.metadata.status}">${titleCase(artifact.metadata.status)}</span><span>${artifact.metadata.requirements.join(", ") || "No requirement IDs"}</span>`; $("#dialog-content").textContent = JSON.stringify(artifact.content, null, 2); const versions = await api(`/artifacts/${id}/versions`); $("#version-list").innerHTML = versions.map((version) => `<span class="version">v${version.version} · ${titleCase(version.status)}</span>`).join(""); $("#artifact-dialog").showModal(); } catch (error) { showNotice(error.message, true); }
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
const VALID_TABS = new Set(["overview", "system", "data-model", "architecture", "mockups", "history"]);
function parseHash() {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const parts = raw.split("/").filter(Boolean);
  let projectId = "default", tab = "overview";
  if (parts[0] === "projects" && parts[1]) projectId = decodeURIComponent(parts[1]);
  if (parts[2] && VALID_TABS.has(parts[2])) tab = parts[2];
  else if (parts[0] && VALID_TABS.has(parts[0])) tab = parts[0]; // #/mockups short-form
  return { projectId, tab };
}
function updateHash(patch = {}) {
  const current = parseHash();
  const next = { ...current, ...patch };
  const target = `#/projects/${encodeURIComponent(next.projectId)}/${next.tab}`;
  if (window.location.hash !== target) window.location.hash = target;
}
function applyActiveTab(tab) {
  document.querySelectorAll(".nav-link").forEach((link) => link.classList.toggle("active", link.dataset.view === tab));
  document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.id === `${tab}-view`));
}
document.addEventListener("click", (event) => { if (!event.target.closest(".mock-pin-popover, .mock-pin")) closePinPopover(); });
document.querySelectorAll(".nav-link").forEach((button) => button.addEventListener("click", () => updateHash({ tab: button.dataset.view })));
async function onHashChange() {
  const { projectId, tab } = parseHash();
  applyActiveTab(tab);
  if (projectId !== state.projectId) { state.projectId = projectId; await refresh(); }
}
window.addEventListener("hashchange", onHashChange);

async function refreshProjects() {
  try { state.projects = await api("/projects"); } catch (error) { state.projects = []; }
  const select = $("#project-select"); if (!select) return;
  select.innerHTML = state.projects.map((project) => `<option value="${escapeHtml(project.id)}">${escapeHtml(project.name || project.id)}</option>`).join("");
  select.value = state.projectId;
}
$("#project-select")?.addEventListener("change", (event) => updateHash({ projectId: event.target.value }));
$("#new-project-button")?.addEventListener("click", async () => {
  const name = await appPrompt("Name your project. Letters, digits, and dashes work best.", { title: "New project", placeholder: "e.g. Customer Portal Redesign", confirmLabel: "Create project" });
  if (!name) return;
  try {
    const project = await api("/projects", { method: "POST", body: JSON.stringify({ name: name.trim() }) });
    await refreshProjects();
    updateHash({ projectId: project.id, tab: "overview" });
    showNotice(`Project "${project.name}" created.`);
  } catch (error) { showNotice(error.message, true); }
});

$("#document-input").addEventListener("change", (event) => { $("#file-name").textContent = event.target.files[0]?.name || "No file selected"; });
$("#provider-select").addEventListener("change", async (event) => { const previous = state.status?.provider?.provider || "stub"; try { const provider = await api("/provider", { method: "PUT", body: JSON.stringify({ provider: event.target.value }) }); showNotice(`Switched to ${titleCase(provider.provider)}.`); await refresh(); } catch (error) { event.target.value = previous; showNotice(error.message, true); } });

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
  if (name.endsWith(".docx") || name.endsWith(".pdf")) {
    if (file.size > 10 * 1024 * 1024) {
      const typeLabel = name.endsWith(".docx") ? "Word documents" : "PDF documents";
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
  const file = $("#document-input").files[0];
  if (!file) return showNotice("Choose a Word, PDF, Markdown, text, or RST file first.", true);
  const button = $("#upload-button");
  const originalHtml = button.innerHTML;
  button.disabled = true;
  button.innerHTML = `<span class="btn-spinner"></span> Uploading...`;
  showNotice(`Uploading and extracting text from ${file.name}...`, false, true);
  try {
    await ensureProject();
    await api("/documents/brd", { method: "POST", body: JSON.stringify(await documentPayload(file)) });
    showNotice(`${file.name} uploaded as the BRD input.`);
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
  const originalHtml = button.innerHTML;
  button.disabled = true;
  button.innerHTML = `<span class="btn-spinner"></span> Running...`;
  showNotice("Running workflow... Please wait.", false, true);
  setWorkflowStatus("running");
  try {
    await ensureProject();
    const report = await api("/workflow/run", { method: "POST" });
    showNotice(report.message, report.status === "failed");
    await refresh();
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    button.innerHTML = originalHtml;
    button.disabled = false;
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
    const report = await api("/workflow/restart", { method: "POST" });
    showNotice(report.message, report.status === "failed");
    await refresh();
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    button.innerHTML = originalHtml;
    button.disabled = false;
  }
});
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
    showNotice("Comment saved -- retry the artifact to have the agent apply it.");
  } catch (error) { showNotice(error.message, true); }
});
// Boot: resolve current project + tab from the URL hash, populate the
// project switcher, then run the initial refresh. If no hash is set,
// default to #/projects/default/overview and let hashchange fire.
(async () => {
  const { projectId, tab } = parseHash();
  state.projectId = projectId;
  applyActiveTab(tab);
  await refreshProjects();
  await refresh();
  if (!window.location.hash) updateHash({ projectId, tab });
})();
