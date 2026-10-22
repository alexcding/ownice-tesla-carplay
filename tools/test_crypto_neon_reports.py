#!/usr/bin/env python3
"""Regression checks for failure reporting; no emulated code or device access."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

import test_crypto_neon as runner


class ReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='ownice-report-test-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.baseline = self.directory / 'stock'
        self.baseline.write_bytes(b'not the pinned binary')
        (self.directory / 'z-tesla.neon').write_bytes(b'not an ELF')
        (self.directory / 'manifest.json').write_text('{}')
        self.report = self.directory / 'test-results.json'
        self.report.write_text(json.dumps({'passed': True, 'stale_marker': True}))
        arguments = ['test_crypto_neon.py', '--binary', str(self.baseline),
                     '--candidate', str(self.directory)]
        argv = patch.object(sys, 'argv', arguments)
        argv.start()
        self.addCleanup(argv.stop)

    def assert_failed(self, error_type):
        report = json.loads(self.report.read_text())
        self.assertIs(report['passed'], False)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['error_type'], error_type)
        self.assertNotIn('stale_marker', report)
        self.assertIn('started_at', report)
        self.assertIn('finished_at', report)
        self.assertEqual(list(self.directory.glob('.test-results-*.tmp')), [])

    def test_missing_input_replaces_old_pass(self):
        self.baseline.unlink()
        with self.assertRaises(FileNotFoundError):
            runner.main()
        self.assert_failed('FileNotFoundError')

    def test_malformed_manifest_replaces_old_pass(self):
        (self.directory / 'manifest.json').write_text('{malformed')
        with self.assertRaises(json.JSONDecodeError):
            runner.main()
        self.assert_failed('JSONDecodeError')

    def test_failed_validation_replaces_old_pass(self):
        with self.assertRaisesRegex(ValueError, 'wrong test baseline'):
            runner.main()
        self.assert_failed('ValueError')

    def test_old_pass_is_invalidated_before_suite_and_interrupt_is_recorded(self):
        def interrupted(args):
            report = json.loads(self.report.read_text())
            self.assertIs(report['passed'], False)
            self.assertEqual(report['status'], 'running')
            self.assertNotIn('stale_marker', report)
            raise KeyboardInterrupt()

        with patch.object(runner, 'run_suite', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                runner.main()
        self.assert_failed('KeyboardInterrupt')


if __name__ == '__main__':
    unittest.main()
