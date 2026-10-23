# AGENTS.md

Current device installation: `2.9.11-oc12` (owner-flashed and power-cycled): full
native HEVC + ChaCha20 NEON receiver with video diagnostics and the side-window →
full-screen resize fix. HEVC plays in Mac Chrome and in the **Tesla browser**
(owner photos). Open: an HEVC blackout after the phone ran with no player connected
for a while (oc13 adds an automatic session restart, built, not flashed) and
extended idle/return. The NEON copy passed a five-minute live test on the earlier
`2.9.11-oc10-hevc-neon`; oc12's diagnostic receiver is a new derived base
(`deployment_validated: false` until confirmed). See
[the HEVC diagnostic record](docs/HEVC_VIDEO_DIAGNOSTICS.md) and
[crypto evidence](docs/CRYPTO_RESEARCH.md).
Current stock-to-package build/install commands are in [README.md](README.md).
A complete HEVC build includes browser files; use `--native-only` only when
intentionally preserving an already installed player. Never infer deployment
authorization from a request to build or push source.

Version naming follows the stock base: plain `2.9.11-ocN`, currently `2.9.11-oc13`, with no
feature suffixes (build options live in the package metadata). Increment `oc` for future changes.
Never reuse the stock `2.9.11` string. Historical `3.0-...` records retain their
original identifiers; renaming a package does not flash or rename the adapter.

## Crypto release invariant

- Every `build_patch.py --native-hevc` build, including `--native-only`, MUST
  apply the ChaCha20 NEON patch after HEVC. Do not omit it to work around a build
  failure. Browser/Wi-Fi-only packages must leave the installed receiver binary
  untouched so they retain both HEVC and NEON. `--native-test` and stock restore
  are deliberate diagnostic/recovery exceptions.
- Keep strict stock, derived-HEVC, upstream-source and compiled-payload hashes.
  If another native feature changes `z-tesla`, review and explicitly pin the new
  derived base before composing NEON; never accept arbitrary hashes or silently
  fall back to scalar crypto. Keep the original ChaCha20 routine/caller/loader
  checks. AES, Poly1305 and cipher negotiation are separate concerns.
- Run `python -I tools/test_crypto_release.py --firmware <stock.zip>` in the
  crypto environment before releasing native changes. It checks all four HEVC
  flag combinations, original HEVC mappings, final hashes and web-only
  preservation. See [release instructions](docs/CRYPTO_RELEASES.md) for the full
  validation commands and rollback boundaries. Never commit generated firmware.

Notes for agents and contributors working on this repo.

## Project purpose and scope

This project is a community revival of the Ownice T3 adapter for use in a Tesla browser. Its scope
covers the whole adapter experience: Wi-Fi and Bluetooth connectivity, CarPlay and Android Auto,
native receiver and vendor services, video and audio quality, latency, browser playback, settings,
diagnostics, updates, and recovery. The intended target is the owner's adapter, or a device they
are authorized to modify.
Ownership does not authorize actions against vendor infrastructure or other people's devices.

The adapter is discontinued. The aim is to extend the useful life of existing hardware and share
reproducible research, maintainable improvements, and compatibility findings with other owners.
Browser patches are the starting point, not a limit on the project. This purpose does not
expand the authorization boundaries below.

The current custom update package contains HTML/JavaScript player changes and a custom web-version
string, packaged with the vendor's existing update script. Work may research, develop, and validate
native service improvements and compatible receiver updates as well as web changes. It does not
modify Tesla vehicle firmware or the adapter's bootloader or kernel.

Device internals and recovery observations below are reference evidence, not instructions to attempt
privileged access or bypass protections. Password guessing, authentication or signature bypasses,
and use or disclosure of bundled private keys are outside this project's scope. If a task appears to
require those operations, explain the dependency and look for an approach within these boundaries.

## Working on a task

- State the concrete adapter behavior to improve and inspect the relevant source files or firmware artifacts first.
  Describe the actual operation precisely; retain security-relevant facts and uncertainties.
- Keep source analysis, code edits, local validation, package building, and device deployment as
  distinct steps. A request to edit or build does not authorize an upload or reboot.
- Prefer local checks and the development proxy for iteration. Read-only device queries are allowed;
  changes to device settings need authorization for the specific task.
- Before an authorized deployment, report the package version, changed files, validation results,
  and rollback path. Upload or reboot only with the owner's explicit go-ahead, as required below.
- Do not print private keys or paste complete vendor archive contents into model context, logs, or
  reports. Inspect only the files needed for the task and keep vendor artifacts out of Git.
