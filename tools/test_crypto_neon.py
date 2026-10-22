#!/usr/bin/env python3
"""Run stock and patched ARM machine code offline; never contacts a device."""

import argparse
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import random
import struct
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).parent))

from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN
from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_WRITE, UC_HOOK_MEM_READ
from unicorn import UC_PROT_READ, UC_PROT_WRITE, UC_PROT_EXEC
from unicorn.arm_const import (
    UC_CPU_ARM_CORTEX_A7, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
    UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6,
    UC_ARM_REG_R7, UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10,
    UC_ARM_REG_R11, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC,
    UC_ARM_REG_D8, UC_ARM_REG_FPEXC,
)

from build_crypto_neon import SUPPORTED_BASES, ENTRY_OFFSET, ENTRY_VA, align, digest, require

MASK = 0xFFFFFFFF
STACK = 0x1000000
STACK_BYTES = 0x10000
SP = STACK + STACK_BYTES - 256
INPUT = 0x2000000
OUTPUT = 0x3000000
KEY = 0x4000000
COUNTER = KEY + 128
STOP = 0x5000000
SAVED_REGS = [UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
              UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11]


def reference(data, key, counter):
    """Independent RFC 8439 rounds, with OpenSSL ctr32's low-word-only wrap."""
    words = list(struct.unpack('<4I', b'expand 32-byte k'))
    words += list(struct.unpack('<8I', key)) + list(struct.unpack('<4I', counter))
    output = bytearray()

    def rotate(value, bits):
        return ((value << bits) | (value >> (32 - bits))) & MASK

    for offset in range(0, len(data), 64):
        state = words.copy()

        def quarter(a, b, c, d):
            state[a] = (state[a] + state[b]) & MASK
            state[d] = rotate(state[d] ^ state[a], 16)
            state[c] = (state[c] + state[d]) & MASK
            state[b] = rotate(state[b] ^ state[c], 12)
            state[a] = (state[a] + state[b]) & MASK
            state[d] = rotate(state[d] ^ state[a], 8)
            state[c] = (state[c] + state[d]) & MASK
            state[b] = rotate(state[b] ^ state[c], 7)

        for _ in range(10):
            for indices in [(0, 4, 8, 12), (1, 5, 9, 13), (2, 6, 10, 14),
                            (3, 7, 11, 15), (0, 5, 10, 15), (1, 6, 11, 12),
                            (2, 7, 8, 13), (3, 4, 9, 14)]:
                quarter(*indices)
        block = struct.pack('<16I', *((a + b) & MASK for a, b in zip(state, words)))
        output.extend(a ^ b for a, b in zip(data[offset:offset + 64], block))
        words[12] = (words[12] + 1) & MASK
    return bytes(output)


