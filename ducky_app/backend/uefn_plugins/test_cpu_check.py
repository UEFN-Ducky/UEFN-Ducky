"""cpu_check: a compiled plugin only ships with instructions every 64-bit PC runs."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from backend.uefn_plugins import compile as compile_mod
from backend.uefn_plugins.cpu_check import code_sections, problems

BASELINE = bytes.fromhex(
    "4801d8"  # add rax, rbx
    "f30f58c1"  # addss xmm0, xmm1 (SSE)
    "660fefc0"  # pxor xmm0, xmm0 (SSE2)
    "f3480fbcc0"  # tzcnt rax, rax: BSF on CPUs without BMI1
    "c3"  # ret
)
AVX2 = bytes.fromhex("c5fdfec1")  # vpaddd ymm0, ymm0, ymm1
AVX512 = bytes.fromhex("62f17c4858c1")  # vaddps zmm0, zmm0, zmm1
BMI2 = bytes.fromhex("c4e2fbf7c1")  # shlx rax, rcx, rax
POPCNT = bytes.fromhex("f3480fb8c0")  # popcnt rax, rax


def _pe(code: bytes, *, data: bytes = b"\xc5\xfd\xfe\xc1") -> bytes:
    """A minimal x64 PE: one code section, one data section (never decoded)."""
    pe_at, opt_size = 0x40, 0xF0
    table = pe_at + 24 + opt_size
    head = bytearray(0x400)
    head[:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, pe_at)
    head[pe_at:pe_at + 4] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", head, pe_at + 4, 0x8664, 2, 0, 0, 0, opt_size, 0x2022)
    sections = ((b".text", code, 0x60000020, 0x400), (b".rdata", data, 0x40000040, 0x400 + len(code)))
    for i, (name, body, flags, raw_at) in enumerate(sections):
        at = table + 40 * i
        head[at:at + 8] = name.ljust(8, b"\0")
        struct.pack_into("<IIII", head, at + 8, len(body), 0x1000 * (i + 1), len(body), raw_at)
        struct.pack_into("<I", head, at + 36, flags)
    return bytes(head) + code + data


def _elf(code: bytes, *, data: bytes = b"\xc5\xfd\xfe\xc1", machine: int = 0x3E) -> bytes:
    """A minimal 64-bit ELF (a Linux .so): .text (executable), .rodata (never decoded), .shstrtab."""
    names = b"\0.text\0.rodata\0.shstrtab\0"
    text_at, data_at = 0x40, 0x40 + len(code)
    names_at = data_at + len(data)
    shoff = (names_at + len(names) + 7) & ~7
    head = bytearray(0x40)
    head[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<HHI", head, 0x10, 3, machine, 1)
    struct.pack_into("<Q", head, 0x28, shoff)
    struct.pack_into("<HHHHHH", head, 0x34, 0x40, 0, 0, 0x40, 4, 3)
    body = bytearray(head + code + data + names)
    body += b"\0" * (shoff - len(body))
    for name_off, kind, flags, at, size in ((0, 0, 0, 0, 0), (1, 1, 0x6, text_at, len(code)),
                                             (7, 1, 0x2, data_at, len(data)), (15, 3, 0, names_at, len(names))):
        body += struct.pack("<IIQQQQIIQQ", name_off, kind, flags, at, at, size, 0, 0, 1, 0)
    return bytes(body)


def test_baseline_code_runs_everywhere():
    assert problems(_pe(BASELINE)) == []
    # AVX bytes in a data section are data, not code.
    assert [name for name, _rva, _code in code_sections(_pe(BASELINE))] == [".text"]


@pytest.mark.parametrize(("code", "feature"), [
    (AVX2, "AVX2"), (AVX512, "AVX512F"), (BMI2, "BMI2"), (POPCNT, "POPCNT"),
])
def test_newer_instructions_are_named_with_where(code, feature):
    found = problems(_pe(BASELINE[:-1] + code + b"\xc3"))
    assert len(found) == 1 and found[0].startswith(f"{feature}: .text+0x")


def test_only_windows_x64_modules():
    with pytest.raises(ValueError, match="no MZ"):
        problems(b"\0" * 0x100)
    arm = bytearray(_pe(BASELINE))
    struct.pack_into("<H", arm, 0x44, 0xAA64)
    with pytest.raises(ValueError, match="not a 64-bit x86"):
        problems(bytes(arm))


def test_the_build_refuses_a_module_some_cpus_cant_run(tmp_path: Path):
    bad = tmp_path / "uefn_plugin_x.pyd"
    bad.write_bytes(_pe(AVX512 + b"\xc3"))
    with pytest.raises(compile_mod.CompileError, match="illegal instruction") as err:
        compile_mod._assert_runs_on_every_cpu(bad)
    assert "AVX512F" in str(err.value)
    good = tmp_path / "uefn_plugin_y.pyd"
    good.write_bytes(_pe(BASELINE))
    compile_mod._assert_runs_on_every_cpu(good)


ENDBR64 = bytes.fromhex("f30f1efa")


def test_linux_builds_are_held_to_the_same_list(tmp_path: Path):
    assert problems(_elf(ENDBR64 + BASELINE)) == []  # gcc's CET mark is a hint NOP everywhere
    assert [name for name, _at, _code in code_sections(_elf(BASELINE))] == [".text"]
    found = problems(_elf(BASELINE[:-1] + AVX2 + b"\xc3"))
    assert len(found) == 1 and found[0].startswith("AVX2: .text+0x")
    assert problems(_pe(ENDBR64 + b"\xc3")) != []  # Windows keeps its list exactly
    bad = tmp_path / "uefn_plugin_x.cpython-313-x86_64-linux-gnu.so"
    bad.write_bytes(_elf(AVX512 + b"\xc3"))
    with pytest.raises(compile_mod.CompileError, match="illegal instruction") as err:
        compile_mod._assert_runs_on_every_cpu(bad)
    assert "AVX512F" in str(err.value)
    good = tmp_path / "uefn_plugin_y.cpython-313-x86_64-linux-gnu.so"
    good.write_bytes(_elf(BASELINE))
    compile_mod._assert_runs_on_every_cpu(good)
    arm = tmp_path / "uefn_plugin_z.cpython-313-aarch64-linux-gnu.so"
    arm.write_bytes(_elf(AVX512 + b"\xc3", machine=0xB7))
    compile_mod._assert_runs_on_every_cpu(arm)  # no arm64 disassembler: gcc's armv8-a baseline holds
    with pytest.raises(ValueError, match="not a 64-bit x86"):
        problems(arm.read_bytes())
