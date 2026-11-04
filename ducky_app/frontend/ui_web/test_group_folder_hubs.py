"""Group folders: legacy hubs migrate; deleting a group takes its whole subtree."""

from __future__ import annotations

import tempfile
from pathlib import Path

from frontend.archive_folder import ARCHIVE_FOLDER_ID
from frontend.settings import PanelSettings
from frontend.ui_web.project_chats import (
    create_conversation,
    create_folder,
    delete_conversation,
    delete_folder,
    ensure_group_folder_hubs,
    load_conversation,
    load_folders,
    move_conversation,
    save_conversation,
    save_folders,
)


def _make_group(settings, name: str, parent_id: str, root: str):
    folder = create_folder(name, parent_id, root)
    hub = create_conversation(settings, folder.id, title=name, project_root=root)
    hub.is_group = True
    hub.group_members = []
    save_conversation(hub, root)
    folders = load_folders(root)
    for f in folders:
        if f.id == folder.id:
            f.group_hub_id = hub.id
            break
    save_folders(folders, root)
    return folder, hub


def test_delete_group_keeps_duckies_and_archives_the_group():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        settings = PanelSettings.load()
        folder, hub = _make_group(settings, "Squad", "", root)
        member = create_conversation(
            settings,
            folder.id,
            title="Verse Coder",
            parent_conv_id=hub.id,
            project_root=root,
        )
        nested, nested_hub = _make_group(settings, "Inner", folder.id, root)

        assert delete_folder(folder.id, root) == [hub.id]

        archived_hub = load_conversation(hub.id, project_root=root)
        assert archived_hub is not None
        assert archived_hub.folder_id == ARCHIVE_FOLDER_ID
        assert archived_hub.is_group
        kept = load_conversation(member.id, project_root=root)
        assert kept is not None
        assert kept.folder_id == ""
        assert kept.parent_conv_id == ""
        folders = load_folders(root)
        assert all(f.id != folder.id for f in folders)
        moved = next(f for f in folders if f.id == nested.id)
        assert moved.parent_id == ""
        assert load_conversation(nested_hub.id, project_root=root) is not None
        assert ensure_group_folder_hubs(root) == 0


def test_delete_group_can_archive_duckies_too():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        settings = PanelSettings.load()
        folder, hub = _make_group(settings, "Squad", "", root)
        member = create_conversation(
            settings,
            folder.id,
            title="Verse Coder",
            parent_conv_id=hub.id,
            project_root=root,
        )

        assert delete_folder(folder.id, root, archive_members=True) == [hub.id]

        archived_hub = load_conversation(hub.id, project_root=root)
        assert archived_hub is not None
        assert archived_hub.folder_id == ARCHIVE_FOLDER_ID
        archived_member = load_conversation(member.id, project_root=root)
        assert archived_member is not None
        assert archived_member.folder_id == ARCHIVE_FOLDER_ID
        # Still the group's ducky in Archive, so it shows and comes back with the group.
        assert archived_member.parent_conv_id == hub.id
        assert all(f.id != folder.id for f in load_folders(root))


def test_delete_group_and_duckies_archives_nested_groups():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        settings = PanelSettings.load()
        outer, outer_hub = _make_group(settings, "Outer", "", root)
        _inner, inner_hub = _make_group(settings, "Inner", outer.id, root)

        assert delete_folder(outer.id, root, archive_members=True) == sorted(
            [outer_hub.id, inner_hub.id]
        )

        assert all(f.id not in {outer.id, _inner.id} for f in load_folders(root))
        for hub_id in (outer_hub.id, inner_hub.id):
            archived = load_conversation(hub_id, project_root=root)
            assert archived is not None
            assert archived.folder_id == ARCHIVE_FOLDER_ID
        # The nested group points at the group around it.
        assert load_conversation(inner_hub.id, project_root=root).parent_conv_id == outer_hub.id
        assert load_conversation(outer_hub.id, project_root=root).parent_conv_id == ""
        assert ensure_group_folder_hubs(root) == 0


