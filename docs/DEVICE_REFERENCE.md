# Device reference

Technical observations and historical device status for the Ownice T3 adapter.
Read the sections relevant to the current task; follow the scope and authorization rules in
[AGENTS.md](../AGENTS.md). Historical observations are not a fresh device status check.

## How the box works (Ownice T3 / zjinnova, firmware 2.9.11, build 202402031913)

```
iPhone ──Wi-Fi + Bluetooth (CarPlay)──► BOX 3.3.3.3 ──Wi-Fi──► Tesla browser  https://www.zbox.link
                                          ├─ player web page (z-connman)
                                          ├─ video   wss :5002  (H.264 passthrough or JPEG)
                                          ├─ audio   wss :7002
                                          └─ control wss :9002  (INIT, touch, session state)
```

| Component | Role | Evidence |
|---|---|---|
| Allwinner **T113** (dual Cortex-A7, armhf), "KunoOS" Linux | platform | `etc/issue`, `libt113_*.so` |
| init scripts | network, udev, dbus, dnsmasq, **telnet** (untested), Bluetooth | `etc/init.d/S*` |
| `gocsdk` | Bluetooth stack; pairs phone, starts wireless CarPlay | `S80gocsdk`, `/root/goc/bt_conf.ini` |
| `wlan0` / `wlan1` | box AP (3.3.3.3, DHCP .10–.30, channel 149 "auto50") / optional client to the phone's hotspot for car internet | `udhcpd.conf`, `wlan-wifi2-start.sh` |
| `z-dnsmasq` | resolves `*.zbox.link` → 3.3.3.3 (`www.autoaibox.link` → 2.2.2.2) | `dig @3.3.3.3` |
| `z-connman*` (Go) | web server: picks site by Host header, CGI API, upload, TLS with the zbox.link cert | `usr/z-loader/config*.yml` |
| `z-tesla` | CarPlay receiver + WebSocket sender (libwebsockets). JPEG mode: T113 decodes the phone's H.264 and re-encodes JPEG. H.264 mode: passthrough. Mode persisted in `/data/local/tmp/local_is_h264` | strings: `is_h264_mode`, `t113_h264_decode_*`, `t113_jpeg_encode_*` |
| player (`web/`) | Vue bundle + worker; renders video, sends touch, home/settings UI | `player.html`, `assets/index.0917c25d.js` |

Which binary owns which socket is inferred from strings; the ports and passthrough behaviour were verified live.

### Web hosting

| Host header | Site |
|---|---|
| `3.3.3.3` | update page (`update-web`) |
| `*.zbox.link` | CarPlay player (`web`) |

TLS cert: CN `zbox.link`, SAN `zbox.link, www.zbox.link`, expires 2027-02-28. HTTPS only works under those names.

### Player ports (`web/assets/index.*.js`)

| Purpose | HTTPS | HTTP |
|---|---|---|
| control | `wss://:9002` | `ws://:9112` |
| audio | `wss://:7002` | `ws://:7112` |
| video | `wss://:5002` | — |
| JPEG preview | `:8002` | `:8112` |

### Control protocol (JSON over the control socket)

Message ids: INIT=2, VIDEO_FOCUS_RESPONSE=3, REQUEST_VIDEO_FOCUS=5, TOUCH_INPUT=8, SESSION_STATE=11, AUDIO_INFO=12,
HEARTBEAT=13, UPDATE_INFO=14, AP_INFO=15, AP_INFO_SET=16, RUNTIME_STATE=20, WIDGET_MSG=21, SET_LINK_TYPE=24,
RECOVERY=25, MESSAGE_REFRESH=32, MESSAGE_REQUEST_VIDEO_CONNECT=34, MESSAGE_IS_SETTING_PAGE=37, MESSAGE_IS_H264_MODE=38.
Session states: WAIT_INIT=0, STARTED=4, END=6, VIDEO_LOST=8.

INIT fields the player sends: `width`, `height` (16-aligned width), `fps`, `is_h264_mode`, `is_right_hand`,
`apple_wireless`, `android_wireless`, `aa_density`, audio format. `UPDATE_INFO` returns the negotiated size.
**There is no keyframe-request message**; a fresh video socket starts with SPS/IDR.

Video frames are bare Annex B H.264 (no timestamp header) or JPEG (`FF D8`).

### CGI API (`http://3.3.3.3/cgi-bin/…`)

`checkver` (`ver`, `ver2` = web version), `checkchannel`, `checkband`/`setband`, `getwifimode`/`setwifimode`,
`getapchannel`/`setapchannel`/`setapcountry`, `getchannellist`, `checkname`, `downloadLog`, `doreboot`,
`upload` (POST multipart; saved as `/misc/update_tesla.zip`).

### Update flow

1. Update page asks `http://updates.zhijian.link/updates/zsender?chn=tesla-linux` → 302 to
   `update-1251259776.cos.ap-shanghai.myqcloud.com/tesla/tesla.json` → `{webver, url, md5}`.
2. If `webver != current` (string compare, so any custom version makes the vendor re-offer stock): download,
   MD5 check, POST to `upload.cgi`.
3. `/usr/z-loader/update.sh` unzips to `/misc/update-file` and runs the package's `update.sh` (stock: `cp ./* / -fr`).
   LED on PB6 blinks meanwhile.
4. No signature check in the shell path; `z-connman` has `systemVerify`/`rsaDecryptOk` symbols — whether uploads are
   verified is **unconfirmed** (nothing has been flashed yet).

## Recovery / flashing channels

- **`tools/flash.sh` is the deploy + rollback tool.** `update` uploads `build/update_tesla.zip`; `rollback`
  downloads (MD5-checked) and flashes stock 2.9.11; `status` prints the version. It POSTs `file` (as `upload.zip`)
  plus `md5` to `/cgi-bin/upload.cgi` — exactly what the box's update page does — then waits for the reboot.
  A `{"code": 0}` response means the box accepted it and installs on its own.
- **Web UI at `http://3.3.3.3` is the underlying path.** The box runs `/usr/z-loader/update.sh` after upload. Our
  packages never touch `update-web`, `z-connman`, `update.sh` or boot scripts, so this path survives a bad
  web-only package.
- **ADB over USB** (gadget `18d1:0002`, `Google.Inc / Configfs ffs gadget`, started by `etc/adb_conf.sh` at boot):
  present but **not a usable flash path**. `adb push` writes files (reports success then a harmless socket `EOF`),
  but `adb shell <cmd>` is hijacked — adbd execs `/bin/login`, which rejects `-c`, so no command runs; `adb pull`/`ls`
  hang. Interactive `adb shell` drops to a `login` prompt (root password unknown — don't crack it). adbd also starts
  late in boot, so ADB is no help for a true brick.
- **True brick** (won't boot): only Allwinner **FEL mode** (USB boot ROM + xfel/PhoenixSuit) would recover it, and
  that needs the full flash image, which we do not have and do not extract/redistribute. No SD-card slot on this unit,
  so `sdcard.sh` is not an option either.

## Status

- Repo public: github.com/alexcding/ownice-tesla-carplay.
- **3.0 flashed to the box and verified serving all patched files** (settings script, our worker with resync,
  `__zq` bundle patches, hover fix). `checkver` reports `3.0`. **Not yet road-tested in the Tesla itself.**
- Stock entry page loads Baidu analytics (`hm.baidu.com`) and the "enterFullscreen" action goes through
  `v.qq.com` / `1905.com` redirectors back to `www.zbox.link`.
