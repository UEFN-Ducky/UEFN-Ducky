"""lore_cli parsing + orchestration on captured lore 0.8.6 output from test_ducky_v2 (no lore.exe)."""

from __future__ import annotations

from pathlib import Path

import pytest

from frontend.ui_web.verse_editor.urc import detect_urc, lore_cli

SIG11 = "28195751d281c67a5a6c5fedac009e0f45d2fb82baf27c0e3cfedf81558ade92"
SIG12 = "0f7997cb7d402e68ba671180621d5f82089e144048f4e7ccf225726a6aa8dcdd"  # autosave, personal branch
SIG13 = "86c62332b5e0304cd977eaf325200325dfaa1122e4f976da3c0df597c7b242bb"  # check-in merging 12
SIG15 = "75912e7488cbbc63b2b4b24ad139b104f9c084814519fd959f806d436ad70680"
PERSONAL = "main-180ea60248f30c4b177f1db9e1bc1b1f"
ACTOR = "Content/__ExternalActors__/test_ducky_v2/8/UG/VYTNCN5KW3CYSAI2X494RQ.uasset"

# After an editor save: status compares with the personal branch's own remote, so "in sync".
STATUS_PERSONAL = f"""Repository ee8be99853d04268b2387ed5bf07177e
On branch {PERSONAL} revision 12 -> {SIG12}
Remote revision 12 -> {SIG12}
Local branch in sync with remote
"""

STATUS_MAIN_13 = f"""Repository ee8be99853d04268b2387ed5bf07177e
On branch main revision 13 -> {SIG13}
Remote revision 15 -> {SIG15}
Local branch is behind remote
"""

STATUS_WORKTREE = f"""Repository ee8be99853d04268b2387ed5bf07177e
On branch main revision 13 -> {SIG13}
Remote revision 13 -> {SIG13}
Local branch in sync with remote
Changes staged for commit:
A Content/Python/init_unreal.py
M test_ducky_v2.uefnproject
D Content/Old.uasset
Untracked files:
A Content/Developers/PC/
A Content/NewFolder/
"""

# lore history 200 --revision <SIG12>
HISTORY_PERSONAL_12 = f"""Revision  : 12
Signature : {SIG12}
*uefn.*CompatibilityVersion: 42.40
Branch    : 01a17919ee427003ba4d85660de9f591
Date      : Mon, 26 Oct 2026 16:45:06 +0000
    [Sauvegarde automatique] 1 modification
Creator   : Nacht
Committer : Nacht

Revision  : 11
Signature : {SIG11}
Merge     : f46ff6b0aea617655512c16558acd13367ed40873570974983c3e846569410ce
*uefn.*CompatibilityVersion: 42.40
Branch    : e726318bbc3fd75ac8733a7e030cc35b
Date      : Mon, 26 Oct 2026 16:32:23 +0000
Creator   : Mich
Committer : Mich
    Test sync 4 - envoi 30s apres sauvegarde
"""

# lore --remote history 200 --branch main, before the check-in
REMOTE_MAIN_11 = f"""Revision  : 11
Signature : {SIG11}
Merge     : f46ff6b0aea617655512c16558acd13367ed40873570974983c3e846569410ce
Branch    : e726318bbc3fd75ac8733a7e030cc35b
    Test sync 4 - envoi 30s apres sauvegarde

Revision  : 9
Signature : a7d9b3b798200f5ba1d970e8e06f640cf303550cdd728886134382d0d38250a8
Merge     : 04affc6f77e43083cd6d4e3918423070054c89ea6571931207408741086c89a8
    Test sync 3 - sauvegarde + envoi immediat
"""

# ... right after the check-in (13 merges the autosave 12)
REMOTE_MAIN_13 = f"""Revision  : 13
Signature : {SIG13}
Merge     : {SIG12}
*uefn.*CompatibilityVersion: 42.40
Branch    : e726318bbc3fd75ac8733a7e030cc35b
Date      : Mon, 26 Oct 2026 16:45:46 +0000
    Test outil 1 - cube deplace depuis le PC (retry)

{REMOTE_MAIN_11}"""

