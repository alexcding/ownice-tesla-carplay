# HEVC black after browser refresh

Version naming: current releases use `2.9.11-oc8-hevc`, with `2.9.11-oc8`
as the default build base. Earlier `3.0-...` strings below identify historical
builds or the installed preview, not a different supported stock-firmware base.
See [README](../README.md) for current package names and installation commands.

**Latest status, 2026-10-22:** complete `3.0-oc8-hevc-preview.1` is installed,
including native compressed-focus retention and the separate browser cleanup
fix in `tools/player_patches.py`. Install completion and served browser files
are verified; an explicit reboot and post-install playback check remain pending.
A fresh-session HEVC refresh passed; the already-black session required a phone
reconnect, and extended idle/return remains pending. See the
[diagnostic record](HEVC_VIDEO_DIAGNOSTICS.md) for chronology and corrected
inferences. The original keyframe hook alone was insufficient.

Observed on Mac Chrome through the dev proxy: refreshing during HEVC playback
can leave the player black with `codec:""`. H.264 recovers in the owner's tests.
No Tesla browser was involved in this observation. An empty codec establishes
that the worker has not configured a decoder; it does not by itself identify
why parameter sets or a usable random-access frame are missing.

## Corrections to the earlier proposed fix

The original version of this document claimed that `sub_21778` replays cached
parameter sets and loops until an I-frame. Those claims were not supported:

- The short H.264 SPS-leading buffer path at `0x2185a`–`0x218b2` appends **one
  further queue entry**. It retries queue reads that return negative, but does
  not inspect the next NAL type or verify that it contains an I-frame. The log
  text `not get I frame` is not proof of a NAL classification check.
- The existing HEVC metadata hook at `0x2fb3e` reaches `0x238b4`, which emits
  dimension/control information. It does **not** cache VPS/SPS/PPS or IRAP frames.
- On a new video socket, `sub_21778` clears the old queue (when not connected),
  marks the new client active, sends the existing video-focus update and asks
  libwebsockets for a writable callback. There is no cached-GOP replay in this
  inspected path. `sub_21bd4` enqueues frames only while the client is active.
- Watching whether a black player eventually recovers cannot prove any cache
  mechanism: a new phone-generated IRAP could also explain recovery.

Widening the H.264 byte check alone is therefore not a confirmed solution. The
current converter already sends VPS/SPS/PPS together with every HEVC IRAP, so
there is no separate short VPS buffer that needs this H.264 merge path.

## Implemented reconnect recovery, 3.0-oc8

The receiver has an existing keyframe request API that was not called by its
CarPlay focus path. The new hook uses it:

1. A new video WebSocket executes `z-tesla`'s existing focus update at `0x2181c`.
2. `libzlink.so:libzlink_video_focus` (`0x17898`) always serializes/sends the focus
   message; it does not suppress identical values.
3. In `libzlink-main.so`, `video_audio_focus_handle` (`0x4df04`) dispatches valid
   video focus values for wired/wireless CarPlay (types 1/5) to `cp_video_focus`.
   That dispatcher also does not suppress identical focus values.
4. At `cp_video_focus`'s shared exit `0x5a498`, an exact four-byte replacement
   (`00bf0837`: `nop; adds r7,#8`) branches to our RX payload. The payload calls
   the original `cp_RequestKeyFrame` (`0x5a6bc`), restores r0-r11 and resumes the
   original epilogue at `0x5a49c`. r12 remains AAPCS bridge scratch. Existing
   resource/focus behavior runs first and remains intact.
5. The vendor wrapper checks the active CarPlay session at `0xf1118`. If non-null,
   it calls existing `AirPlayReceiverSessionForceKeyFrame(session, NULL, NULL)`;
   with no phone it does nothing. The API builds a `forceKeyFrame` command and
   sends it through the existing session command path. No authentication or
   transport changes are introduced.
6. When a new HEVC IRAP arrives, our existing ScreenStream converter prepends
   current VPS/SPS/PPS. A refreshed worker then has a fresh independently
   decodable picture and its configuration in the same access unit.

This requests a fresh frame instead of replaying a stale IRAP. Replaying an old
IRAP alone while omitting intervening reference frames would not establish a
valid prediction chain for the current delta frames. No GOP cache is added.

The request applies to CarPlay focus updates generally, including H.264 and
focus close/reopen transitions. It may add keyframe requests during those
transitions; there is no per-frame request loop. Android Auto and other focus
dispatchers do not use this hook. Phone acceptance, response timing and actual
refresh recovery remain device-validation requirements.

## Artifact identity and evidence

Stock firmware: Ownice T3 2.9.11 / build 202402031913.
The newly affected `libzlink-main.so` SHA-256 is pinned to
`9cba83a9297299dcacd3acd0d1e1fd1d73750c62f733f28dc77eb75df7452e2e`.
Other binary identities remain pinned in `tools/native_hevc.py`.

REA/Hopper evidence (static observations; provider pseudocode is not source):

