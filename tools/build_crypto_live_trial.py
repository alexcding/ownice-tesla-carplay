#!/usr/bin/env python3
"""Build a bounded live trial, requiring matching local and device validation.

Never uploads, reboots, replaces installed receiver files or adds boot hooks.
The package briefly restarts the receiver using a sidecar and restores its
original launcher. An owner-authorized upload is a separate operation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).parent))
import build_crypto_preflight as runtime

VERSION = 'crypto-neon-live.4'
STAGE = '/misc/zq-crypto-live4'
REPORT = '/usr/z-loader/zjinnova/web/zq-crypto-live4.txt'
RUNTIME = '/usr/z-loader/zjinnova/web/zq-crypto-live4-runtime.txt'
CANDIDATE_SHA = '43e9b44dba7e87940fcdb1b4bd436973e247760c710799a77648f1f7b0d83c52'
GUARD_SHA = 'f9a90b56cbb129b0e73d00a33ec11696914289a80ee718b5522adaf11fcc7d30'
LOADER_GUARD_SHA = 'e760a68c154ef69b11f15b5ec6c12f98e332fd752aa03dad34680bbf53b5773e'


def require_device_rehearsal(text):
    required = ['package=crypto-guard-check.4', 'runtime_exit=0', 'kernel_check_exit=0',
                'protected_files_unchanged=1', 'overall_exit=0', 'complete=1']
    for mode, code in [('trial', 0), ('crash', 90), ('hang', 137)]:
        required += [f'fixture_{mode}.controller_exit={code}',
                     f'fixture_{mode}.restored=1', f'fixture_{mode}.remaining=0']
    lines = text.splitlines()
    if lines.count('child_sigpipe_probe_survived=1') != 3:
        raise ValueError('missing real-kernel child SIGPIPE rehearsals')
    for value in required:
        key = value.split('=', 1)[0] + '='
        if [line for line in lines if line.startswith(key)] != [value]:
            raise ValueError('missing, failed or ambiguous device check: ' + key)


def installer(candidate_md5, guard_md5, runtime_md5, loader_md5):
    return r'''#!/bin/sh
STAGE='@STAGE@'
REPORT='@REPORT@'
RUNTIME='@RUNTIME@'
[ ! -e "$REPORT" ] && [ ! -e "$RUNTIME" ] && [ ! -e "$STAGE/z-tesla.neon" ] && [ ! -e "$STAGE/loader-guard" ] || exit 1
ENV_BIN=$(command -v env) || exit 1
sum() { md5sum "$1" 2>/dev/null | cut -d' ' -f1; }
protected() {
 for path in /usr/z-loader/bin/z-tesla /usr/z-loader/bin/z-tesla-sh.sh /usr/z-loader/webversion /usr/z-loader/update.sh /etc/wlan-softap.sh /etc/check-hostap.sh; do
  md5sum "$path" || return 1
 done
}
before=$(protected) || exit 1
[ "$(sum "$STAGE/trial-guard")" = '@GUARD_MD5@' ] || exit 1
[ "$(sum payload/z-tesla.neon)" = '@CANDIDATE_MD5@' ] || exit 1
[ "$(sum payload/runtime-check.sh)" = '@RUNTIME_MD5@' ] || exit 1
[ "$(sum payload/loader-guard)" = '@LOADER_MD5@' ] || exit 1
exec > "$REPORT" 2>&1 || exit 1
echo 'package=@VERSION@'
echo trial_seconds=300
echo installed_binary_replaced=0
echo autostart=0
printf 'protected_before:\n%s\n' "$before"
cp payload/z-tesla.neon "$STAGE/z-tesla.neon" && chmod 755 "$STAGE/z-tesla.neon" || exit 1
[ "$(sum "$STAGE/z-tesla.neon")" = '@CANDIDATE_MD5@' ] || exit 1
cp payload/loader-guard "$STAGE/loader-guard" && chmod 755 "$STAGE/loader-guard" || exit 1
[ "$(sum "$STAGE/loader-guard")" = '@LOADER_MD5@' ] || exit 1
"$STAGE/loader-guard" --guard 5 "$ENV_BIN" LD_LIBRARY_PATH=/usr/z-loader/lib LD_PRELOAD= LD_TRACE_LOADED_OBJECTS=1 "$STAGE/z-tesla.neon"
loader_exit=$?
echo "loader_trace_exit=$loader_exit"
sh payload/runtime-check.sh
runtime_exit=$?
echo "runtime_exit=$runtime_exit"
value() { sed -n "s/^$1=//p" "$RUNTIME"; }
trial_exit=1
restored=0
if [ "$loader_exit" = 0 ] && [ "$runtime_exit" = 0 ] && [ "$(value overall_exit)" = 0 ]; then
 lp=$(value launcher.pid); li=$(value launcher.identity); li=${li#*:}; lt=${li%:*}
 rp=$(value z-tesla.pid); ri=$(value z-tesla.identity); ri=${ri#*:}; rt=${ri%:*}
 "$STAGE/trial-guard" --trial "$lp" "$lt" "$rp" "$rt" 300 "$STAGE/z-tesla.neon"
 trial_exit=$?
 echo "supervisor_exit=$trial_exit"
 # Resuming the launcher is a request. Separately verify its original binary
 # reappears with the expected parent, without starting another launcher.
 i=0
 while [ "$i" -lt 10 ]; do
  count=0
  for entry in /proc/[0-9]*; do
   [ "$(readlink "$entry/exe" 2>/dev/null)" = /usr/z-loader/bin/z-tesla ] || continue
   [ "$(sum "$entry/exe")" = '@RECEIVER_MD5@' ] || continue
   line=$(cat "$entry/stat" 2>/dev/null) || continue
   rest=${line##*) }; set -- $rest
   [ "$#" -ge 2 ] || continue
   case "$1" in T|t|Z|X) continue;; esac
   [ "$2" = "$lp" ] || continue
   count=$((count + 1))
  done
  if [ "$count" = 1 ]; then restored=1; break; fi
  sleep 1; i=$((i+1))
 done
fi
echo "original_receiver_restored=$restored"
after=$(protected)
snapshot_exit=$?
printf 'protected_after:\n%s\n' "$after"
preserved=0
if [ "$snapshot_exit" = 0 ] && [ "$before" = "$after" ]; then preserved=1; fi
echo "protected_files_unchanged=$preserved"
overall_exit=1
if [ "$loader_exit" = 0 ] && [ "$trial_exit" = 0 ] && [ "$restored" = 1 ] && [ "$preserved" = 1 ]; then overall_exit=0; fi
echo "overall_exit=$overall_exit"
echo complete=1
sync
exit "$overall_exit"
'''.replace('@STAGE@', STAGE).replace('@REPORT@', REPORT).replace(
        '@RUNTIME@', RUNTIME).replace('@VERSION@', VERSION).replace(
        '@CANDIDATE_MD5@', candidate_md5).replace('@GUARD_MD5@', guard_md5).replace(
        '@RUNTIME_MD5@', runtime_md5).replace('@LOADER_MD5@', loader_md5).replace(
        '@RECEIVER_MD5@', runtime.RECEIVER_MD5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--guard', type=Path, required=True)
    parser.add_argument('--device-check', type=Path, required=True)
    parser.add_argument('--loader-guard', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require_device_rehearsal(args.device_check.read_text())
    candidate = args.candidate / 'z-tesla.neon'
    data = candidate.read_bytes()
    guard = args.guard.read_bytes()
    loader = args.loader_guard.read_bytes()
    if hashlib.sha256(data).hexdigest() != CANDIDATE_SHA:
        raise ValueError('unexpected HEVC candidate')
    if hashlib.sha256(guard).hexdigest() != GUARD_SHA:
        raise ValueError('guard differs from the device-rehearsed binary')
    if hashlib.sha256(loader).hexdigest() != LOADER_GUARD_SHA:
        raise ValueError('loader guard differs from the device-tested binary')
    tests = json.loads((args.candidate / 'test-results.json').read_text())
    if tests.get('passed') is not True:
        raise ValueError('candidate validation did not pass')
    # Recheck the exact artifact identity in the saved result, not just a pass flag.
    if tests.get('candidate_sha256') != CANDIDATE_SHA or tests.get('status') != 'passed':
        raise ValueError('candidate validation identity missing')
    probe = runtime.script().replace(runtime.REPORT, RUNTIME).encode()
    update = installer(hashlib.md5(data).hexdigest(), hashlib.md5(guard).hexdigest(),
                       hashlib.md5(probe).hexdigest(), hashlib.md5(loader).hexdigest())
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'update.sh').write_text(update)
    subprocess.run(['sh', '-n', str(args.output / 'update.sh')], check=True)
    archive = args.output / (VERSION + '.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as package:
        package.writestr('update.sh', update)
        package.writestr('payload/z-tesla.neon', data)
        package.writestr('payload/runtime-check.sh', probe)
        package.writestr('payload/loader-guard', loader)
    manifest = {'package_version': VERSION,
                'zip_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                'candidate_sha256': CANDIDATE_SHA, 'guard_sha256': GUARD_SHA,
                'new_files': [STAGE + '/z-tesla.neon', STAGE + '/loader-guard', REPORT, RUNTIME],
                'installed_binary_replaced': False, 'autostart': False,
                'trial_seconds': 300, 'upload_timeout_seconds': 420,
                'service_restart': 'z-tesla receiver only',
                'rollback': 'Guard stops the trial copy and resumes the original launcher; '
                            'the unchanged boot launcher starts the installed original after a power cycle.',
                'not_yet_validated': ['full receiver startup', 'live TLS with this HEVC candidate',
                                      'CarPlay playback', 'streaming performance improvement']}
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
