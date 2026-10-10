"""Will a compiled plugin run on every PC? Disassemble its code and look.

Account 1.0.50 came out of the build with the build PC's AVX-512 in it, and Ducky closed
with an illegal instruction (0xC000001D) on every CPU without it. The build engine pins
baseline x86-64 (compile.BASELINE_CPU_FLAGS); this proves the result: every instruction
in a Windows module's code must be one that any CPU running 64-bit Windows has. The
UEFN Ducky Store runs the same check (same disassembler, same list) on each upload.
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

_IMAGE_SCN_CNT_CODE = 0x00000020
_IMAGE_SCN_MEM_EXECUTE = 0x20000000
_MAX_EXAMPLES = 3


def code_sections(image: bytes) -> list[tuple[str, int, bytes]]:
    """The executable sections of a PE image (a .pyd/.dll): (name, rva, bytes)."""
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
    allowed = {m for m, n in mnemonics.items() if n in BASELINE_MNEMONICS}
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
