"""A page of a long chat opens only the messages it shows, and reads the same rows."""

from __future__ import annotations

import time
from pathlib import Path

import frontend.ui_web.panel_api  # noqa: F401 - mixins import the package root first
from backend.store.repos import chats as repo
from frontend.settings import PanelSettings
from frontend.ui_web import project_chats as pc
from frontend.ui_web.panel_api_chats import PanelApiChatsMixin


def _transcript(n: int) -> list[dict]:
    out: list[dict] = []
    for i in range(n):
        if i % 2 == 0:
            m = {"role": "user", "content": f"step {i}", "text": f"step {i}", "ts": i}
            if i % 20 == 0:  # 30 attachments
                m["attachments"] = [{"name": f"shot{i}.png", "path": f"C:/x/shot{i}.png", "mime": "image/png"}]
            out.append(m)
            continue
        blocks = []
        for j in range(4):
            blocks += [{"type": "text", "text": "Working on it."},
                       {"type": "tool_call", "id": f"t{i}_{j}", "name": "workspace_read_file", "arguments": {"path": "a"},
                        "status": "success", "duration_ms": 1, "result": {"data": "x" * 3000}}]
        out.append({"role": "assistant", "content": "Done.", "blocks": blocks, "run_id": f"r{i}", "ts": i})
    return out


def test_first_page_of_a_600_message_chat_opens_only_its_tail(tmp_path: Path, monkeypatch) -> None:
    project = str(tmp_path / "Island")
    Path(project).mkdir()
    s = PanelSettings.load()
    s.uefn_project_root = project
    s.save()
    conv = pc.create_conversation(title="Big", project_root=project, skill_snapshot="x")
    conv = pc.load_conversation(conv.id, project)
    conv.messages = _transcript(600)
    pc.save_conversation(conv, project, wait=True)
    api = PanelApiChatsMixin()
    whole = api.load_messages(conv.id)

    opened = {"n": 0}
    real = repo._message

    def counting(*a, **kw):
        opened["n"] += 1
        return real(*a, **kw)

    monkeypatch.setattr(repo, "_message", counting)
    repo._opened_forget(conv.id)  # cold: nothing of this chat decoded yet
    started = time.perf_counter()
    page = api.load_messages(conv.id, None, 300)
    took = time.perf_counter() - started

    assert page == whole[-300:]
    assert took < 2.0
    assert 0 < opened["n"] < 100, opened  # was 600: every message, one DPAPI call each

    # Older pages walk back to the start with the same ids and rows.
    seen, before_id = list(page), page[0]["id"]
    while True:
        older = api.load_messages(conv.id, before_id, 300)
        if not older:
            break
        seen = older + seen
        before_id = older[0]["id"]
    assert seen == whole
