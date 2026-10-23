# HEVC refresh delivery diagnostics

Version naming: current builds are plain `2.9.11-ocN` (now `2.9.11-oc12`; see the
oc12 section at the end). Older `-hevc`/`-hevc-neon` names are historical. Earlier `3.0-...` strings below identify historical
builds or the installed preview, not a different supported stock-firmware base.
See [README](../README.md) for current package names and installation commands.

Current status: complete `3.0-oc8-hevc-preview.1` is installed with owner authorization,
including the browser cleanup and rollback validation fixes. The version, install
log and served browser-file bytes are verified; the required explicit reboot has
not been performed. Prior HEVC playback/refresh checks passed in Mac Chrome.
Post-install playback, extended idle/return, Tesla playback and live restore/watchdog
recovery remain unvalidated. The record
below is chronological; later control-message captures correct earlier activation
inferences.

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

## Active-device refresh results

After the owner's phone reconnect, all four checkpoint files were readable:

| Checkpoint | Raw fields |
| --- | --- |
| request | `00000000 00000001 00000000` |
| phone | `00000789 00000002 00000000` |
| sender | `00000789 00000002 00020001` |
| output | `0000114c 00000040 00000000` |

The existing worker reported HEVC `hvc1.1.6.L120.B0`. These snapshots establish
that diagnostic code is active and normal callbacks/write attempts occur after
phone reconnect. They do not reconstruct the earlier blackout because focus
updates reset the frame checkpoints.

The agent then reloaded the existing player once. At socket age 17 seconds the
worker had an open video socket, zero messages/bytes/configurations/keyframes,
empty codec, and selected/reported mode 2. The request checkpoint again read
`00000000 00000001 00000000`; phone/sender/output returned HTTP 404. At socket age
122 seconds, worker input remained zero. Thus the hook ran with an active
session, and its keyframe API returned immediate success, but no post-request
frame reached the receiver callback during this observation. This places the
failure before `cp_VideoData_cb`, not specifically in the sender queue.

Immediate success is not a phone acknowledgement. Static inspection of
`AirPlayReceiverSessionSendCommand` confirms it queues an HTTP command and uses
a separate completion path when a callback is provided. The current diagnostic
call provides no completion callback. Frames might be absent on the wire or
rejected before `cp_VideoData_cb`; the checkpoints do not distinguish these.
REA/Hopper evidence: `ev_042c62554fad87e1810b915d98a3dbcf18f942bf3c792d6647336ee99c64f84e`.

## Opt-in focus retention experiment

Candidate build `3.0-oc8-vdiag-focus` retains the prior video-focus value when
the current browser video socket closes in numeric mode 2. It still marks the
browser disconnected and retains existing queue and session-close handling.
New sockets still run the original focus update and the oc8 keyframe hook.
Explicit control-channel focus requests remain intact. Modes 0, 1 and other
values execute the original focus-release path. The condition is the selected
numeric mode, not a separate test of phone platform.

Hypothesis: avoiding the browser-close resource-release/reacquire transition
may prevent the HEVC stream from becoming stuck. This has not been proven on
hardware. It does not fix a converter failure or establish that the phone
honors `forceKeyFrame`. It may leave the phone encoding after the last browser
closes, increasing phone/Wi-Fi activity until the phone session ends or an
explicit focus change occurs. No frame cache or stale IRAP replay is added.

The only additional instruction replacement is `z-tesla:0x219a8`, originally
`83f84820` (`strb.w r2,[r3,#0x48]`). The new leaf checks context mode at `+0x44`:
mode 2 skips to `0x219ba`, preserving the prior focus byte; other modes replay
the store and resume at `0x219ac`. The client-active flag was cleared at
`0x21998` before this site. Instruction evidence:
`ev_705ca5c7326e8c7e52bfafbdbfe9b91c1136dcb7367f6b5d7c8d75d53ed5cb67`.

Build with `--native-hevc --native-video-diag --native-only --hevc-keep-video-focus`.
Default packages do not enable this experiment. Thirty ARM tests and eight
package tests pass, including the complete current-client close branch through
focus dispatch, preservation of prior focus in mode 2, original mode 0/1 paths,
and original stack/live-register behavior at two load addresses. The native-only
package contains no player files and its binaries match the tested payloads.
Only `z-tesla` differs from the installed diagnostic build's native binaries.

Candidate: `build/update_tesla_native_3.0-oc8-vdiag-focus.zip` (1,975,175 bytes).
SHA-256: `b43b5be5986b2681390a59f4e8b4317c0a6ffbc504579a8e0db4e91fbb007d9e`.
Stock restore: `build/restore_native_3.0-oc8-vdiag-focus.zip`, using the existing
saved backup (SHA-256
`0b3b275fa450801a42a7e5ea2ed1894a08a4da695688f6ea764707432a2bf295`).
Reinstalling the previous native-only `3.0-oc8-vdiag` package
removes the experiment while retaining diagnostics. Each activation or rollback
requires a reboot. Candidate deployment requires separate owner approval;
neither candidate upload nor reboot has occurred.

### Focus-retention package installation

Subsequently uploaded the native-only `3.0-oc8-vdiag-focus` package with explicit
owner authorization on 2026-10-22. The updater returned code 0 / success, and the
version endpoint changed from `3.0-oc8-vdiag` to `3.0-oc8-vdiag-focus`. Package
SHA-256 matched the candidate identity above, and the ZIP contains no player
files. No reboot was issued. Activation and HEVC refresh recovery still require
the owner to reboot, reconnect the phone, then test a browser refresh without
another phone reconnect until checkpoints are collected.

### HEVC refresh recovery observed on Mac Chrome

After selecting HEVC and reconnecting the phone, the existing Chrome window
reported `hvc1.1.6.L120.B0`, box mode 2 and a configured decoder. The agent then
performed one normal reload of that same window, without reconnecting the phone
or opening another player. At socket age 8 seconds the refreshed page reported:

