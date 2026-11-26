"""Will a compiled plugin run on every PC? Disassemble its code and look.

Account 1.0.50 came out of the build with the build PC's AVX-512 in it, and Ducky closed
with an illegal instruction (0xC000001D) on every CPU without it. The build engine pins
baseline x86-64 (compile.BASELINE_CPU_FLAGS); this proves the result: every instruction
in a Windows module's code must be one that any CPU running 64-bit Windows has. The
UEFN Ducky Store runs the same check (same disassembler, same list) on each upload.
A Linux x86_64 build (an ELF ``.so``) is held to the same list.
"""

from __future__ import annotations

import struct

# What every 64-bit Windows PC has: x86-64 itself (SSE2), plus what Windows 8.1+ demands
# (CMPXCHG16B, PREFETCHW). Anything else, AVX/AVX2/AVX-512, BMI, FMA, SSE3+ and every
# newer extension, is missing on CPUs people use (Pentium and Celeron lack AVX and BMI).
BASELINE_FEATURES = frozenset({
    "INTEL8086", "INTEL186", "INTEL286", "INTEL386", "INTEL486", "X64",
    "FPU", "FPU287", "FPU387", "MMX", "SSE", "SSE2",
    "CMOV", "CX8", "CMPXCHG16B", "CPUID", "TSC", "FXSR", "SYSCALL",
    "CLFSH", "PAUSE", "MULTIBYTENOP", "PREFETCHW",
})
# TZCNT is BSF with a prefix older CPUs ignore, so they run it; compilers emit it for baseline.
BASELINE_MNEMONICS = frozenset({"TZCNT"})
# Linux gcc marks every function with ENDBR64 (control-flow protection): a hint NOP that CPUs
# without CET skip, so it runs everywhere. Windows builds never carry it.
ELF_BASELINE_MNEMONICS = frozenset({"ENDBR64", "ENDBR32"})

_IMAGE_SCN_CNT_CODE = 0x00000020
_IMAGE_SCN_MEM_EXECUTE = 0x20000000
_MAX_EXAMPLES = 3

ELF_MAGIC = b"\x7fELF"
ELF_X86_64 = 0x3E
ELF_AARCH64 = 0xB7
_SHF_EXECINSTR = 0x4
_SHT_NOBITS = 8


PE_ARM64 = 0xAA64


def pe_machine(image: bytes) -> int | None:
    """COFF ``Machine`` of a PE image (a Windows .pyd), None when it is not one."""
    if len(image) < 0x40 or image[:2] != b"MZ":
        return None
    pe = struct.unpack_from("<I", image, 0x3C)[0]
    if len(image) < pe + 6 or image[pe:pe + 4] != b"PE\0\0":
        return None
    return struct.unpack_from("<H", image, pe + 4)[0]


def elf_machine(image: bytes) -> int | None:
    """``e_machine`` of a 64-bit little-endian ELF image, None when it is not one."""
    if len(image) < 0x40 or image[:4] != ELF_MAGIC or image[4] != 2 or image[5] != 1:
        return None
    return struct.unpack_from("<H", image, 0x12)[0]


def _elf_code_sections(image: bytes) -> list[tuple[str, int, bytes]]:
    """The executable sections of an x86_64 ELF image (a Linux .so): (name, address, bytes)."""
    machine = elf_machine(image)
    if machine != ELF_X86_64:
        raise ValueError(f"not a 64-bit x86 module (machine 0x{machine or 0:04x})")
    shoff = struct.unpack_from("<Q", image, 0x28)[0]
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", image, 0x3A)
    headers = [struct.unpack_from("<IIQQQQ", image, shoff + shentsize * i) for i in range(shnum)]
    names_at = headers[shstrndx][4] if shstrndx < shnum else 0
    out: list[tuple[str, int, bytes]] = []
    for name_off, kind, flags, addr, offset, size in headers:
        if flags & _SHF_EXECINSTR and kind != _SHT_NOBITS:
            end = image.find(b"\0", names_at + name_off)
            name = image[names_at + name_off:end].decode("ascii", "replace") if names_at else ""
            out.append((name, addr, image[offset:offset + size]))
    return out


def code_sections(image: bytes) -> list[tuple[str, int, bytes]]:
    """The executable sections of a PE image (a .pyd/.dll) or x86_64 ELF (.so): (name, rva, bytes)."""
    if image[:4] == ELF_MAGIC:
        return _elf_code_sections(image)
    if len(image) < 0x40 or image[:2] != b"MZ":
        raise ValueError("not a Windows module (no MZ header)")
    pe = struct.unpack_from("<I", image, 0x3C)[0]
    if image[pe:pe + 4] != b"PE\0\0":
        raise ValueError("not a Windows module (no PE header)")
    machine, count = struct.unpack_from("<HH", image, pe + 4)
    if machine != 0x8664:
        raise ValueError(f"not a 64-bit x86 module (machine 0x{machine:04x})")
    opt_size = struct.unpack_from("<H", image, pe + 20)[0]
    table = pe + 24 + opt_size
    out: list[tuple[str, int, bytes]] = []
    for i in range(count):
        at = table + 40 * i
        name = image[at:at + 8].rstrip(b"\0").decode("ascii", "replace")
        vsize, rva, raw_size, raw_at = struct.unpack_from("<IIII", image, at + 8)
        flags = struct.unpack_from("<I", image, at + 36)[0]
        if flags & (_IMAGE_SCN_CNT_CODE | _IMAGE_SCN_MEM_EXECUTE):
            size = min(vsize or raw_size, raw_size)
            out.append((name, rva, image[raw_at:raw_at + size]))
    return out


def problems(image: bytes) -> list[str]:
    """What in this module's code some 64-bit CPUs can't run, one line per extension
    (with up to 3 example instructions). Empty means it runs everywhere."""
    import iced_x86

    names = {getattr(iced_x86.CpuidFeature, n): n for n in dir(iced_x86.CpuidFeature) if n.isupper()}
    mnemonics = {getattr(iced_x86.Mnemonic, n): n for n in dir(iced_x86.Mnemonic) if n.isupper()}
    baseline = BASELINE_MNEMONICS | ELF_BASELINE_MNEMONICS if image[:4] == ELF_MAGIC else BASELINE_MNEMONICS
    allowed = {m for m, n in mnemonics.items() if n in baseline}
    found: dict[str, list[str]] = {}
    formatter = iced_x86.Formatter(iced_x86.FormatterSyntax.INTEL)
    for section, rva, code in code_sections(image):
        decoder = iced_x86.Decoder(64, code, ip=rva)
        for instr in decoder:
            if instr.code == iced_x86.Code.INVALID or instr.mnemonic in allowed:
                continue
            for feature in instr.cpuid_features():
                name = names.get(feature, str(feature))
                if name in BASELINE_FEATURES:
                    continue
                examples = found.setdefault(name, [])
                if len(examples) < _MAX_EXAMPLES:
                    examples.append(f"{section}+0x{instr.ip - rva:x} {formatter.format(instr)}")
    return [f"{name}: {', '.join(examples)}" for name, examples in sorted(found.items())]
