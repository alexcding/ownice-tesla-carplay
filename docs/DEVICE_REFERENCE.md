# Device reference

**Current device status, 2026-10-22:** the owner's adapter reports
`3.0-oc8-hevc-preview.1`. The full update includes browser cleanup and rollback
validation fixes; served browser files match the package. Its explicit reboot and
post-install playback check remain pending. Previous HEVC 1328x658 playback and
refresh checks passed in Mac Chrome; long idle/return, Tesla playback and live
restore/watchdog recovery remain pending.
See [diagnostics](HEVC_VIDEO_DIAGNOSTICS.md) for package identities and observations.
Historical version entries below describe earlier installations.

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

The CGIs also answer over HTTPS (`https://3.3.3.3/cgi-bin/…`, `access-control-allow-origin: *`); the player's own
files are served only for the `www.zbox.link` host name (`https://3.3.3.3/player.html` is a 404).
**Do not call `downloadLog`** (serves `/misc/logs/log.txt` per `usr/z-loader/config.yml`): on 2026-10-21 it returned
nothing and took down the server that answered it, port 80 once and port 443 once, until a power cycle.
`checkband` returns `{"code": -1, "data": {"band": 0000}}` (not valid JSON).

### Update flow

1. Update page asks `http://updates.zhijian.link/updates/zsender?chn=tesla-linux` → 302 to
   `update-1251259776.cos.ap-shanghai.myqcloud.com/tesla/tesla.json` → `{webver, url, md5}`.
2. If `webver != current` (string compare, so any custom version makes the vendor re-offer stock): download,
   MD5 check, POST to `upload.cgi`.
3. `/usr/z-loader/update.sh` unzips to `/misc/update-file` and runs the package's `update.sh` (stock: `cp ./* / -fr`).
   LED on PB6 blinks meanwhile.
4. No signature check in the shell path; `z-connman` has `systemVerify`/`rsaDecryptOk` symbols — whether uploads are
   verified internally remains **unconfirmed**. Owner-authorized custom package uploads
   have succeeded; that observation does not establish the internal verification mechanism.

## Recovery / flashing channels

- **`tools/flash.sh` is the deploy + rollback tool.** `update` uploads `build/update_tesla.zip`; `rollback`
  downloads (MD5-checked) and flashes stock 2.9.11; `status` prints the version. It POSTs `file` (as `upload.zip`)
  plus `md5` to `/cgi-bin/upload.cgi` — exactly what the box's update page does — then waits until `checkver` reports the new version.
  A `{"code": 0}` response means upload acceptance; confirm install logs/version separately.
  Native packages require an explicit reboot after installation; the version marker alone
  does not establish which native generation is running.
- **Web UI at `http://3.3.3.3` is the underlying path.** The box runs `/usr/z-loader/update.sh` after upload. Our
  packages never touch `update-web`, `z-connman` or `update.sh`, so this path survives a bad package. Since
  `3.0-oc5` every package also ships `usr/z-loader/bin/z-tesla-sh.sh` (ChaCha20 TLS config for z-tesla only, or the
  stock script with `--no-tls-chacha`); `--wifi-*` options patch `etc/` boot scripts.
