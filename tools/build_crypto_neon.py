#!/usr/bin/env python3
"""Offline, hash-pinned experimental z-tesla ChaCha20 patch. Never deploys."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import urllib.request
import zipfile

from elftools.elf.elffile import ELFFile

BASE_SHA256 = "fabf68f52d56e487a591e1229c3e2fa3c9006a9efc91bb4af57c03f1bd501ed8"
HEVC_SHA256 = "03475fae7167818e20fd9f44da2ccb7869bd4dc09fc2850a35824efc53652bec"
SUPPORTED_BASES = {BASE_SHA256: 'stock-2.9.11',
                   HEVC_SHA256: 'hevc-diagnostics-focus-retained-493a699',
                   '9bef3de9c21b64d5dc626a58754fc1d8829326c3a72e3d6a2ff89a4f67ea055b': 'hevc-diagnostics-493a699',
                   '3ccb01b3b6b7226ce7956240f6687dcbf0a0a330c99ebaf8c1d6843439e5b252': 'hevc-493a699',
                   'dfe0359f10476ddbf8d1e03054c2f9b1cdce0fa0bbf339347ab7adeea2b0b0a6': 'hevc-focus-retained-493a699',
                   # Converter-report checkpoint filter (diagnostic builds only); reviewed: only
                   # the appended RX segment and its size fields differ from the 493a699 bases.
                   '61154fed4ab241168b35eb4b78893d80b78b6e85f9fc54e4f353b682944d556f': 'hevc-diagnostics-focus-retained-oc12',
                   '37c65d4e57e5b69b5bc5e0749b7ad846adbe68da637c604cebc4c06a4ac5ae36': 'hevc-diagnostics-oc12'}
ZIP_MD5 = "3a743be56b60df524825cffcf45238cd"
ENTRY_OFFSET = 0x11B454
ENTRY_VA = 0x12B454
ENTRY_BYTES = bytes.fromhex("2de9f04fabb00a91")
ROUTINE_SIZE = 1278
ROUTINE_SHA256 = "37037039acd11d0dafe877e918a539c7b06e056da7b5ce0f60760066bc025eb1"
SOURCES = {
    "chacha-armv4.pl": "99f5da3bed7a9983c75e2507d70f4ff1a098a6e8f043bf041be91b3629ae422c",
    "arm_arch.h": "7e5614435c2fdf780310858ea0ff7cc524b5bbc112e97d9c861cda095fe07380",
    "LICENSE": "c32913b33252e71190af2066f08115c69bc9fddadf3bf29296e20c835389841c",
}
PAYLOAD_SHA256 = '9b3a88ed027f0bc995bb8a45ab6fcdae8be5c2810bad76963f31895ac10bd61a'
UPSTREAM = 'https://raw.githubusercontent.com/openssl/openssl/OpenSSL_1_1_1w/'


def resolve_sources(directory=None):
    """Fetch missing pinned public sources into ignored build output, never firmware."""
    directory = Path(directory) if directory else Path(__file__).resolve().parent.parent/'build/crypto-source'
    directory.mkdir(parents=True, exist_ok=True)
    paths = {'chacha-armv4.pl': 'crypto/chacha/asm/chacha-armv4.pl',
             'arm_arch.h': 'crypto/arm_arch.h', 'LICENSE': 'LICENSE'}
    for name, expected in SOURCES.items():
        destination = directory/name
        if destination.exists():
            require(digest(destination.read_bytes()) == expected, 'cached source hash mismatch: '+name)
            continue
        with urllib.request.urlopen(UPSTREAM+paths[name], timeout=30) as response:
            data = response.read(1024*1024)
        require(digest(data) == expected, 'downloaded source hash mismatch: '+name)
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as temporary:
            temporary.write(data)
            staged = Path(temporary.name)
        staged.replace(destination)
    return directory


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def align(value, boundary):
    return (value + boundary - 1) & -boundary


def replace_once(text, old, new):
    require(text.count(old) == 1, "unexpected assembly match count: " + old)
    return text.replace(old, new, 1)


def compile_payload(source_dir, clang):
    """Build self-contained ARM code with a local, read-only NEON capability word."""
    for name, expected in SOURCES.items():
        require(digest((source_dir / name).read_bytes()) == expected,
                "upstream source hash mismatch: " + name)
    with tempfile.TemporaryDirectory(prefix="ownice-neon-") as temporary:
        work = Path(temporary)
        asm = work / "chacha.S"
        subprocess.run(["perl", str(source_dir / "chacha-armv4.pl"), "void", str(asm)],
                       check=True)
        text = asm.read_text()
        text = replace_once(text, "OPENSSL_armcap_P-.LChaCha20_ctr32",
                            ".Lownice_armcap-.LChaCha20_ctr32")
        text = replace_once(text, ".comm\tOPENSSL_armcap_P,4,4",
                            ".balign 4\n.Lownice_armcap:\n.word 1")
        asm.write_text(text)
        obj = work / "chacha.o"
        command = [clang, "--target=armv7-linux-gnueabihf", "-mcpu=cortex-a7",
                   "-mfpu=neon-vfpv4", "-mfloat-abi=hard", "-marm", "-c",
                   "-I", str(source_dir), str(asm), "-o", str(obj)]
        subprocess.run(command, check=True)
        elf = ELFFile(io.BytesIO(obj.read_bytes()))
        require(elf['e_machine'] == 'EM_ARM' and elf.elfclass == 32 and elf.little_endian,
                "unexpected payload architecture")
        require(not any(s['sh_type'] in ('SHT_REL', 'SHT_RELA') and s['sh_size']
                        for s in elf.iter_sections()), "payload has unresolved relocations")
        section = elf.get_section_by_name('.text')
        symbols = elf.get_section_by_name('.symtab')
        entry = symbols.get_symbol_by_name('ChaCha20_ctr32')
        neon = symbols.get_symbol_by_name('ChaCha20_neon')
        require(entry and len(entry) == 1 and neon and len(neon) == 1,
                "unexpected payload symbols")
        require(entry[0]['st_shndx'] == neon[0]['st_shndx'], "split payload sections")
        require(digest(section.data()) == PAYLOAD_SHA256, 'compiled crypto payload differs from tested code')
        return section.data(), entry[0]['st_value'], neon[0]['st_value']


def validate_patch(base, patched, manifest):
    """Fail before packaging on corruption, lost HEVC code or invalid ARM loading."""
    require(digest(base) in SUPPORTED_BASES, 'unsupported validation baseline')
    require(digest(base) == manifest['base_sha256'], 'baseline manifest mismatch')
    require(digest(patched) == manifest['patched_sha256'], 'patched manifest mismatch')
    require(len(patched) == manifest['new_segment_offset'] + manifest['new_segment_bytes'],
            'truncated or extended patched executable')
    original = bytearray(patched[:len(base)])
    for start, stop in ((28, 32), (44, 46), (ENTRY_OFFSET, ENTRY_OFFSET+8)):
        original[start:stop] = base[start:stop]
    require(bytes(original) == base, 'patch changed unrelated original bytes or HEVC code')
    require(patched[ENTRY_OFFSET:ENTRY_OFFSET+8].hex() == manifest['entry_patch_hex'],
            'entry branch differs')
    old = ELFFile(io.BytesIO(base))
    new = ELFFile(io.BytesIO(patched))
    require(new.elfclass == 32 and new.little_endian and new['e_machine'] == 'EM_ARM',
            'invalid output architecture')
    old_loads = [dict(s.header) for s in old.iter_segments() if s['p_type'] == 'PT_LOAD']
    loads = [dict(s.header) for s in new.iter_segments() if s['p_type'] == 'PT_LOAD']
    require(loads[:-1] == old_loads, 'original executable mappings changed')
    extra = loads[-1]
    require(extra['p_flags'] == 5, 'new executable segment must be RX')
    end = 0
    for load in loads:
        require(load['p_vaddr'] >= end, 'overlapping executable load mappings')
        end = align(load['p_vaddr'] + load['p_memsz'], 4096)
    headers = next(s for s in new.iter_segments() if s['p_type'] == 'PT_PHDR')
    at_phdr = loads[0]['p_vaddr'] - loads[0]['p_offset'] + new['e_phoff']
    require(at_phdr == headers['p_vaddr'] == extra['p_vaddr'] and
            headers['p_offset'] == new['e_phoff'] == extra['p_offset'] and
            headers['p_filesz'] == new['e_phnum']*32 <= extra['p_filesz'],
            'invalid Linux 5.4 loader/program-header mapping')
    code = extra['p_offset'] + align(new['e_phnum']*32, 32)
    require(digest(patched[code:]) == PAYLOAD_SHA256 == manifest['payload_sha256'],
            'optimized payload corrupted')


def patch_binary(base, payload, payload_entry, neon_entry):
    base_sha256 = digest(base)
    require(base_sha256 in SUPPORTED_BASES, "unsupported z-tesla SHA-256")
    require(base[ENTRY_OFFSET:ENTRY_OFFSET + 8] == ENTRY_BYTES, "entry bytes differ")
    require(digest(base[ENTRY_OFFSET:ENTRY_OFFSET + ROUTINE_SIZE]) == ROUTINE_SHA256,
            "ChaCha20 routine differs")
    elf = ELFFile(io.BytesIO(base))
    require(elf['e_type'] == 'ET_EXEC' and elf['e_machine'] == 'EM_ARM',
            "expected the pinned ARM executable")
    require(elf['e_phentsize'] == 32 and elf['e_phnum'] == 8, "unexpected program headers")
    segments = list(elf.iter_segments())
    require(sum(s['p_type'] == 'PT_PHDR' for s in segments) == 1, "missing PT_PHDR")
    loads = [s for s in segments if s['p_type'] == 'PT_LOAD']
    require(any(s['p_vaddr'] + ENTRY_OFFSET - s['p_offset'] == ENTRY_VA
                and s['p_offset'] <= ENTRY_OFFSET < s['p_offset'] + s['p_filesz']
                for s in loads), "entry mapping differs")
    # Linux 5.4 derives AT_PHDR from the FIRST load's vaddr-offset plus e_phoff,
    # not from PT_PHDR. Give the new segment that same bias, padding the file
    # beyond existing BSS so its mapping cannot collide with any original load.
    # Keep every old PT_LOAD, BSS extent, section, note and interpreter unchanged.
    first_load_bias = loads[0]['p_vaddr'] - loads[0]['p_offset']
    memory_end = max(s['p_vaddr'] + s['p_memsz'] for s in loads)
    file_offset = align(max(len(base), memory_end - first_load_bias), 4096)
    address = file_offset + first_load_bias
    phnum = len(segments) + 1
    table_size = phnum * 32
    code_offset = align(table_size, 32)
    segment_size = code_offset + len(payload)
    require(len(payload) > payload_entry >= 0 and len(payload) > neon_entry >= 0,
            "invalid payload entries")
    headers = []
    old_offset = elf['e_phoff']
    for i, segment in enumerate(segments):
        if segment['p_type'] == 'PT_PHDR':
            headers.append(struct.pack('<8I', 6, file_offset, address, address,
                                       table_size, table_size, 4, 4))
        else:
            headers.append(base[old_offset + i * 32:old_offset + (i + 1) * 32])
    headers.append(struct.pack('<8I', 1, file_offset, address, address,
                               segment_size, segment_size, 5, 4096))
    output = bytearray(base)
    struct.pack_into('<I', output, 28, file_offset)
    struct.pack_into('<H', output, 44, phnum)
    target = address + code_offset + payload_entry
    # Thumb-2 LDR.W PC,[PC,#0], followed by an ARM-state destination (bit 0=0).
    # Tail transfer preserves LR, SP, argument registers and the fifth argument.
    output[ENTRY_OFFSET:ENTRY_OFFSET + 8] = bytes.fromhex('dff800f0') + struct.pack('<I', target)
    output.extend(bytes(file_offset - len(output)))
    output.extend(b''.join(headers))
    output.extend(bytes(code_offset - table_size))
    output.extend(payload)
    manifest = {
        'prototype': 'chacha20-neon-v2', 'deployment_validated': False,
        'base_sha256': base_sha256, 'base_identity': SUPPORTED_BASES[base_sha256],
        'base_bytes': len(base),
        'patched_sha256': digest(output),
        'upstream_tag': 'OpenSSL_1_1_1w', 'upstream_sources_sha256': SOURCES,
        'payload_sha256': digest(payload), 'payload_bytes': len(payload),
        'entry_va': ENTRY_VA, 'entry_file_offset': ENTRY_OFFSET,
        'entry_original_hex': ENTRY_BYTES.hex(),
        'entry_patch_hex': output[ENTRY_OFFSET:ENTRY_OFFSET + 8].hex(),
        'payload_entry_va': target, 'neon_entry_va': address + code_offset + neon_entry,
        'new_segment_va': address, 'new_segment_offset': file_offset,
        'new_segment_bytes': segment_size, 'new_segment_permissions': 'RX',
        'program_headers': phnum,
        'linux54_at_phdr': first_load_bias + file_offset,
        'limits': ['Cortex-A7/NEON required; no runtime feature probe',
                   'ChaCha20 only; Poly1305 and AES-GCM unchanged',
                   'Build step does not perform device validation; see docs/CRYPTO_RESEARCH.md for tested hashes',
                   'Not an uploadable firmware package'],
    }
    validate_patch(base, bytes(output), manifest)
    return bytes(output), manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--binary', type=Path)
    source.add_argument('--firmware', type=Path)
    parser.add_argument('--openssl-source', type=Path, required=True,
                        help='directory containing pinned chacha-armv4.pl and arm_arch.h')
    parser.add_argument('--clang', default=shutil.which('clang') or 'clang')
    parser.add_argument('--output', type=Path, default=Path('build/crypto-neon'))
    args = parser.parse_args()
    if args.firmware:
        data = args.firmware.read_bytes()
        require(hashlib.md5(data).hexdigest() == ZIP_MD5, 'firmware MD5 mismatch')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = [n for n in archive.namelist()
                     if n.removeprefix('./') == 'usr/z-loader/bin/z-tesla']
            require(len(names) == 1, 'unexpected firmware executable match count')
            base = archive.read(names[0])
    else:
        base = args.binary.read_bytes()
    require(digest(base) in SUPPORTED_BASES, 'unsupported z-tesla SHA-256')
    # An output directory must be new: never overwrite an earlier experiment.
    require(not args.output.exists(), 'output directory already exists')
    payload, entry, neon = compile_payload(args.openssl_source.resolve(), args.clang)
    patched, manifest = patch_binary(base, payload, entry, neon)
    args.output.mkdir(parents=True)
    binary = args.output / 'z-tesla.neon'
    binary.write_bytes(patched)
    binary.chmod(0o755)
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
