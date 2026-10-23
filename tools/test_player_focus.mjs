// Exercise cleanup from the strictly patched vendor bundle, without a box.
// Pass the stock firmware archive; no extracted vendor code is stored in Git.
import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import vm from "node:vm";

if (process.argv.length !== 3) throw Error("Usage: node tools/test_player_focus.mjs <firmware.zip>");
const bundle = execFileSync("python3", ["-c", `
import sys, zipfile
sys.path.insert(0, 'tools')
from player_patches import apply, BUNDLE_PATCHES
with zipfile.ZipFile(sys.argv[1]) as archive:
    source = archive.read('usr/z-loader/zjinnova/web/assets/index.0917c25d.js').decode()
print(apply(source, BUNDLE_PATCHES))
`, process.argv[2]], {encoding: "utf8", maxBuffer: 8 * 1024 * 1024});
const cleanup = bundle.match(/function cn\(\)\{[^}]*\}/)?.[0];
assert.ok(cleanup, "vendor cleanup function is present");
const focusStart = bundle.indexOf("function Xn(_)");
const focusEnd = bundle.indexOf("}function ", focusStart);
assert.ok(focusStart >= 0 && focusEnd > focusStart, "explicit focus function is present");
const focusSource = bundle.slice(focusStart, focusEnd + 1);

for (const mode of [0, 1, 2]) {
  for (const connected of [false, true]) {
    const sent = [];
    let closes = 0;
    const socket = {
      onopen() {}, onmessage() {}, onclose() {}, onerror() {},
      close() {
        for (const key of ["onopen", "onmessage", "onclose", "onerror"])
          assert.equal(this[key], null, "handlers cleared before closing");
        closes++;
      },
    };
    const context = vm.createContext({
      __zq: {h264: mode > 0}, n: connected ? socket : null,
      j: {value: "old"}, l: {value: true}, C: {value: true},
      me: {REQUEST_VIDEO_FOCUS: 5}, He: message => sent.push({...message}),
    });
    vm.runInContext(`${focusSource};${cleanup};cn();cn();`, context);
    assert.equal(closes, connected ? 1 : 0, "repeated cleanup closes socket once");
    assert.equal(context.n, null);
    assert.equal(context.j.value, "");
    assert.equal(context.l.value, false);
    assert.equal(context.C.value, false);
    assert.deepEqual(sent, mode === 0 ? [
      {message_id: 5, video_focus_request: 1},
      {message_id: 5, video_focus_request: 1},
    ] : [], `cleanup focus messages for mode ${mode}`);
    sent.length = 0;
    vm.runInContext("Xn(true);Xn(false);", context);
    assert.deepEqual(sent, [
      {message_id: 5, video_focus_request: 0},
      {message_id: 5, video_focus_request: 1},
    ], "explicit acquire and release remain available in every mode");
  }
}
console.log("Compressed cleanup retains focus; JPEG and explicit focus controls preserve stock behavior.");

// Touch mapping (patched ka): clamp to the stream from this page's UPDATE_INFO,
// not window x scale. Recorded 2026-10-22: a reused 2912x1440 session in a
// 1328x657 window at Resolution 1080 (scale 1.64) cut taps off below y = 1080,
// so the CarPlay home button at the bottom of the dock was unreachable.
{
  const ka = bundle.match(/function ka\(e\)\{[\s\S]*?\}\}(?=var Vi=)/)?.[0];
  assert.ok(ka, "touch mapping function is present (ends before the Home component)");
  const map = (bounds, gs = 0) => vm.runInContext(`${ka}; [ka({x: 30, y: 637}), ka({x: 664, y: 328})]`, vm.createContext({
    __zq: {touchBounds: () => bounds}, document: {documentElement: {clientWidth: 1328, clientHeight: 657}},
    en: v => v === Infinity ? 0 : parseInt((v || 0).toFixed(0)), Ue: 1.643835616438356,
    ls: 2912 / 1328, cs: {hor: 0, ver: 0}, gs, ft: {CARLIFE_WIRELESS: 7},
  }));
  const [corner, middle] = map({w: 2912, h: 1440});
  assert.deepEqual([corner.x, corner.y], [66, 1397]);  // the home button is reachable
  assert.deepEqual([middle.x, middle.y], [1456, 719]);
  const [stockCorner] = map(null);  // before UPDATE_INFO: stock window x scale bounds
  assert.equal(stockCorner.y, 1080);  // the clamp the fix removes once the stream size is known
  // CarLife wireless divides by Ue in its own mapping: it keeps stock bounds.
  assert.deepEqual(JSON.stringify(map({w: 2912, h: 1440}, 7)), JSON.stringify(map(null, 7)));
  console.log("Touch mapping reaches the whole stream when it is larger than window x scale.");
}
