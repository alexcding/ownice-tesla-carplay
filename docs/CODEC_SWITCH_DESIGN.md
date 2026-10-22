# User-selectable video codec (JPEG / H.264 / HEVC)

Version naming: current releases use `2.9.11-oc8-hevc`, with `2.9.11-oc8`
as the default build base. Earlier `3.0-...` strings below identify historical
builds or the installed preview, not a different supported stock-firmware base.
See [README](../README.md) for current package names and installation commands.

Codec switch implemented in `3.0-oc7`; current release is `2.9.11-oc8-hevc`, adding
[refresh keyframe requests](HEVC_RECONNECT_FIX.md). The codec-switch/native diagnostic
candidate is now the complete `3.0-oc8-hevc-preview.1` package on the owner's adapter,
including browser cleanup and rollback validation fixes. Install completion is
verified, with explicit reboot and post-install playback pending; see
[diagnostics](HEVC_VIDEO_DIAGNOSTICS.md). Native installation and restore
require an explicit owner-triggered reboot before use. The earlier `3.0-oc6` native
package advertises HEVC unconditionally and does not implement this switch.
This targets only Ownice T3 firmware 2.9.11 / build 202402031913 and the four
SHA-256 identities pinned by `tools/native_hevc.py`.

## Owner-facing behavior

The native package's Video panel offers HEVC, H.264 and JPEG. The browser sends
`is_h264_mode` as an integer, preserving the existing protocol:

| Mode | Receiver advertisement on next phone connection | Video path |
|---|---|---|
| 0 / JPEG | H.264 capability only | H.264 local decode, JPEG encoding |
| 1 / H.264 | H.264 capability only | Compressed passthrough |
| 2 / HEVC | Adds HEVC capability | Compressed passthrough |

A new profile defaults to H.264 (1). Legacy boolean settings migrate to 1/0.
An explicit saved mode 2 is retained by the native package, but clamped to 1
by a web-only package. Invalid saved modes fall back to 1. The chosen setting
requests a capability; the actual `hvc1`/`hev1` or `avc1` stream is shown in stats.

**After installing or restoring native files, reboot the adapter.** Installation
and restore log `REBOOT REQUIRED`; neither attempts a partial live restart.
The independent `z-tesla` supervisor and the running `z-link.sh` shell must both
load their new generation. No reboot is performed by the package.

**Disconnect the phone, choose the codec, Apply & reload, then reconnect the
phone.** The page sends INIT when the control socket opens, independently of
phone connection. Reloading does not renegotiate an existing phone session.
Selecting JPEG while an HEVC session is still running sends HEVC to the stock
H.264-only local decoder and can produce a black screen until reconnection.
Keep one player connected: concurrent clients can contend for the stream and
last-written adapter setting.

The dev proxy exposes the same options automatically: when serving
`zq-settings.js` it reads the box's copy and sets `NATIVE_HEVC` only if the box
runs the codec-switch package (`NATIVE_HEVC = true` plus the `vmode` option,
3.0-oc7+). 3.0-oc6's unconditional HEVC receiver, a stock box, or an unreachable
box all get the H.264/JPEG menu. (The former `--native-hevc` flag is removed.)

## Native implementation and evidence

Stock `z-tesla` SHA-256:
`fabf68f52d56e487a591e1229c3e2fa3c9006a9efc91bb4af57c03f1bd501ed8`.
Addresses below are ELF virtual addresses, not file offsets.

- `0x1db40`: existing mode writer formats the integer and saves
  `/data/local/tmp/local_is_h264` through `0x20b46`, then runs the stock sync.
  Its shared exit `0x1dbaa` originally contains `mov r0,r3; adds r7,#0x48`
  (bytes `18464837`). An exact four-byte branch now calls `zq_mode_saved`
  with the stock result and saved mode, then resumes at `0x1dbae`. The original
  stack restoration and return remain in place; no entry trampoline is needed.
- After successful stock persistence, `tools/native/codec_mode.c` creates
  `/data/local/tmp/zq_hevc` for mode 2 and removes it for every other value.
  A failed original save leaves the flag unchanged. On save/flag failure the
  helper attempts to persist safe mode 1 directly through `0x20b46`, avoiding
  recursive calls to the writer hook. It records an error in `/zq-codec.txt`
  when writable; the Video panel fetches this diagnostic when opened. A later
  successful save clears it. Read-only/full storage can also prevent logging.
- Existing imports `fopen`, `fclose`, `unlink`, `access`, `fwrite`, `system` suffice. No imports or
  dynamic relocations are added. The containing directory already exists if the
  stock mode save succeeds. A final constant `system("sync")` follows the flag
  update/error recovery, in addition to the stock sync. The two files are not
  an atomic transaction across power loss; the startup guard below handles the
  unsafe JPEG/HEVC combination even if rollback persistence failed.
