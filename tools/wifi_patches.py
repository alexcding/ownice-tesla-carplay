"""Opt-in soft-AP boot-script patches (firmware 2.9.11): 802.11ac 80 MHz and diagnostics.

802.11ac (--wifi-ac):

Both shipped radios (RTL8821CS on BOX_1189B, RTL8733BS on BOX_0333C) and hostapd
v2.7 support VHT, but the stock config stops at 802.11n HT40. The VHT lines go
only into the runtime copy /dev/hostapd.conf, never into /etc/hostapd.conf:
check-hostap.sh restores /etc/hostapd.conf as its last-resort fallback, so that
fallback stays the stock 802.11n config.

The box can only be reflashed over this AP, so every failure path ends in 802.11n:
- VHT only on a fixed channel (wifi-channel-mode == wifi-channel) in 36-48 or
  149-161. z-tesla's auto modes move the AP to random channels at runtime with
  `hostapd_cli set channel` and never update the 80 MHz center.
- If hostapd does not reach state=ENABLED with VHT, strip it and restart stock;
  the next boot then runs 802.11n too (counter set to 2).
- Boot counter /usr/z-loader/zq-vht-fails: set before each VHT start, cleared once
  a station joins. After two VHT boots without a station, the next boot runs
  802.11n and resets the counter (an AP that is up but unreachable recovers on
  every third power cycle). Any station counts, including a wireless-CarPlay
  phone on this AP, so an AP only the car cannot join is not detected.
- A watcher drops VHT once z-tesla changes the channel mode, country or channel
  at runtime, and re-enables the AP if that switch left it down.
- check-hostap.sh strips VHT on its second failed restart, before its own
  channel 149 / country CN reset.

Diagnostics (--wifi-diag): changes no radio setting. 60 s after the AP starts, a
background job collects hostapd_cli status, the driver's /proc state and Wi-Fi
kernel messages in /tmp and publishes them once at https://www.zbox.link/zq-wifi.txt.
Markers zq-diag-<n>.txt in the web root show how far it got.
It exists to find out why the AP runs 20 MHz although hostapd.conf requests HT40.
No passphrase is written (filtered from the config copy; wifi-passwd is not read);
lines naming an SSID (they contain the box serial) are dropped and MACs masked.

40 MHz fix (--wifi-ht40): the RTL8821CS driver reports a 20 MHz channel switch when the
AP starts (start_bss_network -> ch_switch_notify), so hostapd runs 20 MHz although
hostapd.conf asks for HT40. A background loop asks the driver again for 40 MHz through
/proc/net/rtl8821cs/wlan0/change_bss_chbw once the AP is up, and after each channel
change. Verified once on BOX_1189B: 72 -> 150 Mbit/s. If the AP is not ENABLED 5 s after
a request it reverts to 20 MHz, and re-enables the AP if it is still down. A counter
(/usr/z-loader/zq-ht40-fails, written before each request, cleared on success) turns
the fix off for good after two failed boots ("off <webversion>"); flashing a different
web version clears it. One request per channel per boot: if the AP is restarted on the
same channel it stays at 20 MHz until the next boot.

40 MHz experiment (--wifi-bw40-test, implies --wifi-diag): **changes the radio once.**
On the first boot after flashing, the diagnostics job asks the RTL8821CS driver for
40 MHz on the current channel through /proc/net/rtl8821cs/wlan0/change_bss_chbw and
records the before/after state in the report. A flag file, written and synced first,
keeps it from ever running again; a power cycle restores the normal start-up.

A patched boot script stays on the box until `tools/flash.sh rollback` (stock)
replaces it: builds without an option do not ship the script, so flashing them
leaves an earlier patched copy in place. The report and markers stay in the web
root even after a rollback (the installer only copies, it never deletes).

Used by tools/build_patch.py (patches()) and applied with player_patches.apply.
Each entry is (description, original, replacement, expected count).
"""

SOFTAP_PATH = "etc/wlan-softap.sh"
CHECK_HOSTAP_PATH = "etc/check-hostap.sh"

STRIP_VHT = "sed -i '/^ieee80211ac=/d;/^vht_/d' /dev/hostapd.conf"
VHT_FAILS = "/usr/z-loader/zq-vht-fails"

