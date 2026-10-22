# OpenSSL linkage and ARM optimization

Initial inspection was offline on 2026-10-22. Owner-authorized sidecar uploads and
physical-device testing followed; see the device record below. A corrected
five-minute live trial passed, and the owner-authorized crypto-only update
`2.9.11-oc10-hevc-neon` is now installed on disk. Permanent-path activation awaits
the owner's next power cycle. No reboot has been performed.

## Firmware identity

Base: vendor `update_tesla0817.zip`, MD5
`3a743be56b60df524825cffcf45238cd`; packaged platform `202402031913`, web `2.9.11`.
Only the selected executable and SSL/crypto libraries were extracted for this
inspection. Vendor artifacts remain outside Git.

| Archive path | SHA-256 |
|---|---|
| `usr/z-loader/bin/z-tesla` | `fabf68f52d56e487a591e1229c3e2fa3c9006a9efc91bb4af57c03f1bd501ed8` |
| `usr/z-loader/lib/libcrypto.so.1.1` | `ac6bf48e03481975403563beb3f5ab264311ef92befe514bbcbc3a80c978478a` |
| `usr/z-loader/lib/libssl.so.1.1` | `ac6b13e71f4af36e139c28038dfe9ed3698d6a5268ae11e2e560742438fbcad8` |

## Observations

`llvm-objdump -p z-tesla` lists exactly these `DT_NEEDED` entries:
`libzlink.so`, `libdl.so.2`, `libm.so.6`, `libgcc_s.so.1`,
`libpthread.so.0`, and `libc.so.6`. Neither OpenSSL shared library is listed.
The executable itself defines `AES_encrypt`, `AES_decrypt`,
`AES_cbc_encrypt`, `OPENSSL_cleanup` and SSL-session serialization functions
in `.text`; it contains an `OpenSSL 1.1.1  11 Sep 2018` string.
These are positive evidence of embedded OpenSSL code, rather than a conclusion
based only on missing dependency names. Other components may still load shared
OpenSSL; this is not a complete runtime dependency or TLS call-path trace.

The shared crypto library contains these build-identification strings:

```text
platform: linux-generic32
compiler: arm-linux-gnueabihf-gcc -fPIC -pthread -Wall -O3 -DOPENSSL_USE_NODELETE -DOPENSSL_PIC -DNDEBUG
built on: Tue Mar 28 07:29:11 2023 UTC
```

Its symbol table retains `chacha_enc.c`, `poly1305.c`,
`ChaCha20_ctr32`, `gcm_gmult_4bit` and `gcm_ghash_4bit`.
Disassembly of the complete named `ChaCha20_ctr32` and `gcm_ghash_4bit`
routines shows scalar Thumb instructions and no vector instruction mnemonics.
`AES_encrypt` uses scalar table lookups, with no NEON instructions.
This establishes scalar implementations in the inspected routines; it does not
prove the absence of every assembly optimization throughout either artifact.
Compiled C remains compiler-optimized (`-O3` in the shared library).

Using the shared library's ELF symbol values/sizes and section mappings, unique
64-byte code chunks also locate corresponding routines inside `z-tesla`:

| Shared-library routine | Bytes | Executable file offset | Unique matching 64-byte chunks | Byte differences over the full routine |
|---|---:|---|---:|---:|
| `ChaCha20_ctr32` | 1,278 | `0x11b454` | 19 | **0** |
| `AES_encrypt` | 1,104 | `0x1076a0` | 17 | 3 |
| `gcm_ghash_4bit` | 552 | `0xc9424` | 8 | 3 |
| `Poly1305_Update` | 2,362 | `0xd5768` | 34 | 8 |

The ChaCha20 match proves that this scalar routine is embedded unchanged.
The other matches strongly support the same implementations, but their differing
bytes were not individually explained. File offsets are not virtual addresses;
the executable's `AES_encrypt` virtual address is `0x1176a0`.

Hopper independently disassembled `AES_encrypt` through REA:
`ev_4e261f799721aa2e3f375ba5456877372b0a5128369dca867f8a5f1ea765f4ab`.
The corresponding function-name search is
`ev_722336bdeb21203242cb755610a137e74f28fd301aeea92ffa86625b6bcd6c56`.
These session records were exported locally to
`/private/tmp/ownice-crypto-check/rea-evidence.json` and the native session closed.
ELF metadata, shared-library disassembly and byte comparisons used local Python
and Xcode's `llvm-objdump`; those observations are separate from REA evidence.

## Practical implications and limits

Replacing `libcrypto.so.1.1` or `libssl.so.1.1` would not replace the embedded
code. Ordinary `LD_LIBRARY_PATH` selection cannot substitute these internal
implementations. This does not evaluate advanced symbol interposition, binary
rewriting or every possible dynamically loaded code path.

An optimized OpenSSL build is a possible library-development task, but integration
into the embedded copy requires rebuilding `z-tesla` from source or a separately
researched binary modification. The initial inspection above did not build a
replacement; the follow-up prototype below implements a local binary modification.
The separately staged benchmark packages below do not activate the candidate;
the later live packages
activate it temporarily and restore the original. Device tests measured the primitive's
elapsed and process CPU time: generation 4 used about 49% less CPU time for the
same ChaCha20 work. A GHASH bottleneck and end-to-end streaming improvement have
not been established.

