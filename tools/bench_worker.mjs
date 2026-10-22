// Micro-benchmark of player/worker.js's per-frame JavaScript, in Node.
// WebCodecs, the canvas and postMessage are stubbed, so this measures only
// our own code (NAL scan, bookkeeping, backlog checks, pacing), not decoding.
//
//   node tools/bench_worker.mjs [--smooth] [--frames N]

import {readFileSync} from "node:fs";
import vm from "node:vm";

const args = process.argv.slice(2);
const SMOOTH = args.includes("--smooth");
const FRAMES = Number(args[args.indexOf("--frames") + 1]) || 20000;
const GOP = 300; // one IDR per 300 frames (CarPlay sends key frames rarely)
const P_SIZE = 40_000;
const IDR_SIZE = 250_000;

// Random payload without start codes (H.264 emulation prevention).
function payload(n, seed) {
  const d = new Uint8Array(n);
  let x = seed;
  for (let i = 0; i < n; i++) {
    x = (x * 1103515245 + 12345) >>> 0;
    d[i] = x >>> 24;
  }
  for (let i = 0; i + 2 < n; i++) {
    if (d[i] === 0 && d[i + 1] === 0 && d[i + 2] <= 3) d[i + 2] = 3;
  }
  return d;
}

function concat(...parts) {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let o = 0;
  for (const p of parts) {
    out.set(p, o);
    o += p.length;
  }
  return out;
}

const SC = [0, 0, 0, 1];
const sps = Uint8Array.from([...SC, 0x67, 0x64, 0x00, 0x33, 0xac, 0x2b, 0x40, 0x3c, 0x01, 0x13, 0xf2]);
const pps = Uint8Array.from([...SC, 0x68, 0xee, 0x3c, 0xb0]);
const idr = concat(sps, pps, Uint8Array.from([...SC, 0x65]), payload(IDR_SIZE, 1));
const pFrames = Array.from({length: 8}, (_, i) => concat(Uint8Array.from([...SC, 0x41]), payload(P_SIZE, i + 2)));

// Virtual clock: frames arrive every 16.7 ms and decode in 4 ms.
let clock = 0;
const timers = [];
let decodedOut = 0;

class VideoDecoder {
  constructor({output, error}) {
    this.output = output;
    this.error = error;
    this.state = "unconfigured";
    this.decodeQueueSize = 0;
    this.ondequeue = null;
  }
  configure() { this.state = "configured"; }
  decode(chunk) {
    this.decodeQueueSize++;
    timers.push({at: clock + 4, fn: () => {
      this.decodeQueueSize--;
      this.ondequeue?.();
      this.output(new VideoFrame(chunk.timestamp));
    }});
  }
  close() { this.state = "closed"; }
}
class EncodedVideoChunk {
  constructor({type, timestamp, data}) {
    this.type = type;
    this.timestamp = timestamp;
    this.byteLength = data.byteLength;
  }
}
class VideoFrame {
  constructor(ts) {
    this.timestamp = ts;
    this.displayWidth = 1920;
    this.displayHeight = 1100;
  }
  close() { decodedOut++; }
}

let listener = null;
let posted = 0;
const logs = [];
const ctx2d = {drawImage() {}, clearRect() {}};
const sandbox = {
  self: {
    location: {search: SMOOTH ? "?smooth=1" : ""},
    postMessage(m) {
      posted++;
      if (typeof m === "string") logs.push(m);
    },
    addEventListener(_t, fn) { listener = fn; },
  },
  performance: {now: () => clock},
  setTimeout(fn, ms) { timers.push({at: clock + ms, fn}); },
  setInterval() {},
  clearTimeout() {},
  URLSearchParams, Map, Math, Blob: class {}, createImageBitmap: async () => ({}),
  VideoDecoder, EncodedVideoChunk, console,
};
vm.createContext(sandbox);
vm.runInContext(readFileSync(new URL("../player/worker.js", import.meta.url), "utf8"), sandbox);

function runTimers() {
  timers.sort((a, b) => a.at - b.at);
  while (timers.length && timers[0].at <= clock) timers.shift().fn();
}

listener({data: {id: "InitRenderer", canvas: {width: 0, height: 0, getContext: () => ctx2d}}});

// Each frame buffer is fresh in real life (transferred), so copy outside the timed region.
const inputs = [];
for (let i = 0; i < FRAMES; i++) inputs.push(i % GOP === 0 ? idr.slice() : pFrames[i % pFrames.length].slice());

let busy = 0n;
let busyIdr = 0n;
let busyP = 0n;
for (let i = 0; i < FRAMES; i++) {
  clock = i * (1000 / 60) + (i % 7) * 1.3; // mild arrival jitter
  const t0 = process.hrtime.bigint();
  listener({data: {id: "Data", data: inputs[i]}});
  runTimers();
  const dt = process.hrtime.bigint() - t0;
  busy += dt;
  if (i % GOP === 0) busyIdr += dt;
  else busyP += dt;
}
const idrCount = Math.ceil(FRAMES / GOP);
console.log(JSON.stringify({
  mode: SMOOTH ? "smooth" : "low-latency",
  frames: FRAMES,
  usPerFrame: +(Number(busy) / FRAMES / 1e3).toFixed(2),
  usPerP: +(Number(busyP) / (FRAMES - idrCount) / 1e3).toFixed(2),
  usPerIdr: +(Number(busyIdr) / idrCount / 1e3).toFixed(2),
  decoded: decodedOut,
  posted,
  log: logs.filter(m => !m.startsWith("renderer")),
}));
