"""Per-project memory entries (index + pull, write-own/read-any) + cross-project ducky context."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def isolated_appdata(tmp_path, monkeypatch):
    """Point app-data storage at a temp dir so tests never touch real AppData."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("UEFN_DUCKY_PROJECT_ROOT", raising=False)
    return tmp_path


@pytest.fixture(autouse=True)
def _ungate_plugin_tools(monkeypatch):
    """Memory tools are gated on the 'uefn' Store plugin; bypass the gate so these
    unit tests don't depend on the machine's installed/enabled plugin state."""
    monkeypatch.setattr("backend.tools.plugin_gate.require_plugin", lambda pid: None)


@pytest.fixture
def two_projects(isolated_appdata, tmp_path, monkeypatch):
    """Two fake projects; ProjA is active (env + settings + roster)."""
    from frontend.settings import PanelSettings
    from frontend.ui_web import project_switch
    from frontend.ui_web.project_chats import project_display_name, project_slug

    proj_a = str(tmp_path / "ProjA")
    proj_b = str(tmp_path / "ProjB")
    (tmp_path / "ProjA").mkdir()
    (tmp_path / "ProjB").mkdir()
    monkeypatch.setenv("UEFN_DUCKY_PROJECT_ROOT", proj_a)
    settings = PanelSettings.load()
    settings.uefn_project_root = proj_a
    settings.save()
    monkeypatch.setattr(
        project_switch,
        "list_panel_projects",
        lambda: [
            {
                "path": r,
                "name": project_display_name(r),
                "slug": project_slug(r),
                "active": r == proj_a,
            }
            for r in (proj_a, proj_b)
        ],
    )
    return proj_a, proj_b


def test_empty_index(isolated_appdata, tmp_path):
    from backend.project_memory import index_markdown, list_entries

    root = str(tmp_path / "Empty")
    assert list_entries(root) == []
    assert index_markdown(root) == ""


def test_save_and_get_entry_in_appdata_project_slug_dir(isolated_appdata, tmp_path):
    from frontend.settings import default_app_data_dir
    from frontend.ui_web.project_chats import project_slug
    from backend.project_memory import list_entries, read_entry, save_entry

    root = str(tmp_path / "Proj")
    (tmp_path / "Proj").mkdir()
    save_entry(
        "Verse Device Naming",
        "Use kebab-case labels for all devices.",
        description="Device label convention",
        author="VerseDucky",
        project_root=root,
    )
    entry = read_entry("verse-device-naming", root)
    assert entry is not None
    assert entry["content"].strip() == "Use kebab-case labels for all devices."
    assert entry["author"] == "VerseDucky"

    # Entries live in APP DATA keyed by project slug — never inside the project folder.
    expected = (
        default_app_data_dir() / "memory" / "projects" / project_slug(root) / "verse-device-naming.md"
    )
    assert expected.is_file()
    assert not (tmp_path / "Proj" / ".uefn-ducky").exists()

    index = list_entries(root)
    assert [e["name"] for e in index] == ["verse-device-naming"]
    assert index[0]["description"] == "Device label convention"


def test_nested_sub_entries_split_like_skills(isolated_appdata, tmp_path):
    """Saving entry/sub converts a flat entry into a dir with MEMORY.md + sub files."""
    from frontend.settings import default_app_data_dir
    from frontend.ui_web.project_chats import project_slug
    from backend.project_memory import list_entries, read_entry, save_entry

    root = str(tmp_path / "Proj")
    save_entry("coding-standards", "General rules.", description="Project coding rules", project_root=root)
    save_entry(
        "coding-standards/error-handling",
        "Always check STOP errors.",
        description="How we handle Verse errors",
        author="VerseDucky",
        project_root=root,
    )

    base = default_app_data_dir() / "memory" / "projects" / project_slug(root) / "coding-standards"
    assert (base / "MEMORY.md").is_file()
    assert (base / "error-handling.md").is_file()
    assert not (base.parent / "coding-standards.md").exists()  # flat file was split

    # Parent read returns its body plus the sub index; sub read returns the sub body.
    parent = read_entry("coding-standards", root)
    assert parent["content"].strip() == "General rules."
    assert parent["subs"] == [
        {"name": "coding-standards/error-handling", "description": "How we handle Verse errors"}
    ]
    sub = read_entry("coding-standards/error-handling", root)
    assert sub["content"].strip() == "Always check STOP errors."

    # Index lists the parent with nested subs.
    entries = list_entries(root)
    assert entries[0]["name"] == "coding-standards"
    assert entries[0]["subs"][0]["name"] == "coding-standards/error-handling"


def test_sub_entry_without_parent_creates_stub_main(isolated_appdata, tmp_path):
    from backend.project_memory import read_entry, save_entry

    root = str(tmp_path / "Proj")
    save_entry("verse-api/timers", "Use loop + Sleep.", description="Timer patterns", project_root=root)
    parent = read_entry("verse-api", root)
    assert parent is not None
    assert "sub-entries" in parent["description"]
    assert parent["subs"][0]["name"] == "verse-api/timers"


