// Regression: main's AVC frame_num recovery must not inspect HEVC slice headers.
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const context = vm.createContext({
  self: {location: {search: ""}, addEventListener() {}, postMessage() {}},
  performance: {now: () => 0}, setInterval() {}, URLSearchParams,
});
vm.runInContext(readFileSync(new URL("../player/worker.js", import.meta.url), "utf8"), context);
vm.runInContext(`
  let avcChecks = 0;
  const originalCheck = checkFrameNum;
  checkFrameNum = (...args) => { avcChecks++; return originalCheck(...args); };
`, context);

function bits() {
  let s = "";
  return {
    u(n, v) { s += v.toString(2).padStart(n, "0"); return this; },
    ue(v) { const b = (v + 1).toString(2); s += "0".repeat(b.length - 1) + b; return this; },
    bytes() { s += "1"; s = s.padEnd(Math.ceil(s.length / 8) * 8, "0");
      return [...s.match(/.{8}/g)].map(b => parseInt(b, 2)); },
  };
}
function nal(...data) { return [0, 0, 0, 1, ...data]; }
function inspect(...parts) {
  context.input = Uint8Array.from(parts.flat());
  return vm.runInContext("inspect(input)", context);
}
const sps = nal(0x67, ...bits().u(8, 66).u(8, 0).u(8, 30).ue(0).ue(0).ue(0).ue(0).ue(1).u(1, 0).bytes());
function avcSlice(num, idr = false) {
  return nal(idr ? 0x65 : 0x41, ...bits().ue(0).ue(idr ? 2 : 0).ue(0).u(4, num).bytes());
}
assert.equal(inspect(sps, avcSlice(0, true)).gap, false);
assert.equal(inspect(avcSlice(1)).gap, false);
assert.equal(inspect(avcSlice(3)).gap, true);
assert.equal(vm.runInContext("stats.gaps", context), 1);

const checksBeforeHevc = vm.runInContext("avcChecks", context);
const hevcSps = nal(0x42, 1, 1, 1, 0x60, 0, 0, 0, 0xb0, 0, 0, 0, 0, 0, 123);
const result = inspect(nal(0x40, 1, 0xaa), hevcSps, nal(0x26, 1, 0x80));
assert.equal(result.gap, false);
assert.equal(result.key, true);
assert.match(result.spsCodec, /^hvc1\./);
assert.equal(inspect(nal(2, 1, 0xff)).gap, false);
assert.equal(vm.runInContext("avcChecks", context), checksBeforeHevc);
assert.equal(inspect(sps, avcSlice(0, true)).gap, false);
assert.equal(inspect(avcSlice(1)).gap, false);
assert.equal(vm.runInContext("hevc", context), false);
assert.equal(vm.runInContext("stats.gaps", context), 1);
assert.equal(vm.runInContext("stats.inBytes", context), 0);
console.log("AVC gaps detected; HEVC bypasses AVC checks; AVC fallback and both stats sets preserved.");
