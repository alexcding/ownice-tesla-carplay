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
    /* match the vendor toolbar buttons (.settings-btn / .refresh-btn) */
    #zq-btn{display:inline-flex;align-items:center;justify-content:center;vertical-align:middle;
      height:50px;margin-left:30px;padding:0 20px;border-radius:26px;border:0;cursor:pointer;
      background:#F0F0F0;color:#6d6d6d;box-shadow:0 2px 4px #0003;
      font:600 20px system-ui,sans-serif;-webkit-user-select:none;user-select:none}
    /* match the vendor settings card: light surface, gray pills, blue accent */
    #zq-panel{position:fixed;top:60px;right:8px;z-index:99999;width:300px;padding:18px;border-radius:20px;
      background:#fff;color:#222;font:16px system-ui,sans-serif;box-shadow:0 6px 24px #0004;display:none}
    #zq-panel.open{display:block}
    #zq-panel h4{margin:16px 0 8px;font-size:13px;font-weight:600;color:#8a8a8e;text-transform:uppercase}
    #zq-panel h4:first-child{margin-top:0}
    #zq-panel .row{display:flex;flex-wrap:wrap;gap:8px}
    #zq-panel button{flex:1 0 auto;min-height:44px;padding:0 14px;border-radius:22px;border:0;cursor:pointer;
      background:#F0F0F0;color:#6d6d6d;font:600 15px system-ui,sans-serif}
    #zq-panel button.on{background:#105bf1;color:#fff}
    #zq-panel .apply{width:100%;margin-top:16px;background:#105bf1;color:#fff}
    #zq-panel .info{margin-top:12px;font-size:12px;color:#999;line-height:1.5}
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
        panel.style.top = `${Math.round(Math.min(r.bottom + 8, window.innerHeight - 8))}px`;
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
