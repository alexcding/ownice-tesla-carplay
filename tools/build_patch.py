#!/usr/bin/env python3
"""Build a patched update_tesla.zip for the Ownice T3 (zjinnova "tesla-linux" channel).

The package only replaces the web entry page and the reported web version.
No vendor binaries, keys or certificates are modified or redistributed: the
official firmware is downloaded from the vendor CDN, verified by MD5, and the
patch is applied at build time.

Usage:
    python3 -I tools/build_patch.py                     # download + build
    python3 -I tools/build_patch.py --firmware path.zip # use a local copy
"""

import argparse
import hashlib
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

BASE_URL = "https://cdn.zjinnova.com/tesla/en/update_tesla0817.zip"
BASE_MD5 = "3a743be56b60df524825cffcf45238cd"
BASE_WEBVERSION = "2.9.11"

INDEX_PATH = "usr/z-loader/zjinnova/web/index.html"
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
    ap.add_argument("--version", default="2.10", help="web version to report (default 2.10)")
    ap.add_argument("--out", default="build/update_tesla.zip")
    args = ap.parse_args()

    base = fetch_base(args.firmware)
    if base.read(WEBVERSION_PATH).decode().strip() != BASE_WEBVERSION:
        sys.exit(f"base firmware is not webversion {BASE_WEBVERSION}")

    index = patch_index(base.read(INDEX_PATH).decode())

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        # update.sh is the vendor's own installer: `cp ./* / -fr`
        z.writestr(zipfile.ZipInfo(UPDATE_SH_PATH), base.read(UPDATE_SH_PATH))
        z.writestr(INDEX_PATH, index)
        z.writestr(WEBVERSION_PATH, args.version + "\n")

    digest = hashlib.md5(out.read_bytes()).hexdigest()
    print(f"wrote {out} ({out.stat().st_size} bytes, md5 {digest}, version {args.version})")


if __name__ == "__main__":
    main()
