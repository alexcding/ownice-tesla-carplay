#!/usr/bin/env python3
"""Simulate native package install/upgrade/rollback in a temporary filesystem.

No device queries or deployments. Requires packages already built by build_patch.py.
Only native.list's selected stock files are read from the vendor archive.
"""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_patch import CUSTOM_VERSION

FIRMWARE = None
BUILD = Path("build")
PACKAGE_VERSION = CUSTOM_VERSION


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="zq-native-package-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "root"
        self.misc = self.root / "misc"
        self.root.mkdir(); self.misc.mkdir()
        self.env = dict(os.environ, ZQ_ROOT=str(self.root), ZQ_MISC=str(self.misc))
        self.base = zipfile.ZipFile(FIRMWARE)
        self.addCleanup(self.base.close)
        self.hevc = BUILD / f"update_tesla_hevc_{PACKAGE_VERSION}.zip"
        self.native = BUILD / f"native_test_{PACKAGE_VERSION}.zip"
        with zipfile.ZipFile(self.hevc) as z:
            self.listing = z.read("misc/zq-native/native.list").decode().splitlines()
        for line in self.listing:
            _, path = line.split()
            dest = self.root / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(self.base.read(path))
        (self.root / "usr/z-loader/zjinnova/web").mkdir(parents=True)

    def install(self, package, name="install", success=True):
        dest = Path(self.temp.name) / name
        dest.mkdir()
        with zipfile.ZipFile(package) as z:
            for path in z.namelist():
                self.assertFalse(Path(path).is_absolute() or ".." in Path(path).parts)
                self.assertNotIn(Path(path).suffix, (".key", ".crt"))
            z.extractall(dest)
        result = subprocess.run(["sh", "update.sh"], cwd=dest, env=self.env,
                                capture_output=True, text=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return dest

    def assert_stock(self):
        for line in self.listing:
            _, path = line.split()
            self.assertEqual((self.root / path).read_bytes(), self.base.read(path), path)

    def test_native_only_preserves_installed_player_and_launchers(self):
        package = Path(self.temp.name) / "native-only.zip"
        subprocess.run([sys.executable, "-I", str(Path(__file__).with_name("build_patch.py")),
                        "--firmware", str(FIRMWARE.resolve()), "--native-hevc", "--native-only",
                        "--version", PACKAGE_VERSION, "--out", str(package)],
                       check=True, capture_output=True, text=True)
        paths = ["usr/z-loader/zjinnova/web/worker.js",
                 "usr/z-loader/zjinnova/web/zq-settings.js",
                 "usr/z-loader/zjinnova/web/player.html",
                 "usr/z-loader/zjinnova/web/assets/index.0917c25d.js",
                 "usr/z-loader/bin/z-tesla-sh.sh", "usr/z-loader/zq-openssl.cnf"]
        for path in paths:
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"independent session changes: " + path.encode())
        before = {path: (self.root / path).read_bytes() for path in paths}
        with zipfile.ZipFile(package) as z:
            self.assertFalse(any(path in z.namelist() for path in paths))
            self.assertFalse(any("/web/" in path for path in z.namelist()))
        self.install(package)
        for path, content in before.items():
            self.assertEqual((self.root / path).read_bytes(), content, path)

    def test_fresh_install_and_restore(self):
        self.install(self.hevc)
        backup = self.misc / "zq-native/stock"
        for line in self.listing:
            expected, path = line.split()
            self.assertEqual(hashlib.md5((backup / path).read_bytes()).hexdigest(), expected)
            self.assertNotEqual((self.root / path).read_bytes(), self.base.read(path))
        flag = self.root / "data/local/tmp/zq_hevc"
        flag.parent.mkdir(parents=True); flag.touch()
        codec_status = self.root / "usr/z-loader/zjinnova/web/zq-codec.txt"
        codec_status.write_text("Codec change failed")
        checkpoints = [codec_status.with_name(f"zq-video-{name}.txt")
                       for name in ("request", "phone", "sender", "output")]
        for checkpoint in checkpoints:
            checkpoint.write_text("00000000 00000000 00000000\n")
        self.install(BUILD / f"restore_native_{PACKAGE_VERSION}.zip", "restore")
        self.assert_stock()
        self.assertFalse((self.misc / "zq-native/active").exists())
        self.assertFalse(flag.exists())
        self.assertFalse(codec_status.exists())
        for checkpoint in checkpoints:
            self.assertFalse(checkpoint.exists())
        status = (self.root / "usr/z-loader/zjinnova/web/zq-native.txt").read_text()
        self.assertIn("REBOOT REQUIRED: reboot the adapter before using this native package", status)
        self.assertIn("REBOOT REQUIRED: reboot the adapter to activate all restored native files", status)

    def test_native_test_backup_expands_before_hevc_install(self):
        self.install(self.native, "native-test")
        initial = (self.misc / "zq-native/stock/native.list").read_text().splitlines()
        self.assertEqual(len(initial), 2)
        self.install(self.hevc, "hevc")
        expanded = (self.misc / "zq-native/stock/native.list").read_text().splitlines()
        self.assertEqual(len(expanded), 5)
        self.install(BUILD / f"restore_native_{PACKAGE_VERSION}.zip", "restore")
        self.assert_stock()

    def test_reinstall_preserves_original_backup_and_saved_codec(self):
        self.install(self.hevc, "first")
        flag = self.root / "data/local/tmp/zq_hevc"
        flag.parent.mkdir(parents=True); flag.touch()
        (flag.parent / "local_is_h264").write_text("2")
        self.install(self.hevc, "second")
        self.assertTrue(flag.exists())
        self.assertEqual((flag.parent / "local_is_h264").read_text(), "2")
        self.install(BUILD / f"restore_native_{PACKAGE_VERSION}.zip", "restore")
        self.assert_stock()
        self.assertFalse(flag.exists())

    def test_four_file_backup_expands_to_include_keyframe_library(self):
        # Reproduce the previous generation's native file list without needing
        # a historical vendor-containing package in the repository.
        previous = Path(self.temp.name) / "four-file-native.zip"
        added = "usr/z-loader/lib/libzlink-main.so"
        with zipfile.ZipFile(self.hevc) as source, zipfile.ZipFile(previous, "w") as dest:
            for entry in source.infolist():
                if entry.filename == added: continue
                data = source.read(entry.filename)
                if entry.filename == "misc/zq-native/native.list":
                    data = "".join(line + "\n" for line in data.decode().splitlines()
                                   if line.split()[1] != added).encode()
                dest.writestr(entry, data)
        self.install(previous, "four-file")
        backup = self.misc / "zq-native/stock"
        self.assertEqual(len((backup / "native.list").read_text().splitlines()), 4)
        self.assertEqual((self.root / added).read_bytes(), self.base.read(added))
        self.install(self.hevc, "five-file")
        self.assertEqual(len((backup / "native.list").read_text().splitlines()), 5)
        self.assertEqual((backup / added).read_bytes(), self.base.read(added))
        self.install(BUILD / f"restore_native_{PACKAGE_VERSION}.zip", "restore")
        self.assert_stock()

    def test_nonstock_new_file_aborts_upgrade_before_replacements(self):
        self.install(self.native, "native-test")
        airplay = self.root / "usr/z-loader/lib/libAirPlay.so"
        airplay.write_bytes(b"different receiver")
        before = {line.split()[1]: (self.root / line.split()[1]).read_bytes()
                  for line in self.listing}
        self.install(self.hevc, "hevc", success=False)
        for path, content in before.items(): self.assertEqual((self.root / path).read_bytes(), content)
        self.assertEqual(len((self.misc / "zq-native/stock/native.list").read_text().splitlines()), 2)

    def test_corrupted_existing_backup_aborts(self):
        self.install(self.native, "native-test")
        backup = self.misc / "zq-native/stock/usr/z-loader/lib/libScreenStream.so"
        backup.write_bytes(b"corrupt")
        self.install(self.hevc, "hevc", success=False)
        for line in self.listing:
            _, path = line.split()
            if path.endswith("z-link.sh"): continue  # installed by native-test
            self.assertEqual((self.root / path).read_bytes(), self.base.read(path))

    def assert_restore_refuses_without_changes(self, name):
        before = {line.split()[1]: (self.root / line.split()[1]).read_bytes()
                  for line in self.listing}
        version = self.root / "usr/z-loader/webversion"
        old_version = version.read_bytes()
        flag = self.root / "data/local/tmp/zq_hevc"
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("keep codec flag")
        checkpoint = self.root / "usr/z-loader/zjinnova/web/zq-video-request.txt"
        checkpoint.write_text("keep diagnostic")
        self.install(BUILD / f"restore_native_{PACKAGE_VERSION}.zip", name, success=False)
        for path, content in before.items():
            self.assertEqual((self.root / path).read_bytes(), content, path)
        self.assertEqual(version.read_bytes(), old_version)
        self.assertTrue((self.misc / "zq-native/active").exists())
        self.assertEqual(flag.read_text(), "keep codec flag")
        self.assertEqual(checkpoint.read_text(), "keep diagnostic")
        status = (checkpoint.parent / "zq-native.txt").read_text()
        self.assertNotIn("stock native files restored", status)
        self.assertIn("restore aborted", status)

    def test_restore_rejects_missing_empty_and_malformed_manifests(self):
        self.install(self.hevc)
        manifest = self.misc / "zq-native/stock/native.list"
        for name, contents in [
            ("missing", None), ("empty", ""), ("blank", "\n \t\n"),
            ("bad-hash", "not-an-md5 usr/z-loader/lib/libAirPlay.so\n"),
            ("extra-field", self.listing[0] + " unexpected\n"),
            ("unsafe-path", "0" * 32 + " usr/z-loader/../escape\n"),
        ]:
            with self.subTest(name=name):
                if contents is None:
                    manifest.unlink()
                else:
                    manifest.write_text(contents)
                self.assert_restore_refuses_without_changes("restore-" + name)

    def test_restore_checks_all_backups_before_any_replacement(self):
        self.install(self.hevc)
        # A failure at the LAST entry must leave the earlier live files intact.
        for index in [0, -1]:
            path = self.listing[index].split()[1]
            backup = self.misc / "zq-native/stock" / path
            original = backup.read_bytes()
            for failure in ["corrupt", "missing"]:
                with self.subTest(index=index, failure=failure):
                    if failure == "corrupt":
                        backup.write_bytes(b"corrupted backup")
                    else:
                        backup.unlink()
                    try:
                        self.assert_restore_refuses_without_changes(f"restore-{index}-{failure}")
                    finally:
                        backup.write_bytes(original)

    def test_restore_package_uses_its_validator_with_older_installed_helper(self):
        self.install(self.hevc)
        # Previously the small restore ZIP invoked the installed helper, so a
        # new ZIP could silently retain the old unchecked restoration behavior.
        (self.misc / "zq-native/restore.sh").write_text("#!/bin/sh\nexit 0\n")
        manifest = self.misc / "zq-native/stock/native.list"
        contents = manifest.read_bytes()
        manifest.unlink()
        self.assert_restore_refuses_without_changes("restore-old-helper-invalid")
        manifest.write_bytes(contents)
        self.install(BUILD / f"restore_native_{PACKAGE_VERSION}.zip", "restore-old-helper-valid")
        self.assert_stock()
        self.assertFalse((self.misc / "zq-native/active").exists())

    def test_restore_accepts_valid_manifest_without_final_newline(self):
        self.install(self.hevc)
        manifest = self.misc / "zq-native/stock/native.list"
        manifest.write_text(manifest.read_text().rstrip("\n"))
        self.install(BUILD / f"restore_native_{PACKAGE_VERSION}.zip", "restore-no-newline")
        self.assert_stock()

    def test_restore_rejects_truncated_and_duplicate_manifest_entries(self):
        self.install(self.hevc)
        manifest = self.misc / "zq-native/stock/native.list"
        cases = [(f"omitted-{index}", self.listing[:index] + self.listing[index + 1:])
                 for index in range(len(self.listing))]
        cases.append(("duplicate", self.listing[:-1] + [self.listing[0]]))
        for name, lines in cases:
            with self.subTest(name=name):
                manifest.write_text("\n".join(lines) + "\n")
                self.assert_restore_refuses_without_changes("restore-" + name)

    def test_restore_detects_file_omitted_from_backup_and_manifest(self):
        self.install(self.hevc)
        manifest = self.misc / "zq-native/stock/native.list"
        for index in [0, -1]:
            path = self.listing[index].split()[1]
            backup = self.misc / "zq-native/stock" / path
            original = backup.read_bytes()
            with self.subTest(path=path):
                manifest.write_text("".join(line + "\n" for line in self.listing
                                            if line.split()[1] != path))
                backup.unlink()
                try:
                    self.assert_restore_refuses_without_changes(f"restore-missing-both-{index}")
                finally:
                    backup.write_bytes(original)

    def test_restore_inventory_catches_matching_truncated_manifests(self):
        self.install(self.hevc)
        # Neither list alone reveals this missing row; its saved file does.
        truncated = "\n".join(self.listing[:-1]) + "\n"
        for path in ["zq-native/native.list", "zq-native/stock/native.list"]:
            (self.misc / path).write_text(truncated)
        self.assert_restore_refuses_without_changes("restore-both-manifests-truncated")

    def test_restore_rejects_missing_or_invalid_install_metadata(self):
        self.install(self.hevc)
        metadata = self.misc / "zq-native/native.list"
        for name, contents in [("missing", None), ("empty", ""),
                               ("checksum", "0" * 32 + " " + self.listing[0].split()[1] + "\n")]:
            with self.subTest(name=name):
                if contents is None:
                    metadata.unlink()
                else:
                    metadata.write_text(contents)
                self.assert_restore_refuses_without_changes("restore-metadata-" + name)

    def test_restore_covers_backup_union_after_installing_smaller_generation(self):
        self.install(self.hevc)
        self.install(self.native, "smaller-generation")
        metadata = self.misc / "zq-native/native.list"
        self.assertEqual(len(metadata.read_text().splitlines()), 2)
        self.install(BUILD / f"restore_native_{PACKAGE_VERSION}.zip", "restore-union")
        self.assert_stock()

    def test_restore_uses_staged_metadata_before_first_install_finishes(self):
        self.install(self.hevc)
        (self.misc / "zq-native/native.list").unlink()
        staged = Path(self.temp.name) / "staged-generation"
        with zipfile.ZipFile(self.hevc) as z:
            z.extractall(staged)
        result = subprocess.run(["sh", "misc/zq-native/restore.sh"], cwd=staged,
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_stock()

    def test_package_limits_and_compressed_video_setting(self):
        with zipfile.ZipFile(self.hevc) as z:
            names = set(z.namelist())
            self.assertEqual({p for p in names if p.endswith(".so")}, {
                "usr/z-loader/lib/libAirPlay.so", "usr/z-loader/lib/libScreenStream.so",
                "usr/z-loader/lib/libzlink-main.so"})
            self.assertEqual(z.read("usr/z-loader/webversion"), (PACKAGE_VERSION + "\n").encode())
            settings = z.read("usr/z-loader/zjinnova/web/zq-settings.js")
            self.assertIn(b"const NATIVE_HEVC = true;", settings)
            self.assertNotIn(b"const NATIVE_HEVC = false;", settings)
            self.assertIn(b'"HEVC (retry)" : "HEVC (HD)"], [1, "H.264 (HD)"], [0, "JPEG (compatible)"]]', settings)
            self.assertIn(b'changes take effect after you reconnect your phone', settings)
            self.assertIn(b'Reloading this page alone keeps the current codec', settings)
            self.assertIn(b'For JPEG, disconnect the phone before saving, then reconnect', settings)
            from build_patch import BUNDLE_PATH
            self.assertIn(b'var Mf=__zq.mode|0;', z.read(BUNDLE_PATH))
            import json
            manifests = json.loads(z.read("misc/zq-native/hevc-build.json"))
            tesla = next(item for item in manifests if item["file"] == "z-tesla")
            self.assertIn("zq_mode_exit", [p["handler"] for p in tesla["patches"]])
            self.assertIn("zq_mode_runtime_result", [p["handler"] for p in tesla["patches"]])
            self.assertIn("zq_mode_init_result", [p["handler"] for p in tesla["patches"]])
            receiver = next(item for item in manifests if item["file"] == "libzlink-main.so")
            self.assertIn("zq_focus_exit", [p["handler"] for p in receiver["patches"]])
            # Native generations activate together on an explicit owner reboot.
            # Do not regress to restarting only z-link while z-tesla stays old.
            self.assertNotIn(b"killall z-link", z.read("update.sh"))
            self.assertNotIn(b"killall z-link", z.read("misc/zq-native/restore.sh"))
            self.assertIn(b'touch: "raw"', settings)
            self.assertIn(b"Ciphersuites = TLS_CHACHA20_POLY1305_SHA256:",
                          z.read("usr/z-loader/zq-openssl.cnf"))
            self.assertIn(b"OPENSSL_CONF=$LOADER_PATH/zq-openssl.cnf",
                          z.read("usr/z-loader/bin/z-tesla-sh.sh"))
            self.assertIn(b"function checkFrameNum", z.read("usr/z-loader/zjinnova/web/worker.js"))
            self.assertEqual(len(self.listing), 5)
            for line in self.listing:
                checksum, path = line.split()
                self.assertEqual(hashlib.md5(self.base.read(path)).hexdigest(), checksum)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, default=BUILD)
    parser.add_argument("--package-version", default=CUSTOM_VERSION)
    args, rest = parser.parse_known_args()
    FIRMWARE, BUILD, PACKAGE_VERSION = args.firmware, args.build_dir, args.package_version
    unittest.main(argv=[__file__, *rest])
