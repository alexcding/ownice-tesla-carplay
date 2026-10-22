#!/usr/bin/env python3
"""Hash-pinned, offline HEVC patch builder for stock Ownice T3 2.9.11.

Only our C/assembly lives in Git. Firmware bytes are read at build time. A new
RX PT_LOAD holds our position-independent handlers; existing PLT/GOT entries
provide the original library APIs. No new imports, writable executable memory,
authentication changes, or version impersonation are introduced.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "tools/native"
PINNED = {
    "libAirPlay.so": "963f33856cb90649d9625485eb9ee302ea63a964465a0149ac9e6f6e25a1ef8a",
    "libScreenStream.so": "6e0ea898f25481bf62b3a1e3184a916b1a469df71fd33a8b3637b9b159e8c588",
    "z-tesla": "fabf68f52d56e487a591e1229c3e2fa3c9006a9efc91bb4af57c03f1bd501ed8",
    "libzlink-main.so": "9cba83a9297299dcacd3acd0d1e1fd1d73750c62f733f28dc77eb75df7452e2e",
}


def align(n, unit=0x10000):
    return (n + unit - 1) & ~(unit - 1)


class ELF:
    """Small reader for the exact ELF32 little-endian ARM build we accept."""
    def __init__(self, data):
        self.data = bytes(data)
        if data[:7] != b"\x7fELF\x01\x01\x01":
            raise ValueError("expected ELF32 little-endian")
        self.e = list(struct.unpack_from("<16sHHIIIIIHHHHHH", data))
        if self.e[2] != 40 or self.e[9] != 32 or self.e[11] != 40:
            raise ValueError("unsupported ELF layout")
        self.ph = [list(struct.unpack_from("<8I", data, self.e[5] + i * 32))
                   for i in range(self.e[10])]
        self.sh = [list(struct.unpack_from("<10I", data, self.e[6] + i * 40))
                   for i in range(self.e[12])]
        st = self.sh[self.e[13]]
        self.shstr = data[st[4]:st[4] + st[5]]
        self.sections = {self.string(self.shstr, s[0]): s for s in self.sh}

    @staticmethod
    def string(data, at):
        return data[at:data.index(0, at)].decode("ascii")

    def section(self, name):
        s = self.sections[name]
        return self.data[s[4]:s[4] + s[5]]

    def offset(self, address, n=1):
        matches = [p[1] + address - p[2] for p in self.ph
                   if p[0] == 1 and p[2] <= address and address + n <= p[2] + p[4]]
        if len(matches) != 1:
            raise ValueError(f"unmapped/ambiguous address {address:#x}")
        return matches[0]

    def symbols(self, table=".symtab"):
        s = self.sections[table]
        strings = self.sh[s[6]]
        names = self.data[strings[4]:strings[4] + strings[5]]
        result = []
        for at in range(s[4], s[4] + s[5], 16):
            name, value, size, info, other, index = struct.unpack_from("<IIIBBH", self.data, at)
            result.append((self.string(names, name), value, size, info, other, index))
        return result

    def imports(self):
        symbols = self.symbols(".dynsym")
        rel = self.section(".rel.plt")
        plt = self.sections[".plt"][3]
        slots = {address: symbols[info >> 8][0] for at in range(0, len(rel), 8)
                 for address, info in [struct.unpack_from("<II", rel, at)]}
        code = self.section(".plt")
        result = {}
        def immediate(instruction):
            value, rotation = instruction & 255, ((instruction >> 8) & 15) * 2
            return ((value >> rotation) | (value << ((32 - rotation) & 31))) & 0xffffffff
        # GNU ARM PLTs may contain extra Thumb interworking veneers, so their
        # relocation index is not a fixed-stride PLT address. Decode each ARM
        # stub's GOT target and match the actual relocation instead.
        for at in range(0, len(code) - 11, 4):
            a, b, c = struct.unpack_from("<3I", code, at)
            if (a & 0xfffff000, b & 0xfffff000, c & 0xfffff000) != (
                    0xe28fc000, 0xe28cc000, 0xe5bcf000):
                continue
            slot = plt + at + 8 + immediate(a) + immediate(b) + (c & 0xfff)
            if slot in slots:
                result[slots[slot]] = plt + at
        if len(result) != len(slots):
            raise ValueError("not all ARM PLT slots resolved")
        return result

    def got(self, name):
        symbols = self.symbols(".dynsym")
        rel = self.section(".rel.dyn")
        hits = [address for at in range(0, len(rel), 8)
                for address, info in [struct.unpack_from("<II", rel, at)]
                if (info & 255) == 21 and symbols[info >> 8][0] == name]
        if len(hits) != 1:
            raise ValueError(f"expected exactly one GLOB_DAT for {name}")
        return hits[0]


def branch(source, target):
    """Encode Thumb-2 B.W. No absolute pointer or load-address relocation."""
    delta = (target & ~1) - (source + 4)
    if delta & 1 or not -(1 << 24) <= delta < (1 << 24):
        raise ValueError("Thumb branch out of range")
    value = delta & 0x1ffffff
    s, i1, i2 = (value >> 24) & 1, (value >> 23) & 1, (value >> 22) & 1
    j1, j2 = 1 ^ i1 ^ s, 1 ^ i2 ^ s
    return struct.pack("<HH", 0xf000 | s << 10 | (value >> 12) & 0x3ff,
                       0x9000 | j1 << 13 | j2 << 11 | (value >> 1) & 0x7ff)


def tool(name, env):
    configured = os.environ.get(env)
    found = configured or shutil.which(name)
    fallback = {"clang": "/opt/homebrew/opt/llvm@22/bin/clang",
                "ld.lld": "/opt/homebrew/opt/lld@22/bin/ld.lld"}[name]
    if not configured and Path(fallback).is_file():
        found = fallback
    if not found or not Path(found).is_file():
        raise ValueError(f"{name} not found; set {env}")
    return found


def getter(name, symbol, dereference=False):
    # Constant is a PC-relative difference, not an address requiring relocation.
    return f"""
