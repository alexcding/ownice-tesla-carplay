// In-car video settings for the zjinnova player.
// Loaded by player.html before the vendor bundle; the patched bundle reads
// window.__zq for the stream size, frame rate and codec it requests.
// The "Video" button appears on the box's home screen (Tesla house icon).

(function () {
  const KEY = "zq.settings";
  const DEFAULTS = {res: "auto", fps: 60, h264: true, smooth: false};
  const AUTO_MAX_HEIGHT = 1200; // Model 3/Y screen height; S/X browser windows are smaller than 1300
  const RESOLUTIONS = [
    ["auto", "Auto (screen)"],
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
  const target = settings.res === "auto" ? 0 : parseInt(settings.res, 10);

  window.__zq = {
    // multiplier applied by the player to the window size it requests
    get scale() {
      const h = document.documentElement.clientHeight || window.innerHeight || 720;
      return target ? target / h : 1;
    },
    maxh: target || AUTO_MAX_HEIGHT,
    fps: settings.fps,
    h264: settings.h264,
    smooth: settings.smooth,
    stats: null, // set once per second by the patched bundle from worker.js
  };

  const CSS = `
    #zq-btn{position:fixed;top:12px;right:12px;z-index:99999;height:48px;padding:0 18px;border-radius:24px;
      border:0;background:rgba(10,132,255,.9);color:#fff;font:600 16px system-ui,sans-serif;display:none}
    #zq-btn.show{display:block}
    #zq-panel{position:fixed;top:60px;right:8px;z-index:99999;width:260px;padding:16px;border-radius:14px;
      background:rgba(28,28,30,.95);color:#fff;font:15px system-ui,sans-serif;display:none}
    #zq-panel.open{display:block}
    #zq-panel h4{margin:12px 0 6px;font-size:12px;font-weight:600;color:#aaa;text-transform:uppercase}
    #zq-panel h4:first-child{margin-top:0}
    #zq-panel .row{display:flex;flex-wrap:wrap;gap:6px}
    #zq-panel button{flex:1 0 auto;min-height:40px;padding:0 10px;border-radius:8px;border:0;
      background:#3a3a3c;color:#fff;font:inherit}
    #zq-panel button.on{background:#0a84ff}
    #zq-panel .apply{width:100%;margin-top:14px;background:#30d158;color:#000;font-weight:600}
    #zq-panel .info{margin-top:10px;font-size:12px;color:#888}
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

  // The vendor's Settings > Display Mode row only offers "Compat." since 2.9.x.
  // Replace its options with HD (H.264) / Compat. (JPEG), bound to our settings.
  function restoreDisplayModeRow() {
    const vendorOpt = document.querySelector(".compat-mode-opt:not(.zq-opt)");
    if (!vendorOpt) return;
    const row = vendorOpt.parentElement;
    if (row.querySelector(".zq-opt")) return;
    for (const el of row.children) el.style.display = "none";
    for (const [h264, label] of [[true, "HD (H.264)"], [false, "Compat. (JPEG)"]]) {
      const opt = document.createElement("span");
      opt.className = "compat-mode-opt zq-opt" + (settings.h264 === h264 ? " selected" : "");
      opt.textContent = label;
      opt.onclick = () => {
        if (settings.h264 === h264) return;
        save({...settings, h264});
        location.reload();
      };
      row.appendChild(opt);
    }
  }

  function buildUI() {
    const style = document.createElement("style");
    style.textContent = CSS;
    document.head.appendChild(style);

    const btn = document.createElement("button");
    btn.id = "zq-btn";
    btn.textContent = `Video · ${settings.res === "auto" ? "Auto" : settings.res + "p"}`;
    const panel = document.createElement("div");
    panel.id = "zq-panel";

    const draft = {...settings};
    function render() {
      panel.replaceChildren(
        ...group("Resolution", "res", RESOLUTIONS, draft, render),
        ...group("Frame rate", "fps", [[30, "30"], [60, "60"]], draft, render),
        ...group("Video", "h264", [[true, "H.264 (HD)"], [false, "JPEG (compatible)"]], draft, render),
        ...group("Playback", "smooth", [[false, "Low latency"], [true, "Smooth"]], draft, render),
      );
      const apply = document.createElement("button");
      apply.className = "apply";
      apply.textContent = "Apply & reload";
      apply.onclick = () => {
        save(draft);
        location.reload();
      };
      const info = document.createElement("div");
      info.className = "info";
      panel.append(apply, info);
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
        if (s.smooth) lines.push(`Smooth delay ${s.delayMs} ms · late ${s.late}`);
        lines.push(`Resyncs ${s.resyncs}`);
      }
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
      panel.classList.toggle("open");
    };
    document.body.append(btn, panel);

    // Only offer the button on the box's home screen (house icon), identified
    // by the vendor's settings button, so nothing covers CarPlay itself.
    let queued = false;
    function sync() {
      queued = false;
      const home = !!document.querySelector(".settings-btn");
      btn.classList.toggle("show", home);
      if (!home) panel.classList.remove("open");
      restoreDisplayModeRow();
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
