// In-car video settings for the zjinnova player.
// Loaded by player.html before the vendor bundle; the patched bundle reads
// window.__zq for the stream size, frame rate and codec it requests.
// The "Video" button appears on the box's home screen (Tesla house icon).

(function () {
  if (window.__zqLoaded) return; // idempotent: tolerate a double <script> include
  window.__zqLoaded = true;

  const PROJECT = "ownice-tesla-carplay";
  const PROJECT_URL = "github.com/alexcding/ownice-tesla-carplay";
  const KEY = "zq.settings";
  const NATIVE_HEVC = false; // set by the opt-in native package builder
  const DEV_PROXY = false; // set by tools/dev_player.py: debug overlay always on
  // vmode is the video mode sent to the box as is_h264_mode: 0 JPEG (local decode),
  // 1 H.264 passthrough, 2 HEVC passthrough. The box treats any non-zero value as
  // passthrough; the codec is decided by what the native receiver advertises (2 vs 1).
  const DEFAULTS = {res: "auto", fps: 60, vmode: 1, smooth: false, sock: "worker", touch: "raw", debug: false};
  const AUTO_MAX_HEIGHT = 1200; // Model 3/Y screen height; S/X browser windows are smaller than 1300
  // Auto HD: the window size scaled up (by devicePixelRatio, at least 1.5x)
  // within DiPlay's ~3.7 MP budget for 50+ fps.
  const HD_MIN_SCALE = 1.5;
  const HD_MAX_PIXELS = 3.7e6;
  const HD_MAX_HEIGHT = 1800;
  const RESOLUTIONS = [
    ["auto", "Auto (screen)"],
    ["hd", "Auto HD"],
    ["540", "540p"],
    ["720", "720p"],
    ["900", "900p"],
    ["1080", "1080p"],
    ["1440", "1440p"],
  ];

  function load() {
    try {
      const saved = JSON.parse(localStorage.getItem(KEY) || "{}");
      const value = saved && typeof saved === "object" ? saved : {};
      const vmode = value.vmode === undefined && typeof value.h264 === "boolean"
        ? (value.h264 ? 1 : 0) : (value.vmode ?? DEFAULTS.vmode);
      return {...DEFAULTS, ...value, vmode};
    } catch (e) {
      return {...DEFAULTS};
    }
  }

  function save(s) {
    try {
      localStorage.setItem(KEY, JSON.stringify(s));
    } catch (e) {
      // storage blocked: settings only last for this page load
    }
  }

  const settings = load();
  // Reject malformed stored modes; HEVC needs the matching native package.
  if (![0, 1, 2].includes(settings.vmode) || (!NATIVE_HEVC && settings.vmode === 2)) settings.vmode = 1;
  delete settings.h264;

  // Probe before the module sends INIT. Do not persist a decoder refusal as
  // a browser-wide blacklist: resource exhaustion can be temporary.
  const HEVC_PROBES = ["hvc1.1.6.L120.B0", "hev1.1.6.L120.B0"];
  const hevcSupport = {ok: null};
  const fallbackURL = new URL(location.href);
  const forcedH264 = fallbackURL.searchParams.get("zq-h264") === "1";
  let hevcBlocked = forcedH264 && settings.vmode === 2;
  let hevcFailedBy = "";
  function hevcFailed(by) {
    if (hevcFailedBy) return;
    hevcFailedBy = by;
    hevcSupport.ok = false;
    hevcBlocked = true;
    if (settings.vmode !== 2 || window.__zq.mode !== 2) return;
    // Session-only fallback: a URL flag, never the saved preference, so a new
    // browser session probes HEVC again and selecting HEVC clears the flag.
    fallbackURL.searchParams.set("zq-h264", "1");
    location.replace(fallbackURL.href);
  }
  const hevcReady = (async () => {
    await Promise.resolve(); // __zq is published before a synchronous probe result
    if (!NATIVE_HEVC || settings.vmode !== 2 || forcedH264) return;
    const decoder = window.VideoDecoder;
    if (!decoder) hevcSupport.ok = false;
    else if (decoder.isConfigSupported) {
      let timer;
      try {
        const results = await Promise.race([
          Promise.all(HEVC_PROBES.map(codec => Promise.resolve().then(() =>
            decoder.isConfigSupported({codec})).then(r => !!r.supported, () => null))),
          new Promise(resolve => { timer = setTimeout(() => resolve([null]), 1500); }),
        ]);
        // A failed API call or timeout is inconclusive, not lack of support.
        hevcSupport.ok = results.some(r => r === true) ? true : results.every(r => r === false) ? false : null;
      } finally { clearTimeout(timer); }
    }
    if (hevcSupport.ok === false) {
      hevcBlocked = true;
      window.__zq.mode = 1;
    }
  })();
  if (DEV_PROXY) settings.debug = true;

  // Read the control socket's session messages (the box's view of the phone
  // session) for the debug overlay and the no-video recovery below. One
  // listener on that socket; video is untouched.
  const ctrl = {state: null, phone: null, boxMode: null, at: 0};
  if (window.WebSocket) {
    const NativeWebSocket = window.WebSocket;
    window.WebSocket = class extends NativeWebSocket {
      constructor(url, protocols) {
        super(url, protocols);
        if (!/:(9002|9112)(\/|\?|$)/.test(String(url))) return;
        this.addEventListener("message", e => {
          try {
            const m = JSON.parse(e.data);
            if (m.message_id === 11) {
              ctrl.state = m.session_state;
              ctrl.phone = m.phone_type;
            }
            if ("is_h264_mode" in m) ctrl.boxMode = m.is_h264_mode;
            ctrl.at = performance.now();
          } catch (err) {
            // not a JSON control message
          }
        });
      }
    };
  }
  const hd = settings.res === "hd";
  const target = settings.res === "auto" || hd ? 0 : parseInt(settings.res, 10);

  // The patched bundle opens the video socket through videoSocket(). In
  // "worker" mode the socket lives in worker.js, so frames go straight to the
  // decoder instead of through this (busy) main thread; the bundle gets a
  // WebSocket-like stand-in driven by {id: "ws"} notes from the worker.
  const sockets = new Map(); // id -> stand-in
  let nextSocket = 0;

  function videoSocket(url, worker) {
    if (worker && !worker.zqWake) {
      worker.zqWake = true;
      worker.addEventListener("message", onWorkerNote);
    }
    if (settings.sock !== "worker" || !worker) return new WebSocket(url);
    const id = ++nextSocket;
    const sock = {
      readyState: 0, // WebSocket.CONNECTING
      binaryType: "arraybuffer",
      worker,
      onopen: null,
      onmessage: null,
      onclose: null,
      onerror: null,
      send(data) {
        if (sock.readyState === 1) worker.postMessage({id: "WsSend", sock: id, data});
      },
      close() {
        if (sock.readyState >= 2) return;
        sock.readyState = 3;
        sockets.delete(id);
        worker.postMessage({id: "WsClose", sock: id});
      },
    };
    sockets.set(id, sock);
    worker.postMessage({id: "WsOpen", sock: id, url});
    return sock;
  }

  function onWorkerSocket({sock: id, ev}) {
    const sock = sockets.get(id);
    if (!sock) return; // closed by the page already
    if (ev === "open") {
      sock.readyState = 1;
      if (sock.onopen) sock.onopen({});
    } else if (ev === "frame") {
      if (sock.onmessage) sock.onmessage({zq: true}); // frames arriving; data stays in the worker
    } else if (ev === "error") {
      if (sock.onerror) sock.onerror({});
    } else if (ev === "close") {
      sock.readyState = 3;
      sockets.delete(id);
      if (sock.onclose) sock.onclose({});
    }
  }

  // The bundle is terminating a worker: its sockets die with it.
  function workerGone(worker) {
    for (const [id, sock] of sockets) {
      if (sock.worker !== worker) continue;
      sock.readyState = 3;
      sockets.delete(id);
      if (sock.onclose) sock.onclose({});
    }
  }

  // Raw touch: Chrome holds touchmove back until the next animation frame, so
  // the bundle's drag handler runs up to a frame late. pointerrawupdate fires
  // as soon as the input arrives. Moves of the finger that went down on the
  // player go out from here through the bundle's own mapping and sender
  // (touchSender); down/up stay on the stock handlers. A move that maps to the
  // point already sent is skipped.
  const rawTouch = settings.touch === "raw";
  const MOVE_EVENT = "onpointerrawupdate" in window ? "pointerrawupdate" : "pointermove";
  const TOUCH_SCREEN = "ontouchstart" in window || navigator.maxTouchPoints > 0; // the bundle ignores mice here
  const touchStats = {event: rawTouch ? MOVE_EVENT : "touchmove", moves: 0, sent: 0, delay: 0, since: 0};
  // Touch-to-frame: from a touch-down to the first video frame after a still
  // screen (worker "wake" note). The phone round trip, without decode/display.
  const WAKE_WINDOW = 1000;
  const wake = {down: 0, last: 0, samples: []};
  let touchMap = null;
  let touchSend = null;
  let touchPointer = null;
  let lastTouch = "";

  function touchSender(map, send) {
    touchMap = map;
    touchSend = send;
  }

  function countMove(e) {
    touchStats.moves++;
    touchStats.delay += Math.max(0, performance.now() - e.timeStamp);
  }

  function onPointerDown(e) {
    const t = e.target;
    if (!e.isPrimary || !t.closest || !t.closest(".player-box") || t.closest("#zq-btn, #zq-panel")) return;
    wake.down = performance.timeOrigin + performance.now();
    if (!rawTouch) return; // stock path: only measured
    if (e.pointerType === "mouse" && (TOUCH_SCREEN || e.button !== 0)) return;
    touchPointer = e.pointerId;
    lastTouch = "";
  }

  function onPointerEnd(e) {
    if (e.pointerId === touchPointer) touchPointer = null;
  }

  function onPointerMove(e) {
    if (e.pointerId !== touchPointer || !touchMap) return;
    countMove(e);
    const {x, y} = touchMap({clientX: e.clientX, clientY: e.clientY});
    const key = `${x},${y}`;
    if (key === lastTouch) return;
    lastTouch = key;
    touchStats.sent++;
    touchSend(x, y);
  }

  function onWorkerNote(e) {
    const d = e.data;
    if (!d || d.id !== "wake" || !wake.down) return;
    const ms = d.t - wake.down;
    wake.down = 0;
    if (ms < 0 || ms > WAKE_WINDOW) return;
    wake.last = ms;
    wake.samples.push(ms);
    if (wake.samples.length > 20) wake.samples.shift();
  }

  window.addEventListener("pointerdown", onPointerDown, true);
  if (rawTouch) {
    window.addEventListener(MOVE_EVENT, onPointerMove, true);
    window.addEventListener("pointerup", onPointerEnd, true);
    window.addEventListener("pointercancel", onPointerEnd, true);
  } else {
    // stock path: only measured, for comparison
    window.addEventListener("touchmove", countMove, {capture: true, passive: true});
  }

  // "Touch …" panel line; counters cover the time since the previous call.
  function touchLine() {
    const now = performance.now();
    const s = touchStats;
    const secs = s.since ? (now - s.since) / 1000 : 0;
    const out = !secs || !s.moves ? `Touch ${s.event} · drag to measure`
      : `Touch ${s.event} · ${Math.round(s.moves / secs)} moves/s` +
        (rawTouch ? ` · ${Math.round(s.sent / secs)} sent/s` : "") +
        ` · input delay ${(s.delay / s.moves).toFixed(1)} ms`;
    Object.assign(s, {moves: 0, sent: 0, delay: 0, since: now});
    return out;
  }

  function wakeLine() {
    if (!wake.samples.length) return "Touch→frame · tap a still screen to measure";
    const avg = wake.samples.reduce((a, b) => a + b, 0) / wake.samples.length;
    return `Touch→frame ${Math.round(wake.last)} ms · avg ${Math.round(avg)} ms (${wake.samples.length})`;
  }

  window.__zq = {
    // multiplier applied by the player to the window size it requests
    get scale() {
      const el = document.documentElement;
      const h = el.clientHeight || window.innerHeight || 720;
      if (target) return target / h;
      if (!hd) return 1;
      const w = el.clientWidth || window.innerWidth || 1280;
      const up = Math.max(window.devicePixelRatio || 1, HD_MIN_SCALE);
      return Math.max(1, Math.min(up, Math.sqrt(HD_MAX_PIXELS / (w * h))));
    },
    maxh: target || (hd ? HD_MAX_HEIGHT : AUTO_MAX_HEIGHT),
    fps: settings.fps,
    hevcReady,
    mode: hevcBlocked ? 1 : settings.vmode,
    h264: settings.vmode > 0, // compat: any non-zero mode is compressed passthrough

    smooth: settings.smooth,
    rawTouch,
    touchStats,
    touchSender,
    wake,
    stats: null, // set once per second by the patched bundle from worker.js
    videoSocket,
    onWorkerSocket,
    workerGone,
    nudge: 0, // px taken off the INIT height (patched bundle) to force a session restart
    kicks: 0,
  };

  // No-video recovery. After the phone has run a session with no player
  // connected for a while (browser closed, adapter booted before the page
  // opened), HEVC never resumes: a new video socket stays open with zero
  // messages and the keyframe request gets no answer. A same-size INIT does
  // not help; an INIT with a different size makes the box restart the CarPlay
  // session, which recovers. So, when a running session's video socket has
  // received nothing at all 8 s after opening, resend INIT 2 px shorter (or
  // back), at most twice per page load, 20 s apart. A still screen after a
  // working start is untouched: it has received messages.
  const KICK_AFTER = 8000;
  const KICK_INTERVAL = 20000;
  const KICK_LIMIT = 2;
  let lastKick = -Infinity;
  setInterval(() => {
    const s = window.__zq.stats;
    const now = performance.now();
    // HEVC only: the blackout has only been seen with HEVC, and an H.264 or JPEG
    // socket reopened on a still screen can legitimately wait for its first frame.
    if (!s || settings.sock !== "worker" || window.__zq.mode !== 2 || ctrl.state !== 4) return;
    // Video messages only: diagnostic reports (e.g. "zq dropped") are not video.
    if (s.sockState !== 1 || s.sockVideoMsgs !== 0 || !(s.sockAgeMs > KICK_AFTER)) return;
    if (window.__zq.kicks >= KICK_LIMIT || now - lastKick < KICK_INTERVAL) return;
    lastKick = now;
    window.__zq.kicks++;
    window.__zq.nudge = window.__zq.nudge ? 0 : 2;
    window.dispatchEvent(new Event("resize")); // the bundle's resize handler resends INIT
  }, 1000);
  // A real resize already changes the size (and restarts the session): request
  // the full height again. Registered before the bundle's own resize handler.
  window.addEventListener("resize", e => { if (e.isTrusted) window.__zq.nudge = 0; });

  const CSS = `
    /* CarPlay gets every touch: no browser pan/zoom gesture detection on the player */
    .player-box{touch-action:none}
    /* match the vendor toolbar buttons (.settings-btn / .refresh-btn): a 30px icon on a gray pill */
    #zq-btn{display:inline-flex;align-items:center;justify-content:center;vertical-align:middle;
      width:86px;height:50px;margin-left:30px;padding:0;border-radius:26px;border:0;cursor:pointer;
      background:#F0F0F0;color:#6d6d6d;box-shadow:0 2px 4px #0003;-webkit-user-select:none;user-select:none}
    #zq-btn svg{width:30px;height:30px;pointer-events:none}
    /* match the vendor settings card: light surface, gray pills, blue accent */
    #zq-panel{position:fixed;top:60px;right:8px;z-index:99999;width:300px;padding:18px;border-radius:20px;
      background:#fff;color:#222;font:16px system-ui,sans-serif;box-shadow:0 6px 24px #0004;display:none;
      box-sizing:border-box;overflow-y:auto;overscroll-behavior:contain}
    #zq-panel.open{display:block}
    #zq-panel h4{margin:16px 0 8px;font-size:13px;font-weight:600;color:#8a8a8e;text-transform:uppercase}
    #zq-panel h4:first-child{margin-top:0}
    #zq-panel .row{display:flex;flex-wrap:wrap;gap:8px}
    #zq-panel button{flex:1 0 auto;min-height:44px;padding:0 14px;border-radius:22px;border:0;cursor:pointer;
      background:#F0F0F0;color:#6d6d6d;font:600 15px system-ui,sans-serif}
    #zq-panel button.on{background:#105bf1;color:#fff}
    #zq-panel .apply{width:100%;margin-top:16px;background:#105bf1;color:#fff}
    #zq-panel .info{margin-top:12px;font-size:12px;color:#999;line-height:1.5;overflow-wrap:anywhere}
    /* Debug overlay over the CarPlay picture: text only, never takes a touch */
    #zq-debug{position:fixed;top:8px;right:8px;z-index:99998;pointer-events:none;padding:6px 8px;
      border-radius:8px;background:#000a;color:#0f0;font:12px/1.35 ui-monospace,monospace;white-space:pre}
  `;

  // Box AP radio settings from the read-only CGIs the vendor settings page uses.
  // Replies are loose JSON (checkband sends `"band": 0000`), so values are
  // picked out with a regex instead of JSON.parse. Fetched when the panel opens.
  let wifi = null;
  let wifiAt = 0;
  const WIFI_TTL = 30000;

  async function cgi(name) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 3000);
    try {
      const r = await fetch(`/cgi-bin/${name}.cgi?t=${Date.now()}`, {cache: "no-store", signal: ctl.signal});
      const text = await r.text();
      const code = /"code":\s*(-?\d+)/.exec(text);
      if (!code || code[1] !== "0") return {};
      const out = {};
      for (const [, k, v] of text.matchAll(/"(\w+)":\s*"([^"]*)"/g)) out[k] = v;
      return out;
    } catch (e) {
      return {};
    } finally {
      clearTimeout(timer);
    }
  }

  async function refreshWifi(done) {
    if (Date.now() - wifiAt < WIFI_TTL) return;
    wifiAt = Date.now();
    const [ap, mode] = await Promise.all([cgi("getapchannel"), cgi("getwifimode")]);
    wifi = {channel: ap.apchannel, country: ap.country, mode: mode.ChannelMode};
    if (!wifi.channel) wifiAt -= WIFI_TTL - 5000; // failed: retry on an open after 5 s
    done();
  }

  function wifiLine() {
    if (!wifi) return "Wi-Fi …";
    if (!wifi.channel) return "Wi-Fi status unavailable";
    const ch = parseInt(wifi.channel, 10);
    const band = ch > 14 ? "5 GHz" : ch > 0 ? "2.4 GHz" : "";
    return [`Wi-Fi ch ${wifi.channel}`, band, wifi.mode, wifi.country].filter(Boolean).join(" · ");
  }

  const HEVC_NOTE = "HEVC is unavailable in this session; using H.264. Reconnect your phone if the picture stays black.";

  // Name the live stream codec for the stats readout, rather than assuming H.264.
  function codecName(codec) {
    if (/^(hvc1|hev1)\./.test(codec || "")) return "HEVC";
    if (/^avc1\./.test(codec || "")) return "H.264";
    return "";
  }

  // Debug overlay text: what the stream is, whether data arrives and whether the
  // decoder has a key frame to start from (the facts a black screen hinges on).
  function debugText() {
    const s = window.__zq.stats;
    const ago = ms => ms == null ? "?" : ms < 0 ? "never" : `${(ms / 1000).toFixed(1)} s ago`;
    // Raw control-message fields; their vendor meaning is not established.
    const box = `session_state ${ctrl.state ?? "?"} · phone ${ctrl.phone ?? "?"} · box mode ${ctrl.boxMode ?? "?"}` +
      ` · ctrl msg ${ago(ctrl.at ? Math.round(performance.now() - ctrl.at) : -1)}`;
    if (!s) return `zq debug: no worker stats yet\n${box}`;
    const open = s.sockAgeMs == null || s.sockAgeMs < 0 ? "" : ` ${Math.round(s.sockAgeMs / 1000)} s`;
    const kb = s.sockBytes == null ? "?" : Math.round(s.sockBytes / 1024);
    // In page socket mode the worker never sees the socket, so its counters stay 0.
    const sockLine = settings.sock !== "worker" ? "video socket page (counters need Worker mode)"
      : `video socket ${["connecting", "open", "closing", "closed"][s.sockState] || "none"}${open}` +
        ` · opens ${s.sockOpens ?? "?"} · msgs ${s.sockMsgs ?? "?"} · ${kb} KB` +
        (s.lastClose ? ` · last close ${s.lastClose}` : "");
    // JPEG frames bypass the decoder: no codec string and no key frames to wait for.
    const codec = s.jpeg ? "JPEG" : `${codecName(s.codec) || "none"} ${s.codec || ""}`;
    const key = s.jpeg ? "keyframe n/a (JPEG)" : `keyframe ${s.waiting ? "WAITING" : "ok"}`;
    return [
      `codec ${codec} ${s.size || ""}`,
      `fps ${s.fps} shown / ${s.inFps} in · ${(s.inKbps / 1000).toFixed(1)} Mbps · decode ${s.decodeMs} ms · queue ${s.queue}`,
      `${key} · video input ${ago(s.inputAgeMs)} · params ${s.params ?? "?"} · keys ${s.keys}`,
      sockLine,
      box,
      `resyncs ${s.resyncs} · gaps ${s.gaps || 0} · restarts ${window.__zq.kicks}${s.smooth ? ` · delay ${s.delayMs} ms` : ""}` +
        ` · mode ${["JPEG", "H.264", "HEVC"][window.__zq.mode]}`,
      ...(s.error ? [`decoder error ${s.error}`] : []),
      ...(s.nativeReports ? [`native ×${s.nativeReports}: ${s.nativeReport}`] : []),
      ...(hevcBlocked ? [HEVC_NOTE] : []),
    ].join("\n");
  }

  function group(title, key, options, draft, render) {
    const h = document.createElement("h4");
    h.textContent = title;
    const row = document.createElement("div");
    row.className = "row";
    for (const [value, label] of options) {
      const b = document.createElement("button");
      b.textContent = label;
      b.className = String(draft[key]) === String(value) ? "on" : "";
      b.onclick = () => {
        draft[key] = value;
        render();
      };
      row.appendChild(b);
    }
    return [h, row];
  }

  function buildUI() {
    const style = document.createElement("style");
    style.textContent = CSS;
    document.head.appendChild(style);

    const btn = document.createElement("button");
    btn.id = "zq-btn";
    btn.title = "Video settings";
    btn.setAttribute("aria-label", "Video settings");
    // Video camera icon, drawn in the button's text color
    btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"' +
      ' stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="6" width="14" height="12" rx="2"/>' +
      '<path d="M16 10.5l6-3.5v10l-6-3.5z"/></svg>';
    const panel = document.createElement("div");
    panel.id = "zq-panel";

    const draft = {...settings};
    let codecError = "";
    function refreshCodecStatus() {
      if (!NATIVE_HEVC) return;
      fetch("/zq-codec.txt", {cache: "no-store"})
        .then(response => response.ok ? response.text() : "")
        .then(text => { codecError = text.trim(); updateInfo(); })
        .catch(() => {});
    }
    function render() {
      panel.replaceChildren(
        ...group("Resolution", "res", RESOLUTIONS, draft, render),
        ...group("Frame rate", "fps", [[30, "30"], [60, "60"]], draft, render),
        ...group("Video", "vmode", NATIVE_HEVC
          ? [[2, hevcBlocked ? "HEVC (retry)" : "HEVC (HD)"], [1, "H.264 (HD)"], [0, "JPEG (compatible)"]]
          : [[1, "H.264 (HD)"], [0, "JPEG (compatible)"]], draft, render),
        ...group("Playback", "smooth", [[false, "Low latency"], [true, "Smooth"]], draft, render),
        ...group("Video socket", "sock", [["worker", "Worker (fast)"], ["page", "Page (stock)"]], draft, render),
        ...group("Touch", "touch", [["raw", "Raw (fast)"], ["stock", "Stock"]], draft, render),
        // The dev proxy forces the overlay on, so the switch would do nothing there.
        ...(DEV_PROXY ? [] : group("Debug overlay", "debug", [[false, "Off"], [true, "On"]], draft, render)),
      );
      const apply = document.createElement("button");
      apply.className = "apply";
      apply.textContent = "Apply & reload";
      apply.onclick = () => {
        if (draft.vmode === 2) {
          const url = new URL(location.href);
          url.searchParams.delete("zq-h264");
          history.replaceState(null, "", url.href);
        }
        save(draft);
        location.reload();
      };
      const info = document.createElement("div");
      info.className = "info stream-info";
      const codecHelp = document.createElement("div");
      codecHelp.className = "info";
      codecHelp.textContent = NATIVE_HEVC
        ? "H.264 / HEVC changes take effect after you reconnect your phone. Reloading this page alone keeps the current codec. For JPEG, disconnect the phone before saving, then reconnect."
        : "HEVC selection requires the native codec-switch package. Stream stats show the codec currently received.";
      panel.append(codecHelp, apply, info);
      updateInfo();
    }

    function updateInfo() {
      const info = panel.querySelector(".stream-info");
      if (!info) return;
      const s = window.__zq.stats;
      const lines = [`Window ${document.documentElement.clientWidth}×${document.documentElement.clientHeight}` +
        ` @${window.devicePixelRatio || 1}x`, wifiLine(), touchLine(), wakeLine()];
      if (codecError) lines.push(codecError);
      if (hevcBlocked) lines.push(HEVC_NOTE);
      if (s && s.error) lines.push(`Decoder error ${s.error}`);
      if (s && s.nativeReports) lines.push(`Native ×${s.nativeReports}: ${s.nativeReport}`);
      if (s && s.codec) {
        const name = codecName(s.codec);
        lines.push(`Stream ${s.size} ${name ? `${name} · ` : ""}${s.codec}`);
        lines.push(`${s.fps} fps shown / ${s.inFps} in · decode ${s.decodeMs} ms · queue ${s.queue}`);
        // Bits per pixel per frame: the phone's bitrate spread over the stream size.
        // Lower means blockier motion; compare resolutions with the same screen moving.
        const [w, h] = String(s.size).split("x").map(Number);
        const bpp = w && h && s.inFps ? ((s.inKbps * 1000) / (w * h * s.inFps)).toFixed(3) : "-";
        lines.push(`Bitrate ${(s.inKbps / 1000).toFixed(1)} Mbps · ${bpp} bits/px`);
        if (s.smooth) lines.push(`Smooth delay ${s.delayMs} ms · late ${s.late}`);
        const sock = settings.sock !== "worker" ? "page" : s.socket ? "worker" : "worker (closed)";
        lines.push(`Resyncs ${s.resyncs} · frame gaps ${s.gaps || 0} · socket ${sock}`);
      }
      lines.push(`${PROJECT} · custom, unofficial`);
      lines.push(PROJECT_URL);
      info.replaceChildren(...lines.flatMap((l, i) => (i ? [document.createElement("br"), l] : [l])));
    }
    // Optional overlay on the CarPlay picture. Text changes once per second, so it
    // costs one small repaint per second; the video itself is drawn in the worker.
    const overlay = settings.debug ? document.createElement("div") : null;
    if (overlay) {
      overlay.id = "zq-debug";
      document.body.append(overlay);
    }
    setInterval(() => {
      if (window.__zq.stats?.hevcUnsupported) hevcFailed("decode");
      if (panel.classList.contains("open")) updateInfo();
      if (overlay) overlay.textContent = debugText();
    }, 1000);

    // Keep taps on our controls away from the CarPlay touch handlers.
    for (const el of [btn, panel]) {
      for (const t of ["touchstart", "touchmove", "touchend", "mousedown", "mouseup"]) {
        el.addEventListener(t, e => e.stopPropagation());
      }
    }
    btn.onclick = () => {
      render();
      const open = !panel.classList.contains("open");
      if (open) {
        // Anchor the panel under the button, clamped to stay on-screen.
        const r = btn.getBoundingClientRect();
        const w = panel.offsetWidth || 300;
        const left = Math.min(Math.max(8, r.right - w), window.innerWidth - w - 8);
        panel.style.right = "auto";
        panel.style.left = `${Math.round(left)}px`;
        const top = Math.round(Math.min(r.bottom + 8, window.innerHeight - 8));
        panel.style.top = `${top}px`;
        // Short windows: scroll inside the panel instead of cutting it off.
        panel.style.maxHeight = `${Math.max(120, window.innerHeight - top - 8)}px`;
      }
      panel.classList.toggle("open", open);
      if (open) {
        refreshWifi(updateInfo);
        refreshCodecStatus();
      }
    };
    document.body.append(panel); // the button lives in the vendor toolbar (see sync)

    // Put the Video button inline in the vendor home toolbar, right after the
    // settings (gear) button, instead of floating over the screen. Vue may
    // re-render and drop it, so the observer below re-inserts it.
    let queued = false;
    function sync() {
      queued = false;
      const gear = document.querySelector(".settings-btn");
      for (const el of document.querySelectorAll("#zq-btn")) if (el !== btn) el.remove();
      if (gear) {
        if (btn.previousElementSibling !== gear) gear.insertAdjacentElement("afterend", btn);
      } else {
        btn.remove();
        panel.classList.remove("open");
      }
    }
    new MutationObserver(() => {
      if (!queued) {
        queued = true;
        requestAnimationFrame(sync);
      }
    }).observe(document.body, {childList: true, subtree: true});
    sync();
  }

  if (document.body) buildUI();
  else document.addEventListener("DOMContentLoaded", buildUI);
})();
