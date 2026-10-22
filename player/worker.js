// Low-latency video worker for the zjinnova player.
// Drop-in replacement for the stock web/worker.js: same message protocol
// (InitRenderer, ResetRenderer, Data, ClearBuffer, UpdateFrame), but decodes
// H.264 (Annex B) with WebCodecs instead of only drawing JPEG frames.
//
// Extra messages to the page (handled by the patched bundle):
//   {id: "resync"}  reopen the video socket; a new socket starts with SPS/IDR
//   {id: "stats"}   once per second, shown in the Video settings panel
//   {id: "ws"}      events of a worker-owned video socket (WsOpen/WsSend/WsClose)
//
// Backlog recovery and smooth pacing follow DiPlay's measurements
// (VideoBacklogRecovery.kt, docs/SMOOTH_WIRELESS.md).

const SMOOTH = new URLSearchParams(self.location.search).get("smooth") === "1";

// Backlog recovery (ms)
const PAUSE_GAP = 120; // input gap that marks a still screen; held frames are not backlog
const CLEAR_AGE = 150; // oldest undecoded frame younger than this: no backlog
const CLEAR_SPAN = 50; // queued frames spanning less than this: nothing to drain
const MIN_SPAN = 100; // span needed before an old frame counts as backlog
const SOFT_AGE = 250; // backlog worth watching
const SOFT_GRACE = 750; // ...that must keep growing this long before we resync
const GROW_SPAN = 100; // "growing": the span rose this much...
const GROW_FRAMES = 4; // ...or the queue this many frames (a draining burst does neither)
const HARD_AGE = 1500; // resync immediately
const STALL = 500; // decoder holds queued input this long without progress
const RESYNC_INTERVAL = 1000; // at most one resync per second
const KEYFRAME_TIMEOUT = 3000; // still no key frame after a resync: try again

// Smooth pacing (ms)
const WINDOW = 120; // arrivals used for the jitter estimate
const MIN_DELAY = 30;
const MAX_DELAY = 200;
const MARGIN = 20;
const MAX_PENDING = 6; // decoded frames waiting to be shown

const RENDER_NOTE_INTERVAL = 250; // ms between "render" notes to the page

let canvas = null;
let ctx = null;
let decoder = null;
let codec = "";
let tuned = true; // cleared once the browser refuses optimizeForLatency/prefer-hardware
let waitingForKeyFrame = true;
let waitingSince = 0;
let timestamp = 0;
let frameDimen = {w: 0, h: 0};

const arrivals = new Map(); // chunk timestamp -> arrival time, insertion ordered
let lastInput = 0;
let lastQueued = 0; // arrival time of the newest chunk sent to the decoder
let lastProgress = 0; // last time the decoder consumed input or produced output
let decoderReady = false; // the current decoder has consumed input (configure is done)
let retryTimer = 0;
let lastRenderNote = -Infinity;
let renderSince = -1; // first frame drawn since the last reset

let socket = null; // video WebSocket, when the page lets the worker own it
let socketId = 0; // the page's id for it
let lastFrameNote = -Infinity;
let softSince = 0;
let softStartSpan = 0;
let softStartQueue = 0;
let lastResync = 0;

const intervals = [];
let smoothDelay = 90;
let smoothCounter = 0; // frames since the last delay re-evaluation
let smoothFrameMs = 1000 / 60; // median frame interval, refreshed with the delay
let lastShow = 0;
let lastShownStamp = -1; // timestamp of the last frame drawn, to keep display order
let pending = 0;

const stats = {inFrames: 0, shown: 0, late: 0, dropped: 0, resyncs: 0, decodeMs: 0};

function sendLog(message) {
  self.postMessage(message);
}

function hex(b) {
  return b.toString(16).padStart(2, "0");
}

