#!/usr/bin/env python3
"""Run the patched CarPlay player against a live box, without flashing it.

A local HTTPS server reverse-proxies the box's web UI, applies
tools/player_patches.py on the fly and serves player/worker.js and
player/zq-settings.js. The page's WebSockets still go straight to the box.
Resolution, frame rate and codec are chosen in the page (the "Video" button in
the home toolbar).

    python3 -I tools/dev_player.py

Then open the printed Chrome command. Ctrl-C to stop.

A box already running our firmware serves a patched player, which the stock
patch anchors no longer match: pass --firmware (a local copy of the official
update zip, or "cdn" to download it) to patch the stock player instead.
"""

import argparse
import http.client
import http.server
import re
import ssl
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_patch  # noqa: E402
import player_patches  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOCAL_FILES = {
    "/worker.js": ROOT / "player" / "worker.js",
    player_patches.SETTINGS_SCRIPT: ROOT / "player" / "zq-settings.js",
}
CERT_DIR = ROOT / "build" / "devcert"

DEV_HOST = "www.zbox.link"  # mapped to 127.0.0.1 in the browser
BOX_HOST = "zbox.link"  # mapped to the box; WebSockets connect here

# Dev only: point the page's WebSockets at the box instead of this proxy.
DEV_BUNDLE_PATCHES = player_patches.BUNDLE_PATCHES + [
    ("sockets go to the box", "Bs=window.location.hostname", f'Bs="{BOX_HOST}"', 1),
]


def ensure_cert():
    crt, key = CERT_DIR / "dev.crt", CERT_DIR / "dev.key"
    if not crt.exists():
        CERT_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "365",
             "-keyout", str(key), "-out", str(crt), "-subj", f"/CN={DEV_HOST}",
             "-addext", f"subjectAltName=DNS:{DEV_HOST}"],
            check=True, capture_output=True,
        )
    return crt, key


def stock_player(firmware):
    """Stock player.html and bundle from the official update zip, by served path."""
    if not firmware:
        return {}
    z = build_patch.fetch_base(None if firmware == "cdn" else firmware)
    web = build_patch.WEB
    return {"/player.html": z.read(build_patch.PLAYER_PATH),
            build_patch.BUNDLE_PATH[len(web):]: z.read(build_patch.BUNDLE_PATH)}


def make_handler(args):
    box_ctx = ssl._create_unverified_context()
    stock = stock_player(args.firmware)

    def box_native_hevc():
        """Mirror the box's own NATIVE_HEVC, but only for the codec-switch package
        (legacy oc7+ and current 2.9.11-ocN builds, carrying "vmode"). Legacy oc6 also set
        NATIVE_HEVC yet advertises HEVC unconditionally, so its H.264/JPEG choices
        would not apply. Unreachable or unknown means False (H.264/JPEG menu)."""
        conn = http.client.HTTPSConnection(args.box, 443, context=box_ctx, timeout=3)
        try:
            conn.request("GET", player_patches.SETTINGS_SCRIPT, headers={"Host": DEV_HOST})
            resp = conn.getresponse()
            body = resp.read()
            return resp.status == 200 and b"const NATIVE_HEVC = true;" in body and b'"vmode"' in body
        except (OSError, http.client.HTTPException):
            return False
        finally:
            conn.close()

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *a):
            if args.verbose:
                super().log_message(fmt, *a)

        def send_body(self, status, headers, body):
            self.send_response(status)
            for k, v in headers:
                if k.lower() not in ("content-length", "transfer-encoding", "connection", "content-encoding"):
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def proxy(self):
            path = self.path.split("?")[0]
            if path in LOCAL_FILES:
                data = LOCAL_FILES[path].read_bytes()
                if path == player_patches.SETTINGS_SCRIPT:
                    patches = [("dev proxy: debug overlay always on", "const DEV_PROXY = false;",
                                "const DEV_PROXY = true;", 1)]
                    native = box_native_hevc()
                    print(f"-> {path}: box native HEVC {'yes' if native else 'no'}", file=sys.stderr)
                    if native:
                        patches.append(("native codec selection", "const NATIVE_HEVC = false;",
                                        "const NATIVE_HEVC = true;", 1))
                    data = player_patches.apply(data.decode(), patches).encode()
                return self.send_body(200, [("Content-Type", "application/javascript")],
                                      data)

            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            headers = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "accept-encoding")}
            headers["Host"] = DEV_HOST
            conn = http.client.HTTPSConnection(args.box, 443, context=box_ctx, timeout=15)
            try:
                conn.request(self.command, self.path, body=body, headers=headers)
                resp = conn.getresponse()
                data = resp.read()
            except OSError as e:
                return self.send_body(502, [("Content-Type", "text/plain")], f"box unreachable: {e}".encode())
            finally:
                conn.close()

            if path in stock:
                data = stock[path]
            if path == "/player.html":
                patches = player_patches.PLAYER_HTML_PATCHES
            elif re.fullmatch(r"/assets/index\.[0-9a-f]+\.js", path):
                patches = DEV_BUNDLE_PATCHES + (player_patches.HEVC_BUNDLE_PATCHES if box_native_hevc() else [])
            else:
                patches = None
            if patches:
                print(f"-> patching {path}", file=sys.stderr)
                data = player_patches.apply(data.decode("utf-8"), patches, strict=False).encode("utf-8")
            self.send_body(resp.status, resp.getheaders(), data)

        do_GET = do_POST = do_HEAD = proxy

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--box", default="3.3.3.3")
    ap.add_argument("--port", type=int, default=8443)
    ap.add_argument("--firmware", help='official update zip (or "cdn") to patch the stock player from')
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    crt, key = ensure_cert()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args))
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(crt, key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)

    print(f"serving https://{DEV_HOST}:{args.port}/ -> box {args.box}", file=sys.stderr)
    print("open with:\n"
          '  open -na "Google Chrome" --args --user-data-dir=/tmp/zbox-dev --no-proxy-server '
          "--ignore-certificate-errors "
          f'--host-resolver-rules="MAP {DEV_HOST} 127.0.0.1, MAP {BOX_HOST} {args.box}" '
          f"https://{DEV_HOST}:{args.port}/", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
