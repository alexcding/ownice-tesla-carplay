#!/usr/bin/env python3
"""Exercise generated crypto-only install/rollback scripts in scratch roots."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import build_crypto_install as builder


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='crypto-install-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.root, self.misc, self.package = [self.directory/n for n in ('root', 'misc', 'package')]
        self.payload = self.package/'payload'
        self.payload.mkdir(parents=True)
        self.bin = self.root/'usr/z-loader/bin/z-tesla'
        self.version = self.root/'usr/z-loader/webversion'
        self.old = b'original HEVC executable'
        self.new = b'optimized HEVC executable'
        base = patch.object(builder, 'BASE_MD5', hashlib.md5(self.old).hexdigest())
        base.start()
        self.addCleanup(base.stop)
        for path, data in [(self.bin, self.old),
                           (self.version, (builder.BASE_VERSION+'\n').encode())]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (self.root/'usr/z-loader/zjinnova/web').mkdir(parents=True)
        self.misc.mkdir()
        self.protected = []
        for name in ('usr/z-loader/bin/z-tesla-sh.sh', 'usr/z-loader/update.sh',
                     'etc/wlan-softap.sh', 'etc/check-hostap.sh', 'data/local/tmp/zq_hevc'):
            path = self.root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('preserved:'+name)
            self.protected.append((path, path.read_bytes()))
        (self.misc/'zq-native').mkdir()
        native = self.misc/'zq-native/active'
        native.write_text('existing HEVC metadata')
        self.protected.append((native, native.read_bytes()))
        self.restore = builder.restore_script()
        (self.payload/'restore.sh').write_text(self.restore)
        (self.payload/'z-tesla').write_bytes(self.new)
        (self.payload/'webversion').write_text(builder.VERSION+'\n')
        self.installer = builder.install_script(hashlib.md5(self.new).hexdigest(),
            hashlib.md5((builder.VERSION+'\n').encode()).hexdigest(),
            hashlib.md5(self.restore.encode()).hexdigest())
        self.env = dict(os.environ, ZQ_ROOT=str(self.root), ZQ_MISC=str(self.misc))
        # Avoid flushing the host filesystem while exercising scratch files.
        self.commands = self.directory/'commands'
        self.commands.mkdir()
        (self.commands/'sync').write_text('#!/bin/sh\nexit 0\n')
        (self.commands/'sync').chmod(0o755)
        self.env['PATH'] = str(self.commands)+':'+os.environ['PATH']

    def run_script(self, text):
        return subprocess.run(['sh', '-c', text], cwd=self.package, env=self.env,
                              capture_output=True, text=True, timeout=10)

    def assert_preserved(self):
        for path, data in self.protected:
            self.assertEqual(path.read_bytes(), data, str(path))

    def test_install_retains_running_inode_and_exact_rollback(self):
        with self.bin.open('rb') as running:
            result = self.run_script(self.installer)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(running.read(), self.old)
        self.assertEqual(self.bin.read_bytes(), self.new)
        self.assertEqual(self.version.read_text(), builder.VERSION+'\n')
        self.assertEqual(self.bin.stat().st_mode&0o777, 0o755)
        self.assert_preserved()
        result = self.run_script(self.restore)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.bin.read_bytes(), self.old)
        self.assertEqual(self.version.read_text(), builder.BASE_VERSION+'\n')
        self.assert_preserved()

    def test_wrong_installed_base_refuses_before_backup(self):
        self.bin.write_bytes(b'unknown receiver')
        self.assertNotEqual(self.run_script(self.installer).returncode, 0)
        self.assertEqual(self.bin.read_bytes(), b'unknown receiver')
        self.assertFalse((self.misc/'zq-crypto-install1').exists())

    def test_corrupt_payload_refuses_before_replacement(self):
        (self.payload/'z-tesla').write_bytes(b'corrupt')
        self.assertNotEqual(self.run_script(self.installer).returncode, 0)
        self.assertEqual(self.bin.read_bytes(), self.old)
        self.assert_preserved()

    def test_failed_version_swap_rolls_back_both_files(self):
        real_mv = shutil.which('mv')
        stub = self.commands/'mv'
        stub.write_text('#!/bin/sh\ncase "$2" in *.zq-crypto-new)\n'
                        ' case "$2" in *webversion*) exit 1;; esac;;\nesac\n'
                        'exec '+real_mv+' "$@"\n')
        stub.chmod(0o755)
        self.assertNotEqual(self.run_script(self.installer).returncode, 0)
        self.assertEqual(self.bin.read_bytes(), self.old)
        self.assertEqual(self.version.read_text(), builder.BASE_VERSION+'\n')
        self.assert_preserved()

    def test_corrupt_backup_refuses_rollback_before_writing(self):
        self.assertEqual(self.run_script(self.installer).returncode, 0)
        (self.misc/'zq-crypto-install1/z-tesla').write_bytes(b'corrupt')
        self.assertNotEqual(self.run_script(self.restore).returncode, 0)
        self.assertEqual(self.bin.read_bytes(), self.new)
        self.assertEqual(self.version.read_text(), builder.VERSION+'\n')
        self.assert_preserved()

    def test_existing_backup_is_never_overwritten(self):
        self.assertEqual(self.run_script(self.installer).returncode, 0)
        backup = self.misc/'zq-crypto-install1/z-tesla'
        self.assertNotEqual(self.run_script(self.installer).returncode, 0)
        self.assertEqual(backup.read_bytes(), self.old)


if __name__ == '__main__':
    unittest.main()
