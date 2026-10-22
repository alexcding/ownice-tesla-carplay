# ownice-tesla-carplay

Community patch for the **Ownice T3 / zjinnova "T-Box"** wireless CarPlay / Android Auto adapter for Tesla.
It fixes the box getting stuck in low-resolution **compatible mode** in the Tesla browser.

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

1. Join the box's Wi-Fi (or use a phone connected to it).
2. Open `http://3.3.3.3`, choose the update file, and upload `build/update_tesla.zip`.
   (The stock uploader posts to `/cgi-bin/upload.cgi`; the box unzips it and runs `update.sh`.)
3. Wait for the reboot; `sh tools/box_info.sh` should report `"ver2": "2.10"`.

### Roll back

The vendor's "Check for updates" compares versions with `!=`, so on 2.10 it offers the official 2.9.11
package again. Or upload the official zip manually.

## Viewing CarPlay on a Mac

The box serves the player by hostname, so `www.zbox.link` must resolve to `3.3.3.3`:

```sh
open -na "Google Chrome" --args --user-data-dir=/tmp/zbox-chrome --no-proxy-server \
  --host-resolver-rules="MAP www.zbox.link 3.3.3.3" https://www.zbox.link/
```

## Tools

| Script | Purpose |
|---|---|
| `tools/build_patch.py` | build the patched update package |
| `tools/box_info.sh` | read-only status: firmware version, channel, Wi-Fi, certificate expiry |

See [AGENTS.md](AGENTS.md) for device internals.

## License

MIT. Covers the code in this repository only, not the vendor firmware.
