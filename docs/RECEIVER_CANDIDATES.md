# CarPlay receiver update candidates

**Follow-up status, 2026-10-22:** no third-party receiver candidate below is
a verified drop-in T3 upgrade. Separately, this repository's patch to the existing
T3 receiver now provides observed HEVC playback in Mac Chrome on the owner's
adapter. See [the native path](CARPLAY_HEVC_NATIVE_PATH.md) and
[diagnostics](HEVC_VIDEO_DIAGNOSTICS.md); Tesla compatibility remains untested.

Follow-up research: 2026-10-21, America/Toronto. These results come from public
release metadata, downloaded artifacts and selected source files. Nothing was
installed or executed on an adapter. Vendor artifacts stay outside Git at
`/private/tmp/ownice-firmware-candidates`.

## Result

**A newer native receiver was found: CarlinKit T2C firmware contains
`AirPlay/320.17.6`, versus the T3's `AirPlay/280.3`. It is not an ABI-compatible
replacement for the T3 library.** Its HEVC capability is unconfirmed.

There are also current GitHub receiver implementations with HEVC-related code.
None has a verified Ownice T3 build. A source port is a concrete development
option, separate from installing another model's firmware.

## Downloaded vendor firmware

| Candidate | Firmware identity from public manifest | Inspected receiver | Result |
|---|---|---|---|
| T3 baseline | 2.9.11; platform 202402031913 | `usr/z-loader/lib/libAirPlay.so` | `AirPlay/280.3`; 1S70 build path |
| T2C 1508 stable | `26020416.1508.1` | SquashFS `/app/libcarplay.so` | `AirPlay/320.17.6` |
| T2C 1552 stable | `26020909.1552.1` | SquashFS `/app/libcarplay.so` | Same receiver bytes as 1508 stable |
| T2C 1508 public test image | Manifest test entry `26090414.1508.1` | SquashFS `/app/libcarplay.so` | Same receiver bytes as stable; no receiver upgrade in this candidate |

The test entry is listed for selected device identifiers. Its image was publicly
downloadable without submitting an identifier. It was inspected offline only;
no device identity was used or spoofed. The firmware identities above are the
manifest's labels, not independent verification of every component's build date.

Public sources:

