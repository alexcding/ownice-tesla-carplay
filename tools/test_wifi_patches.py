#!/usr/bin/env python3
"""Run the --wifi-ac patched soft-AP scripts against mocked commands.

    python3 -I tools/test_wifi_patches.py --firmware path/to/update_tesla0817.zip

Each scenario runs the patched etc/wlan-softap.sh (or etc/check-hostap.sh) under
/bin/sh in a temp root: absolute paths are rewritten into it and insmod,
ifconfig, hostapd, hostapd_cli, ps, killall, sed and sleep are stubs. The
hostapd stub keeps state in files (process, enabled, channel, runtime VHT
center) and, like hostapd 2.7, refuses to enable when the VHT center does not
fit the channel. z-tesla's runtime switch (disable, set channel, enable) is
replayed through the same stub. This checks the script logic only; the box's
busybox ash, driver and hostapd are not exercised.
"""

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_patch  # noqa: E402
import player_patches  # noqa: E402
import wifi_patches  # noqa: E402

PREFIXES = ["/dev/", "/usr/z-loader/", "/data/local/tmp/", "/etc/", "/sys/bus/", "/proc/net/",
            "/lib/modules/", "/var/run/", "/tmp/", "/proc/uptime"]

# State files in the temp root: running, enabled, chan, vht (runtime VHT center), sta.
# MOCK_VHT=reject: hostapd exits on a config with VHT. MOCK_ENABLE_VHT=reject:
# enable fails while VHT is on (a country that does not allow 80 MHz).
HOSTAPD = r'''
echo "hostapd $*" >> "$R/log"
case "$1" in -v|-h) echo "hostapd v2.7"; exit 1 ;; esac  # like hostapd: print and exit, no AP change
if [ "$MOCK_VHT" = reject ] && grep -q "^ieee80211ac=" "$2"; then exit 1; fi
grep "^channel=" "$2" | cut -d= -f2 > "$R/chan"
c=$(grep "^vht_oper_centr_freq_seg0_idx=" "$2" | cut -d= -f2)
if [ -n "$c" ]; then echo "$c" > "$R/vht"; else rm -f "$R/vht"; fi
touch "$R/running" "$R/enabled"
'''
HOSTAPD_CLI = r'''
echo "hostapd_cli $*" >> "$R/log"
[ "$1" = -i ] && shift 2
fits() {
  v=$(cat "$R/vht" 2>/dev/null) || return 0  # read once: the watcher may delete it concurrently
  [ "$MOCK_ENABLE_VHT" = reject ] && return 1
  case "$v:$(cat "$R/chan")" in
    155:149|155:153|155:157|155:161|42:36|42:40|42:44|42:48) return 0 ;;
  esac
  return 1
}
case "$1" in
  status)
    [ -f "$R/running" ] || exit 1
    cc="$R/proc/net/rtl8821cs/wlan0/change_bss_chbw"
    # cc_hard: a 40 MHz request leaves the AP down even after the driver revert, until "enable"
    [ -f "$R/cc_hard" ] && grep -q " 1 " "$cc" 2>/dev/null && touch "$R/stuck"
    if [ -f "$R/enabled" ] && [ ! -f "$R/stuck" ] && ! { [ -f "$R/cc_breaks" ] && grep -q " 1 " "$cc" 2>/dev/null; }; then
      echo "state=ENABLED"; else echo "state=DISABLED"; fi
    echo "secondary_channel=$(cat "$R/sec" 2>/dev/null || echo 0)"
    echo "channel=$(cat "$R/chan")"
    echo "bssid[0]=aa:bb:cc:dd:ee:01"; echo "ssid[0]=T3-TEST" ;;
  all_sta) [ -f "$R/sta" ] && echo "aa:bb:cc:dd:ee:ff" && echo "flags=[AUTH][ASSOC]" ;;
  disable) rm -f "$R/enabled" ;;
  enable) rm -f "$R/stuck"; if fits; then touch "$R/enabled"; else echo FAIL; exit 1; fi ;;
  set)
    case "$2" in
      channel) echo "$3" > "$R/chan" ;;
      ieee80211ac|vht_oper_chwidth) [ "$3" = 0 ] && rm -f "$R/vht" ;;
    esac ;;
esac
true
'''
STUBS = {
    "insmod": 'd="$R/proc/net/rtl8821cs/wlan0"; mkdir -p "$d" && echo CA > "$d/country_code" && echo "bw=1" > "$d/rf_info"'
              ' && : > "$d/change_bss_chbw"',
    "dmesg": 'echo "RTW: rtw_ap_chbw_decision ch=44 bw=0"; echo "unrelated line"; echo "RTW: scan(wlan1)";'
             ' echo "RTW: rtw_select_and_join_from_scanned_queue: candidate: Owner (Phone)(aa:bb:cc:dd:ee:03, ch:6)";'
             ' echo "RTW: cfg80211_rtw_change_station(wlan0) mac:aa:bb:cc:dd:ee:66"',
    "ps": '[ -f "$R/running" ] && echo "123 root 0:00 hostapd -B /dev/hostapd.conf"; true',
    "ifconfig": '[ $# -eq 1 ] && echo "wlan0 Link encap:Ethernet" && echo "inet addr:3.3.3.3  Bcast:3.3.3.255"; true',
    "hostapd": HOSTAPD,
    "hostapd-airplay": HOSTAPD,
    "hostapd_cli": HOSTAPD_CLI,
    "iw": 'case "$1 $2" in "dev wlan0") echo "ssid T3-TEST"; echo "addr aa:bb:cc:dd:ee:02";'
          ' echo "channel 44 (5220 MHz), width: 20 MHz";; "reg get") echo "country CA: DFS-FCC";;'
          ' *) echo "* 5220 MHz [44] (20.0 dBm)";; esac',
    "killall": 'echo "killall $*" >> "$R/log"; rm -f "$R/running" "$R/enabled"',
    "udhcpd": "true",
    "sync": "true",
    # BSD sed needs an explicit (empty) -i suffix; busybox sed does not.
    "sed": 'if [ "$1" = -i ]; then shift; exec /usr/bin/sed -i "" "$@"; fi; exec /usr/bin/sed "$@"',
    "sleep": "/bin/sleep 0.02",
}


