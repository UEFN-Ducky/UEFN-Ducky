"""Opening another project's chat must not steal it onto the active island."""

from __future__ import annotations

import tempfile
from pathlib import Path

from frontend.settings import PanelSettings
from frontend.ui_web.project_chats import (
    _use_db,
    conversation_path,
    create_conversation,
    load_conversation,
    project_slug,
    save_conversation,
)


def test_load_and_save_keep_home_project():
    with tempfile.TemporaryDirectory() as tmp:
        home = str(Path(tmp) / "HomeIsland")
        other = str(Path(tmp) / "OtherIsland")
        Path(home).mkdir()
        Path(other).mkdir()
        conv = create_conversation(PanelSettings.load(), "", title="Away", project_root=home)
        conv.messages = [{"role": "user", "text": "hi", "content": "hi", "ts": 1}]
        save_conversation(conv, home)

        loaded = load_conversation(conv.id, project_root=other)
        assert loaded is not None
        assert loaded.title == "Away"
        loaded.title = "Still away"
        save_conversation(loaded, other)

        home_again = load_conversation(conv.id, project_root=home)
        assert home_again is not None
        assert home_again.title == "Still away"
        if _use_db():
            from frontend.ui_web.project_chats import _repo

            assert _repo().conv_project_id(conv.id) == project_slug(home)
        else:
            assert conversation_path(conv.id, home).is_file()
            assert not conversation_path(conv.id, other).is_file()
