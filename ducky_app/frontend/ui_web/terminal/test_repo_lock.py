"""One test or build run per repo, and one on the whole PC, at a time."""

from __future__ import annotations

import threading
import time

from frontend.ui_web.terminal import repo_lock
from frontend.ui_web.terminal.manager import TerminalManager


class _Run:
    """A test run that ends when told to (or when its terminal is killed)."""

    shell = "bash"

    def __init__(self, cwd: str) -> None:
        self.cwd = cwd
        self.started = threading.Event()
        self.finish = threading.Event()
        self.notes: list[str] = []

    def is_busy(self) -> bool:
        return False

    def run_command(self, command: str, **_kw) -> dict:
        self.started.set()
        self.finish.wait(5)
        return {"ok": True, "exit_code": 0, "output_tail": ""}

    def note(self, text: str) -> None:
        self.notes.append(text)

    def read_output_tail(self, **_kw) -> str:
        return ""


def _repo(tmp_path, name: str) -> str:
    (tmp_path / name / ".git").mkdir(parents=True)
    (tmp_path / name / "src").mkdir()
    return str(tmp_path / name / "src")


def _start(mgr: TerminalManager, session: _Run, command: str = "py -m pytest -q") -> threading.Thread:
    worker = threading.Thread(target=mgr._run_command, args=(session, command), kwargs={"timeout_s": 10}, daemon=True)
    worker.start()
    return worker


def test_heavy_commands() -> None:
    for cmd in ("pytest -q", "npm test", "npm run test", "pnpm test", "cargo build", "npx vitest run",
                "py release/publish_app.py", "bash release.sh", "tsc -b", "py build_exes.py",
                "cargo test -p duckyos-core --lib ducky_ai::", "cargo check", "npm run build", "msbuild x.sln",
                "dotnet test", "./gradlew assemble", "go test ./..."):
        assert repo_lock.is_heavy(cmd), cmd
    for cmd in ("git status", "npm install", "ls tests", "cargo fmt", "cargo --version"):
        assert not repo_lock.is_heavy(cmd), cmd


def test_repo_is_the_git_top(tmp_path) -> None:
    src = _repo(tmp_path, "a")
    assert repo_lock.repo_of(src) == repo_lock.repo_of(str(tmp_path / "a"))


def test_a_second_run_in_the_same_repo_waits(tmp_path) -> None:
    mgr = TerminalManager()
    cwd = _repo(tmp_path, "same")
    first, second = _Run(cwd), _Run(cwd)
    t1 = _start(mgr, first)
    assert first.started.wait(2)
    t2 = _start(mgr, second)
    time.sleep(0.2)
    assert not second.started.is_set()
    assert second.notes == ["Waiting for another terminal's test or build run in this repo"]
    first.finish.set()
    t1.join(2)
    assert second.started.wait(2)
    second.finish.set()
    t2.join(2)
    assert repo_lock.holder(repo_lock.repo_of(cwd)) == ""


def test_different_repos_take_turns_on_the_pc(tmp_path) -> None:
    mgr = TerminalManager()
    a, b = _Run(_repo(tmp_path, "a")), _Run(_repo(tmp_path, "b"))
    t1 = _start(mgr, a, "cargo test -p core")
    assert a.started.wait(2)
    t2 = _start(mgr, b)
    time.sleep(0.2)
    assert not b.started.is_set()
    assert b.notes == ["Waiting for another terminal's test or build run on this PC"]
    a.finish.set()
    t1.join(2)
    assert b.started.wait(2)
    b.finish.set()
    t2.join(2)
    assert repo_lock.holder(repo_lock.PC) == ""


def test_a_light_command_never_waits(tmp_path) -> None:
    mgr = TerminalManager()
    cwd = _repo(tmp_path, "light")
    tests, status = _Run(cwd), _Run(cwd)
    t1 = _start(mgr, tests)
    assert tests.started.wait(2)
    status.finish.set()
    _start(mgr, status, "git status").join(2)
    assert status.started.is_set()
    tests.finish.set()
    t1.join(2)


def test_a_killed_run_lets_go(tmp_path) -> None:
    mgr = TerminalManager()
    cwd = _repo(tmp_path, "killed")
    killed = _Run(cwd)
    killed.run_command = lambda command, **kw: (killed.started.set(), killed.finish.wait(5),  # type: ignore[method-assign]
                                                 {"ok": False, "error": "terminal closed"})[-1]
    t1 = _start(mgr, killed)
    assert killed.started.wait(2)
    killed.finish.set()  # TerminalSession.kill ends its run_command the same way
    t1.join(2)
    after = _Run(cwd)
    after.finish.set()
    _start(mgr, after).join(2)
    assert after.started.is_set() and after.notes == []


def test_a_run_that_raises_lets_go(tmp_path) -> None:
    mgr = TerminalManager()
    cwd = _repo(tmp_path, "raises")
    broken = _Run(cwd)

    def boom(command: str, **_kw) -> dict:
        raise RuntimeError("terminal session not running")

    broken.run_command = boom  # type: ignore[method-assign]
    try:
        mgr._run_command(broken, "pytest", timeout_s=1)
    except RuntimeError:
        pass
    assert repo_lock.holder(repo_lock.repo_of(cwd)) == ""
