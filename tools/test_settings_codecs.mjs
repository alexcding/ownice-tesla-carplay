// Settings migration and numeric protocol modes, without a player connection.
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../player/zq-settings.js", import.meta.url), "utf8");
function settings(saved, native) {
  const window = {addEventListener() {}};
  const context = vm.createContext({
    window, URL, location: {href: "https://box/player.html"}, navigator: {maxTouchPoints: 0}, performance: {now: () => 0},
    setInterval() {},
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
// A runtime refusal falls back for this session only, through a URL flag: the
// saved HEVC preference is untouched, so a new browser session probes again.
const saved = r.store["zq.settings"];
r.window.__zq.stats = {hevcUnsupported: true};
r.tick(); r.tick();
assert.equal(r.reloads, 0); assert.match(r.replacement, /zq-h264=1/);
assert.equal(r.store["zq.settings"], saved);
r = await hevcLoad({probe: true, blocked: true});
r.window.__zq.stats = {hevcUnsupported: true}; r.tick(); r.tick();
assert.equal(r.reloads, 0); assert.match(r.replacement, /zq-h264=1/);  // works with storage blocked too
r = await hevcLoad({probe: true, blocked: true, url: r.replacement});
assert.equal(r.window.__zq.mode, 1); r.tick(); assert.equal(r.replacement, undefined);  // no loop
r = await hevcLoad(); assert.equal(r.window.__zq.mode, 1); // no VideoDecoder
r = await hevcLoad({probe: Error("temporary")}); assert.equal(r.window.__zq.mode, 2);
console.log("Startup gates INIT, blocked storage falls back once, and transient refusals do not permanently ban HEVC.");

// INIT size policy and no-video recovery. The page's window gives 1328x658;
// dispatchEvent stands in for the bundle's resize handler, which asks
// __zq.initSize for the INIT size it will send.
const WIN = {w: 1328, h: 658};
const plain = v => JSON.parse(JSON.stringify(v));  // sandbox objects compare by value
async function recoveryPage(vmode, store = {}) {
  const intervals = [], resizes = [], listeners = {};
  let now = 0, ctrlSocket = null;
  class FakeSocket { constructor(url) { this.url = url; this.listeners = []; if (/:9002/.test(url)) ctrlSocket = this; }
    addEventListener(type, fn) { if (type === "message") this.listeners.push(fn); } }
  const window = {WebSocket: FakeSocket, VideoDecoder: {isConfigSupported: async () => ({supported: true})},
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    dispatchEvent(e) { resizes.push(plain(window.__zq.initSize(WIN.w, WIN.h))); }};
  const context = vm.createContext({
    window, URL, Event: class { constructor(type) { this.type = type; } }, setTimeout, clearTimeout,
    location: {href: "https://box/player.html"}, navigator: {maxTouchPoints: 0},
    performance: {now: () => now}, document: {body: null, addEventListener() {}},
    localStorage: {getItem: key => key === "zq.session" ? store.session ?? null : JSON.stringify({vmode}),
      setItem: (key, value) => { if (key === "zq.session") store.session = value; }},
    sessionStorage: {getItem: key => store[key] ?? null, setItem: (key, value) => { store[key] = value; },
      removeItem: key => { delete store[key]; }},
    setInterval(fn) { intervals.push(fn); },
  });
  vm.runInContext(source.replace("const NATIVE_HEVC = false;", "const NATIVE_HEVC = true;"), context);
  await window.__zq.hevcReady;
  new window.WebSocket("wss://zbox.link:9002");  // the bundle's control socket, through the wrapper
  const ctrl = m => ctrlSocket.listeners.forEach(fn => fn({data: JSON.stringify(m)}));
  return {window, resizes, listeners, store,
    session: state => ctrl({message_id: 11, session_state: state, phone_type: 5}),
    updateInfo: (width, height) => ctrl({message_id: 14, width, height}),
    tick: (ms, stats = window.__zq.stats) => { now += ms; window.__zq.stats = stats; intervals.forEach(fn => fn()); }};
}
{
  // Each initSize call is an INIT the bundle sends. Opening the player while
  // the phone is connected reuses the box's size (no phone reconnect).
  const first = await recoveryPage(2);
  first.updateInfo(1328, 600);  // a session at 1328x600 (remembered)
  assert.equal(first.store.session, JSON.stringify({w: 1328, h: 600}));
  const init = (page, w = WIN.w, h = WIN.h) => plain(page.window.__zq.initSize(w, h));
  const fresh = await recoveryPage(2, first.store);
  assert.deepEqual(init(fresh), WIN);  // no running session yet: the window
  const next = await recoveryPage(2, first.store);
  next.session(4);
  assert.deepEqual(init(next), {w: 1328, h: 600});  // reuse: no restart
  next.listeners.resize.forEach(fn => fn({isTrusted: true}));  // a resize that changed nothing
  assert.deepEqual(init(next), {w: 1328, h: 600});
  next.listeners.resize.forEach(fn => fn({isTrusted: true}));  // a real resize
  assert.deepEqual(init(next, 1400, 700), {w: 1400, h: 700});
  assert.deepEqual(plain(next.window.__zq.touchBounds()), {w: 1400, h: 700});  // bounds follow the INIT
  next.window.__zq.streamFrame(1392, 700);  // ...then the bundle's UPDATE_INFO stream
  assert.deepEqual(plain(next.window.__zq.touchBounds()), {w: 1392, h: 700});
  next.listeners.resize.forEach(fn => fn({isTrusted: false}));  // synthetic: not a user resize
  assert.deepEqual(init(next, 1500, 750), {w: 1400, h: 700});  // keeps what the box holds
  next.listeners.resize.forEach(fn => fn({isTrusted: true}));
  next.tick(3000);  // no INIT followed within 2 s: the reason expired
  assert.deepEqual(init(next, 1500, 750), {w: 1400, h: 700});
  // Apply & reload: a new resolution asks for the settings' size once; a codec
  // change asks for a size other than the box's (the phone renegotiates).
  next.store["zq.init-reason"] = "resize";
  const applied = await recoveryPage(2, next.store);
  applied.session(4);
  assert.deepEqual(init(applied), WIN);
  assert.equal(next.store["zq.init-reason"], undefined);
  assert.deepEqual(init(applied), WIN);  // only once: then the box's size again
  next.store["zq.init-reason"] = "restart";
  const codec = await recoveryPage(2, next.store);
  codec.session(4);
  codec.updateInfo(1328, 658);
  assert.deepEqual(init(codec), {w: 1328, h: 656});
  // A carried restart outranks a resize at load that ends at the box's size.
  next.store["zq.init-reason"] = "restart";
  const resized = await recoveryPage(2, next.store);
  resized.session(4);
  resized.updateInfo(1328, 658);
  resized.listeners.resize.forEach(fn => fn({isTrusted: true}));
  assert.deepEqual(init(resized), {w: 1328, h: 656});
  console.log("INIT reuses the box's size; real resizes, new resolutions and codec changes request what they need.");
}
{
  const page = await recoveryPage(1);  // H.264: a reopened socket may wait for its first frame
  page.session(4);
  page.tick(10000, {sockState: 1, sockMsgs: 0, sockVideoMsgs: 0, sockAgeMs: 30000});
  assert.deepEqual(page.resizes, []);
}
{
  const {window, resizes, session, updateInfo, tick} = await recoveryPage(2);
  const dead = age => ({sockState: 1, sockMsgs: 4, sockVideoMsgs: 0, sockAgeMs: age});  // reports only
  session(4);
  tick(1000, dead(5000));
  assert.deepEqual(resizes, []);  // too early
  tick(1000, {sockState: 1, sockMsgs: 3, sockVideoMsgs: 3, sockAgeMs: 30000});
  assert.deepEqual(resizes, []);  // video arrived earlier: a still screen, not a dead stream
  session(6); tick(1000, dead(9000));
  assert.deepEqual(resizes, []);  // no running session
  session(4); tick(1000, dead(9000));
  assert.deepEqual(resizes, []);  // the session has only run 1 s
  tick(8000, dead(17000));
  assert.deepEqual(resizes, [{w: 1328, h: 656}]);  // a size other than the session's
  updateInfo(1328, 656);  // the box restarted the session at that size
  tick(5000, dead(14000));
  assert.equal(resizes.length, 1);  // 20 s between restarts
  tick(20000, dead(9000));
  assert.deepEqual(resizes, [{w: 1328, h: 656}, {w: 1328, h: 658}]);  // differs again: back to full
  // A reused session of another size: the kick asks for the window's own size,
  // never a mix of the session's width and the window's height.
  const mixed = await recoveryPage(2, {session: JSON.stringify({w: 1200, h: 600})});
  mixed.session(4);
  mixed.tick(9000, dead(9000));
  assert.deepEqual(mixed.resizes, [WIN]);
  tick(30000, dead(9000));
  assert.equal(resizes.length, 2);  // at most twice per page load
  assert.equal(window.__zq.kicks, 2);
  // Recorded 2026-10-22: the box ended the session after the first INIT and the
  // phone came back 13 s later while the (older) socket was still empty: no kick
  // until the new session itself has run 8 s without video.
  const fresh = await recoveryPage(2);
  fresh.session(4); fresh.session(6);
  fresh.tick(13000, {sockState: 1, sockMsgs: 0, sockVideoMsgs: 0, sockAgeMs: 15000});
  fresh.session(4);
  fresh.tick(1000, {sockState: 1, sockMsgs: 0, sockVideoMsgs: 0, sockAgeMs: 16000});
  assert.deepEqual(fresh.resizes, []);  // the session started 1 s ago
  fresh.tick(8000, {sockState: 1, sockMsgs: 0, sockVideoMsgs: 0, sockAgeMs: 24000});
  assert.equal(fresh.resizes.length, 1);
  // Review case: a stale remembered 1200x600, but this page's INIT (sent before
  // session_state arrived) gave the box the window size. The recovery must
  // differ from what the box holds, not from the remembered size.
  const early = await recoveryPage(2, {session: JSON.stringify({w: 1200, h: 600})});
  early.window.__zq.initSize(WIN.w, WIN.h);  // load INIT before session_state: window size
  early.session(4);
  early.tick(9000, {sockState: 1, sockMsgs: 0, sockVideoMsgs: 0, sockAgeMs: 9000});
  assert.deepEqual(early.resizes, [{w: 1328, h: 656}]);
  console.log("No-video recovery restarts a dead session twice at most and leaves still screens alone.");
}
