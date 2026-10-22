# Ownice T3 firmware improvement survey

This community project covers the whole adapter: Wi-Fi, Bluetooth, CarPlay,
Android Auto, audio/video, native services, browser playback, diagnostics,
updates and recovery. The goal is to keep existing hardware useful and produce
reproducible improvements for other owners.

## Scope and confidence

Survey date: 2026-10-21, America/Toronto. Base archive: vendor
`update_tesla0817.zip`, MD5 `3a743be56b60df524825cffcf45238cd`.
Packaged platform version: `202402031913`; web version: `2.9.11`.

Inventoried all 434 non-directory archive entries (168,025,914 uncompressed
bytes). Reviewed the boot entry points, active network/service launch scripts,
selected network/audio/server configurations, browser entry points, player
socket/audio paths, update UI, installer and certificate-refresh script. Compared
those with our player patches at commit `5e8868d`; subsequent main-branch player
optimizations are not re-audited here. Native HEVC analysis is recorded in
[HEVC_RESEARCH.md](HEVC_RESEARCH.md).

The [receiver update comparison](RECEIVER_CANDIDATES.md) adds follow-up inspection
of newer T2C firmware and GitHub projects. AirPlay/320.17.6 was found, but its API
differs from the T3 receiver and its HEVC capability remains unconfirmed. LIVI and
CatPlay are source-port candidates, not verified T3 update packages.

This is a broad static survey, not a full decompilation of every executable or
an exhaustive bug list. The archive is an update payload, not necessarily a
complete root filesystem. Included scripts and configurations can be unused,
overridden or changed at runtime. No live device state, radio topology, CPU/RAM
usage, audio latency or connection timing was measured. No device changes were made.
Keys, certificates and opaque accessory identity data were excluded from content
inspection. Vendor files remain outside Git.

**Observed** means a concrete packaged code/configuration behavior. Its impact
still needs testing. **Investigate** means a plausible opportunity whose active
path, cost or compatibility is unverified. Priorities below order the work, not
security severity: P1 = first reliability improvements; P2 = subsequent work;
P3 = larger research or optimization.

Paths below are relative to the firmware archive unless explicitly marked repo.
Script line numbers refer to this pinned archive. JavaScript function names
refer to this specific minified build.

## Wi-Fi and internet sharing