def check_layout(base, patched, manifest):
    require(digest(base) in SUPPORTED_BASES, 'wrong test baseline')
    require(manifest['base_sha256'] == digest(base), 'manifest baseline mismatch')
    require(digest(patched) == manifest['patched_sha256'], 'patched hash mismatch')
    old = ELFFile(io.BytesIO(base))
    new = ELFFile(io.BytesIO(patched))
    old_loads = [dict(s.header) for s in old.iter_segments() if s['p_type'] == 'PT_LOAD']
    new_loads = [dict(s.header) for s in new.iter_segments() if s['p_type'] == 'PT_LOAD']
    require(new_loads[:-1] == old_loads, 'original load mappings changed')
    extra = new_loads[-1]
    require(extra['p_flags'] == 5, 'new code must be RX, never writable')
    require(extra['p_offset'] % extra['p_align'] == extra['p_vaddr'] % extra['p_align'],
            'invalid ELF segment congruence')
    end = 0
    for segment in new_loads:
        require(segment['p_vaddr'] >= end, 'load segments overlap or are unordered')
        end = align(segment['p_vaddr'] + segment['p_memsz'], 4096)
    phdr = next(s for s in new.iter_segments() if s['p_type'] == 'PT_PHDR')
    require(phdr['p_offset'] == new['e_phoff'] == extra['p_offset'], 'PHDR file mapping')
    require(phdr['p_vaddr'] == extra['p_vaddr'], 'PHDR virtual mapping')
    require(phdr['p_filesz'] == new['e_phnum'] * new['e_phentsize'], 'PHDR size')
    # Reproduce Linux 5.4 create_elf_tables(), independently of the manifest.
    # Merely checking PT_PHDR against the new segment missed the v1 startup bug.
    at_phdr = new_loads[0]['p_vaddr'] - new_loads[0]['p_offset'] + new['e_phoff']
    require(at_phdr == phdr['p_vaddr'], 'Linux 5.4 AT_PHDR does not point to the headers')
    table_size = new['e_phnum'] * new['e_phentsize']
    mapped_table = next((s for s in new_loads
                        if s['p_vaddr'] <= at_phdr and
                        at_phdr + table_size <= s['p_vaddr'] + s['p_filesz']), None)
    require(mapped_table is not None, 'Linux 5.4 AT_PHDR table is not file-backed')
    mapped_offset = mapped_table['p_offset'] + at_phdr - mapped_table['p_vaddr']
    require(mapped_offset == new['e_phoff'], 'Linux 5.4 AT_PHDR maps unrelated file bytes')
    # Only ELF e_phoff/e_phnum and the exact function entry may change in base bytes.
    allowed = set(range(28, 32)) | set(range(44, 46)) | set(range(ENTRY_OFFSET, ENTRY_OFFSET + 8))
    require(all(a == b or i in allowed for i, (a, b) in enumerate(zip(base, patched))),
            'unexpected modification of original bytes')
    require(list(old.iter_sections()) and old.num_sections() == new.num_sections(),
            'section inventory changed')
    for a, b in zip(old.iter_sections(), new.iter_sections()):
        require(dict(a.header) == dict(b.header), 'section metadata changed')


def callsites(base):
    decoder = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_LITTLE_ENDIAN)
    result = []
    for start, size in [(0xC9F7C, 0x300), (0xCA4D0, 0x6B4)]:
        code = base[start - 0x10000:start - 0x10000 + size]
        for insn in decoder.disasm(code, start):
            if insn.mnemonic == 'bl' and insn.op_str == '#0x12b454':
                result.append(insn.address)
    require(result == [0xC9FAC, 0xCA000, 0xCA0A2, 0xCA52E, 0xCA6EC,
                       0xCA8CE, 0xCA928, 0xCA9DC], 'original call instructions differ')
    return result


