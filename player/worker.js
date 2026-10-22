// Low-latency video worker for the zjinnova player.
// Drop-in replacement for the stock web/worker.js: same message protocol
// (InitRenderer, ResetRenderer, Data, ClearBuffer, UpdateFrame), but decodes
// H.264 (Annex B) with WebCodecs instead of only drawing JPEG frames.
//
// Extra messages to the page (handled by the patched bundle):
//   {id: "resync"}  reopen the video socket; a new socket starts with SPS/IDR
//   {id: "stats"}   once per second, shown in the Video settings panel
//   {id: "ws"}      events of a worker-owned video socket (WsOpen/WsSend/WsClose)
//   {id: "wake"}    first frame after a still screen, as an epoch time (touch-to-frame
//                   latency in the Video panel)
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
let hevc = false; // the stream is HEVC (set on its first VPS, reset with the renderer)
let hevcTag = "hvc1"; // "hev1" once the browser refuses hvc1 for Annex B input
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

const stats = {inFrames: 0, inBytes: 0, shown: 0, late: 0, dropped: 0, resyncs: 0, decodeMs: 0, gaps: 0, keys: 0,
  params: 0};
// Video socket facts for the debug overlay, counted for every message (JPEG,
// H.264, HEVC or text alike), since lastInput only moves for compressed video.
const sockInfo = {openedAt: 0, opens: 0, msgs: 0, bytes: 0, lastClose: ""};
// Any video data (JPEG or compressed, worker or page socket), for the overlay;
// the decoder's own lastInput stays compressed-only for its pause/smooth logic.
let lastData = 0;
let lastJpeg = false;

function sendLog(message) {
  self.postMessage(message);
}

function hex(b) {
  return b.toString(16).padStart(2, "0");
}

// RFC 6381 / ISO 14496-15 codec string from an HEVC SPS (Annex B, starting at
// its 2-byte NAL header): hvc1.<space><profile>.<compat>.<tier><level>.<constraints>.
function hevcCodec(data, at) {
  // Unescape the fixed-size profile_tier_level prefix (00 00 03 -> 00 00).
  const b = [];
  let zeros = 0;
  for (let i = at + 2; i < data.length && b.length < 13; i++) {
    if (zeros >= 2 && data[i] === 3) {
      zeros = 0;
      continue;
    }
    zeros = data[i] === 0 ? zeros + 1 : 0;
    b.push(data[i]);
  }
  if (b.length < 13) return "";
  const space = ["", "A", "B", "C"][b[1] >> 6];
  const tier = b[1] & 0x20 ? "H" : "L";
  const profile = b[1] & 0x1f;
  let compat = 0; // general_profile_compatibility_flags, bit order reversed
  for (let i = 0; i < 32; i++) if (b[2 + (i >> 3)] & (0x80 >> (i & 7))) compat |= 1 << i;
  const constraints = b.slice(6, 12);
  while (constraints.length && constraints[constraints.length - 1] === 0) constraints.pop();
  return [`${hevcTag}.${space}${profile}`, (compat >>> 0).toString(16).toUpperCase(), `${tier}${b[12]}`,
    ...constraints.map(c => c.toString(16).toUpperCase())].join(".");
}

// Exp-Golomb reader over an RBSP that still has emulation-prevention bytes.
function bitReader(data, start, end) {
  let pos = start;
  let bit = 0;
  let zeros = 0;
  function u1() {
    if (pos >= end) throw new RangeError("short NAL");
    if (bit === 0) {
      if (zeros >= 2 && data[pos] === 3) {
        pos++; // emulation prevention
        zeros = 0;
        if (pos >= end) throw new RangeError("short NAL");
      }
      zeros = data[pos] === 0 ? zeros + 1 : 0;
    }
    const v = (data[pos] >> (7 - bit)) & 1;
    if (++bit === 8) {
      bit = 0;
      pos++;
    }
    return v;
  }
  function u(n) {
    let v = 0;
    for (let i = 0; i < n; i++) v = v * 2 + u1();
    return v;
  }
  function ue() {
    let lz = 0;
    while (u1() === 0) if (++lz > 31) throw new RangeError("bad ue");
    return (2 ** lz) - 1 + u(lz);
  }
  return {u1, u, ue};
}

