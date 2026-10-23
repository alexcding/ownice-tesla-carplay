"""Patches for the vendor CarPlay player (web/player.html + web/assets/index.*.js, firmware 2.9.11).

Shared by tools/dev_player.py (live proxy) and the firmware build. Each entry is
(description, original, replacement, expected count or None for "at least one").
"""

import sys

SETTINGS_SCRIPT = "/zq-settings.js"

PLAYER_HTML_PATCHES = [
    ("load in-car settings before the bundle",
     '<script type="module" crossorigin src="/assets/',
     f'<script src="{SETTINGS_SCRIPT}"></script>\n  <script type="module" crossorigin src="/assets/', 1),
]

# Native HEVC builds only: hold player startup (and its INIT) until the HEVC
# capability probe in zq-settings.js settles. Top-level await, so it is kept out
# of the default package for browsers without it.
HEVC_BUNDLE_PATCHES = [
    ("wait for HEVC capability probe before player startup",
     "const Gf=function(){", "await window.__zq.hevcReady;const Gf=function(){", 1),
]

# Values come from window.__zq, defined by player/zq-settings.js.
BUNDLE_PATCHES = [
    # Stock reads the window size once at load, then fits every later stream
    # (UPDATE_INFO: picture size, touch scale/offsets) into that size. Opening in
    # the side window and expanding left a high-res picture in the old small box
    # with touches mapped for the old window. Re-read it with each resize INIT.
    ("window size is live, not fixed at load",
     "const{w:Fn,h:yt}=a7(),l7=6e3;", "let{w:Fn,h:yt}=a7();const l7=6e3;", 1),
    ("re-read the window size when a resize resends INIT",
     'function rf(){te("--- send init msg delay ---"),',
     'function rf(){({w:Fn,h:yt}=a7()),te("--- send init msg delay ---"),', 1),
    # INIT size policy (zq-settings.js __zq.initSize): reuse a running session's
    # size so opening the player does not restart the phone session; a real
    # resize or the no-video recovery asks for a different size on purpose.
    ("INIT size from __zq.initSize",
     "ae%16!=0&&(ae=ae-ae%16+16);", "ae%16!=0&&(ae=ae-ae%16+16);({w:ae,h:at}=__zq.initSize(ae,at));", 1),
    ("picture scale factor (stock 0.8)", "Ue=.8", "Ue=__zq.scale", 8),
    ("max stream height (stock 720)", "at>720){var Wf=720*ae/at,$f=720",
     "at>__zq.maxh){var Wf=__zq.maxh*ae/at,$f=__zq.maxh", 1),
    ("frame rate (stock 30)", "fps:30", "fps:__zq.fps", 1),
    # Video mode sent as is_h264_mode: 0 JPEG, 1 H.264, 2 HEVC (zq-settings.js vmode).
    ("H.264 mode (stock JPEG)", "var Mf=0;", "var Mf=__zq.mode|0;", 1),
    ("mouse hover is not a touch drag (only send moves while the button is held)",
     "onMousemove:t[3]||(t[3]=l=>{e.handleMouseEvt(l,!0)})",
     "onMousemove:t[3]||(t[3]=l=>{l.buttons&1&&!__zq.rawTouch&&e.handleMouseEvt(l,!0)})", 1),
    # Raw touch (zq-settings.js): moves go out from pointerrawupdate, not from the
    # rAF-aligned touchmove; down/up stay on the stock handlers.
    ("touch moves come from __zq in raw touch mode",
     "onTouchmove:t[6]||(t[6]=l=>{e.handleTouchEvt(l,!0)})",
     "onTouchmove:t[6]||(t[6]=l=>{__zq.rawTouch?l.preventDefault():e.handleTouchEvt(l,!0)})", 1),
    # Ra() also records the position the stock touchend reports.
    ("__zq gets the stock touch mapping and sender",
     "return{handleMouseEvt:wf,handleTouchEvt:yf,",
     "return{zqTouch:__zq.touchSender(p=>ka(Ra({type:\"touchmove\",changedTouches:[p]})),(Q,ae)=>au(Q,ae,!0)),"
     "handleMouseEvt:wf,handleTouchEvt:yf,", 1),
    ("worker gets the smooth-mode flag",
     'A=new Worker("./worker.js")', 'A=new Worker("./worker.js?smooth="+(__zq.smooth?1:0))', 1),
    ("worker can request a video-socket resync and report stats",
     'A.onmessage=R=>{R.data.id=="render"&&g.value==Se.STARTED&&(l.value=!0)}',
     'A.onmessage=R=>{const D=R.data||{};D.id=="resync"&&(cn(),lu());D.id=="stats"&&(__zq.stats=D);'
     'D.id=="ws"&&__zq.onWorkerSocket(D);'
     'D.id=="render"&&g.value==Se.STARTED&&(l.value=!0)}', 1),
    # Socket recovery must not tell the phone to stop compressed video. The
    # native socket-close guard cannot suppress this separate control message.
    # Keep stock JPEG cleanup and all explicit focus controls unchanged.
    ("retain compressed video focus during player cleanup",
     'n=null,j.value="",l.value=!1,C.value=!1,Xn(!1)}function cu()',
     'n=null,j.value="",l.value=!1,C.value=!1,__zq.h264||Xn(!1)}function cu()', 1),
    # Worker-owned video socket (zq-settings.js videoSocket): frames skip the main thread.
    ("video socket opened through __zq (worker-owned unless set to Page)",
     "n=new WebSocket(`${i7}?_t=${new Date().getTime()}`)",
     "n=__zq.videoSocket(`${i7}?_t=${new Date().getTime()}`,A)", 1),
    ("worker-owned socket only reports that frames arrive",
     "n.onmessage=R=>{const Q=new Uint8Array(R.data);",
     "n.onmessage=R=>{if(R.zq){C.value=!0;return}const Q=new Uint8Array(R.data);", 1),
    ("a terminated worker closes its sockets",
     "A==null||A.terminate()", "A==null||(__zq.workerGone(A),A.terminate())", 1),
    # Hot path: runs on every touch move (Pc() logs 3 lines, plus one here).
    # The clamp bounds are the stream size from this page's UPDATE_INFO
    # (__zq.touchBounds): with a reused or higher-resolution session the stream
    # is larger than window x scale, and stock bounds cut off the bottom/right
    # (e.g. the CarPlay home button). Stock window x scale until it arrives.
    # CarLife wireless keeps stock bounds (its mapping divides by Ue instead).
    ("touch mapping without per-move console logging, clamped to the stream",
     "function ka(e){const{w:t,h:n}=Pc();te(`getViewDimensions:= ${t} X ${n}`);",
     "function ka(e){const Pd=document.documentElement,Zb=gs!=ft.CARLIFE_WIRELESS&&__zq.touchBounds(),"
     "t=Zb?Zb.w:en((Pd.clientWidth||document.body.clientWidth)*Ue),"
     "n=Zb?Zb.h:en((Pd.clientHeight||document.body.clientHeight)*Ue);", 1),
    # The stream size the bundle derives from UPDATE_INFO (content size for
    # some phone types): the same values its touch scale ls uses.
    ("touch bounds follow the bundle's own UPDATE_INFO stream size",
     'A.postMessage({id:"UpdateFrame",w:Q,h:ae})', '__zq.streamFrame(Q,ae),A.postMessage({id:"UpdateFrame",w:Q,h:ae})', 1),
    # Hot path: runs once per video frame.
    ("no reactive debug label per frame", ':j.value="frame..."+_++,', ":_++,", 1),
    ("transfer frame buffer to worker instead of copying",
     'A.postMessage({id:"Data",data:Q})', 'A.postMessage({id:"Data",data:Q},[Q.buffer])', 1),
]


def apply(text, patches, strict=True):
    """Apply patches; on a count mismatch exit (strict) or warn and skip."""
    for desc, old, new, expected in patches:
        count = text.count(old)
        if count == 0 or (expected is not None and count != expected):
            msg = f"patch '{desc}': found {count} matches"
            if strict:
                sys.exit(msg)
            print(f"!! {msg}, skipped", file=sys.stderr)
            continue
        text = text.replace(old, new)
    return text