SOFTAP_PATCHES = [
    ("add 802.11ac 80 MHz settings on a fixed channel",
     'sed -i "s/HT40+/${ht40_mode}/g" /dev/hostapd.conf\n',
     'sed -i "s/HT40+/${ht40_mode}/g" /dev/hostapd.conf\n'
     "\n"
     "#ownice: 802.11ac at 80 MHz, only on a fixed channel (auto modes switch channels at runtime)\n"
     "wifi_mode=$(cat /usr/z-loader/wifi-channel-mode)\n"
     "vht_center=\n"
     'if [ "$wifi_mode" == "$wifi_channel" ]; then\n'
     '\tcase "$wifi_channel" in\n'
     "\t\t36|40|44|48) vht_center=42 ;;\n"
     "\t\t149|153|157|161) vht_center=155 ;;\n"
     "\tesac\n"
     "fi\n"
     "#two VHT boots in a row without a station: run this boot with 802.11n\n"
     'if [ -n "$vht_center" ]; then\n'
     f"\tvht_fails=$(cat {VHT_FAILS} 2>/dev/null)\n"
     "\tif [ \"${vht_fails:-0}\" -ge 2 ]; then\n"
     '\t\techo "802.11ac: no station on the last two boots, using 802.11n"\n'
     f"\t\trm -f {VHT_FAILS}\n"
     "\t\tvht_center=\n"
     "\telse\n"
     f"\t\techo $((${{vht_fails:-0}} + 1)) > {VHT_FAILS}\n"
     "\tfi\n"
     "fi\n"
     'if [ -n "$vht_center" ]; then\n'
     "\tprintf 'ieee80211ac=1\\nvht_oper_chwidth=1\\nvht_oper_centr_freq_seg0_idx=%s\\n' $vht_center >> /dev/hostapd.conf\n"
     "fi\n", 1),
    ("start hostapd through a function; fall back to 802.11n if VHT does not come up; VHT watcher",
     "#start hostapd\n"
     "apple_mode=$(cat /data/local/tmp/local_apple)\n"
     'if [ $apple_mode == "32" ]\n'
     "then\n"
     '\techo "start airplay hostapd mode"\n'
     "\thostapd-airplay -B /dev/hostapd.conf\n"
     "else\n"
     "\thostapd -B /dev/hostapd.conf\n"
     "fi\n",
     "#start hostapd\n"
     "apple_mode=$(cat /data/local/tmp/local_apple)\n"
     "start_hostapd() {\n"
     'if [ $apple_mode == "32" ]\n'
     "then\n"
     '\techo "start airplay hostapd mode"\n'
     "\thostapd-airplay -B /dev/hostapd.conf\n"
     "else\n"
     "\thostapd -B /dev/hostapd.conf\n"
     "fi\n"
     "}\n"
     "start_hostapd\n"
     "\n"
     "#ownice: hostapd exits (or never enables) if the driver rejects VHT: retry with 802.11n\n"
     'if [ -n "$vht_center" ]; then\n'
     "\tvht_ok=0\n"
     "\tfor i in 1 2 3 4 5 6 7 8 9 10; do\n"
     "\t\tsleep 1\n"
     "\t\tif hostapd_cli -i wlan0 status 2>/dev/null | grep -q \"state=ENABLED\"; then\n"
     "\t\t\tvht_ok=1\n"
     "\t\t\tbreak\n"
     "\t\tfi\n"
     "\t\tps -ef | grep -v grep | grep -q hostapd || break\n"
     "\tdone\n"
     "\tif [ $vht_ok == 0 ]; then\n"
     '\t\techo "802.11ac start failed, using 802.11n"\n'
     f"\t\techo 2 > {VHT_FAILS}\n"
     f"\t\t{STRIP_VHT}\n"
     "\t\tkillall -9 hostapd hostapd-airplay 2>/dev/null\n"
     "\t\tvht_center=\n"
     "\t\tstart_hostapd\n"
     "\tfi\n"
     "fi\n"
     "\n"
     "#ownice: z-tesla re-applies channel, mode and country at runtime (disable, set channel,\n"
     "#enable) without the 80 MHz center. Once any of them changes, drop VHT and bring the\n"
     "#AP back if the switch left it down. Also clears the boot counter once a station joins.\n"
     'if [ -n "$vht_center" ]; then\n'
     "\t(\n"
     "\t\tcountry=$(cat /usr/z-loader/country)\n"
     "\t\twas_up=0\n"
     "\t\twhile true; do\n"
     "\t\t\tsleep 2\n"
     "\t\t\tst=$(hostapd_cli -i wlan0 status 2>/dev/null)\n"
     '\t\t\tcase "$st" in *state=ENABLED*) was_up=1 ;; esac\n'
     f"\t\t\tif [ -f {VHT_FAILS} ] && hostapd_cli -i wlan0 all_sta 2>/dev/null | grep -qE '^([0-9a-f]{{2}}:){{5}}'; then\n"
     f"\t\t\t\trm -f {VHT_FAILS}\n"
     "\t\t\tfi\n"
     "\t\t\tm=$(cat /usr/z-loader/wifi-channel-mode)\n"
     "\t\t\tc=$(cat /usr/z-loader/country)\n"
     '\t\t\tch=$(echo "$st" | grep "^channel=" | cut -d= -f2)\n'
     '\t\t\t[ -n "$m" ] && [ "$m" != "$wifi_mode" ] && break\n'
     '\t\t\t[ -n "$c" ] && [ "$c" != "$country" ] && break\n'
     '\t\t\t[ -n "$ch" ] && [ "$ch" != "$wifi_channel" ] && was_up=1 && break\n'
     "\t\tdone\n"
     '\t\techo "wifi settings changed, turning 802.11ac off"\n'
     f"\t\t{STRIP_VHT}\n"
     "\t\thostapd_cli -i wlan0 set vht_oper_chwidth 0\n"
     "\t\thostapd_cli -i wlan0 set ieee80211ac 0\n"
     "\t\t#a channel change means z-tesla ran a refresh, which always ends in enable\n"
     "\t\t[ $was_up == 1 ] || exit 0\n"
     "\t\tfor i in 1 2 3 4 5; do\n"
     "\t\t\tsleep 2\n"
     '\t\t\thostapd_cli -i wlan0 status 2>/dev/null | grep -q "state=ENABLED" && break\n'
     "\t\t\thostapd_cli -i wlan0 enable\n"
     "\t\tdone\n"
     "\t) &\n"
     "fi\n", 1),
]

