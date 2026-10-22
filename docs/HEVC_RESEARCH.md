# HEVC CarPlay feasibility on Ownice T3

Research date: 2026-10-21 (America/Toronto). This is offline firmware analysis and
public-source research, not a successful HEVC test on the adapter or Tesla.

## Finding

HEVC CarPlay is real, but the stock T3 video path is explicitly H.264-specific.
A browser-only codec switch cannot enable it. A viable design would negotiate
HEVC on the iPhone, preserve the compressed stream through the adapter, and
decode it in the Tesla browser. No compatible replacement receiver package has
been verified. It is not yet established whether this T3 can negotiate HEVC
using its existing authentication hardware and vendor integration.

Follow-up firmware inspection found **AirPlay/320.17.6** in three public CarlinKit
T2C images. Their receiver libraries are byte-identical and expose a different API
and dependency set from the T3's `libAirPlay.so`; they are not direct replacements,
and HEVC support remains unconfirmed. GitHub source candidates include LIVI v9.4.0
and CatPlay v0.17.0, with no verified T3 build. See the
[receiver comparison](RECEIVER_CANDIDATES.md) for artifact hashes, version evidence,
source links and integration limits.

## Evidence from this adapter's firmware

The local vendor archive was checked against the repository's pinned base MD5:
`3a743be56b60df524825cffcf45238cd`. Only the relevant native files were extracted
to `/private/tmp/ownice-hevc-research`, outside Git. No vendor code was executed,
no private keys were inspected, and no device was queried, uploaded to, or rebooted.

| Artifact | SHA-256 |
|---|---|
| `libAirPlay.so` | `963f33856cb90649d9625485eb9ee302ea63a964465a0149ac9e6f6e25a1ef8a` |
| `libAirPlaySupport.so` | `89306904ffa8a7d027f5e2adda8fcb3767f919fac814798bcf82b364bb054a13` |
| `libScreenStream.so` | `6e0ea898f25481bf62b3a1e3184a916b1a469df71fd33a8b3637b9b159e8c588` |
| `z-tesla` | `fabf68f52d56e487a591e1229c3e2fa3c9006a9efc91bb4af57c03f1bd501ed8` |

Hopper inspection through REA established the following static observations:

- `libAirPlay.so` is ARM ELF and identifies the 1S70 communication plug-in and
  `AirPlay/280.3`. These identify the archived build, not the installed device.
- `_ScreenStreamSetProperty` at `0xcb4` in `libScreenStream.so` calls
  `H264ConvertAVCCtoAnnexBHeader` when processing codec configuration.
- `ScreenStreamProcessData` at `0xed8` calls `H264GetNextNALUnit`, converts
  length-prefixed NAL units to Annex B, tests the H.264 NAL type using the low
  five bits against type 5, and prepends the saved parameter-set header before
  invoking `ScreenStreamSend_CB` on that path.
- A byte-level search in `libAirPlay.so`, `libScreenStream.so`, and `z-tesla`
  found no literal `hevc`, `HEVC`, or `hvcC`. Absence of these strings alone
  would not prove absence of functionality. The explicit H.264 parsing above
  is the stronger evidence.
- `z-tesla` contains `is_h264_mode`, the persisted H.264-mode path, and T113
  H.264 decoder loader strings. Its complete forwarding path was not decompiled;
  whether it could pass HEVC unchanged remains unknown.
- Our `player/worker.js` inspects H.264 NAL types and derives an `avc1` codec
  string. The control patches expose H.264/JPEG selection, not HEVC negotiation.

REA evidence identifiers (full selected records retained in the temporary
`evidence.json` beside the inspected files):

| Observation | Evidence ID |
|---|---|
| Receiver string inventory | `ev_f47d93cbe455705e1b682fd77d9fa4d5814ff46c0db3ad5944be43029179e457` |
| Screen property handler | `ev_7b4148d4df9a9e3cd0e489705e567aa5f9137bca309a3c42c519593da835dbc6` |
| Screen frame handler | `ev_7d643688ff213439c464a6bf0e41cbb75c944b2ac40e28c624d200935983ac7c` |
| Wrapper strings | `ev_ad8b3d88f3861c3b0756c325974a1f5062aecdcf63780df5965dfc5b5294fe5b` |

These are decompiler observations, not original source or runtime traces. Register
and argument recovery can be incomplete. The report summarizes behavior rather
than redistributing vendor code; temporary evidence is not a repository dependency.

## What public primary sources establish