- HEVC `hvc1.1.6.L120.B0`, 1328x658, selected/reported mode 2.
- Eight video messages, 179,330 bytes, two parameter-set observations and two
  keyframes; `waitingForKeyFrame` false and no decoder backlog.
- The owner independently confirmed: browser refresh works and the picture
  returned.

This is positive on-device evidence for the focus-retention candidate, replacing
the previous all-zero-input result for this controlled refresh. It establishes
recovery in this Mac Chrome session, not exhaustive refresh/reconnect reliability
or Tesla-browser compatibility. A zero instantaneous fps after the initial burst
on a static screen does not negate the received configuration/keyframes or the
owner's visual confirmation. Long-term behavior when the browser remains closed,
phone energy use, other phone/OS versions and Android Auto remain unvalidated.

### Codec preference transition exposed another blackout

The owner then selected H.264 while the phone had been sending HEVC. The new
page selected mode 1 but initially displayed the earlier box-mode-2 report.
It received zero video messages. One further normal browser reload did not
recover: at socket age 29 seconds the selected and box-reported modes were both
1, with zero messages/bytes, no parameter sets/keyframes and no decoder codec.
The keyframe request log remained `00000000 00000001 00000000`, while all three
frame checkpoints were missing. No phone reconnect was performed for this test.

The original focus experiment has a mode-transition flaw: it decides whether to
retain focus using the current selected integer, which is not the active phone
codec. A preference change to 1 can therefore enable the original browser-close
focus-release path while the existing phone session is still HEVC. This fits
the observed failure; the frame checkpoints do not independently show the wire
codec or prove the phone's internal reason for stopping.

Candidate `3.0-oc8-vdiag-focus2` extends retention to both supported compressed
modes, 1 and 2. JPEG mode 0 and unsupported values retain the original close
behavior. This permits an existing compressed session to keep displaying during
an H.264/HEVC preference change; it does not renegotiate the codec. The codec
preference still applies on the next phone connection. The phone may keep
encoding with the browser closed in either compressed mode. JPEG transitions
from active HEVC still require disconnecting the phone before changing mode.

The same hash-pinned `0x219a8` site is used. A mode-1 comparison is added before
the mode-2 comparison, preserving their equality flag through the register pop.
Thumb disassembly and 30 ARM tests verify the generated code, prior-focus-byte
preservation, stack/r0-r11 preservation and current-client deactivation for both
compressed modes. Eight install/upgrade/restore tests pass. Packaged native
bytes match the tested payloads; only `z-tesla` differs from the installed focus
candidate's native binaries. There are no player files in the native-only ZIP.

Build with `--native-hevc --native-video-diag --native-only
--keep-compressed-video-focus` (the older `--hevc-keep-video-focus` spelling is
retained as an alias). The generated suffix is now `-focus2`; defaults still
leave focus retention disabled.

Update: `build/update_tesla_native_3.0-oc8-vdiag-focus2.zip`, 1,975,204 bytes,
SHA-256 `e8aa539ca94ac363db6b0deb34278de32b110af2af9596b39515d7cd0075d467`.
Restore: `build/restore_native_3.0-oc8-vdiag-focus2.zip`, SHA-256
`ae1eb360583dbb540742e4ec28484d6116d8db8b442fafb851d87bd954150245`.
The previous focus and diagnostic packages remain available for native rollback.
Installation and rollback require explicit authorization and a reboot. This
candidate is built and locally reviewed/tested only; no upload or reboot has
occurred, and mode-transition recovery remains to be validated on-device.

#### Focus2 installation

The owner subsequently authorized flashing `3.0-oc8-vdiag-focus2` on 2026-10-22.
Uploaded the native-only package after verifying its SHA-256 and absence of
player files. The updater returned code 0 / success, and the version endpoint
reported `3.0-oc8-vdiag-focus2`, previously `3.0-oc8-vdiag-focus`. No reboot was
issued. Activation and recovery validation remain pending the owner's reboot
and initial phone reconnect, followed by H.264 refresh and codec-transition tests.

#### Runtime activation check after another transition blackout

The owner clarified the sequence: the phone continued sending HEVC after the
H.264 preference was selected, with a visible picture; changing the preference
back to HEVC and applying then went black. The video socket was open but received
zero messages/bytes. The keyframe submission checkpoint was `[0,1,0]`; all three
frame checkpoints were absent. Installed version alone did not establish which
native process image was active.

A controlled probe of the existing black page tested mode-1 disconnect behavior
without another flash, reboot, phone reconnect or player client. The existing
control socket sent MESSAGE_IS_H264_MODE (38) with value 1. After 1.5 seconds,
the page's existing video worker received WsClose for its current socket. The
request checkpoint changed from `00000000 00000001 00000000` to
`00000001 00000001 00000000`: a mode-1 browser close still sent HU focus 1.
The probe then restored mode 2, waited, and reopened the same worker socket.

The mode handler itself only persists/sets mode; its inspected path contains no
focus update. The observed close behavior is what the mode-2-only guard permits
and focus2 is designed to suppress. This strongly indicates the newly installed
focus2 binary was not active in this process, rather than establishing a failure
of the corrected guard. Owner confirmation of an adapter reboot after the focus2
upload was requested but not received during this observation. Require an adapter
power cycle and repeat the transition before changing the patch again. Do not
treat the web-version marker as proof of native runtime activation.

The initial worker-inspector probe stopped without changing state: the worker
discovery URL was blank, and direct worker Runtime access subsequently timed out.
The completed probe used the existing page's Worker messaging interface, with
finally-handled restoration of mode and socket, instead.

#### Owner-authorized reboot request