CHECK_HOSTAP_PATCHES = [
    ("second failed restart drops VHT before the stock channel 149 / CN reset",
     '\t\techo "restart hostapd......."\n'
     "\t\thostapd -B /dev/hostapd.conf\n",
     '\t\techo "restart hostapd......."\n'
     f"\t\t[ $check_err -ge 1 ] && {STRIP_VHT}\n"
     "\t\thostapd -B /dev/hostapd.conf\n", 1),
]

AC_PATCHES = {
    SOFTAP_PATH: SOFTAP_PATCHES,
    CHECK_HOSTAP_PATH: CHECK_HOSTAP_PATCHES,
}

DIAG_OUT = "/usr/z-loader/zjinnova/web/zq-wifi.txt"
DIAG_PROC = "rf_info chan_plan country_code ap_info ht_option bw_mode mlmext_state fwstate adapters_status"
MAC_BRE = r"[0-9a-fA-F]\{2\}\(:[0-9a-fA-F]\{2\}\)\{5\}"

# Plain busybox commands only (v1 with a `{ } | sed -E` pipeline wrote nothing on the box).
# Markers zq-diag-<n>.txt record how far it got: 0 = script ran (written before the
# background job), 1 = after the wait, 2 settings, 3 hostapd_cli, 4 = /proc file being read,
# 5 dmesg, 6 = report published.
DIAG_PATCHES = {
    SOFTAP_PATH: [
        ("write read-only Wi-Fi diagnostics to the web root after the AP starts",
         "\thostapd_cli -i wlan0 disable\n"
         "fi\n"
         "\n"
         "sync\n",
         "\thostapd_cli -i wlan0 disable\n"
         "fi\n"
         "\n"
         "#ownice: read-only Wi-Fi diagnostics, https://www.zbox.link/zq-wifi.txt\n"
         "#(markers zq-diag-<n>.txt show how far it got; SSIDs and MACs are removed)\n"
         "zq_web=/usr/z-loader/zjinnova/web\n"
         "echo started > $zq_web/zq-diag-0.txt\n"
         "(\n"
         "\t#kernel log of the AP start, before the 2.4 GHz client (wlan1, 8189es) floods the ring buffer\n"
         "\tsleep 8\n"
         "\tdmesg > /tmp/zq-dmesg-boot.txt 2>&1\n"
         "\tsleep 52\n"
         "\techo slept > $zq_web/zq-diag-1.txt\n"
         "\tout=/tmp/zq-wifi.txt\n"
         '\techo "== uptime" > $out\n'
         "\tcat /proc/uptime >> $out 2>&1\n"
         '\techo "== hardware" >> $out\n'
         "\tcat /usr/z-loader/zj_hardware >> $out 2>&1\n"
         '\techo "== settings" >> $out\n'
         "\tfor f in wifi-channel wifi-channel-mode apchannel country zq-vht-fails; do\n"
         '\t\techo "$f=$(cat /usr/z-loader/$f 2>/dev/null)" >> $out\n'
         "\tdone\n"
         '\techo "== hostapd.conf in use" >> $out\n'
         "\tgrep -v passphrase /dev/hostapd.conf >> $out 2>&1\n"
         "\techo settings > $zq_web/zq-diag-2.txt\n"
         '\techo "== hostapd_cli status" >> $out\n'
         "\thostapd_cli -i wlan0 status >> $out 2>&1\n"
         '\techo "== hostapd process" >> $out\n'
         "\tps -ef | grep hostapd | grep -v grep >> $out 2>&1\n"
         "\tls -l /usr/sbin/hostapd* /usr/bin/hostapd* /sbin/hostapd* /usr/bin/iw /usr/sbin/iw >> $out 2>&1\n"
         "\t#-v / -h only print and exit; they do not touch the interface\n"
         "\tfor h in hostapd hostapd-airplay; do\n"
         '\t\techo "== $h -v / -h" >> $out\n'
         "\t\t$h -v >> $out 2>&1\n"
         "\t\t$h -h >> $out 2>&1\n"
         "\tdone\n"
         '\techo "== iw dev wlan0 info" >> $out\n'
         "\tiw dev wlan0 info >> $out 2>&1\n"
         '\techo "== iw reg get" >> $out\n'
         "\tiw reg get >> $out 2>&1\n"
         '\techo "== iw phy" >> $out\n'
         "\tiw phy >> $out 2>&1\n"
         "\techo hostapd > $zq_web/zq-diag-3.txt\n"
         "\tfor d in /proc/net/rtl8821cs/wlan0 /proc/net/rtl8733bs/wlan0; do\n"
         "\t\t[ -d $d ] || continue\n"
         f"\t\tfor f in {DIAG_PROC}; do\n"
         "\t\t\techo $d/$f > $zq_web/zq-diag-4.txt\n"
         '\t\t\techo "== $d/$f" >> $out\n'
         "\t\t\tcat $d/$f >> $out 2>&1\n"
         "\t\tdone\n"
         "\tdone\n"
         "\techo proc > $zq_web/zq-diag-4.txt\n"
         "\tdmesg > /tmp/zq-dmesg.txt 2>&1\n"
         "\t#the 2.4 GHz client (wlan1, 8189es) floods the log; keep the AP's start (first lines) separately\n"
         '\techo "== dmesg at 8 s (AP start)" >> $out\n'
         "\tgrep -i -e rtw -e rtl8 -e wlan -e ht40 -e ht20 -e chbw -e hostapd -e cfg80211 -e regulatory -e 80211"
         " /tmp/zq-dmesg-boot.txt | head -n 500 >> $out\n"
         '\techo "== dmesg (wifi, last 150)" >> $out\n'
         "\tgrep -i -e rtw -e rtl8 -e wlan0 -e ht40 -e ht20 -e chbw -e hostapd /tmp/zq-dmesg.txt | tail -n 150 >> $out\n"
         "\trm -f /tmp/zq-dmesg.txt /tmp/zq-dmesg-boot.txt\n"
         "\techo dmesg > $zq_web/zq-diag-5.txt\n"
         "\t#network names: lines naming an SSID are dropped; the client's join log prints\n"
         "\t#'candidate: <name>(<mac>, ch:N)', so the name before '(' is masked too\n"
         f"\tsed -e '/[Ss][Ss][Ii][Dd]/d' -e 's/{MAC_BRE}/xx:xx:xx:xx:xx:xx/g'"
         " -e 's/xx:xx:xx:xx:xx:xx:[0-9a-fA-F]\\{2\\}/xx:xx:xx:xx:xx:xx/g'"
         " -e 's/candidate: .*(xx:xx:xx:xx:xx:xx/candidate: <name>(xx:xx:xx:xx:xx:xx/' $out > $out.pub &&\n"
         f"\t\tmv $out.pub {DIAG_OUT} && echo published > $zq_web/zq-diag-6.txt\n"
         "\trm -f $out $out.pub\n"
         "\tsync\n"
         ") &\n"
         "\n"
         "sync\n", 1),
    ],
}


