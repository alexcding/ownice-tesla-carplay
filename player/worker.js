// Low-latency video worker for the zjinnova player.
// Drop-in replacement for the stock web/worker.js: same message protocol
// (InitRenderer, ResetRenderer, Data, ClearBuffer, UpdateFrame), but decodes
// H.264 (Annex B) with WebCodecs instead of only drawing JPEG frames.
//
// Extra messages to the page (handled by the patched bundle):
//   {id: "resync"}  reopen the video socket; a new socket starts with SPS/IDR
//   {id: "stats"}   once per second, shown in the Video settings panel
//
// Backlog recovery and smooth pacing follow DiPlay's measurements
// (VideoBacklogRecovery.kt, docs/SMOOTH_WIRELESS.md).

const SMOOTH = new URLSearchParams(self.location.search).get("smooth") === "1";

// Backlog recovery (ms)
const PAUSE_GAP = 120; // input gap that marks a still screen; held frames are not backlog
const SOFT_AGE = 250; // backlog worth watching
const SOFT_GRACE = 750; // ...that must keep growing this long before we resync
const HARD_AGE = 1500; // resync immediately
const RESYNC_INTERVAL = 1000; // at most one resync per second
const KEYFRAME_TIMEOUT = 3000; // still no key frame after a resync: try again

// Smooth pacing (ms)
const WINDOW = 120; // arrivals used for the jitter estimate
const MIN_DELAY = 30;
const MAX_DELAY = 200;
const MARGIN = 20;
const MAX_PENDING = 6; // decoded frames waiting to be shown

let canvas = null;
let ctx = null;
let decoder = null;
let codec = "";
let waitingForKeyFrame = true;
let waitingSince = 0;
let timestamp = 0;
let frameDimen = {w: 0, h: 0};

const arrivals = new Map(); // chunk timestamp -> arrival time, insertion ordered
let lastInput = 0;
let softSince = 0;
let softStartAge = 0;
let lastResync = 0;

const intervals = [];
let smoothDelay = 90;
let lastShow = 0;
let pending = 0;

const stats = {inFrames: 0, shown: 0, late: 0, dropped: 0, resyncs: 0, decodeMs: 0};

function sendLog(message) {
  self.postMessage(message);
}

// Yields [nalType, payloadOffset] for each NAL unit in an Annex B buffer.
function* nalUnits(data) {
  for (let i = 0; i + 3 < data.length; i++) {
    if (data[i] === 0 && data[i + 1] === 0) {
      if (data[i + 2] === 1) {
        yield [data[i + 3] & 0x1f, i + 3];
        i += 2;
      } else if (data[i + 2] === 0 && data[i + 3] === 1 && i + 4 < data.length) {
        yield [data[i + 4] & 0x1f, i + 4];
        i += 3;
      }
    }
  }
}

function hex(b) {
  return b.toString(16).padStart(2, "0");
}

function inspect(data) {
  let key = false;
  let spsCodec = "";
  for (const [type, at] of nalUnits(data)) {
    if (type === 5) key = true;
    if (type === 7 && at + 3 < data.length) {
      key = true;
      spsCodec = `avc1.${hex(data[at + 1])}${hex(data[at + 2])}${hex(data[at + 3])}`;
    }
    if (type === 1) break; // slice data follows; no parameter sets after this
  }
  return {key, spsCodec};
}

function draw(source, w, h) {
  if (!ctx) return;
  const cw = frameDimen.w || w;
  const ch = frameDimen.h || h;
  if (canvas.width !== cw || canvas.height !== ch) {
    canvas.width = cw;
    canvas.height = ch;
  }
  ctx.drawImage(source, 0, 0, cw, ch, 0, 0, cw, ch);
  stats.shown++;
  self.postMessage({id: "render"});
}

function showFrame(frame) {
  draw(frame, frame.displayWidth, frame.displayHeight);
  frame.close();
}

function median(values) {
  const s = [...values].sort((a, b) => a - b);
  return s[s.length >> 1];
}

function percentile(values, p) {
  const s = [...values].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.floor(s.length * p))];
}

// Smooth mode: show each frame at arrival + adaptive delay, spaced at least
// one frame interval apart, instead of as soon as it is decoded.
function updateSmoothDelay(interval) {
  intervals.push(interval);
  if (intervals.length > WINDOW) intervals.shift();
  if (intervals.length < 15 || intervals.length % 15) return;
  const frameMs = Math.min(1000 / 24, Math.max(1000 / 60, median(intervals)));
  const jitter = intervals.map(v => Math.max(0, v - frameMs));
  const goal = Math.min(MAX_DELAY, Math.max(MIN_DELAY, percentile(jitter, 0.9) + MARGIN));
  // move gradually: up at most 15 ms, down at most 7.5 ms per evaluation (15 frames)
  smoothDelay += Math.max(-7.5, Math.min(15, goal - smoothDelay));
}

function scheduleFrame(frame, arrival) {
  const now = performance.now();
  const frameMs = intervals.length ? Math.min(1000 / 24, Math.max(1000 / 60, median(intervals))) : 1000 / 60;
  let at = Math.max(arrival + smoothDelay, lastShow + frameMs * 0.9);
  if (at - now > MAX_DELAY) at = now + smoothDelay; // never let pacing build up latency
  if (at <= now || pending >= MAX_PENDING) {
    if (at <= now) stats.late++;
    lastShow = now;
    showFrame(frame);
    return;
  }
  lastShow = at;
  pending++;
  setTimeout(() => {
    pending--;
    showFrame(frame);
  }, at - now);
}

