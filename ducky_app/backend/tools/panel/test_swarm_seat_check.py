"""A seat whose check turn finished is seated, and a confirm build keeps the card's model."""

from __future__ import annotations

import json

from backend.tools.panel import ducky_swarm as sw
from backend.tools.panel import swarm_build as sb
from backend.tools.panel.test_ducky_swarm import INVENTORY_PLAN, FakeStore
from backend.tools.panel.test_swarm_build import Api


class CheckApi(Api):
    """ping runs the real queued_ping; the seat-check turn ends 'done' with *reply*."""

    def __init__(self, root, reply, **kw):
        super().__init__(root, **kw)
        self.reply, self.models = reply, {}

    def invite(self, group_id, profile, name, model, write_allowed):
        cid = super().invite(group_id, profile, name, model, write_allowed)
        self.models[name] = model
        return cid

    def ping(self, conv_id, text, timeout_s, later=None):
        ask = lambda cid, msg, secs: {"status": "done", "conv_id": cid, "assistant_text": self.reply}
        return sb.queued_ping(lambda cid, start: start(), ask, conv_id, text, timeout_s, later)


def _plan():
    return json.loads(json.dumps(INVENTORY_PLAN))


def test_done_seat_check_with_ok_is_assigned_not_failed(tmp_path):
    for reply in ("ok", ""):  # Nov 3 2026: the reply text was not read back yet
        api = CheckApi(tmp_path / (reply or "empty"), reply, plan=_plan())
        out = sb.build_swarm(api=api, from_plan=api.plan)
        seats = list(sb.walk_seats(out["tree"]))
        assert [s["status"] for s in seats] == ["assigned"] * 3
        assert out["failed"] == []
        assert sorted(c[1] for c in api.names("assign")) == ["api", "store", "ui"]


def test_confirm_with_model_edit_seats_that_model(tmp_path):
    api = CheckApi(tmp_path, "ok", plan=_plan())
    store = FakeStore(api.plan)
    done = sw.swarm_create("", None, from_plan=True, confirm=True, api=api, store=store,
                           edits=[{"seat": "Widget", "model": "codex:gpt-5"}])
    assert done["status"] == "done" and done["failed"] == []
    assert api.models["Widget"] == "codex:gpt-5" and api.models["API"] == "m1"
