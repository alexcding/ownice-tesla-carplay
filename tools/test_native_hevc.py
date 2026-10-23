#!/usr/bin/env python3
"""Offline ARM tests of generated patches, with mocked receiver/CF/memory APIs.

Requires unicorn (pip install unicorn) and an already-built --out-dir. Vendor
code is never run beyond the explicitly patched instruction windows. Tests
exercise actual compiled ARM payloads at two independent library load bases.
"""
import argparse
import json
from pathlib import Path
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_hevc import ELF, PINNED, branch

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
    UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
    UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11,
    UC_ARM_REG_R12, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC)

REGS = [UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
        UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
        UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11,
        UC_ARM_REG_R12]
BUILD = Path("build/hevc-native")


def avcc(length=4):
    return bytes([1, 100, 0, 32, 0xfc | (length - 1), 0xe1, 0, 3,
                  0x67, 100, 32, 1, 0, 2, 0x68, 0xee])


def hvcc(length=4):
    b = bytearray(23)
    b[0] = 1; b[1] = 1; b[12] = 120; b[13] = 0xf0
    b[15] = b[16] = 0xfc; b[17] = b[18] = 0xf8
    b[21] = 0xfc | (length - 1); b[22] = 3
    for kind in (32, 33, 34):
        b += bytes([0x80 | kind, 0, 1, 0, 3, kind << 1, 1, 0xaa])
    return bytes(b)


def packet(*nals, length=4):
    return b"".join(len(n).to_bytes(length, "big") + n for n in nals)


def annex(*nals):
    return b"".join(b"\0\0\0\1" + n for n in nals)


FLAG = "/data/local/tmp/zq_hevc"
MODE_FILE = "/data/local/tmp/local_is_h264"
CODEC_STATUS = "/usr/z-loader/zjinnova/web/zq-codec.txt"
DIAG_ROOT = "/usr/z-loader/zjinnova/web/zq-video-"
REPORT_UUID = b"ownice-zq-diag01"
DIAG_FILES = {k: DIAG_ROOT + k + ".txt" for k in ("request", "phone", "sender", "output")}


