"""Nothing that identifies a person or unlocks an account survives backend.util.privacy.scrub."""

from __future__ import annotations

from pathlib import Path

from backend.util.privacy import scrub


def test_home_folder_becomes_tilde() -> None:
    home = str(Path.home())
    assert scrub(home + r"\Documents\island.verse") == r"~\Documents\island.verse"
    assert scrub(home.replace("\\", "/") + "/x") == "~/x"


def test_personal_data_is_removed_and_the_rest_stays_readable() -> None:
    line = (
        r"Plugin meshy failed for someone@example.com at C:\Users\otherperson\Desktop\a.glb "
        "Authorization: Bearer abcdefghijklmnop api_key=supersecretvalue password: hunter22 "
        "url=https://bob:pw123@host.example/x?X-Amz-Signature=deadbeef&size=3 "
        "sk-proj-AAAAAAAAAAAAAAAAAAAA ghp_BBBBBBBBBBBBBBBBBBBBBBBB from 203.0.113.7 via 127.0.0.1:4199"
    )
    out = scrub(line)
    for gone in (
        "someone@example.com", "otherperson", "abcdefghijklmnop", "supersecretvalue", "hunter22",
        "bob:pw123", "deadbeef", "sk-proj-AAAA", "ghp_BBBB", "203.0.113.7",
    ):
        assert gone not in out, gone
    for kept in ("Plugin meshy failed for", r"Desktop\a.glb", "size=3", "127.0.0.1:4199"):
        assert kept in out, kept


def test_ordinary_log_lines_are_untouched() -> None:
    for line in (
        "[2026-10-09 10:00:00] (agent) max_tokens: 4096, tokens used 1200",
        "UEFN Ducky 1.2.357 on Windows 10.0.26300",
        "WebView2 process failed (main:2); reloading",
    ):
        assert scrub(line) == line
