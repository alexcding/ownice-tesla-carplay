#!/usr/bin/env python3
"""Package a separately staged crypto benchmark. Never uploads or replaces services."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

VERSION = 'crypto-neon-v2-sidecar.4'
CANDIDATE_SHA = '5f78c46743dfb667a5d4c8d8b4d8a0d109fbed983aad305214a13b4641c738c2'
STAGE = '/misc/zq-crypto-v2-sidecar4'
REPORT = '/usr/z-loader/zjinnova/web/zq-crypto-v2-sidecar4.txt'
LINKER_SCRIPT = '''PHDRS { headers PT_PHDR PHDRS; text PT_LOAD FILEHDR PHDRS FLAGS(5); data PT_LOAD FLAGS(6); }
SECTIONS {
 . = 0x10000000 + SIZEOF_HEADERS;
 .text : { *(.text*) } :text
 .rodata : { *(.rodata*) } :text
 . = ALIGN(0x1000);
 .data : { *(.data*) } :data
 .bss (NOLOAD) : { *(.bss*) *(COMMON) } :data
 /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) }
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--clang', default='clang')
    parser.add_argument('--lld', default='/opt/homebrew/opt/lld@22/bin/ld.lld')
    args = parser.parse_args()
    data = args.candidate.read_bytes()
    if hashlib.sha256(data).hexdigest() != CANDIDATE_SHA:
        raise ValueError('unexpected candidate identity')
    args.output.mkdir(parents=True, exist_ok=False)
    link = args.output / 'probe.ld'
    link.write_text(LINKER_SCRIPT)
    obj, probe = args.output / 'probe.o', args.output / 'crypto-probe'
    subprocess.run([args.clang, '--target=armv7-linux-gnueabihf', '-mcpu=cortex-a7',
                    '-marm', '-mfloat-abi=soft', '-mfpu=none', '-O2', '-ffreestanding',
                    '-fno-builtin', '-fno-stack-protector', '-fomit-frame-pointer',
                    '-fno-unwind-tables', '-fno-asynchronous-unwind-tables', '-Wall', '-Wextra',
                    '-c', str(Path(__file__).with_name('crypto_device_probe.c')), '-o', str(obj)], check=True)
    subprocess.run([args.lld, '-m', 'armelf_linux_eabi', '-static', '--no-undefined',
                    '-T', str(link), '-e', '_start', str(obj), '-o', str(probe)], check=True)
    helper = probe.read_bytes()
    candidate_md5, helper_md5 = hashlib.md5(data).hexdigest(), hashlib.md5(helper).hexdigest()
    installer = f'''#!/bin/sh
# {VERSION}: a standalone test, never a service replacement.
STAGE='{STAGE}'
REPORT='{REPORT}'
[ ! -e "$STAGE" ] && [ ! -e "$REPORT" ] || exit 1
[ -d /usr/z-loader/zjinnova/web ] || exit 1
command -v md5sum >/dev/null 2>&1 || exit 1
ENV_BIN=$(command -v env) || exit 1
sum() {{ md5sum "$1" | cut -d' ' -f1; }}
[ "$(sum payload/z-tesla.neon)" = '{candidate_md5}' ] || exit 1
[ "$(sum payload/crypto-probe)" = '{helper_md5}' ] || exit 1
protected() {{
 for path in /usr/z-loader/bin/z-tesla /usr/z-loader/bin/z-tesla-sh.sh /usr/z-loader/webversion /usr/z-loader/update.sh /etc/wlan-softap.sh /etc/check-hostap.sh; do
  md5sum "$path" || return 1
 done
}}
before=$(protected) || exit 1
mkdir "$STAGE" || exit 1
echo 'package={VERSION}' > "$REPORT" || exit 1
echo 'installed_files_unchanged_expected=1' >> "$REPORT"
printf 'protected_before:\\n%s\\n' "$before" >> "$REPORT"
cp payload/z-tesla.neon "$STAGE/z-tesla.neon" && cp payload/crypto-probe "$STAGE/crypto-probe" || exit 1
chmod 755 "$STAGE/z-tesla.neon" "$STAGE/crypto-probe" || exit 1
[ "$(sum "$STAGE/z-tesla.neon")" = '{candidate_md5}' ] && [ "$(sum "$STAGE/crypto-probe")" = '{helper_md5}' ] || exit 1
echo 'candidate_sha256={CANDIDATE_SHA}' >> "$REPORT"
echo 'loader_trace_begin' >> "$REPORT"
"$STAGE/crypto-probe" --guard 5 "$ENV_BIN" LD_LIBRARY_PATH=/usr/z-loader/lib LD_PRELOAD= LD_TRACE_LOADED_OBJECTS=1 "$STAGE/z-tesla.neon" >> "$REPORT" 2>&1
loader_exit=$?
echo "loader_trace_exit=$loader_exit" >> "$REPORT"
echo 'benchmark_begin' >> "$REPORT"
"$STAGE/crypto-probe" --guard 12 "$STAGE/crypto-probe" "$STAGE/z-tesla.neon" >> "$REPORT" 2>&1
probe_exit=$?
echo "benchmark_exit=$probe_exit" >> "$REPORT"
after=$(protected)
snapshot_exit=$?
printf 'protected_after:\\n%s\\n' "$after" >> "$REPORT"
preservation_exit=0
if [ "$snapshot_exit" = 0 ] && [ "$before" = "$after" ]; then
 echo 'protected_files_unchanged=1' >> "$REPORT"
else
 echo 'protected_files_unchanged=0' >> "$REPORT"
 preservation_exit=1
fi
overall_exit=0
if [ "$probe_exit" != 0 ] || [ "$loader_exit" != 0 ] || [ "$preservation_exit" != 0 ]; then overall_exit=1; fi
echo "overall_exit=$overall_exit" >> "$REPORT"
echo 'complete=1' >> "$REPORT"
sync
exit "$overall_exit"
'''
    (args.output / 'update.sh').write_text(installer)
    package = args.output / (VERSION + '.zip')
    with zipfile.ZipFile(package, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('update.sh', installer)
        archive.writestr('payload/z-tesla.neon', data)
        archive.writestr('payload/crypto-probe', helper)
    manifest = {'package_version': VERSION, 'zip_sha256': hashlib.sha256(package.read_bytes()).hexdigest(),
                'candidate_sha256': CANDIDATE_SHA, 'probe_sha256': hashlib.sha256(helper).hexdigest(),
                'new_files': [STAGE + '/z-tesla.neon', STAGE + '/crypto-probe', REPORT],
                'installed_binary_replaced': False, 'service_restarts': False,
                'webversion_changed': False, 'autostart': False,
                'timing_schema': 2,
                'benchmark_clocks': ['CLOCK_MONOTONIC', 'CLOCK_PROCESS_CPUTIME_ID'],
                'rollback': 'No activation to undo; the staged files are inert and tests exit automatically.'}
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
