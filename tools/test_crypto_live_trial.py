#!/usr/bin/env python3
"""Reject incomplete recovery evidence and false-success trial reports."""
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parent))
import build_crypto_live_trial as builder


def good_evidence():
    lines = ['package=crypto-guard-check.4', 'runtime_exit=0', 'kernel_check_exit=0',
             'protected_files_unchanged=1', 'overall_exit=0', 'complete=1']
    lines += ['child_sigpipe_probe_survived=1'] * 3
    for mode, code in [('trial', 0), ('crash', 90), ('hang', 137)]:
        lines += [f'fixture_{mode}.controller_exit={code}',
                  f'fixture_{mode}.restored=1', f'fixture_{mode}.remaining=0']
    return '\n'.join(lines) + '\n'


class EvidenceTests(unittest.TestCase):
    def test_complete_evidence_passes(self):
        builder.require_device_rehearsal(good_evidence())

    def test_missing_or_failed_recovery_blocks(self):
        for line in good_evidence().splitlines():
            with self.subTest(line=line):
                with self.assertRaises(ValueError):
                    builder.require_device_rehearsal(good_evidence().replace(line+'\n', ''))
                with self.assertRaises(ValueError):
                    builder.require_device_rehearsal(good_evidence().replace(line, line+'-invalid'))

    def test_duplicate_status_blocks(self):
        with self.assertRaises(ValueError):
            builder.require_device_rehearsal(good_evidence()+'overall_exit=1\n')


class StatusTests(unittest.TestCase):
    def run_tail(self, loader=0, trial=0, restored=1, snapshot='same', snapshot_status=0):
        script = builder.installer('candidate', 'guard', 'runtime', 'loader')
        tail = script[script.index('after=$(protected)'):]
        with tempfile.TemporaryDirectory(prefix='crypto-live-status-') as directory:
            output = Path(directory) / 'report'
            prefix = ('protected() { printf %s '+shlex.quote(snapshot)+'; return '+
                      str(snapshot_status)+'; }\nsync() { :; }\nbefore=same\n'+
                      f'loader_exit={loader}\ntrial_exit={trial}\nrestored={restored}\n')
            with output.open('w') as stream:
                result = subprocess.run(['sh', '-c', prefix+tail], stdout=stream,
                                        stderr=subprocess.PIPE, timeout=5)
            return result.returncode, output.read_text()

    def test_success_requires_completed_trial_and_restoration(self):
        status, report = self.run_tail()
        self.assertEqual(status, 0, report)
        for arguments in [{'loader': 1}, {'trial': 1}, {'restored': 0},
                          {'snapshot': 'changed'}, {'snapshot_status': 1}]:
            with self.subTest(arguments=arguments):
                status, report = self.run_tail(**arguments)
                self.assertNotEqual(status, 0, report)
                self.assertIn('overall_exit=1', report)


if __name__ == '__main__':
    unittest.main()
