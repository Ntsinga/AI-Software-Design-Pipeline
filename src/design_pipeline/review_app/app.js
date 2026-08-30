const state = { status: null, artifacts: [], selected: null, currentMockup: 0 };
const $ = (selector) => document.querySelector(selector);
const api = async (path, options = {}) => {
  const response = await fetch(path, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
  if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.detail || `Request failed (${response.status})`); }
  return response.status === 204 ? null : response.json();
};
const titleCase = (value = "") => value.replaceAll("-", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
const showNotice = (message, error = false) => { const notice = $("#notice"); notice.textContent = message; notice.className = `notice${error ? " error" : ""}`; setTimeout(() => notice.classList.add("hidden"), 5200); };
const empty = (target, message) => { target.className = `${target.className.split(" ").filter((name) => name !== "empty-state").join(" ")} empty-state`; target.textContent = message; };

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
async function renderSystemModel() {
  const target = $("#system-model"); const artifact = state.artifacts.find((item) => item.logical_id === "system-model");
  if (!artifact) return empty(target, "Generate the system model to see the traceability map.");
  const model = (await api(`/artifacts/system-model`)).content;
  target.className = "model-layout";
  target.innerHTML = `<div class="panel"><h3>Traceability map</h3><div class="model-map">${[["Requirements", model.requirements], ["Capabilities", model.system_capabilities], ["Services", model.services], ["Screens", model.screens]].map(([name, values]) => `<div class="map-column"><h4>${name}</h4>${(values || []).map((value) => `<div class="map-node">${value}</div>`).join("") || "<div class=\"map-node\">—</div>"}</div>`).join("")}</div></div><div class="model-grid"><div class="panel"><h3>Business workflows</h3>${list(model.business_workflows)}</div><div class="panel"><h3>Permissions</h3>${Object.entries(model.permissions || {}).map(([role, rights]) => `<p><strong>${role}</strong><br><span class="muted-copy">${rights.join(", ")}</span></p>`).join("") || "<p class=\"muted-copy\">No permissions recorded.</p>"}</div></div>`;
}
async function renderArchitecture() {
  const target = $("#architecture-model"); const artifact = state.artifacts.find((item) => item.logical_id === "architecture-model");
  if (!artifact) return empty(target, "Approve the system model to generate architecture recommendations.");
  const architecture = (await api(`/artifacts/architecture-model`)).content; const recommendations = state.artifacts.find((item) => item.logical_id === "diagram-recommendations");
  let recommended = null; if (recommendations) recommended = (await api(`/artifacts/diagram-recommendations`)).content;
  target.className = "model-layout";
  target.innerHTML = `<div class="panel"><h3>${architecture.style || "Architecture"}</h3><p class="muted-copy">${architecture.rationale || ""}</p><div class="component-flow">${(architecture.components || []).map((component, index) => `<div class="component">${component}</div>${index < architecture.components.length - 1 ? "<span class=\"arrow\">→</span>" : ""}`).join("")}</div></div><div class="model-grid"><div class="panel"><h3>Boundaries</h3>${list(architecture.boundaries)}</div><div class="panel"><h3>Recommended diagrams</h3>${list(recommended?.recommended)}</div></div>`;
}
async function renderMockups() {
  const target = $("#mockup-model"); const artifact = state.artifacts.find((item) => item.logical_id === "mockup-spec");
  if (!artifact) return empty(target, "Approve the architecture to generate the mockup specification.");
  const mockup = (await api(`/artifacts/mockup-spec`)).content; const screens = mockup.screens || [];
  state.currentMockup = Math.min(state.currentMockup, Math.max(screens.length - 1, 0)); const active = screens[state.currentMockup] || "Screen preview";
  target.className = "mockup-layout";
  target.innerHTML = `<div class="screen-list">${screens.map((screen, index) => `<button class="${index === state.currentMockup ? "active" : ""}" data-screen="${index}">${screen}</button>`).join("")}</div><div class="mock-frame"><header><div><p class="eyebrow">INTERACTIVE MOCKUP</p><h3>${active}</h3></div><span class="status-pill">Synthetic data</span></header><div class="mock-content"><div class="mock-block"><strong>Workflow status</strong><p class="muted-copy">Awaiting review</p></div><div class="mock-block"><strong>Linked artifacts</strong><p class="muted-copy">System model · Architecture</p></div><div class="mock-block"><strong>Actions</strong><p class="muted-copy">Approve · Request changes</p></div></div></div>`;
  target.querySelectorAll("[data-screen]").forEach((button) => button.addEventListener("click", () => { state.currentMockup = Number(button.dataset.screen); renderMockups(); }));
}
async function renderHistory() {
  const target = $("#history-list"); const history = await api("/history");
  if (!history.length) return empty(target, "No recorded events yet.");
  target.className = "history-list"; target.innerHTML = history.slice().reverse().map((event) => `<div class="history-item"><div><strong>${titleCase(event.event_type)}</strong><p>${event.artifact_id || event.step_id || "Project"}</p></div><p>${new Date(event.timestamp).toLocaleString()}</p></div>`).join("");
}
async function refresh() {
  try { state.status = await api("/status"); state.artifacts = await api("/artifacts"); $("#project-name").textContent = state.status.project_id || "Design Pipeline"; setWorkflowStatus(state.status.workflow_status); renderStats(); renderArtifacts(); if (state.status.provider?.mode === "live" && !state.status.provider.configured) showNotice(`${titleCase(state.status.provider.provider)} is selected but needs both an API key and model in .env. Restart the server after saving it.`, true); await Promise.all([renderSystemModel(), renderArchitecture(), renderMockups(), renderHistory()]); } catch (error) { setWorkflowStatus("not_started"); showNotice(error.message, true); }
}
async function openArtifact(id) {
  try { const artifact = await api(`/artifacts/${id}`); state.selected = artifact; $("#dialog-title").textContent = titleCase(id); $("#dialog-type").textContent = titleCase(artifact.metadata.type); $("#dialog-meta").innerHTML = `<span>v${artifact.metadata.version}</span><span class="status-pill ${artifact.metadata.status}">${titleCase(artifact.metadata.status)}</span><span>${artifact.metadata.requirements.join(", ") || "No requirement IDs"}</span>`; $("#dialog-content").textContent = JSON.stringify(artifact.content, null, 2); const versions = await api(`/artifacts/${id}/versions`); $("#version-list").innerHTML = versions.map((version) => `<span class="version">v${version.version} · ${titleCase(version.status)}</span>`).join(""); $("#artifact-dialog").showModal(); } catch (error) { showNotice(error.message, true); }
}
async function action(path, body = {}) { await api(path, { method: "POST", body: JSON.stringify(body) }); $("#artifact-dialog").close(); await refresh(); }

document.querySelectorAll(".nav-link").forEach((button) => button.addEventListener("click", () => { document.querySelectorAll(".nav-link").forEach((link) => link.classList.remove("active")); document.querySelectorAll(".view").forEach((view) => view.classList.remove("active")); button.classList.add("active"); $(`#${button.dataset.view}-view`).classList.add("active"); }));
$("#document-input").addEventListener("change", (event) => { $("#file-name").textContent = event.target.files[0]?.name || "No file selected"; });
$("#initialize-button").addEventListener("click", async () => { try { await ensureProject(); showNotice("Project initialized."); await refresh(); } catch (error) { showNotice(error.message, true); } });
async function documentPayload(file) {
  if (file.name.toLowerCase().endsWith(".docx")) {
    if (file.size > 10 * 1024 * 1024) throw new Error("Word documents must be 10 MB or smaller.");
    const dataUrl = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = () => reject(new Error("Could not read the Word document.")); reader.readAsDataURL(file); });
    return { filename: file.name, content_base64: String(dataUrl).split(",", 2)[1] };
  }
  return { filename: file.name, text: await file.text() };
}
$("#upload-button").addEventListener("click", async () => { const file = $("#document-input").files[0]; if (!file) return showNotice("Choose a Word, Markdown, text, or RST file first.", true); try { await ensureProject(); await api("/documents/brd", { method: "POST", body: JSON.stringify(await documentPayload(file)) }); showNotice(`${file.name} uploaded as the BRD input.`); await refresh(); } catch (error) { showNotice(error.message, true); } });
$("#run-button").addEventListener("click", async () => { try { await ensureProject(); const report = await api("/workflow/run", { method: "POST" }); showNotice(report.message, report.status === "failed"); await refresh(); } catch (error) { showNotice(error.message, true); } });
$("#live-run-button").addEventListener("click", async () => { try { await ensureProject(); const report = await api("/workflow/restart", { method: "POST" }); showNotice(report.message, report.status === "failed"); await refresh(); } catch (error) { showNotice(error.message, true); } });
$("#refresh-button").addEventListener("click", refresh); $("#close-dialog").addEventListener("click", () => $("#artifact-dialog").close());
$("#approve-button").addEventListener("click", () => state.selected && action(`/artifacts/${state.selected.metadata.logical_id}/approve`));
$("#changes-button").addEventListener("click", () => state.selected && action(`/artifacts/${state.selected.metadata.logical_id}/request-changes`, { note: "Changes requested from the review workspace." }));
$("#retry-button").addEventListener("click", () => state.selected && action(`/artifacts/${state.selected.metadata.logical_id}/retry`, { instruction: "Update this artifact using the open review comments." }));
$("#comment-form").addEventListener("submit", async (event) => { event.preventDefault(); const text = $("#comment-text").value.trim(); if (!state.selected || !text) return; try { await action(`/artifacts/${state.selected.metadata.logical_id}/comments`, { text, location: { surface: "review-workspace" } }); $("#comment-text").value = ""; showNotice("Comment saved."); } catch (error) { showNotice(error.message, true); } });
refresh();