- For community-facing findings, record the firmware identity, affected subsystem, evidence,
  expected benefit, compatibility limits, validation status, and recovery implications. Distinguish
  observations from hypotheses and fixes from proposals; do not assume another owner's board matches.
- Do not treat these notes as a way to disable model safeguards. If a tool flags a task, report the
  actual operation and available diagnostic text; do not infer a cause from the warning alone.

## Rules

- **Never commit vendor firmware or anything extracted from it.** The official zip contains private TLS keys
  (`usr/z-loader/*.key` for zbox.link, autoaibox.link, kalos.link, tslbox.link). `.gitignore` blocks
  `*.zip`, `*.key` and `*.crt`; keep it that way. Patches are applied at build time from the vendor download.
- **Never upload to or reboot a box without the owner's explicit go-ahead.** Read-only queries
  (`tools/box_info.sh`, `GET /cgi-bin/check*.cgi`) are fine.
- Every patch is an exact string replacement with an expected match count (`tools/player_patches.py`,
  `INDEX_PATCHES` in `tools/build_patch.py`). The firmware build is strict: if the base changes, it must fail,
  not guess.
- **Version scheme:** `CUSTOM_VERSION` in `build_patch.py`, currently `2.9.11-oc13` (bump `-oc14`, `-oc15`…). Always keep
  a non-vendor suffix so our `webversion` never equals a stock string — that is what keeps the box's own
  "Check for updates" re-offering stock (it compares current != CDN webver). Full attribution
  (`ownice-tesla-carplay · custom, unofficial · <url>`) shows in the player's Video panel footer.
- **Never publish the vendor firmware zip** (it holds the private `*.key` TLS keys). `flash.sh rollback` pulls it
  from the CDN on demand; don't commit or upload it anywhere public.
- Target car: AMD Tesla (WebCodecs available). Default stream = **Auto**: browser window size at 1x,
  capped at 1200 tall. Don't default to devicePixelRatio scaling (too heavy; see Performance).

## Repo layout

| Path | Purpose |
|---|---|
| `tools/build_patch.py` | download official 2.9.11 zip (MD5-checked), apply patches, write `build/update_tesla.zip` (default `2.9.11-oc13`); `--native-hevc` includes NEON |
| `tools/player_patches.py` | patch list for `player.html` and the player bundle, shared by build and dev server |
| `tools/wifi_patches.py` | opt-in (`build_patch.py --wifi-ac`) soft-AP boot-script patches: 802.11ac 80 MHz on a fixed channel, with 802.11n fallback |
| `tools/tls_patches.py` | default (opt out: `build_patch.py --no-tls-chacha`): z-tesla started with `OPENSSL_CONF` → `usr/z-loader/zq-openssl.cnf`, TLS 1.3 ChaCha20 first with server preference |
| `tools/test_wifi_patches.py` | runs the `--wifi-ac` scripts against mocked commands: `python3 -I tools/test_wifi_patches.py --firmware <zip>` |
| `tools/dev_player.py` | local HTTPS reverse proxy to a live box; serves patched player + `player/*.js` without flashing (`--firmware <zip>` when the box already runs our build) |
| `tools/bench_worker.mjs` | Node micro-benchmark of `player/worker.js` per-frame JS (WebCodecs stubbed): `node tools/bench_worker.mjs [--smooth]` |
| `tools/box_info.sh` | read-only status: version, channel, Wi-Fi, cert expiry |
| `player/worker.js` | our video worker: H.264/HEVC via WebCodecs (low latency), JPEG fallback; owns the video WebSocket in "Worker" socket mode |
| `player/zq-settings.js` | in-car settings (`window.__zq`): resolution (incl. Auto HD), fps, codec, playback, video socket, touch (raw/stock); "Video" button in the home toolbar + its panel (stream and touch stats, box AP Wi-Fi channel/mode/country from read-only CGIs); WebSocket stand-in for the worker-owned socket |

## Task-specific reference

[Device reference](docs/DEVICE_REFERENCE.md) contains architecture, ports, control protocol, CGI API,
update flow, recovery limitations, and historical status. Read the relevant sections when the task
needs them. Deployment and recovery details do not grant permission to upload or reboot.

[Firmware improvement survey](docs/FIRMWARE_IMPROVEMENTS.md) maps Wi-Fi, phone projection,
audio/video, native services, updates and diagnostics opportunities to firmware evidence and
validation requirements. [HEVC research](docs/HEVC_RESEARCH.md) records codec-specific findings.
[Receiver update candidates](docs/RECEIVER_CANDIDATES.md) records inspected firmware versions,
artifact hashes, API/dependency differences and GitHub source-port candidates. A newer firmware
label or receiver version does not establish compatibility or HEVC support.