- **ADB over USB** (gadget `18d1:0002`, `Google.Inc / Configfs ffs gadget`, started by `etc/adb_conf.sh` at boot):
  present but **not a usable flash path**. `adb push` reports success, then `failed to read copy response: EOF`
  (exit 1); on 2026-10-21 a pushed `web/zq-adbtest.txt` was not served afterwards (404 while the player's files
  served 200), so the write most likely does not land (adbd may run as uid 2000: `adb_conf.sh` mounts ffs that way).
  but `adb shell <cmd>` is hijacked — adbd execs `/bin/login`, which rejects `-c`, so no command runs; `adb pull`/`ls`
  hang. Interactive `adb shell` drops to a `login` prompt (root password unknown — don't crack it). adbd also starts
  late in boot, so ADB is no help for a true brick.
- **True brick** (won't boot): only Allwinner **FEL mode** (USB boot ROM + xfel/PhoenixSuit) would recover it, and
  that needs the full flash image, which we do not have and do not extract/redistribute. No SD-card slot on this unit,
  so `sdcard.sh` is not an option either.

## Wi-Fi radio (static, firmware 2.9.11)

- `etc/wlan-softap.sh` picks the driver from SDIO device id: `0xb733` → `8733bs.ko` (BOX_0333C), otherwise
  `8821cs.ko` (BOX_1189B). Driver versions: 8821cs v5.12.0-8 (2020-10-15), 8733bs v5.14.1-46 (2022-01-13).
- Driver module defaults (`.data`): `rtw_bw_mode=0x21` (5 GHz up to 80 MHz, 2.4 GHz up to 40 MHz), `rtw_ht_enable=1`,
  `rtw_vht_enable=1` (8821cs). Country table maps CA → channel plan `0x2B`, CN → `0x48`, US → `0x76`.
- `z-tesla` changes the AP channel at runtime (`platform_softAP_channel_refresh`) with `hostapd_cli set channel`,
  `set hw_mode`, and `set ht_capab HT40±` **without brackets**, which hostapd's parser ignores, so the HT40
  direction from boot is kept.
- Observed on one owner's box (2026-10-21, macOS client): the AP runs **802.11n 20 MHz, MCS 7, 72 Mbit/s** on
  channels 161 and 44 (country CA, fresh boots, no neighbours on 36–48), although the config requests HT40.
- Diagnostics report (`--wifi-diag`, owner's box, channel 44, uptime 65 s): board **BOX_1189B** (RTL8821CS for the
  AP). `hostapd_cli status`: `secondary_channel=0`, `ht_caps_info=006e` (40 MHz capability bit still set),
  `ieee80211ac=0`. Driver: `cur_bw=0`, `union_bw:0`; its CA plan (`0x2B`) lists 36–48 and 149–165 without flags.
- **Cause: the AP driver reports 20 MHz at AP start.** `start_bss_network` (8821cs.ko) runs `rtw_ap_chbw_decision`
  and then `rtw_cfg80211_ch_switch_notify` (`rtw_cfg80211_allow_ch_switch_notify` always returns 1); hostapd's
  `hostapd_event_ch_switch` then sets `secondary_channel=0` and leaves `ht_capab` alone, hence `006e` + 20 MHz.
  hostapd's own 20 MHz paths were ruled out in the 2.7 code (`hostapd-airplay`, same version): its scan-failure
  fallback also clears the 40 MHz capability bit; `allowed_ht40_channel_pair` only checks that 48 exists, is enabled
  and that 44 is a valid HT40 start (`iw phy`: no flags on 36–48/149–165); `check_40mhz_5g` never refuses 40 MHz on
  5 GHz (it only swaps primary/secondary). Kernel regdomain is world `00` (5170–5250 @ 80, PASSIVE-SCAN), and the
  driver clears channel flags itself in `rtw_regd_apply_flags`. Why the start-up decision picks 20 MHz is unresolved
  (the function is ~200 KB of inlined code; the driver logs almost nothing at its default level).
- **Workaround, verified 2026-10-22 on the owner's box:** writing `44 1 1` (channel, bw 1 = 40 MHz, offset 1 =
  secondary above; `rtw_get_center_ch`: centre = ch + 2 for offset 1, ch − 2 for offset 2) to
  `/proc/net/rtl8821cs/wlan0/change_bss_chbw` ~65 s after boot switched the running AP to **40 MHz, centre 5230**:
  driver `cur_bw=1`, hostapd `secondary_channel=1`, AP stayed ENABLED with both stations associated. The macOS client
  then reported `44 (5GHz, 40MHz)` at **150 Mbit/s** (MCS 7), up from 72. The proc handler's format is
  `"%hd %hhd %hhd %hhx"` (ch, bw, offset, optional interface bitmap); it goes through `rtw_change_bss_chbw_cmd`,
  which validates against the channel plan and waits up to 2 s. It lasts until the next boot.
- Board inventory from the reports: `hostapd` is `/usr/sbin/hostapd` v2.7 (no `-f`/syslog option, so its messages
  are lost with `-B`); `/usr/sbin/iw` exists; the kernel log ring is small and the 2.4 GHz client floods it, so the
  AP's start must be captured within ~8 s of boot.
- The hotspot client `wlan1` is a **separate RTL8189ES** (2.4 GHz; `8189es.ko` claims SDIO id 8179, the AP driver
  claims C821/B821/C80C/8733), so its connection on channel 6 and its scans every ~6.5 s do not move the AP radio.

## Status

- Repo public: github.com/alexcding/ownice-tesla-carplay.
- **3.0 flashed to the box and verified serving all patched files** (settings script, our worker with resync,
  `__zq` bundle patches, hover fix). `checkver` reports `3.0`. **Not yet road-tested in the Tesla itself.**
- 2026-10-22: the owner's box runs `3.0-oc4-ht40` (oc4 player + Wi-Fi status line + `--wifi-ht40`), country CA,
  fixed channel 44. After a power cycle it came up at **40 MHz / 150 Mbit/s** on its own (macOS client).
  Leftovers from the diagnostics builds stay in the web root (`zq-wifi.txt`, `zq-diag-*.txt`, redacted) and
  `/usr/z-loader/zq-bw40-tried`. 802.11ac (`--wifi-ac`) is not flashed.
- 2026-10-22 (later): `3.0-oc4-ht40-tls` flashed and power-cycled: raw touch, frame-gap resync, and z-tesla's player
  sockets on TLS 1.3 ChaCha20 (verified on 5002/9002/7002). The owner reports the player as much faster.
  The same content is the `3.0-oc5` default build (`--wifi-ht40` stays opt-in).
- **An update does not reboot the box** (observed twice): `checkver` shows the new version at once, web files apply
  at once, but a changed `etc/` boot script only runs after a power cycle. Diagnostics v1 looked broken for that
  reason; its report (uptime 65 s) appeared after the next boot.
- Stock entry page loads Baidu analytics (`hm.baidu.com`) and the "enterFullscreen" action goes through
  `v.qq.com` / `1905.com` redirectors back to `www.zbox.link`.