| Finding | Evidence |
|---|---|
| Video socket connect/queue/merge behavior | `ev_cad6e2dc4f17a5828a91513624845eabfba18e221e221e1d456ab9472f1b0c9e` |
| Queue reader | `ev_a2f59da57640b55e2821ab446ab122999b4dfa52d967a02bd8a696b846b875bc` |
| Producer only enqueues while connected | `ev_296f1f28f78f782e0a108399f7970af6a39cfe689febf4887ab63b6b2ad21f49` |
| Metadata path is a dimension/control message | `ev_14d65dc11ab514a91f40b4126ea746f14117748f5e5581f03a00703b4fa23f23` |
| Original CarPlay focus callback | `ev_d4a259fece31f8e98cc9e04247d7ee5b3eccdb2bcb5a033987a536a8b0061474` |
| Session-aware keyframe request wrapper | `ev_1d626384fbb2c4631fc6753e1c51aea322635152e2b98ade4e676f94bdb8350a` |
| Existing keyframe command implementation | `ev_7a93ef1594f85cc678ef1cbce2618aeb272951d8016be6d75115785bda8b7e4d` |
| Focus dispatcher does not filter unchanged values | `ev_2556835ad4049c075a3654072c133f02811d4787093469266c7a51e481583e0d` |

LLVM 22 disassembly independently checked the focus sender, original patched
instructions and resume address. Hopper's function-body range and unresolved
indirect-reference limitations apply; no live phone request was captured here.

## Validation and recovery

`tools/test_native_hevc.py`: 25 passing ARM tests. New cases execute the patched
focus callback and original keyframe wrapper at two load addresses, verify
stack/r0-r11 preservation, repeated focus values, wired/wireless dispatcher
routing and a null session. The final API is mocked: these tests prove the
request invocation, not the phone's response. Existing real-HEVC decoded-pixel,
AVC fallback, codec-save failure and startup recovery checks remain covered.

The HEVC package now replaces **five** native files: `libAirPlay.so`,
`libScreenStream.so`, `libzlink-main.so`, `z-tesla`, and `z-link.sh`. Installer
backup expansion covers the added library; native restore restores all five.
There are ten exact instruction replacements across the four binaries, with
no new imports or dynamic relocations. Vendor artifacts remain under ignored
`build/` and must not be publicly redistributed.

Seven package tests also pass, including expansion of an existing four-file
backup to five files and restoration of the newly affected library. Player
settings/worker regression and syntax checks pass.

`3.0-oc8` update/native-test/restore/web packages are built locally. Native install
and restore require an explicit owner-triggered reboot before use. No upload,
reboot, phone reconnect, browser refresh or extra player connection was performed
for this work. Deployment needs separate owner approval under [AGENTS.md](../AGENTS.md).
See [NATIVE_ROLLBACK.md](NATIVE_ROLLBACK.md) and [CODEC_SWITCH_DESIGN.md](CODEC_SWITCH_DESIGN.md).

## Owner-authorized installation, 2026-10-22 13:37 UTC

Uploaded `build/update_tesla_hevc_3.0-oc8.zip` through the normal upload CGI
using `tools/flash.sh update`. Previous version: `3.0-oc6`; response: code 0,
`success`; subsequent read-only version checks reported `3.0-oc8`.
Package SHA-256: `471f3b9b291b8d60fb80a95addb6af1494704965c8e11c779e97615ae3326ea7`;
MD5: `71e59ed1b017db6995e53317e7ce91fc`; size: 2,087,095 bytes.

The native log reported `installed 3.0-oc8` and `REBOOT REQUIRED`; its unset
device clock showed 1970-01-01 02:11:53. No restore/error entry appeared in that
queried log. A later settings-file verification could not complete (TLS timeout),
and subsequent HTTP version / HTTPS log queries timed out too. This establishes
loss of HTTP/TLS responsiveness at that point, not its cause. Served settings
bytes and native activation were not verified.

No reboot, browser reload, new player connection or phone reconnect was initiated.
Chrome refresh recovery remains unverified until the owner reboots and tests
HEVC. The native package defaults to H.264; HEVC must be selected through the
native-capable player before phone reconnect. The dev proxy now detects the
codec-switch package on the box and exposes this selection automatically (the
former `--native-hevc` proxy flag is removed).
Rollback package: `build/restore_native_3.0-oc8.zip`, followed by an explicit
owner-triggered reboot.

## Reconnect still black after reboot: live diagnostic, 2026-10-22

After the owner rebooted and tested, the first HEVC connection drew video, and a
browser refresh went black again. Read-only inspection of the refreshed page's
worker (Mac Chrome through the `:8443` dev proxy; nothing was sent to the box),
sampled over ~24 s:

- `socket.readyState == 1` — the video WebSocket is open — yet `lastInput` never
  advanced: **zero bytes reached the worker** after the refresh. `waitingForKeyFrame`
  stayed true and no decoder was configured (`codec == ""`).

