#!/usr/bin/env python3
"""Build an owner-authorized runtime identity probe; never restart a service."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

VERSION = 'crypto-runtime-preflight.3'
REPORT = '/usr/z-loader/zjinnova/web/zq-crypto-runtime3.txt'
RECEIVER_MD5 = 'a099e55592e70cabab0883e892410c14'
LAUNCHER_MD5 = '3cd76394f515114db3fc4acb0125e678'


def script():
    # Read only /proc executable/library identities. Never dump environments,
    # configuration, arbitrary mapped files, keys or command-line arguments.
    return r'''#!/bin/sh
REPORT='@REPORT@'
PROC=/proc
BIN=/usr/z-loader/bin
[ ! -e "$REPORT" ] || exit 1
umask 077
exec > "$REPORT" 2>&1 || exit 1
echo 'package=@VERSION@'
echo 'service_changes=0'
echo 'autostart=0'
failed=0
reject() { echo "blocked=$1"; failed=1; }
sum() { md5sum "$1" 2>/dev/null | cut -d' ' -f1; }
identity() {
 # Strip through the final ')' because Linux process names may contain spaces.
 line=$(cat "$PROC/$1/stat" 2>/dev/null) || return 1
 rest=${line##*) }
 set -- $rest
 [ "$#" -ge 20 ] || return 1
 # Fields 3 (state), 4 (parent) and 22 (start tick).
 state=$1; parent=$2
 shift 19
 tick=$1
 case "$parent:$tick" in *[!0-9:]*|:*) return 1;; esac
 printf '%s:%s:%s\n' "$parent" "$tick" "$state"
}
signals() {
 signal_pid=$1
 prefix=$2
 fields=0
 while read -r key value extra; do
  case "$key" in SigIgn:|SigBlk:|SigCgt:)
   case "$value" in ''|*[!0-9a-fA-F]*) return 1;; esac
   echo "$prefix.${key%:}=$value"
   fields=$((fields+1));;
  esac
 done < "$PROC/$signal_pid/status"
 [ "$fields" = 3 ]
}
for command in md5sum readlink cat cut tr grep; do
 command -v "$command" >/dev/null 2>&1 || { reject "missing-$command"; echo complete=1; exit 1; }
done
[ "$(sum "$BIN/z-tesla")" = '@RECEIVER_MD5@' ] || reject installed-receiver-identity
[ "$(sum "$BIN/z-tesla-sh.sh")" = '@LAUNCHER_MD5@' ] || reject installed-launcher-identity
for name in z-tesla z-link; do
 count=0
 pid=
 for entry in "$PROC"/[0-9]*; do
  executable=$(readlink "$entry/exe" 2>/dev/null) || continue
  case "$executable" in "$BIN/$name"|"$BIN/$name (deleted)")
   count=$((count + 1)); pid=${entry##*/};;
  esac
 done
 echo "$name.process_count=$count"
 if [ "$count" != 1 ]; then reject "$name-process-count"; continue; fi
 before=$(identity "$pid") || { reject "$name-process-identity"; continue; }
 case "$before" in *:T|*:t|*:Z|*:X) reject "$name-process-state";; esac
 disk=$(sum "$BIN/$name")
 running=$(sum "$PROC/$pid/exe")
 echo "$name.pid=$pid"
 echo "$name.identity=$before"
 echo "$name.disk_md5=$disk"
 echo "$name.running_md5=$running"
 signals "$pid" "$name" || reject "$name-signal-status-unavailable"
 if [ -z "$disk" ] || [ "$disk" != "$running" ]; then reject "$name-generation-mismatch"; fi
 if [ "$name" = z-tesla ]; then
  launcher=${before%%:*}
  launcher_before=$(identity "$launcher") || reject launcher-process-identity
  # Inspect argument tokens privately; print no arguments to the report.
  if ! tr '\000' '\n' < "$PROC/$launcher/cmdline" | grep -Fx "$BIN/z-tesla-sh.sh" >/dev/null; then
   reject receiver-parent-is-not-launcher
  fi
  case "$launcher_before" in *:T|*:t|*:Z|*:X) reject launcher-process-state;; esac
  echo "launcher.pid=$launcher"
  echo "launcher.identity=$launcher_before"
  signals "$launcher" launcher || reject launcher-signal-status-unavailable
 fi
 checked=0
 anonymous=0
 seen='|'
 # Avoid a pipeline subshell so failed checks propagate to the final status.
 while read -r range permissions offset device inode path extra; do
  case "$permissions" in *x*) ;; *) continue;; esac
  # Anonymous mappings do not identify an on-disk native generation. Count them
  # without claiming to verify their contents; this is not a code-integrity scan.
  [ -n "$path" ] || { anonymous=$((anonymous + 1)); continue; }
  case "$path" in '[vectors]'|'[vdso]'|'[sigpage]'|'[vvar]'|'[heap]'|'[stack]')
   echo "$name.special_map=$path"; continue;; esac
  # Reject deleted maps and unfamiliar executable file locations.
  [ -z "$extra" ] || { reject "$name-deleted-or-ambiguous-map"; continue; }
  case "$path" in "$BIN/$name"|/lib/*.so*|/usr/lib/*.so*|/usr/z-loader/lib/*.so*) ;;
   *) reject "$name-unexpected-executable-map"; continue;;
  esac
  case "$seen" in *"|$path|"*) continue;; esac
  seen="$seen$path|"
  mapped=$(sum "$PROC/$pid/map_files/$range")
  current=$(sum "$path")
  # This kernel exposes main-executable identity via /proc/PID/exe, but its
  # corresponding map_files link may be unavailable. Do not apply this fallback
  # to any library: each library must still be read through its mapped inode.
  if [ "$path" = "$BIN/$name" ] && [ -z "$mapped" ]; then
   mapped=$(sum "$PROC/$pid/exe")
   echo "$name.main_map_source=proc-exe"
  fi
  echo "$name.map=$path mapped_md5=$mapped disk_md5=$current"
  if [ -z "$mapped" ] || [ "$mapped" != "$current" ]; then
   reject "$name-mapped-file-unverified"
  fi
  checked=$((checked + 1))
 done < "$PROC/$pid/maps"
 echo "$name.anonymous_executable_maps=$anonymous"
 [ "$checked" -gt 0 ] || reject "$name-no-verified-maps"
 after=$(identity "$pid") || reject "$name-process-disappeared"
 # State can change during inspection, but parent and start tick must match.
 [ "${before%:*}" = "${after%:*}" ] || reject "$name-process-changed"
 if [ "$name" = z-tesla ]; then
  launcher_after=$(identity "$launcher") || reject launcher-disappeared
  [ "${launcher_before%:*}" = "${launcher_after%:*}" ] || reject launcher-changed
 fi
done
echo "overall_exit=$failed"
echo complete=1
exit "$failed"
'''.replace('@REPORT@', REPORT).replace('@VERSION@', VERSION).replace(
        '@RECEIVER_MD5@', RECEIVER_MD5).replace('@LAUNCHER_MD5@', LAUNCHER_MD5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    installer = script()
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'update.sh').write_text(installer)
    archive = args.output / (VERSION + '.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as package:
        package.writestr('update.sh', installer)
    manifest = {'package_version': VERSION,
                'zip_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                'new_files': [REPORT], 'service_restarts': False,
                'installed_binary_replaced': False, 'autostart': False,
                'rollback': 'No activation; only an identity report is added.'}
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
