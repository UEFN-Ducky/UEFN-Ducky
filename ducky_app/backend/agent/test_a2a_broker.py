"""Tests for the A2A broker (inbox, threads, notices) with faked chat runtime."""

from __future__ import annotations

import importlib
import sys
import threading
import time
from types import ModuleType, SimpleNamespace

import pytest


class FakeAgentModes(ModuleType):
    def __init__(self) -> None:
        super().__init__("frontend.ui_web.agent_modes")
        self.running: set[str] = set()
        self.sent: list[tuple[str, str]] = []
        self.delivered = threading.Event()

    def is_agent_running(self, conv_id: str) -> bool:
        return conv_id in self.running

    def wait_for_idle(self, conv_id: str, timeout: float = 1.0) -> bool:
        return conv_id not in self.running

    def run_message(self, conv_id: str, text: str, mode: str, model: str, **kwargs) -> str:
        self.sent.append((conv_id, text))
        self.delivered.set()
        return "run"


class FakeProjectChats(ModuleType):
    def __init__(self) -> None:
        super().__init__("frontend.ui_web.project_chats")
        self.convs: dict[str, SimpleNamespace] = {}

    def load_conversation(self, conv_id: str, project_root: str | None = None):
        return self.convs.get(conv_id)


class FakeGroupOrchestrator(ModuleType):
    def __init__(self, chats: FakeProjectChats) -> None:
        super().__init__("frontend.ui_web.group_orchestrator")
        self._chats = chats

    def is_group_conversation(self, conv) -> bool:
        return bool(getattr(conv, "is_group", False))

    def group_members(self, conv) -> list:
        return list(getattr(conv, "members", None) or [])

    def group_leader_member(self, group, members=None):
        rows = members if members is not None else self.group_members(group)
        leader_id = (getattr(group, "leader_conv_id", None) or "").strip()
        if leader_id:
            for m in rows:
                if str(m.get("member_conv_id") or "").strip() == leader_id:
                    return m
            return {"member_conv_id": leader_id, "name": "Leader"}
        return rows[0] if rows else None


@pytest.fixture()
def broker(monkeypatch):
    fake_modes = FakeAgentModes()
    fake_chats = FakeProjectChats()
    fake_groups = FakeGroupOrchestrator(fake_chats)
    fake_chats.convs["sender1"] = SimpleNamespace(
        ducky_name="Manager",
        title="Manager",
        coding_agent="ducky",
        messages=[],
        parent_conv_id="",
        is_group=False,
    )
    fake_chats.convs["recv1"] = SimpleNamespace(
        ducky_name="Builder",
        title="Builder",
        coding_agent="claude_code",
        messages=[{"role": "assistant", "content": "final answer text"}],
        parent_conv_id="",
        is_group=False,
    )
    monkeypatch.setitem(sys.modules, "frontend.ui_web.agent_modes", fake_modes)
    monkeypatch.setitem(sys.modules, "frontend.ui_web.project_chats", fake_chats)
    monkeypatch.setitem(sys.modules, "frontend.ui_web.group_orchestrator", fake_groups)
    module = importlib.import_module("backend.agent.a2a_broker")
    importlib.reload(module)
    return SimpleNamespace(mod=module, modes=fake_modes, chats=fake_chats)