def execute(binary, data, key, counter, manifest=None, in_place=False,
            misalign=0, page_end=False, callsite=None):
    machine = Uc(UC_ARCH_ARM, UC_MODE_ARM)
    machine.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_A7)
    machine.reg_write(UC_ARM_REG_FPEXC, 1 << 30)
    elf = ELFFile(io.BytesIO(binary))
    mapped = []
    for segment in elf.iter_segments():
        if segment['p_type'] != 'PT_LOAD':
            continue
        start = segment['p_vaddr'] & -4096
        size = align(segment['p_vaddr'] + segment['p_memsz'], 4096) - start
        flags = segment['p_flags']
        permissions = (UC_PROT_READ if flags & 4 else 0)
        permissions |= UC_PROT_WRITE if flags & 2 else 0
        permissions |= UC_PROT_EXEC if flags & 1 else 0
        machine.mem_map(start, size, permissions)
        machine.mem_write(segment['p_vaddr'], segment.data())
        mapped.append((segment['p_vaddr'], segment['p_vaddr'] + segment['p_filesz']))
    machine.mem_map(STACK, STACK_BYTES, UC_PROT_READ | UC_PROT_WRITE)
    space = align(max(len(data), 1) + 128, 4096)
    for address in [INPUT, OUTPUT]:
        machine.mem_map(address, space, UC_PROT_READ | UC_PROT_WRITE)
        machine.mem_write(address, b'\xa5' * space)
    machine.mem_map(KEY, 4096, UC_PROT_READ | UC_PROT_WRITE)
    machine.mem_write(KEY, key)
    machine.mem_write(COUNTER, counter)
    machine.mem_map(STOP, 4096, UC_PROT_READ | UC_PROT_EXEC)
    offset = space - len(data) if page_end else 32 + misalign
    source = INPUT + offset
    destination = source if in_place else OUTPUT + offset
    if data:
        machine.mem_write(source, data)
    machine.reg_write(UC_ARM_REG_R0, destination)
    machine.reg_write(UC_ARM_REG_R1, source)
    machine.reg_write(UC_ARM_REG_R2, len(data))
    machine.reg_write(UC_ARM_REG_R3, KEY)
    machine.reg_write(UC_ARM_REG_SP, SP)
    machine.mem_write(SP, struct.pack('<I', COUNTER))
    return_address = callsite + 4 if callsite is not None else STOP
    machine.reg_write(UC_ARM_REG_LR, return_address | 1)
    saved = {reg: (0x12340000 + i * 0x101) for i, reg in enumerate(SAVED_REGS)}
    # AAPCS requires d8-d15 preservation, not the caller-saved NEON registers.
    saved.update({UC_ARM_REG_D8 + i: 0x1122334455667700 + i for i in range(8)})
    for reg, value in saved.items():
        machine.reg_write(reg, value)
    vector_addresses = set()
    if manifest:
        decoder = Cs(CS_ARCH_ARM, CS_MODE_ARM | CS_MODE_LITTLE_ENDIAN)
        payload_offset = manifest['new_segment_offset']
        payload_va = manifest['new_segment_va']
        start = manifest['neon_entry_va']
        offset_in_file = payload_offset + start - payload_va
        for insn in decoder.disasm(binary[offset_in_file:], start):
            if insn.mnemonic.startswith('v'):
                vector_addresses.add(insn.address)
        require(vector_addresses, 'payload contains no decoded NEON instructions')
    stats = {'instructions': 0, 'entry_hits': 0, 'payload_hits': 0, 'neon_hits': 0,
             'vector_instructions': 0}

    def instruction(uc, address, size, unused):
        stats['instructions'] += 1
        stats['entry_hits'] += address == ENTRY_VA
        stats['vector_instructions'] += address in vector_addresses
        if manifest:
            stats['payload_hits'] += address == manifest['payload_entry_va']
            # Dispatch branches into the NEON body after its standalone prologue.
            stats['neon_hits'] += address == manifest['neon_entry_va'] + 8

    def write(uc, access, address, size, value, unused):
        require(STACK <= address and address + size <= SP or
                destination <= address and address + size <= destination + len(data),
                'write outside stack/output: ' + hex(address))

    def read(uc, access, address, size, value, unused):
        permitted = mapped + [(STACK, SP + 4), (source, source + len(data)),
                              (destination, destination + len(data)),
                              (KEY, KEY + 32), (COUNTER, COUNTER + 16)]
        require(any(low <= address and address + size <= high for low, high in permitted),
                'read outside declared buffers: ' + hex(address))

    machine.hook_add(UC_HOOK_CODE, instruction)
    machine.hook_add(UC_HOOK_MEM_WRITE, write)
    machine.hook_add(UC_HOOK_MEM_READ, read)
    machine.emu_start((callsite if callsite is not None else ENTRY_VA) | 1,
                      return_address, timeout=10_000_000, count=5_000_000)
    require(machine.reg_read(UC_ARM_REG_PC) == return_address, 'did not return normally')
    require(machine.reg_read(UC_ARM_REG_SP) == SP, 'stack pointer not restored')
    require(all(machine.reg_read(reg) == value for reg, value in saved.items()),
            'callee-saved register changed')
    require(bytes(machine.mem_read(KEY, 32)) == key, 'key was modified')
    require(bytes(machine.mem_read(COUNTER, 16)) == counter, 'counter/nonce was modified')
    require(bytes(machine.mem_read(SP, 4)) == struct.pack('<I', COUNTER), 'fifth argument changed')
    if not in_place and data:
        require(bytes(machine.mem_read(source, len(data))) == data, 'input changed')
    require(stats['entry_hits'] == 1, 'did not traverse the original entry once')
    if manifest:
        require(stats['payload_hits'] == 1, 'redirection did not reach new code')
        require(bool(stats['neon_hits']) == (len(data) > 192), 'unexpected NEON dispatch')
        require(bool(stats['vector_instructions']) == (len(data) > 192),
                'NEON arithmetic was not executed as expected')
    return bytes(machine.mem_read(destination, len(data))) if data else b'', stats