After another failed refresh, the mode-1 probe was repeated with an additional
sample between setting mode 1 and closing video. The request checkpoint stayed
`[0,1,0]` after the mode change, then became `[1,1,0]` after closing the video
socket. Mode 2 and the socket were restored. This separates mode persistence
from the observed close-triggered focus release.

The owner then explicitly authorized an adapter reboot. A GET to the documented
`/cgi-bin/doreboot.cgi` endpoint returned an empty body with curl exit 0. The
adapter subsequently stopped answering read-only version requests, and the
existing Chrome worker reported a connecting socket rather than an open one.
Ten bounded version polls and a later retry timed out. The Mac still had a
route to the adapter through its existing gateway. These observations confirm
loss of reachability after the reboot request, not successful boot completion
or focus2 runtime activation. No further flash or reboot request was sent.
Physical power/network confirmation is needed before continuing validation.

#### Focus2 activation and transition validation after recovery

The adapter became reachable again and reported `3.0-oc8-vdiag-focus2`. A fresh
mode-1 disconnect probe now left the keyframe-request checkpoint at `[0,1,0]`
before mode change, after mode change, and after video close. This contrasts with
the earlier `[1,1,0]` close result and confirms the intended mode-1 retention
behavior in that probe. The probe restored mode 2 and the existing video socket.
The later control-message capture below limits this probe's ability to establish
native runtime activation independently.

The old black phone session still had no frames. The owner was asked to reconnect
the phone once; the page subsequently showed session end then session 4 with
working `hvc1.1.6.L120.B0`, 1328x658. From that fresh working baseline:

| Operation | Selected mode | Actual decoder after reload | Received messages / bytes | Parameters / keyframes |
| --- | --- | --- | --- | --- |
| Normal root-page refresh | 2 | HEVC `hvc1.1.6.L120.B0` | 7 / 176,850 | 2 / 2 |
| Select H.264 and reload player | 1 | HEVC `hvc1.1.6.L120.B0` | 4 / 90,060 | 1 / 1 |
| Select HEVC and reload player | 2 | HEVC `hvc1.1.6.L120.B0` | 8 / 180,120 | 2 / 2 |

Each refreshed decoder had `waitingForKeyFrame == false`, zero queue backlog
and a resized video canvas; no phone reconnect occurred between these three
operations. Preference changes used the existing profile's localStorage and
player reload, matching the Apply handler's operations. The final selection was
restored to HEVC. This tests preference transitions within an active HEVC phone
session, not an actual codec renegotiation or a pure H.264-session refresh.

A narrowly filtered CDP Network capture across the normal refresh observed two
outgoing REQUEST_VIDEO_FOCUS values of 0 and an INIT with `is_h264_mode:2` and
`is_hu_video_focus_on:0`; no outgoing focus release was observed in that capture.
No video payloads, general control contents or credentials were recorded. The
bundle contains a conditional INIT focus value and a cleanup focus-release path,
but this trace does not implicate either in the successful refresh. No further
JavaScript focus patch was made.

These results demonstrate the reported HEVC preference-transition sequence on
this Mac Chrome/adapter session. They
do not establish exhaustive reconnect reliability, long-term phone power use,
Android Auto behavior or Tesla-browser compatibility. No additional flash was
needed for those successful transitions.

#### Blackout after leaving and returning: browser cleanup focus release

The owner subsequently reported leaving and returning to a black screen, without
an intentional codec change. The selected and persisted modes were both 2; the
video socket was open but had received zero messages/bytes and configured no
decoder. The request checkpoint was `[0,1,0]`; receiver/sender/output checkpoints
were absent. This establishes no video delivery, not a decoder failure. The
precise event during the absence remains unknown.

A repeated temporary mode-1/socket-close probe now captured only whitelisted
outgoing control fields on the existing browser socket. Before and after setting
mode 1 the checkpoint was `[0,1,0]`. After video close it was `[1,1,0]`, and the
browser had sent both `{message_id:38,is_h264_mode:1}` and
`{message_id:5,video_focus_request:1}`. The probe restored mode 2 and reopened the
existing video socket. No additional player client, upload or reboot was used.

This corrects the earlier inference that a close-triggered `[1,1,0]` necessarily
proved an old native binary was running: browser cleanup can itself send an
explicit release, outside the native socket-close guard. Conversely a retained
checkpoint alone does not independently prove which native code is active.

The stock bundle's `cn()` cleanup calls `Xn(false)`, which sends that explicit
release. Worker resync calls cleanup, and control reconnect/health-check failure
paths also reach it. The local bundle patch now suppresses that cleanup release
when `__zq.h264` is true (numeric mode 1 or 2). It retains socket teardown and
state reset, JPEG cleanup release, and all explicit acquire/release controls.
This complements the native focus guard; it does not replace it. Retaining
compressed video focus can keep phone video activity running during recovery.

`tools/test_player_focus.mjs <stock.zip>` applies every strict bundle replacement,
executes the resulting cleanup with connected/absent sockets in all three modes,
and verifies repeated cleanup, handler removal, state reset, JPEG release and
explicit focus controls. Live idle/resume reliability and the cause of the
original unattended transition remain unvalidated; do not describe this patch
as a confirmed complete fix until those checks pass. Removing this bundle
replacement and restarting the development proxy reverses the local change.

Local validation passed: the new cleanup regression, settings-codec tests,
worker-codec tests, JavaScript syntax, `git diff --check`, and a strict web-package
build from the pinned archive. The review-only output is
`build/update_tesla_focus_cleanup_review.zip`, version `3.0-oc8`; it was not
uploaded and must not be confused with the installed native diagnostic package.