function onDecoded(frame) {
  const arrival = arrivals.get(frame.timestamp);
  arrivals.delete(frame.timestamp);
  if (arrival !== undefined) {
    stats.decodeMs = stats.decodeMs * 0.9 + (performance.now() - arrival) * 0.1;
  }
  if (SMOOTH && arrival !== undefined) scheduleFrame(frame, arrival);
  else showFrame(frame);
}

function closeDecoder() {
  if (decoder && decoder.state !== "closed") decoder.close();
  decoder = null;
  codec = "";
  arrivals.clear();
  softSince = 0;
  waitingForKeyFrame = true;
  waitingSince = performance.now();
}

function openDecoder(newCodec) {
  closeDecoder();
  decoder = new VideoDecoder({
    output: onDecoded,
    error(e) {
      sendLog(`decode error: ${e.message}`);
      resync("decode error");
    },
  });
  decoder.configure({
    codec: newCodec,
    optimizeForLatency: true,
    hardwareAcceleration: "prefer-hardware",
  });
  codec = newCodec;
  sendLog(`decoder configured: ${newCodec}${SMOOTH ? " (smooth)" : ""}`);
}

// Drop everything queued and ask the page for a fresh video socket (new IDR).
function resync(reason) {
  const now = performance.now();
  closeDecoder();
  if (now - lastResync < RESYNC_INTERVAL) return;
  lastResync = now;
  stats.resyncs++;
  sendLog(`resync: ${reason}`);
  self.postMessage({id: "resync"});
}

function checkBacklog(now) {
  const oldest = arrivals.values().next();
  if (oldest.done) {
    softSince = 0;
    return;
  }
  const age = now - oldest.value;
  if (age >= HARD_AGE) {
    resync(`backlog ${Math.round(age)} ms`);
  } else if (age > SOFT_AGE) {
    if (!softSince) {
      softSince = now;
      softStartAge = age;
    } else if (now - softSince > SOFT_GRACE && age > softStartAge) {
      resync(`backlog growing ${Math.round(softStartAge)} -> ${Math.round(age)} ms`);
    }
  } else {
    softSince = 0;
  }
}

function decodeH264(data) {
  const now = performance.now();
  stats.inFrames++;
  if (lastInput && now - lastInput > PAUSE_GAP) {
    // Still screen: frames the decoder held during the pause are not a backlog.
    arrivals.clear();
    softSince = 0;
  } else if (lastInput && SMOOTH) {
    updateSmoothDelay(now - lastInput);
  }
  lastInput = now;

  const {key, spsCodec} = inspect(data);
  if (spsCodec && spsCodec !== codec) openDecoder(spsCodec);

  if (waitingForKeyFrame) {
    if (!key || !decoder) {
      if (waitingSince && now - waitingSince > KEYFRAME_TIMEOUT) {
        waitingSince = now;
        resync("no key frame");
      }
      return;
    }
    waitingForKeyFrame = false;
  }
  if (!decoder) return;

  checkBacklog(now);
  if (!decoder) return; // resync closed it

  // CarPlay sends key frames rarely, so deltas are never dropped individually:
  // recovery always goes through a resync to a fresh IDR.
  timestamp += 1;
  arrivals.set(timestamp, now);
  decoder.decode(new EncodedVideoChunk({type: key ? "key" : "delta", timestamp, data}));
}

async function drawJPEG(data) {
  try {
    const bitmap = await createImageBitmap(new Blob([data], {type: "image/jpeg"}));
    draw(bitmap, bitmap.width, bitmap.height);
    bitmap.close();
  } catch (e) {
    sendLog(`jpeg error: ${e.message}`);
  }
}

setInterval(() => {
  self.postMessage({
    id: "stats",
    inFps: stats.inFrames,
    fps: stats.shown,
    decodeMs: Math.round(stats.decodeMs),
    queue: decoder ? decoder.decodeQueueSize : 0,
    smooth: SMOOTH,
    delayMs: SMOOTH ? Math.round(smoothDelay) : 0,
    late: stats.late,
    resyncs: stats.resyncs,
    codec,
    size: canvas ? `${canvas.width}x${canvas.height}` : "",
  });
  stats.inFrames = 0;
  stats.shown = 0;
}, 1000);

self.addEventListener("message", ({data: msg}) => {
  switch (msg.id) {
    case "InitRenderer":
      canvas = msg.canvas;
      ctx = canvas.getContext("2d", {alpha: false, desynchronized: true});
      closeDecoder();
      sendLog("renderer ready");
      break;
    case "ResetRenderer":
      closeDecoder();
      break;
    case "Data": {
      const d = msg.data;
      if (d.length >= 2 && d[0] === 0xff && d[1] === 0xd8) drawJPEG(d);
      else decodeH264(d);
      break;
    }
    case "ClearBuffer":
      if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
      closeDecoder();
      break;
    case "UpdateFrame":
      frameDimen = {w: msg.w, h: msg.h};
      break;
  }
});