def test_nested_index_markdown_indents_subs(isolated_appdata, tmp_path):
    from backend.project_memory import index_markdown, save_entry

    root = str(tmp_path / "Proj")
    save_entry("standards", "Main body", description="Rules", project_root=root)
    save_entry("standards/naming", "kebab-case", description="Naming rules", project_root=root)
    index = index_markdown(root)
    assert "- standards — Rules" in index
    assert "\n  - standards/naming — Naming rules" in index
    assert "kebab-case" not in index


def test_delete_nested(isolated_appdata, tmp_path):
    from backend.project_memory import delete_entry, list_entries, read_entry, save_entry

    root = str(tmp_path / "Proj")
    save_entry("topic/a", "aaa", project_root=root)
    save_entry("topic/b", "bbb", project_root=root)
    assert delete_entry("topic/a", root) is True
    assert read_entry("topic/a", root) is None
    assert read_entry("topic/b", root) is not None
    assert delete_entry("topic", root) is True  # removes main + remaining subs
    assert list_entries(root) == []


def test_deep_nesting_rejected(isolated_appdata):
    from backend.project_memory import slugify_entry_name

    with pytest.raises(ValueError, match="one nesting level"):
        slugify_entry_name("a/b/c")


def test_same_named_projects_stay_separated(isolated_appdata, tmp_path):
    """Two projects with the same folder name get distinct slug dirs (path hash)."""
    from backend.project_memory import list_entries, save_entry

    root_1 = str(tmp_path / "TeamA" / "Game")
    root_2 = str(tmp_path / "TeamB" / "Game")
    save_entry("fact", "from team A", project_root=root_1)
    save_entry("fact", "from team B", project_root=root_2)

    from backend.project_memory import read_entry

    assert "team A" in read_entry("fact", root_1)["content"]
    assert "team B" in read_entry("fact", root_2)["content"]
    assert len(list_entries(root_1)) == 1
    assert len(list_entries(root_2)) == 1


def test_slugify(isolated_appdata):
    from backend.project_memory import slugify_entry_name

    assert slugify_entry_name("Verse API quirks") == "verse-api-quirks"
    with pytest.raises(ValueError):
        slugify_entry_name("!!!")


def test_append_creates_then_extends(isolated_appdata, tmp_path):
    from backend.project_memory import append_entry, read_entry

    root = str(tmp_path / "Proj")
    append_entry("lessons", "Never batch editor calls.", author="Scout", project_root=root)
    append_entry("lessons", "Save the level once at the end.", author="Scout", project_root=root)
    entry = read_entry("lessons", root)
    assert "Never batch editor calls." in entry["content"]
    assert "Save the level once at the end." in entry["content"]
    assert "— Scout" in entry["content"]  # second block is attributed


def test_delete_entry(isolated_appdata, tmp_path):
    from backend.project_memory import delete_entry, list_entries, save_entry

    root = str(tmp_path / "Proj")
    save_entry("obsolete", "old fact", project_root=root)
    assert delete_entry("obsolete", root) is True
    assert delete_entry("obsolete", root) is False
    assert list_entries(root) == []


def test_index_markdown_lines_and_overflow(isolated_appdata, tmp_path):
    from backend.project_memory import index_markdown, save_entry

    root = str(tmp_path / "Proj")
    for i in range(5):
        save_entry(f"fact-{i}", f"body {i}", description=f"desc {i}", author="D", project_root=root)
    index = index_markdown(root)
    assert "- fact-0 — desc 0 (D)" in index
    assert "body 0" not in index  # bodies never leak into the index

    tiny = index_markdown(root, max_chars=40)
    assert "more — call project_memory_list" in tiny


def test_prompt_carries_index_not_bodies(isolated_appdata, tmp_path):
    from backend.project_memory import save_entry
    from backend.agent.prompt import get_system_prompt_parts

    root = str(tmp_path / "Proj")
    save_entry(
        "vault",
        "The gold is stored in the third basement vault behind the waterfall.",
        description="Where this project stores gold",
        author="Scout",
        project_root=root,
    )
    parts = get_system_prompt_parts(listener_online=False, listener_port=4200, project_root=root)
    assert "vault — Where this project stores gold" in parts["memory"]
    assert "project_memory_get" in parts["memory"]
    assert "third basement" not in parts["memory"]  # body stays out of the prompt


def test_prompt_shows_capture_and_cross_project_guidance_even_when_empty(isolated_appdata, tmp_path):
    """Empty project still gets the capture + cross-project directives (write gaps closed)."""
    from backend.agent.prompt import get_system_prompt_parts

    root = str(tmp_path / "FreshProj")
    parts = get_system_prompt_parts(listener_online=False, listener_port=4200, project_root=root)
    mem = parts["memory"]
    assert "Capture as you work" in mem  # proactive-write directive present
    assert "project_memory_save" in mem
    assert "ducky_memory_overview" in mem  # cross-project survey advertised
    assert "no shared" in mem.lower() or "memory yet" in mem.lower()


