#!/usr/bin/env python3
"""Build a patched update_tesla.zip for the Ownice T3 (zjinnova "tesla-linux" channel).

The default package only replaces web files (entry page, player page and bundle,
video worker, settings script) and the reported web version. Native packages also
replace receiver files and ship an installer that backs up the stock files first
(see docs/NATIVE_ROLLBACK.md). No keys or certificates are modified or
redistributed: the official firmware is downloaded from the vendor CDN, verified
by MD5, and patches are applied at build time.

Usage:
    python3 -I tools/build_patch.py                     # download + build
    python3 -I tools/build_patch.py --firmware path.zip # use a local copy
    python3 -I tools/build_patch.py --native-test       # + rollback machinery, receiver unchanged
    python3 -I tools/build_patch.py --restore-native --out build/restore_native.zip
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

# Native (receiver) packages. Any package that replaces a native file ships
# tools/native/update.sh instead of the vendor installer: it backs up the stock
# files to /misc/zq-native/stock first, and the patched z-link.sh restores them
# automatically if the receiver keeps crashing. See docs/NATIVE_ROLLBACK.md.
NATIVE_DIR = ROOT / "tools" / "native"
NATIVE_META = "misc/zq-native"  # copied to /misc/zq-native by the installer
ZLINK_SH_PATH = "usr/z-loader/bin/z-link.sh"
ZLINK_WATCHDOG_PATCHES = [
    (
        "restore the stock native files if the receiver exits 5 times within 120 s",
        "while true;do\n"
        "    $EXE_PATH/bin/z-link \n"
        "    sleep 1 \n"
        "done",
        "ZQ=/misc/zq-native\n"
        "fails=0\n"
        "first=0\n"
        "while true;do\n"
        "    $EXE_PATH/bin/z-link \n"
        "    if [ -f $ZQ/active ]; then\n"
        "        now=$(date +%s)\n"
        "        if [ $((now - first)) -gt 120 ]; then first=$now; fails=0; fi\n"
        "        fails=$((fails + 1))\n"
        "        if [ $fails -ge 5 ]; then\n"
        "            echo \"$(date '+%Y-%m-%d %H:%M:%S') watchdog: z-link exited $fails times in 120 s\" \\\n"
        "                >> /usr/z-loader/zjinnova/web/zq-native.txt\n"
        "            sh $ZQ/restore.sh\n"
        "            fails=0\n"
        "        fi\n"
        "    fi\n"
        "    sleep 1 \n"
        "done",
        1,
    ),
]
# Native files a test package ships unchanged, to exercise install and restore
# on a box without changing receiver behaviour.
NATIVE_TEST_FILES = ["usr/z-loader/lib/libScreenStream.so"]

RESTORE_UPDATE_SH = """#!/bin/sh
# Restore package: put back the stock native files saved by a native install.
if [ -f /misc/zq-native/restore.sh ]; then
  sh /misc/zq-native/restore.sh
else
  echo "$(date '+%Y-%m-%d %H:%M:%S') restore: no /misc/zq-native/restore.sh" \\
    >> /usr/z-loader/zjinnova/web/zq-native.txt
fi
cp ./usr/z-loader/webversion /usr/z-loader/webversion
sync
"""

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
    ap.add_argument("--version", default=CUSTOM_VERSION,
                    help=f"web version to report (default {CUSTOM_VERSION}); keep a non-vendor suffix")
    ap.add_argument("--out", default="build/update_tesla.zip")
    native = ap.add_mutually_exclusive_group()
    native.add_argument("--native-test", action="store_true",
                        help="also install the native rollback machinery with unchanged receiver files")
    native.add_argument("--restore-native", action="store_true",
                        help="build a package that only restores the stock native files")
    args = ap.parse_args()

    base = fetch_base(args.firmware)
    if base.read(WEBVERSION_PATH).decode().strip() != BASE_WEBVERSION:
        sys.exit(f"base firmware is not webversion {BASE_WEBVERSION}")

    if args.restore_native:
        write_zip(Path(args.out), args.version, {
            UPDATE_SH_PATH: (RESTORE_UPDATE_SH, 0o755),
            WEBVERSION_PATH: (args.version + "\n", 0o644),
        })
        return

    files = {
        INDEX_PATH: patch_index(base.read(INDEX_PATH).decode()),
        PLAYER_PATH: player_patches.apply(base.read(PLAYER_PATH).decode(), player_patches.PLAYER_HTML_PATCHES),
        BUNDLE_PATH: player_patches.apply(base.read(BUNDLE_PATH).decode(), player_patches.BUNDLE_PATCHES),
        WEBVERSION_PATH: args.version + "\n",
    }
    for dest, src in LOCAL_FILES.items():
        files[dest] = src.read_text(encoding="utf-8")
    entries = {dest: (content, 0o644) for dest, content in files.items()}

    if args.native_test:
        # Native files: patched z-link.sh (watchdog) plus receiver files as shipped.
        natives = {ZLINK_SH_PATH: (player_patches.apply(base.read(ZLINK_SH_PATH).decode(),
                                                        ZLINK_WATCHDOG_PATCHES), 0o755)}
        for path in NATIVE_TEST_FILES:
            natives[path] = (base.read(path), 0o755)
        listing = "".join(f"{hashlib.md5(base.read(p)).hexdigest()} {p}\n" for p in natives)
        entries.update(natives)
        entries[f"{NATIVE_META}/native.list"] = (listing, 0o644)
        entries[f"{NATIVE_META}/restore.sh"] = ((NATIVE_DIR / "restore.sh").read_text(), 0o755)
        entries[UPDATE_SH_PATH] = ((NATIVE_DIR / "update.sh").read_text(), 0o755)
    else:
        # update.sh is the vendor's own installer (`cp ./* / -fr`); keep it executable.
        entries[UPDATE_SH_PATH] = (base.read(UPDATE_SH_PATH), 0o755)

    write_zip(Path(args.out), args.version, entries)


def write_zip(out, version, entries):
    def entry(name, mode):
        info = zipfile.ZipInfo(name)
        info.external_attr = mode << 16  # unix permissions; default writestr leaves 0
        info.compress_type = zipfile.ZIP_DEFLATED
        return info

    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        # update.sh first, as in the vendor package
        for dest in sorted(entries, key=lambda d: d != UPDATE_SH_PATH):
            content, mode = entries[dest]
            z.writestr(entry(dest, mode), content)

    digest = hashlib.md5(out.read_bytes()).hexdigest()
    print(f"wrote {out} ({out.stat().st_size} bytes, md5 {digest}, version {version})")


if __name__ == "__main__":
    main()
