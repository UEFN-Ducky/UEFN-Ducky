"""load_messages cuts giant tool results for the UI; load_tool_result has them whole."""

from __future__ import annotations

import json
from pathlib import Path

import frontend.ui_web.panel_api  # noqa: F401 - mixins import the package root first
from frontend.settings import PanelSettings
from frontend.ui_web import project_chats as pc
from frontend.ui_web.panel_api_chats import TOOL_RESULT_CAP, PanelApiChatsMixin

BIG = "y" * 40_000


def _transcript(n: int) -> list[dict]:
    out: list[dict] = []
    for i in range(n):
        if i % 2 == 0:
            out.append({"role": "user", "content": f"step {i}", "text": f"step {i}", "ts": i})
            continue
        blocks = []
        for j in range(4):
            body = BIG if (i % 50 == 1 and j == 0) else "x" * 600  # one 40 KB result per 25 turns
            blocks += [{"type": "text", "text": "Working on it."},
                       {"type": "tool_call", "id": f"t{i}_{j}", "name": "workspace_read_file", "arguments": {"path": "a"},
                        "status": "success", "duration_ms": 1, "result": {"data": body}}]
        out.append({"role": "assistant", "content": "Done.", "blocks": blocks, "run_id": f"r{i}", "ts": i})
    return out


def test_big_tool_results_are_cut_and_fetched_whole(tmp_path: Path) -> None:
    project = str(tmp_path / "Island")
    Path(project).mkdir()
    s = PanelSettings.load()
    s.uefn_project_root = project
    s.save()
    conv = pc.create_conversation(title="Big", project_root=project, skill_snapshot="x")
    conv = pc.load_conversation(conv.id, project)
    conv.messages = _transcript(2000)
    pc.save_conversation(conv, project, wait=True)
    api = PanelApiChatsMixin()

    whole = api.load_messages(conv.id, full_results=True)
    rows = api.load_messages(conv.id)
    before, after = len(json.dumps(whole)), len(json.dumps(rows))
    print(f"load_messages 2,000 msgs: {before / 1024:.0f} KB -> {after / 1024:.0f} KB")
    assert [r["id"] for r in rows] == [r["id"] for r in whole]
    assert after <= before * 0.85
    assert max(len(json.dumps(r)) for r in rows) < TOOL_RESULT_CAP + 2_000
    cut = [r for r in rows if (r.get("tool") or {}).get("resultTruncated")]
    assert len(cut) == 40 and all(r["tool"]["resultBytes"] == len(BIG) for r in cut)
    assert api.load_tool_result(conv.id, cut[0]["id"]) == {"ok": True, "result": BIG}

    # Paging still walks to the start, row ids unchanged.
    seen, before_id = [], None
    while True:
        page = api.load_messages(conv.id, before_id=before_id, limit=300)
        if not page:
            break
        seen = page + seen
        before_id = page[0]["id"]
    assert [r["id"] for r in seen] == [r["id"] for r in rows]
