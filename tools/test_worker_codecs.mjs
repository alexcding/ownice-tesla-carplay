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

// Keep feeding an unsupported HEVC stream well beyond retry/keyframe deadlines.
// Neither incoming parameter sets nor timeout recovery may reopen the decoder/socket.
context.hevcIrap = Uint8Array.from([...nal(0x40, 1, 0xaa), ...hevcSps, ...nal(0x26, 1, 0x80)]);
context.hevcDelta = Uint8Array.from(nal(2, 1, 0xff));
vm.runInContext(`
  var stoppedResyncs = stats.resyncs;
  var stoppedOpens = 0;
  VideoDecoder = class { constructor(init) { stoppedOpens++; this.init = init; } configure() {} close() {} };
  performance.now = () => 60000;
  for (let i = 0; i < 100; i++) {
    decodeH264(i % 10 ? hevcDelta : hevcIrap, 60000 + i * 4000); resync("timeout", true);
  }
`, context);
assert.equal(vm.runInContext("stats.resyncs === stoppedResyncs && stoppedOpens === 0 && retryTimer === 0", context), true);
// The phone reconnects with H.264 (the page fell back): it must decode, with
// the latency/hardware hints back on despite the HEVC refusals.
// The phone's SPS may use any nal_ref_idc: 0x27 must also leave HEVC mode.
context.avcIdr = Uint8Array.from([...sps.slice(0, 4), 0x27, ...sps.slice(5), ...avcSlice(0, true)]);
vm.runInContext(`var avcTuned = null, avcChunks = 0; EncodedVideoChunk = class {};
  VideoDecoder = class { constructor(init) { stoppedOpens++; this.init = init; this.decodeQueueSize = 0; }
  configure(c) { avcTuned = !!c.optimizeForLatency; } decode() { avcChunks++; } close() {} }; decodeH264(avcIdr, 200000);`, context);
assert.equal(vm.runInContext("avcChunks", context), 1);
assert.equal(vm.runInContext("stoppedOpens === 1 && /^avc1\\./.test(codec) && avcTuned", context), true);
console.log("Unsupported HEVC stays stopped; a later H.264 stream decodes with the tuned config.");

// Refusals only count in a row: decoded output in between resets the count,
// so occasional refusals (e.g. new sizes after resizes) never ban HEVC.
vm.runInContext(`
  hevcUnsupported = false; tuned = true; hevcTag = "hvc1"; refusals = 0; refusedFamily = "";
  var refuseNext = true;
  VideoDecoder = class {
    constructor(init) { this.init = init; }
    configure() { if (refuseNext) throw {name: "NotSupportedError", message: "refused"}; }
    close() {}
  };
  onDecoded = frame => {};
  for (let round = 0; round < 5; round++) {
    refuseNext = true; lastResync = -Infinity; openDecoder("hvc1.1.6.L120.B0");
    lastResync = -Infinity; openDecoder(hevcTag + ".1.6.L120.B0");
    refuseNext = false; lastResync = -Infinity; openDecoder(hevcTag + ".1.6.L120.B0");
    decoder.init.output({close() {}});
  }
`, context);
assert.equal(vm.runInContext("hevcUnsupported", context), false);
assert.equal(vm.runInContext("refusals", context), 0);
console.log("Ten refusals spread over five successful sessions do not ban HEVC.");

// configure() can also throw synchronously. It must follow the same three
// attempts, then stop. A good output clears the panel's transient error.
vm.runInContext(`
  hevcUnsupported = false; tuned = true; hevcTag = "hvc1"; refusals = 0; refusedFamily = "";
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

// Any synchronous configure exception is a refusal too, so a TypeError cannot
// loop forever; a swallowed report is not announced to the page as a frame.
vm.runInContext(`
  hevcUnsupported = false; tuned = true; hevcTag = "hvc1"; refusals = 0; refusedFamily = "";
  VideoDecoder = class { constructor(init) { this.init = init; } configure() { throw new TypeError("bad config"); } close() {} };
  for (let i = 0; i < 6; i++) { lastResync = -Infinity; openDecoder(hevcTag + ".1.6.L120.B0"); }
`, context);
assert.equal(vm.runInContext("hevcUnsupported", context), true);
assert.equal(vm.runInContext("handleData(sei)", context), false);
assert.equal(vm.runInContext("handleData(Uint8Array.from([0xff, 0xd8, 0xff, 0xd9]))", context), true);
console.log("Synchronous configure exceptions are bounded; reports are not frames.");

// H.264 has no page fallback: once both of its configs are refused in a row it
// rests, then retries from the tuned config with a fresh socket, never staying black.
vm.runInContext(`
  var timers = [], retried = [];
  setTimeout = (fn, ms) => { timers.push([fn, ms]); return timers.length; };
  hevcUnsupported = false; avcUnsupported = false; tuned = true; hevcTag = "hvc1"; refusals = 0; refusedFamily = "";
  VideoDecoder = class { constructor(init) { this.init = init; } configure() { throw {name: "NotSupportedError", message: "busy"}; } close() {} };
  self.postMessage = m => { if (m && m.id === "resync") retried.push(m); };
  lastResync = -Infinity; openDecoder("avc1.640020");
  var afterFirst = tuned;
  lastResync = -Infinity; openDecoder("avc1.640020");
`, context);
assert.equal(vm.runInContext("afterFirst", context), false);  // tuned -> plain
assert.equal(vm.runInContext("avcUnsupported", context), true);  // both refused: resting
assert.equal(vm.runInContext("timers.filter(t => t[1] === AVC_RETRY_AFTER).length", context), 1);
vm.runInContext(`retried.length = 0; lastResync = -Infinity; resync("timeout");`, context);
assert.equal(vm.runInContext("retried.length", context), 0);  // no resync loop while resting
vm.runInContext(`lastResync = -Infinity; timers.find(t => t[1] === AVC_RETRY_AFTER)[0]();`, context);
assert.equal(vm.runInContext("avcUnsupported === false && tuned && refusals === 0 && retried.length === 1", context), true);
console.log("Refused H.264 rests and retries from the tuned config instead of staying black.");
