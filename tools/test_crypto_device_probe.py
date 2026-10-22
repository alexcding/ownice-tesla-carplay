#!/usr/bin/env python3
"""Emulate the complete freestanding sidecar with a read-only syscall fixture."""
import argparse
import io
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).parent))

from elftools.elf.elffile import ELFFile
from unicorn import Uc, UcError, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_INTR
from unicorn.arm_const import (UC_CPU_ARM_CORTEX_A7, UC_ARM_REG_FPEXC, UC_ARM_REG_R0,
                              UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
                              UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R7, UC_ARM_REG_SP)
from build_crypto_neon import BASE_SHA256, ENTRY_BYTES, digest, require, align


def run(probe, candidate, neon=True, mismatch=False, timer_error=False, guard=None,
        cpu_clock_error=False):
    base = bytearray(candidate[:2540072])
    struct.pack_into('<I', base, 28, 52)
    struct.pack_into('<H', base, 44, 8)
    base[0x11b454:0x11b45c] = ENTRY_BYTES
    require(digest(base) == BASE_SHA256, 'fixture baseline identity')
    if mismatch:
        base[0x11b454 + 100] ^= 1
    machine = Uc(UC_ARCH_ARM, UC_MODE_ARM)
    machine.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_A7)
    machine.reg_write(UC_ARM_REG_FPEXC, 1 << 30)
    elf = ELFFile(io.BytesIO(probe))
    require(elf['e_machine'] == 'EM_ARM' and elf['e_type'] == 'ET_EXEC', 'probe format')
    require(not any(s['p_type'] == 'PT_INTERP' for s in elf.iter_segments()), 'probe must be static')
    for segment in elf.iter_segments():
        if segment['p_type'] != 'PT_LOAD':
            continue
        start = segment['p_vaddr'] & -4096
        length = align(segment['p_vaddr'] + segment['p_memsz'], 4096) - start
        require(start >= 0x10000000, 'probe must not collide with candidate mappings')
        machine.mem_map(start, length, segment['p_flags'])  # R/X bits swap below
        permissions = (1 if segment['p_flags'] & 4 else 0) | (2 if segment['p_flags'] & 2 else 0) | (4 if segment['p_flags'] & 1 else 0)
        machine.mem_protect(start, length, permissions)
        if segment['p_filesz']:
            machine.mem_write(segment['p_vaddr'], segment.data())
    machine.mem_map(0x70000000, 0x20000)
    arguments = ['crypto-probe', 'candidate'] if not guard else ['crypto-probe', '--guard', '12', 'child', 'argument']
    pointers, cursor = [], 0x7001f000
    for argument in arguments:
        pointers.append(cursor)
        machine.mem_write(cursor, argument.encode() + b'\0')
        cursor += len(argument) + 1
    stack = [len(arguments)] + pointers + [0, 0, 16, (1 << 12) if neon else 0, 0, 0]
    machine.mem_write(0x7001e000, struct.pack('<' + str(len(stack)) + 'I', *stack))
    machine.reg_write(UC_ARM_REG_SP, 0x7001e000)
    files = {'candidate': candidate, '/usr/z-loader/bin/z-tesla': bytes(base)}
    descriptors = {}
    output, seen, status = [], set(), []
    clocks = {1: 0, 2: 0}
    killed = []

    def string(address):
        data = bytearray()
        while True:
            byte = bytes(machine.mem_read(address + len(data), 1))
            if byte == b'\0':
                return data.decode()
            data.extend(byte)
            require(len(data) < 512, 'unterminated path')

    def syscall(uc, interrupt, unused):
        number = uc.reg_read(UC_ARM_REG_R7)
        args = [uc.reg_read(r) for r in [UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                       UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5]]
        seen.add(number)
        result = 0
        if number == 1:
            status.append(args[0]); uc.emu_stop()
        elif number == 4:
            require(args[0] == 1, 'probe attempted a file write')
            output.append(bytes(uc.mem_read(args[1], args[2])).decode())
            result = args[2]
        elif number == 5:
            require(args[1] == 0, 'probe must open files read-only')
            name = string(args[0]); require(name in files, 'unexpected file: ' + name)
            result = max([2] + list(descriptors)) + 1
            descriptors[result] = [files[name], 0]
        elif number == 3:
            data, offset = descriptors[args[0]]
            chunk = data[offset:offset + args[2]]
            uc.mem_write(args[1], chunk)
            descriptors[args[0]][1] += len(chunk); result = len(chunk)
        elif number == 19:
            require(args[2] == 0, 'unexpected seek mode')
            descriptors[args[0]][1] = args[1]; result = args[1]
        elif number == 6:
            del descriptors[args[0]]
        elif number == 192:
            address, length, prot, flags, fd, offset = args
            require(flags == 0x100002 and prot == 5, 'candidate mappings must be private RX/NOREPLACE')
            try:
                uc.mem_map(address, length, prot)
                uc.mem_write(address, descriptors[fd][0][offset * 4096:offset * 4096 + length])
                result = address
            except UcError:
                result = -17
        elif number == 125:
            require(args[0] == 0x12b000 and args[1] == 4096 and args[2] in (3, 5), 'unexpected mprotect')
            uc.mem_protect(*args[:3])
        elif number == 0xf0002:
            require(args[:3] == [0x12b454, 0x12b45c, 0], 'unexpected cache flush')
            uc.ctl_remove_cache(args[0], args[1])
        elif number == 263:
            clock_id = args[0]
            require(clock_id in clocks and (not guard or clock_id == 1), 'unexpected clock')
            # Deliberately model descheduling: wall time advances three times
            # faster than CPU time. The two output series must remain distinct.
            if cpu_clock_error and clock_id == 2:
                result = -38
            else:
                clocks[clock_id] += 1_000_000_000 if guard else (3_000_000 if clock_id == 1 else 1_000_000)
                value = clocks[clock_id]
                uc.mem_write(args[1], struct.pack('<II', value // 1_000_000_000,
                                                 value % 1_000_000_000))
        elif number == 104:
            require(args[0] == 0 and args[2] == 0, 'timer must be process-local ITIMER_REAL')
            require(struct.unpack('<4I', bytes(uc.mem_read(args[1], 16))) == (0, 0, 10, 0),
                    'timer must fire once after ten seconds')
            if timer_error:
                result = -38
        elif number == 97:
            require(args[:3] == [0, 0, 19], 'priority must apply only to this process')
        elif number == 2:
            require(guard is not None, 'fork is only allowed in watchdog mode')
            result = 0 if guard == 'exec_failure' else 42
        elif number == 11:
            require(guard == 'exec_failure' and string(args[0]) == 'child', 'unexpected exec')
            child_args = struct.unpack('<3I', bytes(uc.mem_read(args[1], 12)))
            require([string(child_args[0]), string(child_args[1]), child_args[2]] == ['child', 'argument', 0], 'exec arguments')
            result = -2
        elif number == 114:
            require(guard is not None and args[0] == 42 and args[2] in (0, 1), 'must wait only for own child')
            if guard == 'timeout' and not killed:
                result = 0
            else:
                result = 42
                uc.mem_write(args[1], struct.pack('<I', (7 << 8) if guard == 'child_failure' else (9 if killed else 0)))
        elif number == 37:
            require(guard == 'timeout' and args[:3] == [42, 9, 0], 'must kill only own unreaped child')
            killed.append(42)
        elif number == 162:
            require(guard is not None, 'unexpected sleep')
        else:
            raise AssertionError('unexpected syscall: ' + str(number))
        uc.reg_write(UC_ARM_REG_R0, result & 0xffffffff)

    machine.hook_add(UC_HOOK_INTR, syscall)
    machine.emu_start(elf['e_entry'], 0xffffffff, timeout=45_000_000, count=1_000_000_000)
    require(len(status) == 1, 'probe did not exit normally; output=' + ''.join(output))
    text = ''.join(output)
    if guard:
        wanted = {'success': 0, 'child_failure': 7, 'timeout': 124, 'exec_failure': 127}[guard]
        require(status == [wanted], 'watchdog exit status ' + repr(status))
        require(bool(killed) == (guard == 'timeout'), 'watchdog kill behavior')
    elif timer_error:
        require(status == [1] and 'failed=setitimer' in text, 'failed timer was ignored')
        require(5 not in seen and 192 not in seen, 'candidate opened/mapped after timer failure')
    elif cpu_clock_error:
        require(status == [1] and 'failed=benchmark' in text, 'CPU-clock failure was ignored')
        require('timing_schema=' not in text and 'stock_cpu_ns_' not in text,
                'failed CPU clock produced a timing report')
    elif not neon:
        require(status == [1] and 'kernel-does-not-advertise-neon' in text, 'NEON guard failed')
    elif mismatch:
        require(status == [1] and 'installed-crypto-routine-differs-from-baseline' in text, 'baseline guard failed')
    else:
        require(status == [0] and 'selftest=pass' in text and 'installed_crypto_matches=1' in text, text)
        fields = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
        require(fields.get('timing_schema') == '2', 'wrong timing schema')
        require(fields.get('wall_clock') == 'CLOCK_MONOTONIC' and
                fields.get('cpu_clock') == 'CLOCK_PROCESS_CPUTIME_ID', 'unlabelled clocks')
        for implementation in ['stock', 'neon']:
            for sample in range(3):
                require(int(fields[f'{implementation}_wall_ns_{sample}'], 0) == 3_000_000,
                        'wall timing was confused with CPU timing')
                require(int(fields[f'{implementation}_cpu_ns_{sample}'], 0) == 1_000_000,
                        'CPU timing includes simulated descheduling')
    return {'exit': status[0], 'output': text, 'syscalls': sorted(seen)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    args = parser.parse_args()
    probe, candidate = args.probe.read_bytes(), args.candidate.read_bytes()
    results = {'positive': run(probe, candidate),
               'no_neon': run(probe, candidate, neon=False),
               'different_installed_crypto': run(probe, candidate, mismatch=True),
               'timer_failure': run(probe, candidate, timer_error=True),
               'cpu_clock_failure': run(probe, candidate, cpu_clock_error=True),
               'watchdog_success': run(probe, candidate, guard='success'),
               'watchdog_child_failure': run(probe, candidate, guard='child_failure'),
               'watchdog_timeout': run(probe, candidate, guard='timeout'),
               'watchdog_exec_failure': run(probe, candidate, guard='exec_failure')}
    print(json.dumps({'passed': True, 'timing_is_synthetic': True, 'results': results}, indent=2))
