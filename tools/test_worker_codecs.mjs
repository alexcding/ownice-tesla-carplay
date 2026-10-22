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

// Every HEVC decoder config refused: flagged for the page instead of looping.
vm.runInContext(`
  var refusals = 0;
  VideoDecoder = class {
    constructor(init) { this.init = init; }
    configure() { const init = this.init; Promise.resolve().then(() =>
      init.error({name: "NotSupportedError", message: "unsupported codec"})); }
    close() {}
  };
  setTimeout = () => 0; clearTimeout = () => {};
`, context);
for (const tag of ["hvc1", "hvc1", "hev1"]) {
  vm.runInContext(`lastResync = -Infinity; openDecoder("${tag}.1.6.L120.B0")`, context);
  await Promise.resolve();
}
assert.equal(vm.runInContext("hevcUnsupported", context), true);
assert.match(vm.runInContext("lastError", context), /NotSupportedError/);
console.log("Refused HEVC configs (tuned, plain hvc1, hev1) flag HEVC as unsupported.");

// Diagnostic native builds report converter events as a UUID-tagged SEI that is never decoded.
{
  const text = "zq config rejected err=ffffe59c";
  const sei = Uint8Array.from([0, 0, 0, 1, 0x4e, 1, 5, 16 + text.length,
    ..."ownice-zq-diag01", ...text].map(c => typeof c === "string" ? c.charCodeAt(0) : c).concat([0x80]));
  context.sei = sei;
  vm.runInContext("decodeCalls = 0; var origDecode = decodeH264; decodeH264 = (...a) => { decodeCalls++; return origDecode(...a); }; handleData(sei)", context);
  assert.equal(vm.runInContext("decodeCalls", context), 0);
  assert.equal(vm.runInContext("stats.nativeReport", context), text);
  context.plain = Uint8Array.from([0, 0, 0, 1, 0x4e, 1, 5, 4, 1, 2, 3, 4, 0x80]);
  assert.equal(vm.runInContext("nativeReport(plain)", context), "");
  console.log("Native converter reports are shown and never decoded; other SEI is untouched.");
}

// Keep feeding an unsupported stream well beyond retry/keyframe deadlines.
// Neither incoming SPS nor timeout recovery may reopen the decoder/socket.
vm.runInContext(`
  var stoppedResyncs = stats.resyncs;
  var stoppedOpens = 0;
  VideoDecoder = class { constructor() { stoppedOpens++; } };
  performance.now = () => 60000;
  for (let i = 0; i < 100; i++) { decodeH264(input, 60000 + i * 4000); resync("timeout", true); }
`, context);
assert.equal(vm.runInContext("stats.resyncs === stoppedResyncs && stoppedOpens === 0 && retryTimer === 0", context), true);

// configure() can also throw synchronously. It must follow the same three
// attempts, then stop. A good output clears the panel's transient error.
vm.runInContext(`
  hevcUnsupported = false; tuned = true; hevcTag = "hvc1";
  VideoDecoder = class {
    constructor(init) { this.init = init; }
    configure() { throw {name: "NotSupportedError", message: "sync refusal"}; }
    close() {}
  };
`, context);
for (const tag of ["hvc1", "hvc1", "hev1"]) {
  vm.runInContext(`lastResync = -Infinity; openDecoder("${tag}.1.6.L120.B0")`, context);
}
assert.equal(vm.runInContext("hevcUnsupported", context), true);
vm.runInContext(`
  hevcUnsupported = false;
  VideoDecoder = class {
    constructor(init) { this.init = init; }
    configure() {} close() {}
  };
  onDecoded = () => {};
  openDecoder("hvc1.1.6.L120.B0");
  decoder.init.output({close() {}});
`, context);
assert.equal(vm.runInContext("lastError", context), "");
console.log("Unsupported streams stay stopped after deadlines; synchronous refusal is bounded; successful output clears errors.");
