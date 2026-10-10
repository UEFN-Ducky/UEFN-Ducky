"""Reload cards use stored snapshots, never the later contents on disk."""
from types import SimpleNamespace

from frontend.ui_web.panel_api import _messages_to_ui


def test_multiple_file_edits_survive_history_reload():
    edits = [{"path": path, "before": "old\n", "after": "new\n",
              "linesAdded": 1, "linesRemoved": 1, "kind": "write"}
             for path in ("abs:C:/repo/a.txt", "abs:C:/repo/b.txt")]
    conv = SimpleNamespace(id="chat", messages=[{
        "role": "assistant", "content": "", "coding_agent": "codex", "blocks": [{
            "type": "tool_call", "id": "edit", "name": "command_execution",
            "arguments": {}, "status": "success", "result": {"ok": True, "data": ""},
            "file_edit": edits[0], "file_edits": edits,
        }],
    }])
    rows = _messages_to_ui(conv, project_root="")
    done = next(row["tool"] for row in rows if row["role"] == "success")
    assert done["fileEdits"] == edits
    assert done["fileEdit"] == edits[0]


def test_no_change_snapshot_prevents_history_reconstruction(monkeypatch):
    from frontend.ui_web.verse_editor import agent_sync
    monkeypatch.setattr(agent_sync, "build_file_edit_meta", lambda *a: {"path": "wrong"})
    conv = SimpleNamespace(id="chat", messages=[{
        "role": "assistant", "content": "", "coding_agent": "codex", "blocks": [{
            "type": "tool_call", "id": "edit", "name": "Edit", "arguments": {},
            "status": "success", "result": {"ok": True}, "file_edits": [],
        }],
    }])
    rows = _messages_to_ui(conv, project_root="")
    done = next(row["tool"] for row in rows if row["role"] == "success")
    assert done["fileEdits"] == []
    assert "fileEdit" not in done