def test_capture_and_overview_tools_are_deferred(isolated_appdata):
    """Memory capture tools are deferred via ducky_call_tool (Cursor-style floor)."""
    from backend.agent.toolsets.categories.memory import CORE_TOOLS, EXTENDED_TOOLS

    assert not CORE_TOOLS
    assert {"project_memory_save", "project_memory_append", "ducky_memory_overview"} <= EXTENDED_TOOLS
    assert "project_memory_delete" in EXTENDED_TOOLS


def test_writes_own_project_reads_any(two_projects):
    """A ducky writes ONLY its own project's memory but can read another's."""
    from backend.project_memory import list_entries
    from backend.tools.memory import project_memory_get, project_memory_list, project_memory_save

    proj_a, proj_b = two_projects

    # Seed ProjB's memory as if its own ducky wrote it.
    from backend.project_memory import save_entry

    save_entry("projb-fact", "B uses team index 7", description="ProjB teams", project_root=proj_b)

    # Active-project write lands in ProjA — never in ProjB.
    json.loads(project_memory_save("proja-fact", "A uses grid snap 100", description="ProjA grid"))
    assert [e["name"] for e in list_entries(proj_a)] == ["proja-fact"]
    assert [e["name"] for e in list_entries(proj_b)] == ["projb-fact"]

    # Cross-project READ works by project name.
    listed = json.loads(project_memory_list(project="ProjB"))
    assert listed["count"] == 1
    got = json.loads(project_memory_get("projb-fact", project="ProjB"))
    assert "team index 7" in got["content"]

    # Default read scope is the active project.
    with pytest.raises(ValueError, match="Known entries"):
        project_memory_get("projb-fact")


def test_tools_roundtrip_active_project(two_projects):
    from backend.tools.memory import (
        project_memory_append,
        project_memory_delete,
        project_memory_get,
        project_memory_list,
        project_memory_save,
    )

    out = json.loads(project_memory_save("team-fact", "gold in vault", description="Where gold is"))
    assert out["ok"] is True
    assert json.loads(project_memory_list())["count"] == 1
    json.loads(project_memory_append("team-fact", "moved to vault 2", author="Scout"))
    got = json.loads(project_memory_get("team-fact"))
    assert "moved to vault 2" in got["content"]
    assert json.loads(project_memory_delete("team-fact"))["ok"] is True
    with pytest.raises(ValueError):
        project_memory_delete("team-fact")


def test_memory_overview_spans_projects(two_projects):
    from frontend.ui_web.project_chats import create_conversation
    from backend.project_memory import save_entry
    from backend.tools.memory import ducky_memory_overview

    proj_a, proj_b = two_projects
    save_entry("a-fact", "alpha", description="A's fact", project_root=proj_a)
    save_entry("b-fact", "beta", description="B's fact", project_root=proj_b)
    conv = create_conversation(project_root=proj_b)

    out = json.loads(ducky_memory_overview())
    by_path = {p["path"]: p for p in out["projects"]}
    assert by_path[proj_a]["memory"]["index"][0]["name"] == "a-fact"
    assert by_path[proj_b]["memory"]["index"][0]["name"] == "b-fact"
    assert any(c["id"] == conv.id for c in by_path[proj_b]["chats"])


def test_read_chat_cross_project(two_projects):
    from frontend.ui_web.project_chats import append_message, create_conversation

    proj_a, proj_b = two_projects
    conv = create_conversation(project_root=proj_b)
    append_message(
        conv, {"role": "assistant", "content": "other-project knowledge", "ts": 1.0}, project_root=proj_b
    )

    from backend.tools.ducky_panel import ducky_get_chat_context, ducky_read_chat

    # Not visible without project= (chat lives in the other project).
    with pytest.raises(ValueError):
        ducky_read_chat(conv.id)

    out = json.loads(ducky_read_chat(conv.id, project="ProjB"))
    assert out["messages"][0]["content"] == "other-project knowledge"

    ctx = json.loads(ducky_get_chat_context(conv.id, project="ProjB"))
    assert ctx["scope"] == "cross_project_summary"
    assert ctx["message_count"] == 1
    assert ctx["last_messages"][0]["content"] == "other-project knowledge"


def test_resolve_project_arg_unknown_raises(isolated_appdata, monkeypatch):
    from frontend.ui_web import project_switch

    monkeypatch.setattr(project_switch, "list_panel_projects", lambda: [])

    from backend.tools.ducky_panel import _resolve_project_root_arg

    with pytest.raises(ValueError, match="Unknown project"):
        _resolve_project_root_arg("nope")