**Apple supports HEVC CarPlay UI streams.** Apple announced it in WWDC23,
recommending continued H.264 support for backward compatibility. The talk does
not publish a plug-in version matrix or establish T3 compatibility.
[Apple: Optimize CarPlay for vehicle systems, 15:45](https://developer.apple.com/videos/play/wwdc2023/10150/)

**The T113 is not limited to H.264 decoding.** Allwinner lists H.265/H.264
decoding up to 1080p60, with JPEG/MJPEG encoding. Its published hardware features
do not offer an HEVC encoder. Software transcoding is not ruled out in principle,
but no evidence supports it meeting this project's resolution, frame-rate and
latency goals on the dual Cortex-A7. Passthrough avoids this encoding work.
[Allwinner T113 specifications](https://www.allwinnertech.com/index.php?a=index&c=product&id=106&solveid=43)

**HEVC is negotiated above the browser.** The public xcertplay implementation
conditionally publishes `hevcInfo` based on its HEVC setting and enabled feature
set. The OCBM author reports a live HEVC cluster stream and parsing its `hvcC`
configuration. These are implementation examples, not verified T3 replacements.
[xcertplay info construction](https://github.com/shilapi/xcertplay/blob/master/shared/src/main/java/com/shilapi/xcertplay/airplay/AirPlayInfoPlist.kt),
[OCBM A/V pipeline](https://github.com/lvalen91/ocbm/blob/main/docs/carplay/06_AV_PIPELINE.md)

**A marker alone is insufficient.** A CarlinKit researcher reports an iPhone
rejecting `hevcInfo` when HEVC had not been negotiated, and reports a dependency
on protocol/authentication generation in that adapter family. This is firsthand
research on different hardware, not proof that the T3 has the same restriction.
Do not infer that changing a model name, version string, or advertised key will
enable HEVC. T3 authentication-generation compatibility remains unverified.
[CarlinKit investigation, October 2025](https://github.com/ludwig-v/wireless-carplay-dongle-reverse-engineering/discussions/216)

**Browser HEVC availability must be measured.** WebCodecs defines HEVC input
formats but does not require HEVC implementation. Annex B HEVC input needs correct
parameter sets and random-access handling. The current AVC parser cannot be
reused unchanged.
[W3C HEVC registration](https://www.w3.org/TR/webcodecs-hevc-codec-registration/)

## Candidate routes

| Route | Assessment |
|---|---|
| Add an HEVC option to current JavaScript | Insufficient: upstream still supplies H.264 and the native converter assumes AVC. |
| Update the receiver with a compatible Zjinnova build | Vendor-supported route; no verified public T3 package found. The newer T2C 320.17.6 library uses a different API and dependencies. |
| Integrate another receiver implementation | LIVI's GPLv3 receiver core is a concrete ARMv7 feasibility-study candidate. CatPlay has relevant HEVC code, but reuse terms remain unresolved. Both need native callbacks, authentication and browser-forwarding integration; neither has T3 proof. |
| Install another adapter's complete firmware | Compatibility not established; shared branding or CPU is insufficient. |
| Transcode H.264 to HEVC on the T3 | No published HEVC hardware encoder; unsuitable as the initial approach. Also does not reduce the phone-to-adapter H.264 traffic or recover its lost detail. |

Zjinnova advertises Linux/RTOS libraries and Allwinner platform support, making a
vendor-supported integration a concrete lead. Its page does not explicitly
promise HEVC or T3 support; request those specifically, including whether 1S70
can be upgraded while retaining this unit's existing authentication hardware.
[Zjinnova libzlink SDK](https://www.zjinnova.com/libzlink-sdk.html)

## Tests that settle the remaining questions

1. **Tesla decoder:** On an HTTPS page in the actual Tesla, query HEVC Main
   configurations for both `hvc1` and `hev1`, with the intended resolution and
   hardware preference. Follow any positive result by decoding a known HEVC
   sample and measuring output; support queries are not performance tests.
   A negative result for one profile/level does not exclude every HEVC variant.
   The Video panel's **Check HEVC decoder** button runs this query at the
   current stream size (`hw` = `prefer-hardware`, `any` = `no-preference`) and
   logs the full results as `[zq] decoder check` in the console.
2. **Receiver capability:** Obtain a vendor compatibility statement or observe
   the owner's normal CarPlay session negotiation through supported diagnostics.
   Determine whether the phone proposes HEVC and the receiver accepts it. Keep
   authentication material out of reports. No authentication bypass is part of
   this route.
3. **Native integration:** For any candidate build, compare ARM ABI, C library,
   exported/imported interfaces, screen configuration handling and callback
   contracts. Require actual HEVC parameter sets and frame output, not a new
   wrapper version number or an HEVC-related string.
4. **Forwarding and rendering:** Verify unchanged HEVC access units reach the
   video WebSocket, then implement HEVC parameter-set parsing, codec selection,
   browser support checking and keyframe recovery with H.264 fallback.
5. **Acceptance:** Test phone reconnection, still screens, motion, touch,
   audio sync, latency and fallback at Auto resolution. Building and device
   deployment remain separate steps; deployment needs explicit authorization.

The highest-value next check is the Tesla decoder test, followed by confirmation
of the existing T3 authentication/receiver's HEVC eligibility. Until those are
resolved, a drop-in firmware upgrade or a software-only success cannot be promised.
