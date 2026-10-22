#!/usr/bin/env python3
"""Build an exact-base crypto-only install and exact-HEVC rollback package.

No service restart or reboot. Existing mappings remain active until the owner
power-cycles. No libraries, player, launchers, Wi-Fi or native recovery metadata
are changed. Vendor executable bytes are only written to ignored build output.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).parent))
from build_crypto_live_trial import CANDIDATE_SHA

VERSION = '2.9.11-oc10-hevc-neon'
BASE_VERSION = '2.9.11-oc9-hevc'
BASE_MD5 = 'a099e55592e70cabab0883e892410c14'
BASE_VERSION_MD5 = '709dd50851f49c71dfe6da3358a3aae3'
BACKUP = '/misc/zq-crypto-install1'
REPORT = '/usr/z-loader/zjinnova/web/zq-crypto-install1.txt'


def common():
    return '''#!/bin/sh
ROOT="${ZQ_ROOT:-}"
SAVE="${ZQ_MISC:-/misc}/zq-crypto-install1"
BIN="$ROOT/usr/z-loader/bin/z-tesla"
VER="$ROOT/usr/z-loader/webversion"
REPORT="$ROOT/usr/z-loader/zjinnova/web/zq-crypto-install1.txt"
sum() { md5sum "$1" 2>/dev/null | cut -d' ' -f1; }
log() { printf '%s\\n' "$*" >> "$REPORT"; }
command -v md5sum >/dev/null 2>&1 || exit 1
'''


def restore_script():
    return common() + f'''
# Validate the whole exact-HEVC backup before changing either installed file.
[ -f "$SAVE/COMPLETE" ] || exit 1
[ "$(sum "$SAVE/z-tesla")" = '{BASE_MD5}' ] || exit 1
[ "$(sum "$SAVE/webversion")" = '{BASE_VERSION_MD5}' ] || exit 1
cp -p "$SAVE/z-tesla" "$BIN.zq-crypto-old" || exit 1
cp -p "$SAVE/webversion" "$VER.zq-crypto-old" || exit 1
[ "$(sum "$BIN.zq-crypto-old")" = '{BASE_MD5}' ] || exit 1
[ "$(sum "$VER.zq-crypto-old")" = '{BASE_VERSION_MD5}' ] || exit 1
mv -f "$BIN.zq-crypto-old" "$BIN" || exit 1
mv -f "$VER.zq-crypto-old" "$VER" || exit 1
sync
[ "$(sum "$BIN")" = '{BASE_MD5}' ] || exit 1
[ "$(sum "$VER")" = '{BASE_VERSION_MD5}' ] || exit 1
rm -f "$SAVE/active"
sync
log 'rollback=exact-pre-crypto-HEVC-restored'
log 'activation=owner-power-cycle-required'
exit 0
'''


def install_script(candidate_md5, version_md5, restore_md5):
    return common() + f'''
[ ! -e "$SAVE" ] || exit 1
[ "$(sum "$BIN")" = '{BASE_MD5}' ] || exit 1
[ "$(sum "$VER")" = '{BASE_VERSION_MD5}' ] || exit 1
[ "$(sum payload/z-tesla)" = '{candidate_md5}' ] || exit 1
[ "$(sum payload/webversion)" = '{version_md5}' ] || exit 1
[ "$(sum payload/restore.sh)" = '{restore_md5}' ] || exit 1
unchanged() {{
 for path in usr/z-loader/bin/z-tesla-sh.sh usr/z-loader/update.sh etc/wlan-softap.sh etc/check-hostap.sh; do
  md5sum "$ROOT/$path" || return 1
 done
}}
before=$(unchanged) || exit 1
mkdir "$SAVE" || exit 1
cp -p "$BIN" "$SAVE/z-tesla" && cp -p "$VER" "$SAVE/webversion" || exit 1
cp payload/restore.sh "$SAVE/restore.sh" && chmod 755 "$SAVE/restore.sh" || exit 1
[ "$(sum "$SAVE/z-tesla")" = '{BASE_MD5}' ] || exit 1
[ "$(sum "$SAVE/webversion")" = '{BASE_VERSION_MD5}' ] || exit 1
[ "$(sum "$SAVE/restore.sh")" = '{restore_md5}' ] || exit 1
sync
touch "$SAVE/COMPLETE" || exit 1
sync
log 'package={VERSION}'
log 'backup=verified-exact-pre-crypto-HEVC'
abort_install() {{
 log 'install_exit=1'
 sh "$SAVE/restore.sh"
 rollback_exit=$?
 log "rollback_exit=$rollback_exit"
 exit 1
}}
# Stage and hash BOTH outputs before touching a live file. Rename keeps the
# currently running receiver's old inode intact; never overwrite it in place.
cp payload/z-tesla "$BIN.zq-crypto-new" && chmod 755 "$BIN.zq-crypto-new" || abort_install
cp payload/webversion "$VER.zq-crypto-new" && chmod 644 "$VER.zq-crypto-new" || abort_install
[ "$(sum "$BIN.zq-crypto-new")" = '{candidate_md5}' ] || abort_install
[ "$(sum "$VER.zq-crypto-new")" = '{version_md5}' ] || abort_install
touch "$SAVE/active" || abort_install
sync
mv -f "$BIN.zq-crypto-new" "$BIN" || abort_install
mv -f "$VER.zq-crypto-new" "$VER" || abort_install
sync
[ "$(sum "$BIN")" = '{candidate_md5}' ] || abort_install
[ "$(sum "$VER")" = '{version_md5}' ] || abort_install
after=$(unchanged) || abort_install
[ "$before" = "$after" ] || abort_install
log 'installed_binary_md5={candidate_md5}'
log 'installed_version_md5={version_md5}'
printf 'unchanged_files:\\n%s\\n' "$after" >> "$REPORT"
log 'protected_files_unchanged=1'
log 'activation=owner-power-cycle-required'
log 'service_restarts=0'
log 'install_exit=0'
log 'complete=1'
sync
exit 0
'''


def require_trial(text):
    fields = ['package=crypto-neon-live.4', 'trial_stop_reason=deadline-complete',
              'child_sigpipe_ignored=1', 'trial_exit=0', 'supervisor_exit=0',
              'original_receiver_restored=1', 'protected_files_unchanged=1',
              'overall_exit=0', 'complete=1']
    lines = text.splitlines()
    for field in fields:
        key = field.split('=', 1)[0]+'='
        if [line for line in lines if line.startswith(key)] != [field]:
            raise ValueError('missing or failed live validation: '+key)


def write_zip(path, files):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--live-report', required=True, type=Path)
    parser.add_argument('--tls-report', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    require_trial(args.live_report.read_text())
    data = (args.candidate/'z-tesla.neon').read_bytes()
    if hashlib.sha256(data).hexdigest() != CANDIDATE_SHA:
        raise ValueError('candidate differs from the live-tested binary')
    tests = json.loads((args.candidate/'test-results.json').read_text())
    if tests.get('passed') is not True or tests.get('candidate_sha256') != CANDIDATE_SHA:
        raise ValueError('missing matching local validation')
    probes = json.loads(args.tls_report.read_text())
    if not any(p.get('attributable_to_trial') is True and
               {r.get('port') for r in p.get('ports', []) if r.get('exit') == 0 and
                'Ciphersuite: TLS_CHACHA20_POLY1305_SHA256' in r.get('tls', [])} ==
               {5002, 7002, 9002} for p in probes):
        raise ValueError('missing complete attributable live TLS validation')
    version = (VERSION+'\n').encode()
    restore = restore_script()
    installer = install_script(hashlib.md5(data).hexdigest(), hashlib.md5(version).hexdigest(),
                               hashlib.md5(restore.encode()).hexdigest())
    rollback = '#!/bin/sh\nsh payload/restore.sh\nexit $?\n'
    args.output.mkdir(parents=True, exist_ok=False)
    for name, script in [('update.sh', installer), ('restore.sh', restore)]:
        path = args.output/name
        path.write_text(script)
        subprocess.run(['sh', '-n', str(path)], check=True)
    package = args.output/(VERSION+'.zip')
    undo = args.output/'rollback-crypto-hevc.zip'
    write_zip(package, {'update.sh': installer, 'payload/z-tesla': data,
                        'payload/webversion': version, 'payload/restore.sh': restore})
    write_zip(undo, {'update.sh': rollback, 'payload/restore.sh': restore})
    manifest = {'version': VERSION, 'base_version': BASE_VERSION,
                'package_sha256': hashlib.sha256(package.read_bytes()).hexdigest(),
                'rollback_sha256': hashlib.sha256(undo.read_bytes()).hexdigest(),
                'candidate_sha256': CANDIDATE_SHA,
                'changed_files': ['/usr/z-loader/bin/z-tesla', '/usr/z-loader/webversion'],
                'new_files': [BACKUP, REPORT], 'service_restarts': False, 'reboot': False,
                'activation': 'Next owner power cycle; current running mappings retained.',
                'rollback': 'Upload rollback-crypto-hevc.zip then power-cycle to restore '+BASE_VERSION,
                'limits': ['Five-minute functional validation on one adapter',
                           'No demonstrated end-to-end latency improvement',
                           'Permanent-path activation after power cycle remains to be checked']}
    (args.output/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
