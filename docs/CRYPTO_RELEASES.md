# Keeping ChaCha20 NEON in future releases

The owner's installed crypto-only package is `2.9.11-oc10-hevc-neon`. The tested
optimized receiver supports CarPlay video, audio and touch. Its ChaCha20 primitive
used about 49% less CPU time (1.95× throughput) in the device benchmark. A matched
end-to-end latency improvement has not been established. Full evidence and
exact firmware/binary hashes are in [CRYPTO_RESEARCH.md](CRYPTO_RESEARCH.md).

## Release rule

Every `build_patch.py --native-hevc` build automatically applies NEON **after**
HEVC, including `--native-only`. Browser/Wi-Fi-only packages do not replace
`z-tesla`, so they preserve the installed optimization. `--native-test` remains a
diagnostic package and `--restore-native` deliberately restores stock crypto.
There is no silent scalar fallback for a failed HEVC/NEON composition.

Versions are plain `2.9.11-ocN` (now `2.9.11-oc15`) for every package type; build
options are recorded in the package metadata. Increment `oc` for subsequent releases.
Do not reuse a stock version. A source/version change does not update the device.

## Build

Use the supported stock 2.9.11 archive, Python 3.9+, Perl and the ARM-capable
Clang/LLD toolchain described in [the native build guide](CARPLAY_HEVC_NATIVE_PATH.md).
Current derived receiver hashes were generated with LLVM/LLD 22. A different
compiler or native source revision may require reviewed hash updates; an
unexpected output must fail rather than bypass checks.

```sh
python3 -m venv build/crypto-env
build/crypto-env/bin/pip install -r tools/requirements-crypto-neon.txt
build/crypto-env/bin/python -I tools/build_patch.py \
  --firmware build/stock_2.9.11.zip \
  --native-hevc --native-video-diag --keep-compressed-video-focus \
  --version 2.9.11-oc15 \
  --out build/update_tesla_hevc_2.9.11-oc15.zip
```

Missing public sources/license are fetched from the pinned
[OpenSSL 1.1.1w tag](https://github.com/openssl/openssl/tree/OpenSSL_1_1_1w)
and verified before caching under ignored `build/crypto-source`. For offline
builds, supply `--crypto-source <directory>` containing `chacha-armv4.pl`,
`arm_arch.h` and `LICENSE`, all matching the hashes in `build_crypto_neon.py`.
Cached corruption is an error; it is not silently replaced or accepted.

## Validation and corruption checks

The builder verifies the stock archive and native input hashes, generated HEVC
receiver hash, original ChaCha20 entry/routine, pinned upstream source/license
and exact compiled payload. After patching it verifies all unrelated original
bytes and HEVC load mappings remain intact, the new code is RX, and Linux 5.4's
program-header mapping is valid. It then rereads every ZIP member and checks its
bytes and Unix permissions before reporting a completed build. A failed archive
check removes the output. These detect construction errors; live behavior still
requires functional validation.

```sh
build/crypto-env/bin/python -I tools/test_crypto_release.py \
  --firmware build/stock_2.9.11.zip
build/crypto-env/bin/python -I tools/test_crypto_install.py
build/crypto-env/bin/python -I tools/test_crypto_neon_reports.py
```

The release suite builds all four diagnostics/focus combinations and verifies
HEVC preservation, NEON inclusion, metadata, license and deliberate corruption
refusal. Its diagnostics+focus output must equal the live-tested receiver SHA-256
`43e9b44dba7e87940fcdb1b4bd436973e247760c710799a77648f1f7b0d83c52`.
It also verifies browser-only builds leave the receiver untouched.

For a changed native implementation, first validate its HEVC behavior and pin the
reviewed derived hash in `SUPPORTED_BASES`; do not remove NEON or weaken hashes
to make a release build pass. Run `test_crypto_neon.py` against the corresponding
unoptimized HEVC baseline and candidate (684 differential cases / 1,400 emulated
executions in the recorded run). Re-run the native package install/restore suite
with freshly built HEVC, native-test and restore packages:

```sh
build/crypto-env/bin/python -I tools/test_native_packages.py \
  --firmware build/stock_2.9.11.zip --build-dir <validation-build-directory> \
  --package-version <matching-version>
```

Those fixtures must be named `update_tesla_hevc_<version>.zip`,
`native_test_<version>.zip` and `restore_native_<version>.zip`, all built with the
same explicit `--version`. Device uploads/restarts/reboots require owner approval.
The general native installer swaps files with copy/rename and does not activate
half of a native generation; full activation requires an owner power cycle.

## Metadata and recovery

Native HEVC packages include `misc/zq-native/crypto-build.json`, final receiver
hashes in `hevc-build.json`, the upstream license and stock-backed native recovery
metadata. `--native-only` retains these while preserving player/TLS/Wi-Fi files.
See [NATIVE_ROLLBACK.md](NATIVE_ROLLBACK.md) for the full stock-native rollback.

The separate `build_crypto_install.py` is an **exact-base, owner-device upgrade**
from `2.9.11-oc9-hevc` to `2.9.11-oc10-hevc-neon`; it is not the general future
release path. Its device backup at `/misc/zq-crypto-install1` and generated
`rollback-crypto-hevc.zip` restore the exact pre-crypto HEVC executable/version.
The rollback was simulated locally and the device backup verified, but actual
device execution of that rollback has not been exercised.

The temporary trial harness preserves the original process's ignored and blocked
signals. This fixed the observed SIGPIPE termination during trial startup. That
helper is not added to normal boot scripts. Permanent-path startup after a power
cycle has not been independently recorded yet; do not equate the version marker
with proof of the currently running binary.

Never commit or publicly attach the vendor archive, generated firmware packages,
extracted executables, keys or certificates. Only our source, tests and docs go
to Git. Building/pushing source does not authorize a device deployment.
