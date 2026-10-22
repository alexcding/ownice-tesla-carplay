# AGENTS.md

Notes for agents and contributors working on this repo.

## Rules

- **Never commit vendor firmware or anything extracted from it.** The official zip contains private TLS keys
  (`usr/z-loader/*.key` for zbox.link, autoaibox.link, kalos.link, tslbox.link). `.gitignore` blocks
  `*.zip`, `*.key` and `*.crt`; keep it that way. Patches are applied at build time from the vendor download.
- **Never upload to or reboot a box without the owner's explicit go-ahead.** Read-only queries
  (`tools/box_info.sh`, `GET /cgi-bin/check*.cgi`) are fine.
- Each patch is an exact string replacement that must match once (`INDEX_PATCHES` in `tools/build_patch.py`).
  If the base firmware changes, the build must fail rather than guess.
- Keep the package minimal: only files we change, plus the vendor's `update.sh`.

## Device facts (Ownice T3, firmware 2.9.11 / build 202402031913)

| Item | Value |
|---|---|
| SoC / OS | Allwinner, "KunoOS" Linux, 32-bit ARM (armhf) |
| Box IP / DHCP | `3.3.3.3`, clients `3.3.3.10–30`, box is DNS + router |
| DNS | box resolves `*.zbox.link` → 3.3.3.3, `www.autoaibox.link` → 2.2.2.2 |
| Web server | Go binary `z-connman*` (`/usr/z-loader/config*.yml`), webroot `/usr/z-loader/zjinnova/web` |
| Host routing | `Host: 3.3.3.3` → update page (`update-web`); `Host: *.zbox.link` → CarPlay player (`web`) |
| TLS cert | CN `zbox.link`, SAN `zbox.link, www.zbox.link`, expires 2027-02-28 |
| Update channel | `tesla-linux` |
| Other services | `S50telnet` in init.d (untested) |

### Player ports (`web/assets/index.*.js`)

| Purpose | HTTPS mode | HTTP mode |
|---|---|---|
| control WebSocket | `wss://:9002` | `ws://:9112` |
| audio WebSocket | `wss://:7002` | `ws://:7112` |
| video WebSocket (H.264 → WebCodecs) | `wss://:5002` | not used |
| HTTP preview (JPEG, low-res) | `:8002` | `:8112` |

HD requires `location.protocol === "https:"` **and** `typeof VideoDecoder === "function"`;
otherwise the player asks the parent page to reload over HTTP (compatible mode).

### CGI API (`http://3.3.3.3/cgi-bin/…`)

`checkver` (`ver`, `ver2` = web version), `checkchannel`, `checkband`/`setband`, `getwifimode`/`setwifimode`,
`getapchannel`/`setapchannel`/`setapcountry`, `getchannellist`, `checkname`, `downloadLog`, `doreboot`,
`upload` (POST multipart; saved as `/misc/update_tesla.zip`).

### Update flow

1. The update page queries `http://updates.zhijian.link/updates/zsender?chn=<channel>`, which 302-redirects to
   `update-1251259776.cos.ap-shanghai.myqcloud.com/tesla/tesla.json`, returning
   `{webver, url, md5}`. Fallback: `https://u.zhijian.link/...`.
2. If `webver != current`, the browser downloads `url`, checks the MD5 and POSTs the zip to `upload.cgi`.
3. `/usr/z-loader/update.sh` unzips into `/misc/update-file` and runs the package's own `update.sh`
   (stock: `cp ./* / -fr`). The status LED on PB6 blinks while it runs.
4. The shell path has no signature check. `z-connman` contains `systemVerify` / `rsaDecryptOk` symbols;
   whether uploads are verified is **unconfirmed**.

## Status

- Patch 2.10 builds and its diff is verified (3 lines in `index.html`). **It has not been flashed or tested on a box.**
- Unverified: whether the Tesla browser exposes `VideoDecoder` on Intel Atom cars.

## Privacy notes found in stock `index.html`

- Loads Baidu analytics (`hm.baidu.com`).
- The "enterFullscreen" action navigates through `v.qq.com` and `1905.com` redirectors back to `www.zbox.link`.
