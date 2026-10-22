#!/bin/sh
# Puts back the stock native files saved by update.sh. Installed as
# /misc/zq-native/restore.sh; run by z-link.sh when the receiver keeps crashing,
# or by the restore package (build_patch.py --restore-native).
#
# ZQ_ROOT / ZQ_MISC exist only for local testing against a scratch tree.

ROOT="${ZQ_ROOT:-}"
ZQ="${ZQ_MISC:-/misc}/zq-native"
STATUS="$ROOT/usr/z-loader/zjinnova/web/zq-native.txt"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') restore: $*" >> "$STATUS"
}

if [ ! -f "$ZQ/stock/COMPLETE" ]; then
  log "no complete backup in $ZQ/stock; use tools/flash.sh rollback"
  exit 1
fi

# Check the entire backup before touching any live file. A COMPLETE marker alone
# does not establish that the manifest or the saved files survived intact.
abort() {
  log "$*; restore aborted, native files unchanged; use tools/flash.sh rollback"
  exit 1
}
LIST="$ZQ/stock/native.list"
[ -f "$LIST" ] && [ -r "$LIST" ] && [ -s "$LIST" ] || abort "missing or empty backup manifest"
command -v md5sum >/dev/null 2>&1 || abort "md5sum missing"
count=0
paths=" "
records=" "
while read -r sum path extra || [ -n "$sum$path$extra" ]; do
  [ -n "$sum$path$extra" ] || continue
  [ -z "$extra" ] && [ "${#sum}" -eq 32 ] || abort "invalid backup manifest entry"
  case "$sum" in *[!0123456789abcdef]*) abort "invalid backup checksum" ;; esac
  case "$path" in
    usr/z-loader/*) ;;
    *) abort "invalid backup path" ;;
  esac
  case "/$path/" in */../*|*/./*|*//*) abort "invalid backup path" ;; esac
  case "$paths" in *" $path "*) abort "duplicate backup manifest path" ;; esac
  [ -f "$ZQ/stock/$path" ] || abort "missing backup of $path"
  got="$(md5sum "$ZQ/stock/$path")" || abort "could not checksum backup of $path"
  got="${got%% *}"
  [ "$got" = "$sum" ] || abort "backup checksum mismatch for $path"
  paths="$paths$path "
  records="$records$sum:$path "
  count=$((count + 1))
done < "$LIST"
[ "$count" -gt 0 ] || abort "empty backup manifest"

# The saved list is a union across native generations. Every current installed
# entry must be covered, but a smaller later generation must not discard older
# backups. During a first install failure, metadata may still be in staging.
INSTALLED="$ZQ/native.list"
if [ ! -e "$INSTALLED" ]; then INSTALLED="misc/zq-native/native.list"; fi
[ -f "$INSTALLED" ] && [ -r "$INSTALLED" ] && [ -s "$INSTALLED" ] ||
  abort "missing or empty installed native manifest"
installed_count=0
while read -r sum path extra || [ -n "$sum$path$extra" ]; do
  [ -n "$sum$path$extra" ] || continue
  [ -z "$extra" ] || abort "invalid installed native manifest entry"
  case "$records" in
    *" $sum:$path "*) ;;
    *) abort "backup manifest does not cover installed native entry $path" ;;
  esac
  installed_count=$((installed_count + 1))
done < "$INSTALLED"
[ "$installed_count" -gt 0 ] || abort "empty installed native manifest"

# Inventory catches omissions even if the installed manifest is also truncated
# or represents a smaller generation. No external find/grep utility is needed.
check_inventory() {
  for saved_file in "$1"/* "$1"/.[!.]* "$1"/..?*; do
    [ -e "$saved_file" ] || [ -L "$saved_file" ] || continue
    [ ! -L "$saved_file" ] || abort "unexpected symlink in native backup"
    if [ -d "$saved_file" ]; then
      check_inventory "$saved_file"
    else
      inventory_path="${saved_file#"$ZQ/stock/"}"
      case "$paths" in
        *" $inventory_path "*) ;;
        *) abort "backup manifest omits saved native file $inventory_path" ;;
      esac
    fi
  done
}
check_inventory "$ZQ/stock/usr/z-loader"

failed=0
while read -r sum path || [ -n "$sum$path" ]; do
  [ -n "$path" ] || continue
  # already stock (e.g. an install that failed before reaching this file)
  if command -v cmp >/dev/null 2>&1 && cmp -s "$ZQ/stock/$path" "$ROOT/$path"; then
    continue
  fi
  # copy + rename: the running receiver and this script keep their old files open
  if cp -p "$ZQ/stock/$path" "$ROOT/$path.zq-old" && mv -f "$ROOT/$path.zq-old" "$ROOT/$path"; then
    :
  else
    failed=1
    log "could not restore $path"
  fi
done < "$LIST"
sync

if [ "$failed" = 0 ]; then
  rm -f "$ROOT/data/local/tmp/zq_hevc" "$ROOT/usr/z-loader/zjinnova/web/zq-codec.txt"
  for checkpoint in request phone sender output; do
    rm -f "$ROOT/usr/z-loader/zjinnova/web/zq-video-$checkpoint.txt"
  done
  rm -f "$ZQ/active"
  sync
  log "stock native files restored"
else
  log "restore incomplete; use tools/flash.sh rollback"
fi

log "REBOOT REQUIRED: reboot the adapter to activate all restored native files"
exit "$failed"
