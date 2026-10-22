#!/usr/bin/env python3
"""Build a patched update_tesla.zip for the Ownice T3 (zjinnova "tesla-linux" channel).

By default the package only replaces web files (entry page, player page and
bundle, video worker, settings script) and the reported web version. --wifi-ac
also patches two soft-AP boot scripts for 802.11ac 80 MHz; --wifi-diag adds
read-only Wi-Fi diagnostics at /zq-wifi.txt (both in tools/wifi_patches.py).
No vendor binaries, keys or certificates are modified or redistributed: the
official firmware is downloaded from the vendor CDN, verified by MD5, and patches
are applied at build time.

Usage:
    python3 -I tools/build_patch.py                     # download + build
    python3 -I tools/build_patch.py --firmware path.zip # use a local copy
    python3 -I tools/build_patch.py --wifi-ac           # + 802.11ac soft AP (version 3.0-oc4-ac)
    python3 -I tools/build_patch.py --wifi-diag         # + Wi-Fi diagnostics (version 3.0-oc4-diag)
    python3 -I tools/build_patch.py --wifi-ht40         # + 40 MHz soft AP (version 3.0-oc4-ht40)
"""

import argparse
import hashlib
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import player_patches  # noqa: E402
import tls_patches  # noqa: E402
import wifi_patches  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

BASE_URL = "https://cdn.zjinnova.com/tesla/en/update_tesla0817.zip"
BASE_MD5 = "3a743be56b60df524825cffcf45238cd"
BASE_WEBVERSION = "2.9.11"

# Custom build identity. Keep the "-oc<N>" suffix so our version never equals a
# vendor version string: the box's "Check for updates" re-offers stock whenever
# current != the CDN's webver, so an always-distinct tag keeps stock rollback
# working from the box's own UI as well as from tools/flash.sh. Bump oc1->oc2...
# Project attribution shown in the player lives in player/zq-settings.js.
CUSTOM_VERSION = "3.0-oc4"

WEB = "usr/z-loader/zjinnova/web"
INDEX_PATH = f"{WEB}/index.html"
PLAYER_PATH = f"{WEB}/player.html"
BUNDLE_PATH = f"{WEB}/assets/index.0917c25d.js"  # the bundle player.html loads
# Our own files, copied into the web root.
LOCAL_FILES = {
    f"{WEB}/worker.js": ROOT / "player" / "worker.js",
    f"{WEB}{player_patches.SETTINGS_SCRIPT}": ROOT / "player" / "zq-settings.js",
}
WEBVERSION_PATH = "usr/z-loader/webversion"
UPDATE_SH_PATH = "update.sh"

CANONICAL_HOST = "www.zbox.link"  # the only name the box's TLS cert covers

# (description, original, replacement). Each original must occur exactly once.
INDEX_PATCHES = [
    (
        "probe HTTPS on the certificate's hostname, not on whatever host the car used (e.g. 3.3.3.3)",
        "document.getElementById(\"innerChecker\").src = `https://${window.location.host}/checker.html?t=${new Date().getTime()}`;",
        f"document.getElementById(\"innerChecker\").src = `https://{CANONICAL_HOST}/checker.html?t=${{new Date().getTime()}}`;",
    ),
    (
        "redirect to the certificate's hostname and drop the screen-height (< 1300px) restriction",
        'if (evtData.id === "redirect" && evtData.forceHttps && ch < 1300) {\n'
        "        window.location.replace(`https://${window.location.host}`);",
        'if (evtData.id === "redirect" && evtData.forceHttps) {\n'
        f"        window.location.replace(`https://{CANONICAL_HOST}`);",
    ),
]


def fetch_base(path):
    if path:
        data = Path(path).read_bytes()
    else:
        print(f"downloading {BASE_URL}", file=sys.stderr)
        with urllib.request.urlopen(BASE_URL, timeout=300) as resp:
            data = resp.read()
    digest = hashlib.md5(data).hexdigest()
    if digest != BASE_MD5:
        sys.exit(f"base firmware MD5 mismatch: got {digest}, expected {BASE_MD5}")
    return zipfile.ZipFile(io.BytesIO(data))


