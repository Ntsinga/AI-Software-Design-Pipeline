// Unit tests for review_app/screen_tree.js -- the mockup navigator's
// sidebar tree logic, extracted into its own DOM-free module specifically
// so it's testable here without a browser. Zero dependencies: uses only
// Node's built-in test runner (Node 18+).
//
// Run with:  node --test tests/frontend

const test = require("node:test");
const assert = require("node:assert/strict");
const { buildScreenList } = require("../../src/design_pipeline/review_app/screen_tree.js");

function page(screenId, ...targets) {
  const goto = targets.map((t) => `<button data-goto="${t}">go</button>`).join("");
  return { screen_id: screenId, html: `<html><body>${goto}</body></html>` };
}

function screen(id, name, extra = {}) {
  return { id, name, ...extra };
}

test("empty screens list renders nothing", () => {
  assert.equal(buildScreenList([], 0, []), "");
});

test("landing screen is always the root, even before any pages exist", () => {
  const screens = [screen("a", "Landing", { workflow_id: "__landing__" }), screen("b", "Other")];
  const html = buildScreenList(screens, 0, []);
  // No pages means no known links, so both screens fall back to being
  // their own roots at depth 0 -- but landing must still come first.
  const order = [...html.matchAll(/data-screen="(\d+)"/g)].map((m) => Number(m[1]));
  assert.deepEqual(order, [0, 1]);
});

test("a simple chain nests each child under its parent, in link order", () => {
  const screens = [screen("a", "A", { workflow_id: "__landing__" }), screen("b", "B"), screen("c", "C")];
  const pages = [page("a", "b"), page("b", "c"), page("c")];
  const html = buildScreenList(screens, -1, pages);

  const buttons = [...html.matchAll(/<button[^>]*style="padding-left:(\d+)px"[^>]*>(.*?)<\/button>/g)];
  assert.equal(buttons.length, 3);
  const [a, b, c] = buttons;
  assert.ok(a[2].includes("A") && !a[2].includes("screen-nest-marker"));
  assert.ok(b[2].includes("B") && b[2].includes("screen-nest-marker"));
  assert.ok(c[2].includes("C") && c[2].includes("screen-nest-marker"));
  // Strictly increasing indentation down the chain.
  assert.ok(Number(a[1]) < Number(b[1]));
  assert.ok(Number(b[1]) < Number(c[1]));
});

test("multiple links from one screen appear as children in the order they appear in its HTML", () => {
  const screens = [screen("a", "A", { workflow_id: "__landing__" }), screen("first", "First"), screen("second", "Second")];
  const pages = [page("a", "second", "first"), page("first"), page("second")];
  const html = buildScreenList(screens, -1, pages);
  const names = [...html.matchAll(/<button[^>]*>(?:<span[^>]*>.*?<\/span>)?([^<]*)<\/button>/g)].map((m) => m[1]);
  assert.deepEqual(names, ["A", "Second", "First"]);
});

test("a link cycle does not duplicate a screen or hang", () => {
  const screens = [screen("a", "A", { workflow_id: "__landing__" }), screen("b", "B")];
  const pages = [page("a", "b"), page("b", "a")]; // b links back to a
  const html = buildScreenList(screens, -1, pages);
  const occurrences = html.split('data-screen="1"').length - 1; // b is index 1
  assert.equal(occurrences, 1);
});

test("a screen unreachable from any root lands in a dedicated Unlinked section", () => {
  // A screen nothing points to is trivially its own root (that's the
  // "top-level item" case, covered by the chain test above). Real
  // unreachability only happens when a screen is referenced exclusively by
  // other screens that are themselves unreachable -- e.g. an island pair
  // that only link to each other, with no path in from any actual root.
  const screens = [screen("a", "A", { workflow_id: "__landing__" }), screen("b", "B"), screen("c", "C")];
  const pages = [page("a"), page("b", "c"), page("c", "b")]; // b <-> c, nothing links to either from "a"
  const html = buildScreenList(screens, -1, pages);
  assert.ok(html.includes("Unlinked"));
  assert.ok(html.includes(">B<") || html.includes("B</button>"));
  assert.ok(html.includes(">C<") || html.includes("C</button>"));
});

test("the current screen's button carries the active class", () => {
  const screens = [screen("a", "A", { workflow_id: "__landing__" }), screen("b", "B")];
  const pages = [page("a", "b"), page("b")];
  const html = buildScreenList(screens, 1, pages);
  assert.ok(/data-screen="1"[^>]*class="[^"]*active/.test(html) || /class="active"[^>]*data-screen="1"/.test(html));
  assert.ok(!new RegExp(`data-screen="0"[^>]*class="[^"]*active`).test(html));
});

test("screen names are HTML-escaped", () => {
  const screens = [screen("a", 'A & <script>alert(1)</script>', { workflow_id: "__landing__" })];
  const html = buildScreenList(screens, -1, []);
  assert.ok(!html.includes("<script>alert(1)</script>"));
  assert.ok(html.includes("&amp;"));
});

test("legacy plain-string screens (pre-mockup-pages) still render as a flat list", () => {
  const screens = ["Screen One", "Screen Two"];
  const html = buildScreenList(screens, 0, []);
  assert.ok(html.includes("Screen One"));
  assert.ok(html.includes("Screen Two"));
});
