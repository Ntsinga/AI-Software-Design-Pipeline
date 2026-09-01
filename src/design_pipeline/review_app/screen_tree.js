// Pure, DOM-free logic for the mockup navigator's sidebar tree -- kept in
// its own file (nothing below touches `document`/`fetch`/any browser
// global) specifically so it can be unit-tested in plain Node without
// mocking a browser environment. Loaded via <script> before app.js in the
// browser (attaches these names to the global scope, same as every other
// helper in this app); required as a CommonJS module from
// tests/frontend/*.test.js in Node.
(function (root, factory) {
  const exported = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = exported;
  }
  Object.assign(root, exported);
})(typeof window !== "undefined" ? window : globalThis, function () {
  const escapeHtml = (value = "") => String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const screenName = (screen) => (typeof screen === "string" ? screen : screen.name);
  const screenId = (screen) => (typeof screen === "string" ? screen : screen.id);

  function buildScreenList(screens, currentIndex, pages) {
    // This is a click-through prototype, so the sidebar mirrors how you
    // actually move through it: each screen's own data-goto links determine
    // its children, indented beneath it, in the order those links appear in
    // its HTML. Grouping by workflow_id/entity_id (the old approach) sorted
    // screens by an architecture taxonomy orthogonal to navigation --
    // scattered a project's own Fieldwork/Wrap-up folders under unrelated
    // headings, and silently swallowed newly-added screens into whichever
    // existing group they happened to inherit workflow_id from (observed
    // live: several linked-screen additions never visibly changed the
    // sidebar at all). This is self-updating by construction: a new screen
    // slots in whichever chain the button that reaches it belongs to, and a
    // screen nothing links to (or a cycle-only screen unreachable from any
    // root) still shows up, just demoted to a dedicated "Unlinked" section
    // instead of silently vanishing.
    if (!screens.length) return "";
    const byId = new Map();
    screens.forEach((screen, index) => byId.set(screenId(screen), { screen, index }));
    const pageById = new Map((pages || []).map((page) => [page.screen_id, page]));
    const linksOf = (id) => {
      const html = pageById.get(id)?.html || "";
      const seen = new Set();
      const targets = [];
      for (const match of html.matchAll(/data-goto="([^"]+)"/g)) {
        if (byId.has(match[1]) && match[1] !== id && !seen.has(match[1])) {
          seen.add(match[1]);
          targets.push(match[1]);
        }
      }
      return targets;
    };
    const referenced = new Set();
    screens.forEach((screen) => linksOf(screenId(screen)).forEach((target) => referenced.add(target)));
    const isLanding = (screen) => typeof screen !== "string" && screen.workflow_id === "__landing__";
    const rootIds = new Set();
    const roots = screens.filter((screen) => {
      const id = screenId(screen);
      if (rootIds.has(id) || !(isLanding(screen) || !referenced.has(id))) return false;
      rootIds.add(id);
      return true;
    });

    const visited = new Set();
    const rows = [];
    const visit = (id, depth) => {
      if (visited.has(id) || !byId.has(id)) return;
      visited.add(id);
      rows.push({ ...byId.get(id), depth });
      linksOf(id).forEach((childId) => visit(childId, depth + 1));
    };
    roots.forEach((screen) => visit(screenId(screen), 0));
    const orphans = screens.filter((screen) => !visited.has(screenId(screen)));

    // Depth is conveyed by padding alone -- with no explanation, that just
    // reads as an unexplained gap ("was there supposed to be an icon
    // there?"), not "this is nested under the item above." A small muted
    // corner-arrow glyph on every non-root row makes the nesting legible
    // without needing connector lines.
    const row = ({ screen, index, depth }) => {
      const marker = depth > 0 ? `<span class="screen-nest-marker" aria-hidden="true">↳</span>` : "";
      return `<button class="${index === currentIndex ? "active" : ""}" style="padding-left:${12 + Math.min(depth, 6) * 14}px" data-screen="${index}">${marker}${escapeHtml(screenName(screen))}</button>`;
    };
    const tree = rows.map(row).join("");
    const unlinked = orphans.length
      ? `<div class="screen-group"><p class="screen-group-label">Unlinked</p>${orphans.map((screen) => row({ ...byId.get(screenId(screen)), depth: 0 })).join("")}</div>`
      : "";
    return tree + unlinked;
  }

  return { escapeHtml, screenName, screenId, buildScreenList };
});
