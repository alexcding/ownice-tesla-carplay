#!/usr/bin/env python3
"""Exercise the real generated installer's final preservation/status checks locally."""
import argparse
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


class InstallerStatusTests(unittest.TestCase):
    installer = ''

    def run_tail(self, snapshot='same', snapshot_exit=0, probe_exit=0, loader_exit=0):
        tail = self.installer[self.installer.index('after=$(protected)'):]
        with tempfile.TemporaryDirectory(prefix='crypto-installer-test-') as directory:
            report = Path(directory) / 'report.txt'
            script = ('protected() { printf %s ' + shlex.quote(snapshot) +
                      '; return ' + str(snapshot_exit) + '; }\n'
                      'sync() { :; }\nbefore=same\nREPORT=' + shlex.quote(str(report)) +
                      '\nprobe_exit=' + str(probe_exit) + '\nloader_exit=' + str(loader_exit) + '\n' + tail)
            process = subprocess.run(['sh', '-c', script], capture_output=True, text=True, timeout=5)
            return process.returncode, report.read_text()

    def test_preserved_files_and_successful_tests_pass(self):
        status, report = self.run_tail()
        self.assertEqual(status, 0)
        self.assertIn('protected_files_unchanged=1', report)
        self.assertIn('overall_exit=0', report)

    def test_changed_protected_file_fails(self):
        status, report = self.run_tail(snapshot='different')
        self.assertNotEqual(status, 0)
        self.assertIn('protected_files_unchanged=0', report)
        self.assertIn('overall_exit=1', report)

    def test_failed_snapshot_fails_even_when_text_matches(self):
        status, report = self.run_tail(snapshot_exit=1)
        self.assertNotEqual(status, 0)
        self.assertIn('protected_files_unchanged=0', report)

    def test_probe_timeout_fails(self):
        status, report = self.run_tail(probe_exit=124)
        self.assertNotEqual(status, 0)
        self.assertIn('overall_exit=1', report)

    def test_loader_failure_fails(self):
        status, report = self.run_tail(loader_exit=1)
        self.assertNotEqual(status, 0)
        self.assertIn('overall_exit=1', report)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installer', type=Path, required=True)
    args = parser.parse_args()
    InstallerStatusTests.installer = args.installer.read_text()
    unittest.main(argv=['test_crypto_sidecar_installer.py'])