def test_delete_plain_folder_still_moves_chats_to_root():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        settings = PanelSettings.load()
        folder = create_folder("Notes", "", root)
        chat = create_conversation(settings, folder.id, title="Scratch", project_root=root)

        assert delete_folder(folder.id, root) == []

        moved = load_conversation(chat.id, project_root=root)
        assert moved is not None
        assert moved.folder_id == ""


def test_archive_plain_folder_takes_everything_inside():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        settings = PanelSettings.load()
        folder = create_folder("Engine", "", root)
        chat = create_conversation(settings, folder.id, title="Notes", project_root=root)
        group, hub = _make_group(settings, "Shaders", folder.id, root)

        assert delete_folder(folder.id, root, archive_members=True) == [hub.id]

        assert all(f.id not in {folder.id, group.id} for f in load_folders(root))
        for conv_id in (chat.id, hub.id):
            assert load_conversation(conv_id, project_root=root).folder_id == ARCHIVE_FOLDER_ID
        # Each group comes back on its own; a loose chat is no group's member.
        assert load_conversation(hub.id, project_root=root).parent_conv_id == ""
        assert load_conversation(chat.id, project_root=root).parent_conv_id == ""


def test_archiving_a_group_drops_the_plain_folders_it_left_empty():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        settings = PanelSettings.load()
        keep = create_folder("Keep", "", root)
        create_conversation(settings, keep.id, title="Stays", project_root=root)
        wrapper = create_folder("Wrapper", keep.id, root)
        _group, hub = _make_group(settings, "Team", wrapper.id, root)

        move_conversation(hub.id, ARCHIVE_FOLDER_ID, root)

        ids = {f.id for f in load_folders(root)}
        assert wrapper.id not in ids  # held only the group
        assert keep.id in ids  # still has a chat
        assert load_conversation(hub.id, project_root=root).folder_id == ARCHIVE_FOLDER_ID


def _archived_squad(settings, root: str):
    outer, outer_hub = _make_group(settings, "Squad", "", root)
    member = create_conversation(settings, outer.id, title="Builder", parent_conv_id=outer_hub.id, project_root=root)
    loose = create_conversation(settings, outer.id, title="Loose", project_root=root)
    inner, inner_hub = _make_group(settings, "Inner", outer.id, root)
    inner_member = create_conversation(settings, inner.id, title="Verifier", parent_conv_id=inner_hub.id, project_root=root)
    delete_folder(outer.id, root, archive_members=True)
    return outer_hub, member, loose, inner_hub, inner_member


def test_a_ducky_in_the_group_folder_belongs_to_the_group_in_archive():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        outer_hub, _member, loose, _inner_hub, _inner_member = _archived_squad(PanelSettings.load(), root)
        assert load_conversation(loose.id, project_root=root).parent_conv_id == outer_hub.id


def test_return_to_active_brings_the_whole_group_back():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        outer_hub, member, loose, inner_hub, inner_member = _archived_squad(PanelSettings.load(), root)

        move_conversation(outer_hub.id, "", root)

        folders = {f.id: f for f in load_folders(root)}
        outer_folder = next(f for f in folders.values() if f.group_hub_id == outer_hub.id)
        inner_folder = next(f for f in folders.values() if f.group_hub_id == inner_hub.id)
        assert outer_folder.parent_id == "" and inner_folder.parent_id == outer_folder.id
        for conv_id, folder_id, parent in (
            (outer_hub.id, outer_folder.id, ""),
            (member.id, outer_folder.id, outer_hub.id),
            (loose.id, outer_folder.id, outer_hub.id),
            (inner_hub.id, inner_folder.id, ""),
            (inner_member.id, inner_folder.id, inner_hub.id),
        ):
            conv = load_conversation(conv_id, project_root=root)
            assert (conv.folder_id, conv.parent_conv_id) == (folder_id, parent)
        assert ensure_group_folder_hubs(root) == 0


