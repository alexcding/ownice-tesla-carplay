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

## Three layers

| Layer | Trigger | Action |
|---|---|---|
| Automatic | `z-link` (the receiver) exits 5 times within 120 s while a native install is active | The patched `z-link.sh` runs `/misc/zq-native/restore.sh`; the loop relaunches `z-link` with the stock files |
| Restore package | Receiver runs but misbehaves | `build_patch.py --restore-native --out build/restore_native.zip`, then `tools/flash.sh update build/restore_native.zip` |
| Full stock | Anything else | `tools/flash.sh rollback` reinstalls stock 2.9.11, which overwrites every native file and `z-link.sh` |

## What the installer (`tools/native/update.sh`) guarantees

1. On the first native install it checks every listed file against the stock MD5 from
   `native.list` and copies it to `/misc/zq-native/stock/`. If any file is not stock it
   aborts before changing anything. Later installs keep that original backup.
2. It installs `restore.sh` before any native file changes.
3. Native files are swapped with copy + rename, so the running receiver keeps its old
   mapping instead of reading a half-written library. Any failure from here on runs
   `restore.sh` immediately.
4. Events are appended to `/usr/z-loader/zjinnova/web/zq-native.txt`, readable in a
   browser at `https://www.zbox.link/zq-native.txt` (the box has no shell).

`restore.sh` skips files already identical to the backup and clears
`/misc/zq-native/active` only when every file is stock again.

## Validation status

- Local simulation (scratch copy of the stock tree, macOS `sh`): install, restore,
  reinstall, refusal on a non-stock file, mid-install failure, and the crash watchdog,
  25 checks passing. The box runs BusyBox `ash`; the scripts use POSIX features only,
  but have not yet run under BusyBox.
- Not yet run on a box. First on-device step: `build_patch.py --native-test` ships the
  rollback machinery with receiver files unchanged, then `--restore-native`.
- Limits: the watchdog sees only crashes. A receiver that hangs or that the iPhone
  rejects needs the restore package. A change that broke boot, the kernel or Wi-Fi
  would need FEL recovery; native packages here change none of those.
