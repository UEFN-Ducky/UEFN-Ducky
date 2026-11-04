"""Agent terminals must not flood the window: hidden ones send ~1 frame/s, shown ones <= 30/s."""
import json
import threading

from frontend.ui_web.lag.output_pacer import RESET_SEQ, OutputPacer, PacerHub


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class FakeSocket:
    """What TerminalBridge would put on the wire: one JSON frame per send."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.frames: list[tuple[float, dict]] = []

    def output(self, data: str) -> None:
        self.frames.append((self.clock.now, json.loads(json.dumps({"type": "output", "data": data}))))

    def status(self, status: dict) -> None:
        self.frames.append((self.clock.now, {"type": "status", **status}))

    def per_second(self) -> dict[int, int]:
        out: dict[int, int] = {}
        for t, _ in self.frames:
            out[int(t)] = out.get(int(t), 0) + 1
        return out

    def text(self) -> str:
        return "".join(f["data"] for _, f in self.frames if f["type"] == "output")


def _setup():
    clock = Clock()
    sock = FakeSocket(clock)
    history: list[str] = []  # the session's kept output ring
    pacer = OutputPacer(sock.output, sock.status, replay=lambda: "".join(history), clock=clock, hub=None)
    return clock, sock, history, pacer


def _stream(clock, history, pacer, lines, seconds):
    """``lines`` PTY reads spread over ``seconds``, the hub ticking every millisecond."""
    step = seconds / lines
    for i in range(lines):
        text = f"tests/test_{i}.py::test_case PASSED\r\n"
        history.append(text)
        pacer.push(text)
        clock.now += step
        pacer.tick()
    for _ in range(2000):  # let the last frames go out
        clock.now += 0.001
        pacer.tick()


def test_hidden_session_10k_lines_at_most_2_frames_a_second_then_full_replay():
    clock, sock, history, pacer = _setup()
    pacer.set_visible(False)
    _stream(clock, history, pacer, 10_000, 10.0)
    assert max(sock.per_second().values()) <= 2
    assert all(f["type"] == "status" for _, f in sock.frames)
    assert sum(f["hidden_output_chars"] for _, f in sock.frames) == len("".join(history))

    sock.frames.clear()
    pacer.set_visible(True)  # the person opens the tab
    assert len(sock.frames) == 1
    assert sock.frames[0][1]["data"] == RESET_SEQ + "".join(history)


def test_visible_session_is_batched_to_30_frames_a_second_and_loses_nothing():
    clock, sock, history, pacer = _setup()
    _stream(clock, history, pacer, 10_000, 10.0)
    assert max(sock.per_second().values()) <= 31  # 30/s, plus one at a second's edge
    assert len(sock.frames) <= 10 * 30 + 2  # was 10,000
    assert sock.text() == "".join(history)


def test_first_output_after_quiet_goes_out_at_once():
    clock, sock, _history, pacer = _setup()
    pacer.push("$ ")
    assert sock.text() == "$ "  # typing echo is not delayed


def test_hiding_drops_unsent_text_into_the_replay_and_attach_clears_it():
    clock, sock, history, pacer = _setup()
    pacer.push("a")
    history.append("a")
    pacer.push("b")  # inside the frame gap: buffered
    history.append("b")
    pacer.set_visible(False)
    pacer.set_visible(True)
    assert sock.text() == "a" + RESET_SEQ + "ab"

    sock.frames.clear()
    pacer.set_visible(False)
    pacer.push("c")
    pacer.drop_pending()  # a client attached and got the whole history
    pacer.set_visible(True)
    assert sock.text() == ""  # only the hidden status frame, no replay


def test_flush_sends_buffered_output_before_exit():
    clock, sock, _history, pacer = _setup()
    pacer.push("one")
    pacer.push("two")
    pacer.flush()
    assert sock.text() == "onetwo"


def test_one_hub_thread_delivers_for_many_sessions():
    hub = PacerHub()
    got: dict[int, list[str]] = {i: [] for i in range(100)}
    done = threading.Event()
    pacers = [OutputPacer(got[i].append, lambda s: None, hub=hub, fps=50) for i in range(100)]
    before = threading.active_count()
    for i, p in enumerate(pacers):
        for n in range(20):
            p.push(f"{n},")
    for _ in range(200):
        if all("".join(v).count(",") == 20 for v in got.values()):
            done.set()
            break
        threading.Event().wait(0.01)
    assert done.is_set()
    assert threading.active_count() <= before + 1


def test_terminal_manager_marks_unshown_sessions_hidden():
    from frontend.ui_web.terminal import manager as manager_mod

    mgr = manager_mod.TerminalManager()
    entries = {}
    for sid in ("t1", "t2"):
        entry = manager_mod._SessionEntry.__new__(manager_mod._SessionEntry)
        entry.pacer = OutputPacer(lambda d: None, lambda s: None, hub=None)
        entries[sid] = entry
    mgr._sessions = entries
    assert mgr._is_shown("t1") and mgr._is_shown("t2")  # nobody reported: all shown
    mgr.set_visible_terminals(["t1"], "main")
    assert entries["t1"].pacer.visible and not entries["t2"].pacer.visible
    mgr.set_visible_terminals(["t2"], "focus-1")
    assert entries["t2"].pacer.visible
    mgr.set_visible_terminals([], "focus-1")
    assert not entries["t2"].pacer.visible
