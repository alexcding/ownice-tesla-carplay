// Settings migration and numeric protocol modes, without a player connection.
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../player/zq-settings.js", import.meta.url), "utf8");
function settings(saved, native) {
  const window = {addEventListener() {}};
  const context = vm.createContext({
    window, URL, location: {href: "https://box/player.html"}, navigator: {maxTouchPoints: 0}, performance: {now: () => 0},
    document: {body: null, addEventListener() {}},
    localStorage: {getItem: () => typeof saved === "string" ? saved : JSON.stringify(saved)},
  });
  vm.runInContext(native ? source.replace("const NATIVE_HEVC = false;", "const NATIVE_HEVC = true;") : source, context);
  return window.__zq;
}
for (const native of [false, true]) {
  for (const [saved, mode] of [
    [{}, 1], [{h264: true}, 1], [{h264: false}, 0],
    [{vmode: 0, h264: true}, 0], [{vmode: 1, h264: false}, 1],
    [{vmode: 2}, native ? 2 : 1], [{vmode: "2"}, 1],
    [{vmode: -1}, 1], [{vmode: 3}, 1], [{vmode: null}, 1],
    ["null", 1], ["{broken", 1],
  ]) {
    const result = settings(saved, native);
    assert.equal(result.mode, mode, JSON.stringify({saved, native}));
    assert.equal(result.h264, mode > 0);
  }
}
console.log("Legacy H.264/JPEG settings migrate; numeric modes remain distinct; unsupported modes fall back to H.264.");

// Exercise the actual panel and Apply handler, including its reconnect guidance.
class Element {
  constructor() {
    this.children = []; this.style = {}; this.className = "";
    this.classList = {contains: () => false, toggle() {}, remove() {}};
  }
  append(...children) { this.children.push(...children); }
  appendChild(child) { this.append(child); return child; }
  replaceChildren(...children) { this.children = children; }
  addEventListener() {}
  setAttribute() {}
  remove() {}
  getBoundingClientRect() { return {right: 300, bottom: 20}; }
  querySelector(selector) {
    for (const child of this.children) {
      if (!(child instanceof Element)) continue;
      if (child.className.split(" ").includes(selector.slice(1))) return child;
      const found = child.querySelector(selector);
      if (found) return found;
    }
    return null;
  }
}
for (const native of [false, true]) {
  const elements = [];
  let ready, saved, reloads = 0;
  const document = {
    body: null, head: new Element(), documentElement: {clientWidth: 1280, clientHeight: 720},
    addEventListener(event, callback) { if (event === "DOMContentLoaded") ready = callback; },
    createElement() { const element = new Element(); elements.push(element); return element; },
    querySelector: () => null, querySelectorAll: () => [],
  };
  const window = {addEventListener() {}, innerWidth: 1280, innerHeight: 720};
  const context = vm.createContext({
    window, document, URL, history: {replaceState() {}}, navigator: {maxTouchPoints: 0}, performance: {now: () => 0},
    localStorage: {getItem: () => "{}", setItem: (key, value) => { saved = JSON.parse(value); }},
    location: {href: "https://box/player.html", reload: () => { reloads++; }}, setInterval() {},
    MutationObserver: class {observe() {}}, Date: class {static now() { return 0; }},
    fetch: async (url, options) => {
      assert.equal(url, "/zq-codec.txt");
      assert.equal(options.cache, "no-store");
      return {ok: true, text: async () => "Codec change failed; compressed passthrough retained.\n"};
    },
  });
  vm.runInContext(native ? source.replace("const NATIVE_HEVC = false;", "const NATIVE_HEVC = true;") : source, context);
  document.body = new Element(); ready();
  window.__zq.stats = {codec: "hvc1.1.6.L120.B0", size: "1280x720"};
  elements.find(el => el.id === "zq-btn").onclick();
  await new Promise(resolve => setImmediate(resolve));
  const panel = elements.find(el => el.id === "zq-panel");
  assert.match(panel.querySelector(".stream-info").children.join(""), /HEVC/);
  assert.equal(panel.querySelector(".stream-info").children.join("").includes("Codec change failed"), native);
  if (native) assert.ok(panel.children.some(el =>
    /changes take effect after.*reconnect your phone/.test(el.textContent || "")));
  assert.ok(panel.children.some(el => el.textContent?.includes(native ? "disconnect the phone" : "requires the native")));
  const allButtons = () => panel.children.flatMap(el => el.children || []);
  const hevc = allButtons().find(el => el.textContent === "HEVC (HD)");
  assert.equal(!!hevc, native);
  for (const [label, mode] of (native ? [["HEVC (HD)", 2], ["H.264 (HD)", 1], ["JPEG (compatible)", 0]]
    : [["H.264 (HD)", 1], ["JPEG (compatible)", 0]])) {
    allButtons().find(el => el.textContent === label).onclick();
    panel.querySelector(".apply").onclick();
    assert.equal(saved.vmode, mode);
  }
  assert.equal(reloads, native ? 3 : 2);
}
console.log("Panel exposes supported codecs, retains reconnect guidance and stream stats, and saves numeric modes before reloading.");

