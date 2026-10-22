# Existing T3 CarPlay receiver: HEVC modification targets

**Current status, 2026-10-22:** native HEVC playback is verified on the owner's
stock-2.9.11-derived T3 in Mac Chrome. The installed native diagnostic candidate is
`3.0-oc8-vdiag-focus2`; the newer browser cleanup focus fix is proxy-only. Fresh-session
refresh passed, while extended idle/return and Tesla playback remain untested.
The inspection and early install records below are historical; use the
[README](../README.md) for current build/install commands and
[diagnostics](HEVC_VIDEO_DIAGNOSTICS.md) for the latest device evidence.

Inspection date: 2026-10-22. Target behavior: receive compressed HEVC from the
iPhone's CarPlay session and forward it unchanged to the Tesla browser, preserving
normal authentication. An experimental local patch now builds and passes offline
tests. Live CarPlay HEVC negotiation and Tesla playback remain unverified.

## Firmware and inspection boundary

Stock 2.9.11, platform build 202402031913, pinned archive MD5
`3a743be56b60df524825cffcf45238cd`. The archive was downloaded and verified locally;
only selected receiver/video files were extracted outside Git. No keys were read
and no device settings, uploads or reboots occurred. The static inspection did
not execute target code; the later tests emulate our handlers and the adjacent
original instruction windows, with external APIs mocked as described below.

A focused byte-string scan covered all 50 shared-object archive entries, including
versioned sonames. Searched markers: `hevcInfo`, `enabledFeatures`, `hvcC`, `hvc1`,
`hev1`, `HEVC`, `hevc`, `H265`, `h265`, `H.265`. Only four libraries matched:

| Library | Matches | What this establishes |
|---|---|---|
| `libt113_h264_decode.so` | `H265` (`CODEC_H265`) | A codec-name string exists; not a selectable HEVC CarPlay input. Its initializer passes a fixed `0x115` codec field to `AWVideoDecoder::create`; the numeric enum mapping was not independently established. |
| `libt113_jpeg_encode.so` | `H265`, `h265` (`CODEC_H265`, `libvenc_h265.so`) | Names exist in the binary; no evidence that the T113 provides a working HEVC encoder or that this wrapper selects it. |
| `libzlink-main.so` | `H265` | Previous inspection located an Android Auto enum entry, while its AA sink advertises H.264. This does not enable CarPlay HEVC. |
| `libdmsdpdvcamera.so` | `H265` | Marker in a different integration; its implementation was not decompiled in this follow-up. No demonstrated CarPlay connection. |

Absence of markers is a bounded observation, not proof that every code path lacks
HEVC. The hardware decoder dependencies (`libvdecoder.so`, `libsdk_decoder.so`,
`libvideoengine.so`, etc.) are named by the T113 wrapper but are not supplied by
this update archive. Their installed implementations were not inspected.

## Concrete modification targets

| Component | Observed behavior | HEVC work indicated |
|---|---|---|
| `libAirPlay.so` (`AirPlay/280.3`, 1S70) | Builds CarPlay receiver information and SETUP responses. Prior analysis found no HEVC advertisement. | Add verified codec advertisement/negotiation while preserving existing authentication and normal error handling. Compatibility with the phone remains untested. The library name does not mean the target is generic AirPlay mirroring. |
| `libScreenStream.so` | `_ScreenStreamSetProperty` converts AVC configuration with `H264ConvertAVCCtoAnnexBHeader`; `ScreenStreamProcessData` uses H.264 NAL parsing and type-5 keyframe detection. | Handle the actual HEVC configuration format, NAL length size, VPS/SPS/PPS, Annex B conversion and HEVC random-access frames; preserve the callback ABI and H.264 fallback. |
| `libzlink-main.so` | `cp_VideoData_cb` forwards video and associated arguments through a registered callback. | No codec conversion was observed in this function. Trace the remaining transport and registration chain before declaring the whole library codec-neutral. |
| `libzlink.so` | Exports video callback registration and delivery APIs. | Callback/transport compatibility remains to be validated; this follow-up inspected symbols only. |
| `z-tesla` | Incoming CarPlay video callback checks AVC SPS bytes `0x67`/`0x27`; the secure video WebSocket callback also special-cases these bytes. | Make metadata/configuration and small-header handling codec-aware. The queue copies opaque frame bytes, but that alone does not establish complete HEVC passthrough or reconnect behavior. |
| `libt113_h264_decode.so` | Local hardware decode wrapper used by the decode/JPEG route. | Not required to decode HEVC for browser passthrough. Changing it alone cannot make the iPhone send HEVC. Local JPEG fallback would need separate HEVC validation. |