| ID | Priority / confidence | Evidence and opportunity | Required validation |
|---|---|---|---|
| W1 | P1 / Observed | `etc/wlan-softap.sh:39–77` waits indefinitely for a driver procfs node and then an IPv4 address. `rcS:59` calls it synchronously before launching the receiver and web servers. Add bounded readiness checks, useful failure status and a recovery path so a radio failure does not indefinitely block later startup. | Missing radio, failed module load, delayed interface creation; preserve access to diagnostics when AP startup fails. |
| W2 | P1 / Observed | `etc/check-hostap.sh:51–64` resets AP settings to channel 149, country CN and auto50 after repeated hostapd failures. Preserve the owner's region and select a fallback from the device's supported local channel list. | Repeated hostapd failures on each supported region/board; compare saved settings with active radio settings after recovery. |
| W3 | P1 / Observed | `etc/wlan-wifi2-start.sh:44–47` substitutes SSID and password directly into `sed` replacement expressions. Slashes, ampersands and other characters can corrupt configuration. Generate properly escaped supplicant values without exposing credentials in logs. | Spaces, quotes, slashes, ampersands, backslashes and non-ASCII SSIDs; confirm literal values and successful association. |
| W4 | P1 / Observed | The same script uses global `killall -9 udhcpc`, starts another background supplicant, and unconditionally appends a MASQUERADE rule (`27,49–52`). Make setup repeatable with interface-specific process ownership and a single NAT rule. | Run reconnect/setup repeatedly; count DHCP clients, supplicants and rules; verify unrelated interfaces remain working. |
| W5 | P1 / Observed | `etc/check-hostap.sh:40–43` tests a process name, not AP readiness, association or DHCP service. A live-but-stuck AP is invisible to that check. Add staged AP/DHCP health reporting and targeted recovery. | Simulate an unresponsive hostapd and failed DHCP service separately; avoid restarting a healthy CarPlay link during an uplink outage. |
| W6 | P2 / Investigate | `etc/wlan-wifi2.sh:11–17` and `wlan-softap.sh:24–36` read the same SDIO device path but choose different driver/interface combinations. Confirm actual radio enumeration and module ownership before consolidating startup. | Read-only hardware/module/interface inventory on BOX_0333C, BOX_1189B and any other supported revision. Do not assume one driver rule fits every board. |
| W7 | P2 / Observed | `etc/hostapd.conf:10–18` enables 802.11n/WMM and HT40. `wlan-softap.sh:95–118` chooses HT40 direction from the channel. Both shipped drivers (`8821cs.ko`, `8733bs.ko`) contain VHT code and `hostapd-airplay` (v2.7) parses `ieee80211ac`/`vht_oper_*`; plain `hostapd` lives outside the update payload and is unchecked. `z-tesla` re-applies the channel at runtime (`platform_softAP_channel_refresh`: random channel in auto modes, on mode/country change and `MESSAGE_SOFTAP_REFRESH`) and retries until it matches, without VHT settings. Proposal implemented as opt-in `build_patch.py --wifi-ac` (repo `tools/wifi_patches.py`; mock-tested only). WPA3 is unchecked. **Update 2026-10-22 (observed):** the AP actually boots at 20 MHz because the RTL8821CS driver reports a 20 MHz channel switch at AP start; a runtime request through `/proc/net/rtl8821cs/wlan0/change_bss_chbw` gave 40 MHz and 150 Mbit/s (was 72) on one BOX_1189B (repo `docs/DEVICE_REFERENCE.md`). | Throughput, retries and p95/p99 latency with phone and Tesla connected, including hotspot sharing; preserve valid region/channel combinations. |
| W8 | P2 / Observed | `etc/udhcpd.conf` mixes local DNS with public resolver entries. `z-tesla-sh.sh:21` requests extremely large DNS TTL values. Investigate split-DNS and cache behavior so local player names remain reliable while internet connectivity changes. The daemon's handling of those TTL arguments is unverified. | Resolve local hostnames and public names across reconnects, DNS failures and hotspot changes; inspect actual reply TTLs. |
| W9 | P1 / Observed | `usr/z-loader/bin/checknet.sh:21–28` defines internet status using one Baidu request. A blocked or unavailable external site can mark the adapter offline despite a working local CarPlay link. Separate radio association, gateway/DNS reachability, internet access and phone projection status; reduce repetitive probes with backoff. | Local-only operation, working internet with that destination blocked, captive portal, DNS failure and hotspot disconnect. |

## CarPlay, Android Auto and Bluetooth

| ID | Priority / confidence | Evidence and opportunity | Required validation |
|---|---|---|---|
| C1 | P1 / Investigate | Wireless bootstrap spans `S80gocsdk`, `gocsdk.sh`, `S99btname`, `btname.sh`, `z-link.sh`, `z-tesla`, and receiver libraries. Add timestamps and stage-specific diagnostics for pairing, Wi-Fi handoff, session setup and first frame. Do this before guessing at reconnect delays. | Cold boot, paired reconnect, phone locked/unlocked, Bluetooth toggle, multiple paired phones and iOS/Android versions. |
| C2 | P2 / Observed | `etc/btname.sh:4–10` waits once for three seconds if a serial symlink is absent, then may write to `/dev/goc_serial` without rechecking readiness. Use a bounded readiness check and report naming failures. | Slow Bluetooth startup and absent serial device; verify naming does not create an unintended regular file or race the Bluetooth service. |
| C3 | P2 / Investigate | `usr/bin/gocsdk` is launched in the background by `gocsdk.sh`; that launcher has no restart supervision. Establish whether another component owns recovery before adding a watchdog. | Observe process exit and hang behavior; preserve pairings and prevent competing restart loops. |
| C4 | P2 / Observed | Player INIT requests `sample:44100`, stereo, 16-bit audio and a fixed Android Auto density derived from display height. Expose validated per-protocol profiles rather than assuming one density/audio request suits every phone. | Negotiated settings versus requests, map/text scale, touch mapping, Siri/calls/navigation and Android Auto reconnect. |
| C5 | P3 / Observed + Investigate | HEVC is blocked by an explicitly AVC-oriented native screen handler and AVC-only browser worker. Receiver feature negotiation and this unit's authentication compatibility remain unresolved. See the dedicated HEVC report. | Actual Tesla HEVC decode, compatible receiver API/ABI, unchanged HEVC forwarding, fallback and latency. |
| C6 | P3 / Investigate | Native CarPlay, Android Auto, AirPlay, HiCar and CarLife-related libraries are packaged. Presence does not establish that every protocol is enabled or useful on this board. Map active session paths and isolate failures before replacing or removing components. | Protocol-specific startup/callback traces and regression matrix; receiver/wrapper version changes alone do not prove protocol improvements. |