// Startup must settle capability before the bundle sends INIT. Refusals are
// session-scoped; blocked storage must still allow a bounded runtime fallback.
async function hevcLoad({probe, blocked = false, url = "https://box/player.html", stale} = {}) {
  const store = {"zq.settings": JSON.stringify({vmode: 2}), "zq.hevc": JSON.stringify(stale)};
  const elements = [];
  let ready, tick, reloads = 0, replacement;
  const window = {addEventListener() {}};
  if (probe !== undefined) window.VideoDecoder = {isConfigSupported: async () => {
    if (probe instanceof Error) throw probe;
    return {supported: probe};
  }};
  const document = {
    body: null, head: new Element(), documentElement: {clientWidth: 1280, clientHeight: 720},
    addEventListener(event, fn) { if (event === "DOMContentLoaded") ready = fn; },
    createElement() { const el = new Element(); elements.push(el); return el; },
    querySelector: () => null, querySelectorAll: () => [],
  };
  const context = vm.createContext({
    window, document, URL, setTimeout, clearTimeout,
    navigator: {maxTouchPoints: 0, userAgent: "car/1"}, performance: {now: () => 0},
    localStorage: {getItem: key => store[key] ?? null, setItem: (key, value) => {
      if (blocked) throw Error("blocked"); store[key] = value;
    }},
    location: {href: url, reload() { reloads++; }, replace(value) { replacement = value; }},
    setInterval(fn) { tick = fn; }, MutationObserver: class {observe() {}},
    Date, fetch: async () => ({ok: false}),
  });
  vm.runInContext(source.replace("const NATIVE_HEVC = false;", "const NATIVE_HEVC = true;"), context);
  await window.__zq.hevcReady;
  document.body = new Element(); ready();
  return {window, store, tick, get reloads() { return reloads; }, get replacement() { return replacement; }};
}
let r = await hevcLoad({probe: false});
assert.equal(r.window.__zq.mode, 1); assert.equal(r.reloads, 0);
r = await hevcLoad({probe: true, stale: {ua: "car/1", ok: false, by: "decode"}});
assert.equal(r.window.__zq.mode, 2); // a previous refusal cannot ban a working decoder
r.window.__zq.stats = {hevcUnsupported: true};
r.tick(); r.tick();
assert.equal(r.reloads, 1); assert.equal(JSON.parse(r.store["zq.settings"]).vmode, 1);
r = await hevcLoad({probe: true, blocked: true});
r.window.__zq.stats = {hevcUnsupported: true}; r.tick(); r.tick();
assert.equal(r.reloads, 0); assert.match(r.replacement, /zq-h264=1/);
r = await hevcLoad({probe: true, blocked: true, url: r.replacement});
assert.equal(r.window.__zq.mode, 1); r.tick(); assert.equal(r.replacement, undefined);
r = await hevcLoad(); assert.equal(r.window.__zq.mode, 1); // no VideoDecoder
r = await hevcLoad({probe: Error("temporary")}); assert.equal(r.window.__zq.mode, 2);
console.log("Startup gates INIT, blocked storage falls back once, and transient refusals do not permanently ban HEVC.");
