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
command -v md5sum >/dev/null 2>&1 || { log "md5sum missing, aborted; nothing changed"; exit 1; }

# 1. Back up the stock files once. A file that is not the stock build means an
#    earlier native install is still active without a backup: refuse.
if [ ! -f "$ZQ/stock/COMPLETE" ]; then
  rm -rf "$ZQ/stock"
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

# A prior --native-test backup contains fewer files than an HEVC package.
# Verify old backups and add each newly touched stock file BEFORE swapping any
# native file. Retain the union so rollback covers every installed generation.
PENDING="$ZQ/stock/native.list.zq-new"
cp "$ZQ/stock/native.list" "$PENDING" || { log "could not stage backup list, aborted"; exit 1; }
while read -r sum path; do
  [ -n "$path" ] || continue
  if [ -f "$ZQ/stock/$path" ]; then
    got="$(md5 "$ZQ/stock/$path")"
    [ "$got" = "$sum" ] || { log "backup of $path is not stock, aborted; nothing changed"; rm -f "$PENDING"; exit 1; }
  else
    got="$(md5 "$ROOT/$path")"
    [ "$got" = "$sum" ] || { log "$path is not stock, aborted; nothing changed"; rm -f "$PENDING"; exit 1; }
    mkdir -p "$ZQ/stock/$(dirname "$path")" || exit 1
    cp -p "$ROOT/$path" "$ZQ/stock/$path" || { log "backup of $path failed, aborted"; rm -f "$PENDING"; exit 1; }
  fi
  # Paths are repository-defined and contain no whitespace; compare the path
  # field exactly, not a substring or regular expression.
  found=0
  while read -r saved_sum saved_path; do
    [ "$saved_path" = "$path" ] && found=1
  done < "$PENDING"
  [ "$found" = 1 ] || echo "$sum $path" >> "$PENDING" || exit 1
done < "$LIST"
mv -f "$PENDING" "$ZQ/stock/native.list" || { log "could not finalize backup list, aborted"; exit 1; }
sync

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

# 5. Activation requires an owner-triggered reboot: z-tesla and z-link run
# under separate supervisors, and the existing shell does not reload z-link.sh.
# Do not activate just one part of this native generation with a partial restart.
log "REBOOT REQUIRED: reboot the adapter before using this native package"
exit 0
