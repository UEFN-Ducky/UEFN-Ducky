"""Windows Controlled folder access: Ducky never asks about it on its own (Windows shows its own
notice, like for every app); a refused write says what it was and how to allow Ducky."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from frontend import folder_access as fa

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows only")

CLAUDE = r"C:\Users\pat\.local\bin\claude.exe"

_EVENT = ("<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System><EventID>1123</EventID>"
          "<TimeCreated SystemTime='{when}'/></System><EventData><Data Name='Product Name'>Microsoft Defender"
          "</Data><Data Name='Path'>C:\\Users\\pat\\Documents\\x.verse</Data><Data Name='Process Name'>{proc}"
          "</Data></EventData></Event>")


def test_defenders_block_log_is_read_with_program_and_refused_file(monkeypatch) -> None:
    xml = "".join(_EVENT.format(when=w, proc=p) for w, p in (
        ("2026-11-11T08:00:02.1Z", CLAUDE),
        ("2026-11-11T08:00:00.1Z", r"C:\Tools\R&amp;D\Node.EXE"),
    ))
    seen: list = []

    def fake_run(cmd, **_k):
        seen.append(cmd)
        return SimpleNamespace(stdout=xml, returncode=0)

    monkeypatch.setattr(fa.subprocess, "run", fake_run)
    assert fa._blocks(120_000) == [
        ("2026-11-11T08:00:02.1Z", CLAUDE, r"C:\Users\pat\Documents\x.verse"),
        ("2026-11-11T08:00:00.1Z", r"C:\Tools\R&D\Node.EXE", r"C:\Users\pat\Documents\x.verse"),
    ]
    assert seen[0][:3] == ["wevtutil", "qe", fa.LOG] and "EventID=1123" in seen[0][3]


def test_a_refused_write_names_controlled_folder_access_only_when_windows_logged_it() -> None:
    island = r"C:\Users\pat\OneDrive\Documents\Fortnite Projects\abaa\Content\Python"
    logged = [("2026-11-11T08:00:00Z", r"C:\x\UEFN-Ducky-Bridge.exe", island + r"\.init_unreal.py.ab12.tmp")]
    windows: list = []

    def blocks(window_ms: int):
        windows.append(window_ms)
        return logged

    assert fa.refusal_hint(island + r"\init_unreal.py", blocks=blocks) == fa.REFUSED_HINT
    assert fa.refusal_hint(island, blocks=blocks) == fa.REFUSED_HINT  # the folder itself
    assert windows[0] == fa.REFUSAL_WINDOW_S * 1000
    assert fa.refusal_hint(r"C:\Users\pat\Desktop\x.txt", blocks=blocks) == ""  # folder permissions
    assert "Controlled folder access" in fa.REFUSED_HINT and "UEFN-Ducky-Bridge.exe" in fa.REFUSED_HINT


def test_ducky_never_pops_its_own_box_or_admin_prompt() -> None:
    """Users got 'Windows is blocking your project files' over everything after updates (Nov 2026);
    big apps leave this to Windows' own notice."""
    src = Path(fa.__file__).read_text(encoding="utf-8")
    for gone in ("MessageBoxW", "runas", "Add-MpPreference", "check_soon", "after_turn"):
        assert gone not in src, gone


def test_a_device_path_in_the_log_becomes_a_drive_path() -> None:
    import ctypes

    buf = ctypes.create_unicode_buffer(1024)
    assert ctypes.windll.kernel32.QueryDosDeviceW("C:", buf, len(buf))
    assert fa._dos_path(buf.value + r"\Tools\claude.exe") == r"C:\Tools\claude.exe"
    assert fa._dos_path(CLAUDE) == CLAUDE