def test_one_ducky_out_of_an_archived_group_comes_back_on_its_own():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        outer_hub, member, _loose, _inner_hub, _inner_member = _archived_squad(PanelSettings.load(), root)

        move_conversation(member.id, "", root)

        back = load_conversation(member.id, project_root=root)
        assert (back.folder_id, back.parent_conv_id) == ("", "")
        assert load_conversation(outer_hub.id, project_root=root).folder_id == ARCHIVE_FOLDER_ID


def test_deleting_an_archived_group_for_good_takes_everything_in_it():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        outer_hub, member, loose, inner_hub, inner_member = _archived_squad(PanelSettings.load(), root)

        delete_conversation(outer_hub.id, root)

        for conv_id in (outer_hub.id, member.id, loose.id, inner_hub.id, inner_member.id):
            assert load_conversation(conv_id, project_root=root) is None


def test_archiving_a_group_chat_archives_its_folder_and_everything_in_it():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        settings = PanelSettings.load()
        folder, hub = _make_group(settings, "Squad", "", root)
        member = create_conversation(settings, folder.id, title="Builder", parent_conv_id=hub.id, project_root=root)

        move_conversation(hub.id, ARCHIVE_FOLDER_ID, root)

        assert all(f.id != folder.id for f in load_folders(root))
        for conv_id in (hub.id, member.id):
            assert load_conversation(conv_id, project_root=root).folder_id == ARCHIVE_FOLDER_ID


def test_a_lead_stays_on_top_of_its_own_group_when_it_leads_a_subgroup():
    from types import SimpleNamespace as NS

    from frontend.ui_web.group_orchestrator import stays_with_outer_group

    folders = [
        NS(id="main", parent_id="", group_hub_id="hub-main"),
        NS(id="qa", parent_id="main", group_hub_id="hub-qa"),
        NS(id="plain", parent_id="", group_hub_id=""),
    ]
    lead = NS(id="lead", folder_id="main")
    by_id = {"hub-main": NS(id="hub-main", leader_conv_id="lead"), "hub-qa": NS(id="hub-qa", leader_conv_id="lead")}
    assert stays_with_outer_group(lead, "qa", folders, by_id)
    # Not the outer group's lead, filed in a plain folder, or the subgroup is not nested: it moves.
    assert not stays_with_outer_group(NS(id="other", folder_id="main"), "qa", folders, by_id)
    assert not stays_with_outer_group(NS(id="lead", folder_id="plain"), "qa", folders, by_id)
    assert not stays_with_outer_group(NS(id="lead", folder_id="qa"), "main", folders, by_id)


def test_a_group_made_inside_another_sits_in_it_and_is_deleted_with_it(monkeypatch, tmp_path):
    # Oct 15 2026: a lead made its team's groups with ducky_group_create(parent_folder_id=...);
    # the nested hubs had no parent_conv_id, so deleting the team kept them and their members.
    import frontend.ui_web.panel_api as pa
    from frontend.ui_web.panel_api_chats import PanelApiChatsMixin

    settings = PanelSettings.load()
    settings.uefn_project_root = str(tmp_path)
    monkeypatch.setattr(pa.PanelSettings, "load", classmethod(lambda cls: settings))
    monkeypatch.setattr(pa, "notify_chats_changed", lambda *a, **k: None)
    api = PanelApiChatsMixin()
    outer = api.group_create("Ledger Team", "", False)
    inner = api.group_create("Core", outer["folder_id"], False)
    member = create_conversation(settings, inner["folder_id"], title="Builder", parent_conv_id=inner["id"],
                                 project_root=str(tmp_path))
    assert load_conversation(inner["id"], project_root=str(tmp_path)).parent_conv_id == outer["id"]
    assert load_conversation(outer["id"], project_root=str(tmp_path)).parent_conv_id == ""

    api.delete_conversation(outer["id"])
    for cid in (outer["id"], inner["id"], member.id):
        assert load_conversation(cid, project_root=str(tmp_path)) is None, cid


