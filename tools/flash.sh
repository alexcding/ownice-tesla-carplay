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

wait_for_box() {
  echo "waiting for the box to reboot..."
  CURL --retry 40 --retry-delay 3 --retry-all-errors --retry-connrefused -m 5 \
    "http://$BOX/cgi-bin/checkver.cgi?t=$(date +%s)"
  echo
}

upload() {
  zip="$1"
  [ -f "$zip" ] || { echo "no such file: $zip" >&2; exit 1; }
  md5="$(md5of "$zip")"
  echo "box before:  $(version)"
  echo "uploading:   $zip ($(wc -c < "$zip" | tr -d ' ') bytes, md5 $md5)"
  resp="$(CURL -m 120 -F "file=@$zip;filename=upload.zip" -F "md5=$md5" \
    "http://$BOX/cgi-bin/upload.cgi")"
  echo "response:    $resp"
  case "$resp" in
    *'"code": 0'*|*'"code":0'*) ;;
    *) echo "upload rejected — box NOT updated." >&2; exit 1 ;;
  esac
  echo "LED blinks while it installs — do not pull power."
  wait_for_box
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
