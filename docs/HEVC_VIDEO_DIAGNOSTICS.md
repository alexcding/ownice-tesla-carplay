# HEVC refresh delivery diagnostics

Status: owner-authorized native-only package installed; activation requires an
owner-triggered reboot and device validation remains pending. This is an investigation build,
not a confirmed fix for the black screen after refreshing Mac Chrome.

## Observed failure

On the owner's T3 running `3.0-oc8`, HEVC displayed after connecting the phone.
After a browser refresh the existing video WebSocket was open, but the worker
received no video input and configured no decoder. Only one player client was
connected. Sending video focus 0, cycling focus 1 then 0, and reopening only the
video socket each failed to restore input during the observation window. These
probes used the existing player; no second player was connected.

The failure is therefore upstream of browser decoding in this observation.
The immediate keyframe-request result and the point where delivery stops remain
unknown. See [HEVC_RECONNECT_FIX.md](HEVC_RECONNECT_FIX.md) for the prior static
trace and its limitations. A successful request return alone would not prove
that the phone acknowledged it or sent a frame.

## Build and affected files

`--native-hevc --native-video-diag` builds `3.0-oc8-vdiag`. Inputs are the stock
2.9.11 archive (MD5 `3a743be56b60df524825cffcf45238cd`); each native input is also
SHA-256 pinned in `tools/native_hevc.py`. Compatibility with other stock builds
or boards is untested.

The diagnostic changes affect `libzlink-main.so` and `z-tesla`, through
`stream_diag_main.c`, `stream_diag_sender.c`, `stream_diag.h` and opt-in hooks in
`native_hevc.py`. The full package also carries the existing HEVC changes in
`libAirPlay.so`, `libScreenStream.so`, and the `z-link.sh` watchdog. It includes
the current player/settings, including worker socket counters, and an updated
restore script that removes diagnostic files. The diagnostic hooks call existing
imports and preserve the intercepted arguments, stack, original prologues and
WebSocket write call. No new dynamic imports or writable executable segment are
added. With diagnostics disabled, all four generated native binaries remain
byte-identical to the deployed oc8 package.

## Reading checkpoints

Each file is under `/usr/z-loader/zjinnova/web/` and is available through its
same-named HTTP path. Each contains three eight-digit hexadecimal integers:

| File | First field | Second field | Third field |
| --- | --- | --- | --- |
| `zq-video-request.txt` | HU focus value | Session present (0/1) | Immediate keyframe-request return code |
| `zq-video-phone.txt` | Callback buffer length | First Annex B NAL header byte | Reserved (0) |
| `zq-video-sender.txt` | Callback buffer length | First Annex B NAL header byte | Mode in upper 16 bits, connected state in lower 16 bits |
| `zq-video-output.txt` | WebSocket write length | First Annex B NAL header byte | Reserved (0) |

The request hook is at the shared exit of `cp_video_focus` (`0x5a498`). It clears
the three frame checkpoints before issuing the same keyframe API used by oc8.
A missing session produces return `ffffffff`; other returns are the API's raw
32-bit result (interpret as signed when appropriate).

The phone checkpoint is at `cp_VideoData_cb` entry (`0x587fc`), after conversion;
its absence cannot distinguish phone silence from a failure before this callback.
The sender checkpoint is at `z-tesla` callback entry (`0x2fb0c`); the connected
state indicates enqueue eligibility, not proof of successful queue insertion.
The output checkpoint is immediately before the original write at `0x218d6`;
it proves a write was attempted, not that the browser received it. No Annex B
prefix yields header `ffffffff`. Only lengths, the first header byte and scalar
state are saved; no frame payload or picture is recorded.

Frame checkpoints record only the first callback after a focus update. They are
best-effort snapshots, not an atomic event history: another focus update resets
them, callbacks can race, and failed file operations can leave missing or stale
files. The per-frame existence check and occasional file writes may affect
timing. Use this build temporarily to compare a working phone connection with a
failed refresh, then remove diagnostics.

## Validation and recovery

- 28 ARM emulation tests pass, including injected request failures, no-session
  behavior, checkpoint resets, original argument/register/stack preservation,
  and preservation of the original WebSocket write and return value.
- The packaged native binaries match the emulation-tested diagnostic binaries.
- Eight package install/upgrade/restore tests pass, including stock backup
  expansion, corrupt-backup refusal, and removal of all four checkpoint files.
  The native-only install test preserves independently edited player files and
  TLS launcher/configuration bytes.
- Settings and worker codec regression tests, JavaScript/shell syntax checks,
  and `git diff --check` pass.
- Device loading, checkpoint semantics under real concurrency, request response,
  and refresh recovery remain unverified for this diagnostic package.

Update: `build/update_tesla_hevc_3.0-oc8-vdiag.zip`, 2,089,516 bytes,
SHA-256 `058a6c0bc31bf002f5f9910447bb8df47b31dd82fac008a5821271d87e85dfc3`.
The owner has assigned JavaScript work to another session. Use the native-only
variant for this investigation:
`build/update_tesla_native_3.0-oc8-vdiag.zip`, 1,975,089 bytes, SHA-256
`5121c30fe33200716378cee9e55130190f6ba4863ee8a45068b2fa8f16ddeea8`.
Build it with `--native-hevc --native-video-diag --native-only`. Its contents
are restricted to the five native-list files, installer, native metadata,
restore script and web-version marker; it preserves installed player and
TLS/Wi-Fi files. All included bytes match the validated full diagnostic package.
Restore: `build/restore_native_3.0-oc8-vdiag.zip`, SHA-256
`9ffa0beff65c14204b359dcb6d8aaa9845241d2e200c4271a8dfd9bae99dba9b`.

Installation requires separate owner authorization and an owner-triggered reboot.
The restore package uses the saved stock native files and also requires a reboot;
it removes the HEVC flag and diagnostic files. It depends on a complete, intact
backup and a responsive updater. Full stock rollback remains available through
`tools/flash.sh rollback` when the normal update path is working. No bootloader,
kernel, authentication bypass, private key or vendor archive publication is involved.

## Owner-authorized installation, 2026-10-22

Uploaded the native-only package through `tools/flash.sh update` after explicit
owner approval. The updater returned code 0 / success; the subsequent version
query reported `3.0-oc8-vdiag` (previously `3.0-oc8`). The native install log
reported `installed 3.0-oc8-vdiag` and `REBOOT REQUIRED`; the device's unset clock
showed 1970-01-01 01:16:49. Uploaded package identity matches the native-only
SHA-256 above. No reboot was issued, and these checkpoints have not yet been
validated on the running device. After reboot, compare working HEVC playback
against one browser refresh before reconnecting the phone again.