# ... after the other PC checked in again
REMOTE_MAIN_15 = f"""Revision  : 15
Signature : {SIG15}
Merge     : 22f76dbd96344b140ba518bafdf811e3ed4467f502a1b36a8b6c4d6683afe011
Branch    : e726318bbc3fd75ac8733a7e030cc35b
    Test outil 2 - sphere deplacee depuis le portable (revision_check_in)

{REMOTE_MAIN_13}"""

HISTORY_LOCAL_13 = REMOTE_MAIN_13

# lore revision diff <SIG11> --target <SIG12>
DIFF_11_12 = f"M {ACTOR}\n"

REVISION_DIFF = """A Content/__ExternalActors__/test_ducky_v2/1
A Content/__ExternalActors__/test_ducky_v2/1/8X
A Content/__ExternalActors__/test_ducky_v2/1/8X/2FWY3VPOAEBYKZPCJNGYE6.uasset
M Content/__ExternalActors__/test_ducky_v2/C/WX/VYXTJPVDYST7GZN1Z2XJFE.uasset
M Content/test_ducky_v2.umap
A Content/Developers/PC/Collections
D Content/Gone.uasset
"""


def _touch(root: Path, rel: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")


def _hist(text: str) -> list[dict]:
    return lore_cli.parse_history(text)


def test_parse_status_personal_branch() -> None:
    st = lore_cli.parse_status(STATUS_PERSONAL)
    assert st["repository"] == "ee8be99853d04268b2387ed5bf07177e"
    assert st["local_branch"] == PERSONAL
    assert (st["local_revision"], st["local_signature"]) == (12, SIG12)
    assert st["staged"] == [] and st["untracked"] == []


def test_parse_status_sections() -> None:
    st = lore_cli.parse_status(STATUS_WORKTREE)
    assert st["staged"] == [
        ("A", "Content/Python/init_unreal.py"),
        ("M", "test_ducky_v2.uefnproject"),
        ("D", "Content/Old.uasset"),
    ]
    assert st["untracked"] == [("A", "Content/Developers/PC/"), ("A", "Content/NewFolder/")]


def test_parse_history_revisions_and_merges() -> None:
    hist = _hist(REMOTE_MAIN_13)
    assert [r["revision"] for r in hist] == [13, 11, 9]
    assert hist[0]["signature"] == SIG13 and hist[0]["merges"] == [SIG12]
    # revision info prints both parents on one Merge line
    two = _hist(f"Revision  : 13\nSignature : {SIG13}\nMerge     : {SIG11} {SIG12}\n")
    assert two[0]["merges"] == [SIG11, SIG12]


def test_personal_autosave_is_ahead_of_main() -> None:
    cmp = lore_cli.compare_histories(_hist(HISTORY_PERSONAL_12), _hist(REMOTE_MAIN_11))
    assert cmp == {"merge_base": SIG11, "behind": False, "ahead": True, "in_sync": False}


def test_personal_autosave_merged_by_checkin_is_behind_not_ahead() -> None:
    cmp = lore_cli.compare_histories(_hist(HISTORY_PERSONAL_12), _hist(REMOTE_MAIN_13))
    assert cmp == {"merge_base": SIG12, "behind": True, "ahead": False, "in_sync": False}


def test_main_in_sync_and_behind() -> None:
    assert lore_cli.compare_histories(_hist(HISTORY_LOCAL_13), _hist(REMOTE_MAIN_13))["in_sync"]
    cmp = lore_cli.compare_histories(_hist(HISTORY_LOCAL_13), _hist(REMOTE_MAIN_15))
    assert cmp == {"merge_base": SIG13, "behind": True, "ahead": False, "in_sync": False}


def test_parse_entry_move_takes_new_path() -> None:
    assert lore_cli.parse_entry("R Content/A.uasset -> Content/B.uasset") == ("R", "Content/B.uasset")
    assert lore_cli.parse_entry("Untracked files:") is None


def test_to_pending_files_filters(tmp_path: Path) -> None:
    for rel in (
        "Content/__ExternalActors__/test_ducky_v2/1/8X/2FWY3VPOAEBYKZPCJNGYE6.uasset",
        "Content/__ExternalActors__/test_ducky_v2/1/8X/Untracked.uasset",
        "Content/test_ducky_v2.umap",
        "Content/Developers/PC/Collections/Mine.collection",
        "Content/Gone.uasset",
    ):
        _touch(tmp_path, rel)
    out = lore_cli.to_pending_files(str(tmp_path), lore_cli.parse_revision_diff(REVISION_DIFF))
    root = tmp_path.as_posix()
    # Bare dir lines skipped (no stray Untracked.uasset), missing file dropped, D dropped, Developers excluded.
    assert out == [
        f"{root}/Content/__ExternalActors__/test_ducky_v2/1/8X/2FWY3VPOAEBYKZPCJNGYE6.uasset",
        f"{root}/Content/test_ducky_v2.umap",
    ]


def test_to_pending_files_expands_untracked_dir(tmp_path: Path) -> None:
    _touch(tmp_path, "Content/NewFolder/A.uasset")
    _touch(tmp_path, "Content/NewFolder/Sub/B.uasset")
    _touch(tmp_path, "Content/Developers/PC/x.collection")
    st = lore_cli.parse_status(STATUS_WORKTREE)
    out = lore_cli.to_pending_files(str(tmp_path), st["untracked"])
    root = tmp_path.as_posix()
    assert out == [f"{root}/Content/NewFolder/A.uasset", f"{root}/Content/NewFolder/Sub/B.uasset"]


class FakeLore:
    def __init__(self, outputs: dict[tuple[str, ...], str]) -> None:
        self.outputs = outputs
        self.calls: list[list[str]] = []

    def __call__(self, project_root: str, args: list[str], timeout: float) -> tuple[bool, str]:
        self.calls.append(args)
        key = tuple(args)
        if key not in self.outputs:
            return False, f"unexpected lore call {args}"
        return True, self.outputs[key]


def _lore(status: str, local_sig: str, local_hist: str, remote_main: str, **extra: str) -> FakeLore:
    outputs = {
        ("status",): status,
        ("history", "200", "--revision", local_sig): local_hist,
        ("--remote", "history", "200", "--branch", "main"): remote_main,
    }
    outputs.update({tuple(k.split("|")): v for k, v in extra.items()})
    return FakeLore(outputs)


@pytest.fixture
def island(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / ".lore").mkdir()
    monkeypatch.setattr(
        lore_cli, "detect_lore_executable", lambda: {"available": True, "path": "lore.exe", "error": ""}
    )
    return tmp_path


def test_repo_state_personal_branch_vs_main(island: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lore_cli, "_run_lore", _lore(STATUS_PERSONAL, SIG12, HISTORY_PERSONAL_12, REMOTE_MAIN_11))
    st = lore_cli.repo_state(str(island))
    assert st["ok"] and st["error"] == ""
    assert st["branch"] == st["local_branch"] == PERSONAL
    assert st["remote_branch"] == "main"
    assert (st["local_revision"], st["remote_revision"]) == (12, 11)
    assert st["remote_signature"] == SIG11
    assert st["ahead"] and not st["behind"] and not st["in_sync"]


def test_pending_paths_personal_branch_autosave(island: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _touch(island, ACTOR)
    diff_key = f"revision|diff|{SIG11}|--target|{SIG12}"
    fake = _lore(STATUS_PERSONAL, SIG12, HISTORY_PERSONAL_12, REMOTE_MAIN_11, **{diff_key: DIFF_11_12})
    monkeypatch.setattr(lore_cli, "_run_lore", fake)
    out = lore_cli.pending_paths(str(island))
    assert out == {"ok": True, "paths": [f"{island.as_posix()}/{ACTOR}"], "error": ""}
    assert fake.calls[-1] == ["revision", "diff", SIG11, "--target", SIG12]


def test_pending_paths_behind_only_working_tree(island: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _touch(island, "Content/Python/init_unreal.py")
    _touch(island, "test_ducky_v2.uefnproject")
    fake = _lore(STATUS_WORKTREE, SIG13, HISTORY_LOCAL_13, REMOTE_MAIN_15)
    monkeypatch.setattr(lore_cli, "_run_lore", fake)
    out = lore_cli.pending_paths(str(island))
    root = island.as_posix()
    assert out["paths"] == [f"{root}/Content/Python/init_unreal.py", f"{root}/test_ducky_v2.uefnproject"]
    assert not any(call[0] == "revision" for call in fake.calls)


def test_repo_state_main_behind(island: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lore_cli, "_run_lore", _lore(STATUS_MAIN_13, SIG13, HISTORY_LOCAL_13, REMOTE_MAIN_15))
    st = lore_cli.repo_state(str(island))
    assert st["behind"] and not st["ahead"] and (st["local_revision"], st["remote_revision"]) == (13, 15)


def test_errors_never_raise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = lore_cli.pending_paths(str(tmp_path))
    assert out["ok"] is False and ".lore" in out["error"] and out["paths"] == []

    (tmp_path / ".lore").mkdir()
    monkeypatch.setattr(lore_cli, "_run_lore", lambda *a: (False, "lore status timed out after 60s"))
    monkeypatch.setattr(
        lore_cli, "detect_lore_executable", lambda: {"available": True, "path": "lore.exe", "error": ""}
    )
    state = lore_cli.repo_state(str(tmp_path))
    assert state == {"ok": False, "available": True, "error": "lore status timed out after 60s"}

    # Remote main shares nothing with the local history in range
    unrelated = "Revision  : 3\nSignature : ffff\n"
    monkeypatch.setattr(lore_cli, "_run_lore", _lore(STATUS_PERSONAL, SIG12, HISTORY_PERSONAL_12, unrelated))
    out = lore_cli.pending_paths(str(tmp_path))
    assert out["ok"] is False and "no revision shared" in out["error"]

    monkeypatch.setattr(lore_cli, "detect_lore_executable", lambda: {"available": False, "error": "nope"})
    assert lore_cli.repo_state(str(tmp_path))["available"] is False


def test_detect_lore_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = tmp_path / "lore.exe"
    exe.write_bytes(b"")
    monkeypatch.setenv("LORE_CLI_LOCATION", str(exe))
    out = detect_urc.detect_lore_executable()
    assert out == {"available": True, "path": str(exe), "source": "lore_cli_location", "error": ""}


def test_parse_history_creator_and_message() -> None:
    hist = _hist(HISTORY_PERSONAL_12)
    assert (hist[0]["creator"], hist[0]["message"]) == ("Nacht", "[Sauvegarde automatique] 1 modification")
    assert (hist[1]["creator"], hist[1]["message"]) == ("Mich", "Test sync 4 - envoi 30s apres sauvegarde")
    assert lore_cli.display_name("Michㅤㅤㅤㅤou") == "Mich ou"
    assert lore_cli.display_name("Nachtㅤ") == "Nacht"


def test_incoming_names_what_the_other_pc_pushed() -> None:
    out = lore_cli.incoming(_hist(HISTORY_LOCAL_13), _hist(REMOTE_MAIN_15))
    assert [(r["revision"], r["own"]) for r in out] == [(15, False)]
    assert out[0]["message"].startswith("Test outil 2")
    assert lore_cli.incoming(_hist(HISTORY_LOCAL_13), _hist(REMOTE_MAIN_13)) == []


def test_incoming_own_check_in_of_the_autosave() -> None:
    # Local still on the personal autosave 12; main's 13 merges it: behind, but nothing new.
    out = lore_cli.incoming(_hist(HISTORY_PERSONAL_12), _hist(REMOTE_MAIN_13))
    assert [(r["revision"], r["own"]) for r in out] == [(13, True)]


def test_repo_state_lists_incoming(island: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lore_cli, "_run_lore", _lore(STATUS_MAIN_13, SIG13, HISTORY_LOCAL_13, REMOTE_MAIN_15))
    st = lore_cli.repo_state(str(island))
    assert [r["revision"] for r in st["incoming"]] == [15]


def test_remote_and_local_head_one_call_each(island: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeLore({("--remote", "history", "1", "--branch", "main"): REMOTE_MAIN_15, ("status",): STATUS_MAIN_13})
    monkeypatch.setattr(lore_cli, "_run_lore", fake)
    assert lore_cli.remote_head(str(island)) == {"ok": True, "revision": 15, "signature": SIG15, "error": ""}
    assert lore_cli.local_head(str(island)) == {"ok": True, "revision": 13, "signature": SIG13, "changes": [],
                                                "error": ""}
    assert len(fake.calls) == 2
    # The same status call lists the working tree (the island watch's overlap reads it).
    fake.outputs[("status",)] = STATUS_WORKTREE
    assert lore_cli.local_head(str(island))["changes"][:2] == [("A", "Content/Python/init_unreal.py"),
                                                               ("M", "test_ducky_v2.uefnproject")]
    monkeypatch.setattr(lore_cli, "_run_lore", lambda *a: (False, "lore history timed out after 30s"))
    assert lore_cli.remote_head(str(island)) == {"ok": False, "error": "lore history timed out after 30s"}


def test_heads_need_a_lore_folder(tmp_path: Path) -> None:
    assert lore_cli.remote_head(str(tmp_path))["ok"] is False
    assert lore_cli.local_head(str(tmp_path))["ok"] is False


# Captured live on test_ducky_v2 (Oct 26): the laptop's account modified an actor.
LOCKED = "Content/__ExternalActors__/test_ducky_v2/1/8X/2FWY3VPOAEBYKZPCJNGYE6.uasset"
LOCK_QUERY = f"Locks found:\n{LOCKED} by Michㅤㅤㅤㅤou on branch e726318bbc3fd75ac8733a7e030cc35b\n"
LOCK_QUERY_NONE = "Locks found:\n"
LOCK_STATUS = f"Files locked for edit:\n{LOCKED} by Michㅤㅤㅤㅤou on Mon, 26 Oct 2026 20:43:53 +0000\n"
AUTH_INFO = "ID: 01978ee6969170d7b3c32f095502d2c8\nName: Nachtㅤ\n"
LOCK_KEY = ("lock", "query", "--branch", "main")


def test_parse_locks_query_and_status() -> None:
    want = [{"rel": LOCKED, "owner_raw": "Michㅤㅤㅤㅤou"}]
    assert lore_cli.parse_locks(LOCK_QUERY) == want
    assert lore_cli.parse_locks(LOCK_STATUS) == want
    assert lore_cli.parse_locks(LOCK_QUERY_NONE) == []
    spaced = "Locks found:\nContent/My Folder/A by B.uasset by Nachtㅤ on branch abc\n"
    assert lore_cli.parse_locks(spaced) == [{"rel": "Content/My Folder/A by B.uasset", "owner_raw": "Nachtㅤ"}]


def test_parse_auth_info() -> None:
    assert lore_cli.parse_auth_info(AUTH_INFO) == {"id": "01978ee6969170d7b3c32f095502d2c8", "name": "Nachtㅤ"}
    assert lore_cli.parse_auth_info("") == {"id": "", "name": ""}


@pytest.fixture
def no_me_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lore_cli, "_me_cache", {})


def test_locks_other_pc_and_mine(island: Path, monkeypatch: pytest.MonkeyPatch, no_me_cache) -> None:
    mine = f"{LOCK_QUERY}Content/Mine.uasset by Nachtㅤ on branch e726318bbc3fd75ac8733a7e030cc35b\n"
    fake = FakeLore({LOCK_KEY: mine, ("auth", "info"): AUTH_INFO})
    monkeypatch.setattr(lore_cli, "_run_lore", fake)
    out = lore_cli.locks(str(island))
    assert out["ok"] and out["me"] == "Nacht" and out["error"] == ""
    assert out["locks"] == [
        {"path": f"{island.as_posix()}/{LOCKED}", "rel": LOCKED, "owner": "Mich ou", "owner_raw": "Michㅤㅤㅤㅤou",
         "mine": False},
        {"path": f"{island.as_posix()}/Content/Mine.uasset", "rel": "Content/Mine.uasset", "owner": "Nacht",
         "owner_raw": "Nachtㅤ", "mine": True},
    ]
    # auth info is cached: the next round is one lore call
    fake.calls.clear()
    assert lore_cli.locks(str(island))["ok"]
    assert fake.calls == [list(LOCK_KEY)]


def test_locks_none(island: Path, monkeypatch: pytest.MonkeyPatch, no_me_cache) -> None:
    monkeypatch.setattr(lore_cli, "_run_lore", FakeLore({LOCK_KEY: LOCK_QUERY_NONE, ("auth", "info"): AUTH_INFO}))
    assert lore_cli.locks(str(island)) == {"ok": True, "locks": [], "me": "Nacht", "error": ""}


def test_locks_unknown_me_is_not_ok(island: Path, monkeypatch: pytest.MonkeyPatch, no_me_cache) -> None:
    monkeypatch.setattr(lore_cli, "_run_lore", FakeLore({LOCK_KEY: LOCK_QUERY}))  # auth info fails
    out = lore_cli.locks(str(island))
    assert out["ok"] is False and "auth info" in out["error"]
    assert [lk["mine"] for lk in out["locks"]] == [False]
    assert lore_cli._me_cache == {}  # a failure isn't cached


def test_locks_errors_never_raise(tmp_path: Path, island: Path, monkeypatch: pytest.MonkeyPatch, no_me_cache) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert lore_cli.locks(str(plain)) == {"ok": False, "locks": [], "me": "",
                                          "error": "not a Lore repository (no .lore folder)"}
    monkeypatch.setattr(lore_cli, "_run_lore", lambda *a: (False, "lore lock timed out after 60s"))
    assert lore_cli.locks(str(island))["error"] == "lore lock timed out after 60s"

    def boom(*a):
        raise RuntimeError("bad")

    monkeypatch.setattr(lore_cli, "_run_lore", boom)
    assert lore_cli.locks(str(island))["ok"] is False


# --------------------------------------------------------------------------- overlap (step 7)

SIG17 = "372e001ffd979c52b69a388df7d25f2ff79a4425826c84347088519c7d696860"
SIG19 = "8f22c37a2922d17e904a649a223e3ee1b134ad9a1bc5408d438f65a9933d352b"
SPHERE = "Content/__ExternalActors__/test_ducky_v2/1/8X/2FWY3VPOAEBYKZPCJNGYE6.uasset"

REMOTE_MAIN_17 = f"""Revision  : 17
Signature : {SIG17}
Merge     : 0ab87e97481a5256a3c082190aae9bccc07312d233dc32a7d93d06e57af6d697
Creator   : Nachtㅤ
    Test outil 3 - cube deplace depuis le PC (revision_push complet)

{REMOTE_MAIN_15}"""

REMOTE_MAIN_19 = f"""Revision  : 19
Signature : {SIG19}
Merge     : 887880ace9667f561f0d491e3145008347cb082fa3f0901daf915a12277b5d93
Creator   : Michㅤㅤㅤㅤou
    Test alerte - sphere deplacee depuis le portable

{REMOTE_MAIN_17}"""

# lore revision diff <SIG17> --target <SIG19> (captured live)
DIFF_17_19 = f"M {SPHERE}\n"

# lore revision diff <rev 1> --target <rev 5> (captured live): folders come before their files.
DIFF_1_5 = """A Content/Developers/PC
A Content/Developers/PC/Collections
A Content/Python
A Content/Python/init_unreal.py
A Content/__ExternalActors__/test_ducky_v2/8/UG
A Content/__ExternalActors__/test_ducky_v2/8/UG/VYTNCN5KW3CYSAI2X494RQ.uasset
M Content/__ExternalActors__/test_ducky_v2/C/WX/VYXTJPVDYST7GZN1Z2XJFE.uasset
M Content/test_ducky_v2.umap
M test_ducky_v2.uefnproject
"""

# This PC on main 17, a Verse file and the sphere edited outside UEFN, the laptop pushed 19.
STATUS_MAIN_17_EDITED = f"""Repository ee8be99853d04268b2387ed5bf07177e
On branch main revision 17 -> {SIG17}
Remote revision 17 -> {SIG17}
Local branch in sync with remote
Changes staged for commit:
M {SPHERE}
Untracked files:
A Content/conflict_test.verse
"""

INCOMING_KEY = f"revision|diff|{SIG17}|--target|{SIG19}"


def test_incoming_paths_skips_folders_and_flags_deletions(island: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeLore({("revision", "diff", "base", "--target", "head"): DIFF_1_5 + "D Content/Gone.uasset\n"})
    monkeypatch.setattr(lore_cli, "_run_lore", fake)
    state = {"ok": True, "behind": True, "merge_base": "base", "remote_signature": "head"}
    out = lore_cli.incoming_paths(str(island), state=state)
    root = island.as_posix()
    assert out["ok"] and [(f["code"], f["rel"]) for f in out["files"]] == [
        ("A", "Content/Python/init_unreal.py"),
        ("A", "Content/__ExternalActors__/test_ducky_v2/8/UG/VYTNCN5KW3CYSAI2X494RQ.uasset"),
        ("M", "Content/__ExternalActors__/test_ducky_v2/C/WX/VYXTJPVDYST7GZN1Z2XJFE.uasset"),
        ("M", "Content/test_ducky_v2.umap"),
        ("M", "test_ducky_v2.uefnproject"),
        ("D", "Content/Gone.uasset"),
    ]
    assert out["files"][0]["path"] == f"{root}/Content/Python/init_unreal.py"
    assert [f["deleted"] for f in out["files"]] == [False] * 5 + [True]
    assert len(fake.calls) == 1


def test_overlap_names_the_file_changed_on_both_pcs(island: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _lore(STATUS_MAIN_17_EDITED, SIG17, REMOTE_MAIN_17, REMOTE_MAIN_19, **{INCOMING_KEY: DIFF_17_19})
    monkeypatch.setattr(lore_cli, "_run_lore", fake)
    out = lore_cli.overlap(str(island))
    assert out == {"ok": True, "error": "", "files": [
        {"rel": SPHERE, "path": f"{island.as_posix()}/{SPHERE}", "local": "modified", "remote": "M"},
    ]}
    # repo_state (3 calls) + the incoming diff; not ahead, so no local diff
    assert len(fake.calls) == 4 and fake.calls[-1] == ["revision", "diff", SIG17, "--target", SIG19]


def test_overlap_with_a_state_costs_only_the_diffs(island: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Ahead (an autosave main lacks) and behind (the laptop pushed): both diffs are read.
    state = {"ok": True, "behind": True, "ahead": True, "merge_base": "base", "local_signature": "mine",
             "remote_signature": "theirs",
             "changes": [("D", "Content/Both.uasset"), ("A", "Content/NewFolder/"), ("M", "Content/Here.verse")]}
    fake = FakeLore({
        ("revision", "diff", "base", "--target", "theirs"): (
            f"M {ACTOR}\nA Content/NewFolder\nA Content/NewFolder/A.verse\nD Content/Both.uasset\n"
            "M content/here.verse\nM Content/OnlyThere.uasset\n"
        ),
        ("revision", "diff", "base", "--target", "mine"): DIFF_11_12,
    })
    monkeypatch.setattr(lore_cli, "_run_lore", fake)
    out = lore_cli.overlap(str(island), state=state)
    assert [(f["rel"], f["local"], f["remote"]) for f in out["files"]] == [
        (ACTOR, "modified", "M"),  # this PC's autosave commit
        ("Content/NewFolder/A.verse", "added", "A"),  # under an untracked folder
        ("content/here.verse", "modified", "M"),  # Windows paths: case doesn't matter
    ]  # deleted on both sides: nothing to choose
    assert len(fake.calls) == 2


def test_overlap_when_not_behind_reads_nothing_more(island: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeLore({})
    monkeypatch.setattr(lore_cli, "_run_lore", fake)
    state = {"ok": True, "behind": False, "ahead": True, "changes": [("M", "Content/A.verse")]}
    assert lore_cli.overlap(str(island), state=state) == {"ok": True, "files": [], "error": ""}
    assert fake.calls == []


def test_overlap_errors_never_raise(island: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = {"ok": True, "behind": True, "ahead": False, "merge_base": "b", "remote_signature": "t"}
    monkeypatch.setattr(lore_cli, "_run_lore", lambda *a: (False, "lore revision timed out after 60s"))
    assert lore_cli.overlap(str(island), state=state) == {"ok": False, "files": [],
                                                          "error": "lore revision timed out after 60s"}
    assert lore_cli.overlap(str(island), state={"ok": False, "error": "no lore"})["error"] == "no lore"
    assert lore_cli.overlap(str(island), state={"ok": True, "behind": True})["ok"] is False  # malformed


# Oct 27, live: Lore merged a Verse file changed on both PCs with these markers, and wrote
# conflict_test.verse~base / ~mine / ~theirs beside it.
MERGED_VERSE = """using { /Verse.org/Simulation }
<<<<<<< ours
# laptop edit
||||||| original
=======
# main PC edit
>>>>>>> theirs
"""


def test_pending_paths_never_lists_lores_merge_side_files(island: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sides = ("", "~base", "~mine", "~THEIRS")
    status = STATUS_MAIN_13 + "Untracked files:\n" + "".join(f"A Content/conflict_test.verse{s}\n" for s in sides)
    for s in sides:
        _touch(island, f"Content/conflict_test.verse{s}")
    monkeypatch.setattr(lore_cli, "_run_lore", _lore(status, SIG13, HISTORY_LOCAL_13, REMOTE_MAIN_15))
    assert lore_cli.pending_paths(str(island))["paths"] == [f"{island.as_posix()}/Content/conflict_test.verse"]


def test_conflict_marked_finds_lore_markers_in_text_files_only(tmp_path: Path) -> None:
    merged = tmp_path / "conflict_test.verse"
    merged.write_text(MERGED_VERSE, encoding="utf-8")
    clean = tmp_path / "clean.verse"
    clean.write_text("# <<<<<<< not at a line start\nx := 1\n", encoding="utf-8")
    half = tmp_path / "half.json"
    half.write_text("<<<<<<< ours\n{}\n", encoding="utf-8")
    binary = tmp_path / "A.uasset"
    binary.write_bytes(MERGED_VERSE.encode())
    paths = [p.as_posix() for p in (merged, clean, half, binary)] + [(tmp_path / "gone.verse").as_posix()]
    assert lore_cli.conflict_marked(paths) == [merged.as_posix()]