- `tools/native/carplay_hevc.c`: both `/info`'s `hevcInfo` and SETUP's
  `enabledFeatures:["hevc"]` are added only when the flag can be opened for read.
  An absent/unreadable flag disables HEVC advertisement. Existing delegate
  features and authentication/session checks are preserved.
- `0x218c2` loads context `+0x44`; `0x218c4` compares against zero, and
  `0x218c6` branches to local decode at `0x218dc` only for mode 0. Modes 1/2
  fall through to compressed passthrough. The separate calls at `0x21312` and
  `0x220ce` are sender call sites, **not additional mode comparisons**.
- Direct mode-writer call sites observed at `0x22d0a` (INIT) and `0x230d0`
  (runtime mode handler). Their previously ignored errors are handled by exact
  four-byte continuation hooks at `0x22d0e` (`0023fb60`) and `0x230d4`
  (`00231846`). A failure sets runtime context `+0x44` to 1; INIT's local mode
  at frame `+0x18` is also set to 1 so its later assignment at `0x22db6` cannot
  restore JPEG. The runtime handler returns the failure; INIT retains it in
  its existing status local. Successful mode changes preserve stock behavior.
- Startup mode reading has a shared exit hook at `0x1db30` (`18464037`),
  resuming the original epilogue at `0x1db34`. If the saved mode is 0 or missing
  but the HEVC flag is readable, the reader returns compressed mode 1. This
  prevents a reboot into local H.264 decoding while HEVC is advertised.
  These addresses were checked against LLVM 22 disassembly and exercised in
  ARM emulation; they are not claims of device execution.

REA/Hopper evidence for the writer's instructions, shared exit and incoming
references: `ev_46ddd8b10c801e98fea24e11cf206a38d51b4e64079509117af0866b2ecd1982`.
LLVM 22 disassembly independently verified the exact patched instructions,
continuation and video branch. Hopper does not expose complete function body
ranges; its reference inventory cannot prove there are no other file writers.

The flag is adapter-wide and persists wherever `/data/local/tmp` persists. Each
phone reconnect reads it afresh. If the phone connects before the browser saves
its selection, another reconnect may be necessary. Changing selection during
negotiation is unsupported; use the disconnect/apply/reconnect sequence above.

## Local validation and packages

- 30 ARM-emulator tests execute the compiled handlers, stock writer (mocked file
  helper/libc calls), original epilogue and original video mode branch. Shared
  mocked files link mode changes to both actual advertisement hooks. Tests cover
  transitions 1→2→1→2→0, missing flags, failed persistence/open/close/unlink,
  both caller error continuations, startup inconsistency recovery,
  register/stack preservation, relocated library loading, delegate fields,
  malformed streams, AVC fallback and real HEVC decoded-pixel equivalence.
- Eighteen package tests cover fresh install/restore, backup expansion, reinstall,
  changed files/corrupted backups and package contents. Successful native restore
  removes the flag and restores the five original native files.
- `test_settings_codecs.mjs` tests settings migration, invalid modes, numeric
  selections through the actual panel's Apply handler, reconnect guidance and
  live codec stats. `test_worker_codecs.mjs` covers AVC/HEVC regression behavior.
- All four release-check packages build with strict patch anchors and explicit
  `--version 2.9.11-oc8-hevc`: update, native-test, restore and web ZIPs under
  ignored `build/releases/2.9.11-oc8-hevc/`. Published assets are the complete
  HEVC update, native restore and checksums.

The owner has observed codec switching and HEVC playback on the adapter in Mac
Chrome. Controlled H.264/HEVC preference changes within an active HEVC session
also recovered after reload, while the actual decoder remained HEVC until phone
reconnection, as designed. This does not establish a pure H.264-session refresh,
JPEG restoration, watchdog recovery, extended idle reliability or Tesla playback.
The latest diagnostic record distinguishes completed observations from pending checks.

## Deployment and rollback

The initial implementation was validated offline; later owner-authorized installs,
reboot and Mac Chrome tests are recorded in the diagnostic report. Deployment requires the owner's explicit approval under
[AGENTS.md](../AGENTS.md).

The HEVC package replaces `libAirPlay.so`, `libScreenStream.so`, `libzlink-main.so`,
`z-tesla`, `z-link.sh`, web player/settings/bundle, TLS launcher/config and webversion.
Stock backups remain under `/misc/zq-native/stock`. The restore package includes its own validator, checks the saved manifest and
all listed backup checksums before replacing files, and restores the five stock native files and
clears the HEVC flag and codec error file. Reboot after installing or restoring
native files before relying on them. The web player remains and can play H.264; mode 2 is still
non-zero passthrough in stock `z-tesla`. Full stock recovery remains
`tools/flash.sh rollback`. See [NATIVE_ROLLBACK.md](NATIVE_ROLLBACK.md).
