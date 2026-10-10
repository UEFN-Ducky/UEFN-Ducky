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

    def pending_work_held(self, conv_id: str) -> bool:
        return False

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
    import frontend.ui_web
    monkeypatch.setattr(frontend.ui_web, "agent_modes", fake_modes, raising=False)
    monkeypatch.setitem(sys.modules, "frontend.ui_web.project_chats", fake_chats)
    monkeypatch.setitem(sys.modules, "frontend.ui_web.group_orchestrator", fake_groups)
    module = importlib.import_module("backend.agent.a2a_broker")
    importlib.reload(module)
    return SimpleNamespace(mod=module, modes=fake_modes, chats=fake_chats)


def _wait_sent(modes: FakeAgentModes, count: int = 1, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(modes.sent) >= count:
            return
        modes.delivered.wait(0.05)
        modes.delivered.clear()
    raise AssertionError(f"expected {count} deliveries, got {modes.sent!r}")


@pytest.mark.parametrize("diagnostic", [
    '{"error":{"type":"rate_limit_error"}}',
    '{"error":{"code":"rate_limit_exceeded"}}',
    '{"error":{"code":"insufficient_quota"}}',
])
def test_current_structured_account_diagnostic(broker, diagnostic):
    assert broker.mod.account_limited(diagnostic)


def test_success_prose_is_not_account_diagnostic(broker):
    assert not broker.mod.account_limited("Implemented rate limit tests successfully")


def test_historical_prose_does_not_suppress_disk_failure(broker, monkeypatch):
    broker.chats.convs["recv1"].messages = [{"role": "assistant", "content": "Implemented rate limit tests successfully"}]
    broker.mod.open_thread("sender1", "recv1")
    notices = []
    monkeypatch.setattr(broker.mod, "send_notice", lambda **kw: notices.append(kw))
    broker.mod.on_agent_stopped("recv1", "error", detail="disk error")
    assert len(notices) == 1
    assert "disk error" in notices[0]["body"]


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
    monkeypatch.setattr(broker.mod.time, "time", lambda: 1000.0)
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
    # Current terminal diagnostic, not a historical assistant row.
    broker.mod.on_agent_stopped("recv1", "error", detail=broker.chats.convs["recv1"].messages[-1]["content"])
    time.sleep(0.3)
    assert broker.modes.sent == []  # neither the sender nor the group leader was woken
    assert [t.response_id for t in broker.mod.open_threads_for_receiver("recv1")] == [rid]
    assert timers and timers[0][0] == broker.mod._LIMIT_RETRY_S and timers[0][2] == ("recv1",)


def test_an_ordinary_error_still_notifies(broker):
    rid = broker.mod.open_thread("sender1", "recv1")
    broker.mod.on_agent_stopped("recv1", "error", detail="Codex exited with code 1")
    _wait_sent(broker.modes, 1)
    assert broker.modes.sent[0][0] == "sender1" and rid in broker.modes.sent[0][1]


@pytest.fixture
def fake_clock(broker, monkeypatch):
    clock = SimpleNamespace(now=1000.0, timers=[])
    monkeypatch.setattr(broker.mod.time, "time", lambda: clock.now)

    class Timer:
        def __init__(self, delay, fn, args=()):
            self.delay, self.fn, self.args = delay, fn, args
            self.cancelled = False
            clock.timers.append(self)
        def start(self): pass
        def cancel(self): self.cancelled = True
        def fire(self): self.fn(*self.args)
    monkeypatch.setattr(broker.mod.threading, "Timer", Timer)
    return clock


def test_cooldown_retains_never_admitted_work_and_thread_for_900_seconds(broker, fake_clock):
    broker.mod.on_agent_stopped("recv1", "error", detail='{"error":{"code":"insufficient_quota"}}', run_id="limited")
    outcome = broker.mod.send(sender_conv_id="sender1", receiver_conv_id="recv1", body="queued", expect_reply=True)
    original_deadline = broker.mod._cooldowns.copy()
    broker.mod.on_agent_stopped("recv1", "error", detail="rate_limit_error", run_id="limited")
    assert len(fake_clock.timers) == 1
    assert broker.mod._cooldowns == original_deadline
    fake_clock.now += 301
    broker.mod.sweep_quiet_threads()
    assert broker.mod.open_threads_for_receiver("recv1")[0].response_id == outcome["response_id"]
    broker.mod.send(sender_conv_id="sender1", receiver_conv_id="recv1", body="more", expect_reply=False)
    assert broker.modes.sent == []
    fake_clock.now = 1900
    fake_clock.timers[0].fire()
    _wait_sent(broker.modes)
    fake_clock.timers[0].fire()
    assert len(broker.modes.sent) == 1
    assert "queued" in broker.modes.sent[0][1] and "more" in broker.modes.sent[0][1]


@pytest.mark.parametrize("blocks", [[], [{"type": "tool_use", "name": "write_file"}]])
def test_admitted_quota_original_is_held_indefinitely(broker, fake_clock, blocks):
    broker.chats.convs["recv1"].messages[-1]["blocks"] = blocks
    outcome = broker.mod.send(sender_conv_id="sender1", receiver_conv_id="recv1", body="original", expect_reply=True)
    _wait_sent(broker.modes)
    broker.mod.on_agent_stopped("recv1", "error", detail="rate_limit_error", run_id="run")
    held = broker.mod.read_inbox("recv1")[0]
    assert held["status"] == "held_manual_recovery"
    assert held["response_id"] == outcome["response_id"] and held["from"] == "sender1"
    fake_clock.now += 900
    fake_clock.timers[0].fire()
    assert len(broker.modes.sent) == 1
    broker.mod.send(sender_conv_id="sender1", receiver_conv_id="recv1", body="new queued task", expect_reply=False)
    _wait_sent(broker.modes, 2)
    assert "original" not in broker.modes.sent[1][1]
    assert broker.mod.stats()["held"] == {"recv1": 1}


def test_cancel_invalidates_timer_and_reply_ownership(broker, fake_clock, monkeypatch):
    monkeypatch.setattr(broker.mod, "send_notice", lambda **kw: None)
    broker.mod.on_agent_stopped("recv1", "error", detail="rate_limit_error", run_id="limited")
    broker.mod.send(sender_conv_id="sender1", receiver_conv_id="recv1", body="cancel me", expect_reply=True)
    broker.mod.on_agent_cancelled_by_user("recv1")
    assert fake_clock.timers[0].cancelled
    fake_clock.now += 900
    fake_clock.timers[0].fire()
    assert broker.modes.sent == []
    assert broker.mod.open_threads_for_receiver("recv1") == []
    assert broker.mod.stats()["queued"] == {}


def test_later_turn_cannot_complete_or_reuse_held_reply_thread(broker, fake_clock, monkeypatch):
    original = broker.mod.send(sender_conv_id="sender1", receiver_conv_id="recv1",
                               body="uncertain original", expect_reply=True)
    _wait_sent(broker.modes)
    broker.mod.on_agent_stopped("recv1", "error", detail="insufficient_quota", run_id="original")
    fake_clock.now += 900
    fake_clock.timers[0].fire()
    new = broker.mod.send(sender_conv_id="sender1", receiver_conv_id="recv1",
                          body="separate task", expect_reply=True)
    assert new["response_id"] != original["response_id"]
    _wait_sent(broker.modes, 2)
    notices = []
    monkeypatch.setattr(broker.mod, "send_notice", lambda **kw: notices.append(kw))
    broker.mod.on_agent_stopped("recv1", "done", run_id="separate")
    assert len(notices) == 1
    assert original["response_id"] not in notices[0]["body"]
    assert broker.mod._threads[original["response_id"]].noticed is False
    assert broker.mod.stats()["held"] == {"recv1": 1}


def test_cooldown_does_not_lock_out_unrelated_adapter(broker, fake_clock):
    broker.mod.on_agent_stopped("recv1", "error", detail="rate_limit_error")
    broker.chats.convs["other"] = SimpleNamespace(coding_agent="codex", messages=[], ducky_name="Other", title="Other")
    broker.mod.send(sender_conv_id="sender1", receiver_conv_id="other", body="independent account", expect_reply=False)
    _wait_sent(broker.modes)
    assert broker.modes.sent[0][0] == "other"


@pytest.mark.parametrize('sender,receiver', [('stranger', 'sender1'), ('recv1', 'stranger')])
def test_unrelated_reply_cannot_consume_or_deliver_another_thread(broker, sender, receiver):
    rid = broker.mod.open_thread('sender1', 'recv1')
    other = broker.mod.open_thread('stranger', 'other')
    with pytest.raises(ValueError, match='participants'):
        broker.mod.send(sender_conv_id=sender, receiver_conv_id=receiver,
                        body='ff71cd35518a / 19878d4517 / revision abc123',
                        expect_reply=False, response_id=rid)
    assert {t.response_id for t in broker.mod._threads.values()} == {rid, other}
    assert broker.mod.stats()['queued'] == {}
    assert broker.modes.sent == []


def test_plan_revision_handoff_followup_and_reply_keep_attribution(broker):
    request = 'Master ff71cd35518a / original node 19878d4517 / revision abc123'
    outcome = broker.mod.send(sender_conv_id='sender1', receiver_conv_id='recv1',
                              body=request, expect_reply=True)
    rid = outcome['response_id']
    _wait_sent(broker.modes)
    followup = broker.mod.send(sender_conv_id='sender1', receiver_conv_id='recv1',
        body='Inspect the same revision abc123; no new task', expect_reply=False, response_id=rid)
    assert followup['closed_thread'] is False
    assert broker.mod.open_threads_for_receiver('recv1')[0].response_id == rid
    answer = request + ' / PASS with synthetic evidence'
    result = broker.mod.send(sender_conv_id='recv1', receiver_conv_id='sender1',
                             body=answer, expect_reply=False, response_id=rid)
    assert result['closed_thread'] is True
    _wait_sent(broker.modes, 2)
    row = broker.mod.read_inbox('sender1')[0]
    assert (row['from'], row['response_id'], row['body']) == ('recv1', rid, answer)
    report = next(text for cid, text in broker.modes.sent if cid == 'sender1')
    assert 'chat recv1' in report and rid in report
    assert answer in report and '<<<untrusted:peer-agent>>>' in report
    assert broker.mod.read_inbox('recv1')[0]['body'] == request


def test_duplicate_reply_cannot_replay_or_close_new_request(broker):
    rid = broker.mod.open_thread('sender1', 'recv1')
    broker.mod.send(sender_conv_id='recv1', receiver_conv_id='sender1', body='old result',
                    expect_reply=False, response_id=rid)
    _wait_sent(broker.modes)
    new = broker.mod.open_thread('sender1', 'recv1')
    with pytest.raises(ValueError, match='not open'):
        broker.mod.send(sender_conv_id='recv1', receiver_conv_id='sender1', body='old result',
                        expect_reply=False, response_id=rid)
    assert [t.response_id for t in broker.mod.open_threads_for_receiver('recv1')] == [new]
    assert len(broker.mod.read_inbox('sender1')) == 1
    assert len(broker.modes.sent) == 1


def test_unconfirmed_handoff_keeps_evidence_without_automatic_replay(broker, monkeypatch):
    attempts = []
    def fail(conv_id, text, *args, **kwargs):
        attempts.append((conv_id, text))
        raise RuntimeError('synthetic offline / delivery outcome unknown')
    monkeypatch.setattr(broker.modes, 'run_message', fail)
    body = 'ff71cd35518a / 19878d4517 / revision abc123'
    result = broker.mod.send(sender_conv_id='sender1', receiver_conv_id='recv1',
                             body=body, expect_reply=True)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not broker.mod.stats()['held']:
        time.sleep(0.01)
    rows = broker.mod.read_inbox('recv1')
    assert len(rows) == 1 and rows[0]['status'] == 'held_manual_recovery'
    assert (rows[0]['from'], rows[0]['response_id'], rows[0]['body']) == ('sender1', result['response_id'], body)
    broker.mod._kick_delivery('recv1')
    assert len(attempts) == 1
    assert broker.mod.open_threads_for_receiver('recv1')[0].response_id == result['response_id']
