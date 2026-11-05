"""Test and build runs in agent terminals never wait on each other."""

from __future__ import annotations

import threading

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


def test_two_test_runs_in_the_same_repo_start_at_once(tmp_path) -> None:
    # Nov 5 2026: a deploy sat 14 minutes on "Waiting for <chat>'s test or build run on this PC".
    mgr = TerminalManager()
    cwd = _repo(tmp_path, "same")
    first, second = _Run(cwd), _Run(cwd)
    t1 = _start(mgr, first, "py -3 -m pytest")
    t2 = _start(mgr, second, "py -3 -m pytest")
    assert first.started.wait(2) and second.started.wait(2)  # both run side by side
    assert first.notes == [] and second.notes == []  # no "Waiting for" note
    first.finish.set()
    second.finish.set()
    t1.join(2)
    t2.join(2)