// Walks the Annex B NAL headers up to the first slice. Parameter sets precede
// the picture data, so the slice payload (most of the buffer) is never scanned.
function inspect(data) {
  let key = false;
  let spsCodec = "";
  const n = data.length;
  for (let i = 0; i + 3 < n; i++) {
    if (data[i] !== 0 || data[i + 1] !== 0) continue;
    let at;
    if (data[i + 2] === 1) at = i + 3;
    else if (data[i + 2] === 0 && data[i + 3] === 1 && i + 4 < n) at = i + 4;
    else continue;
    const type = data[at] & 0x1f;
    if (type === 7 && at + 3 < n) {
      key = true;
      spsCodec = `avc1.${hex(data[at + 1])}${hex(data[at + 2])}${hex(data[at + 3])}`;
    } else if (type >= 1 && type <= 5) {
      if (type === 5) key = true;
      break; // slice data follows
    }
    i = at;
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
  // The page only needs to know frames are arriving (it flips a "playing"
  // flag), so spare its main thread a task per frame.
  const now = performance.now();
  // Note every frame for the first second after a reset: the page ignores
  // notes until its session reaches STARTED, and a still screen may not send
  // another frame for a while.
  if (now - renderSince < 1000 || now - lastRenderNote >= RENDER_NOTE_INTERVAL) {
    if (renderSince < 0) renderSince = now;
    lastRenderNote = now;
    self.postMessage({id: "render"});
  }
}

function showFrame(frame) {
  // Never draw a frame older than one already shown (a late timer, an
  // immediate-show under backpressure, or a pacing reset can race).
  if (frame.timestamp < lastShownStamp) {
    stats.dropped++;
    frame.close();
    return;
  }
  lastShownStamp = frame.timestamp;
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
  // Re-evaluate every 15 frames via a dedicated counter (intervals.length
  // saturates at WINDOW, a multiple of 15, so it can't gate the cadence).
  // Until 15 intervals exist, keep the frame interval current every frame (cheap while short).
  if (intervals.length >= 15 && ++smoothCounter % 15) return;
  const frameMs = smoothFrameMs = Math.min(1000 / 24, Math.max(1000 / 60, median(intervals)));
  if (intervals.length < 15) return;
  const jitter = intervals.map(v => Math.max(0, v - frameMs));
  const goal = Math.min(MAX_DELAY, Math.max(MIN_DELAY, percentile(jitter, 0.9) + MARGIN));
  // move gradually: up at most 15 ms, down at most 7.5 ms per evaluation (15 frames)
  smoothDelay += Math.max(-7.5, Math.min(15, goal - smoothDelay));
}

function scheduleFrame(frame, arrival) {
  const now = performance.now();
  let at = Math.max(arrival + smoothDelay, lastShow + smoothFrameMs * 0.9);
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
  lastProgress = performance.now();
  decoderReady = true;
  const arrival = arrivals.get(frame.timestamp);
  // Drop this and any earlier chunks that produced no output (e.g. an
  // SPS/PPS-only chunk), so a stale entry can't fake a growing backlog.
  for (const k of arrivals.keys()) {
    if (k <= frame.timestamp) arrivals.delete(k);
    else break;
  }
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
  decoderReady = false;
  lastRenderNote = -Infinity; // tell the page as soon as the picture is back
  renderSince = -1;
  clearTimeout(retryTimer); // a reset or newer resync supersedes a pending retry
  retryTimer = 0;
  waitingForKeyFrame = true;
  waitingSince = performance.now();
}

function openDecoder(newCodec) {
  closeDecoder();
  const d = new VideoDecoder({
    output(frame) {
      if (decoder === d) onDecoded(frame);
      else frame.close(); // late output of a replaced decoder
    },
    error(e) {
      if (decoder !== d) return; // a replaced decoder
      sendLog(`decode error: ${e.name}: ${e.message}`);
      if (e.name === "NotSupportedError" && tuned) {
        tuned = false; // retry without the latency/hardware hints
        sendLog("falling back to a plain decoder config");
      }
      // The browser has already closed it; drop it even if resync() is
      // rate-limited, so the next SPS reopens instead of decoding into a
      // closed decoder.
      closeDecoder();
      resync("decode error", true);
    },
  });
  d.ondequeue = () => {
    lastProgress = performance.now();
    decoderReady = true;
  };
  d.configure(tuned
    ? {codec: newCodec, optimizeForLatency: true, hardwareAcceleration: "prefer-hardware"}
    : {codec: newCodec});
  decoder = d;
  codec = newCodec;
  sendLog(`decoder configured: ${newCodec}${tuned ? "" : " (plain)"}${SMOOTH ? " (smooth)" : ""}`);
}

// Drop everything queued and ask the page for a fresh video socket (new IDR).
function resync(reason, retry = false) {
  const now = performance.now();
  // Rate-limit before closing: a resync we requested <1 s ago is still in
  // flight, so closing again here (without re-requesting) would freeze until
  // the keyframe timeout. Leave the current decoder to be replaced by the
  // pending new socket's SPS.
  if (now - lastResync < RESYNC_INTERVAL) {
    // A decode error has already lost the decoder and the IDR it consumed:
    // retry once the limit allows unless a new SPS has reopened a decoder.
    if (retry && !retryTimer) {
      retryTimer = setTimeout(() => {
        retryTimer = 0;
        if (!decoder) resync(reason);
      }, RESYNC_INTERVAL - (now - lastResync));
    }
    return;
  }
  lastResync = now;
  closeDecoder();
  stats.resyncs++;
  sendLog(`resync: ${reason}`);
  self.postMessage({id: "resync"});
}

function checkBacklog(now) {
  // Input queued but the decoder neither consumed nor produced anything.
  // Skipped until the decoder first consumes input, so a slow hardware
  // configure is not a stall (a hung one still shows up as backlog below).
  if (decoderReady && decoder.decodeQueueSize > 0 && now - lastProgress > STALL) {
    resync(`decoder stalled ${Math.round(now - lastProgress)} ms`);
    return;
  }
  const oldest = arrivals.values().next();
  if (oldest.done) {
    softSince = 0;
    return;
  }
  const age = now - oldest.value;
  // How much stream is queued behind the oldest frame: a delayed burst that
  // is already draining keeps this short and needs no resync.
  const span = lastQueued - oldest.value;
  if (age <= CLEAR_AGE || span <= CLEAR_SPAN) {
    softSince = 0;
    return;
  }
  if (span < MIN_SPAN) return;
  if (age >= HARD_AGE) {
    resync(`backlog ${Math.round(age)} ms, span ${Math.round(span)} ms`);
  } else if (age > SOFT_AGE) {
    if (!softSince) {
      softSince = now;
      softStartSpan = span;
      softStartQueue = arrivals.size;
    } else if (now - softSince > SOFT_GRACE &&
               (span - softStartSpan >= GROW_SPAN || arrivals.size - softStartQueue >= GROW_FRAMES)) {
      resync(`backlog growing: span ${Math.round(softStartSpan)} -> ${Math.round(span)} ms, ` +
             `queue ${softStartQueue} -> ${arrivals.size}`);
    }
  } else {
    softSince = 0; // the grace period covers one continuous episode only
  }
}

function decodeH264(data) {
  const now = performance.now();
  stats.inFrames++;
  if (lastInput && now - lastInput > PAUSE_GAP) {
    // Still screen: frames the decoder held during the pause are not a backlog.
    arrivals.clear();
    softSince = 0;
    lastProgress = now; // nor is input a decoder kept until more arrived
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
  if (decoder.decodeQueueSize === 0) lastProgress = now; // stall clock starts with queued input
  arrivals.set(timestamp, now);
  lastQueued = now;
  decoder.decode(new EncodedVideoChunk({type: key ? "key" : "delta", timestamp, data}));
}

function handleData(d) {
  if (d.length >= 2 && d[0] === 0xff && d[1] === 0xd8) drawJPEG(d);
  else decodeH264(d);
}

// Video socket owned by the worker ("worker" socket mode, see zq-settings.js):
// frames go straight to the decoder instead of through the page's main
// thread. The page only hears open/close/error and a throttled "frame" note.
function socketNote(ev) {
  self.postMessage({id: "ws", sock: socketId, ev});
}

function openSocket(id, url) {
  closeSocket();
  socketId = id;
  lastFrameNote = -Infinity;
  let ws;
  try {
    ws = new WebSocket(url);
  } catch (e) {
    // Report it like a failed connect so the page's stand-in closes and retries.
    sendLog(`video socket: ${e.message}`);
    socketNote("error");
    socketNote("close");
    return;
  }
  socket = ws;
  ws.binaryType = "arraybuffer";
  ws.onopen = () => {
    if (socket === ws) socketNote("open");
  };
  ws.onmessage = e => {
    if (socket !== ws) return;
    handleData(new Uint8Array(e.data));
    const now = performance.now();
    if (now - lastFrameNote >= RENDER_NOTE_INTERVAL) {
      lastFrameNote = now;
      socketNote("frame");
    }
  };
  ws.onerror = () => {
    if (socket === ws) socketNote("error");
  };
  ws.onclose = () => {
    if (socket !== ws) return;
    socket = null;
    socketNote("close");
  };
}

function closeSocket() {
  if (!socket) return;
  socket.onopen = socket.onmessage = socket.onerror = socket.onclose = null;
  socket.close();
  socket = null;
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
    socket: !!socket,
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
    case "Data":
      handleData(msg.data);
      break;
    case "WsOpen":
      openSocket(msg.sock, msg.url);
      break;
    case "WsSend":
      if (socket && msg.sock === socketId && socket.readyState === WebSocket.OPEN) socket.send(msg.data);
      break;
    case "WsClose":
      if (msg.sock === socketId) closeSocket();
      break;
    case "ClearBuffer":
      if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
      closeDecoder();
      break;
    case "UpdateFrame":
      frameDimen = {w: msg.w, h: msg.h};
      break;
  }
});