- [1508 manifest](https://cpbox.oss-cn-shenzhen.aliyuncs.com/1508/version.json)
- [1552 manifest](https://cpbox.oss-cn-shenzhen.aliyuncs.com/1552/version.json)
- [1508 stable image](https://cpbox.oss-cn-shenzhen.aliyuncs.com/1508/update.img)
- [1552 stable image](https://cpbox.oss-cn-shenzhen.aliyuncs.com/1552/update.img)
- [1508 test image](https://cpbox.oss-cn-shenzhen.aliyuncs.com/1508/test.img)
- [Public T2C builder documenting this container](https://github.com/randolfrias/carlinkit-t2c-tesla-fix/blob/main/scripts/build.sh)

All three downloaded image wrappers passed their embedded MD5 checks. This checks
internal integrity, not a cryptographic publisher signature. The outer container
contains a CPIO SWUpdate payload; the `customer` member is ordinary SquashFS 4/XZ.
Only the receiver file was selected from that filesystem for native inspection.

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| 1508 stable image | 8,296,493 | `9a9f0efd2cb22695919ce9a057b9dd6f08f47dab3476f708f8a57ab30cdc2337` |
| 1552 stable image | 8,299,053 | `981509640a352f516d57eca903cbbecfada368e43ca893b528a35e8671e06652` |
| 1508 test image | 8,298,541 | `504a75492f3372689229579d56f7f8be389b70588764c246e124dc7f5db147a7` |
| Receiver in all three images | 2,334,872 | `95459731bf557d617c9809168d24f5aacf64882c7d823ae0313b14c36ff29be0` |

### Why the newer T2C receiver is not a direct replacement

Both the T3 library and T2C receiver are 32-bit ARM ELF, but their interfaces differ:

| Property | T3 `libAirPlay.so` | T2C `libcarplay.so` |
|---|---|---|
| ELF machine | ARM | ARM |
| Exported dynamic names observed | 298 | 64 |
| API examples | `AirPlayReceiverSessionSendCommand`, `AirPlayReceiverServerGetTypeID` | `CreateCarPlay`, `StartCarPlay`, `StopCarPlay`, `SetVideoPlayerOutputFormat` |
| Needed libraries | `libc.so.6`, `libdns_sd.so`, `libm.so.6`, `libstdc++.so.6`, `libAirPlaySupport.so`, `libCoreUtils.so` | `libdns_sd.so`, `libiap.so`, `libstdc++.so.6`, `libc.so`, `libgcc_s.so.1` |

The two observed dynamic export-name sets have **zero overlap**. Matching CPU
architecture does not supply the missing API, audio/video callbacks, Bluetooth
handoff or authentication integration. Adapting this library would require a new
integration layer and compatible dependencies, not renaming and copying the file.
The differing libc sonames do not by themselves establish which libc implementation
is used or whether a compatible runtime could be provided.

The receiver has `H.264`, `H264Source` and a screen-H.264 path string. Searches
found no HEVC/H.265/hvcC/hvc1/hev1 markers. This is **not proof that HEVC is absent**;
it means there is no positive HEVC evidence from this inspection. The specific
Apple Communication Plug-in revision was not recovered from the T2C binary.

REA/Hopper independently located `AirPlay/320.17.6` at address `0x1fa5af`:
evidence `ev_f5b3a8ffd8ac0b11e9f6894766abd0014d32d6b40de75434d821a68d024754f3`.
Other counts/dependencies above came from an offline ELF section/dynamic-table
reader; no candidate native code was run. These observations do not establish
runtime compatibility or successful HEVC negotiation.

## GitHub firmware binaries checked

The complete public tree inventory from
[ludwig-v/wireless-carplay-dongle-reverse-engineering](https://github.com/ludwig-v/wireless-carplay-dongle-reverse-engineering)
was retrieved without truncation. Two relevant extracted receiver executables
were downloaded individually:

| Candidate | SHA-256 | Inspection result |
|---|---|---|
| [U2AW AUTOKIT 2025.10.31.1346 AppleCarPlay](https://github.com/ludwig-v/wireless-carplay-dongle-reverse-engineering/blob/master/Firmware/U2AW/_AUTOKIT/2025.10.31.1346/Extracted/usr/sbin/AppleCarPlay) | `86fae1498930d8f57a7ee4d7871761abb60118cf3af9f92192ab219af0a65c3f` | ARM ELF executable, no section header; receiver version not recovered |
| [A15W 2025.02.25.1521 AppleCarPlay](https://github.com/ludwig-v/wireless-carplay-dongle-reverse-engineering/blob/master/Firmware/A15W/_/2025.02.25.1521/Extracted/usr/sbin/AppleCarPlay) | `37da8959fd00eabc729ca722bad1db9b42c3048cc59aff1f5e67ddaef14c918c` | ARM ELF executable, no section header; receiver version not recovered |

Neither exposed an AirPlay/plug-in version or positive HEVC marker in the focused
byte-string scan. The archive's 2025 firmware label is not evidence of a newer
Apple receiver. These executables are not replacements for the T3's shared-library
API. No protected firmware payload was decrypted and no bundled identities were read.

## GitHub source candidates

### LIVI v9.4.0

[Release](https://github.com/f-io/LIVI/releases/tag/v9.4.0), published
2026-10-10, commit `c068c6c2bb31f209cd7b2dffd8d2ef3d78fe32ce`.

The project describes a native CarPlay receiver with HEVC, real MFi-coprocessor
authentication and Linux support. Its tagged codec module was read locally and
contains H.265 parser/caps/decoder selection. This supports a source-port lead;
it does not demonstrate a working T3 receiver or that all project features were
tested by this investigation.

- [Tagged codec source](https://github.com/f-io/LIVI/blob/v9.4.0/native/livi-gst-video/rust/codec/src/lib.rs)
- [Receiver crate manifest](https://github.com/f-io/LIVI/blob/main/native/livi-helperd/crates/livi-cp/Cargo.toml)
- [Project and authentication integration](https://github.com/f-io/LIVI)
- [GPLv3 license](https://github.com/f-io/LIVI/blob/main/LICENSE)

Released Linux desktop builds are ARM64 and x86-64, not the T3's ARMv7. Its
separate dongle firmware assets target i.MX6ULL, AX520 and V821B variants, not T113.
The complete desktop application is not a drop-in embedded receiver. A useful
next feasibility study would isolate the receiver core, evaluate ARMv7 building,
integrate the T3's existing authorized MFi hardware and forward compressed video
to the existing browser rather than rendering a local desktop UI. That work has
not been built or validated. Preserve upstream licensing if adopting its code.

### CatPlay v0.17.0

[Release](https://github.com/catplay-labs/catplay/releases/tag/v0.17.0), published
2026-10-17. The tagged tree was retrieved without truncation.

It is an independent Rust protocol implementation rather than an update to an
Apple Communication Plug-in binary. Its internal crate version and project
release number must not be compared numerically with AirPlay/280.3.

The inspected [HEVC configuration module](https://github.com/catplay-labs/catplay/blob/v0.17.0/carplay/catplay_carplay/src/video/hvcc.rs)
parses/serializes HEVC configuration and VPS/SPS/PPS. Its
[Annex B converter](https://github.com/catplay-labs/catplay/blob/v0.17.0/carplay/catplay_carplay/src/video/annexb_converter.rs)
handles length-prefixed payloads, and its receiver API exposes a screen sink.
These are concrete source references for video integration, not proof of T3 HEVC.

The [published support matrix](https://github.com/catplay-labs/catplay) includes
Ingenic X1600 and Allwinner V821B/RTL8733BS. T113 is absent. A shared radio family
does not make the V821B image compatible with the T3. The project requires an MFi
chip and describes a wireless-to-wired dongle use case, so browser forwarding would
need adaptation. A top-level project license was not found in the inspected tree,
and the checked workspace/receiver manifests did not declare one. Reuse terms
remain unresolved; do not assume public source visibility supplies a reuse license.

## Recommendation

There is now a verified newer **vendor receiver version**, but no verified
compatible T3 binary. For HEVC, the strongest development lead with explicit
source licensing is a feasibility study of LIVI's receiver core on ARMv7 with
the T3's existing authentication hardware and a browser-streaming backend.
CatPlay is another technically relevant reference pending reuse clarification.
The T2C library is useful for version/API comparison, with HEVC still unconfirmed.

Before implementation, test HEVC decoding on the actual Tesla and establish
supported access to the T3's existing MFi interface. The version/API research
above does not resolve either requirement. Keep the original hardware/network
services intact until a compatible receiver and rollback path are demonstrated.
