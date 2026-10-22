#!/usr/bin/env python3
"""Package kernel checks and recovery rehearsals on disposable owned processes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).parent))
import build_crypto_preflight as runtime
from build_crypto_sidecar import LINKER_SCRIPT

VERSION = 'crypto-guard-check.4'
STAGE = '/misc/zq-crypto-live4'
REPORT = '/usr/z-loader/zjinnova/web/zq-crypto-guard4.txt'
RUNTIME = '/usr/z-loader/zjinnova/web/zq-crypto-guard4-runtime.txt'
SOURCE = Path(__file__).with_name('crypto_trial_guard.c')
FIXTURE = '''#!/bin/sh
STAGE=/misc/zq-crypto-live4
generation=0
while :; do
 "$STAGE/trial-guard" &
 receiver=$!
 generation=$((generation + 1))
 echo "$receiver" > "$STAGE/fixture.pid"
 echo "$generation" > "$STAGE/fixture.generation"
 wait "$receiver"
done
'''


def installer(helper_md5, fixture_md5, runtime_md5):
    return r'''#!/bin/sh
STAGE='@STAGE@'
REPORT='@REPORT@'
RUNTIME='@RUNTIME@'
[ ! -e "$STAGE" ] && [ ! -e "$REPORT" ] && [ ! -e "$RUNTIME" ] || exit 1
sum() { md5sum "$1" 2>/dev/null | cut -d' ' -f1; }
protected() {
 for path in /usr/z-loader/bin/z-tesla /usr/z-loader/bin/z-tesla-sh.sh /usr/z-loader/webversion /usr/z-loader/update.sh /etc/wlan-softap.sh /etc/check-hostap.sh; do
  md5sum "$path" || return 1
 done
}
before=$(protected) || exit 1
[ "$(sum payload/trial-guard)" = '@HELPER_MD5@' ] || exit 1
[ "$(sum payload/fixture.sh)" = '@FIXTURE_MD5@' ] || exit 1
[ "$(sum payload/runtime-check.sh)" = '@RUNTIME_MD5@' ] || exit 1
mkdir "$STAGE" || exit 1
for name in trial-guard fixture.sh runtime-check.sh; do
 cp "payload/$name" "$STAGE/$name" && chmod 755 "$STAGE/$name" || exit 1
done
[ "$(sum "$STAGE/trial-guard")" = '@HELPER_MD5@' ] || exit 1
[ "$(sum "$STAGE/fixture.sh")" = '@FIXTURE_MD5@' ] || exit 1
[ "$(sum "$STAGE/runtime-check.sh")" = '@RUNTIME_MD5@' ] || exit 1
exec > "$REPORT" 2>&1 || exit 1
echo 'package=@VERSION@'
echo 'installed_service_changes=0'
printf 'protected_before:\n%s\n' "$before"
failed=0
launcher=
launcher_tick=
cleanup() {
 if [ -n "$launcher" ] && [ -n "$launcher_tick" ]; then
  "$STAGE/trial-guard" --cleanup-fixture "$launcher" "$launcher_tick" || failed=1
  wait "$launcher" 2>/dev/null
  launcher=
 fi
}
trap 'cleanup' 0
trap 'exit 1' 1 2 15
tick() {
 line=$(cat "/proc/$1/stat" 2>/dev/null) || return 1
 rest=${line##*) }; set -- $rest
 [ "$#" -ge 20 ] || return 1
 shift 19
 case "$1" in ''|*[!0-9]*) return 1;; esac
 echo "$1"
}
value() { sed -n "s/^$1=//p" "$RUNTIME"; }
sh "$STAGE/runtime-check.sh"
runtime_exit=$?
echo "runtime_exit=$runtime_exit"
if [ "$runtime_exit" = 0 ] && [ "$(value overall_exit)" = 0 ]; then
 lp=$(value launcher.pid); li=$(value launcher.identity); li=${li#*:}; lt=${li%:*}
 rp=$(value z-tesla.pid); ri=$(value z-tesla.identity); ri=${ri#*:}; rt=${ri%:*}
 "$STAGE/trial-guard" --check "$lp" "$lt" "$rp" "$rt"
 kernel_exit=$?
 echo "kernel_check_exit=$kernel_exit"
else
 kernel_exit=1
fi
if [ "$kernel_exit" = 0 ]; then
 for mode in trial crash hang; do
  echo "fixture_begin=$mode"
  # These files contain only identities of our own disposable processes.
  rm -f "$STAGE/fixture.pid" "$STAGE/fixture.generation"
  sh "$STAGE/fixture.sh" &
  launcher=$!
  launcher_tick=$(tick "$launcher")
  i=0
  while [ ! -s "$STAGE/fixture.pid" ] && [ "$i" -lt 5 ]; do sleep 1; i=$((i+1)); done
  sleep 1
  receiver=$(cat "$STAGE/fixture.pid" 2>/dev/null)
  receiver_tick=$(tick "$receiver")
  if [ -z "$launcher_tick" ] || [ -z "$receiver_tick" ]; then
   failed=1; cleanup; break
  fi
  "$STAGE/trial-guard" "--fixture-$mode" "$launcher" "$launcher_tick" "$receiver" "$receiver_tick" 2 "$STAGE/trial-guard"
  code=$?
  echo "fixture_$mode.controller_exit=$code"
  case "$mode:$code" in trial:0|crash:90|hang:137) ;; *) failed=1;; esac
  i=0
  while [ "$i" -lt 5 ]; do
   generation=$(cat "$STAGE/fixture.generation" 2>/dev/null)
   [ "${generation:-0}" -ge 2 ] && break
   sleep 1; i=$((i+1))
  done
  replacement=$(cat "$STAGE/fixture.pid" 2>/dev/null)
  if [ "${generation:-0}" -ge 2 ] && [ "$replacement" != "$receiver" ] &&
     [ "$(readlink "/proc/$replacement/exe")" = "$STAGE/trial-guard" ]; then
   echo "fixture_$mode.restored=1"
  else
   echo "fixture_$mode.restored=0"; failed=1
  fi
  cleanup
  # PDEATHSIG must also remove the fixture's current receiver at cleanup.
  i=0
  while [ "$i" -lt 5 ]; do
   remaining=0
   for entry in /proc/[0-9]*; do
    [ "$(readlink "$entry/exe" 2>/dev/null)" != "$STAGE/trial-guard" ] || remaining=$((remaining+1))
   done
   [ "$remaining" = 0 ] && break
   sleep 1; i=$((i+1))
  done
  echo "fixture_$mode.remaining=$remaining"
  [ "$remaining" = 0 ] || failed=1
  [ "$failed" = 0 ] || break
 done
else
 failed=1
fi
after=$(protected)
snapshot_exit=$?
printf 'protected_after:\n%s\n' "$after"
if [ "$snapshot_exit" = 0 ] && [ "$before" = "$after" ]; then
 echo protected_files_unchanged=1
else
 echo protected_files_unchanged=0; failed=1
fi
echo "overall_exit=$failed"
echo complete=1
sync
exit "$failed"
'''.replace('@STAGE@', STAGE).replace('@REPORT@', REPORT).replace(
        '@RUNTIME@', RUNTIME).replace('@VERSION@', VERSION).replace(
        '@HELPER_MD5@', helper_md5).replace('@FIXTURE_MD5@', fixture_md5).replace(
        '@RUNTIME_MD5@', runtime_md5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--tests', required=True, type=Path)
    parser.add_argument('--clang', default='clang')
    parser.add_argument('--lld', default='/opt/homebrew/opt/lld@22/bin/ld.lld')
    args = parser.parse_args()
    tests = json.loads(args.tests.read_text())
    source_sha = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    if tests.get('passed') is not True or tests.get('source_sha256') != source_sha:
        raise ValueError('missing or stale guard validation')
    args.output.mkdir(parents=True, exist_ok=False)
    link = args.output / 'guard.ld'
    link.write_text(LINKER_SCRIPT)
    obj, helper = args.output / 'guard.o', args.output / 'trial-guard'
    subprocess.run([args.clang, '--target=armv7-linux-gnueabihf', '-mcpu=cortex-a7',
                    '-marm', '-mfloat-abi=soft', '-mfpu=none', '-O2', '-ffreestanding',
                    '-fno-builtin', '-fno-stack-protector', '-fomit-frame-pointer',
                    '-fno-unwind-tables', '-fno-asynchronous-unwind-tables', '-Wall',
                    '-Wextra', '-Werror', '-c', str(SOURCE), '-o', str(obj)], check=True)
    subprocess.run([args.lld, '-m', 'armelf_linux_eabi', '-static', '--no-undefined',
                    '-T', str(link), '-e', '_start', str(obj), '-o', str(helper)], check=True)
    payload = {'trial-guard': helper.read_bytes(), 'fixture.sh': FIXTURE.encode(),
               'runtime-check.sh': runtime.script().replace(runtime.REPORT, RUNTIME).encode()}
    update = installer(*(hashlib.md5(payload[n]).hexdigest()
                         for n in ('trial-guard', 'fixture.sh', 'runtime-check.sh')))
    (args.output / 'update.sh').write_text(update)
    archive = args.output / (VERSION + '.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as package:
        package.writestr('update.sh', update)
        for name, data in payload.items():
            package.writestr('payload/' + name, data)
    manifest = {'package_version': VERSION,
                'zip_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                'guard_source_sha256': source_sha,
                'guard_sha256': hashlib.sha256(payload['trial-guard']).hexdigest(),
                'new_files': [STAGE, REPORT, RUNTIME],
                'installed_service_changes': False, 'autostart': False,
                'tests': ['runtime native identity', 'kernel pidfd support',
                          'dummy normal completion', 'dummy controller crash',
                          'dummy controller timeout', 'dummy cleanup'],
                'rollback': 'Only dummy processes are restarted; no installed activation to undo.'}
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