After restarting this worktree's port-8443 proxy and reloading the existing
player, a filtered trace showed two focus acquisitions (0) and INIT focus 0.
Repeating the same temporary mode-1/socket-close probe left the checkpoint at
`[0,1,0]` throughout, with only the mode-setting message captured and **no outgoing
focus release**. Mode 2 and the existing video socket were restored. This confirms
the browser cleanup behavior changed as intended. The already-black phone
session still supplied zero video bytes afterward; fresh-session idle/resume
validation is pending a phone reconnect. No flash or adapter reboot was performed.

After the owner returned, the existing player was receiving HEVC
`hvc1.1.6.L120.B0` at 1328x658, with two parameter/keyframe observations and no
decoder backlog. A normal root-page refresh then recovered without another
phone reconnect: 184 video messages / 2,829,634 bytes by about seven seconds,
51 fps, two parameter/keyframe observations, `waiting:false`, queue 0. The
filtered control trace showed two acquisitions (0), INIT focus 0, and no release.
This validates one fresh-session refresh with the cleanup patch in the proxy;
extended idle/background return remains untested. Nothing was flashed.

#### Unpublished release review: rollback validation fixes

Review of the complete `3.0-oc8-hevc-preview.1` candidate reproduced two rollback
failures using disposable dummy files: a missing backup manifest returned success
and removed the active marker; a corrupted backup overwrote a working file and
returned success. No device operation was involved.

`restore.sh` now preflights the readable/nonempty manifest, checksum/path fields
and every listed backup before replacing any live file. Failure returns nonzero
and preserves native files, version, active marker, codec flag and checkpoints.
The restore ZIP now includes and invokes its own validator, so an older installed
helper cannot bypass the new checks. See [native rollback](NATIVE_ROLLBACK.md).

The unpublished full update, native-test, restore and web ZIPs were rebuilt.
Twelve package tests pass, including missing/empty/malformed manifests, corrupt
or missing first/last backups, an older installed helper and a valid manifest
without a trailing newline. Package checks also use the current codec help text
instead of an obsolete wording assertion. Python/shell syntax and diff checks
pass. All four native binaries remain byte-identical to the 30-test ARM build;
no native codec or focus code changed in this rollback fix. Both release ZIPs
pass integrity/path/version checks, include the updated validator and contain no
private-key/certificate files. Checksums were regenerated under ignored
`build/releases/3.0-oc8-hevc-preview.1/`. Nothing was released, flashed or rebooted.

#### Follow-up review: truncated backup manifests

A second local review reproduced partial restoration when the backup manifest was
truncated after a complete valid row. The helper returned success and cleared the
active marker while leaving another native file patched. The preflight now also
requires exact checksum/path coverage of the installed native metadata and path
coverage of the saved-file inventory, and rejects duplicate backup paths. It uses
staged metadata only when installed metadata is absent, allowing recovery during
a first installation failure. A smaller current package can still restore the
complete backup union from earlier generations.

The new missing-row/backup regression failed against the previous ZIP before
the fix. Updated package tests also cover every omitted row, matching truncation
of both manifests, duplicate rows, missing or invalid installed metadata, a
smaller installed generation and staged first-install recovery. The unpublished
preview ZIPs were rebuilt; no native codec/focus payload changed. Nothing was
published, flashed or rebooted.

#### Owner-authorized full preview installation

At the owner's explicit request, uploaded the rebuilt complete
`build/releases/3.0-oc8-hevc-preview.1/update_tesla_hevc_3.0-oc8-hevc-preview.1.zip`
(2,090,073 bytes; MD5 `10a477f1dbd96bb4c4af6cab4a1bdfb6`; SHA-256
`113d7c824dea51fa84daa160b9e69b9c901daccc33134e5b5fb0c5884e298bfb`).
The source fixes are committed through `70be977`. The prior device version was
`3.0-oc8-vdiag-focus2`; upload returned code 0 / success, version polling reported
`3.0-oc8-hevc-preview.1`, and `/zq-native.txt` logged installation with
`REBOOT REQUIRED`. Read-only HTTPS checks confirmed byte-identical settings,
worker and player bundle versus the uploaded ZIP. These establish installation
and served-file contents, not fresh playback or a completed reboot.

Matching restore ZIP:
`build/releases/3.0-oc8-hevc-preview.1/restore_native_3.0-oc8-hevc-preview.1.zip`,
SHA-256 `05d88d6df62af10185f14162e5797aa7c9179e98d7cc47357149861feb2a6cca`.
It uses the saved stock backups and the new completeness/checksum validator.
Full stock reinstall remains available through `tools/flash.sh rollback`. Either
rollback is a separate authorized device action; native restoration requires
an explicit reboot afterward. No adapter reboot or GitHub release was performed.

#### Owner feedback and release authorization

After full-preview installation, the owner reported "no regression so far" and
explicitly requested pushing all changes to main and releasing `3.0-oc8-hevc`.
This is owner feedback, not independent confirmation of reboot completion, long
idle behavior, Tesla playback or live rollback. The prerelease uses the exact
installed update ZIP and its matching restore ZIP/checksums; internal version
`3.0-oc8-hevc-preview.1` is retained to preserve the tested artifact identity.
Release instructions specify the supported 2.9.11 / 202402031913 base, the normal
web update page, explicit post-install reboot and phone codec renegotiation.
Older firmware such as 2.9.2 remains unverified.

#### Stock-based version naming and release repack

At the owner's request, the default custom build version is now `2.9.11-oc8`,
and the HEVC release/tag/package version is `2.9.11-oc8-hevc`. Active build commands
and release links use these names; dated `3.0-...` records retain the identities
of the artifacts actually tested or installed. The adapter was not reflashed.

All four release-check ZIPs were rebuilt with explicit `--version 2.9.11-oc8-hevc`.
A member-by-member comparison of update and restore ZIPs against the installed
preview confirmed identical entry sets and contents except for
`usr/z-loader/webversion`. The renamed native/browser/rollback payloads are
therefore the same payloads already validated. Eighteen package tests and the
player tests pass. Checksums for the renamed published assets:

