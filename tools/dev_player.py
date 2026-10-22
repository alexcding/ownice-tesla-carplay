#!/usr/bin/env python3
"""Run the patched CarPlay player against a live box, without flashing it.

A local HTTPS server reverse-proxies the box's web UI, applies
tools/player_patches.py on the fly and serves player/worker.js and
player/zq-settings.js. The page's WebSockets still go straight to the box.
Resolution, frame rate and codec are chosen in the page (the "HD" button).

    python3 -I tools/dev_player.py

Then open the printed Chrome command. Ctrl-C to stop.
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


def make_handler(args):
    box_ctx = ssl._create_unverified_context()

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
                return self.send_body(200, [("Content-Type", "application/javascript")],
                                      LOCAL_FILES[path].read_bytes())

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

            if path == "/player.html":
                patches = player_patches.PLAYER_HTML_PATCHES
            elif re.fullmatch(r"/assets/index\.[0-9a-f]+\.js", path):
                patches = DEV_BUNDLE_PATCHES
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