def test_restart_from_here_erases_the_message_and_everything_after(tmp_path, monkeypatch):
    import frontend.ui_web.panel_api as pa
    from backend.agent.coding_agents import session_refresh
    from frontend.ui_web.panel_api_chats import PanelApiChatsMixin

    root = str(tmp_path)
    settings = PanelSettings.load()
    settings.uefn_project_root = root
    monkeypatch.setattr(pa.PanelSettings, "load", classmethod(lambda cls: settings))
    docs: dict = {}
    monkeypatch.setattr("backend.store.repos.kv.set_doc", lambda t, k, v: docs.__setitem__(k, v))
    monkeypatch.setattr("backend.store.repos.kv.get_doc", lambda t, k: docs.get(k))
    monkeypatch.setattr("backend.store.repos.kv.delete_doc", lambda t, k: docs.pop(k, None))
    conv = create_conversation(settings, "", title="Chat", project_root=root)
    conv.messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "one"},
        {"role": "user", "content": "second", "attachments": [{"kind": "file", "name": "a.txt"}]},
        {"role": "assistant", "content": "two"},
    ]
    conv.upstream_session_id = "claude_code:abc"
    save_conversation(conv, root)
    api = PanelApiChatsMixin()

    assert api.rewind_to_message(conv.id, 2000, "changed meanwhile")["ok"] is False
    assert api.rewind_to_message(conv.id, 1000, "one")["ok"] is False  # not a user message
    assert api.rewind_to_message(conv.id, 2000, "second") == {"ok": True, "kept": 2}

    fresh = load_conversation(conv.id, project_root=root)
    assert [m["content"] for m in fresh.messages] == ["first", "one"]
    assert fresh.upstream_session_id == ""  # the agent's thread still held the erased turn
    assert session_refresh.take_rewound(conv.id) is True  # next turn replays the kept messages
    assert session_refresh.take_rewound(conv.id) is False