class Machine:
    def __init__(self, name, base=0, files=None):
        self.base = base
        self.elf = ELF((BUILD / name).read_bytes())
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        for p in self.elf.ph:
            if p[0] == 1:
                lo = (base + p[2]) & ~4095
                size = ((base + p[2] + p[5] + 4095) & ~4095) - lo
                self.uc.mem_map(lo, size)
                self.uc.mem_write(base + p[2], self.elf.data[p[1]:p[1] + p[4]])
        self.uc.mem_map(0x70000000, 0x1000000)
        self.uc.mem_map(0x71000000, 0x100000)
        self.uc.mem_map(0x72000000, 0x2000)
        self.top = 0x710f0000
        self.next = 0x70000000
        self.objects = {}
        self.allocations = set()
        self.fail_malloc = False
        self.fail_cf = False
        self.files = files if files is not None else {}
        self.handles = {}
        self.fail_open = False
        self.fail_close = False
        self.fail_unlink = False
        self.fail_persist = False
        self.persisted = []
        self.file_calls = []
        self.json_node = 0
        self.sent = []
        self.reports = []
        self.callback_order = []
        self.keyframes = []
        self.focus_changes = []
        self.force_error = 0
        self.native_writes = []
        self.completed = []
        self.context = self.allocate(bytes(24))
        self.apis = {base + v: k for k, v in self.elf.imports().items()}
        if name == "z-tesla":
            self.apis[base + 0x1eb64] = "stock_video_focus"
            self.apis[base + 0x20b46] = "stock_write_file"
            self.apis[base + 0x20b08] = "stock_read_file"
            self.apis[base + 0x27b12] = "json_get"
            self.apis[base + 0x226d8] = "stock_init_aux"
            self.apis[base + 0x40df6] = "native_lws_write"
        if name == "libzlink-main.so":
            self.apis[base + 0x5a3bc] = "stock_cp_resource_mode"
        self.stop = 0x72000000
        self.completed_address = 0x72001000
        self.uc.hook_add(UC_HOOK_CODE, self.hook)
        self.boundary = None
        self.hit_stop = False
        if name == "libAirPlay.so":
            for i, key in enumerate(("kCFLDictionaryKeyCallBacksCFLTypes",
                                     "kCFLDictionaryValueCallBacksCFLTypes",
                                     "kCFLArrayCallBacksCFLTypes")):
                self.put32(base + self.elf.got(key), 0x700ff000 + i * 16)

    def allocate(self, data):
        at = self.next
        self.next += (len(data) + 15) & ~15 or 16
        if self.next > 0x70f00000:
            raise AssertionError("test heap exhausted")
        self.uc.mem_write(at, bytes(data))
        return at

    def object(self, kind, value):
        at = self.allocate(bytes(16))
        self.objects[at] = [kind, value, 1]
        return at

    def string(self, at):
        if at in self.objects:
            return self.objects[at][1]
        # Stock CFL constant string object, eight-byte header.
        return self.cstring(at + 8)

    def cstring(self, at):
        out = bytearray()
        for i in range(1024):
            b = self.uc.mem_read(at + i, 1)[0]
            if not b: return out.decode()
            out.append(b)
        raise AssertionError("unterminated string")

    def put32(self, at, *values):
        self.uc.mem_write(at, struct.pack("<" + "I" * len(values), *values))

    def get32(self, at, n=1):
        return struct.unpack("<" + "I" * n, self.uc.mem_read(at, 4 * n))

    def release(self, at):
        obj = self.objects[at]; obj[2] -= 1
        if obj[2] == 0:
            if obj[0] == "dict":
                for v in obj[1].values(): self.release(v)
            elif obj[0] == "array":
                for v in obj[1]: self.release(v)
        if obj[2] < 0: raise AssertionError("double CFRelease")

    def api(self, name, a, sp):
        if name == "stock_video_focus":
            self.focus_changes.append((a[0], a[1])); return 0
        if name == "puts": return 0
        if name == "stock_cp_resource_mode":
            self.focus_changes.append(a[0]); return 0
        if name == "AirPlayReceiverSessionForceKeyFrame":
            self.keyframes.append(tuple(a[:3])); return self.force_error
        if name == "native_lws_write":
            self.native_writes.append((a[0], bytes(self.uc.mem_read(a[1], a[2])), a[3]))
            return a[2]
        if name == "fopen":
            path, mode = self.cstring(a[0]), self.cstring(a[1])
            self.file_calls.append((name, path, mode))
            assert path in (FLAG, CODEC_STATUS, *DIAG_FILES.values()) and mode in ("r", "w")
            if self.fail_open or (mode == "r" and path not in self.files): return 0
            if mode == "w": self.files[path] = b""
            handle = self.allocate(bytes(16)); self.handles[handle] = path
            return handle
        if name == "fclose":
            self.handles.pop(a[0])
            return -1 if self.fail_close else 0
        if name in ("unlink", "remove"):
            path = self.cstring(a[0]); assert path in (FLAG, CODEC_STATUS, *DIAG_FILES.values())
            self.file_calls.append((name, path))
            if self.fail_unlink or path not in self.files: return -1
            del self.files[path]; return 0
        if name == "fwrite":
            assert a[1] == 1
            self.files[self.handles[a[3]]] += bytes(self.uc.mem_read(a[0], a[2]))
            return a[2]
        if name == "json_get":
            assert self.cstring(a[1]) == "is_h264_mode"
            return self.json_node
        if name == "stock_init_aux": return 0
        if name == "access":
            assert a[1] == 0
            return 0 if self.cstring(a[0]) in self.files else -1
        if name == "memset":
            self.uc.mem_write(a[0], bytes([a[1] & 255]) * a[2]); return a[0]
        if name == "sprintf":
            assert self.cstring(a[1]) == "%d"
            value = a[2] if a[2] < 0x80000000 else a[2] - 0x100000000
            raw = str(value).encode()
            self.uc.mem_write(a[0], raw + b"\0"); return len(raw)
        if name == "strlen": return len(self.cstring(a[0]).encode())
        if name == "stock_write_file":
            path = self.cstring(a[0]); assert path == MODE_FILE
            raw = bytes(self.uc.mem_read(a[1], a[2])); self.persisted.append(raw)
            if self.fail_persist: return -1
            self.files[path] = raw; return len(raw)
        if name == "stock_read_file":
            path = self.cstring(a[0]); assert path == MODE_FILE
            if path not in self.files: return -1
            raw = self.files[path][:a[2]]
            self.uc.mem_write(a[1], raw)
            return len(raw)
        if name == "atoi":
            try: return int(self.cstring(a[0]).strip())
            except ValueError: return 0
        if name == "system":
            assert self.cstring(a[0]) == "sync"  # observed; never run on host
            return 0
        if name == "printf":
            assert self.cstring(a[0]).startswith(("T113_h264_mode_write:", "T113_h264_mode_read:"))
            return 0
        if name == "malloc":
            if self.fail_malloc: return 0
            at = self.allocate(bytes(a[0])); self.allocations.add(at); return at
        if name == "free":
            if a[0]: self.allocations.remove(a[0])
            return 0
        if name == "memcpy":
            self.uc.mem_write(a[0], bytes(self.uc.mem_read(a[1], a[2]))); return a[0]
        if name == "ScreenStreamGetContext": return self.context
        if name == "CFEqual": return int(self.string(a[0]) == self.string(a[1]))
        if name == "CFDataGetTypeID": return 2
        if name == "CFGetTypeID": return 2 if self.objects[a[0]][0] == "data" else 3
        if name == "CFDataGetLength": return self.objects[a[0]][1][1]
        if name == "CFDataGetBytePtr": return self.objects[a[0]][1][0]
        if name == "CFGetInt64":
            self.put32(a[1], 0)
            self.uc.reg_write(UC_ARM_REG_R1, 0)
            return self.objects[a[0]][1]
        if name == "ScreenStreamSend_CB":
            ticks = self.get32(sp, 2)
            data = bytes(self.uc.mem_read(a[0], a[1]))
            if data[4:7] == b"\x4e\x01\x05" and data[8:24] == REPORT_UUID:
                self.callback_order.append("report")
                self.reports.append(data[24:-1].decode())  # diagnostic builds only
            else:
                self.callback_order.append("frame")
                self.sent.append((data, a[2], a[3], ticks[0] | ticks[1] << 32))
            return 0
        if name == "CFStringCreateWithCString":
            if self.fail_cf: return 0
            assert a[0] == 0 and a[2] == 0x08000100
            return self.object("str", self.cstring(a[1]))
        if name == "CFDictionaryCreateMutable":
            assert a == [0, 0, 0x700ff000, 0x700ff010]
            return self.object("dict", {})
        if name == "CFArrayCreateMutable":
            assert a[:3] == [0, 0, 0x700ff020]
            return self.object("array", [])
        if name == "CFDictionarySetValue":
            d = self.objects[a[0]][1]; k = self.string(a[1])
            if k in d: self.release(d[k])
            d[k] = a[2]; self.objects[a[2]][2] += 1
            return 0
        if name == "CFDictionaryGetValue":
            return self.objects[a[0]][1].get(self.string(a[1]), 0)
        if name == "CFArrayAppendValue":
            self.objects[a[0]][1].append(a[1]); self.objects[a[1]][2] += 1
            return 0
        if name == "CFRelease": self.release(a[0]); return 0
        raise AssertionError("unexpected receiver API: " + name)

    def hook(self, uc, address, size, unused):
        if address == self.stop or address == self.boundary:
            self.hit_stop = True; uc.emu_stop(); return
        if address == self.completed_address:
            self.completed.append(uc.reg_read(UC_ARM_REG_R0))
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR)); return
        if address in self.apis and self.apis[address] == "cp_video_focus":
            # Execute the real patched CarPlay callback, reached through the
            # stock focus dispatcher PLT, rather than stubbing out the hook.
            uc.reg_write(UC_ARM_REG_PC, (self.base + 0x5a44c) | 1); return
        if address in self.apis:
            args = [uc.reg_read(r) for r in REGS[:4]]
            sp = uc.reg_read(UC_ARM_REG_SP)
            assert sp % 8 == 0, "AAPCS stack alignment"
            result = self.api(self.apis[address], args, sp)
            uc.reg_write(UC_ARM_REG_R0, result & 0xffffffff)
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

    def run(self, address, args=(), stack=(), boundary=None):
        for i, r in enumerate(REGS): self.uc.reg_write(r, 0x11110000 + i)
        for r, a in zip(REGS, args): self.uc.reg_write(r, a)
        self.uc.reg_write(UC_ARM_REG_SP, self.top)
        self.uc.reg_write(UC_ARM_REG_LR, self.stop | 1)
        if stack: self.put32(self.top, *stack)
        self.boundary = boundary
        self.hit_stop = False
        self.uc.emu_start((self.base + address) | 1, 0, count=2000000)
        assert self.hit_stop, "ARM handler failed to return within instruction budget"
        return self.uc.reg_read(UC_ARM_REG_R0)

    def preserved(self):
        return [self.uc.reg_read(r) for r in REGS[4:12]]

    def data(self, raw):
        return self.object("data", (self.allocate(raw), len(raw)))

    def property(self, key, value):
        key = self.object("str", key)
        if isinstance(value, bytes): value = self.data(value)
        else: value = self.object("int", value)
        ret = self.run(0xcb4, (0x1234, 0, key, 0), (value,))
        assert self.preserved() == [0x11110000 + i for i in range(4, 12)]
        return ret

    def frame(self, raw):
        ticks = 0x123456789abcdef0
        ret = self.run(0xed8, (0x1234, self.allocate(raw), len(raw), 0),
                       (ticks & 0xffffffff, ticks >> 32, 0,
                        self.completed_address | 1, 0x76543210))
        assert self.preserved() == [0x11110000 + i for i in range(4, 12)]
        assert self.uc.reg_read(UC_ARM_REG_SP) == self.top
        return ret


