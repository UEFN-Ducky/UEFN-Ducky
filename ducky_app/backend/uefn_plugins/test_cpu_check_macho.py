"""Mach-O (macOS .so) builds: arch from thin and fat headers, x86_64 code held to the baseline."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from backend.uefn_plugins import compile as build_engine
from backend.uefn_plugins import cpu_check

ARM64, X86_64 = 0x0100000C, 0x01000007


def macho(cpu: int, code: bytes = b"") -> bytes:
    """A minimal 64-bit Mach-O: header + one LC_SEGMENT_64 (__TEXT) with one __text section."""
    head = 32 + 72 + 80
    seg = struct.pack("<II16sQQQQiiII", 0x19, 72 + 80, b"__TEXT", 0, 0x1000, 0, head + len(code), 5, 5, 1, 0)
    sec = struct.pack("<16s16sQQIIIIIIII", b"__text", b"__TEXT", 0x1000, len(code), head, 0, 0, 0, 0x80000400, 0, 0, 0)
    hdr = struct.pack("<IiiIIIII", 0xFEEDFACF, cpu, 3, 6, 1, 72 + 80, 0, 0)
    return hdr + seg + sec + code


def fat(*slices: tuple[int, bytes]) -> bytes:
    out, body, at = struct.pack(">II", 0xCAFEBABE, len(slices)), b"", 8 + 20 * len(slices)
    for cpu, image in slices:
        out += struct.pack(">IIIII", cpu, 0, at + len(body), len(image), 0)
        body += image
    return out + body


def test_macho_arch_thin_and_fat() -> None:
    assert cpu_check.macho_arch(macho(ARM64)) == "arm64"
    assert cpu_check.macho_arch(macho(X86_64)) == "x86_64"
    assert cpu_check.macho_arch(fat((X86_64, macho(X86_64)), (ARM64, macho(ARM64)))) == "arm64+x86_64"
    assert cpu_check.macho_arch(b"\x7fELF" + b"\0" * 60) is None
    assert cpu_check.macho_arch(b"MZ") is None


def test_macho_x86_64_text_section_is_read() -> None:
    code = b"\x48\x31\xc0\xc3"  # xor rax, rax; ret
    sections = cpu_check.code_sections(fat((ARM64, macho(ARM64)), (X86_64, macho(X86_64, code))))
    assert sections == [("__TEXT,__text", 0x1000, code)]
    with pytest.raises(ValueError):
        cpu_check.code_sections(macho(ARM64))


def test_macho_x86_64_avx_is_refused() -> None:
    pytest.importorskip("iced_x86")
    assert cpu_check.problems(macho(X86_64, b"\x48\x31\xc0\xc3")) == []
    avx = b"\xc5\xfc\x77"  # vzeroall
    assert any(p.startswith("AVX") for p in cpu_check.problems(macho(X86_64, avx)))


def test_xbegin_into_nowhere_is_data_but_a_real_one_is_refused() -> None:
    pytest.importorskip("iced_x86")
    ret = b"\xc3" * 16
    # vim 1.0.14: data bytes C7 F8 rel32 read as xbegin to far outside the module
    assert cpu_check.problems(macho(X86_64, b"\xc7\xf8" + struct.pack("<i", -0x4EFF000) + ret)) == []
    # xbegin +0 (fallback right after it, inside __text): a real RTM transaction
    real = cpu_check.problems(macho(X86_64, b"\xc7\xf8\0\0\0\0" + ret))
    assert real and real[0].startswith("RTM: __TEXT,__text+0x0 xbegin")


def test_arm64_macho_skips_the_x86_check(tmp_path: Path) -> None:
    so = tmp_path / "demo.cpython-313-darwin.so"
    so.write_bytes(macho(ARM64, b"\xff" * 8))
    build_engine._assert_runs_on_every_cpu(so)  # no raise: arm64 relies on clang's baseline