def test_group_create_never_saves_a_plain_folder_even_when_made_fast(tmp_path, monkeypatch):
    # Oct 25 2026: the folder was saved plain and linked two saves later, so a refresh in
    # between (or a second create replacing the folder list) left a stray "Group6" folder.
    import threading

    import frontend.ui_web.panel_api as pa
    from frontend.ui_web.panel_api_chats import PanelApiChatsMixin

    root = str(tmp_path)
    settings = PanelSettings.load()
    settings.uefn_project_root = root
    monkeypatch.setattr(pa.PanelSettings, "load", classmethod(lambda cls: settings))
    monkeypatch.setattr(pa, "notify_chats_changed", lambda *a, **k: None)
    plain_saves: list[str] = []
    real_save = pa.save_conversation

    def save_and_check(conv, project_root=None):
        plain_saves.extend(f.name for f in load_folders(root) if f.name.startswith("G") and not f.group_hub_id)
        real_save(conv, project_root)

    monkeypatch.setattr(pa, "save_conversation", save_and_check)
    api = PanelApiChatsMixin()
    made: list[dict] = []
    threads = [threading.Thread(target=lambda i=i: made.append(api.group_create(f"G{i}", "", False)))
               for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert plain_saves == []
    groups = [f for f in load_folders(root) if f.name.startswith("G")]
    assert sorted(f.name for f in groups) == [f"G{i}" for i in range(8)]
    assert {f.group_hub_id for f in groups} == {m["id"] for m in made}


def test_a_lead_added_to_a_new_group_stays_in_its_old_group(monkeypatch, tmp_path):
    # Oct 22 2026: as_leader moved the lead into the new group's folder, leaving its old group empty.
    # A chat that leads the group it sits in keeps its place (a plain member would move).
    import frontend.ui_web.panel_api as pa
    from frontend.ui_web.panel_api_chats import PanelApiChatsMixin

    root = str(tmp_path)
    settings = PanelSettings.load()
    settings.uefn_project_root = root
    monkeypatch.setattr(pa.PanelSettings, "load", classmethod(lambda cls: settings))
    monkeypatch.setattr(pa, "notify_chats_changed", lambda *a, **k: None)
    api = PanelApiChatsMixin()
    old = api.group_create("Old Team", "", False)
    new = api.group_create("New Team", "", False)
    lead = create_conversation(settings, old["folder_id"], title="Lead", parent_conv_id=old["id"], project_root=root)
    api.group_add_member(old["id"], lead.id, as_leader=True)

    out = api.group_add_member(new["id"], lead.id, as_leader=True)

    assert out["ok"] and out["leader_conv_id"] == lead.id
    assert lead.id in [m["member_conv_id"] for m in out["group_members"]]
    fresh = load_conversation(lead.id, project_root=root)
    assert fresh.folder_id == old["folder_id"] and fresh.parent_conv_id == old["id"]


def _two_teams(monkeypatch, tmp_path):
    import frontend.ui_web.panel_api as pa
    from frontend.ui_web.panel_api_chats import PanelApiChatsMixin

    root = str(tmp_path)
    settings = PanelSettings.load()
    settings.uefn_project_root = root
    monkeypatch.setattr(pa.PanelSettings, "load", classmethod(lambda cls: settings))
    monkeypatch.setattr(pa, "notify_chats_changed", lambda *a, **k: None)
    api = PanelApiChatsMixin()
    old = api.group_create("Old Team", "", False)
    new = api.group_create("New Team", "", False)
    return api, settings, root, old, new


def _lane(out, cid):
    return next(m for m in out["group_members"] if m["member_conv_id"] == cid).get("write_allowed")


def test_a_member_moved_to_another_group_keeps_its_lane(monkeypatch, tmp_path):
    # Oct 23 2026: the move rebuilt the roster row from the new group and the bar said "No lane".
    api, settings, root, old, new = _two_teams(monkeypatch, tmp_path)
    member = create_conversation(settings, old["folder_id"], title="Coder", parent_conv_id=old["id"], project_root=root)
    assert api.group_add_member(old["id"], member.id)["ok"]
    assert api.group_set_member_lane(old["id"], member.id, ["ducky_app/backend/workspace"], set_by="lead")["ok"]

    out = api.group_add_member(new["id"], member.id)

    assert out["ok"] and "lane_warning" not in out
    assert _lane(out, member.id) == ["ducky_app/backend/workspace/**"]
    assert api.group_get_lanes(new["id"])["lanes"][member.id]["write_allowed"] == ["ducky_app/backend/workspace/**"]


def test_an_outer_lead_sets_lanes_in_its_nested_groups_but_a_sibling_lead_cannot(monkeypatch, tmp_path):
    # Oct 23 2026: the outer lead made the workstream groups, then ducky_group_set_lane refused it.
    import pytest

    from backend.tools.panel import ducky_panel
    from backend.workspace import identity
    from backend.workspace.identity import RunContext

    api, settings, root, outer, _new = _two_teams(monkeypatch, tmp_path)
    inner = api.group_create("Surface", outer["folder_id"], False)
    sibling = api.group_create("Search", outer["folder_id"], False)
    lead = create_conversation(settings, outer["folder_id"], title="Lead", parent_conv_id=outer["id"], project_root=root)
    worker = create_conversation(settings, inner["folder_id"], title="Worker", parent_conv_id=inner["id"], project_root=root)
    other = create_conversation(settings, sibling["folder_id"], title="Other", parent_conv_id=sibling["id"], project_root=root)
    api.group_add_member(outer["id"], lead.id, as_leader=True)
    api.group_add_member(inner["id"], worker.id, as_leader=True)
    api.group_add_member(sibling["id"], other.id, as_leader=True)
    monkeypatch.setattr(ducky_panel, "_panel_api", lambda: api)

    assert api.group_lane_authorities(inner["id"]) == [worker.id, lead.id]
    for caller, allowed in ((lead.id, True), (worker.id, True), (other.id, False)):
        token = identity.bind(RunContext(run_id="r", conv_id=caller))
        try:
            if allowed:
                assert ducky_panel._require_lane_authority(inner["id"]) == caller
            else:
                with pytest.raises(ValueError, match="Only the group leader"):
                    ducky_panel._require_lane_authority(inner["id"])
        finally:
            identity.reset(token)


def test_a_workstream_lead_is_filed_in_the_group_it_leads_and_the_root_lead_stays(monkeypatch, tmp_path):
    api, settings, root, outer, _new = _two_teams(monkeypatch, tmp_path)
    verse = api.group_create("Verse", outer["folder_id"], False)
    boss = create_conversation(settings, outer["folder_id"], title="Lead", parent_conv_id=outer["id"], project_root=root)
    vlead = create_conversation(settings, outer["folder_id"], title="Verse lead", parent_conv_id=outer["id"], project_root=root)
    assert api.group_add_member(outer["id"], boss.id, as_leader=True)["ok"]
    assert api.group_add_member(outer["id"], vlead.id)["ok"]
    assert api.group_set_member_lane(outer["id"], vlead.id, ["Content/Verse"], set_by=boss.id)["ok"]

    out = api.group_add_member(verse["id"], vlead.id, as_leader=True)

    assert out["ok"] and out["leader_conv_id"] == vlead.id and out["folder_id"] == verse["folder_id"]
    fresh = load_conversation(vlead.id, project_root=root)
    assert fresh.folder_id == verse["folder_id"] and fresh.parent_conv_id == verse["id"]
    assert _lane(out, vlead.id) == ["Content/Verse/**"]
    # The root lead joins the nested roster but stays on top of its own group.
    joined = api.group_add_member(verse["id"], boss.id)
    assert joined["ok"] and joined["folder_id"] == outer["folder_id"]
    fresh = load_conversation(boss.id, project_root=root)
    assert fresh.folder_id == outer["folder_id"] and fresh.parent_conv_id == outer["id"]
    # move_folder=False keeps the old pointer-only leader.
    other = api.group_create("Art", outer["folder_id"], False)
    kept = api.group_add_member(other["id"], vlead.id, as_leader=True, move_folder=False)
    assert kept["ok"] and kept["folder_id"] == verse["folder_id"]


def test_a_seat_gets_the_model_it_was_invited_with(monkeypatch, tmp_path):
    import frontend.agent_profiles as profiles
    import frontend.ui_web.panel_api as pa
    from frontend.favorite_models import FavoriteSelection, ResolveOk

    api, settings, root, team, _new = _two_teams(monkeypatch, tmp_path)
    monkeypatch.setattr(profiles, "get_agent_profile", lambda pid: {"id": "tester", "name": "Tester"})
    asked = []

    def resolve(favorites, _settings):
        asked.append(favorites)
        backend, model_id = favorites[0].split(":", 1)
        return ResolveOk(coding_agent=backend, model=model_id, provider="",
                         selection=FavoriteSelection(backend=backend, model_id=model_id))

    monkeypatch.setattr(pa, "resolve_model_selection", resolve)
    monkeypatch.setattr("frontend.favorite_models.refuse_paid_api_for_agent", lambda result, _s: result)
    out = api.group_invite(team["id"], "tester", model="codex:gpt-5-mini", name="Tester", for_agent=True)
    assert out["ok"], out
    assert out["member"]["model"] == "codex:gpt-5-mini"
    chat = load_conversation(out["member"]["member_conv_id"], project_root=root)
    assert chat.model == "gpt-5-mini"
    assert asked == [["codex:gpt-5-mini"]]


def test_mute_is_stored_on_chats_and_groups_and_never_stops_an_agent(monkeypatch, tmp_path):
    import frontend.ui_web.panel_api as pa
    from frontend.ui_web import agent_modes

    api, settings, root, team, _new = _two_teams(monkeypatch, tmp_path)
    member = create_conversation(settings, team["folder_id"], title="Modeler", parent_conv_id=team["id"], project_root=root)
    api.group_add_member(team["id"], member.id)
    monkeypatch.setattr(agent_modes, "is_agent_running", lambda cid: True)
    monkeypatch.setattr(pa, "cancel_agent", lambda *a: (_ for _ in ()).throw(AssertionError("stopped")))

    for cid in (member.id, team["id"]):
        assert api.set_muted(cid, True)["muted"] is True
        fresh = load_conversation(cid, project_root=root)
        assert fresh.muted is True
        assert api._conversation_sidebar_row(fresh)["muted"] is True
    assert api.set_muted(member.id, False)["muted"] is False
    assert load_conversation(member.id, project_root=root).muted is False
    assert api.set_muted("missing", True)["ok"] is False

    # A running turn holds a copy loaded before the mute; saving it keeps the mute.
    stale = load_conversation(member.id, project_root=root)
    assert stale.muted is False
    api.set_muted(member.id, True)
    stale.title = "Modeler (turn done)"
    save_conversation(stale, root)
    assert load_conversation(member.id, project_root=root).muted is True


def test_stopping_a_group_stops_its_nested_groups_too(monkeypatch, tmp_path):
    import frontend.ui_web.panel_api as pa
    from frontend.ui_web import agent_modes

    api, settings, root, outer, _new = _two_teams(monkeypatch, tmp_path)
    inner = api.group_create("Modeling", outer["folder_id"], False)
    seats = {}
    for name, gid, fid in (("A", outer["id"], outer["folder_id"]), ("B", outer["id"], outer["folder_id"]),
                           ("Idle", outer["id"], outer["folder_id"]), ("C", inner["id"], inner["folder_id"])):
        seats[name] = create_conversation(settings, fid, title=name, parent_conv_id=gid, project_root=root).id
        api.group_add_member(gid, seats[name])
    running = {seats["A"], seats["B"], seats["C"]}
    stopped = []
    monkeypatch.setattr(agent_modes, "is_agent_running", lambda cid: cid in running)
    monkeypatch.setattr(pa, "cancel_agent", lambda cid=None: stopped.append(cid))

    lead = load_conversation(outer["id"], project_root=root).leader_conv_id
    assert lead in running  # the outer group's lead is one of the running seats

    out = api.group_stop(outer["id"])

    # Stop team stops every member of the group and its nested groups, never the team's lead.
    assert out["ok"] and out["stopped"] == 2 and out["groups"] == 2 and out["lead_kept"] == lead
    assert sorted(stopped) == sorted(running - {lead})
    for cid in seats.values():
        assert load_conversation(cid, project_root=root) is not None
    assert api.group_stop(seats["A"])["ok"] is False


def test_a_moved_lane_that_overlaps_the_new_team_is_reported_not_forced(monkeypatch, tmp_path):
    api, settings, root, old, new = _two_teams(monkeypatch, tmp_path)
    mover = create_conversation(settings, old["folder_id"], title="Mover", parent_conv_id=old["id"], project_root=root)
    owner = create_conversation(settings, new["folder_id"], title="Owner", parent_conv_id=new["id"], project_root=root)
    api.group_add_member(old["id"], mover.id)
    api.group_add_member(new["id"], owner.id)
    assert api.group_set_member_lane(old["id"], mover.id, ["ducky_app/frontend"])["ok"]
    assert api.group_set_member_lane(new["id"], owner.id, ["ducky_app/frontend"])["ok"]

    out = api.group_add_member(new["id"], mover.id)

    assert out["ok"] and out["lane_warning"].startswith("Lane not kept: Lane overlaps")
    assert _lane(out, mover.id) is None and _lane(out, owner.id) == ["ducky_app/frontend/**"]