def _wait_sent(modes: FakeAgentModes, count: int = 1, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if len(modes.sent) >= count:
            return
        modes.delivered.wait(0.05)
        modes.delivered.clear()
    raise AssertionError(f"expected {count} deliveries, got {modes.sent!r}")


def test_send_expect_reply_delivers_formatted_turn(broker):
    outcome = broker.mod.send(
        sender_conv_id="sender1",
        receiver_conv_id="recv1",
        body="please build the lobby",
        expect_reply=True,
    )
    assert outcome["response_id"]
    _wait_sent(broker.modes, 1)
    conv_id, text = broker.modes.sent[0]
    assert conv_id == "recv1"
    assert "[ducky:agent-message] from Manager (chat sender1)" in text
    assert outcome["response_id"] in text
    assert "please build the lobby" in text
    # Ring retains the delivered envelope for inbox re-reads.
    assert broker.mod.read_inbox("recv1")[0]["body"] == "please build the lobby"


def test_reply_closes_thread(broker):
    rid = broker.mod.open_thread("sender1", "recv1")
    broker.mod.send(
        sender_conv_id="recv1",
        receiver_conv_id="sender1",
        body="here is my answer",
        expect_reply=False,
        response_id=rid,
    )
    _wait_sent(broker.modes, 1)
    assert broker.mod.close_thread(rid) is None  # already closed by the reply


def test_busy_receiver_queues_until_stop(broker):
    broker.modes.running.add("recv1")
    broker.mod.send(
        sender_conv_id="sender1",
        receiver_conv_id="recv1",
        body="queued while busy",
        expect_reply=False,
    )
    time.sleep(0.3)
    assert broker.modes.sent == []
    broker.modes.running.discard("recv1")
    broker.mod.on_agent_stopped("recv1", "done")
    _wait_sent(broker.modes, 1)
    assert "queued while busy" in broker.modes.sent[0][1]


def test_turn_ended_notice_for_owed_reply(broker):
    rid = broker.mod.open_thread("sender1", "recv1")
    broker.mod.on_agent_stopped("recv1", "done")
    _wait_sent(broker.modes, 1)
    conv_id, text = broker.modes.sent[0]
    assert conv_id == "sender1"
    assert "[ducky:agent-notice]" in text
    assert "finished its turn without replying" in text
    assert rid in text


def test_deliver_result_thread_hands_answer_to_sender(broker):
    rid = broker.mod.open_thread("sender1", "recv1", deliver_result=True)
    broker.mod.on_agent_stopped("recv1", "done")
    _wait_sent(broker.modes, 1)
    conv_id, text = broker.modes.sent[0]
    assert conv_id == "sender1"
    assert f"Result for your request (response_id {rid})" in text
    assert "final answer text" in text
    assert "[ducky:agent-notice]" not in text
    assert broker.mod.close_thread(rid) is None  # consumed


def test_timeout_notifies_producer_not_quiet(broker):
    """Claude Code 900s timeout must wake the Producer with timed-out, not quiet."""
    rid = broker.mod.open_thread("sender1", "recv1")
    broker.mod.on_agent_stopped(
        "recv1",
        "timeout",
        detail="Claude Code timed out after 900s",
    )
    _wait_sent(broker.modes, 1)
    conv_id, text = broker.modes.sent[0]
    assert conv_id == "sender1"
    assert "[ducky:agent-notice]" in text
    assert "TIMED OUT" in text
    assert "NOT still working" in text
    assert "Claude Code timed out after 900s" in text
    assert "Partial progress before stop" in text
    assert "final answer text" in text
    assert "may still be working" not in text
    assert rid in text


def _wire_hub_to_producer(broker) -> None:
    """recv1 → Hub group → Producer as leader; Hub → Root group → Producer."""
    broker.chats.convs["producer"] = SimpleNamespace(
        ducky_name="Producer",
        title="Producer",
        coding_agent="ducky",
        messages=[],
        parent_conv_id="",
        is_group=False,
    )
    broker.chats.convs["hub"] = SimpleNamespace(
        ducky_name="Hub",
        title="Hub",
        coding_agent="ducky",
        messages=[],
        parent_conv_id="root",
        is_group=True,
        leader_conv_id="producer",
        members=[{"member_conv_id": "producer", "name": "Producer"}],
    )
    broker.chats.convs["root"] = SimpleNamespace(
        ducky_name="5P Coop",
        title="5P Coop",
        coding_agent="ducky",
        messages=[],
        parent_conv_id="",
        is_group=True,
        leader_conv_id="producer",
        members=[{"member_conv_id": "producer", "name": "Producer"}],
    )
    broker.chats.convs["recv1"].parent_conv_id = "hub"


def test_timeout_escalates_to_group_leader_without_open_thread(broker):
    """No expect_reply thread — system still wakes the Producer via group tree."""
    _wire_hub_to_producer(broker)
    broker.mod.on_agent_stopped(
        "recv1",
        "timeout",
        detail="Claude Code timed out after 900s",
    )
    _wait_sent(broker.modes, 1)
    targets = [cid for cid, _ in broker.modes.sent]
    assert targets.count("producer") == 1
    text = next(t for cid, t in broker.modes.sent if cid == "producer")
    assert "TIMED OUT" in text
    assert "Claude Code timed out after 900s" in text


def test_timeout_escalation_dedupes_when_producer_already_notified(broker):
    """Producer already got the owed-thread notice — do not send a second escalate."""
    _wire_hub_to_producer(broker)
    rid = broker.mod.open_thread("producer", "recv1")
    broker.mod.on_agent_stopped(
        "recv1",
        "timeout",
        detail="Claude Code timed out after 900s",
    )
    _wait_sent(broker.modes, 1)
    producer_msgs = [t for cid, t in broker.modes.sent if cid == "producer"]
    assert len(producer_msgs) == 1
    assert rid in producer_msgs[0]
    assert "TIMED OUT" in producer_msgs[0]


def test_user_cancel_drops_queue_and_notifies(broker):
    rid = broker.mod.open_thread("sender1", "recv1")
    broker.modes.running.add("recv1")
    broker.mod.send(
        sender_conv_id="sender1",
        receiver_conv_id="recv1",
        body="never delivered",
        expect_reply=False,
    )
    broker.mod.on_agent_cancelled_by_user("recv1")
    _wait_sent(broker.modes, 1)
    conv_id, text = broker.modes.sent[0]
    assert conv_id == "sender1"
    assert "stopped by the user" in text
    assert rid in text
    assert broker.mod.close_thread(rid) is None
    assert broker.mod.read_inbox("recv1") == []


def _team(broker) -> None:
    """Coordinator leads a parent group holding group A; A's builder leads A (builder + verifier)."""
    chats = broker.chats
    for cid in ("coord", "builder", "verifier", "stranger"):
        chats.convs[cid] = SimpleNamespace(ducky_name=cid, title=cid, coding_agent="codex", messages=[], parent_conv_id="", is_group=False)
    chats.convs["groupA"] = SimpleNamespace(
        id="groupA", is_group=True, leader_conv_id="builder", title="A", ducky_name="A", coding_agent="ducky",
        members=[{"member_conv_id": "builder"}, {"member_conv_id": "verifier"}],
    )
    chats.convs["parent"] = SimpleNamespace(
        id="parent", is_group=True, leader_conv_id="coord", title="P", ducky_name="P", coding_agent="ducky",
        members=[{"member_conv_id": "coord"}, {"member_conv_id": "groupA"}],
    )
    chats.list_all_conversation_metadata = lambda project_root=None: list(chats.convs.values())


def test_authority_flows_down_the_group_hierarchy_only(broker):
    _team(broker)
    leads = broker.mod.leads_receiver
    assert leads("coord", "builder")  # through the nested group
    assert leads("coord", "verifier")
    assert leads("builder", "verifier")
    assert not leads("verifier", "builder")  # a member reporting up is not an order
    assert not leads("builder", "coord")
    assert not leads("stranger", "builder")
    assert not leads("coord", "coord")


def test_a_leaders_assignment_is_delivered_as_work_and_a_report_stays_fenced(broker):
    _team(broker)
    broker.mod.send(sender_conv_id="coord", receiver_conv_id="builder", body="fix 421acea30a", expect_reply=True)
    _wait_sent(broker.modes, 1)
    order = broker.modes.sent[0][1]
    assert "assignment from your group leader" in order and "<<<untrusted" not in order
    assert "fix 421acea30a" in order
    broker.mod.send(sender_conv_id="builder", receiver_conv_id="coord", body="done: abc123", expect_reply=False)
    _wait_sent(broker.modes, 2)
    report = broker.modes.sent[1][1]
    assert "<<<untrusted:peer-agent>>>" in report and "assignment from your group leader" not in report


def test_delivery_never_interrupts_a_busy_receiver(broker, monkeypatch):
    seen: list[dict] = []
    original = broker.modes.run_message

    def run_message(conv_id, text, mode, model, **kwargs):
        seen.append(kwargs)
        return original(conv_id, text, mode, model, **kwargs)

    monkeypatch.setattr(broker.modes, "run_message", run_message)
    broker.mod.send(sender_conv_id="sender1", receiver_conv_id="recv1", body="hi", expect_reply=False)
    _wait_sent(broker.modes, 1)
    assert seen and seen[0].get("queue_if_busy") is True


def test_the_team_is_found_in_its_own_project_when_another_project_is_open(broker):
    _team(broker)
    team = list(broker.chats.convs.values())
    broker.chats.list_all_conversation_metadata = lambda project_root=None: []  # the open project has none of them
    broker.chats.iter_conversations_by_project = lambda: [("ExampleProject1", c) for c in team]
    assert broker.mod.leads_receiver("coord", "builder")


def test_an_account_limit_does_not_wake_the_team(broker, monkeypatch):
    """Out of credits, each notice woke another agent into the same limit."""
    _wire_hub_to_producer(broker)
    broker.chats.convs["recv1"].messages = [
        {"role": "assistant", "content": '{"type":"error","message":"You’ve hit your usage limit. Visit https://chatgpt.com/settings/usage to purchase more credits"}'}
    ]
    timers: list[tuple] = []

    def _timer(delay, fn, args=()):
        timers.append((delay, fn, args))
        return SimpleNamespace(daemon=False, start=lambda: None)

    monkeypatch.setattr(broker.mod.threading, "Timer", _timer)
    rid = broker.mod.open_thread("sender1", "recv1")
    broker.mod._inbox.setdefault("recv1", broker.mod.deque()).append(
        broker.mod.Envelope(sender_conv_id="sender1", receiver_conv_id="recv1", body="next task")
    )
    broker.mod.on_agent_stopped("recv1", "error", detail="")
    time.sleep(0.3)
    assert broker.modes.sent == []  # neither the sender nor the group leader was woken
    assert [t.response_id for t in broker.mod.open_threads_for_receiver("recv1")] == [rid]
    assert timers and timers[0][0] == broker.mod._LIMIT_RETRY_S and timers[0][2] == ("recv1",)


def test_an_ordinary_error_still_notifies(broker):
    rid = broker.mod.open_thread("sender1", "recv1")
    broker.mod.on_agent_stopped("recv1", "error", detail="Codex exited with code 1")
    _wait_sent(broker.modes, 1)
    assert broker.modes.sent[0][0] == "sender1" and rid in broker.modes.sent[0][1]