.balign 4
.global {name}
.hidden {name}
.type {name},%function
.thumb_func
{name}:
    ldr r0, 1f
0:  add r0, pc
    {"ldr r0, [r0]" if dereference else ""}
    bx lr
.balign 4
1:  .word {symbol} - (0b + 4)
"""


def payload(elf, name, directory, diagnostics=False, keep_video_focus=False):
    base = align(max(p[2] + p[5] for p in elf.ph if p[0] == 1))
    asm = ".syntax unified\n.thumb\n.text\n"
    cfiles, patches = [], []
    constants = {}
    if name == "libAirPlay.so":
        cfiles = ["carplay_hevc.c"]
        for function, symbol in [("zq_dict_keys", "kCFLDictionaryKeyCallBacksCFLTypes"),
                                 ("zq_dict_values", "kCFLDictionaryValueCallBacksCFLTypes"),
                                 ("zq_array_values", "kCFLArrayCallBacksCFLTypes")]:
            constants["addr_" + function] = elf.got(symbol)
            asm += getter(function, "addr_" + function, True)
        asm += """
.global zq_info_exit
.thumb_func
zq_info_exit:
    bl zq_info
    add.w sp, sp, #0x10e0
    b.w info_resume
.global zq_setup_exit
.thumb_func
zq_setup_exit:
    push {r0-r3, r12, lr}
    ldr r0, [sp, #0x38]
    bl zq_setup
    pop {r0-r3, r12, lr}
    mov r2, r3
    ldr r3, [sp, #0x20]
    b.w setup_resume
"""
        patches = [(0xf946, bytes.fromhex("0df5875d"), "zq_info_exit"),
                   (0x1c650, bytes.fromhex("1a46089b"), "zq_setup_exit")]
        absolute = {"info_resume": 0xf94b, "setup_resume": 0x1c655}
    elif name == "libScreenStream.so":
        cfiles = ["screen_hevc.c", "hevc_codec.c"]
        for function, text in [("zq_avcc", b"avcc\0"), ("zq_width", b"widthPixels\0"),
                               ("zq_height", b"heightPixels\0")]:
            hits = [i for i in range(len(elf.data)) if elf.data.startswith(text, i)]
            if len(hits) != 1:
                raise ValueError(f"expected one property constant {text!r}")
            offset = hits[0] - 8
            if elf.data[offset:offset + 8] != bytes.fromhex("56070100ffffff7f"):
                raise ValueError("unexpected CFL constant representation")
            address = next(p[2] + offset - p[1] for p in elf.ph
                           if p[0] == 1 and p[1] <= offset < p[1] + p[4])
            constants["addr_" + function] = address
            asm += getter(function, "addr_" + function)
        patches = [(0xcb4, bytes.fromhex("2de9f043"), "zq_screen_property"),
                   (0xed8, bytes.fromhex("2de9f04f"), "zq_screen_frame")]
        absolute = {}
    elif name == "z-tesla":
        cfiles = ["codec_mode.c"]
        # The first AVC test (0x67) is untouched. Extend its second test to
        # accept AVC 0x27 and HEVC layer-0 VPS/SPS, retaining both AVC variants.
        asm += """
.global zq_metadata_header
.thumb_func
zq_metadata_header:
    cmp r3, #0x27
    beq metadata_yes
    cmp r3, #0x40
    beq metadata_yes
    cmp r3, #0x42
    beq metadata_yes
    b.w metadata_no
.global zq_mode_exit
.thumb_func
zq_mode_exit:
    mov r0, r3
    ldr r1, [r7, #4]
    bl zq_mode_saved
    adds r7, #0x48
    b.w mode_resume
.global zq_mode_runtime_result
.thumb_func
zq_mode_runtime_result:
    cmp r0, #0
    beq 1f
    ldr r3, [r7]
    movs r2, #1
    str r2, [r3, #0x44]
1:  mov r3, r0
    b.w mode_runtime_resume
.global zq_mode_init_result
.thumb_func
zq_mode_init_result:
    cmp r0, #0
    beq 1f
    movs r2, #1
    str r2, [r7, #0x18]
    ldr r3, [r7]
    str r2, [r3, #0x44]
1:  mov r3, r0
    str r3, [r7, #0xc]
    b.w mode_init_resume
.global zq_mode_load_exit
.thumb_func
zq_mode_load_exit:
    mov r0, r3
    bl zq_mode_loaded
    adds r7, #0x40
    b.w mode_load_resume
"""
        # Our converter bundles HEVC parameter sets with every IRAP. The stock
        # WebSocket callback's short-AVC-header merge already skips HEVC, so it
        # needs no instruction change.
        patches = [(0x2fb3e, bytes.fromhex("272b31d1"), "zq_metadata_header"),
                   (0x1dbaa, bytes.fromhex("18464837"), "zq_mode_exit"),
                   (0x230d4, bytes.fromhex("00231846"), "zq_mode_runtime_result"),
                   (0x22d0e, bytes.fromhex("0023fb60"), "zq_mode_init_result"),
                   (0x1db30, bytes.fromhex("18464037"), "zq_mode_load_exit")]
        absolute = {"metadata_yes": 0x2fb43, "metadata_no": 0x2fba7,
                    "mode_resume": 0x1dbaf, "mode_runtime_resume": 0x230d9,
                    "mode_init_resume": 0x22d13, "zq_write_file": 0x20b47,
                    "mode_load_resume": 0x1db35}
    elif name == "libzlink-main.so":
        # Every video socket establishment sends a focus update. Even if focus
        # is unchanged, a new browser decoder needs a fresh random-access frame.
        # Use the vendor's session-aware wrapper, which is a no-op without an
        # active CarPlay session. Preserve r0-r11; r12 is bridge scratch per AAPCS.
        asm += """
.global zq_focus_exit
.thumb_func
zq_focus_exit:
    push {r0-r3, r12, lr}
    bl request_keyframe
    pop {r0-r3, r12, lr}
    adds r7, #8
    b.w focus_resume
"""
        patches = [(0x5a498, bytes.fromhex("00bf0837"), "zq_focus_exit")]
        absolute = {"request_keyframe": 0x5a6bd, "focus_resume": 0x5a49d}
    else:
        raise ValueError(name)
    if keep_video_focus and name == "z-tesla":
        # Only the current video socket's CLOSED branch reaches this site,
        # after connected was cleared. Mode 2 keeps the previously requested
        # focus and skips this browser-close focus notification. Explicit
        # focus requests and all other disconnect/session handling are intact.
        asm += """
.global zq_video_close_focus
.thumb_func
zq_video_close_focus:
    push {r0, r1}
    ldr r0, [r3, #0x44]
    cmp r0, #2
    pop {r0, r1}
    beq 1f
    strb.w r2, [r3, #0x48]
    b.w video_close_focus_resume
1:  b.w video_close_focus_skip
"""
        patches += [(0x219a8, bytes.fromhex("83f84820"), "zq_video_close_focus")]
        absolute.update(video_close_focus_resume=0x219ad, video_close_focus_skip=0x219bb)
    if diagnostics and name == "libzlink-main.so":
        cfiles += ["stream_diag_main.c"]
        constants["addr_zq_diag_session"] = 0xf1118
        asm += getter("zq_diag_session", "addr_zq_diag_session", True)
        if asm.count("    bl request_keyframe\n") != 1:
            raise ValueError("expected exactly one focus keyframe call")
        asm = asm.replace("    bl request_keyframe\n", "    ldr r0, [r7, #4]\n    bl zq_diag_focus\n")
        asm += """
.global zq_diag_phone_entry
.thumb_func
zq_diag_phone_entry:
    push {r0-r3, r12, lr}
    bl zq_diag_phone
    pop {r0-r3, r12, lr}
    push {r4, r5, r7, lr}
    sub sp, #0x18
    b.w diag_phone_resume
"""
        patches += [(0x587fc, bytes.fromhex("b0b586b0"), "zq_diag_phone_entry")]
        absolute["diag_phone_resume"] = 0x58801
    if diagnostics and name == "z-tesla":
        cfiles += ["stream_diag_sender.c"]
        asm += """
.global zq_diag_sender_entry
.thumb_func
zq_diag_sender_entry:
    push {r0-r3, r12, lr}
    bl zq_diag_sender
    pop {r0-r3, r12, lr}
    push {r4-r7, lr}
    sub sp, #0x34
    b.w diag_sender_resume
.global zq_diag_write_entry
.thumb_func
zq_diag_write_entry:
    push {r0-r3, r12, lr}
    mov r0, r1
    mov r1, r2
    bl zq_diag_output
    pop {r0-r3, r12, lr}
    bl diag_original_write
    b.w diag_write_resume
"""
        patches += [(0x2fb0c, bytes.fromhex("f0b58db0"), "zq_diag_sender_entry"),
                    (0x218d6, bytes.fromhex("1ff08efa"), "zq_diag_write_entry")]
        absolute.update(diag_sender_resume=0x2fb11, diag_original_write=0x40df7,
                        diag_write_resume=0x218db)
    # Own Thumb bridges preserve r0-r3 and lr, then tail-jump through a
    # PC-relative address into the original ARM PLT (or Thumb continuation).
    # This avoids absolute interworking thunks under library ASLR.
    required = {
        "libAirPlay.so": "CFStringCreateWithCString CFDictionaryCreateMutable CFDictionarySetValue CFDictionaryGetValue CFArrayCreateMutable CFArrayAppendValue CFRelease fopen fclose",
        "libScreenStream.so": "ScreenStreamGetContext CFEqual CFGetTypeID CFDataGetTypeID CFGetInt64 CFDataGetBytePtr CFDataGetLength ScreenStreamSend_CB malloc free memcpy",
        "z-tesla": "fopen fclose unlink access fwrite system",
        "libzlink-main.so": "",
    }[name].split()
    if diagnostics and name == "libzlink-main.so":
        required += "fopen fclose fwrite access remove AirPlayReceiverSessionForceKeyFrame".split()
    imports = elf.imports()
    for symbol, address in {**{s: imports[s] for s in required}, **absolute}.items():
        if not symbol.replace("_", "").isalnum():
            continue
        constants["addr_" + symbol] = address
        asm += f"""
.balign 4
.global {symbol}
.hidden {symbol}
.type {symbol},%function
.thumb_func
{symbol}:
    ldr.w r12, 1f
0:  add r12, pc
    bx r12
.balign 4
1:  .word addr_{symbol} - (0b + 4)
"""
    directory.mkdir(parents=True, exist_ok=True)
    stub = directory / "hooks.S"
    stub.write_text(asm)
    compiler, linker = tool("clang", "ZQ_CLANG"), tool("ld.lld", "ZQ_LLD")
    flags = ["--target=armv7-linux-gnueabihf", "-mthumb", "-mcpu=cortex-a7",
             "-mfpu=vfpv3-d16", "-mfloat-abi=hard", "-Oz", "-ffreestanding",
             "-fPIC", "-fno-builtin", "-fno-stack-protector", "-fvisibility=hidden",
             "-ffunction-sections", "-fdata-sections", "-Wall", "-Wextra", "-Werror"]
    objects = []
    for source in [stub, *(SOURCES / f for f in cfiles)]:
        obj = directory / (source.stem + ".o")
        subprocess.run([compiler, *flags, "-c", str(source), "-o", str(obj)], check=True)
        objects.append(str(obj))
    script = directory / "payload.ld"
    definitions = "\n".join(f"{k} = {v};" for k, v in constants.items()
                              if k.replace("_", "").isalnum())
    script.write_text(definitions + f"""\nSECTIONS {{
      . = {base};
      .zqhevc : {{ *(.text*) *(.rodata*) }}
      .forbidden : {{ *(.data*) *(.bss*) *(.got*) *(.init_array*) *(.fini_array*) }}
      /DISCARD/ : {{ *(.ARM.exidx*) *(.ARM.extab*) *(.comment*) *(.note*) }}
      ASSERT(SIZEOF(.forbidden) == 0, "payload must have no absolute data/GOT/BSS")
    }}""")
    output = directory / "payload.elf"
    subprocess.run([linker, "-T", str(script), "--no-undefined", "--fatal-warnings", "--pic-veneer",
                    "-e", patches[0][2], *objects, "-o", str(output)], check=True)
    linked = ELF(output.read_bytes())
    if any(s[1] in (4, 9) and s[5] for s in linked.sh):
        raise ValueError("payload contains unhandled relocations")
    symbols = {s[0]: s[1] for s in linked.symbols()}
    return base, linked.section(".zqhevc"), patches, symbols


def patch_native(name, original, directory, diagnostics=False, keep_video_focus=False):
    if name not in PINNED or hashlib.sha256(original).hexdigest() != PINNED[name]:
        raise ValueError(f"{name}: stock SHA-256 mismatch; refusing patch")
    elf = ELF(original)
    base, code, patches, symbols = payload(elf, name, directory, diagnostics, keep_video_focus)
    result = bytearray(original)
    changes = []
    for address, old, symbol in patches:
        offset = elf.offset(address, len(old))
        # Each replacement is at a hash-pinned address with an exact expected
        # match count of one in that explicitly bounded instruction window.
        if original[offset:offset + len(old)].count(old) != 1:
            raise ValueError(f"{name}: unexpected instructions at {address:#x}")
        new = branch(address, symbols[symbol])
        result[offset:offset + len(old)] = new
        changes.append({"address": hex(address), "old": old.hex(), "new": new.hex(),
                        "handler": symbol, "target": hex(symbols[symbol] & ~1)})
    notes = [i for i, p in enumerate(elf.ph) if p[0] == 4]
    if len(notes) != 1:
        raise ValueError("expected one replaceable PT_NOTE")
    offset = align(len(result))
    result.extend(b"\0" * (offset - len(result)))
    result.extend(code)
    struct.pack_into("<8I", result, elf.e[5] + notes[0] * 32,
                     1, offset, base, base, len(code), len(code), 5, 0x10000)
    # Add an allocated section so normal disassemblers can inspect the payload.
    strings = elf.shstr + b".zqhevc\0"
    shstr_offset = len(result)
    result.extend(strings)
    result.extend(b"\0" * (align(len(result), 4) - len(result)))
    table = len(result)
    elf.sh[elf.e[13]][4:6] = [shstr_offset, len(strings)]
    elf.sh.append([len(elf.shstr), 1, 6, base, offset, len(code), 0, 0, 4, 0])
    for s in elf.sh:
        result.extend(struct.pack("<10I", *s))
    elf.e[6], elf.e[12] = table, len(elf.sh)
    struct.pack_into("<16sHHIIIIIHHHHHH", result, 0, *elf.e)
    patched = ELF(result)
    if patched.imports() != elf.imports():
        raise ValueError("patch changed imports")
    if any(p[0] == 1 and p[6] & 3 == 3 for p in patched.ph):
        raise ValueError("writable executable load segment")
    report = {"file": name, "stock_sha256": PINNED[name],
              "patched_sha256": hashlib.sha256(result).hexdigest(),
              "payload_address": hex(base), "payload_bytes": len(code), "patches": changes}
    if diagnostics:
        report["diagnostics"] = True
    if keep_video_focus:
        report["keep_video_focus"] = True
    (directory / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    return bytes(result), report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input-dir", required=True, type=Path)
    p.add_argument("--out-dir", required=True, type=Path)
    p.add_argument("--diagnostics", action="store_true")
    p.add_argument("--keep-video-focus", action="store_true")
    args = p.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    reports = []
    for name in PINNED:
        data, report = patch_native(name, (args.input_dir / name).read_bytes(),
                                    args.out_dir / (name + ".build"), args.diagnostics,
                                    args.keep_video_focus)
        (args.out_dir / name).write_bytes(data)
        reports.append(report)
    print(json.dumps(reports, indent=2))


if __name__ == "__main__":
    main()