BW40_FLAG = "/usr/z-loader/zq-bw40-tried"

# One-time experiment (--wifi-bw40-test, needs --wifi-diag): the RTL8821CS driver reports a
# channel switch to 20 MHz when the AP starts (start_bss_network -> ch_switch_notify), so
# hostapd ends with secondary_channel=0. Ask the driver for 40 MHz at runtime through its
# change_bss_chbw proc file ("<ch> <bw> <offset>", bw 1 = 40 MHz, offset 1 = secondary
# above, 2 = below) and record the result in the report. The flag file is written (and
# synced) first, so it runs on one boot only: a power cycle undoes whatever it did.
BW40_PATCHES = {
    SOFTAP_PATH: [
        ("one-time runtime 40 MHz request through the driver, before the report is published",
         "\techo dmesg > $zq_web/zq-diag-5.txt\n",
         "\techo dmesg > $zq_web/zq-diag-5.txt\n"
         "\t#ownice: one-time 40 MHz experiment (runs once ever; see BW40_FLAG)\n"
         "\tzq_cc=/proc/net/rtl8821cs/wlan0/change_bss_chbw\n"
         "\tzq_ch=$(hostapd_cli -i wlan0 status 2>/dev/null | grep \"^channel=\" | cut -d= -f2)\n"
         "\tzq_off=\n"
         "\tcase \"$zq_ch\" in\n"
         "\t\t36|44|149|157) zq_off=1 ;;\n"
         "\t\t40|48|153|161) zq_off=2 ;;\n"
         "\tesac\n"
         "\t#the flag must be on disk before the radio is touched, or a wedged AP would repeat every boot\n"
         f"\tif [ ! -f {BW40_FLAG} ] && [ -f $zq_cc ] && [ -n \"$zq_off\" ] &&\n"
         f"\t\techo $zq_ch > {BW40_FLAG} && sync && [ -s {BW40_FLAG} ]; then\n"
         '\t\techo "== bw40 test: before" >> $out\n'
         "\t\tiw dev wlan0 info >> $out 2>&1\n"
         "\t\techo \"$zq_ch 1 $zq_off\" > $zq_cc\n"
         '\t\techo "== bw40 test: wrote $zq_ch 1 $zq_off (rc $?)" >> $out\n'
         "\t\tsleep 5\n"
         '\t\techo "== bw40 test: after" >> $out\n'
         "\t\tiw dev wlan0 info >> $out 2>&1\n"
         "\t\tcat /proc/net/rtl8821cs/wlan0/rf_info >> $out 2>&1\n"
         "\t\thostapd_cli -i wlan0 status >> $out 2>&1\n"
         "\t\tdmesg | tail -n 60 >> $out 2>&1\n"
         "\t\techo bw40 > $zq_web/zq-diag-7.txt\n"
         "\telse\n"
         f'\t\techo "== bw40 test: skipped (ch=$zq_ch, flag $(cat {BW40_FLAG} 2>/dev/null))" >> $out\n'
         "\tfi\n", 1),
    ],
}


