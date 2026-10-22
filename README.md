# ownice-tesla-carplay

Community revival project for the discontinued **Ownice T3 / zjinnova "T-Box"** wireless
CarPlay / Android Auto adapter for Tesla. We aim to improve the overall adapter experience:
Wi-Fi and Bluetooth reliability, phone compatibility, native services, video and audio quality,
latency, browser playback, diagnostics, and maintainable updates.

The default update improves the browser player, resolution, latency and TLS configuration.
An opt-in native package adds CarPlay HEVC negotiation, compressed passthrough and a
JPEG/H.264/HEVC setting. HEVC playback is verified on the owner's T3 in **Mac Chrome**;
Tesla-browser playback and broader hardware compatibility remain untested.

Current default build version: **2.9.11-oc8**. The complete experimental package
**3.0-oc8-hevc-preview.1** is now installed on the owner's adapter, including the
browser cleanup and rollback validation fixes. Its install log requires an explicit
reboot, not yet performed. A fresh-session refresh passed previously through the
Mac proxy; post-install playback and extended idle/return remain under validation. See the
[diagnostic record](docs/HEVC_VIDEO_DIAGNOSTICS.md) for the exact evidence.

See the [firmware improvement survey](docs/FIRMWARE_IMPROVEMENTS.md) for Wi-Fi, CarPlay,
Android Auto, audio/video, service reliability and update work, and the
[HEVC research](docs/HEVC_RESEARCH.md) for receiver and browser codec requirements.

The [receiver update comparison](docs/RECEIVER_CANDIDATES.md) documents a newer
**AirPlay/320.17.6** receiver found in CarlinKit T2C firmware, versus this T3 base's
**280.3**, and evaluates LIVI and CatPlay source projects. No compatible T3 receiver
upgrade has been verified; the T2C library uses a different API and dependencies.
Our working experimental HEVC path patches the existing T3 receiver instead.

> Unofficial. Not affiliated with Ownice or zjinnova. Flashing modified firmware is at your own risk.

## The problem

The box's player only uses the full-resolution H.264 stream (WebCodecs `VideoDecoder` over `wss://…:5002`)
when the page is loaded over **HTTPS on `www.zbox.link`**, the only name its TLS certificate covers.
Otherwise it falls back to a low-res JPEG preview over plain HTTP.

The stock entry page (`index.html`, firmware 2.9.11) tries to upgrade to HTTPS, but:

1. it probes `https://<current host>/checker.html`. When the car opened `http://3.3.3.3`, that probe hits
   a certificate-name mismatch, fails silently, and the page stays on HTTP;
2. it only redirects when the window is shorter than 1300 px (`ch < 1300`).

## Quick fix, no flashing