## Audio and browser playback

The loaded player bundle is `usr/z-loader/zjinnova/web/assets/index.0917c25d.js`.
Its audio transport uses MediaSource AAC, not Web Audio PCM rendering.

| ID | Priority / confidence | Evidence and opportunity | Required validation |
|---|---|---|---|
| A1 | P1 / Observed | `cf()` adds one `updateend` callback for each arriving packet while SourceBuffer is busy. Multiple callbacks can then append while the buffer is busy again. Replace this with a single consumer queue, bounded buffered duration and explicit error handling. | Bursty audio, delayed append completion, quota exhaustion, long playback; no packet reordering or unbounded memory growth. |
| A2 | P1 / Observed | `Zo()` checks packet arrival every 200 ms and can reset the media element on a short silence. That path registers another anonymous document click listener after reinitialization. Use media-buffer/stream state, one gesture handler and explicit cleanup. | Music pauses, navigation-only audio, Siri, calls, intermittent Wi-Fi and repeated reset cycles; check listener count and interruptions. |
| A3 | P2 / Observed | `xi()` creates a new MediaSource object URL; no `revokeObjectURL` literal was found in the bundle. Pair URL creation with lifecycle cleanup and track buffered audio duration. This observation does not by itself establish a measured leak. | Many reconnects and background/foreground cycles; monitor memory, object URLs, playback recovery and A/V offset. |
| A4 | P2 / Investigate | `etc/asound.conf` includes mixer defaults of 1024 samples and 16 periods plus several soft-volume paths. The actual CarPlay-to-browser AAC path may bypass them. Trace the active audio path before tuning ALSA buffers. | Determine effective device/configuration, underruns and latency for music, navigation and calls separately; never assume configured defaults are active. |
| V1 | P1 / Observed | `cu()` retries all three sockets on a six-second timer; `tu()` and video startup schedule several empty timeout callbacks. Add explicit connection states, meaningful first-frame deadlines and independent recovery for control, video and audio. | Stalled open sockets, server restart, Wi-Fi loss and late recovery; avoid reconnect storms and loss of touch/session state. |
| V2 | P2 / Observed | A resize handler calls `wt()` directly, sending session initialization. Debounce viewport changes and determine which changes need a reconnect versus a render resize. | Tesla browser toolbar/fullscreen changes, rapid resizing and orientation; confirm negotiated dimensions and correct touch coordinates. |
| V3 | P2 / Existing work | Our worker and patches already add H.264 WebCodecs, Auto sizing, 60-fps requests, transfer of frame buffers, backlog resync, Smooth pacing and the mouse-hover fix. Validate and measure these before treating them as unfinished features. | Long motion/still sessions, sparse IDRs, reconnects and actual Tesla frame/latency measurements; never discard arbitrary H.264 delta frames. |

## Native services, boot and resource use

| ID | Priority / confidence | Evidence and opportunity | Required validation |
|---|---|---|---|
| S1 | P1 / Observed | `z-tesla-sh.sh:26–32` starts five web-server processes once; its loop supervises only `z-tesla`. `z-link.sh` restarts z-link every second after exit. Consolidate service ownership, readiness checks and bounded restart backoff. | Kill or stall each owned service independently; verify recovery without duplicate processes, session churn or hiding crash reasons. |
| S2 | P2 / Observed | All five packaged z-connman executables have identical SHA-256 hashes and are launched with different configs. Measure their RSS/PSS before considering one multi-listener server. Identical files do not imply five times the resident memory, nor that one process supports all listeners. | Per-process CPU/PSS, loaded configs, hostname routing, TLS and CGI behavior; compare boot and steady-state load. |
| S3 | P2 / Observed | Three regular archive entries contain identical 29,296,532-byte libprotobuf payloads; SSL/crypto and USB libraries are duplicated too. Evaluate alias-preserving packaging to reduce extracted size. Existing installed links/files and installer behavior must be checked first. | File type/permissions, dynamic loading and update/rollback semantics; quantify disk savings, not an assumed RAM saving. |
| S4 | P2 / Investigate | `rcS` attempts every `S??*` script. The archive includes PostgreSQL and telnet launchers, but this update payload does not establish that their executables exist or run. Inventory active services and remove unnecessary startup attempts only after dependency review. | Boot logs, listeners and process ownership; no privileged login attempts or authentication changes are needed for this survey. |
| S5 | P2 / Investigate | `rcS:37–42` aliases OpenSSL 1.0 sonames to 1.1 files; a packaged crypto library reports an OpenSSL 1.1.1 version string. Map actual runtime dependencies before attempting library upgrades or ABI cleanup. A version string alone is not a vulnerability assessment. | Loaded library paths and symbol requirements for every dependent receiver/server; stage a compatible replacement and rollback before device testing. |