// From the SPS: the bit width of frame_num and the colour-plane flag that
// precedes it in slice headers, and whether the encoder may skip frame_num
// values (gapsAllowed 1; -1 unknown). null when the SPS uses scaling lists (not parsed).
function parseSps(data, at, end) {
  const r = bitReader(data, at + 1, end);
  const profile = r.u(8);
  r.u(16); // constraint flags, level
  r.ue(); // sps id
  let separatePlanes = 0;
  if ([100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135].includes(profile)) {
    if (r.ue() === 3) separatePlanes = r.u1(); // chroma_format_idc
    r.ue(); // bit depth luma
    r.ue(); // bit depth chroma
    r.u1(); // qpprime_y_zero_transform_bypass
    if (r.u1()) return null; // seq_scaling_matrix_present
  }
  const frameNumBits = r.ue() + 4;
  let gapsAllowed = -1;
  const pocType = r.ue();
  if (pocType === 0) r.ue();
  if (pocType !== 1) {
    r.ue(); // max_num_ref_frames
    gapsAllowed = r.u1();
  }
  return {frameNumBits, separatePlanes, gapsAllowed};
}

// Reference-frame continuity: frame_num advances by one after each reference
// picture, so a jump means a picture the following ones depend on never
// arrived (the decoder keeps going and smears until the next IDR).
// The control protocol has no key-frame request, so recovery is a socket
// resync: the box restarts the CarPlay screen, which beats a picture smeared
// until the phone's next IDR (CarPlay sends them rarely).
let sps = null;
let prevRefNum = -1;

function checkFrameNum(data, at, end) {
  if (!sps || sps.gapsAllowed !== 0) return false;
  const r = bitReader(data, at + 1, end);
  if (r.ue() !== 0) return false; // not the first slice of the picture
  r.ue(); // slice_type
  r.ue(); // pps id
  if (sps.separatePlanes) r.u(2);
  const num = r.u(sps.frameNumBits);
  const type = data[at] & 0x1f;
  const isRef = (data[at] & 0x60) !== 0;
  if (type !== 5 && prevRefNum >= 0 && num !== prevRefNum && num !== (prevRefNum + 1) % (2 ** sps.frameNumBits)) {
    stats.gaps++;
    sendLog(`frame_num gap: ${prevRefNum} -> ${num}`);
    prevRefNum = -1;
    return true;
  }
  if (type === 5 || isRef) prevRefNum = num;
  return false;
}