### Newly traced `z-tesla` path

`sub_3023c` registers callback `sub_2fb0c` through
`libzlink_video_data_cb_init` at `0x3025e`. The callback address was recovered
from its Thumb PC-relative literal and corroborated by Hopper decompilation.

`sub_2fb0c` examines the byte at input offset 4 for `0x67`/`0x27` when updating
screen metadata. With a secure video client present, it calls `sub_21bd4`, which
locks a mutex and queues data through `sub_2040e`. That queue function copies
bytes and preserves per-frame lengths without observed codec parsing.

The secure video WebSocket callback `sub_21778` reads queued frames through
`sub_20288`. It special-cases short AVC SPS-leading buffers (length at most 42)
and merges another queued buffer. In passthrough mode it calls `sub_40df6`
with the queued bytes; otherwise it invokes the local decode/JPEG path. The
HEVC effect of these AVC-specific conditions, output writer behavior, queue
limits, disconnect/reconnect and first-frame recovery still need validation.

## Evidence and validation status

All addresses below apply only to the artifact hashes listed here and in
[HEVC_RESEARCH.md](HEVC_RESEARCH.md). Pseudocode is a decompiler observation,
not original source; recovered argument types and indirect calls remain uncertain.

| Observation | REA evidence ID |
|---|---|
| Screen configuration conversion | `ev_7b4148d4df9a9e3cd0e489705e567aa5f9137bca309a3c42c519593da835dbc6` |
| Screen frame conversion | `ev_7d643688ff213439c464a6bf0e41cbb75c944b2ac40e28c624d200935983ac7c` |
| CarPlay callback in `libzlink-main.so` | `ev_539422f45fccf2aba7d85a09497dd56305245aee9c260105903a8853510da5c3` |
| `z-tesla` callback registration | `ev_91d6f5b00f2481c82d0d9c35b7ad59afbe5f816c4159088167fa1129c6f6830e` |
| `z-tesla` incoming-video callback | `ev_446f836f04778b0fc7765dd1ae67727ffb5a142bec53e7988ca5f7ebe267f5d3` |
| Queue write | `ev_954c7b2d28d2cbc079b9e3fe34a21eddb2fe7fcd074faa4cc7b1935376886f01` |
| Secure video WebSocket callback | `ev_57a79df664b9466f29f7bf81147d47fe51110229bac6b6e75d27677499c025e3` |
| T113 decoder initializer | `ev_81748e6760ab728e63e4d7e9e6462af5566093d0d96e695457de320c84ded0b0` |

Additional inspected artifact identities:

- `libzlink-main.so`: SHA-256 `9cba83a9297299dcacd3acd0d1e1fd1d73750c62f733f28dc77eb75df7452e2e`.
- `libt113_h264_decode.so`: SHA-256 `4bf873bfe084b0fcec582d489eec3969ba976c43c1af407fabb946fcddf4c487`.
- `libt113_jpeg_encode.so`: SHA-256 `de56f3203478ac88784742e100579d36b26fbb2831174d770f6620025cf0cc62`.

## Experimental patch (3.0-oc8)

`tools/native_hevc.py` builds original, freestanding ARMv7 Thumb C/assembly and
applies ten exact instruction replacements to four SHA-256-pinned files:

- `libAirPlay.so`: hook the `/info` return to add an empty `hevcInfo` dictionary;
  hook the successful SETUP response to add `enabledFeatures: ["hevc"]`. Existing
  authentication, error paths and the reported `280.3` version remain unchanged.
  An unexpectedly existing `enabledFeatures` field is preserved. Both HEVC
  additions are gated by `/data/local/tmp/zq_hevc`, maintained by the mode hook.