## Why stock 2.9.11 looks bad

| Stock behaviour | Effect | Our patch |
|---|---|---|
| `is_h264_mode` hard-coded 0; `worker.js` H.264 code commented out | box re-encodes every frame to JPEG on the T113: artifacts, latency | `Mf=__zq.mode|0` (0 JPEG / 1 H.264 / 2 HEVC), new `player/worker.js` |
| `Ue=.8` on every path | requests 80% of the window, then upscales | `Ue=__zq.scale` |
| height cap 720 | at most 720p | `__zq.maxh` (Auto: 1200) |
| `fps:30` | | `__zq.fps` (default 60) |
| Display Mode row offers only "Compat." (LOW/NORMAL/HD code still present) | no way back to HD | HD/JPEG choice moved to our Video panel (`zq-settings.js`) |
| entry page probes `https://<host>` and requires `ch < 1300` | stays on HTTP from `3.3.3.3` or tall screens | `INDEX_PATCHES` → `https://www.zbox.link` |
| mousemove always sent as touch-down | hover drags CarPlay (mouse only; touch screens unaffected) | send only while `buttons&1` |
| per-frame Vue label update, frame buffer copied to worker | main-thread overhead | removed / transferred |
| video socket on the main thread | every frame is a main-thread task competing with touch | worker-owned socket (`__zq.videoSocket`) |
| every touch move logs 4 `console.log` lines (`ka()` → `Pc()`); no `touch-action` on the player | slower drag handling | quiet `ka()`, `.player-box{touch-action:none}` |
| drag moves sent from `touchmove`, which Chrome holds until the next animation frame | moves reach CarPlay up to a frame late | "Raw" touch (default): moves from `pointerrawupdate` through the bundle's `ka(Ra())`/`au()` (`__zq.touchSender`), same-point moves skipped; down/up stay stock |

## Performance notes

- Verified on Mac Chrome: H.264 High profile (`avc1.640020`), decode queue 0, ~50–60 fps while moving.
  2x HiDPI (2656×1314, `avc1.640033`) worked but was too heavy.
- iPhone sends ~30 fps on map screens and in bursts regardless of the request (DiPlay `docs/SMOOTH_WIRELESS.md`).
- Keep ≤ ~3.7 MP per frame for 50+ fps (DiPlay measurements). Tesla Auto ≈ 1920×1100 ≈ 2.1 MP.
- Never drop delta frames to "catch up" without a guaranteed IDR: CarPlay sends keyframes rarely and the picture
  freezes (happened in an early worker version).
- Backlog recovery (`player/worker.js`) reopens the video socket (DiPlay `VideoBacklogRecovery.kt`): needs the
  undecoded frames to span ≥ 100 ms, then hard at 1.5 s, or > 250 ms with span +100 ms / queue +4 frames over
  750 ms. A delayed burst that is draining never resyncs. Also resyncs when the decoder holds queued input 500 ms
  without progress while frames keep arriving (each > 120 ms input pause restarts that clock, so a wedge on a
  sparse still screen is not caught). An unsupported decoder config falls back to a plain `{codec}` one.
