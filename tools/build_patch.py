#!/usr/bin/env python3
"""Build a patched update_tesla.zip for the Ownice T3 (zjinnova "tesla-linux" channel).

The default package replaces web files, the reported web version and the player
TLS launcher/configuration (ChaCha20 first; opt out with --no-tls-chacha).
Optional Wi-Fi patches add soft-AP improvements and diagnostics. Native packages
also replace receiver files and back up stock files before installation
(see docs/NATIVE_ROLLBACK.md). No keys or certificates are modified or
redistributed: the official firmware is downloaded from the vendor CDN, verified
by MD5, and patches are applied at build time.

Every native HEVC build applies the pinned ChaCha20 NEON patch after HEVC and
ships its build metadata/license. Browser-only builds retain installed NEON by
not replacing z-tesla. See docs/CRYPTO_RELEASES.md before changing native code.

Usage:
    python3 -I tools/build_patch.py                     # download + build
    python3 -I tools/build_patch.py --firmware path.zip # use a local copy
    python3 -I tools/build_patch.py --native-test       # + rollback machinery, receiver unchanged
    python3 -I tools/build_patch.py --native-hevc       # experimental CarPlay HEVC + native rollback
    python3 -I tools/build_patch.py --restore-native --out build/restore_native.zip
    python3 -I tools/build_patch.py --wifi-ac           # + 802.11ac soft AP (version 2.9.11-oc8-ac)
    python3 -I tools/build_patch.py --wifi-diag         # + Wi-Fi diagnostics (version 2.9.11-oc8-diag)
    python3 -I tools/build_patch.py --wifi-ht40         # + 40 MHz soft AP (version 2.9.11-oc8-ht40)
    python3 -I tools/build_patch.py --no-tls-chacha     # stock AES-GCM player TLS (version 2.9.11-oc8-aes)
"""

import argparse
import hashlib
import io
import sys
import tempfile
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
CUSTOM_VERSION = "2.9.11-oc11"

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
ROOT="${ZQ_ROOT:-}"
ZQ="${ZQ_MISC:-/misc}/zq-native"
# Use this package's validator, including when the installed helper is older.
if [ -f "./misc/zq-native/restore.sh" ]; then
  sh ./misc/zq-native/restore.sh || exit 1
else
  echo "$(date '+%Y-%m-%d %H:%M:%S') restore: package lacks restore.sh" \\
    >> "$ROOT/usr/z-loader/zjinnova/web/zq-native.txt"
  exit 1