def patch_index(html):
    for desc, old, new in INDEX_PATCHES:
        count = html.count(old)
        if count != 1:
            sys.exit(f"patch '{desc}' expected 1 match, found {count}")
        html = html.replace(old, new)
    return html


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--firmware", help="local copy of the official update zip")
    ap.add_argument("--version",
                    help=f"web version to report (default {CUSTOM_VERSION}, +'-ac'/'-diag'/'-bw40'/'-ht40' per Wi-Fi option, '-tls' for --tls-chacha); "
                         "keep a non-vendor suffix")
    ap.add_argument("--wifi-ac", action="store_true",
                    help="also patch the soft-AP boot scripts for 802.11ac 80 MHz")
    ap.add_argument("--wifi-diag", action="store_true",
                    help="also write read-only Wi-Fi diagnostics to /zq-wifi.txt after boot")
    ap.add_argument("--wifi-ht40", action="store_true",
                    help="run the soft AP at 40 MHz (re-requested from the driver after start-up, with fallback)")
    ap.add_argument("--wifi-bw40-test", action="store_true",
                    help="one-time experiment: ask the AP driver for 40 MHz at runtime (implies --wifi-diag)")
    ap.add_argument("--tls-chacha", action="store_true",
                    help="z-tesla's player sockets prefer ChaCha20 over AES-GCM (cheaper on the T113)")
    ap.add_argument("--out", default="build/update_tesla.zip")
    args = ap.parse_args()
    if args.wifi_bw40_test:
        args.wifi_diag = True  # the experiment reports through the diagnostics file
    if args.version is None:
        args.version = (CUSTOM_VERSION + ("-ac" if args.wifi_ac else "") + ("-diag" if args.wifi_diag else "")
                        + ("-bw40" if args.wifi_bw40_test else "") + ("-ht40" if args.wifi_ht40 else "")
                        + ("-tls" if args.tls_chacha else ""))

    base = fetch_base(args.firmware)
    if base.read(WEBVERSION_PATH).decode().strip() != BASE_WEBVERSION:
        sys.exit(f"base firmware is not webversion {BASE_WEBVERSION}")

    files = {
        INDEX_PATH: patch_index(base.read(INDEX_PATH).decode()),
        PLAYER_PATH: player_patches.apply(base.read(PLAYER_PATH).decode(), player_patches.PLAYER_HTML_PATCHES),
        BUNDLE_PATH: player_patches.apply(base.read(BUNDLE_PATH).decode(), player_patches.BUNDLE_PATCHES),
        WEBVERSION_PATH: args.version + "\n",
    }
    for dest, src in LOCAL_FILES.items():
        files[dest] = src.read_text(encoding="utf-8")
    scripts = {}  # boot scripts, installed executable
    for path, patches in wifi_patches.patches(ac=args.wifi_ac, diag=args.wifi_diag,
                                              bw40=args.wifi_bw40_test, ht40=args.wifi_ht40).items():
        scripts[path] = player_patches.apply(base.read(path).decode(), patches)
    if args.tls_chacha:
        files[tls_patches.CONF_PATH] = tls_patches.CONF
        for path, patches in tls_patches.SCRIPT_PATCHES.items():
            scripts[path] = player_patches.apply(base.read(path).decode(), patches)

    def entry(name, mode):
        info = zipfile.ZipInfo(name)
        info.external_attr = mode << 16  # unix permissions; default writestr leaves 0
        info.compress_type = zipfile.ZIP_DEFLATED
        return info

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        # update.sh is the vendor's own installer (`cp ./* / -fr`); keep it executable.
        z.writestr(entry(UPDATE_SH_PATH, 0o755), base.read(UPDATE_SH_PATH))
        for dest, content in files.items():
            z.writestr(entry(dest, 0o644), content)
        for dest, content in scripts.items():
            z.writestr(entry(dest, 0o755), content)

    digest = hashlib.md5(out.read_bytes()).hexdigest()
    print(f"wrote {out} ({out.stat().st_size} bytes, md5 {digest}, version {args.version})")


if __name__ == "__main__":
    main()