- Update SHA-256: `d7cf8fa3858dcd5c22ecb52cb81de7690ec3dcb1994d3e337821b407ab7b3b05`.
- Restore SHA-256: `0ee0fb70ce09ac763aca7e50d3bc9751b9b5071ef9e52ec8e509b492fc9811fc`.

Release/README rollback instructions now link directly to the pinned official
2.9.11 download and its MD5, recommend keeping a private local copy, and explain
that the attached native restore ZIP needs only the on-device saved backup. The
stock archive containing private TLS keys is not mirrored.

#### 2026-10-22: oc11 diagnostic candidate after blackout review

The owner reports intermittent HEVC black views after closing and reopening the
Tesla browser. The supplied review says oc9 is installed and oc10 is an unflashed
diagnostic candidate; neither identity was independently queried in this task.
The blackout's cause remains unproven. Do not use the earlier oc10 artifact.

The oc11 candidate changes `player/worker.js`, `player/zq-settings.js`,
`tools/player_patches.py`, `tools/native/screen_hevc.c`,
`tools/native/stream_diag_sender.c` and diagnostic compile-flag selection in
`tools/native_hevc.py`. Existing uncommitted toolbar changes were preserved.

- The player module awaits a bounded HEVC capability probe before sending INIT.
  An explicit negative selects H.264 before startup; a thrown probe or timeout
  remains inconclusive. Old browser-wide negative cache entries are ignored.
- Both synchronous configure exceptions and asynchronous decoder refusals use
  the same fallback sequence. Exhausting HEVC configurations stops further
  decode/recovery requests. Successful output clears the displayed decoder error.
- Runtime fallback verifies saved H.264 settings before reloading. Blocked storage
  uses `?zq-h264=1` instead; explicitly selecting HEVC clears that override. A
  refusal does not permanently blacklist a browser version. Phone reconnection
  is still required to change an already negotiated phone codec.
- Native setup never calls the frame sender. It records event bits in the
  existing six-word context and emits a scalar-only UUID-tagged report after
  a successfully converted frame's normal callback. Sender/output checkpoints
  explicitly exclude these reports, including across a checkpoint reset.
- Flags bits 10–15 retain all observed configuration event classes since the
  last report: accepted, null key/value, wrong type, empty record, malformed
  record/invalid dimensions, allocation failure. Repetitions coalesce. Missing
  context cannot retain events. Bits 16–31 count the current dropped-frame
  streak modulo 65536; bit 9 retains pending drops even at a complete wrap.
  Good frames report and clear the streak. Reporting has no eight-report cap.
  This preserves the stock allocation and avoids additional state lifetimes.

These reports cannot diagnose a permanently failing converter in real time:
they wait for a valid frame, and a callback does not prove browser delivery.
Use the separate request/phone/sender/output checkpoints to locate a failure
when no report arrives. Device behavior of the revised in-band reporting remains
unvalidated; the candidate is diagnostic, not a confirmed blackout fix.

Built the **full** `2.9.11-oc11-hevc` package with browser files, ChaCha20,
`--native-video-diag` and `--hevc-keep-video-focus`; no Wi-Fi experiment. The
default `CUSTOM_VERSION` remains oc8; the candidate version is explicit.

```sh
python3 -I tools/build_patch.py --firmware build/base/update_tesla0817.zip \
  --native-hevc --native-video-diag --hevc-keep-video-focus \
  --version 2.9.11-oc11-hevc \
  --out build/oc11/update_tesla_hevc_2.9.11-oc11-hevc.zip
```

Validation: 32 compiled ARM tests pass, including real HEVC packet pixel-hash
comparison, rejection classes, 300 dropped frames, repeated recovery, full
counter wrap, callback order and diagnostic checkpoint exclusion at two load
bases. All 18 package install/rollback tests pass. Settings, worker and focus
JavaScript regressions pass, including blocked storage and synchronous refusal.
The packaged module passes syntax checking. Package integrity, version, safe
paths, absence of keys/certificates and correspondence with tested native/browser
payloads were checked. `git diff --check` passes.

Local artifacts (ignored, not published):

- `build/oc11/update_tesla_hevc_2.9.11-oc11-hevc.zip` (2,092,177 bytes), SHA-256
  `6b5db40bb2ea0079620bc689c4a5a27a5dc69d8a7974414d9691d6e4c0eb13e3`.
- `build/oc11/restore_native_2.9.11-oc11-hevc.zip`, SHA-256
  `ebbe623d43d2a3a736456447d7ca54bebccd3257eddf2553cd8eb77434f6d940`.

Rollback uses the matching native restore package and saved stock backups, or
`tools/flash.sh rollback` for full stock. Upload and subsequent reboot each need
the owner's explicit authorization. No device settings, upload, reboot, commit,
push or release was performed. Tesla close/reopen, extended idle and hardware
recovery remain to be validated after an authorized deployment.

#### 2026-10-22: oc12, Tesla evidence, blackout reporting and resize fix

Naming: versions are now plain `2.9.11-ocN` for every package type (no
`-hevc`, `-neon`, `-vdiag` or `-focus2` suffixes); build options are recorded in
`hevc-build.json` / `crypto-build.json`. The Codex oc11 candidate above was
renamed and rebuilt as **`2.9.11-oc12`** after merging main's ChaCha20 NEON
release work. Installed on the owner's adapter at the time of writing:
`2.9.11-oc10-hevc-neon` (see [crypto evidence](CRYPTO_RESEARCH.md)); oc12 is
built, not flashed.

