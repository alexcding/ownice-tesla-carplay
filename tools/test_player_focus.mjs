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
