#!/bin/sh
# Flash or roll back an Ownice T3 over its web UI (join the box's Wi-Fi first).
#
#   tools/flash.sh status                 # show the box's current version
#   tools/flash.sh update [zip]           # upload build/update_tesla.zip (or <zip>)
#   tools/flash.sh rollback [zip]         # restore stock 2.9.11 (downloads if needed)
#
# The box installs on a `{"code": 0}` response and reboots itself. The script
# then waits for it to come back and prints the new version.
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

# Wait until the box reboots and reports the expected version (or time out).
wait_for_box() {
  want="$1"
  echo "waiting for the box to reboot and report $want ..."
  i=0
  while [ "$i" -lt 60 ]; do
    i=$((i + 1))
    cur="$(CURL -m 4 "http://$BOX/cgi-bin/checkver.cgi?t=$(date +%s)" 2>/dev/null || true)"
    case "$cur" in
      *"\"$want\""*) echo "box now: $cur"; return 0 ;;
    esac
    sleep 2
  done
  echo "timed out waiting for version $want; last: ${cur:-<no response>}" >&2
  echo "re-check with: $0 status" >&2
  return 1
}

# The version string inside a package (usr/z-loader/webversion).
zip_version() { unzip -p "$1" usr/z-loader/webversion 2>/dev/null | tr -d '[:space:]'; }

upload() {
  zip="$1"
  [ -f "$zip" ] || { echo "no such file: $zip" >&2; exit 1; }
  md5="$(md5of "$zip")"
  want="$(zip_version "$zip")"
  echo "box before:  $(version)"
  echo "uploading:   $zip ($(wc -c < "$zip" | tr -d ' ') bytes, md5 $md5, version ${want:-?})"
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
  [ -n "$want" ] && wait_for_box "$want" || version
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
  status)   echo "box: $(version)" ;;
  update)   upload "${2:-$BUILD_ZIP}" ;;
  rollback) [ $# -ge 2 ] && upload "$2" || { ensure_stock; upload "$STOCK_ZIP"; } ;;
  *) echo "usage: $0 {status|update [zip]|rollback [zip]}" >&2; exit 1 ;;
esac