def make_root(script, hostapd_conf, mode, channel, fails=None):
    root = Path(tempfile.mkdtemp(prefix="wifi-ac-"))
    text = script
    for p in PREFIXES:
        text = text.replace(p, f"{root}{p}")
    for d in ["dev", "usr/z-loader", "data/local/tmp", "etc", "sys/bus/sdio/devices/mmc1:0001:1", "var/run", "bin",
              "tmp", "proc"]:
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "sys/bus/sdio/devices/mmc1:0001:1/device").write_text("0x8179\n")
    (root / "proc/uptime").write_text("61.00 50.00\n")
    (root / "etc/hostapd.conf").write_text(hostapd_conf)
    (root / "etc/model").write_text("MODEL=T3\nSERIAL=TEST\n")
    z = root / "usr/z-loader"
    (z / "wifi-channel").write_text(f"{channel}\n")
    (z / "wifi-channel-mode").write_text(f"{mode}\n")
    (z / "country").write_text("CA\n")
    (z / "first_boot").write_text("1\n")
    if fails is not None:
        (z / "zq-vht-fails").write_text(f"{fails}\n")
    (root / "data/local/tmp/local_apple").write_text("0\n")
    (root / "log").write_text("")
    for name, body in STUBS.items():
        stub = root / "bin" / name
        stub.write_text(f"#!/bin/sh\n{body}\n")
        stub.chmod(0o755)
    (root / "script.sh").write_text(text)
    return root


def env_for(root, **mock):
    return {**os.environ, "R": str(root), "PATH": f"{root}/bin:/usr/bin:/bin",
            "MOCK_VHT": mock.get("vht", "ok"), "MOCK_ENABLE_VHT": mock.get("enable_vht", "ok")}


def vht_lines(root):
    conf = (root / "dev/hostapd.conf").read_text()
    return [l for l in conf.splitlines() if l.startswith(("ieee80211ac=", "vht_"))]


def fails_file(root):
    p = root / "usr/z-loader/zq-vht-fails"
    return p.read_text().strip() if p.exists() else None


