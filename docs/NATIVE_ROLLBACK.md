# Native package rollback

Packages that replace receiver files (`libAirPlay.so`, `libScreenStream.so`, …) carry
their own way back. Web-only packages keep the vendor installer and are unaffected.

## How the box installs a package

`/usr/z-loader/update.sh` (stock, not replaced) unzips the upload to `/misc/update-file`
and runs the package's `update.sh` there as root. `/misc` is its own partition
(`/dev/mmcblk0p8`), which updates do not overwrite. Uploads are served by `z-connman`, a
statically linked program that does not load any receiver library, so a broken receiver
does not block the next upload. The box's Wi-Fi AP is started at boot by
`etc/wlan-softap.sh`, independently of the receiver.

Native install and restore now log **REBOOT REQUIRED**. Reboot explicitly after
either operation, before relying on the installed generation: `z-tesla` and
`z-link` have independent supervisors, and a running shell does not reload a
changed `z-link.sh`. Packages do not initiate a reboot or partial live restart.
The crash watchdog can put stock files back on disk, but full activation still
requires that reboot.

## Three layers

| Layer | Trigger | Action |
|---|---|---|
| Automatic | `z-link` (the receiver) exits 5 times within 120 s while a native install is active | The patched `z-link.sh` runs `/misc/zq-native/restore.sh`; the loop relaunches `z-link` with the stock files |
| Restore package | Receiver runs but misbehaves | `build_patch.py --restore-native --out build/restore_native.zip`, then `tools/flash.sh update build/restore_native.zip` |
| Full stock | Anything else | `tools/flash.sh rollback` reinstalls stock 2.9.11, which overwrites every native file and `z-link.sh` |

## What the installer (`tools/native/update.sh`) guarantees

1. On the first native install it checks every listed file against the stock MD5 from
   `native.list` and copies it to `/misc/zq-native/stock/`. If any file is not stock it
   aborts before changing anything. Later installs keep that original backup and
   verify/extend it before touching additional native files (for example, moving
   from `--native-test` to `--native-hevc`). A missing `md5sum` aborts the install.
2. It installs `restore.sh` before any native file changes.
3. Native files are swapped with copy + rename, so the running receiver keeps its old
   mapping instead of reading a half-written library. Any failure from here on runs
   `restore.sh` immediately.
4. Events are appended to `/usr/z-loader/zjinnova/web/zq-native.txt`, readable in a
   browser at `https://www.zbox.link/zq-native.txt` (the box has no shell).

`restore.sh` skips files already identical to the backup and clears
`/misc/zq-native/active` only when every file is stock again. Since 3.0-oc7,
successful restore also removes `/data/local/tmp/zq_hevc` (the codec-selection flag)
and the codec diagnostic `/zq-codec.txt`. Current restore also clears the four
`/zq-video-{request,phone,sender,output}.txt` delivery checkpoints.

## Validation status

- Local simulation (scratch copy of the stock tree, macOS `sh`): install, restore,
  reinstall, refusal on a non-stock file, mid-install failure, and the crash watchdog,
  25 checks passing. The box runs BusyBox `ash`; the scripts use POSIX features only,
  but that local simulation did not run under BusyBox. Installation has since
  run on the owner's adapter; live restore/watchdog checks remain pending.
- Native HEVC installation and stock-backup creation have run on the owner's box.
  Live restore and crash-watchdog recovery have not been demonstrated. A separate
  `--native-test` / `--restore-native` device exercise remains a useful recovery check.
- Eight reproducible package tests (`tools/test_native_packages.py`)
  cover HEVC install/restore, backup expansion, changed native files, corrupted
  backups, reinstall and package contents, including removal of the codec flag. The restore package now propagates restore errors.
- Limits: the watchdog sees only crashes. A receiver that hangs or that the iPhone
  rejects needs the restore package. A change that broke boot, the kernel or Wi-Fi
  would need FEL recovery; native packages here change none of those.

Since 3.0-oc8, the HEVC package also backs up and replaces `libzlink-main.so`
for reconnect keyframe requests. Backup expansion and native restore cover
all five affected native files. See [HEVC_RECONNECT_FIX.md](HEVC_RECONNECT_FIX.md).