Findings apply to the hashes above, not automatically to another firmware or
board. Native changes need compatible ABI/runtime validation and a tested
recovery path before owner-authorized deployment. The web-only rollback
assumptions do not establish recovery from a broken native service. No keys or
certificates were inspected or redistributed.

## Local ChaCha20 NEON prototype v2 (2026-10-22)

Implemented in `tools/build_crypto_neon.py`, with machine-code validation in
`tools/test_crypto_neon.py`. This is a local experiment, not an adapter release.
Neither tool uploads, reboots, modifies device settings or handles TLS keys.

### Implementation

The builder checks the full executable SHA-256, original 1,278-byte routine
SHA-256, and exact eight entry bytes before patching. Unknown firmware fails
closed. It uses the separately downloaded, SHA-256-pinned upstream
[OpenSSL 1.1.1w ARM ChaCha20 generator](https://github.com/openssl/openssl/blob/OpenSSL_1_1_1w/crypto/chacha/asm/chacha-armv4.pl)
and matching
[ARM header](https://github.com/openssl/openssl/blob/OpenSSL_1_1_1w/crypto/arm_arch.h).
This is a replacement of one primitive, not an upgrade of all embedded OpenSSL.
Upstream licensing applies to that source and generated assembly; obtain the
[upstream license](https://github.com/openssl/openssl/blob/OpenSSL_1_1_1w/LICENSE)
with the source. Do not redistribute the patched vendor executable.

The source generator is unchanged. Two count-checked replacements in its generated
assembly make the capability word local and read-only, with the NEON bit set.
This prototype therefore requires Cortex-A7/NEON support and OS NEON state support;
it does not probe the device at runtime. Clang assembles ARMv7-A instructions with
`-mcpu=cortex-a7 -mfpu=neon-vfpv4 -mfloat-abi=hard -marm`.
Any remaining ELF relocation in the resulting object rejects the build.

The 3,296-byte self-contained payload goes into a new **RX**, not writable,
load segment. The program-header table is relocated into that segment and
`PT_PHDR`, `e_phoff` and `e_phnum` updated. Existing load-segment mappings,
BSS sizes, interpreter, dependencies, entrypoint and stack policy are preserved.
The new segment retains the first load's `p_vaddr - p_offset` bias (`0x10000`),
with file padding beyond the original BSS mapping. This is necessary because
[Linux 5.4 computes `AT_PHDR` from the first load's bias plus `e_phoff`](https://github.com/torvalds/linux/blob/v5.4/fs/binfmt_elf.c),
rather than using the relocated `PT_PHDR` address. The firmware's
`lib/modules/5.4.61/8821cs.ko` has `vermagic=5.4.61 SMP preempt mod_unload ARMv7 p2v8`.
For v2, `e_phoff=0x281000`, so both the kernel-derived address and the actual
mapped table are `0x291000`. Tests independently derive this address and reject
a self-consistent segment/header pair whose bias violates the kernel rule.
The original ChaCha20 entry at `0x12b454` gets an eight-byte Thumb-2
`LDR.W PC,[PC,#0]` and an ARM-state destination literal. Existing callers keep
their original instructions and argument setup, including the fifth stack
argument. The tail transfer preserves the caller's return address.

Payload entry: `0x291160`; NEON function: `0x291560` (dispatch enters after its
standalone prologue). Buffers of at most 192 bytes use upstream's scalar assembly;
larger buffers take its combined NEON/integer path. Poly1305, AES-GCM, TLS cipher
negotiation and authentication policy are unchanged.

REA identified caller routines `0xc9f7c` and `0xca4d0`:
`ev_fca79d1bda7ab8009cb0551c1ee40a2896f3e0c3cda5506f0fd8347216469dbb`.
The first caller's disassembly is
`ev_614616722c9e372f3ad0a26452d41b57ad82f2727fdb1e37019a99fc5973fa85`.
Eight direct `BL` instructions targeting the original entry were decoded and
executed during validation: `0xc9fac`, `0xca000`, `0xca0a2`, `0xca52e`,
`0xca6ec`, `0xca8ce`, `0xca928` and `0xca9dc`.
Testing a call instruction with prepared arguments does not execute its entire
enclosing caller or prove an end-to-end TLS path.

### Reproduce locally

Use a new output directory under ignored `build/`; never add generated vendor
artifacts to Git. Download only the public upstream files into a separate source
directory. The builder verifies their hashes before running the generator.

```sh
python3 -m venv build/crypto-env
build/crypto-env/bin/pip install -r tools/requirements-crypto-neon.txt
mkdir -p build/crypto-source
curl -fL https://raw.githubusercontent.com/openssl/openssl/OpenSSL_1_1_1w/crypto/chacha/asm/chacha-armv4.pl -o build/crypto-source/chacha-armv4.pl
curl -fL https://raw.githubusercontent.com/openssl/openssl/OpenSSL_1_1_1w/crypto/arm_arch.h -o build/crypto-source/arm_arch.h
curl -fL https://raw.githubusercontent.com/openssl/openssl/OpenSSL_1_1_1w/LICENSE -o build/crypto-source/LICENSE
build/crypto-env/bin/python tools/build_crypto_neon.py --firmware /path/to/update_tesla0817.zip --openssl-source build/crypto-source --output build/crypto-neon
```

The equivalent `--binary /path/to/z-tesla` mode accepts only the pinned stock
executable or the exact HEVC variant identified below. Other hashes fail closed.
Validate against the corresponding unmodified executable:

```sh
build/crypto-env/bin/python tools/test_crypto_neon.py --binary /path/to/unmodified/z-tesla --candidate build/crypto-neon
build/crypto-env/bin/python tools/test_crypto_neon_reports.py
```

Prerequisites: Python 3.9+, Perl and Clang with its ARM integrated assembler.
The emulator uses Unicorn's Cortex-A7 model. In this Codex environment, Unicorn
initialization exited with SIGILL inside the sandbox; the same test command
completed successfully outside the sandbox after approval. No target process
or network connection is launched by the tests.

### Validation and artifacts

Local candidate: `build/crypto-neon-v2/z-tesla.neon`.
SHA-256: `5f78c46743dfb667a5d4c8d8b4d8a0d109fbed983aad305214a13b4641c738c2`.
Build metadata and test results are beside it in `manifest.json` and
`test-results.json`. A second build directly from the MD5-checked archive was
byte-identical to the build from the extracted executable.

**Passed:** 684 differential cases and 1,400 ARM machine-code executions, including
32 runs through original caller `BL` instructions. Every result matched both
the original scalar machine code and an independent ChaCha20 implementation,
whose rounds were checked against RFC 8439 section 2.3.2. Cases cover every
length 0–512, larger buffers through 16 KiB, deterministic random inputs,
unaligned input/output, exact in-place operation, page-boundary tails and
low-word counter wrap. Hooks confirm actual vector instruction execution on
the NEON path, bounded data reads/writes, unchanged key/nonce/input, and ARM
and NEON callee-saved registers plus stack restoration. Structural checks cover
the new RX segment, program-header mapping, unchanged original mappings/sections,
the Linux 5.4 `AT_PHDR` file mapping, the exact allowed edits, and rejection of
both an incompatible loader layout and a modified baseline.

Four report-lifecycle regression tests also passed: missing input, malformed
manifest, validation failure and interruption. Every run atomically replaces any
old report with `passed=false, status=running` before reading inputs. Exceptions
produce `status=failed` with the error and timestamps; success is recorded only
after the full suite completes. A crash leaves a non-passing running report.

The earlier v1 candidate is **obsolete and rejected** by the loader regression:
its kernel-derived header address was `0x27d000`, while the actual headers were
mapped at `0x291000`. Its previous passing report has been replaced by a failed
report. The v1 crypto results did not prove application startup compatibility.

At 16 KiB the emulator executed 149,985 instructions for the replacement versus
468,002 for stock. **Instruction counts are not a Cortex-A7 cycle benchmark or
evidence of a threefold streaming speedup.**

**Device-tested later:** Linux kernel/dynamic-loader dependency tracing with the
relocated headers, NEON support/execution, primitive correctness and elapsed time.
The full receiver was not started; see the record below for the exact scope.

**Subsequently validated in generation 4:** startup, TLS on the three player
ports, owner-confirmed CarPlay video/audio/touch, five-minute bounded execution
and restoration of the original receiver. Sustained streaming latency,
long-duration reliability and a whole-stream performance improvement remain
unvalidated. The device benchmark candidate is based on the pinned stock
executable. A separate candidate preserving the current HEVC build passed local
validation and the corrected live trial recorded below.
The existing native rollback machinery was not integrated with either candidate.
Replacement of the active receiver still needs preservation of current HEVC
changes, a recovery review and validation, and explicit owner authorization.

## Owner-authorized device sidecar tests

The owner requested separate-file device testing while retaining the installed
binary, and subsequently authorized corrected uploads. This worktree was first
synced to `origin/main` at `493a699`. The adapter reported platform
`202402031913`, web version **`2.9.11-oc9-hevc`** before and after the uploads.
The tests are specific to this owner's T113 adapter; they do not establish
compatibility with another board or firmware.

### Mechanism and activation boundaries

`tools/build_crypto_sidecar.py` packages a copy of the candidate and the
freestanding `tools/crypto_device_probe.c` harness. Each generation adds a new
`/misc/zq-crypto-v2-sidecar<N>/` directory and one results file under
`/usr/z-loader/zjinnova/web/`. Nothing is installed into the receiver's binary
path, launchers, Wi-Fi configuration or boot scripts. No boot hook is added.
Tests run once from the installer; staged files are inactive on reboot.
The firmware version is preserved; sidecar version identifiers identify the
test package, not a replacement web-version string.

The harness opens the candidate and installed receiver read-only. It maps the
candidate's executable segments privately with `MAP_FIXED_NOREPLACE`. The
original and replacement entry bytes are switched only in the harness process's
private mapping. The whole 1,278-byte stock crypto routine is compared against
the installed receiver file before benchmarking; this does not assert that the
whole candidate matches the installed HEVC build.

The independent reference and both routines pass **529 cases per implementation**,
including the RFC known-answer block, short lengths, in-place/unaligned buffers
and counter wrap. Benchmark samples encrypt 128 buffers of 16 KiB (2 MiB total),
after 16 untimed warm-up buffers. Three samples per implementation alternate
execution order. The test process requests nice level 19; the return from that
priority request is not currently validated. No CPU affinity or frequency lock
is applied.

For loader testing, a separate child executes the candidate with
`LD_TRACE_LOADED_OBJECTS=1`. This validates kernel/loader mapping and dependency
resolution without starting the receiver main function or taking over its ports.
It does not validate normal startup, TLS record processing or CarPlay playback.

### Uploaded generations

| Package | Recorded outcome |
|---|---|
| `crypto-neon-v2-sidecar.1` | Upload accepted. Correctness and elapsed-time tests completed; all protected-file hashes matched. Review subsequently found its syscall-27 timeout was OABI-only and ignored under ARM EABI. Superseded; its successful run does not validate its timeout. |
| `crypto-neon-v2-sidecar.2` | Upload accepted, but no result file appeared. This generation required a `timeout` command before staging; generation 1's report explicitly showed that command was unavailable. Superseded. |
| `crypto-neon-v2-sidecar.3` | Upload accepted; self-contained watchdog, loader trace, correctness, elapsed-time benchmark and preservation checks all completed with exit 0. |
| `crypto-neon-v2-sidecar.4` | Latest device-tested generation. Upload accepted; loader trace, correctness, separate CPU/wall benchmarks and preservation checks all completed with exit 0. |

Generation 3 uses checked ARM EABI `setitimer` (104) for the probe's ten-second
timer. Its separate parent watchdog uses monotonic time, `fork`, `wait4` and
`kill` on its own unreaped child, with five seconds for loader tracing and twelve
seconds for the probe. No unrelated service or process group is signalled.
Local fault-injection tests covered timer failure, no NEON, a different installed
crypto routine, watchdog timeout, child failure and failed `execve`. The ordinary
watchdog path ran successfully on the adapter; timeout fault injection has not
been performed on the physical device.

The installer fails overall when loader execution, benchmarking, protected-file
snapshotting or preservation fails. Five local installer regression tests cover
success, a changed file, failed snapshot, benchmark timeout and loader failure.

### Generation 3 device evidence

Package SHA-256:
`4061a78e691a68107e7db4ab3962b7b5c42641a4f9e9c1e6ee5e66c5f6257f03`.
Probe SHA-256:
`435547837a3d72a802b81289ba66353a0238004885307eeb07129c7aa2559430`.
Candidate SHA-256 remains
`5f78c46743dfb667a5d4c8d8b4d8a0d109fbed983aad305214a13b4641c738c2`.

The report records HWCAP `0x001fb0d6` (NEON advertised), successful installed
crypto comparison, and `loader_trace_exit=0`, `benchmark_exit=0`,
`protected_files_unchanged=1`, `overall_exit=0`, `complete=1`.
All dependencies resolved, including `/usr/z-loader/lib/libzlink.so` and
`/lib/ld-linux-armhf.so.3`.

Before/after hashes matched for the installed `z-tesla`, its launcher,
`webversion`, the vendor updater, `/etc/wlan-softap.sh` and
`/etc/check-hostap.sh`. In particular, installed `z-tesla` MD5 remained
`a099e55592e70cabab0883e892410c14`. Wi-Fi and the web server remained accessible
to retrieve the result. This verifies the sampled file contents, not every
possible runtime state or a hardware recovery procedure.

| Implementation | Sample 1 (ms) | Sample 2 (ms) | Sample 3 (ms) | Median (ms) | Median throughput (MiB/s) |
|---|---:|---:|---:|---:|---:|
| Installed-equivalent scalar ChaCha20 | 82.327 | 108.471 | 59.164 | 82.327 | 24.29 |
| Optimized ChaCha20 | 29.085 | 30.523 | 29.038 | 29.085 | 68.76 |

These are **CLOCK_MONOTONIC elapsed-time measurements**, including time spent
descheduled. Their median ratio is **2.83**, but the wide scalar spread and low
process priority prevent interpreting that as a precise CPU-efficiency gain.
Only the ChaCha20 primitive was timed: Poly1305, TLS record overhead, sockets,
radio traffic, decoding and end-to-end streaming latency were excluded.

Local raw evidence is in
`build/crypto-sidecar-package-v2-guarded/device-results.txt`, with the derived
elapsed-time comparison in `comparison.json` and emulation results in
`local-tests.json`. Generated binaries and archives remain ignored and must not
be committed or redistributed.

### Generation 4 device CPU and wall timing

`crypto-neon-v2-sidecar.4` records both `CLOCK_MONOTONIC` wall time and
`CLOCK_PROCESS_CPUTIME_ID` process CPU time around the same encryption loop.
Its version-2 timing output explicitly separates `stock_wall_ns_*`,
`stock_cpu_ns_*`, `neon_wall_ns_*` and `neon_cpu_ns_*`. A failed CPU-clock read
fails the benchmark instead of substituting wall time. Wall time remains the
watchdog's deadline source.

Regression fixtures deliberately advance wall time three times faster than CPU
time and verify that each output series keeps its own values. They also reject
an unavailable CPU clock without producing a timing comparison. These fixtures
use synthetic clocks and do not measure performance.

The owner-authorized generation 4 upload succeeded. Package SHA-256 is
`1df656448985c20face7210dee8e113c21e1885875453c304968f190b16e8eda`;
probe SHA-256 is
`e760a68c154ef69b11f15b5ec6c12f98e332fd752aa03dad34680bbf53b5773e`.
The candidate remains the stock-based v2 candidate identified above.
The device report records `loader_trace_exit=0`, `benchmark_exit=0`,
`protected_files_unchanged=1`, `overall_exit=0`, `complete=1`, and 529 passing
correctness cases per implementation. All six protected-file hashes matched;
installed receiver MD5 remained `a099e55592e70cabab0883e892410c14`.

Each sample processes 2 MiB. These measurements are from the physical adapter:

| Implementation | CPU samples (ms) | Median CPU (ms) | CPU throughput (MiB/s) | Median wall (ms) |
|---|---|---:|---:|---:|
| Installed-equivalent scalar ChaCha20 | 60.087, 59.296, 58.898 | 59.296 | 33.73 | 60.484 |
| Optimized ChaCha20 | 30.365, 30.627, 29.926 | 30.365 | 65.86 | 31.834 |

The median CPU-time ratio is **1.95×**, or about **49% less CPU time** for this
primitive workload. The wall-time ratio is **1.90×**. These are three samples
without a frequency lock, and exclude Poly1305, TLS, networking and playback.
They do not establish an equivalent improvement in CarPlay performance.
Generation 3 retains its original elapsed-time meaning.

Raw evidence is `build/crypto-sidecar-package-v2-cpu/device-results.txt` and the
derived figures are in `comparison.json` in the same directory.

### Candidate preserving the installed HEVC changes

Rebuilding the HEVC diagnostics/focus-retention variant from main commit
`493a699` produced receiver MD5 `a099e55592e70cabab0883e892410c14`, matching the
installed file recorded by the device probe. Its SHA-256 is
`03475fae7167818e20fd9f44da2ccb7869bd4dc09fc2850a35824efc53652bec`.
This confirms the on-disk file identity, not the generation currently mapped by
a running receiver process.

The crypto builder now also accepts this exact base hash. Applying the verified
NEON payload produces candidate SHA-256
`43e9b44dba7e87940fcdb1b4bd436973e247760c710799a77648f1f7b0d83c52` at
`build/crypto-current-hevc-neon/z-tesla.neon`. Local validation passed 684
differential cases and 1,400 machine-code executions, including original call
sites and loader-layout checks. Existing HEVC segments and hooks are preserved.
Results are in `build/crypto-current-hevc-neon/test-results.json`.

This HEVC candidate was subsequently uploaded and activated in the bounded live
trials below. Runtime identity and a separate restoration rehearsal are recorded
below. The original benchmark
sidecar watchdog only bounds its own test child; the new trial guardian adds
launcher restoration. The corrected generation 4 trial passed functional
TLS/CarPlay checks; it does not establish a streaming performance gain.

There is no active receiver replacement to roll back. The sidecar programs exit,
and their directories/results remain as inactive files. Removing those files is
separate from rollback of a receiver update. No reboot has been performed for
this crypto experiment, and the optimized copy does not autostart.

## Temporary live-test preparation and device recovery rehearsal

`crypto-runtime-preflight.1` and `.2` added reports only, with no service signals
or configuration changes. Generation 1 conservatively failed because this kernel
does not expose the main executable through the corresponding `map_files` link,
and because it encountered anonymous/special executable mappings. Generation 2
uses `/proc/PID/exe` for that main-executable identity only; libraries still
require a hash of the actual mapped file through `/proc/PID/map_files`. Anonymous
mappings are counted, not attested. This checks native file generations; it is
not a verification of every byte of executable process memory.

Generation 2 passed on the device. Receiver PID 1315 had parent launcher PID
1271; running and installed receiver MD5 both matched
`a099e55592e70cabab0883e892410c14`. Running `z-link` and every file-backed library
checked in both processes matched their respective on-disk files. This removes
the observed-generation mismatch concern for this snapshot; it does not prove
that another device or a later installation can be partially restarted safely.
Raw evidence: `build/crypto-runtime-preflight-v2/device-results.txt`.

`tools/crypto_trial_guard.c` is a separate static ARM helper. It pins process
identities with Linux pidfds, checks start ticks and the receiver's parent,
captures the receiver's environment privately, and refuses unsupported kernel
operations before signalling a service. It pauses the existing launcher, stops
its receiver, runs the sidecar for at most 60 seconds, stops/reaps that child,
then resumes the original launcher. It does not rerun the startup script, which
would also start DNS and web-server processes. The child requests SIGKILL on
controller death before exec and rechecks its parent to close the setup race.
A separate guardian monitors the controller and its deadline; if recovery is
needed, it terminates the controller and resumes the pinned original launcher.
Resuming the launcher is not itself proof that playback or its ports recovered.

### Device rehearsal: `crypto-guard-check.1`

The owner-authorized package added only our helper/fixture scripts under
`/misc/zq-crypto-live1` and two web reports. It did not signal any installed
service. The helper's fixture modes require our own executable and exact fixture
launcher script. The device ran these disposable-process cases:

| Case | Controller exit | Launcher respawn observed | Remaining test processes |
|---|---:|---:|---:|
| Normal bounded completion | 0 | yes | 0 |
| Deliberate controller exit during the trial | 90 | yes, via guardian | 0 |
| Deliberately hung controller | 137 (guardian SIGKILL) | yes, via guardian deadline | 0 |

The kernel pidfd/process checks passed. All six protected-file MD5 values matched
before/after, including receiver, launcher and Wi-Fi scripts. Report status was
`overall_exit=0`, `complete=1`; reported firmware stayed `2.9.11-oc9-hevc`.
Package SHA-256:
`63f561885ea29782b70ed141efa2a104d6a1be4fc1f27fcf4de9d6887e7ddf00`.
Rehearsed helper SHA-256:
`1d05774f8d37dbda89ae77edb7518682fb950dffc5c4773e4b460177833dcd4d`.
Raw evidence: `build/crypto-guard-check-v1/device-results.txt`.
Twenty local tests exercised the actual C control flow using synthetic syscalls;
eleven runtime-probe tests covered identity and map failures. These are distinct
from the subsequent real ARM/kernel rehearsal.

### First live trial: uploaded, failed, original restored

`tools/build_crypto_live_trial.py` built `crypto-neon-live.1` at
`build/crypto-neon-live-v1-reviewed/crypto-neon-live.1.zip`, SHA-256
`54a72a72fdc85f9c1751554c4fcb2781ca871778626dcde4ec2fa76fe9c100b6`.
Only this reviewed directory is current; the earlier local draft directory is
superseded. The builder requires the pinned HEVC candidate and its passing
validation report, the exact rehearsed guard binary and a complete unambiguous
device rehearsal report. Four local regression tests reject missing/failed/
duplicate evidence and false-success final statuses.

The package stages the candidate and the previously tested loader-timeout helper
as separate files, traces the current HEVC candidate's dynamic dependencies with
a five-second bound, and repeats runtime identity checks before any receiver
signal. It uses the already rehearsed guard for a 60-second trial. Its final
checks require the original receiver to reappear under its existing launcher
and all protected-file hashes to match. Neither candidate nor helpers autostart.
Installed binary, launch script, Wi-Fi files and web version are preserved.

The owner subsequently replied "ready" and authorized this live interruption.
The upload was accepted. The report records `loader_trace_exit=0`,
`runtime_exit=0`, `launcher_paused=1`, `trial_started=1`, followed by
`trial_exit=1`, `supervisor_exit=1`, `original_receiver_restored=1`,
`protected_files_unchanged=1`, `overall_exit=1`, `complete=1`.
This is a **failed trial**, not evidence of successful optimized playback.
The guard restored the original receiver and all six protected-file hashes
matched. The Wi-Fi update CGI remained reachable with version
`2.9.11-oc9-hevc`. Raw evidence is
`build/crypto-neon-live-v1-reviewed/device-results.txt`.

The report does not distinguish an early receiver exit from other monitoring
failures, and it discards the child's wait status. Its failure therefore does
not establish a crypto crash or explain the reported touch failure. Better exit
diagnostics and a longer reconnect window are required before another trial.
No second live trial has been run.

The owner reported touch was not working, then confirmed it worked after
reconnecting CarPlay and refreshing the player following restoration. This
confirms user-observed recovery on the original receiver. It does not establish
whether touch worked on the optimized copy or that the optimized copy caused
the failure. Successful TLS handshakes were collected during monitoring, but
the active trial was not verified both before and after those handshakes; they
cannot conclusively be attributed to the optimized copy. Post-restoration
handshakes succeeded on all three original player ports with TLS 1.3 and
`TLS_CHACHA20_POLY1305_SHA256`. Results are `tls-during.json` and `tls-after.json`
in the same local directory. The earlier progress statement that the optimized
receiver's three TLS ports were verified was stronger than the evidence supports.

A subsequent live stage must check TLS on 5002/7002/9002, video/audio/touch,
and continued access to the Wi-Fi update path while verifying which process is
active throughout each measurement.
Receiver startup includes existing AP-management code, so preserving Wi-Fi files
alone does not establish uninterrupted runtime Wi-Fi. No reboot is required by
this trial and none has been performed. A power cycle uses the unchanged original
launcher and installed binary; that is a fallback, not an exercised recovery test.

Before activation, read-only OpenSSL client handshakes succeeded on all three
player ports (5002, 7002, 9002), each reporting TLS 1.3 with
`TLS_CHACHA20_POLY1305_SHA256`. Baseline results are saved at
`build/crypto-neon-live-v1-reviewed/tls-before.json`. This confirms the current
receiver's negotiated cipher, not operation of the optimized candidate.

### Trial harness correction and generation 2 preparation

After the failed first live trial, the owner requested work on the failure.
The guard now reports `trial_stop_reason`, elapsed milliseconds, test PID,
sleep/poll interruption counts, whether it stopped the child itself, the child
wait status, and either its exit code or terminating signal. This distinguishes
a natural crash/exit from termination requested during restoration. Guardian
recovery reports its trigger separately.

The old harness treated interrupted `nanosleep` as a fatal trial error. The
updated harness treats Linux `EINTR` as a request to recheck the process and
monotonic deadline, rather than aborting or indefinitely retrying sleep. It also
accepts readable/hung-up pidfds for exited/reaped processes. New fault tests
verify interrupted sleep continues, real sleep/poll failures remain failures,
and receiver exit codes/signals are reported. **Neither issue has been proven
to explain the first live failure or the touch symptom.**

Twenty-five local C fault tests passed. Owner-authorized `crypto-guard-check.2`
then rehearsed the revised ARM binary against disposable processes on the box:
normal completion, controller exit and controller timeout all restored their
dummy launcher, and each left zero test processes. The report records
`overall_exit=0`, `complete=1`, `protected_files_unchanged=1`; no installed service
was signalled by this rehearsal. Raw evidence is
`build/crypto-guard-check-v2/device-results.txt`. Package SHA-256 is
`10f9b12c171e8af5b2ef3298eb0a0a97178fa2f5cd4b38c70d06edf177be493a`;
rehearsed guard SHA-256 is
`2c4572137b2240d6b10c525dcc28a4ccb4642073a7cd09e9055b0c9294e026c6`.

The separate live package was prepared as `crypto-neon-live.2` at
`build/crypto-neon-live-v2/crypto-neon-live.2.zip`, SHA-256
`719d939e0cdc8deb0682ac841475f0f619a8a6e24a26f06651d206f6785e5e69`.
It keeps the same pinned HEVC/NEON candidate, uses the rehearsed generation 2
guard in a new immutable `/misc/zq-crypto-live2` stage, and allows **300 seconds**
for reconnecting and checking playback. Its hard guardian deadline is 320 seconds
from guardian startup. All existing preflight, loader, preservation and original
receiver restoration checks remain required.

An authorized deployment must allow a **420-second upload HTTP timeout**; the
ordinary `flash.sh` upload uses 120 seconds and is unsuitable for this longer
synchronous installer. No boot hook or permanent binary replacement is added.
Before interpreting TLS probes, fetch the live report before and after each
probe and require the same test PID with no stop/restoration record throughout;
otherwise the probe cannot be attributed to the optimized copy. Full optimized
CarPlay operation was still unvalidated at this preparation stage; the next
section records its failed upload and the subsequent corrected live trial.

### Second live trial: SIGPIPE termination and signal-context correction

The owner authorized another attempt. `crypto-neon-live.2` was uploaded with a
420-second client timeout. It passed dependency tracing and runtime preflight,
then its test receiver PID 29298 exited after **2,103 ms**. The report records
`trial_stop_reason=receiver-exited`, `child_stopped_by_guard=0`,
`child_wait_status=13`, `child_signal=13` (SIGPIPE),
`original_receiver_restored=1`, `protected_files_unchanged=1`, `overall_exit=1`.
The user did not have time to reconnect. This is another failed startup trial,
not a five-minute playback test. Raw evidence:
`build/crypto-neon-live-v2/device-results.txt`. No TLS probe was attributed to
this candidate; `tls-monitor.json` was not produced because the monitor found
the trial already complete.

Diagnostic-only `crypto-runtime-preflight.3` then read signal masks alongside
the existing file-generation checks. It passed without restarting services.
The original receiver reported `SigIgn=0000000000001006` (SIGINT, SIGQUIT and
SIGPIPE ignored) and `SigBlk=0000000000000000`; its launcher reported ignored
mask `0000000000000006` and blocked mask `0000000000010000`. These are snapshots
of running processes; the launcher's mask does not establish the receiver's
startup-time settings. Raw evidence is
`build/crypto-runtime-preflight-v3/device-results.txt`.

The trial harness previously copied environment and cwd, but did not reproduce
the receiver's ignored-signal or blocked-signal masks. It now captures both from
the original before stopping it, fails closed on missing/malformed masks, and
applies them in the test child before exec. Caught handlers are not copied:
exec resets those, and the receiver initializes its own handlers. The controller
keeps its own SIGCHLD setup so its test child remains waitable. Parent-death
SIGKILL and restoration deadlines remain in force. Matching the original's
ignored SIGPIPE should prevent this termination path; a successful live run is
still needed to establish that it resolves the observed failure.

Twenty-eight local C fault tests pass, including malformed/missing mask refusal
and failure to restore a child's signal context. Generation 3's device dummy
tests recovered correctly and its children survived deliberately delivered
SIGPIPE before exec, but one multi-write metric interleaved with the child's
output. The live builder rejected that ambiguous report. Metrics now use one
write per line. Generation 4 passed a fresh device rehearsal, including three
explicit pre-exec SIGPIPE-survival records, normal completion, controller crash,
controller deadline and zero leftover dummy processes. All six protected-file
hashes matched. Raw evidence: `build/crypto-guard-check-v4/device-results.txt`.
Package SHA-256:
`276d0a8413986713fb1cafa61b2075f7afcf66df9bebeeee8e8de4da0cc0299a`;
rehearsed helper SHA-256:
`f9a90b56cbb129b0e73d00a33ec11696914289a80ee718b5522adaf11fcc7d30`.

### Corrected live trial: generation 4

Continuing the owner-authorized retry, `crypto-neon-live.4` was built and uploaded
with a 420-second client timeout. Its SHA-256 is
`dd48e2bb71b46b1b1062a76e85d9f61c3bff2d1fee8b093399d6cc05c0ed42eb`.
It uses the same HEVC/NEON candidate SHA-256
`43e9b44dba7e87940fcdb1b4bd436973e247760c710799a77648f1f7b0d83c52`;
only the test launcher/supervisor changed. Generation 3 was never live-deployed:
its ambiguous rehearsal report was rejected before packaging the live candidate.

Generation 4 passed runtime and loader preflight. Test receiver PID 19307
reported `child_sigpipe_ignored=1` and remained active after the previous
two-second failure point. Initial TLS probes found no listener. After the owner
reconnected CarPlay, all three ports (5002/7002/9002) negotiated TLS 1.3 with
`TLS_CHACHA20_POLY1305_SHA256`, repeatedly. Each accepted probe is bracketed by
report fetches showing the same active test PID without a stop/restoration
record. This attributes those handshakes to the optimized process, unlike the
ambiguous first live trial. The update CGI remained reachable.

The owner confirmed touch was working, then confirmed video and audio were also
working. This is functional evidence for this owner's current setup, not a
measured streaming speedup or long-duration reliability test. Raw report, TLS
probe snapshots and `tls-monitor.json` are under `build/crypto-neon-live-v4/`.
The completed report records `trial_stop_reason=deadline-complete`,
`trial_elapsed_ms=299921`, `child_stopped_by_guard=1`, `child_signal=9`,
`trial_exit=0`, `supervisor_exit=0`, `original_receiver_restored=1`,
`protected_files_unchanged=1`, `overall_exit=0`, `complete=1`. SIGKILL at the end
was requested by the guard after the bounded trial's graceful-stop allowance;
it was not a spontaneous crash. All six protected-file hashes matched, and the
reported firmware remained `2.9.11-oc9-hevc`. The upload returned success.

Post-restoration TLS handshakes on all three original player ports succeeded
with the same ChaCha20 TLS 1.3 cipher; results are in `tls-after.json`. The update
CGI remained reachable over Wi-Fi. The optimized process is no longer active,
and installed receiver, startup scripts and version are unchanged. No reboot
was performed and no boot hook was added.

The owner also said an "uncompressed version" seemed snappier. Whether this
meant the original binary or a different video mode was not yet clarified;
record this as an unresolved subjective observation, not a measured regression
or speedup. No matched end-to-end latency comparison was collected. The measured
1.95× primitive CPU result and this successful functional test do not establish
snappier CarPlay playback.

## Permanent crypto-only installation

After the successful functional trial, the owner requested shipment.
`tools/build_crypto_install.py` builds an exact-base installer for this owner's
`2.9.11-oc9-hevc` receiver and a separate exact-HEVC rollback package. It refuses
any other installed receiver/version identity, requires the completed successful
generation 4 live report and attributable TLS probes, and pins the same candidate
SHA-256. This device install used a separate crypto-only update. Subsequent
source integration makes every general `build_patch.py --native-hevc` build
compose NEON automatically; browser-only builds preserve the installed receiver.
See [release instructions](CRYPTO_RELEASES.md).

Six scratch-filesystem tests passed: atomic install with an already-open old
inode retained, exact rollback, unknown-base refusal, corrupt-payload refusal,
automatic rollback after a failed version-file rename, corrupt-backup refusal,
and protection against overwriting an existing backup (some checks share a test).
They verify launcher, updater, Wi-Fi files, HEVC codec flag and existing native
recovery metadata remain unchanged. The rollback backup is verified completely
before live-file changes. Receiver and version swaps use copy/hash/rename.

The owner-authorized package **`2.9.11-oc10-hevc-neon` is installed on disk**.
The upload returned success, and `checkver.cgi` reports that version. Install
report `build/crypto-install-v1/device-results.txt` records:

- `backup=verified-exact-pre-crypto-HEVC`
- `installed_binary_md5=9198bd15fd19b56c9fefbce525638f28`
- `installed_version_md5=58c6de4c0fa8438691c803e17f25c6cd`
- `protected_files_unchanged=1`, `service_restarts=0`, `install_exit=0`, `complete=1`
- `activation=owner-power-cycle-required`

Only `/usr/z-loader/bin/z-tesla` and `/usr/z-loader/webversion` changed. Current
receiver mappings were retained: **the permanent path has not been activated or
checked after a power cycle yet**. No reboot was performed. The next power cycle
will launch the optimized installed binary through the existing startup script.
Its signal settings come from that normal startup context; the tested temporary
launcher's signal-copy logic was not installed in the boot path.

Exact previous HEVC binary/version and a validator are saved at
`/misc/zq-crypto-install1/`. Restore package
`build/crypto-install-v1/rollback-crypto-hevc.zip` restores that exact binary and
`2.9.11-oc9-hevc`, with another owner power cycle required for activation. It
does not invoke the broader stock-native restore or clear HEVC state. Actual
device execution of this crypto-only rollback has not been exercised; backup
verification and local install/rollback simulation passed. The existing stock
native recovery metadata remains intact.

Install archive SHA-256:
`a5c508e92a88155f2e5115329cffc37e153e2dceab791d1ea16d21ef5b5ba1d9`.
Rollback archive SHA-256:
`542c6e23139b1dfb5f73d49cf7cad0c36232255db1da741a00026acf19d2a332`.
Neither archive includes key/certificate files, libraries, player or Wi-Fi files.
The executable/archive/backup are private build/device artifacts and must not
be committed or published as vendor firmware.

Reproduce locally, with the already validated candidate/evidence available:

```sh
python3 -I tools/test_crypto_install.py
python3 -I tools/build_crypto_install.py \
  --candidate build/crypto-current-hevc-neon \
  --live-report build/crypto-neon-live-v4/device-results.txt \
  --tls-report build/crypto-neon-live-v4/tls-monitor.json \
  --output build/crypto-install-rebuild
```

Installation/rollback commands require separate owner authorization. Reported
version identifies installed files, not proof of the currently mapped binary.
Long-duration stability, after-boot operation, and end-to-end performance remain
to be checked; the five-minute functional trial does not replace those checks.
