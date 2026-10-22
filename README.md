# ownice-tesla-carplay

Community revival project for the discontinued **Ownice T3 / zjinnova "T-Box"** wireless
CarPlay / Android Auto adapter for Tesla. We aim to improve the overall adapter experience:
Wi-Fi and Bluetooth reliability, phone compatibility, native services, video and audio quality,
latency, browser playback, diagnostics, and maintainable updates.

The current update package improves the browser player, including the low-resolution
**compatible mode** problem. Research also covers native receiver updates and HEVC. Findings and
fixes should be reproducible and explicit about supported firmware and hardware so the community
can keep existing adapters useful. Proposed capabilities are not claims of working features.

See the [firmware improvement survey](docs/FIRMWARE_IMPROVEMENTS.md) for Wi-Fi, CarPlay,
Android Auto, audio/video, service reliability and update work, and the
[HEVC research](docs/HEVC_RESEARCH.md) for receiver and browser codec requirements.

The [receiver update comparison](docs/RECEIVER_CANDIDATES.md) documents a newer
**AirPlay/320.17.6** receiver found in CarlinKit T2C firmware, versus this T3 base's
**280.3**, and evaluates LIVI and CatPlay source projects. No compatible T3 receiver
upgrade or working HEVC path has been verified; the T2C library uses a different
API and dependencies.

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

## Patch 2.10

`tools/build_patch.py` downloads the official 2.9.11 package from the vendor CDN, verifies its MD5,
and builds a small `update_tesla.zip` that replaces only:

| File | Change |
|---|---|
| `usr/z-loader/zjinnova/web/index.html` | probe and redirect to `https://www.zbox.link`; remove the `ch < 1300` condition |
| `usr/z-loader/webversion` | `2.10` |

No binaries, keys or certificates are changed or redistributed.

```sh
python3 -I tools/build_patch.py           # → build/update_tesla.zip
```

### Install

Join the box's Wi-Fi, then:

```sh
tools/flash.sh update        # uploads build/update_tesla.zip, waits for reboot, prints the new version
```

Or by hand: open `http://3.3.3.3`, choose the update file, and upload `build/update_tesla.zip`. The box posts
it to `/cgi-bin/upload.cgi`, unzips it and runs `update.sh`, then reboots. `tools/flash.sh status` should then
report `"ver2": "3.0"`. The LED blinks during the install — do not pull power.

### Roll back

```sh
tools/flash.sh rollback      # downloads + flashes stock 2.9.11 (MD5-checked)
```

Only web files change, so rollback is a normal flash — no special recovery needed. The vendor's "Check for
updates" also re-offers stock, since it treats any different version as an update.

## Viewing CarPlay on a Mac

The box serves the player by hostname, so `www.zbox.link` must resolve to `3.3.3.3`:

```sh
open -na "Google Chrome" --args --user-data-dir=/tmp/zbox-chrome --no-proxy-server \
  --host-resolver-rules="MAP www.zbox.link 3.3.3.3" https://www.zbox.link/
```

## Tools

| Script | Purpose |
|---|---|
| `tools/build_patch.py` | build the patched update package (`build/update_tesla.zip`, default version 3.0) |
| `tools/flash.sh` | flash / roll back over the web UI: `status`, `update [zip]`, `rollback [zip]` |
| `tools/dev_player.py` | test the patched player against a live box without flashing (local HTTPS proxy) |
| `tools/box_info.sh` | read-only status: firmware version, channel, Wi-Fi, certificate expiry |

See [AGENTS.md](AGENTS.md) for contribution rules and [the device reference](docs/DEVICE_REFERENCE.md) for device internals.

## License

MIT. Covers the code in this repository only, not the vendor firmware.
