#!/bin/sh
# Flash or roll back an Ownice T3 over its web UI (join the box's Wi-Fi first).
#
#   tools/flash.sh status                 # show the box's current version
#   tools/flash.sh update [zip]           # upload build/update_tesla.zip (or <zip>)
#   tools/flash.sh rollback [zip]         # restore stock 2.9.11 (downloads if needed)
#
# The box installs on a `{"code": 0}` response. The script then waits until it
# reports the new version (webversion is read live) and, for our packages, the
# package's own build identity (zq-build.txt: version + content hash), since
# every variant of one release shares the plain 2.9.11-ocN version. Web files apply at once;
# boot-script changes (etc/*) only run after a power cycle: in 2026-10-22 tests
# the box did not reboot after an update.
#
# Env: BOX=3.3.3.3 (box IP). Requires curl, md5/md5sum, unzip.
set -eu

BOX="${BOX:-3.3.3.3}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_ZIP="$ROOT/build/update_tesla.zip"

# Stock firmware (vendor CDN), used for rollback. Matches tools/build_patch.py.
STOCK_URL="https://cdn.zjinnova.com/tesla/en/update_tesla0817.zip"
STOCK_MD5="3a743be56b60df524825cffcf45238cd"
STOCK_ZIP="$ROOT/build/stock_2.9.11.zip"

CURL() { curl -s --noproxy '*' "$@"; }

md5of() {
  if command -v md5 >/dev/null 2>&1; then md5 -q "$1"; else md5sum "$1" | cut -d' ' -f1; fi
}

version() {
  CURL -m 5 "http://$BOX/cgi-bin/checkver.cgi?t=$(date +%s)" 2>/dev/null || echo '(no response)'
}

# Installed build identity (first line of zq-build.txt); empty before our builds.
# The web root is served over HTTPS for the certificate's name, so pin it to $BOX.
# Shown only when its version matches checkver: stock installers never delete
# files, so a rollback leaves our zq-build.txt behind.
box_build() {
  b="$(CURL -k -m 4 --resolve "zbox.link:443:$BOX" "https://zbox.link/zq-build.txt?t=$(date +%s)" 2>/dev/null |
    head -1 | tr -d '[:space:]' | grep -E '^[0-9][^<]*\+[0-9a-f]{12}$' || true)"
  case "$(version)" in
    *"\"${b%%+*}\""*) [ -n "$b" ] && echo "$b" ;;
    *) [ -n "$b" ] && echo "(stale: $b)" ;;
  esac
  return 0
}

# Wait until the box reports the expected version and, when the package has
# one, its build identity (or time out).
wait_for_box() {
  want="$1"
  want_build="${2:-}"
  echo "waiting for the box to install and report ${want_build:-$want} ..."
  i=0
  while [ "$i" -lt 60 ]; do
    i=$((i + 1))
    cur="$(CURL -m 4 "http://$BOX/cgi-bin/checkver.cgi?t=$(date +%s)" 2>/dev/null || true)"
    case "$cur" in
      *"\"$want\""*)
        if [ -z "$want_build" ] || [ "$(box_build)" = "$want_build" ]; then
          echo "box now: $cur${want_build:+ build $want_build}"; return 0
        fi ;;
    esac
    sleep 2
  done
  echo "timed out waiting for ${want_build:-version $want}; last: ${cur:-<no response>} build $(box_build)" >&2
  echo "re-check with: $0 status" >&2
  return 1
}

# The version string inside a package (usr/z-loader/webversion).
zip_version() { unzip -p "$1" usr/z-loader/webversion 2>/dev/null | tr -d '[:space:]'; }
# Its build identity (usr/z-loader/zq-build); absent in stock and older packages.
zip_build() { unzip -p "$1" usr/z-loader/zq-build 2>/dev/null | tr -d '[:space:]'; }

upload() {
  zip="$1"
  [ -f "$zip" ] || { echo "no such file: $zip" >&2; exit 1; }
  md5="$(md5of "$zip")"
  want="$(zip_version "$zip")"
  want_build="$(zip_build "$zip")"
  echo "box before:  $(version) build $(box_build)"
  echo "uploading:   $zip ($(wc -c < "$zip" | tr -d ' ') bytes, md5 $md5, version ${want:-?}${want_build:+ build $want_build})"
  # The box may drop the connection as it starts installing, so a curl error
  # here is not necessarily a failure; confirm by polling the version below.
  resp="$(CURL -S -m 120 -F "file=@$zip;filename=upload.zip" -F "md5=$md5" \
    "http://$BOX/cgi-bin/upload.cgi" 2>&1 || true)"
  echo "response:    ${resp:-<none>}"
  case "$resp" in
    *'"code": 0'*|*'"code":0'*) ;;
    *) echo "no success response — will still check whether it applied..." >&2 ;;
  esac
  echo "LED blinks while it installs — do not pull power."
  [ -n "$want" ] && wait_for_box "$want" "$want_build" || version
}

ensure_stock() {
  if [ -f "$STOCK_ZIP" ] && [ "$(md5of "$STOCK_ZIP")" = "$STOCK_MD5" ]; then return; fi
  echo "downloading stock firmware..."
  mkdir -p "$(dirname "$STOCK_ZIP")"
  curl -s -L -o "$STOCK_ZIP" "$STOCK_URL"
  got="$(md5of "$STOCK_ZIP")"
  [ "$got" = "$STOCK_MD5" ] || { echo "stock md5 mismatch: $got != $STOCK_MD5" >&2; exit 1; }
}

case "${1:-status}" in
  status)   echo "box: $(version) build $(box_build)" ;;
  update)   upload "${2:-$BUILD_ZIP}" ;;
  rollback) [ $# -ge 2 ] && upload "$2" || { ensure_stock; upload "$STOCK_ZIP"; } ;;
  *) echo "usage: $0 {status|update [zip]|rollback [zip]}" >&2; exit 1 ;;
esac