class NativeHEVCTests(unittest.TestCase):
    def test_compressed_browser_close_keeps_focus_and_other_modes_keep_stock_path(self):
        manifest = json.loads((BUILD / "z-tesla.build/manifest.json").read_text())
        if not manifest.get("keep_video_focus"): self.skipTest("focus experiment only")
        self.assertEqual(manifest.get("focus_retained_modes"), [1, 2])
        for base in (0, 0x18000000):
            for mode in (0, 1, 2, 3):
                for previous in (0, 1):
                    m = Machine("z-tesla", base)
                    ctx = m.allocate(bytes(0x200)); m.put32(ctx + 0x44, mode)
                    m.uc.mem_write(ctx + 0x48, bytes([previous, 0xaa, 0xbb, 0xcc]))
                    args = (0x12345678, 0x23456789, 1, ctx)
                    resume = 0x219ba if mode in (1, 2) else 0x219ac
                    m.run(0x219a8, args, boundary=base + resume)
                    self.assertEqual(bytes(m.uc.mem_read(ctx + 0x48, 4)),
                                     bytes([previous if mode in (1, 2) else 1, 0xaa, 0xbb, 0xcc]))
                    self.assertEqual([m.uc.reg_read(r) for r in REGS[:4]], list(args))
                    # Original focus call and PC-relative bridges use AAPCS
                    # scratch r12. Check the live/callee-saved registers.
                    self.assertEqual([m.uc.reg_read(r) for r in REGS[4:12]],
                                     [0x11110000 + i for i in range(4, 12)])
                    self.assertEqual(m.uc.reg_read(UC_ARM_REG_SP), m.top)

    def test_compressed_close_still_deactivates_client_without_sending_focus_release(self):
        manifest = json.loads((BUILD / "z-tesla.build/manifest.json").read_text())
        if not manifest.get("keep_video_focus"): self.skipTest("focus experiment only")
        for base in (0, 0x18000000):
            for mode in (0, 1, 2):
                m = Machine("z-tesla", base)
                ctx = m.allocate(bytes(0x200)); frame = m.allocate(bytes(0x40))
                m.put32(ctx + 0x44, mode); m.put32(ctx + 0x6c, 1)
                m.put32(frame + 0x18, ctx)
                args = [0x11110000 + i for i in range(8)]; args[7] = frame
                m.run(0x21994, args, boundary=base + 0x219ba)
                self.assertEqual(m.get32(ctx + 0x6c), (0,))
                self.assertEqual(m.uc.mem_read(ctx + 0x48, 1)[0], 0 if mode in (1, 2) else 1)
                self.assertEqual(m.focus_changes, [] if mode in (1, 2) else [(ctx, 1)])
                self.assertEqual(m.uc.reg_read(UC_ARM_REG_SP), m.top)

    def require_diagnostics(self):
        manifest = json.loads((BUILD / "libzlink-main.so.build/manifest.json").read_text())
        if not manifest.get("diagnostics"): self.skipTest("diagnostic build only")

    def test_diagnostic_records_request_result_and_resets_checkpoints(self):
        self.require_diagnostics()
        for base in (0, 0x18000000):
            m = Machine("libzlink-main.so", base)
            m.put32(base + 0xf1118, 0x12345678)
            for p in DIAG_FILES.values(): m.files[p] = b"old checkpoint"
            m.force_error = -6743
            m.run(0x5a44c, (0,))
            values = [int(x, 16) for x in m.files[DIAG_FILES["request"]].split()]
            self.assertEqual(values, [0, 1, (-6743) & 0xffffffff])
            self.assertTrue(all(DIAG_FILES[k] not in m.files for k in ("phone", "sender", "output")))
            m.put32(base + 0xf1118, 0)
            m.run(0x5a44c, (1,))
            self.assertEqual([int(x, 16) for x in m.files[DIAG_FILES["request"]].split()], [1, 0, 0xffffffff])
            self.assertFalse(m.handles)

    def test_diagnostic_input_hooks_record_once_and_replay_original_prologues(self):
        self.require_diagnostics()
        data = annex(b"\x40\x01\xaa", b"\x26\x01\xbb")
        for base in (0, 0x18000000):
            for name, entry, resume, stack_size, key in (
                ("libzlink-main.so", 0x587fc, 0x58800, 40, "phone"),
                ("z-tesla", 0x2fb0c, 0x2fb10, 72, "sender")):
                m = Machine(name, base)
                ctx = m.allocate(bytes(0x200)); m.put32(ctx + 0x44, 2); m.put32(ctx + 0x6c, 1)
                args = (m.allocate(data), len(data), 0, ctx)
                m.run(entry, args, boundary=base + resume)
                state = (2 << 16 | 1) if key == "sender" else 0
                self.assertEqual([int(x, 16) for x in m.files[DIAG_FILES[key]].split()], [len(data), 0x40, state])
                self.assertEqual([m.uc.reg_read(r) for r in REGS[:4]], list(args))
                self.assertEqual(m.uc.reg_read(UC_ARM_REG_SP), m.top - stack_size)
                previous = m.files[DIAG_FILES[key]]
                m.run(entry, (args[0], 5, 0, ctx), boundary=base + resume)
                self.assertEqual(m.files[DIAG_FILES[key]], previous)
                self.assertFalse(m.handles)

    def test_diagnostic_output_preserves_original_websocket_write(self):
        self.require_diagnostics()
        data = annex(b"\x40\x01\xaa", b"\x26\x01\xbb")
        for base in (0, 0x18000000):
            m = Machine("z-tesla", base)
            raw = m.allocate(data)
            result = m.run(0x218d6, (0x1234, raw, len(data), 1), boundary=base + 0x218da)
            self.assertEqual(result, len(data))
            self.assertEqual(m.native_writes, [(0x1234, data, 1)])
            self.assertEqual([int(x, 16) for x in m.files[DIAG_FILES["output"]].split()], [len(data), 0x40, 0])
            self.assertEqual(m.uc.reg_read(UC_ARM_REG_SP), m.top)

    def test_reports_do_not_consume_sender_or_output_checkpoints(self):
        self.require_diagnostics()
        text = b"zq events flags=00000404"
        report = b"\0\0\0\1\x4e\x01\x05" + bytes([16 + len(text)]) + REPORT_UUID + text + b"\x80"
        for base in (0, 0x18000000):
            m = Machine("z-tesla", base)
            ctx = m.allocate(bytes(0x200))
            raw = m.allocate(report)
            args = (raw, len(report), 0, ctx)
            m.run(0x2fb0c, args, boundary=base + 0x2fb10)
            self.assertNotIn(DIAG_FILES["sender"], m.files)
            m.run(0x218d6, (0x1234, raw, len(report), 1), boundary=base + 0x218da)
            self.assertNotIn(DIAG_FILES["output"], m.files)
            self.assertEqual(m.native_writes, [(0x1234, report, 1)])
            data = annex(b"\x40\x01\xaa")
            raw = m.allocate(data)
            m.run(0x2fb0c, (raw, len(data), 0, ctx), boundary=base + 0x2fb10)
            m.run(0x218d6, (0x1234, raw, len(data), 1), boundary=base + 0x218da)
            for key in ("sender", "output"):
                self.assertEqual([int(x, 16) for x in m.files[DIAG_FILES[key]].split()][:2], [len(data), 0x40])

    def test_focus_dispatcher_repeated_carplay_focus_reaches_real_keyframe_hook(self):
        for base in (0, 0x18000000):
            for phone_type in (1, 5):
                m = Machine("libzlink-main.so", base)
                ctx = m.allocate(bytes(0xb00))
                m.put32(ctx + 0x5bc, phone_type)
                m.put32(base + 0xf1118, 0x12345678)
                for _ in range(2):
                    m.run(0x4df04, (ctx, 0, 0xffffffff, 0xffffffff))
                self.assertEqual(m.keyframes, [(0x12345678, 0, 0)] * 2)
                self.assertEqual(m.uc.reg_read(UC_ARM_REG_SP), m.top)

    def test_carplay_focus_requests_fresh_frame_even_when_focus_unchanged(self):
        for base in (0, 0x18000000):
            m = Machine("libzlink-main.so", base)
            session = 0x12345678
            m.put32(base + 0xf1118, session)
            for focus in (0, 0, 1, 0):
                before = len(m.keyframes)
                m.run(0x5a44c, (focus,))
                self.assertEqual(m.keyframes[before:], [(session, 0, 0)])
                self.assertEqual(m.uc.reg_read(UC_ARM_REG_SP), m.top)
                self.assertEqual(m.preserved(), [0x11110000 + i for i in range(4, 12)])
            m.put32(base + 0xf1118, 0)
            m.run(0x5a44c, (0,))
            self.assertEqual(len(m.keyframes), 4)  # no active phone: no request

    def test_focus_epilogue_keeps_original_registers_and_stack(self):
        for base in (0, 0x18000000):
            m = Machine("libzlink-main.so", base)
            m.put32(base + 0xf1118, 0x12345678)
            m.put32(m.top + 8, 0x11110007, m.stop | 1)
            args = [0x11110000 + i for i in range(13)]
            args[7] = m.top
            m.run(0x5a498, args)
            self.assertEqual([m.uc.reg_read(r) for r in REGS[:12]], [0x11110000 + i for i in range(12)])
            self.assertEqual(m.uc.reg_read(UC_ARM_REG_SP), m.top + 16)
            self.assertEqual(m.keyframes, [(0x12345678, 0, 0)])

    def test_converter_and_fallback_at_two_load_addresses(self):
        for base in (0, 0x18000000):
            for length in (1, 2, 4):
                m = Machine("libScreenStream.so", base)
                self.assertEqual(m.property("widthPixels", 1920), 0)
                self.assertEqual(m.property("heightPixels", 1100), 0)
                self.assertEqual(m.property("avcc", hvcc(length)), 0)
                header = annex(*[bytes([t << 1, 1, 0xaa]) for t in (32, 33, 34)])
                for kind in range(16, 22):
                    nal = bytes([kind << 1, 1, 0xbb])
                    self.assertEqual(m.frame(packet(nal, length=length)), 0)
                    self.assertEqual(m.sent[-1], (header + annex(nal), 1920, 1100, 0x123456789abcdef0))
                delta = bytes([2, 1, 0xcc])
                self.assertEqual(m.frame(packet(delta, length=length)), 0)
                self.assertEqual(m.sent[-1][0], annex(delta))
                self.assertEqual(m.property("avcc", avcc(length)), 0)
                idr = b"\x65\x88"
                self.assertEqual(m.frame(packet(idr, length=length)), 0)
                self.assertEqual(m.sent[-1][0], annex(b"\x67\x64\x20", b"\x68\xee", idr))
                self.assertEqual(len(m.allocations), 1)  # current config header only
                self.assertEqual(m.completed, [0x76543210] * 8)

    def test_multiple_nals_keyframe_not_last(self):
        m = Machine("libScreenStream.so", 0x18000000)
        self.assertEqual(m.property("avcc", hvcc()), 0)
        nals = (b"\x26\x01\xaa", b"\x02\x01\xbb", b"\x4e\x01\xcc")
        self.assertEqual(m.frame(packet(*nals)), 0)
        self.assertTrue(m.sent[-1][0].endswith(annex(*nals)))
        self.assertTrue(m.sent[-1][0].startswith(b"\0\0\0\1\x40\x01"))

    def test_boxed_format_descriptions(self):
        def box(name, value): return struct.pack(">I", 8 + len(value)) + name + value
        for name in (b"hvc1", b"hev1"):
            config = box(name, bytes(78) + box(b"hvcC", hvcc()))
            for value in (config, box(b"stsd", bytes(4) + struct.pack(">I", 1) + config)):
                m = Machine("libScreenStream.so")
                self.assertEqual(m.property("avcc", value), 0)
                self.assertEqual(m.frame(packet(b"\x26\x01\xaa")), 0)

    def test_invalid_config_preserves_previous_and_no_leak(self):
        m = Machine("libScreenStream.so")
        self.assertEqual(m.property("avcc", hvcc()), 0)
        # Diagnostic builds count failures in the upper half of the flags word.
        def stable(): ctx = m.get32(m.context, 6); return ctx[:5] + (ctx[5] & 0x1ff,)
        old = stable()
        for malformed in (b"", hvcc()[:-1], avcc()[:-1], hvcc(3), avcc(3), bytes(100)):
            self.assertNotEqual(m.property("avcc", malformed), 0)
            self.assertEqual(stable(), old)
        m.fail_malloc = True
        self.assertNotEqual(m.property("avcc", avcc()), 0)
        self.assertEqual(stable(), old)
        self.assertEqual(len(m.allocations), 1)

    def test_invalid_frames_always_complete_without_output(self):
        m = Machine("libScreenStream.so")
        self.assertEqual(m.property("avcc", hvcc()), 0)
        cases = (b"", b"\0\0", b"\0\0\0\0", packet(b"\x26"), packet(b"\x26\0"),
                 packet(b"\xa6\x01"), b"\xff\xff\xff\xff\x26\x01")
        for bad in cases:
            self.assertNotEqual(m.frame(bad), 0)
        m.fail_malloc = True
        self.assertNotEqual(m.frame(packet(b"\x26\x01")), 0)
        self.assertEqual(len(m.completed), len(cases) + 1)
        self.assertFalse(m.sent)
        self.assertEqual(len(m.allocations), 1)

    def test_diagnostic_converter_defers_events_and_resets_drop_streak(self):
        self.require_diagnostics()
        for base in (0, 0x18000000):
            m = Machine("libScreenStream.so", base)
            # Every rejection must survive a subsequent successful config.
            key = m.object("str", "avcc")
            self.assertNotEqual(m.run(0xcb4, (0x1234, 0, key, 0), (0,)), 0)
            self.assertNotEqual(m.property("avcc", 1), 0)
            self.assertNotEqual(m.property("avcc", b""), 0)
            self.assertNotEqual(m.property("avcc", hvcc(3)), 0)
            m.fail_malloc = True
            self.assertNotEqual(m.property("avcc", hvcc()), 0)
            m.fail_malloc = False
            for _ in range(300):
                self.assertNotEqual(m.frame(packet(b"\x26\x01\xaa")), 0)
            self.assertEqual(m.reports, [])  # no unsafe setup/failure callbacks
            self.assertEqual(m.sent, [])
            self.assertEqual(m.property("avcc", hvcc()), 0)
            m.property("widthPixels", 1392); m.property("heightPixels", 1080)
            self.assertEqual(m.frame(packet(b"\x26\x01\xaa")), 0)
            self.assertEqual(m.callback_order, ["frame", "report"])
            self.assertIn("flags=012c", m.reports[-1])  # not capped at 255
            self.assertIn("ff04", m.reports[-1])  # all event bits plus HEVC/length
            self.assertEqual(m.get32(m.context + 20)[0], 0x104)
            for _ in range(10):
                self.assertNotEqual(m.frame(b""), 0)
                self.assertEqual(m.frame(packet(b"\x26\x01\xaa")), 0)
            self.assertEqual(len(m.reports), 21)  # each streak: drop report, then events
            self.assertTrue(m.reports[-2].startswith("zq dropped flags=00010304 err=ffffe59c len=00000000"))
            self.assertIn("flags=00010304", m.reports[-1])  # good frame reset streak
            # Even a complete counter wrap must retain the pending report.
            m.put32(m.context + 20, 0xffff0104)
            self.assertNotEqual(m.frame(b""), 0)
            self.assertEqual(m.frame(packet(b"\x26\x01\xaa")), 0)
            self.assertIn("flags=00000304", m.reports[-1])
            self.assertEqual(len(m.allocations), 1)

    def test_diagnostic_converter_reports_a_blackout_without_good_frames(self):
        self.require_diagnostics()
        m = Machine("libScreenStream.so", 0x18000000)
        m.property("widthPixels", 1392); m.property("heightPixels", 1080)
        self.assertNotEqual(m.property("avcc", hvcc(3)), 0)  # rejected: no header, every frame drops
        for _ in range(70):
            self.assertNotEqual(m.frame(packet(b"\x26\x01\xaa")), 0)
        self.assertEqual(len(m.reports), 3)  # drops 1, 33 and 65
        self.assertEqual(m.sent, [])
        # 0x4200: config rejected as malformed (code 5) plus pending drops
        for report, streak in zip(m.reports, (1, 0x21, 0x41)):
            self.assertTrue(report.startswith(f"zq dropped flags={streak:04x}4200 err=ffffe59c len=00000007"), report)
            self.assertIn("header=00000000 size=05700438", report)
        self.assertEqual(len(m.allocations), 0)

    def test_bounded_random_malformed_input(self):
        m = Machine("libScreenStream.so", 0x18000000)
        self.assertEqual(m.property("avcc", hvcc()), 0)
        rng = random.Random(6435)
        for i in range(100):
            raw = bytes(rng.getrandbits(8) for _ in range(rng.randrange(100)))
            self.assertNotEqual(m.property("avcc", raw), 0)
            self.assertNotEqual(m.frame(raw), 0)
        self.assertEqual(len(m.allocations), 1)

    def test_info_hook_original_epilogue_and_ownership(self):
        for base in (0, 0x18000000):
            m = Machine("libAirPlay.so", base)
            m.files[FLAG] = b""
            info = m.object("dict", {})
            saved = [0x44440000 + i for i in range(8)]
            m.put32(m.top + 0x10ec, *saved, m.stop | 1)
            # Start immediately before the patched stack-cleanup instruction.
            self.assertEqual(m.run(0xf944, (0, 0, 0, 0, info)), info)
            self.assertEqual(m.preserved(), saved)
            self.assertEqual(m.uc.reg_read(UC_ARM_REG_SP), m.top + 0x1110)
            self.assertIn("hevcInfo", m.objects[info][1])
            m.release(info)
            self.assertTrue(all(o[2] == 0 for o in m.objects.values()))

    def test_setup_hook_writes_original_output_and_preserves_registers(self):
        for base in (0, 0x18000000):
            m = Machine("libAirPlay.so", base)
            m.files[FLAG] = b""
            response = m.object("dict", {})
            out = m.allocate(bytes(4))
            words = [0] * 9; words[8] = response
            m.run(0x1c650, (0x1122, 0x3344, 0x5566, out), words,
                  boundary=base + 0x1c656)
            self.assertEqual(m.get32(out)[0], response)
            self.assertEqual(m.uc.reg_read(UC_ARM_REG_R0), 0x1122)
            self.assertEqual(m.uc.reg_read(UC_ARM_REG_R1), 0x3344)
            self.assertEqual(m.uc.reg_read(UC_ARM_REG_SP), m.top)
            self.assertEqual(m.preserved(), [0x11110000 + i for i in range(4, 12)])
            values = m.objects[m.objects[response][1]["enabledFeatures"]][1]
            self.assertEqual([m.string(x) for x in values], ["hevc"])
            m.release(response)
            self.assertTrue(all(o[2] == 0 for o in m.objects.values()))

    def test_mode_writer_controls_both_advertisements_across_transitions(self):
        for base in (0, 0x18000000):
            files = {}
            writer = Machine("z-tesla", files=files)
            for mode in (1, 2, 1, 2, 0, 0, 2, -1, 3):
                self.assertEqual(writer.run(0x1db40, (mode & 0xffffffff,)), 0)
                self.assertEqual(files[MODE_FILE], str(mode).encode())
                self.assertEqual(FLAG in files, mode == 2)
                self.assertFalse(writer.handles)
                self.assertEqual(writer.preserved(), [0x11110000 + i for i in range(4, 12)])
                self.assertEqual(writer.uc.reg_read(UC_ARM_REG_SP), writer.top)
                receiver = Machine("libAirPlay.so", base, files)
                info = receiver.object("dict", {})
                saved = [0x44440000 + i for i in range(8)]
                receiver.put32(receiver.top + 0x10ec, *saved, receiver.stop | 1)
                self.assertEqual(receiver.run(0xf944, (0, 0, 0, 0, info)), info)
                self.assertEqual("hevcInfo" in receiver.objects[info][1], mode == 2)
                response = receiver.object("dict", {})
                out = receiver.allocate(bytes(4))
                words = [0] * 9; words[8] = response
                receiver.run(0x1c650, (0x1122, 0x3344, 0x5566, out), words,
                             boundary=base + 0x1c656)
                self.assertEqual(receiver.get32(out)[0], response)
                self.assertEqual("enabledFeatures" in receiver.objects[response][1], mode == 2)
                self.assertFalse(receiver.handles)
                receiver.release(info); receiver.release(response)
                self.assertTrue(all(o[2] == 0 for o in receiver.objects.values()))

    def test_failed_stock_save_leaves_flag_unchanged_and_records_failure(self):
        for enabled, mode in ((False, 2), (True, 0), (True, 1)):
            files = {FLAG: b""} if enabled else {}
            m = Machine("z-tesla", files=files); m.fail_persist = True
            self.assertEqual(m.run(0x1db40, (mode,)), 0xffffffff)
            self.assertEqual(FLAG in files, enabled)
            self.assertIn(b"Codec change failed", m.files[CODEC_STATUS])
            self.assertEqual(m.uc.reg_read(UC_ARM_REG_SP), m.top)

    def test_runtime_handler_failures_keep_compressed_mode_and_report_error(self):
        for failure, mode in (("fail_persist", 0), ("fail_unlink", 0),
                              ("fail_unlink", 1), ("fail_open", 2), ("fail_close", 2)):
            with self.subTest(failure=failure, mode=mode):
                files = {FLAG: b"", MODE_FILE: b"2"}
                m = Machine("z-tesla", files=files)
                m.json_node = m.allocate(bytes(32)); m.put32(m.json_node + 24, mode, 0)
                ctx = m.allocate(bytes(0x300)); m.put32(ctx + 0x44, 2)
                setattr(m, failure, True)
                self.assertEqual(m.run(0x2309c, (0x1234, ctx)), 0xffffffff)
                self.assertEqual(m.get32(ctx + 0x44)[0], 1)
                self.assertNotEqual(files[MODE_FILE], b"0")
                if failure != "fail_open": self.assertIn(b"Codec change failed", files[CODEC_STATUS])
                self.assertEqual(m.uc.reg_read(UC_ARM_REG_SP), m.top)
                self.assertFalse(m.handles)

    def test_init_continuation_keeps_safe_mode_after_failed_save(self):
        for failure in ("fail_persist", "fail_unlink", None):
            with self.subTest(failure=failure):
                m = Machine("z-tesla", files={FLAG: b"", MODE_FILE: b"2"})
                ctx = m.allocate(bytes(0x300))
                frame = m.allocate(bytes(0x100)); m.put32(frame, ctx)
                m.put32(frame + 0x18, 0)
                if failure: setattr(m, failure, True)
                m.run(0x22d08, (0, 0, 0, 0, 0, 0, 0, frame), boundary=0x22db8)
                self.assertEqual(m.get32(ctx + 0x44)[0], 1 if failure else 0)
                self.assertEqual(m.get32(frame + 0x18)[0], 1 if failure else 0)
                self.assertEqual(m.get32(frame + 0xc)[0], 0xffffffff if failure else 0)
                self.assertFalse(m.handles)

    def test_runtime_success_commits_mode_and_clears_old_error(self):
        m = Machine("z-tesla", files={CODEC_STATUS: b"previous failure"})
        ctx = m.allocate(bytes(0x300))
        m.json_node = m.allocate(bytes(32))
        for mode in (2, 1, 0):
            m.put32(m.json_node + 24, mode, 0)
            self.assertEqual(m.run(0x2309c, (0x1234, ctx)), 0)
            self.assertEqual(m.get32(ctx + 0x44)[0], mode)
            self.assertEqual(m.files[MODE_FILE], str(mode).encode())
            self.assertEqual(FLAG in m.files, mode == 2)
            self.assertNotIn(CODEC_STATUS, m.files)

    def test_startup_inconsistent_or_missing_mode_never_decodes_hevc_as_jpeg(self):
        for saved, enabled, expected in ((b"0", True, 1), (None, True, 1),
                                        (b"1", True, 1), (b"2", True, 2),
                                        (b"0", False, 0), (None, False, 0)):
            files = {FLAG: b""} if enabled else {}
            if saved is not None: files[MODE_FILE] = saved
            m = Machine("z-tesla", files=files)
            self.assertEqual(m.run(0x1daec), expected)
            self.assertEqual(m.uc.reg_read(UC_ARM_REG_SP), m.top)
            self.assertEqual(m.preserved(), [0x11110000 + i for i in range(4, 12)])
            self.assertFalse(m.handles)

    def test_stock_video_branch_keeps_mode_two_in_passthrough(self):
        for mode, target in ((0, 0x218dc), (1, 0x218c8), (2, 0x218c8)):
            m = Machine("z-tesla")
            context = m.allocate(bytes(0x48))
            m.put32(context + 0x44, mode)
            m.run(0x218c2, (0, 0, 0, context), boundary=target)
            self.assertEqual(m.uc.reg_read(UC_ARM_REG_PC), target)

    def test_flag_errors_report_failure_and_unreadable_flag_disables_hevc(self):
        for failure, mode in (("fail_open", 2), ("fail_close", 2), ("fail_unlink", 1)):
            m = Machine("z-tesla", files={FLAG: b""})
            setattr(m, failure, True)
            self.assertEqual(m.run(0x1db40, (mode,)), 0xffffffff)
            self.assertFalse(m.handles)
        m = Machine("libAirPlay.so", files={FLAG: b""}); m.fail_open = True
        info = m.object("dict", {})
        m.put32(m.top + 0x10ec, *range(8), m.stop | 1)
        self.assertEqual(m.run(0xf944, (0, 0, 0, 0, info)), info)
        self.assertNotIn("hevcInfo", m.objects[info][1])

    def test_setup_preserves_delegate_features_when_hevc_selected(self):
        m = Machine("libAirPlay.so", files={FLAG: b""})
        features = m.object("array", [])
        response = m.object("dict", {"enabledFeatures": features})
        out = m.allocate(bytes(4))
        words = [0] * 9; words[8] = response
        m.run(0x1c650, (0, 0, 0, out), words, boundary=0x1c656)
        self.assertEqual(m.objects[response][1]["enabledFeatures"], features)
        m.release(response)
        self.assertTrue(all(o[2] == 0 for o in m.objects.values()))

    def test_metadata_classification_preserves_avc(self):
        for byte, target in ((0x27, 0x2fb42), (0x40, 0x2fb42), (0x42, 0x2fb42),
                             (0x26, 0x2fba6), (0x65, 0x2fba6)):
            m = Machine("z-tesla")
            m.run(0x2fb3e, (0, 0, 0, byte), boundary=target)
            self.assertEqual(m.uc.reg_read(UC_ARM_REG_PC), target)

    def test_changed_firmware_is_rejected(self):
        from native_hevc import patch_native
        for name in PINNED:
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                patch_native(name, b"wrong firmware", BUILD / "should-not-exist")

    def test_manifest_branch_targets_and_load_permissions(self):
        for name in PINNED:
            e = ELF((BUILD / name).read_bytes())
            report = json.loads((BUILD / (name + ".build/manifest.json")).read_text())
            for p in report["patches"]:
                a, t = int(p["address"], 16), int(p["target"], 16)
                self.assertEqual(e.data[e.offset(a, 4):e.offset(a, 4) + 4], branch(a, t))
            self.assertFalse(any(p[0] == 1 and p[6] & 3 == 3 for p in e.ph))

    def test_parameter_set_array_order(self):
        config = hvcc()
        # Three equal-sized arrays; deliberately put PPS first and VPS last.
        config = config[:23] + config[39:47] + config[31:39] + config[23:31]
        m = Machine("libScreenStream.so")
        self.assertEqual(m.property("avcc", config), 0)
        self.assertEqual(m.frame(packet(b"\x26\x01\xaa")), 0)
        self.assertEqual(m.sent[-1][0], annex(b"\x40\x01\xaa", b"\x42\x01\xaa",
                                             b"\x44\x01\xaa", b"\x26\x01\xaa"))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_real_hevc_packets_decode_identically_after_arm_conversion(self):
        def run(*args): return subprocess.check_output(args, stderr=subprocess.PIPE)
        def raw(dump):
            return b"".join(bytes.fromhex(line.split(": ", 1)[1].split("  ", 1)[0])
                            for line in dump.splitlines() if ": " in line)
        with tempfile.TemporaryDirectory(prefix="zq-hevc-decode-") as directory:
            source = str(Path(directory) / "source.mp4")
            converted = str(Path(directory) / "converted.h265")
            run("ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=3",
                "-frames:v", "3", "-c:v", "libx265", "-x265-params",
                "pools=1:frame-threads=1:log-level=error:bframes=0", "-tag:v", "hvc1", source)
            probe = json.loads(run("ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_streams", "-show_packets", "-show_data", "-of", "json", source))
            m = Machine("libScreenStream.so", 0x18000000)
            self.assertEqual(m.property("avcc", raw(probe["streams"][0]["extradata"])), 0)
            for frame in probe["packets"]:
                self.assertEqual(m.frame(raw(frame["data"])), 0)
            Path(converted).write_bytes(b"".join(s[0] for s in m.sent))
            # Compare decoded pixel hashes, ignoring stream-specific timestamps.
            def hashes(path):
                output = run("ffmpeg", "-v", "error", "-i", path, "-f", "framemd5", "-").decode()
                return [line.rsplit(",", 1)[1].strip() for line in output.splitlines()
                        if line and not line.startswith("#")]
            self.assertEqual(hashes(converted), hashes(source))
            self.assertEqual(len(hashes(converted)), 3)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=BUILD)
    args, remaining = parser.parse_known_args()
    BUILD = args.build_dir
    unittest.main(argv=[sys.argv[0], *remaining])