- `libzlink-main.so`: request a fresh keyframe through the existing session API
  after CarPlay video focus updates, including identical values on socket reopen.
  See [HEVC_RECONNECT_FIX.md](HEVC_RECONNECT_FIX.md) for evidence and limits.
- `libScreenStream.so`: replace the configuration and frame handlers with our
  AVC/HEVC converter. It accepts raw configuration records and bounded ISO BMFF
  `avcC`/`hvcC`, `avc1`/`hvc1`/`hev1` and `stsd` containers. It handles one-, two-
  and four-byte NAL lengths, emits VPS/SPS/PPS in order before HEVC IRAP frames
  (types 16–21), preserves timestamps/dimensions/completion callbacks, and rejects
  malformed input transactionally. AVC configuration and type-5 keyframes remain
  supported. Extended-size BMFF boxes and arbitrary unstructured wrappers are
  rejected; the actual iPhone configuration payload has not been captured here.
- `z-tesla`: extend the incoming metadata check to recognize HEVC VPS/SPS as well
  as both existing AVC SPS variants. Its WebSocket sender needs no instruction
  change: our HEVC parameter sets are bundled with the random-access frame, so
  the stock short-AVC-header merge is not used for HEVC. A second hook at the
  shared mode-writer exit (`0x1dbaa`) maintains the HEVC flag after a successful
  save: mode 2 creates it; modes 0/1 remove it. INIT/runtime continuation hooks
  retain compressed passthrough on errors; a startup guard prevents loading
  JPEG mode when a readable HEVC flag remains.

Each file gains a read/execute load segment containing our handlers. Bridges use
PC-relative calls into existing PLT/GOT entries; no imports, authentication
material or dynamic relocations are added. Original vendor bytes are read only
at build time and outputs stay under ignored `build/`. Do not commit or publicly
upload these native artifacts. The old embedded GNU build ID is inherited from
the base; use the manifest's SHA-256 hashes to identify patched files.

The HEVC package offers HEVC (mode 2), H.264 (mode 1) and JPEG (mode 0), with
H.264 as the default. Reboot the adapter after a native install or restore.
Disconnect the phone, apply the setting, then reconnect:
existing sessions retain their negotiated codec. JPEG requires the phone to
send H.264 for local decoding. This is a receiver capability request, not a
guarantee that the phone chooses HEVC. The actual stream codec in the Video
panel is the confirmation. See [CODEC_SWITCH_DESIGN.md](CODEC_SWITCH_DESIGN.md)
for the hook, failure behavior, timing and offline validation details.