**Case 1: the Tesla browser decodes HEVC (observation).** After an
owner-run install of `2.9.11-oc9-hevc` and power cycle, photos of the Tesla
browser (window 773x601 @1.53x) showed `hvc1.1.6.L123.B0` 1392x1080 decoding:
18–38 fps shown equal to fps received, decode 1–5 ms, queue 0, keyframe ok,
0 resyncs and 0 frame gaps, box mode 2. The original report ("HEVC not working
in the Tesla browser") was therefore not browser support; the likeliest causes
were the missing post-install reboot or a phone that had not reconnected after
a codec change. Not proven.

**Browser HEVC fallback (safety net, not the cause above).** Chrome has no
software HEVC decoder. `zq-settings.js` checks `VideoDecoder.isConfigSupported`
for `hvc1`/`hev1.1.6.L120.B0`; the worker flags HEVC unsupported once every
decoder configuration is refused. Either result switches mode 2 to H.264 for
that browser version, without reload loops, blocked-storage loops or a
permanent ban (Codex review fixes). The phone must still reconnect before the
codec actually changes.

**Case 2: blackout after a full browser close, not after a reload (observation).**
Mac Chrome via the dev proxy, HEVC: a normal page reload recovered without a
phone reconnect (≈20 s later: 638 messages, 2 keyframes; request, phone, sender
and output checkpoints all recorded the new VPS-led IRAP). Killing and
relaunching the whole browser left the new video socket open with **0 messages
for 20 s**, keyframe waiting, and needed a CarPlay restart on the phone. The
owner reports the same in the Tesla, and that H.264 survives it (owner report;
not reproduced here). Hypothesis: the longer period with no player connected,
rather than the reconnect itself, stops the HEVC stream.

The converter (`screen_hevc.c` / `hevc_codec.c`) holds no connection state. Stock
`ScreenStreamStop` only releases the context's CFData field, which our code never
sets, and stock and ours return -6714 for unknown properties (rizin disassembly
of stock `libScreenStream.so`). The only way it drops every frame is a stream
with no accepted configuration, for example a resumed stream whose `hvcC` our
parser rejects. The existing "phone" checkpoint sits after conversion, so it cannot
tell that case from a silent phone.

**oc12 blackout reporting.** Diagnostic builds now also report from the frame
callback when frames are dropped, once the stream has its dimensions: `zq dropped
flags=… err=… len=… header=… size=…` on the first drop of a run and every 32nd,
with no cap. Codex's deferred `zq events` reports still follow the next good
frame, and its filter keeps reports out of the sender/output checkpoints. The
Video panel and overlay show them as `Native ×N: …`. Reading a blackout:

| Overlay | Meaning |
| --- | --- |
| `zq dropped … header=00000000` | No accepted configuration: our parser rejects the phone's `hvcC` (`flags` bits 10–15 give the rejection code) |
| `zq dropped …` with a header | Configuration accepted, frames rejected; `err`/`len` say how |
| no `Native` line | Nothing reached the converter: phone or `libAirPlay` silence |

Reports carry scalars only, not configuration bytes: keeping bytes would need
state shared across the property and frame threads.

Main's NEON composer pins derived `z-tesla` hashes. Codex's checkpoint filter
changes only the diagnostic `z-tesla`. Main's pinned diagnostic+focus build
(`03475fae…`) was reproduced exactly from main's sources and compared with ours.
The two differ only in the appended RX payload segment and its ELF size fields
(`e_shoff`, third `PT_LOAD` `p_filesz`/`p_memsz`); every vendor byte and all
earlier HEVC code are identical. Pinned: `61154fed…` (diag+focus) and
`37c65d4e…` (diag). `test_crypto_neon.py` passed against the new base. The final
diag+focus receiver is `8406352…`; the live-tested oc10 value remains in the
test's comment. Builds without diagnostics still match main's pins.

**Case 3: expanding the Tesla browser from the side window (cause found).** Owner
report: the usual side window renders correctly. Expanding to full screen
restarts the picture as a high-resolution image in a smaller box, and nothing is
clickable. Restoring the side window recovers.

Cause (vendor bundle): `const{w:Fn,h:yt}=a7()` reads the window size once at
load. A resize resends `INIT` with the new size (`wt()` → `rf()`), and the adapter
restarts the CarPlay session (observed `session_state 6 → 2 → 3`). The
`UPDATE_INFO` handler then fits the new stream into the **load-time** size: the
picture box `s`/`o` and scale `r`, the touch scale `ls` and the offsets `cs`. The
picture is therefore scaled into the old box, and touches are mapped for the old
window. Shrinking back matches the load-time size again.

Fix (`tools/player_patches.py`, two exact replacements): `Fn`/`yt` become `let`
and are re-read in `rf()` before each resize `INIT`. The proxy served the patched
bundle and a side-size load played HEVC 864x618. After expanding, the restarted
session sent only a delta frame first (phone checkpoint NAL `0x02`), no keyframe
in about 60 s, so the new fit could not be confirmed on the Mac. **Needs a Tesla
side→full-screen test.** The missing keyframe after a session restart is a
separate open issue, related to Case 2.

Validation: 33 ARM tests (diagnostic build) / 25 + 8 skipped (normal build),
`test_crypto_release`, `test_crypto_install`, `test_crypto_neon_reports`,
`test_crypto_neon`, 18 native package install/restore tests, and the
worker/settings/focus JavaScript regressions all pass. Nothing flashed, rebooted
or pushed for oc12.

#### 2026-10-22: oc13, automatic no-video recovery and review fixes

**Case 2 reproduced on oc12 (observation).** After an owner power cycle, the phone
connected before any player was open. The proxy page then showed `session_state 4`,
an open video socket with **0 messages**, request checkpoint `00000000 00000001
00000000` (session present, keyframe request returned success), phone/sender/output
checkpoints **absent**, and **no `Native` report**. Reports pass through the same
frame callback that the phone checkpoint watches, so nothing at all left the
converter. Drop reports start only once the stream has its dimensions, so this
does not fully exclude a converter that never received its size. The evidence
nevertheless places the stop upstream of our converter: phone or `libAirPlay`
silence.

**Recovery found (observation).** A same-size `INIT` (the bundle's resize handler
with no size change) was answered with `session_state 4` and brought no video. A
real 40 px size change made the box restart the session (`6 → 4`). About 25 s later
HEVC was back **without a phone reconnect**: `hvc1.1.6.L123.B0` 1904x658, 31 fps,
2 keyframes, and `zq events flags=00000504`, meaning the converter accepted the
new `hvcC`. In the same run the canvas filled the new 1900x657 window, which
confirms the oc12 resize fix on Mac Chrome.

**oc13 automatic recovery (`zq-settings.js`, browser only).** Recovery fires when
the box reports a running session (`session_state 4`), the worker-owned video
socket is open, and it has received **no message at all** 8 s after opening. The
page then resends `INIT` with a different size (oc14: `__zq.initSize`; oc13 used a 2 px `__zq.nudge`), via a strict
`player_patches.py` replacement), so the box restarts the session. At most twice
per page load, 20 s apart. A still screen after a working start is untouched,
because it has received messages. The overlay shows `restarts N`. In one live run
after a 15 s browser absence, video resumed by itself and the recovery correctly
did not fire. The firing path is covered by `test_settings_codecs.mjs` and still
awaits a real blackout.

