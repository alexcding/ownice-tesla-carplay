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
  const DEFAULTS = {res: "auto", fps: 60, h264: true, smooth: false, sock: "worker"};
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
      return {...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) || "{}")};
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
  const hd = settings.res === "hd";
  const target = settings.res === "auto" || hd ? 0 : parseInt(settings.res, 10);

  // The patched bundle opens the video socket through videoSocket(). In
  // "worker" mode the socket lives in worker.js, so frames go straight to the
  // decoder instead of through this (busy) main thread; the bundle gets a
  // WebSocket-like stand-in driven by {id: "ws"} notes from the worker.
  const sockets = new Map(); // id -> stand-in
  let nextSocket = 0;

  function videoSocket(url, worker) {
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
    h264: settings.h264,
    smooth: settings.smooth,
    stats: null, // set once per second by the patched bundle from worker.js
    videoSocket,
    onWorkerSocket,
    workerGone,
  };

  const CSS = `
    /* CarPlay gets every touch: no browser pan/zoom gesture detection on the player */
    .player-box{touch-action:none}
    /* match the vendor toolbar buttons (.settings-btn / .refresh-btn) */
    #zq-btn{display:inline-flex;align-items:center;justify-content:center;vertical-align:middle;
      height:50px;margin-left:30px;padding:0 20px;border-radius:26px;border:0;cursor:pointer;
      background:#F0F0F0;color:#6d6d6d;box-shadow:0 2px 4px #0003;
      font:600 20px system-ui,sans-serif;-webkit-user-select:none;user-select:none}
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
    #zq-panel .check{width:100%;margin-top:8px}
    #zq-panel .info{margin-top:12px;font-size:12px;color:#999;line-height:1.5;overflow-wrap:anywhere}
    #zq-panel .probe{margin-top:12px;font-size:12px;color:#555;line-height:1.5}
    #zq-panel .probe:empty{display:none}
  `;

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

  // HEVC decoder check (docs/HEVC_RESEARCH.md, test 1): asks WebCodecs whether
  // this browser could decode HEVC at the current stream size. A support query
  // only; it decodes nothing and says nothing about speed. H.264 is the baseline.
  const PROBE_CODECS = [
    ["avc1.640033", "H.264 High 5.1"],
    ["hvc1.1.6.L123.B0", "HEVC Main 4.1 hvc1"],
    ["hev1.1.6.L123.B0", "HEVC Main 4.1 hev1"],
    ["hvc1.1.6.L153.B0", "HEVC Main 5.1 hvc1"],
    ["hev1.1.6.L153.B0", "HEVC Main 5.1 hev1"],
    ["hvc1.2.4.L153.B0", "HEVC Main10 5.1 hvc1"],
  ];
  const PROBE_ACCEL = [["prefer-hardware", "hw"], ["no-preference", "any"]];

  // The size the patched bundle requests: window × scale, capped at maxh.
  function streamSize() {
    const el = document.documentElement;
    let w = (el.clientWidth || window.innerWidth || 1280) * window.__zq.scale;
    let h = (el.clientHeight || window.innerHeight || 720) * window.__zq.scale;
    if (h > window.__zq.maxh) {
      w = (window.__zq.maxh * w) / h;
      h = window.__zq.maxh;
    }
    return [Math.round(w / 2) * 2, Math.round(h / 2) * 2];
  }

  async function probeDecoders() {
    const [codedWidth, codedHeight] = streamSize();
    const lines = [`Decoder check ${codedWidth}×${codedHeight}`];
    if (typeof VideoDecoder === "undefined" || !VideoDecoder.isConfigSupported) {
      lines.push("WebCodecs VideoDecoder unavailable");
      return lines;
    }
    const results = [];
    for (const [codec, label] of PROBE_CODECS) {
      const parts = [];
      for (const [hardwareAcceleration, short] of PROBE_ACCEL) {
        let r;
        try {
          r = (await VideoDecoder.isConfigSupported({codec, codedWidth, codedHeight, hardwareAcceleration}))
            .supported ? "yes" : "no";
        } catch (e) {
          r = "error";
        }
        parts.push(`${short} ${r}`);
        results.push({codec, codedWidth, codedHeight, hardwareAcceleration, result: r});
      }
      lines.push(`${label}: ${parts.join(" · ")}`);
    }
    console.log("[zq] decoder check", JSON.stringify(results));
    return lines;
  }

  function buildUI() {
    const style = document.createElement("style");
    style.textContent = CSS;
    document.head.appendChild(style);

    const btn = document.createElement("button");
    btn.id = "zq-btn";
    btn.textContent = `Video · ${settings.res === "auto" ? "Auto" : hd ? "Auto HD" : settings.res + "p"}`;
    const panel = document.createElement("div");
    panel.id = "zq-panel";

    const draft = {...settings};
    let probe = null; // decoder check result lines, kept across re-renders
    function render() {
      panel.replaceChildren(
        ...group("Resolution", "res", RESOLUTIONS, draft, render),
        ...group("Frame rate", "fps", [[30, "30"], [60, "60"]], draft, render),
        ...group("Video", "h264", [[true, "H.264 (HD)"], [false, "JPEG (compatible)"]], draft, render),
        ...group("Playback", "smooth", [[false, "Low latency"], [true, "Smooth"]], draft, render),
        ...group("Video socket", "sock", [["worker", "Worker (fast)"], ["page", "Page (stock)"]], draft, render),
      );
      const apply = document.createElement("button");
      apply.className = "apply";
      apply.textContent = "Apply & reload";
      apply.onclick = () => {
        save(draft);
        location.reload();
      };
      const check = document.createElement("button");
      check.className = "check";
      check.textContent = "Check HEVC decoder";
      check.onclick = () => {
        probe = ["Checking…"];
        render();
        probeDecoders().then(lines => {
          probe = lines;
          render();
        });
      };
      const probeInfo = document.createElement("div");
      probeInfo.className = "probe";
      if (probe) probeInfo.replaceChildren(...probe.flatMap((l, i) => (i ? [document.createElement("br"), l] : [l])));
      const info = document.createElement("div");
      info.className = "info";
      panel.append(apply, check, probeInfo, info);
      updateInfo();
    }

    function updateInfo() {
      const info = panel.querySelector(".info");
      if (!info) return;
      const s = window.__zq.stats;
      const lines = [`Window ${document.documentElement.clientWidth}×${document.documentElement.clientHeight}` +
        ` @${window.devicePixelRatio || 1}x`];
      if (s && s.codec) {
        lines.push(`Stream ${s.size} ${s.codec}`);
        lines.push(`${s.fps} fps shown / ${s.inFps} in · decode ${s.decodeMs} ms · queue ${s.queue}`);
        // Bits per pixel per frame: the phone's bitrate spread over the stream size.
        // Lower means blockier motion; compare resolutions with the same screen moving.
        const [w, h] = String(s.size).split("x").map(Number);
        const bpp = w && h && s.inFps ? ((s.inKbps * 1000) / (w * h * s.inFps)).toFixed(3) : "-";
        lines.push(`Bitrate ${(s.inKbps / 1000).toFixed(1)} Mbps · ${bpp} bits/px`);
        if (s.smooth) lines.push(`Smooth delay ${s.delayMs} ms · late ${s.late}`);
        const sock = settings.sock !== "worker" ? "page" : s.socket ? "worker" : "worker (closed)";
        lines.push(`Resyncs ${s.resyncs} · socket ${sock}`);
      }
      lines.push(`${PROJECT} · custom, unofficial`);
      lines.push(PROJECT_URL);
      info.replaceChildren(...lines.flatMap((l, i) => (i ? [document.createElement("br"), l] : [l])));
    }
    setInterval(() => {
      if (panel.classList.contains("open")) updateInfo();
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