def wait_for(cond, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


def run_softap(scripts, hostapd_conf, mode, channel, fails=None, **mock):
    root = make_root(scripts[wifi_patches.SOFTAP_PATH], hostapd_conf, mode, channel, fails)
    # The background watcher outlives the script (stopped by stop_watchers), so
    # output goes to a file: a pipe would stay open until the watcher exits.
    with open(root / "out", "w") as out:
        rc = subprocess.run(["/bin/sh", root / "script.sh"], env=env_for(root, **mock), cwd=root,
                            stdout=out, stderr=subprocess.STDOUT, timeout=20).returncode
    return root, rc


def cli(root, *args, **mock):
    """Run the hostapd_cli stub the way z-tesla does."""
    subprocess.run([root / "bin/hostapd_cli", *args], env=env_for(root, **mock),
                   stdout=subprocess.DEVNULL, check=False)


def stop_watchers(root):
    subprocess.run(["pkill", "-f", str(root / "script.sh")], check=False)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--firmware", help="local copy of the official update zip")
    args = ap.parse_args()
    base = build_patch.fetch_base(args.firmware)
    stock = {path: base.read(path).decode() for path in wifi_patches.AC_PATCHES}
    conf = base.read("etc/hostapd.conf").decode()
    def build(**opts):
        return {path: player_patches.apply(stock[path], patches)
                for path, patches in wifi_patches.patches(**opts).items()}
    scripts = build(ac=True)
    for opts in [{"ac": True}, {"diag": True}, {"ac": True, "diag": True}]:
        for text in build(**opts).values():  # the box runs busybox sh; at least parse them here
            subprocess.run(["/bin/sh", "-n", "/dev/stdin"], input=text, text=True, check=True)

    failures = []

    def check(name, cond, detail=""):
        print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f"  ({detail})" if detail and not cond else ""))
        if not cond:
            failures.append(name)

    def softap(mode, channel, fails=None, **mock):
        return run_softap(scripts, conf, mode, channel, fails, **mock)

    for channel, center in [(161, 155), (149, 155), (36, 42), (48, 42)]:
        root, rc = softap(channel, channel)
        lines = vht_lines(root)
        text = (root / "dev/hostapd.conf").read_text()
        check(f"fixed {channel}: VHT80 center {center}",
              lines == ["ieee80211ac=1", "vht_oper_chwidth=1", f"vht_oper_centr_freq_seg0_idx={center}"],
              f"{lines} rc={rc} {(root / 'out').read_text().strip()}")
        check(f"fixed {channel}: stock config kept", f"channel={channel}\n" in text and "ssid=T3-TEST\n" in text, text)
        check(f"fixed {channel}: hostapd started once, AP enabled",
              (root / "log").read_text().count("hostapd -B") == 1 and (root / "enabled").exists())
        check(f"fixed {channel}: boot counter set", fails_file(root) == "1", str(fails_file(root)))
        stop_watchers(root)

    for mode, channel in [("auto50", 161), ("auto24", 6), (6, 6), (165, 165), (157, 161)]:
        root, _ = softap(mode, channel)
        check(f"mode {mode} channel {channel}: no VHT, no counter",
              vht_lines(root) == [] and fails_file(root) is None, f"{vht_lines(root)} {fails_file(root)}")
        stop_watchers(root)

    root, _ = softap(161, 161, vht="reject")
    log = (root / "log").read_text()
    check("driver rejects VHT: restarted without it",
          vht_lines(root) == [] and log.count("hostapd -B") == 2 and (root / "enabled").exists(), log)
    check("driver rejects VHT: logged", "802.11ac start failed" in (root / "out").read_text())
    check("driver rejects VHT: next boot 802.11n", fails_file(root) == "2", str(fails_file(root)))
    stop_watchers(root)

    # Boot counter: two VHT boots without a station -> next boot 802.11n, counter reset.
    root, _ = softap(161, 161, fails=1)
    check("counter 1: VHT, counter 2", vht_lines(root) != [] and fails_file(root) == "2", str(fails_file(root)))
    stop_watchers(root)
    root, _ = softap(161, 161, fails=2)
    check("counter 2: 802.11n boot, counter reset", vht_lines(root) == [] and fails_file(root) is None,
          f"{vht_lines(root)} {fails_file(root)}")
    stop_watchers(root)
    root, _ = softap(161, 161)
    (root / "sta").touch()
    check("station joins: counter cleared", wait_for(lambda: fails_file(root) is None), str(fails_file(root)))
    check("station joins: VHT kept", vht_lines(root) != [] and (root / "vht").exists())
    stop_watchers(root)

    # z-tesla switches to auto50 and picks channel 36: its enable fails with center 155.
    root, _ = softap(161, 161)
    wait_for(lambda: "status" in (root / "log").read_text())
    (root / "usr/z-loader/wifi-channel-mode").write_text("auto50\n")
    cli(root, "-i", "wlan0", "disable")
    cli(root, "set", "channel", "36")
    cli(root, "-i", "wlan0", "enable")
    # z-tesla's enable fails with the stale center unless the watcher already dropped VHT (the
    # mock polls fast, so either order happens); the outcome below must hold in both.
    check("mode change: watcher drops VHT and re-enables the AP",
          wait_for(lambda: (root / "enabled").exists()) and vht_lines(root) == [] and not (root / "vht").exists()
          and (root / "chan").read_text().strip() == "36", (root / "log").read_text())
    stop_watchers(root)

    # Country change on a fixed channel; the new country does not allow 80 MHz. z-tesla's
    # disable/enable runs before the watcher can see the new country (nothing else changed),
    # so the enable fails deterministically and the watcher must bring the AP back itself.
    root, _ = softap(161, 161)
    wait_for(lambda: "status" in (root / "log").read_text())
    cli(root, "-i", "wlan0", "disable")
    cli(root, "-i", "wlan0", "enable", enable_vht="reject")
    check("country change: z-tesla's enable fails first", not (root / "enabled").exists())
    (root / "usr/z-loader/country").write_text("JP\n")
    check("country change: AP re-enabled without VHT",
          wait_for(lambda: (root / "enabled").exists()) and vht_lines(root) == [], (root / "log").read_text())
    stop_watchers(root)

    # AP intentionally down (first_boot==0) when z-tesla switches channel: its enable fails,
    # the watcher never saw the AP up, but the channel change still brings it back.
    root, _ = softap(161, 161)
    cli(root, "-i", "wlan0", "disable")
    (root / "usr/z-loader/wifi-channel-mode").write_text("36\n")
    cli(root, "set", "channel", "36")
    cli(root, "-i", "wlan0", "enable")
    check("channel change while AP was down: re-enabled without VHT",
          wait_for(lambda: (root / "enabled").exists()) and vht_lines(root) == [], (root / "log").read_text())
    stop_watchers(root)

    # An empty read (file being rewritten) is not a mode change.
    root, _ = softap(161, 161)
    (root / "usr/z-loader/wifi-channel-mode").write_text("")
    time.sleep(0.5)
    check("empty mode read: VHT kept", vht_lines(root) != [] and "turning 802.11ac off" not in (root / "out").read_text())
    stop_watchers(root)

    # Diagnostics: written to the web root, no passphrase, radio untouched; with and without ac.
    for opts, label in [({"diag": True}, "diag"), ({"ac": True, "diag": True}, "ac+diag")]:
        root = make_root(build(**opts)[wifi_patches.SOFTAP_PATH], conf, 44, 44)
        (root / "usr/z-loader/zj_hardware").write_text("BOX_1189B\n")
        (root / "usr/z-loader/zjinnova/web").mkdir(parents=True)
        with open(root / "out", "w") as out:
            subprocess.run(["/bin/sh", root / "script.sh"], env=env_for(root), cwd=root,
                           stdout=out, stderr=subprocess.STDOUT, timeout=20)
        diag = root / "usr/z-loader/zjinnova/web/zq-wifi.txt"
        wait_for((root / "usr/z-loader/zjinnova/web/zq-diag-6.txt").exists)  # last step
        text = diag.read_text() if diag.exists() else ""
        check(f"{label}: diagnostics written",
              all(k in text for k in ["BOX_1189B", "wifi-channel=44", "state=ENABLED", "rf_info\nbw=1",
                                      "rtw_ap_chbw_decision", "ht_capab=[SHORT-GI-20]",
                                      "== hostapd process\n123 root 0:00 hostapd -B", "== hostapd -v / -h"]), text)
        check(f"{label}: iw and boot dmesg sections",
              all(k in text for k in ["width: 20 MHz", "country CA", "[44]", "== dmesg at 8 s (AP start)\nRTW:"]), text)
        check(f"{label}: no passphrase, unrelated dmesg dropped",
              "wpa_passphrase" not in text and "12345678" not in text and "unrelated line" not in text)
        check(f"{label}: SSID and MACs redacted",
              "T3-TEST" not in text and "aa:bb:cc" not in text and "ssid" not in text.lower()
              and "Owner" not in text and "Phone)" not in text and ":66" not in text
              # "mac:" loses its hex-looking "ac" to the MAC mask; the identifier is still fully hidden
              and "candidate: <name>(xx:xx:xx:xx:xx:xx, ch:6)" in text and "mxx:xx:xx:xx:xx:xx\n" in text, text)
        web = root / "usr/z-loader/zjinnova/web"
        markers = [(web / f"zq-diag-{n}.txt").read_text().strip() if (web / f"zq-diag-{n}.txt").exists() else None
                   for n in range(7)]
        check(f"{label}: stage markers", markers == ["started", "slept", "settings", "hostapd", "proc", "dmesg",
                                                     "published"], str(markers))
        check(f"{label}: nothing left in /tmp", not any((root / "tmp").glob("zq-*")))
        cli_cmds = [l for l in (root / "log").read_text().splitlines() if l.startswith("hostapd_cli")]
        check(f"{label}: radio untouched by diagnostics",
              all(c.endswith((" status", " all_sta")) for c in cli_cmds), str(cli_cmds))
        check(f"{label}: VHT only with ac", (vht_lines(root) != []) == ("ac" in opts))
        stop_watchers(root)

    # bw40 experiment: writes "<ch> 1 <offset>" once, records before/after, never repeats.
    bw40_script = build(diag=True, bw40=True)[wifi_patches.SOFTAP_PATH]
    for ch, off in [(44, 2 - 1), (161, 2)]:
        root = make_root(bw40_script, conf, ch, ch)
        (root / "usr/z-loader/zjinnova/web").mkdir(parents=True)
        web = root / "usr/z-loader/zjinnova/web"
        cc = root / "proc/net/rtl8821cs/wlan0/change_bss_chbw"
        for boot in (1, 2):
            for f in web.glob("zq-diag-*.txt"):
                f.unlink()
            with open(root / "out", "w") as out:
                subprocess.run(["/bin/sh", root / "script.sh"], env=env_for(root), cwd=root,
                               stdout=out, stderr=subprocess.STDOUT, timeout=20)
            wait_for((web / "zq-diag-6.txt").exists)
            text = (web / "zq-wifi.txt").read_text()
            stop_watchers(root)
            if boot == 1:
                check(f"bw40 ch {ch}: requested '{ch} 1 {off}' once",
                      cc.read_text().strip() == f"{ch} 1 {off}" and "== bw40 test: after" in text
                      and (root / "usr/z-loader/zq-bw40-tried").read_text().strip() == str(ch)
                      and (web / "zq-diag-7.txt").exists(), text)
                cc.write_text("")
            else:
                check(f"bw40 ch {ch}: second boot skips it", cc.read_text() == "" and "bw40 test: skipped" in text
                      and not (web / "zq-diag-7.txt").exists(), text)
    try:
        wifi_patches.patches(bw40=True)
        check("bw40 requires diag", False)
    except ValueError:
        check("bw40 requires diag", True)

    # 40 MHz fix: request after start, revert on failure, counter, channel change, no-ops.
    ht40_script = build(ht40=True)[wifi_patches.SOFTAP_PATH]
    def ht40_boot(ch, fails=None, breaks=False, sec=None, hard=False, ver="2.9.11-oc4-ht40"):
        root = make_root(ht40_script, conf, ch, ch, None)
        (root / "usr/z-loader/webversion").write_text(f"{ver}\n")
        if hard:
            (root / "cc_hard").touch()
        if fails is not None:
            (root / "usr/z-loader/zq-ht40-fails").write_text(f"{fails}\n")
        if breaks:
            (root / "cc_breaks").touch()
        if sec is not None:
            (root / "sec").write_text(f"{sec}\n")
        with open(root / "out", "w") as out:
            subprocess.run(["/bin/sh", root / "script.sh"], env=env_for(root), cwd=root,
                           stdout=out, stderr=subprocess.STDOUT, timeout=20)
        return root
    def cc_text(root):
        p = root / "proc/net/rtl8821cs/wlan0/change_bss_chbw"
        return p.read_text().strip() if p.exists() else None
    def ht40_fails(root):
        p = root / "usr/z-loader/zq-ht40-fails"
        return p.read_text().strip() if p.exists() else None

    root = ht40_boot(44)
    check("ht40 ch 44: requests 44 1 1, counter cleared",
          wait_for(lambda: cc_text(root) == "44 1 1" and ht40_fails(root) is None), f"{cc_text(root)} {ht40_fails(root)}")
    (root / "sec").write_text("1\n")  # the driver switched; hostapd now reports 40 MHz
    (root / "chan").write_text("161\n")  # z-tesla moves the AP; the driver reports 20 MHz again
    (root / "sec").write_text("0\n")
    check("ht40 channel change: requests 161 1 2", wait_for(lambda: cc_text(root) == "161 1 2"), str(cc_text(root)))
    stop_watchers(root)

    root = ht40_boot(44, breaks=True)
    check("ht40 AP breaks: reverts to 44 0 0, failure counted",
          wait_for(lambda: cc_text(root) == "44 0 0") and ht40_fails(root) == "1"
          and "back to 20 MHz" in (root / "out").read_text(), f"{cc_text(root)} {ht40_fails(root)}")
    time.sleep(0.5)
    check("ht40 AP breaks: one request per channel", (root / "out").read_text().count("requesting") == 1,
          (root / "out").read_text())
    stop_watchers(root)

    root = ht40_boot(44, fails=1, breaks=True)
    check("ht40 second failed boot: counter 2", wait_for(lambda: ht40_fails(root) == "2"), str(ht40_fails(root)))
    stop_watchers(root)
    root = ht40_boot(44, fails=2)
    time.sleep(0.5)
    check("ht40 after two failed boots: turned off for this version",
          cc_text(root) == "" and ht40_fails(root) == "off 2.9.11-oc4-ht40" and "turning it off" in (root / "out").read_text(),
          f"{cc_text(root)} {ht40_fails(root)}")
    stop_watchers(root)
    root = ht40_boot(44, fails="off 2.9.11-oc4-ht40")
    time.sleep(0.5)
    check("ht40 off: stays off on later boots", cc_text(root) == "" and ht40_fails(root) == "off 2.9.11-oc4-ht40",
          f"{cc_text(root)} {ht40_fails(root)}")
    stop_watchers(root)
    root = ht40_boot(44, fails="off 2.9.11-oc4-ht40", ver="2.9.11-oc5-ht40")
    check("ht40 off + new version flashed: tries again",
          wait_for(lambda: cc_text(root) == "44 1 1" and ht40_fails(root) is None), f"{cc_text(root)} {ht40_fails(root)}")
    stop_watchers(root)
    root = ht40_boot(44, hard=True)
    check("ht40 AP still down after revert: re-enabled",
          wait_for(lambda: "-i wlan0 enable" in (root / "log").read_text()) and cc_text(root) == "44 0 0"
          and wait_for(lambda: not (root / "stuck").exists()), (root / "log").read_text())
    stop_watchers(root)

    for ch, sec, why in [(6, None, "2.4 GHz"), (165, None, "no 40 MHz pair"), (44, 1, "already 40 MHz")]:
        root = ht40_boot(ch, sec=sec)
        time.sleep(0.5)
        check(f"ht40 {why}: no request", cc_text(root) == "", str(cc_text(root)))
        stop_watchers(root)

    for opts in [{"ac": True, "ht40": True}, {"diag": True, "bw40": True, "ht40": True}]:
        try:
            wifi_patches.patches(**opts)
            check(f"ht40 rejects {sorted(opts)}", False)
        except ValueError:
            check(f"ht40 rejects {sorted(opts)}", True)
    both = build(diag=True, ht40=True)[wifi_patches.SOFTAP_PATH]
    check("ht40 + diag: both blocks present", "#ownice: 40 MHz." in both and "#ownice: read-only Wi-Fi" in both)

    # check-hostap.sh: hostapd is gone and rejects VHT; the second restart must drop
    # VHT before the stock >4-failure reset to channel 149 / CN.
    root = make_root(scripts[wifi_patches.CHECK_HOSTAP_PATH], conf, 161, 161)
    (root / "dev/hostapd.conf").write_text(conf.replace("channel=149", "channel=161")
                                           + "ieee80211ac=1\nvht_oper_chwidth=1\nvht_oper_centr_freq_seg0_idx=155\n")
    proc = subprocess.Popen(["/bin/sh", root / "script.sh"], env=env_for(root, vht="reject"), cwd=root,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_for(lambda: (root / "running").exists())
    proc.kill()
    log = (root / "log").read_text()
    check("check-hostap: second restart drops VHT, keeps channel and country",
          (root / "running").exists() and vht_lines(root) == [] and log.count("hostapd -B") == 2
          and (root / "usr/z-loader/country").read_text().strip() == "CA"
          and "channel=161" in (root / "dev/hostapd.conf").read_text(), log)

    print(f"{len(failures)} failed" if failures else "all passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