// Walks the Annex B NAL headers up to the first slice. Parameter sets precede
// the picture data, so the slice payload (most of the buffer) is never scanned.
// HEVC is recognised by its VPS (40 01), which is not a valid H.264 NAL header.
function inspect(data) {
  let key = false;
  let gap = false;
  let spsCodec = "";
  const n = data.length;
  for (let i = 0; i + 3 < n; i++) {
    if (data[i] !== 0 || data[i + 1] !== 0) continue;
    let at;
    if (data[i + 2] === 1) at = i + 3;
    else if (data[i + 2] === 0 && data[i + 3] === 1 && i + 4 < n) at = i + 4;
    else continue;
    if (data[at] === 0x40 && data[at + 1] === 0x01) hevc = true;
    else if (data[at] === 0x67) hevc = false; // H.264 SPS; a reserved type in HEVC
    if (hevc) {
      const type = (data[at] >> 1) & 0x3f;
      if (type === 33) {
        key = true;
        spsCodec = hevcCodec(data, at);
      } else if (type < 32) {
        if (type >= 16 && type <= 21) key = true; // IRAP: BLA, IDR, CRA
        break; // slice data follows
      }
    } else {
      const type = data[at] & 0x1f;
      if (type === 7 && at + 3 < n) {
        key = true;
        spsCodec = `avc1.${hex(data[at + 1])}${hex(data[at + 2])}${hex(data[at + 3])}`;
        try {
          sps = parseSps(data, at, Math.min(n, at + 64));
        } catch (e) {
          sps = null;
        }
      } else if (type >= 1 && type <= 5) {
        if (type === 5) key = true;
        try {
          gap = checkFrameNum(data, at, Math.min(n, at + 32));
        } catch (e) {
          // header cut short: skip the check for this frame
        }
        break; // slice data follows
      }
    }
    i = at;
  }
  return {key, gap, spsCodec};
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
  prevRefNum = -1; // the next picture used is an IDR
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
      } else if (e.name === "NotSupportedError" && hevcTag === "hvc1" && codec.startsWith("hvc1.")) {
        hevcTag = "hev1"; // the next SPS builds an hev1 string
        sendLog("falling back to hev1");
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

function decodeH264(data, now) {
  stats.inFrames++;
  if (lastInput && now - lastInput > PAUSE_GAP) {
    self.postMessage({id: "wake", t: performance.timeOrigin + now});
    // Still screen: frames the decoder held during the pause are not a backlog.
    arrivals.clear();
    softSince = 0;
    lastProgress = now; // nor is input a decoder kept until more arrived
  } else if (lastInput && SMOOTH) {
    updateSmoothDelay(now - lastInput);
  }
  lastInput = now;

  const {key, gap, spsCodec} = inspect(data);
  if (key) stats.keys++;
  if (spsCodec) stats.params++;
  if (spsCodec && spsCodec !== codec) openDecoder(spsCodec);
  // A reference picture never arrived: hold the last good picture until the
  // resync's fresh IDR instead of decoding smears.
  if (gap && !waitingForKeyFrame) {
    resync("frame_num gap");
    waitingForKeyFrame = true;
    // Rate-limited (the last resync's IDR already arrived): the key-frame
    // timeout below resyncs again as soon as the limit allows.
    waitingSince = lastResync === now ? now : lastResync + RESYNC_INTERVAL - KEYFRAME_TIMEOUT;
    return;
  }

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
  stats.inBytes += d.length;
  lastData = performance.now();
  lastJpeg = d.length >= 2 && d[0] === 0xff && d[1] === 0xd8;
  if (lastJpeg) drawJPEG(d);
  else decodeH264(d, lastData);
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
  sockInfo.openedAt = performance.now();
  sockInfo.opens++;
  sockInfo.msgs = sockInfo.bytes = 0;
  ws.binaryType = "arraybuffer";
  ws.onopen = () => {
    if (socket === ws) socketNote("open");
  };
  ws.onmessage = e => {
    if (socket !== ws) return;
    sockInfo.msgs++;
    if (typeof e.data === "string") {
      sockInfo.bytes += e.data.length;
      return;
    }
    sockInfo.bytes += e.data.byteLength;
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
  ws.onclose = e => {
    if (socket !== ws) return;
    sockInfo.lastClose = `${e.code}${e.reason ? ` ${e.reason}` : ""}`;
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
    inKbps: Math.round((stats.inBytes * 8) / 1000),
    fps: stats.shown,
    decodeMs: Math.round(stats.decodeMs),
    queue: decoder ? decoder.decodeQueueSize : 0,
    smooth: SMOOTH,
    delayMs: SMOOTH ? Math.round(smoothDelay) : 0,
    late: stats.late,
    resyncs: stats.resyncs,
    gaps: stats.gaps,
    keys: stats.keys,
    waiting: waitingForKeyFrame,
    inputAgeMs: lastData ? Math.round(performance.now() - lastData) : -1, // -1: nothing received yet
    jpeg: lastJpeg,
    sockState: socket ? socket.readyState : -1,
    sockAgeMs: socket ? Math.round(performance.now() - sockInfo.openedAt) : -1,
    sockOpens: sockInfo.opens,
    sockMsgs: sockInfo.msgs,
    sockBytes: sockInfo.bytes,
    lastClose: sockInfo.lastClose,
    params: stats.params,
    socket: !!socket,
    codec,
    size: canvas ? `${canvas.width}x${canvas.height}` : "",
  });
  stats.inFrames = 0;
  stats.inBytes = 0;
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