The supplied DiPlay reference was checked at commit
`143f4674b60b564a16baae5110b3b90fb7277631`.
[Its screen-stream implementation](https://github.com/shihabal3amri/DiPlay/blob/143f4674b60b564a16baae5110b3b90fb7277631/shared/src/main/java/com/shilapi/xcertplay/airplay/ScreenStream.kt)
distinguishes configuration from frames and detects `hvcC`/`avcC` records. Its
configuration detector scans for a fourcc more broadly than our bounded box
parser. No source or authentication code was copied from DiPlay.

### Local build and checks

Requires Clang with ARM support and LLD (`ZQ_CLANG`/`ZQ_LLD` can specify absolute
paths). Defaults discover the tools, including Homebrew LLVM/LLD 22 on this Mac.

```sh
python3 -I tools/build_patch.py --firmware /path/to/stock.zip \
  --native-hevc --out build/update_tesla_hevc_3.0-oc8.zip
python3 -I tools/build_patch.py --firmware /path/to/stock.zip \
  --native-test --out build/native_test_3.0-oc8.zip
python3 -I tools/build_patch.py --firmware /path/to/stock.zip \
  --restore-native --out build/restore_native_3.0-oc8.zip
```

For independently inspectable native output and ARM tests (Python `unicorn`
required; FFmpeg/FFprobe enable the real-bitstream round trip):

```sh
python3 -I tools/native_hevc.py --input-dir /path/to/selected-stock-files \
  --out-dir build/hevc-native
python3 tools/test_native_hevc.py -v
python3 -I tools/test_native_packages.py --firmware /path/to/stock.zip -v
```

Validation on this Mac:

- 25 ARM-emulator tests passed: actual patched entry points, callback ownership,
  original epilogue/output behavior, preserved registers/stack alignment, two
  library load addresses, AVC fallback, NAL widths, IRAP detection, box bounds,
  parameter-set order, allocation failures and malformed input.
- A newly generated HEVC MP4's real configuration and three compressed packets
  passed through the ARM converter. FFmpeg decoded the resulting Annex B stream
  with pixel hashes identical to the original MP4. This validates conversion;
  it does not run the Tesla decoder or the vendor session engine.
- Seven package tests passed in a scratch filesystem: fresh install/restore,
  upgrade from the two-file native-test backup to the five-file HEVC backup,
  refusal of changed native files/corrupted backups, package contents and the
  codec selection. Shell scripts have not run under the device's BusyBox.
- JavaScript/Python/shell syntax checks passed.

### Integration with main

Merged main through `68c7617` in merge commit `65132aa`. The integrated build is
`3.0-oc6`, distinct from main's `3.0-oc5`. It includes raw touch, touch-to-frame
stats, Wi-Fi diagnostics/options, AVC frame-gap resync and default ChaCha20 TLS.
`tools/test_worker_codecs.mjs` verifies AVC gap detection, HEVC bypass of AVC
slice parsing, switching back to AVC, and preservation of both sets of stats.
The existing micro-benchmark uses random AVC slice headers, which now trigger
the frame-gap detector; its old throughput number is not comparable without
updating that benchmark's fixtures.

The HEVC package retains main's default ChaCha20 configuration and launcher.
Native rollback restores the original native binaries and watchdog script;
the TLS setting remains as in main. `--no-tls-chacha` still builds a package
that restores the stock TLS launcher, separately from native rollback.

### Deployment boundary and recovery

Current `3.0-oc8` native install and restore scripts log `REBOOT REQUIRED` and
perform no partial live restart. An explicit owner-triggered reboot is required
to activate all native binaries and the supervisor/watchdog script together.

The offline build/test phase performed no upload or reboot. The native-test and
restore packages allow separate on-device recovery validation; that sequence has
not yet been exercised on this box. The owner subsequently authorized the HEVC
installation recorded below.
The HEVC package replaces `libAirPlay.so`, `libScreenStream.so`, `libzlink-main.so`,
`z-tesla`, `z-link.sh`, the normal patched web files and custom webversion. Its manifest is
`misc/zq-native/hevc-build.json` inside the package.

Rollback restores the five stock native files using the saved backup and
`build/restore_native_3.0-oc8.zip`. Successful restore also removes the codec
flag. The custom web player remains installed and can play stock H.264;
full stock remains available through `tools/flash.sh rollback`.

The crash watchdog only catches receiver exits, not an iPhone rejecting the
session or a live process with a black screen. Native hardware, phone acceptance,
real callback/CF behavior, browser decode, latency, audio synchronization and
reconnect recovery require on-device validation. See
[NATIVE_ROLLBACK.md](NATIVE_ROLLBACK.md) before any separately authorized deployment.

### Owner-authorized installation, 2026-10-22

Installed `build/update_tesla_hevc_3.0-oc6.zip` through the normal upload CGI at
the owner's explicit request. Package SHA-256:
`2e70fa62815421143488c469a85b05587ee16ecf7a8a97919ebb8f698cf81203`.
Before upload the adapter at `3.3.3.3` reported platform `202402031913`, web
version `3.0-oc5-ht40`. Upload returned `code: 0`; subsequent queries reported
`3.0-oc6`. No explicit reboot request was sent; the native installer restarts the
receiver. The package did not replace the existing Wi-Fi boot scripts.

At 11:13 UTC, the web status endpoint remained reachable. The native log
confirmed a stock backup at `/misc/zq-native/stock` and installation of `3.0-oc6`,
with no automatic restore entry observed. TLS handshakes succeeded on receiver
ports 5002, 7002 and 9002. This demonstrates initial listener availability,
not successful HEVC negotiation, sustained operation or live rollback. The
device log uses a 1970 timestamp, so the observation time comes from the host.
Live CarPlay and Tesla rendering still need confirmation in the Video panel.