- **Lost frames**: hard drags can make the box drop H.264 frames before they reach the browser (TCP to the
  browser; 102 `frame_num` gaps in one smeared run, 0 in 1345 frames of a clean one). The decoder raises no
  error and smears until the next IDR, which CarPlay sends rarely. `worker.js` checks `frame_num` continuity
  (the phone's SPS forbids gaps) and resyncs on a gap: the box restarts the CarPlay screen, preferred over a
  smear. The web control protocol has no key-frame request; `libAirPlay.so` has
  `AirPlayReceiverSessionForceKeyFrame` (LIVI sends `forceKeyFrame`; node-carplay sends Carlinkit `frame` every
  5 s), so a native z-tesla change could replace the restart. A focus request while focused gives no IDR.
- Player sockets (z-tesla, `:5002` etc.) negotiate TLS 1.3 **AES-256-GCM** with stock settings; the T113 (Cortex-A7)
  has no AES instructions. Plain `ws://` is not an option: WebCodecs needs an HTTPS page and Chrome blocks `ws://`
  from HTTPS. ChaCha20 is the **default** since `3.0-oc5` (`tools/tls_patches.py`; `--no-tls-chacha` builds,
  formerly suffixed `-aes`, ship the stock `z-tesla-sh.sh`, which undoes it). First flashed on BOX_1189B as
  `3.0-oc4-ht40-tls`: after a power cycle ports 5002/9002/7002 negotiate `TLS_CHACHA20_POLY1305_SHA256` against an
  AES-first client, Mac Chrome streams 51–61 fps with 0 frame gaps, and the owner reports the player as "a lot
  faster" (subjective; no before/after Touch→frame numbers yet); one hard-drag run afterwards had 0 frame gaps and
  0 resyncs. Only z-tesla gets the config; uploads/rollback (z-connman, Go TLS) are unaffected. Needs a power cycle.
  The box's OpenSSL (static in z-tesla and the shared `libcrypto.so.1.1`) has no ARM assembly, so every cipher runs
  in plain C; ChaCha20-Poly1305 is the cheapest of the three Chrome offers there.
- Touch→frame (touch-down to the first frame after a still screen, Video panel): ~157 ms median, 70–313 ms over
  11 taps on Mac Chrome via the dev proxy. Decode is ~2 ms, so the lag is the phone/box round trip.
- Video socket defaults to **Worker**: `__zq.videoSocket` gives the bundle a WebSocket stand-in, `worker.js` owns
  the real socket, and frames never touch the main thread (stock path: ~1 main-thread task per frame; worker
  path: ~4 notes/s). "Page (stock)" in the Video panel restores the vendor path.
- **Auto HD** (opt-in): window × max(devicePixelRatio, 1.5), capped at 3.7 MP / 1800 tall (≈ 2448×1520 on a
  1920×1200 window). More pixels share the phone's fixed bitrate, so motion may look blockier.
- Opt-in "Smooth" jitter buffer: p90 arrival jitter + 20 ms, 30–200 ms (arrival-timed; DiPlay uses sender PTS).
- Wi-Fi 802.11ac (`--wifi-ac`, version `3.0-oc4-ac`, **not yet flashed**): both radios (RTL8821CS on BOX_1189B,
  RTL8733BS on BOX_0333C) and hostapd v2.7 have VHT; stock stops at 802.11n HT40. VHT goes only into the runtime
  `/dev/hostapd.conf`, and only when `wifi-channel-mode` is a fixed channel in 36–48/149–161: in `auto50`/`auto24`,
  `z-tesla` switches to random channels at runtime (`hostapd_cli set channel`) without touching the 80 MHz center.
  A watcher drops VHT once the mode, country or channel changes and re-enables the AP if that switch left it down.
  Boot counter `/usr/z-loader/zq-vht-fails` (cleared when a station joins): two VHT boots without a station make
  the next boot 802.11n. A rollback (`flash.sh rollback`) needs the AP up, so every fallback ends in stock 802.11n.
  USB/ADB as an alternative recovery path is untested (see docs/DEVICE_REFERENCE.md).
- Wi-Fi width: the box's AP boots at 802.11n **20 MHz** (72 Mbit/s at MCS 7) although `hostapd.conf` asks for HT40.
  The RTL8821CS driver decides 20 MHz at AP start and tells hostapd (channel-switch notify). Writing
  `<ch> 1 <offset>` to `/proc/net/rtl8821cs/wlan0/change_bss_chbw` afterwards gives **40 MHz / 150 Mbit/s**
  (verified once, `--wifi-bw40-test`); details in docs/DEVICE_REFERENCE.md "Wi-Fi radio". `--wifi-ht40` does this
  at every boot and after channel changes (verified on BOX_1189B); `--wifi-ac` (80 MHz) would be cut the same way
  without it and cannot be combined with it yet.
- Boot-script changes (`etc/`, `usr/z-loader/bin/z-tesla-sh.sh`) need a **power cycle** after flashing: an update
  does not reboot the box.
- Box I/O limits: no shell; `adb push` does not land; `downloadLog.cgi` crashes the server that answers it.
  Read state only through the `check*`/`get*` CGIs (HTTP or HTTPS) and files the web root serves.
- Wi-Fi: adapter and car network on different 5 GHz channels caused 0.6–2 s stalls in DiPlay's tests
  (`docs/SMOOTH_WIRELESS.md`); check channels before blaming the player.

## Dev workflow

```sh
python3 -I tools/dev_player.py      # proxy https://www.zbox.link:8443 -> box, prints the Chrome command
```

The Chrome command maps `www.zbox.link` → 127.0.0.1 (proxy) and `zbox.link` → box (WebSockets), with
`--no-proxy-server` (a system SOCKS proxy that isn't running breaks everything) and `--ignore-certificate-errors`
(self-signed dev cert in `build/devcert/`). `player/*.js` is served live from disk; `tools/player_patches.py` changes
need a server restart. Debug via `--remote-debugging-port` and CDP.