**Review fixes (second `/code-review`):**

- **H.264 after HEVC refusal:** a worker that gave up on HEVC now drops only HEVC
  input. A later H.264 stream (after the phone reconnects) decodes, with the
  latency/hardware hints restored.
- **Consecutive refusals only:** refusals count in a row, and any decoded output
  resets the count. The fallback chain cycles through tuned → plain → hev1, and
  HEVC is given up after 3 refusals in a row. Any synchronous `configure`
  exception counts as a refusal, so it cannot loop forever. H.264 has the same
  bound.
- **No saved-preference rewrite:** a runtime refusal falls back for the session
  only (`?zq-h264=1`). The saved HEVC preference is untouched, and a new browser
  session probes again.
- **Atomic diagnostic flags:** `screen_hevc.c` accesses `nal_flags` only through
  `__atomic_*` operations (inline `ldrex`/`strex`; no library calls). The property
  thread swaps in the format bits by compare-and-swap, and the frame thread takes
  pending events with one `fetch_and`. A concurrent configuration change can no
  longer lose the length size or the HEVC bit. The existing unsynchronised header
  replacement against an in-flight frame (stock has the same pattern) is unchanged.
- **Reports are not frames:** swallowed reports no longer send the page a "frame"
  note. `--native-only --native-video-diag` is refused, because an older preserved
  player would decode the reports. The report format is defined once in
  `tools/native/zq_report.h`.
- **Top-level await only in HEVC builds:** the HEVC-probe startup gate is applied
  only to `--native-hevc` bundles (and by the dev proxy when the box is native HEVC).
- **Build identity:** every package carries `usr/z-loader/zq-build`
  (`<version>+<12-hex content hash>`) and `web/zq-build.txt` (identity plus build
  options). `tools/flash.sh` waits for that identity, so reflashing the same
  version can no longer report "done" early. The 40 MHz failure counter is keyed
  on it, so a rebuilt fix is retried. The restore installer copies both files.

Diagnostic and normal `z-tesla` outputs are unchanged and still match the pins.

Independent review of the oc13 tree (REWORK, fixed before packaging):