This reframes the failure. The symptom is **no data delivered to the reconnected
video socket**, not an observed stream flowing without a random-access frame.
The oc8 keyframe hook did not recover this owner's refresh test. This observation
does not establish whether its request ran, succeeded, reached the phone, or
produced a frame that was subsequently lost. It therefore does not prove that a
keyframe request can never recover delivery; native checkpoints are needed to
identify the failing stage.

Inference (not independently reconfirmed here): the owner reports H.264 survives a
refresh, i.e. the box *does* deliver to a reconnected socket for H.264 but not for
HEVC. The passthrough queue is codec-opaque, so the HEVC-specific element is the
new-socket priming/trigger path — the H.264-only SPS test at `0x2185a` in the
sender (`sub_21778`), and/or the enqueue gate in `sub_21bd4`. The open question is
why **zero HEVC bytes** reach a freshly-connected client when H.264 bytes do.

Caveats: observed only on Mac Chrome through the dev proxy; "no bytes over ~24 s"
is a bounded observation, not a proof of permanent silence; the worker never saw a
VPS after the refresh (`hevc == false`), so the undelivered stream's codec is
inferred from the pre-refresh session, not re-observed. The next investigation
should target the new-socket **delivery** path with this symptom in mind, not
keyframe availability.

An opt-in, offline-tested diagnostic package now instruments the request result
and three delivery checkpoints. See [HEVC_VIDEO_DIAGNOSTICS.md](HEVC_VIDEO_DIAGNOSTICS.md)
for fields, limitations, validation and rollback. It has not been deployed.

Update: the native-only diagnostic package was subsequently installed with owner
approval. See the installation record in HEVC_VIDEO_DIAGNOSTICS.md; reboot and
on-device checkpoint validation remain pending.

## Follow-up with the on-screen overlay

Read-only inspection of the existing Chrome page after the owner confirmed the
JavaScript work showed mode 1, `avc1.640028`, one configuration/keyframe and 707
received video messages (23,255,040 bytes). A later sample had 715 messages, so
the current H.264 socket continued receiving occasional input. Zero instantaneous
fps on that session is not evidence of the HEVC refresh failure. The owner was
asked to select HEVC, reconnect the phone, refresh once and retain the failed
page; a new HEVC failure has not yet been observed in this follow-up.

The adapter's native install log still lists oc8 and no diagnostic installation.
The diagnostic request file returns HTTP 404 when read with the expected
`Host: www.zbox.link`. This is consistent with checkpoints not being installed;
absence alone would not prove their code never ran. Direct IP-host requests also
returned 404 for the existing install log, so diagnostic HTTP requests must use
the expected Host header.

Additional stock-binary observations (REA/Hopper, same pinned hashes):

- `z-tesla:0x21778` sets stored video focus to 1 when its current video socket
  closes. On establishment it activates the socket and sends the stored focus.
  Evidence `ev_33d715445bbcd0c304d4e0281d27b4c4058abd9b4152959afacdd65e5450bce9`.
- `libzlink-main.so:0x5a3bc` calls the existing resource-mode API for either focus
  value and calls `cp_RequestUI_current` on the zero path. Assembly confirms
  different argument sets; pseudocode alone omits these arguments. Evidence
  `ev_881984b8fe8c51bc8f757131ed26066bbeab3acf425dec11da0898c620077cdc`.
- The enqueue gate remains the connected flag (`ctx+0x6c`), with no codec test
  in this helper. Evidence
  `ev_0afe29b541c72aa36110ca82f13ec3b01d027f5f8e1016416206c7fca9f98cc7`.

These observations identify focus/resource transitions and delivery gates to
measure. They do not establish a new root cause or justify widening the H.264
SPS test. No JavaScript changes, focus commands, reload, upload or reboot were
performed in this follow-up.

### Controlled HEVC refresh reproduction

After the owner selected HEVC, the page initially showed selected mode 2 but
continued decoding H.264. A later sample reported box mode 2 during the phone
transition. Reconnecting from the phone then restored confirmed HEVC:
`hvc1.1.6.L120.B0`, approximately 58 fps, decoder queue 0, box mode 2.
The existing socket had received 450 messages / 5,341,009 bytes at that sample.

The agent then performed one normal reload of the existing Chrome page using
CDP `Page.reload` (no new player target). The refreshed worker's sample at
socket age 11 seconds showed:

- Video socket open, one open, **0 messages and 0 bytes**.
- Input age -1 (never), zero parameter sets, zero keyframes, empty codec.
- Selected mode 2, box-reported mode 2, session_state 4.

This independently reproduces the no-input symptom from a confirmed working
HEVC baseline. The decoder cannot be the initial cause of zero WebSocket
messages. It still does not identify the stopped native stage or establish
whether the phone received a keyframe request. Native-only checkpoint deployment
remains pending owner approval. No JavaScript edits, upload or reboot occurred.

REA's browser scenario provider returned an execution failure before this
reload; unchanged requests were not retried. The subsequent CDP reload returned
success, and the new player URL timestamp/socket counters confirmed the reload.
