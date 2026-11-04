"""Chat storage basics: project slugs, the one list, per-chat locks and project roots."""

from __future__ import annotations

from frontend.ui_web.chats import store


def test_a_slug_is_stable_safe_and_case_blind(tmp_path) -> None:
    root = tmp_path / "My Island!"
    slug = store.project_slug(str(root))
    assert slug == store.project_slug(str(root))
    assert slug.rsplit("_", 1)[1] == store.project_slug(str(root).upper()).rsplit("_", 1)[1]
    assert slug.startswith("My_Island__") and len(slug.rsplit("_", 1)[1]) == 16
    assert store.project_slug("") == "_no_project"
    assert store.project_display_name(str(root)) == "My Island!"
    assert store.project_display_name("") == "No project"
    assert store.project_slug_display_name("_no_project") == "No project"


def test_every_project_is_stored_in_the_one_list() -> None:
    assert store._project_id("C:/Some/Island") == store.ONE_LIST_SLUG == "_no_project"
    assert store._project_id(None) == store.ONE_LIST_SLUG


def test_a_chat_lock_is_one_lock_per_chat_until_released() -> None:
    first = store._conversation_lock("chat-a")
    assert store._conversation_lock("chat-a") is first
    assert store._conversation_lock("chat-b") is not first
    store.release_conversation_lock("chat-a")
    assert store._conversation_lock("chat-a") is not first
    store.release_conversation_lock("chat-a")
    store.release_conversation_lock("chat-b")


def test_create_project_root_keeps_the_open_island_or_names_none(tmp_path) -> None:
    current = str(tmp_path)
    assert store.create_project_root("", current) is None
    assert store.create_project_root(store.project_slug(current), current) is None
    assert store.create_project_root("_no_project", current) == ""
    assert store.project_root_for_slug("") is None


def test_a_conversation_lives_under_its_slug(monkeypatch, tmp_path) -> None:
    root = tmp_path / "projects"
    monkeypatch.setattr(store, "_chats_root", lambda: root)
    assert store._iter_disk_project_slugs() == []
    assert store._conversation_disk_dir("slug", "c1") == root / "slug" / "conversations" / "c1"
    (root / "b").mkdir(parents=True)
    (root / "a").mkdir()
    assert store._iter_disk_project_slugs() == ["a", "b"]