- `inspect()` left HEVC mode only on an H.264 SPS header of exactly `0x67`. After
  an HEVC refusal, an H.264 SPS with another `nal_ref_idc` (`0x27`, which the
  vendor bundle's key test also accepts) would have kept every H.264 frame
  dropped. Any `(byte & 0x9f) == 0x07` now counts; as HEVC headers those bytes
  would need layer id ≥ 32. Covered with a `0x27` case.
- The no-video watchdog counted diagnostic reports as messages, so a
  diagnostic build sending `zq dropped` would never restart. It now uses
  `sockVideoMsgs` (video only).
- After a stock rollback a leftover `zq-build.txt` is shown by `flash.sh` as
  `(stale: …)` instead of as the current build.

Follow-up `/code-review high` fixes (also before packaging):

- The no-video recovery runs in **HEVC mode only**. In H.264 or JPEG mode a socket
  reopened on a still screen may legitimately wait for its first frame. A real
  (trusted) window resize resets the 2 px nudge (oc13; replaced in oc14), so the next INIT asks for the
  full height.
- H.264 decoder refusals no longer block H.264 until a reload. H.264 has no page
  fallback, so after both of its configs (tuned, plain) are refused in a row it
  rests for 5 s, then retries from the tuned config with a fresh socket. HEVC
  still gives up after one try of each of its three configs. Each refusal moves to
  the next distinct config. The chain only starts over when a config that worked
  earlier is refused.
- Build identity semantics: `zq-build` / `zq-build.txt` identify the **last
  installed package**. A `--native-only` package therefore updates the identity
  while the previously installed player files stay in place. `checkver.cgi` (and the
  box's own update check) still sees only the plain version.

**Owner report, 2026-10-22, Tesla browser on `2.9.11-oc13+a96bfcaf1529`:** "no more
black screen". This is a subjective owner report: the restart count, timings and
which scenarios were exercised were not recorded. Extended idle/return and the
side-window → full-screen check in the car remain to be confirmed explicitly.


#### 2026-10-22: oc14, one connection when the phone connects first

Recorded on Mac Chrome through the proxy (CDP console + checkpoint
`Last-Modified` = time since boot), phone connected before any player:

1. Before the player opened, the box had issued a keyframe request but the phone
   sent no frames. That is expected with no player connected.
2. The player's first `INIT` made the **box end the session** (`session_state 6`
   about 3 s later). The phone reconnected about 13 s later (`3 → 4`). Cause: the box
   sets a phone session up at the size of the **last INIT it received** and
   restarts it when a later INIT asks for another size. The remembered size came
   from another client (the car, or the Mac) and from oc13's 2 px recovery nudge.
   This is stock behaviour: CarPlay fixes the phone's screen size per session.
3. oc13's recovery then fired **as soon as** the session returned to 4. It measured
   the video socket's age (15 s, all spent in the restart), not the session's.
   The new session was already delivering (frames, `zq events … 504`, UPDATE_INFO),
   and the nudged INIT ended it: a third connection. The owner saw the same in the
   Tesla ("restarts twice").

oc14 (player only):

- **INIT size policy** (`__zq.initSize`, one strict bundle replacement after the
  stock cap/16-alignment). When the page opens while a session is running, it
  sends the running session's size, remembered from the box's `UPDATE_INFO`
  (`message_id 14`, localStorage `zq.session`). The box keeps the session and the
  bundle fits the picture to the window. A real (trusted) resize sends the window
  size, so full resolution needs one restart, as before. The recovery sends a size
  that differs from the session's (2 px shorter, or back to the window size). This
  replaces the oc13 nudge.
- **Recovery timing:** the session itself must have run 8 s (`session_state`
  change time) with no video message, not just the socket.

Live check (proxy, phone connected, page reload): no session restart, HEVC
1328x658 about 4 s after load, 54–59 fps, `restarts 0`. The phone-first run before
the fix had video only after 35 s and three connections.

`/code-review high` of the oc14 change:

- **Fixed:** "Apply & reload" with a new resolution or frame rate now requests
  the settings' size once (a `sessionStorage` flag) instead of reusing the session
  size.
- **Fixed:** the recovery asks for the window's own size (2 px shorter when that
  equals the session's), never a mix of the session's width and the window's
  height that would then be remembered.
- **Fixed:** an unchanged UPDATE_INFO does not rewrite storage.
- **Limits, documented:** the box does not report its size before `INIT`, so a
  size remembered from a session another client started still costs one restart
  (then it is relearned). Reuse relies on `session_state` arriving before `INIT`.
  The bundle waits 500 ms, and the state arrived first in every recording.
- **Not changed:** the hook runs after the stock height cap and 16-alignment,
  and the box echoed the reused (1328x658) and recovery (1328x656) sizes exactly.
  `rf()` is the only INIT sender, so each resize consumes its reason once.

#### 2026-10-22: oc15, touch reaches the whole stream

Owner report (Mac proxy, Resolution set high): with a reused **2912x1440**
session in a 1328x657 window (Resolution 1080, scale 1.64), the CarPlay home
button at the bottom of the left dock could not be tapped. The stock touch mapping
`ka()` clamps to `Pc()` = window x scale (≈2184x1080), which equalled the stream
size only while every INIT restarted the session at that size. With oc14's
session-size reuse the stream can be larger, so taps below y ≈ 493 px were clamped
to phone y = 1080, and similarly on the right.

Fix (`tools/player_patches.py`, the existing `ka()` replacement): the clamp
bounds are the stream size from **this page's** `UPDATE_INFO`
(`__zq.touchBounds`), with stock window x scale until it arrives. Verified live:
the bottom-left tap (30, 637) maps to (66, 1397) of 2912x1440 (was clamped to
y = 1080), and the centre (664, 328.5) to (1456, 722); the regression test taps (664, 328), which gives 719. The owner confirmed the home button works.
Regression: `tools/test_player_focus.mjs` runs the patched vendor `ka()`.
`__zq.mapTouch(x, y)` and `__zq.lastInit` (INIT size, window, reason, session
state) are exposed for debugging.

`/code-review` of oc15 (fixed before packaging):

- **Recovery size:** the restart size is now compared with what the box holds
  (this page's last INIT, then UPDATE_INFO), not with the remembered size. A stale
  remembered size plus an early window-size INIT could otherwise resend the box's
  own size, and nothing would restart.
- **Resizes:** a trusted resize restarts only if the window size actually changed.
  Reasons ("resize", "restart") expire after 2 s if no INIT follows (the bundle
  sends one within 500 ms).
- **Touch bounds:** taken from the bundle's own UPDATE_INFO values (`Q`/`ae`, which
  are content size for some phone types) through a strict patch
  (`__zq.streamFrame`). They are set to the requested size when an INIT is sent,
  and CarLife wireless keeps stock bounds. The test extracts `ka()` by its end
  before the Home component and covers CarLife.
- **Control messages:** JSON parsing is isolated from handling, so handler errors
  are no longer swallowed as "not JSON".

New in oc15 (both unvalidated on a device):

- **Codec change without a manual reconnect:** Apply with H.264 ↔ HEVC (and the
  automatic HEVC → H.264 fallback) requests a size other than the box's, so the box
  restarts the phone session and the phone renegotiates the codec. JPEG keeps its
  documented disconnect/apply/reconnect procedure.
- **Decoder line in the Video panel:** `isConfigSupported` for HEVC/H.264 ×
  `prefer-hardware`/`prefer-software`, plus whether the running decoder got the
  hardware-preferred low-latency config or the plain one. Chrome has no software
  HEVC decoder, so HEVC in the Tesla is necessarily hardware-decoded. This line shows
  whether H.264 there is software, a candidate explanation for HEVC feeling
  slightly behind H.264 in the car (hypothesis).

Independent review of the oc15 player changes (REWORK, fixed): Apply compares a
codec change with the mode the page runs (`__zq.mode`), not the saved one, so
"HEVC (retry)" from the session H.264 fallback restarts the session. A carried
"restart" outranks a resize at load. The panel's decoder-path probes time out
after 1.5 s ("?").