In the Tesla browser (connected to the box's Wi-Fi), open exactly:

```
https://www.zbox.link
```

and bookmark it. If that gives you HD, you may not need the patch.

## Download and install

Get the experimental [2.9.11-oc8-hevc release](https://github.com/alexcding/ownice-tesla-carplay/releases/tag/2.9.11-oc8-hevc).
Download the **update** ZIP, leave it zipped, connect to the adapter's Wi-Fi,
and open `http://3.3.3.3`. Use its normal firmware update option to upload the ZIP.
Wait for installation to finish, then reboot the adapter and reconnect the phone.
No terminal or on-device patching is needed. The separate **restore** ZIP is for rollback.

Supported base: **Ownice T3 stock 2.9.11 / build 202402031913**. Older firmware such
as 2.9.2 and other models are unverified. Reach the supported official base through
the normal vendor update path before applying this package.

The release, ZIP names and package version are **`2.9.11-oc8-hevc`**. This names
the supported stock base explicitly; `oc8` identifies our custom revision. The
previous `3.0-...` identifiers remain only in historical device/build records.
Compared with the installed preview ZIP, the renamed update changes only the
reported webversion; its native/browser payloads are identical. The renamed ZIP
has not been flashed. The owner reports no regression so far with the preview;
long idle/return, Tesla playback and live recovery testing remain pending.

## Build from stock 2.9.11

The builder downloads the pinned official archive (or accepts `--firmware /path/to/stock.zip`),
verifies its MD5 and applies strict, counted replacements. Native patches also require exact
stock binary SHA-256 matches. Other firmware/board versions are not supported by inference.
Only repository code and patch descriptions belong in Git; never publish the vendor archive,
which includes private keys. Generated update packages select only the needed files.

Default browser/TLS package:

```sh
python3 -I tools/build_patch.py --out build/update_tesla.zip
```

This includes the HTTPS entry fix, player bundle/settings/worker, custom webversion,
and ChaCha20 TLS launcher/config. Wi-Fi boot-script experiments remain opt-in.
`--no-tls-chacha` restores the stock TLS launcher.

Experimental complete HEVC package, including the latest browser cleanup fix:

```sh
python3 -m venv build/crypto-env
build/crypto-env/bin/pip install -r tools/requirements-crypto-neon.txt
build/crypto-env/bin/python -I tools/build_patch.py --native-hevc --native-video-diag \
  --keep-compressed-video-focus --version 2.9.11-oc11-hevc-neon \
  --out build/update_tesla_hevc_2.9.11-oc11-hevc-neon.zip
```

Native builds additionally need an ARM-capable Clang/LLD toolchain; see
[the native path](docs/CARPLAY_HEVC_NATIVE_PATH.md). Every HEVC build now also
includes the tested ChaCha20 NEON optimization, applied after the HEVC patches.
Pinned public OpenSSL sources/license are downloaded into ignored
`build/crypto-source` and verified; `--crypto-source <directory>` allows a prepared
offline cache. Unknown source, compiled payload or derived receiver hashes fail
the build. See [crypto release instructions](docs/CRYPTO_RELEASES.md) and
[device results](docs/CRYPTO_RESEARCH.md).

Browser/Wi-Fi-only updates leave the installed receiver untouched, preserving
its NEON optimization. Native HEVC releases cannot silently drop NEON when adding
other features. Stock restore is an intentional exception. This package patches
`libAirPlay.so`, `libScreenStream.so`, `libzlink-main.so`, `z-tesla` and `z-link.sh`,
and includes the browser/TLS files. It can be installed directly over the supported stock
2.9.11 base; earlier experimental packages are not prerequisites. Without an explicit `--version`, the focus option adds
`-focus2` and native diagnostics add `-vdiag`. The release command above sets a
fixed `2.9.11-oc11-hevc-neon` version and includes temporary delivery checkpoints. **Do not use `--native-only` for a first complete installation**:
that option deliberately preserves the installed browser/TLS/Wi-Fi files.

A new browser profile defaults to H.264. In Video, select a codec, use **Apply & reload**,
then reconnect the phone. Reloading alone does not renegotiate H.264 versus HEVC.
For JPEG, disconnect the phone before saving, then reconnect. The live stream codec
in the stats is the actual decoder; the selected setting is the next-session preference.
See [codec switching](docs/CODEC_SWITCH_DESIGN.md) and
[reconnect recovery](docs/HEVC_RECONNECT_FIX.md).

### Install and activate

After reviewing the package and explicitly authorizing the device update, join the
box's network (or use an existing route to it) and upload the chosen package:

```sh
tools/flash.sh update build/update_tesla_hevc_2.9.11-oc8-hevc.zip
tools/flash.sh status
```

The web UI at `http://3.3.3.3` also accepts the package. Upload acceptance and a new
version marker do not prove that native code is active. Native install/restore scripts
log **REBOOT REQUIRED** and do not reboot automatically. Explicitly reboot/power-cycle
after installation completes, then reconnect the phone. Do not interrupt installation.
Keep only one player client connected while testing.

### Roll back

Build and retain a native restore package before an experimental installation:

```sh
python3 -I tools/build_patch.py --restore-native --out build/restore_native.zip
tools/flash.sh update build/restore_native.zip
```

The restore uses stock backups saved by the native installer and requires a subsequent
explicit reboot. It restores the five native files and clears codec/diagnostic state;
its bundled validator checks backup checksums and manifest completeness against
installation metadata and saved files before replacing any file.
An invalid manifest or backup aborts restoration and preserves recovery state.
The custom browser/TLS files remain. The crash watchdog handles repeated receiver exits,
not a running receiver with a black screen. Hardware watchdog/restore recovery still needs
validation. See [native rollback](docs/NATIVE_ROLLBACK.md).

Official full-stock download: [vendor 2.9.11 ZIP](https://cdn.zjinnova.com/tesla/en/update_tesla0817.zip),
expected MD5 **`3a743be56b60df524825cffcf45238cd`**. Download and keep a private
copy in case the vendor link disappears. Do not rehost this archive: it contains
private TLS keys. Our attached native restore ZIP uses the adapter's saved backup
and needs no vendor download; a complete stock reinstall needs the official ZIP.

For a complete stock reinstall, `tools/flash.sh rollback` downloads and uploads the
MD5-checked stock 2.9.11 archive. The vendor's Check for updates also re-offers stock
because our version has a non-vendor suffix. Preserve normal network/update access.

## Viewing CarPlay on a Mac

The box serves the player by hostname, so `www.zbox.link` must resolve to `3.3.3.3`:

```sh
open -na "Google Chrome" --args --user-data-dir=/tmp/zbox-chrome --no-proxy-server \
  --host-resolver-rules="MAP www.zbox.link 3.3.3.3" https://www.zbox.link/
```

## Tools

| Script | Purpose |
|---|---|
| `tools/build_patch.py` | build the update package (default 2.9.11-oc11; native HEVC includes NEON) |
| `tools/flash.sh` | flash / roll back over the web UI: `status`, `update [zip]`, `rollback [zip]` |
| `tools/dev_player.py` | test the patched player against a live box without flashing (local HTTPS proxy) |
| `tools/box_info.sh` | read-only status: firmware version, channel, Wi-Fi, certificate expiry |

See [AGENTS.md](AGENTS.md) for contribution rules and [the device reference](docs/DEVICE_REFERENCE.md) for device internals.

## License

MIT. Covers the code in this repository only, not the vendor firmware.