def check_loader_regression(base, patched, manifest):
    """A self-consistent PT_PHDR/PT_LOAD pair must still satisfy old Linux auxv."""
    elf = ELFFile(io.BytesIO(patched))
    broken = bytearray(patched)
    for index, segment in enumerate(elf.iter_segments()):
        if segment['p_type'] == 'PT_PHDR' or (
                segment['p_type'] == 'PT_LOAD' and segment['p_offset'] == elf['e_phoff']):
            for field in [8, 12]:  # p_vaddr and p_paddr; preserve alignment and ordering
                offset = elf['e_phoff'] + index * elf['e_phentsize'] + field
                struct.pack_into('<I', broken, offset, struct.unpack_from('<I', broken, offset)[0] + 4096)
    altered_manifest = dict(manifest, patched_sha256=digest(broken))
    try:
        check_layout(base, bytes(broken), altered_manifest)
    except ValueError as error:
        require(str(error) == 'Linux 5.4 AT_PHDR does not point to the headers',
                'loader regression failed for an unrelated reason: ' + str(error))
    else:
        raise AssertionError('Linux 5.4-incompatible headers accepted')


def run_suite(args):
    base = args.binary.read_bytes()
    patched = (args.candidate / 'z-tesla.neon').read_bytes()
    manifest = json.loads((args.candidate / 'manifest.json').read_text())
    check_layout(base, patched, manifest)
    check_loader_regression(base, patched, manifest)
    rng = random.Random(0x8439)
    randbytes = lambda n: bytes(rng.randrange(256) for _ in range(n))
    cases = []
    # RFC 8439 section 2.3.2: encryption of zeros yields the published block.
    key = bytes(range(32))
    counter = struct.pack('<I', 1) + bytes.fromhex('000000090000004a00000000')
    known = bytes.fromhex(
        '10f1e7e4d13b5915500fdd1fa32071c4c7d1f4c733c068030422aa9ac3d46c4e'
        'd2826446079faa0914c2d705d98b02a2b5129cd1de164eb9cbd083e8a2503c4e')
    require(reference(bytes(64), key, counter) == known, 'RFC reference check failed')
    cases.append((bytes(64), key, counter, False, 0, False))
    # Exhaust every tail length across scalar, NEON dispatch and four-block loops.
    for length in range(513):
        cases.append((randbytes(length), randbytes(32), randbytes(16),
                      bool(length % 2), length % 16, False))
    lengths = [0, 1, 15, 16, 31, 32, 63, 64, 65, 127, 128, 129,
               191, 192, 193, 255, 256, 257, 511, 512, 513, 1023,
               1024, 1025, 4095, 4096, 4097, 16384]
    for length in lengths:
        for in_place in [False, True]:
            cases.append((randbytes(length), randbytes(32), randbytes(16),
                          in_place, length % 16, False))
    for length in [1, 63, 64, 65, 192, 193, 255, 256, 257, 1024, 4096]:
        for in_place in [False, True]:
            cases.append((randbytes(length), randbytes(32), randbytes(16), in_place, 0, True))
    for low in [0xFFFFFFFC, 0xFFFFFFFE, 0xFFFFFFFF]:
        for length in [193, 256, 257, 1024]:
            cases.append((randbytes(length), randbytes(32),
                          struct.pack('<I', low) + randbytes(12), False, 0, False))
    for _ in range(args.random_cases):
        cases.append((randbytes(rng.randrange(8193)), randbytes(32), randbytes(16),
                      bool(rng.randrange(2)), rng.randrange(16), False))
    instruction_examples = {}
    for i, (data, key, counter, in_place, misalign, page_end) in enumerate(cases):
        expected = reference(data, key, counter)
        stats = []
        for binary, meta in [(base, None), (patched, manifest)]:
            actual, stat = execute(binary, data, key, counter, meta, in_place, misalign, page_end)
            require(actual == expected, 'ciphertext mismatch in case ' + str(i))
            stats.append(stat)
        if len(data) in [64, 1024, 16384] and not in_place:
            instruction_examples[str(len(data))] = {
                'stock': stats[0]['instructions'], 'patched': stats[1]['instructions']}
    sites = callsites(base)
    for site in sites:
        for length in [0, 64, 193, 1024]:
            data, key, counter = randbytes(length), randbytes(32), randbytes(16)
            actual, _ = execute(patched, data, key, counter, manifest, callsite=site)
            require(actual == reference(data, key, counter), 'original BL call failed')
    # A changed baseline must fail closed before a patch can be generated.
    from build_crypto_neon import patch_binary
    changed = bytearray(base)
    changed[-1] ^= 1
    try:
        patch_binary(bytes(changed), b'', 0, 0)
    except ValueError:
        pass
    else:
        raise AssertionError('modified baseline accepted')
    result = {
        'passed': True, 'baseline_sha256': digest(base), 'candidate_sha256': digest(patched),
        'differential_cases': len(cases), 'machine_code_executions': len(cases) * 2 + len(sites) * 4,
        'original_bl_callsites': [hex(s) for s in sites], 'original_callsite_cases': len(sites) * 4,
        'checks': ['RFC 8439 known answer', 'stock/patched/independent-reference agreement',
                   'NEON dispatch executed for buffers >192 bytes', 'unaligned input/output',
                   'exact in-place encryption', 'guard-page tails', 'counter wrap',
                   'callee-saved ARM and NEON registers', 'stack restoration',
                   'bounded reads and writes', 'key/nonce/input preservation',
                   'original caller BL instructions reach replacement',
                   'ELF RX segment and PHDR invariants', 'Linux 5.4 AT_PHDR file mapping',
                   'incompatible loader layout rejected', 'modified baseline rejected'],
        'instruction_counts_not_device_speed': instruction_examples,
        'not_validated': ['Linux loader and dynamic linker startup',
                          'complete z-tesla process and TLS session',
                          'physical adapter performance and recovery'],
    }
    return result


def write_report(path, result):
    """Replace the whole report atomically; never expose a partially written JSON."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.test-results-', suffix='.tmp', delete=False) as file:
            temporary = Path(file.name)
            file.write(json.dumps(result, indent=2) + '\n')
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True,
                        help='directory containing z-tesla.neon and manifest.json')
    parser.add_argument('--random-cases', type=int, default=80)
    args = parser.parse_args()
    report = args.candidate / 'test-results.json'
    started = datetime.now(timezone.utc).isoformat()
    # Invalidate an earlier pass BEFORE reading the inputs. Even a process crash
    # or malformed manifest leaves passed=false, with status=running or failed.
    write_report(report, {'passed': False, 'status': 'running', 'started_at': started})
    try:
        require(args.random_cases >= 0, 'random case count must be nonnegative')
        result = run_suite(args)
    except BaseException as error:
        write_report(report, {'passed': False, 'status': 'failed', 'started_at': started,
                             'finished_at': datetime.now(timezone.utc).isoformat(),
                             'error_type': type(error).__name__, 'error': str(error)})
        raise
    result.update(status='passed', started_at=started,
                  finished_at=datetime.now(timezone.utc).isoformat())
    write_report(report, result)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