fi
cp ./usr/z-loader/webversion "$ROOT/usr/z-loader/webversion" || exit 1
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
    ap.add_argument("--version",
                    help=f"web version to report (default {CUSTOM_VERSION}, +'-ac'/'-diag'/'-bw40'/'-ht40' per Wi-Fi option, '-aes' for --no-tls-chacha); "
                         "keep a non-vendor suffix")
    ap.add_argument("--wifi-ac", action="store_true",
                    help="also patch the soft-AP boot scripts for 802.11ac 80 MHz")
    ap.add_argument("--wifi-diag", action="store_true",
                    help="also write read-only Wi-Fi diagnostics to /zq-wifi.txt after boot")
    ap.add_argument("--wifi-ht40", action="store_true",
                    help="run the soft AP at 40 MHz (re-requested from the driver after start-up, with fallback)")
    ap.add_argument("--wifi-bw40-test", action="store_true",
                    help="one-time experiment: ask the AP driver for 40 MHz at runtime (implies --wifi-diag)")
    ap.add_argument("--tls-chacha", action=argparse.BooleanOptionalAction, default=True,
                    help="z-tesla's player sockets prefer ChaCha20 over AES-GCM (cheaper on the T113; default on, "
                         "--no-tls-chacha ships the stock z-tesla-sh.sh)")
    ap.add_argument("--out", default="build/update_tesla.zip")
    native = ap.add_mutually_exclusive_group()
    native.add_argument("--native-test", action="store_true",
                        help="also install the native rollback machinery with unchanged receiver files")
    native.add_argument("--native-hevc", action="store_true",
                        help="native CarPlay HEVC passthrough plus mandatory tested ChaCha20 NEON optimization")
    ap.add_argument("--crypto-source", type=Path,
                    help="pinned OpenSSL sources for HEVC builds (default: verified download/cache in build/crypto-source)")
    ap.add_argument("--native-video-diag", action="store_true",
                    help="with --native-hevc, expose temporary native video checkpoints (adds -vdiag)")
    ap.add_argument("--keep-compressed-video-focus", "--hevc-keep-video-focus",
                    dest="hevc_keep_video_focus", action="store_true",
                    help="experimental: retain compressed video focus across browser disconnect (adds -focus2)")
    ap.add_argument("--native-only", action="store_true",
                    help="with --native-hevc/--native-test, preserve installed player and TLS/Wi-Fi files")
    native.add_argument("--restore-native", action="store_true",
                        help="build a package that only restores the stock native files")
    args = ap.parse_args()
    if args.native_video_diag and not args.native_hevc:
        ap.error("--native-video-diag requires --native-hevc")
    if args.hevc_keep_video_focus and not args.native_hevc:
        ap.error("--hevc-keep-video-focus requires --native-hevc")
    if args.native_only and not (args.native_hevc or args.native_test):
        ap.error("--native-only requires --native-hevc or --native-test")
    if args.wifi_bw40_test:
        args.wifi_diag = True  # the experiment reports through the diagnostics file
    if args.version is None:
        args.version = (CUSTOM_VERSION + ("-ac" if args.wifi_ac else "") + ("-diag" if args.wifi_diag else "")
                        + ("-bw40" if args.wifi_bw40_test else "") + ("-ht40" if args.wifi_ht40 else "")
                        + ("" if args.tls_chacha else "-aes")
                        + ("-vdiag" if args.native_video_diag else "")
                        + ("-focus2" if args.hevc_keep_video_focus else ""))
        if args.native_hevc:
            args.version += "-hevc-neon"

    base = fetch_base(args.firmware)
    if base.read(WEBVERSION_PATH).decode().strip() != BASE_WEBVERSION:
        sys.exit(f"base firmware is not webversion {BASE_WEBVERSION}")

    if args.restore_native:
        write_zip(Path(args.out), args.version, {
            UPDATE_SH_PATH: (RESTORE_UPDATE_SH, 0o755),
            WEBVERSION_PATH: (args.version + "\n", 0o644),
            f"{NATIVE_META}/restore.sh": ((NATIVE_DIR / "restore.sh").read_text(), 0o755),
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
        if args.native_hevc and src.name == "zq-settings.js":
            files[dest] = player_patches.apply(files[dest], [
                ("enable codec selection in native HEVC builds",
                 "const NATIVE_HEVC = false;", "const NATIVE_HEVC = true;", 1),
            ])
    scripts = {}  # boot scripts, installed executable
    for path, patches in wifi_patches.patches(ac=args.wifi_ac, diag=args.wifi_diag,
                                              bw40=args.wifi_bw40_test, ht40=args.wifi_ht40).items():
        scripts[path] = player_patches.apply(base.read(path).decode(), patches)
    # z-tesla-sh.sh is always shipped: patched, or stock to undo an earlier ChaCha20 build.
    for path, patches in tls_patches.SCRIPT_PATCHES.items():
        stock = base.read(path).decode()
        scripts[path] = player_patches.apply(stock, patches) if args.tls_chacha else stock
    if args.tls_chacha:
        files[tls_patches.CONF_PATH] = tls_patches.CONF
    entries = {dest: (content, 0o644) for dest, content in files.items()}
    entries.update({dest: (content, 0o755) for dest, content in scripts.items()})

    if args.native_test or args.native_hevc:
        # Native files: patched z-link.sh (watchdog) plus receiver files as shipped.
        natives = {ZLINK_SH_PATH: (player_patches.apply(base.read(ZLINK_SH_PATH).decode(),
                                                        ZLINK_WATCHDOG_PATCHES), 0o755)}
        if args.native_hevc:
            import json
            import native_hevc
            import build_crypto_neon
            crypto_sources = build_crypto_neon.resolve_sources(args.crypto_source)
            crypto_payload = build_crypto_neon.compile_payload(crypto_sources, native_hevc.tool("clang", "ZQ_CLANG"))
            reports = []
            build_dir = ROOT / "build"
            build_dir.mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="hevc-", dir=build_dir) as directory:
                for name in native_hevc.PINNED:
                    path = f"usr/z-loader/{'bin' if name == 'z-tesla' else 'lib'}/{name}"
                    content, report = native_hevc.patch_native(
                        name, base.read(path), Path(directory) / (name + ".build"),
                        diagnostics=args.native_video_diag,
                        keep_video_focus=args.hevc_keep_video_focus)
                    if name == 'z-tesla':
                        content, crypto_report = build_crypto_neon.patch_binary(content, *crypto_payload)
                        report['hevc_only_sha256'] = report['patched_sha256']
                        report['patched_sha256'] = hashlib.sha256(content).hexdigest()
                        report['crypto_neon'] = crypto_report
                        entries[f"{NATIVE_META}/crypto-build.json"] = (json.dumps(crypto_report, indent=2)+'\n', 0o644)
                        entries[f"{NATIVE_META}/OPENSSL-LICENSE"] = ((crypto_sources/'LICENSE').read_text(), 0o644)
                    natives[path] = (content, 0o755)
                    reports.append(report)
            entries[f"{NATIVE_META}/hevc-build.json"] = (json.dumps(reports, indent=2) + "\n", 0o644)
        else:
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

    if args.native_only:
        allowed = set(natives) | {UPDATE_SH_PATH, WEBVERSION_PATH,
                                  f"{NATIVE_META}/hevc-build.json",
                                  f"{NATIVE_META}/crypto-build.json", f"{NATIVE_META}/OPENSSL-LICENSE",
                                  f"{NATIVE_META}/native.list", f"{NATIVE_META}/restore.sh"}
        entries = {path: value for path, value in entries.items() if path in allowed}
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

    # Verify the actual archive bytes/modes rather than trusting staged inputs.
    # Never leave an apparently usable package after a failed archive check.
    try:
        with zipfile.ZipFile(out) as verified:
            if len(verified.infolist()) != len(entries) or set(verified.namelist()) != set(entries):
                raise ValueError('package inventory differs from build inputs')
            for dest, (content, mode) in entries.items():
                expected = content.encode('utf-8') if isinstance(content, str) else content
                if verified.read(dest) != expected or verified.getinfo(dest).external_attr >> 16 != mode:
                    raise ValueError('package contents or permissions differ: '+dest)
    except Exception:
        out.unlink(missing_ok=True)
        raise

    digest = hashlib.md5(out.read_bytes()).hexdigest()
    print(f"wrote {out} ({out.stat().st_size} bytes, md5 {digest}, version {version})")


if __name__ == "__main__":
    main()