HT40_FAILS = "/usr/z-loader/zq-ht40-fails"

HT40_PATCHES = {
    SOFTAP_PATH: [
        ("ask the AP driver for 40 MHz after start-up and after channel changes, with fallback",
         "\thostapd_cli -i wlan0 disable\n"
         "fi\n",
         "\thostapd_cli -i wlan0 disable\n"
         "fi\n"
         "\n"
         "#ownice: 40 MHz. The RTL8821CS driver reports 20 MHz when the AP starts (hostapd follows);\n"
         "#ask it again through change_bss_chbw once the AP is up and after each channel change.\n"
         "#Not ENABLED 5 s later: back to 20 MHz. Two failed boots: off until a new web version is flashed.\n"
         "zq_cc=/proc/net/rtl8821cs/wlan0/change_bss_chbw\n"
         "if [ -f $zq_cc ]; then\n"
         "(\n"
         "\tzq_ver=$(cat /usr/z-loader/webversion 2>/dev/null)\n"
         f"\tzq_fails=$(cat {HT40_FAILS} 2>/dev/null)\n"
         '\tcase "$zq_fails" in\n'
         '\t\t"off $zq_ver")\n'
         '\t\t\techo "40 MHz: off after two failed boots (flash a new version to retry)"\n'
         "\t\t\texit 0 ;;\n"
         "\t\toff*)\n"
         f"\t\t\tzq_fails=\n"
         f"\t\t\trm -f {HT40_FAILS} ;;\n"
         "\tesac\n"
         '\tif [ "${zq_fails:-0}" -ge 2 ]; then\n'
         '\t\techo "40 MHz: failed on the last two boots, turning it off"\n'
         f'\t\techo "off $zq_ver" > {HT40_FAILS}\n'
         "\t\tsync\n"
         "\t\texit 0\n"
         "\tfi\n"
         "\tzq_last=\n"
         "\tsleep 15\n"
         "\twhile true; do\n"
         "\t\tzq_st=$(hostapd_cli -i wlan0 status 2>/dev/null)\n"
         '\t\tzq_ch=$(echo "$zq_st" | grep "^channel=" | cut -d= -f2)\n'
         '\t\tzq_sec=$(echo "$zq_st" | grep "^secondary_channel=" | cut -d= -f2)\n'
         "\t\tzq_off=\n"
         '\t\tcase "$zq_ch" in\n'
         "\t\t\t36|44|149|157) zq_off=1 ;;\n"
         "\t\t\t40|48|153|161) zq_off=2 ;;\n"
         "\t\tesac\n"
         '\t\tcase "$zq_st" in *state=ENABLED*) ;; *) zq_off= ;; esac\n'
         '\t\tif [ -n "$zq_off" ] && [ "$zq_sec" == "0" ] && [ "$zq_ch" != "$zq_last" ]; then\n'
         "\t\t\tzq_last=$zq_ch\n"
         f"\t\t\techo $((${{zq_fails:-0}} + 1)) > {HT40_FAILS}\n"
         "\t\t\tsync\n"
         '\t\t\techo "40 MHz: requesting $zq_ch 1 $zq_off"\n'
         '\t\t\techo "$zq_ch 1 $zq_off" > $zq_cc\n'
         "\t\t\tsleep 5\n"
         '\t\t\tif hostapd_cli -i wlan0 status 2>/dev/null | grep -q "state=ENABLED"; then\n'
         f"\t\t\t\trm -f {HT40_FAILS}\n"
         "\t\t\t\tzq_fails=\n"
         "\t\t\telse\n"
         '\t\t\t\techo "40 MHz: AP not enabled, back to 20 MHz"\n'
         '\t\t\t\techo "$zq_ch 0 0" > $zq_cc\n'
         "\t\t\t\tsleep 5\n"
         '\t\t\t\tif ! hostapd_cli -i wlan0 status 2>/dev/null | grep -q "state=ENABLED"; then\n'
         '\t\t\t\t\techo "40 MHz: AP still down after the revert, re-enabling it"\n'
         "\t\t\t\t\thostapd_cli -i wlan0 enable\n"
         "\t\t\t\tfi\n"
         "\t\t\tfi\n"
         "\t\tfi\n"
         "\t\tsleep 10\n"
         "\tdone\n"
         ") &\n"
         "fi\n", 1),
    ],
}


def patches(ac=False, diag=False, bw40=False, ht40=False):
    """Patch lists per boot script for the selected options (ac, diag, bw40 on top of diag, ht40)."""
    if bw40 and not diag:
        raise ValueError("the bw40 experiment writes into the diagnostics report: enable diag too")
    if ht40 and (ac or bw40):
        raise ValueError("ht40 is not combined with ac (80 MHz) or the one-time bw40 experiment yet")
    out = {}
    for enabled, group in [(ac, AC_PATCHES), (diag, DIAG_PATCHES), (bw40, BW40_PATCHES), (ht40, HT40_PATCHES)]:
        if enabled:
            for path, items in group.items():
                out.setdefault(path, []).extend(items)
    return out
