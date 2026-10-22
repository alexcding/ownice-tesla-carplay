#!/usr/bin/env python3
"""Run the generated runtime probe against synthetic proc/file identities."""
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parent))
import build_crypto_preflight as builder


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='crypto-preflight-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'usr/z-loader/bin'
        self.proc = self.root / 'proc'
        self.bin.mkdir(parents=True)
        self.proc.mkdir()
        self.report = self.root / 'report.txt'
        for name in ('z-tesla', 'z-link', 'z-tesla-sh.sh'):
            (self.bin / name).write_text(name)
        for pid, name, parent in ((100, 'z-tesla', 50), (200, 'z-link', 60)):
            entry = self.proc / str(pid)
            (entry / 'map_files').mkdir(parents=True)
            (entry / 'exe').symlink_to(self.bin / name)
            (entry / 'map_files/1000-2000').symlink_to(self.bin / name)
            (entry / 'maps').write_text(
                f'1000-2000 r-xp 0000 00:01 1 {self.bin / name}\n')
            self.stat(pid, parent)
        (self.proc / '50').mkdir()
        self.stat(50, 1)
        (self.proc / '50/cmdline').write_bytes(
            b'/bin/sh\0' + str(self.bin / 'z-tesla-sh.sh').encode() + b'\0')
        for pid in (50, 100, 200):
            (self.proc / str(pid) / 'status').write_text(
                'SigIgn:\t0000000000001000\nSigBlk:\t0000000000000000\n'
                'SigCgt:\t0000000000000000\n')

    def stat(self, pid, parent, state='S', tick=123):
        # Include spaces and ')' in comm to exercise the stat parser.
        tail = [state, str(parent)] + ['0'] * 17 + [str(tick)]
        (self.proc / str(pid) / 'stat').write_text(
            f'{pid} (test ) receiver) ' + ' '.join(tail) + '\n')

    def run_probe(self):
        text = builder.script().replace(builder.REPORT, str(self.report))
        text = text.replace('PROC=/proc', f'PROC={self.proc}')
        text = text.replace('/usr/z-loader/bin', str(self.bin))
        text = text.replace(builder.RECEIVER_MD5, hashlib.md5(b'z-tesla').hexdigest())
        text = text.replace(builder.LAUNCHER_MD5, hashlib.md5(b'z-tesla-sh.sh').hexdigest())
        result = subprocess.run(['sh', '-c', text], capture_output=True, timeout=10)
        return result.returncode, self.report.read_text()

    def test_matching_running_generation_passes(self):
        status, report = self.run_probe()
        self.assertEqual(status, 0, report)
        self.assertIn('overall_exit=0', report)
        self.assertIn('launcher.identity=1:123:S', report)
        self.assertIn('z-tesla.SigIgn=0000000000001000', report)

    def test_old_running_executable_blocks(self):
        # readlink still resolves the expected pathname; md5sum sees old bytes.
        # Replace md5sum for this one file using a shell function.
        text = builder.script()
        original = builder.script
        builder.script = lambda: text.replace(
            'running=$(sum "$PROC/$pid/exe")',
            'running=$(sum "$PROC/$pid/exe"); [ "$name" != z-tesla ] || running=old')
        try:
            status, report = self.run_probe()
        finally:
            builder.script = original
        self.assertNotEqual(status, 0)
        self.assertIn('blocked=z-tesla-generation-mismatch', report)

    def test_replaced_mapped_library_blocks(self):
        mapped = self.proc / '100/map_files/1000-2000'
        mapped.unlink()
        old = self.root / 'old-mapped-file'
        old.write_text('old')
        mapped.symlink_to(old)
        status, report = self.run_probe()
        self.assertNotEqual(status, 0)
        self.assertIn('blocked=z-tesla-mapped-file-unverified', report)

    def test_inaccessible_mapped_file_blocks(self):
        with (self.proc / '100/maps').open('a') as maps:
            maps.write('3000-4000 r-xp 0000 00:01 2 /lib/libmissing.so\n')
        status, report = self.run_probe()
        self.assertNotEqual(status, 0)
        self.assertIn('blocked=z-tesla-mapped-file-unverified', report)

    def test_unavailable_main_map_uses_running_executable(self):
        (self.proc / '100/map_files/1000-2000').unlink()
        status, report = self.run_probe()
        self.assertEqual(status, 0, report)
        self.assertIn('z-tesla.main_map_source=proc-exe', report)

    def test_anonymous_and_arm_special_maps_are_reported(self):
        with (self.proc / '100/maps').open('a') as maps:
            maps.write('3000-4000 rwxp 0000 00:00 0\n')
            maps.write('4000-5000 r-xp 0000 00:00 0 [sigpage]\n')
        status, report = self.run_probe()
        self.assertEqual(status, 0, report)
        self.assertIn('z-tesla.anonymous_executable_maps=1', report)
        self.assertIn('z-tesla.special_map=[sigpage]', report)

    def test_wrong_receiver_parent_blocks(self):
        (self.proc / '50/cmdline').write_bytes(b'/bin/sh\0unrelated\0')
        status, report = self.run_probe()
        self.assertNotEqual(status, 0)
        self.assertIn('blocked=receiver-parent-is-not-launcher', report)

    def test_duplicate_receiver_blocks(self):
        extra = self.proc / '101'
        extra.mkdir()
        (extra / 'exe').symlink_to(self.bin / 'z-tesla')
        status, report = self.run_probe()
        self.assertNotEqual(status, 0)
        self.assertIn('blocked=z-tesla-process-count', report)

    def test_deleted_mapping_blocks(self):
        with (self.proc / '100/maps').open('a') as maps:
            maps.write(f'3000-4000 r-xp 0000 00:01 2 {self.bin / "z-tesla"} (deleted)\n')
        status, report = self.run_probe()
        self.assertNotEqual(status, 0)
        self.assertIn('blocked=z-tesla-deleted-or-ambiguous-map', report)

    def test_stopped_launcher_blocks(self):
        self.stat(50, 1, state='T')
        status, report = self.run_probe()
        self.assertNotEqual(status, 0)
        self.assertIn('blocked=launcher-process-state', report)

    def test_existing_report_is_not_overwritten(self):
        self.report.write_text('previous evidence')
        status, report = self.run_probe()
        self.assertNotEqual(status, 0)
        self.assertEqual(report, 'previous evidence')


if __name__ == '__main__':
    unittest.main()
