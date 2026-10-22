#!/bin/sh
# Installer for packages that replace native (receiver) files. The box unzips the
# package to /misc/update-file and runs this script from there as root.
#
# Before the first native install it saves the current files, verified against the
# stock 2.9.11 MD5s, to /misc/zq-native/stock/. restore.sh (installed before any
# native file changes, and run automatically by z-link.sh if the receiver keeps
# crashing) puts them back. Any failure after that point restores them at once.
#
# ZQ_ROOT / ZQ_MISC exist only for local testing against a scratch tree.

ROOT="${ZQ_ROOT:-}"
ZQ="${ZQ_MISC:-/misc}/zq-native"
META=misc/zq-native # in the package; copied to /misc/zq-native
LIST="$META/native.list" # "<stock md5> <path>" per native file, relative to /
STATUS="$ROOT/usr/z-loader/zjinnova/web/zq-native.txt"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') install: $*" >> "$STATUS"
}

md5() {
  if command -v md5sum >/dev/null 2>&1; then md5sum "$1" | cut -d' ' -f1; else echo none; fi
}

fail_restore() {
  log "$1; restoring stock native files"
  sh "$ZQ/restore.sh"
  exit 1
}

[ -f "$LIST" ] && [ -f "$META/restore.sh" ] || { log "package lacks $META files, aborted; nothing changed"; exit 1; }

# 1. Back up the stock files once. A file that is not the stock build means an
#    earlier native install is still active without a backup: refuse.
if [ ! -f "$ZQ/stock/COMPLETE" ]; then
  rm -rf "$ZQ/stock"
  command -v md5sum >/dev/null 2>&1 || log "md5sum not found; backing up without stock check"
  while read -r sum path; do
    [ -n "$path" ] || continue
    got="$(md5 "$ROOT/$path")"
    if [ "$got" != none ] && [ "$got" != "$sum" ]; then
      log "$path is not stock (md5 $got, expected $sum), aborted; nothing changed"
      rm -rf "$ZQ/stock"
      exit 1
    fi
    mkdir -p "$ZQ/stock/$(dirname "$path")"
    cp -p "$ROOT/$path" "$ZQ/stock/$path" || { log "backup of $path failed, aborted; nothing changed"; rm -rf "$ZQ/stock"; exit 1; }
  done < "$LIST"
  cp "$LIST" "$ZQ/stock/native.list" || { log "backup failed, aborted; nothing changed"; rm -rf "$ZQ/stock"; exit 1; }
  sync
  touch "$ZQ/stock/COMPLETE"
  sync
  log "saved stock native files to $ZQ/stock"
fi

# 2. The way back goes in before anything changes.
cp -p "$META/restore.sh" "$ZQ/restore.sh" || { log "could not install restore.sh, aborted; nothing changed"; exit 1; }
sync

# 3. Swap native files in with copy + rename, so a running process keeps its
#    old mapping instead of reading a half-written library. Remove them from the
#    package afterwards so the copy below does not overwrite them in place.
touch "$ZQ/active"
while read -r sum path; do
  [ -n "$path" ] || continue
  [ -f "./$path" ] || continue
  cp -p "./$path" "$ROOT/$path.zq-new" && mv -f "$ROOT/$path.zq-new" "$ROOT/$path" ||
    fail_restore "install of $path failed"
  rm -f "./$path"
done < "$LIST"

# 4. Everything else (web files, version, metadata), as the vendor installer does.
cp -fr ./* "$ROOT/" || fail_restore "copying package files failed"
sync
log "installed $(cat "$ROOT/usr/z-loader/webversion" 2>/dev/null)"

# 5. Restart the receiver so it loads the new files (z-link.sh relaunches it).
[ -z "$ROOT" ] && killall z-link 2>/dev/null
exit 0