## Updates, recovery, privacy and diagnostics

| ID | Priority / confidence | Evidence and opportunity | Required validation |
|---|---|---|---|
| U1 | P1 / Observed | `usr/z-loader/update.sh:27–40` reuses a staging directory and runs a package script after unzip without checking unzip success. The package installer copies payload files over the filesystem. Add clean staging, free-space/preflight checks, checked exit status and recoverable backups before native updates. Upload acceptance and signature verification inside z-connman remain unconfirmed. | Corrupt/truncated archive, full storage, stale staging, failed copy and interrupted install using a local disposable harness first. |
| U2 | P1 / Observed | The loaded update bundle `index.c78ff78a.js` displays success after upload acceptance, initiates reboot, then polls version. Distinguish accepted, installing, rebooting, verified and failed states. Other archived bundles differ; patch the entrypoint actually loaded. | Delayed install, accepted-but-failed update, timeout and unchanged version; never equate upload code 0 with a verified installation. |
| U3 | P1 / Observed | `bin/upcert.sh:12–44` relies on a Baidu probe, fetches metadata over HTTP, downloads an indicated archive with certificate checking disabled and copies its contents into the loader directory. Design authenticated, validated, narrowly scoped certificate maintenance with checked staging and expiry diagnostics. Do not reuse/distribute bundled private keys. | Offline/blocked probe, invalid metadata, failed/partial download, malformed archive, expiry and renewal; preserve the existing recovery UI. |
| U4 | P1 / Investigate | Native/configuration updates can affect networking and update access. The current web-only rollback description does not cover those changes. Preserve known-good networking settings and a tested recovery route per supported board before expanding release payloads. | Restore a previous package/settings from simulated failure; establish which recovery route survives each proposed native change. |
| D1 | P2 / Observed | The entry page loads Baidu analytics and sends fullscreen navigation through external redirectors. Remove optional analytics and investigate direct fullscreen with a graceful browser-supported fallback. This is a reliability/privacy improvement; browser fullscreen behavior still needs testing. | Offline operation, blocked external sites, Tesla gesture/fullscreen restrictions and return navigation. |
| D2 | P1 / Investigate | Existing status files, CGI reads, service logs and our frame statistics provide pieces of diagnostics. Add an owner-readable health view and a redacted support export with board/firmware identity, connection stages, socket state, decode/buffer stats and restart counts. | Clear separation of saved versus active settings; exclude Wi-Fi passwords, identifiers unless necessary, private keys and phone content. |
| D3 | P2 / Observed | `z-link.sh:9` applies broad executable permissions to every matching entry in its bin directory each launch. Replace this with correct build/install permissions after verifying actual file requirements. | Service startup and update behavior on a local filesystem fixture, then authorized hardware testing; preserve necessary execution access. |

## Recommended implementation order

1. Establish diagnostics and board identity (D2, C1); capture a repeatable baseline
   for startup, first-frame time, Wi-Fi retries, audio interruptions and latency.
2. Fix hotspot configuration handling and recovery (W1–W5, W9). Keep each change
   isolated and reversible; test scripts with mocked commands before deployment.
3. Fix AAC append scheduling and media lifecycle (A1–A3), then socket recovery
   and resize behavior (V1–V2). These can mostly be developed through the proxy.
4. Make native/configuration package updates recoverable (U1–U4) before releasing
   broader firmware changes. The current builder remains web-only until explicitly
   extended and validated; broad project scope does not change its actual payload.
5. Investigate protocol compatibility/HEVC and measured resource optimization
   (C4–C6, S2–S5) once basic reliability and recovery are established.

For every community release, publish a compatibility matrix, source changes,
reproducible build/check instructions, measured results and rollback limitations.
Do not redistribute vendor archives, extracted vendor binaries or identity material.
Research, code changes, package building and owner-authorized deployment remain
separate steps.
