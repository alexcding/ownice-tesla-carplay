#!/usr/bin/env python3
"""Build release combinations and verify that native upgrades retain NEON/HEVC.

No device access. Reads only selected firmware members through the real builder.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).parent))
import build_crypto_neon as crypto
from test_crypto_neon import check_layout

FIRMWARE = None


class ReleaseTests(unittest.TestCase):
    def build(self, output, *options):
        subprocess.run([sys.executable, '-I', str(Path(__file__).with_name('build_patch.py')),
                        '--firmware', str(FIRMWARE), '--out', str(output), *options],
                       check=True, capture_output=True, text=True, timeout=120)

    def test_all_native_hevc_variants_include_neon_and_preserve_hevc(self):
        with tempfile.TemporaryDirectory(prefix='crypto-releases-') as temporary:
            for diag, focus in ((False, False), (False, True), (True, False), (True, True)):
                with self.subTest(diagnostics=diag, focus=focus):
                    output = Path(temporary)/f'{diag}-{focus}.zip'
                    options = ['--native-hevc']
                    if diag:
                        options += ['--native-video-diag']
                    if focus:
                        options += ['--keep-compressed-video-focus']
                    self.build(output, *options)
                    with zipfile.ZipFile(output) as archive:
                        patched = archive.read('usr/z-loader/bin/z-tesla')
                        manifest = json.loads(archive.read('misc/zq-native/crypto-build.json'))
                        baseline = bytearray(patched[:manifest['base_bytes']])
                        baseline[crypto.ENTRY_OFFSET:crypto.ENTRY_OFFSET+8] = crypto.ENTRY_BYTES
                        struct.pack_into('<I', baseline, 28, 52)
                        struct.pack_into('<H', baseline, 44, 8)
                        check_layout(bytes(baseline), patched, manifest)
                        # Recompute the report hash too: the builder must catch
                        # damaged structure/content, not merely a stale digest.
                        for offset, message in [(0x1000, 'unrelated'), (-1, 'payload corrupted')]:
                            damaged = bytearray(patched)
                            damaged[offset] ^= 1
                            bad_report = dict(manifest, patched_sha256=hashlib.sha256(damaged).hexdigest())
                            with self.assertRaisesRegex(ValueError, message):
                                crypto.validate_patch(bytes(baseline), bytes(damaged), bad_report)
                        reports = json.loads(archive.read('misc/zq-native/hevc-build.json'))
                        tesla = next(r for r in reports if r['file'] == 'z-tesla')
                        self.assertEqual(tesla['patched_sha256'], hashlib.sha256(patched).hexdigest())
                        self.assertEqual(tesla['hevc_only_sha256'], manifest['base_sha256'])
                        self.assertEqual(tesla['crypto_neon'], manifest)
                        self.assertEqual(manifest['payload_sha256'], crypto.PAYLOAD_SHA256)
                        self.assertRegex(archive.read('usr/z-loader/webversion'), rb'^2\.9\.11-oc\d+\n$')
                        # Plain versions: the build identity says which variant is installed.
                        ident = archive.read('usr/z-loader/zq-build')
                        self.assertRegex(ident, rb'^2\.9\.11-oc\d+\+[0-9a-f]{12}\n$')
                        info = archive.read('usr/z-loader/zjinnova/web/zq-build.txt').decode()
                        self.assertTrue(info.startswith(ident.decode()))
                        self.assertIn('native-hevc', info)
                        self.assertEqual('native-video-diag' in info, diag)
                        self.assertEqual(hashlib.sha256(archive.read('misc/zq-native/OPENSSL-LICENSE')).hexdigest(),
                                         crypto.SOURCES['LICENSE'])
                        if diag and focus:
                            # oc12 converter-report filter; the live-tested oc10 receiver was
                            # 43e9b44dba7e87940fcdb1b4bd436973e247760c710799a77648f1f7b0d83c52.
                            self.assertEqual(hashlib.sha256(patched).hexdigest(),
                                '8406352675df0e3ce88d1f06cc35e2ba4d4e3f42e913020b6016a39420ce43da')

    def test_web_only_preserves_installed_receiver_and_recovery_exceptions_are_explicit(self):
        with tempfile.TemporaryDirectory(prefix='crypto-preservation-') as temporary:
            for name, options in [('web', []), ('native-test', ['--native-test']),
                                  ('restore', ['--restore-native'])]:
                with self.subTest(package=name):
                    output = Path(temporary)/(name+'.zip')
                    self.build(output, *options)
                    with zipfile.ZipFile(output) as archive:
                        self.assertNotIn('usr/z-loader/bin/z-tesla', archive.namelist())
                        self.assertIn('usr/z-loader/zq-build', archive.namelist())
                        self.assertNotIn('misc/zq-native/crypto-build.json', archive.namelist())

    def test_unreviewed_native_base_and_corrupt_source_fail_closed(self):
        with self.assertRaisesRegex(ValueError, 'unsupported'):
            crypto.patch_binary(b'unreviewed future native generation', b'', 0, 0)
        with tempfile.TemporaryDirectory(prefix='crypto-source-refusal-') as directory:
            (Path(directory)/'chacha-armv4.pl').write_text('modified source')
            with self.assertRaisesRegex(ValueError, 'cached source hash mismatch'):
                crypto.resolve_sources(directory)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware', required=True, type=Path)
    args, remaining = parser.parse_known_args()
    FIRMWARE = args.firmware.resolve()
    unittest.main(argv=[__file__, *remaining])
