"""Sandbox: per-chat folders and profile, refusals, jobs that end whole trees, ports, local
HTTP, MCP sessions, worktrees, the file-tool and writer rules, and no leaks over many runs."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from backend.sandbox import env as sandbox_env_mod
from backend.sandbox import jobs, manifest, paths, policy, service

CONV = "chat-sbx-1"
WIN = sys.platform == "win32"
needs_windows = pytest.mark.skipif(not WIN, reason="Job Objects are Windows-only")


@pytest.fixture(autouse=True)
def _no_programs_left():
    yield
    service.stop_everything()


def test_each_chat_gets_its_own_folders_and_bad_ids_are_refused():
    box = paths.sandbox_dir(CONV, create=True)
    for sub in paths.SUBDIRS:
        assert (box / sub).is_dir()
    assert paths.owner_of(str(box / "work" / "x.txt")) == CONV
    assert paths.is_sandbox_path(str(box / "work"), CONV)
    assert not paths.is_sandbox_path(str(box / "work"), "other-chat")
    assert not paths.is_sandbox_path(str(Path.home()))
    for bad in ("", "..", "a/b", "x" * 81, "a b"):
        with pytest.raises(ValueError):
            paths.sandbox_dir(bad)


def test_profile_folders_and_caches_point_into_the_sandbox():
    box = paths.sandbox_dir(CONV, create=True)
    os.environ["DUCKY_CONV_ID"] = "leak-me"
    try:
        env = sandbox_env_mod.sandbox_env(box, {"EXTRA": "1"})
    finally:
        os.environ.pop("DUCKY_CONV_ID", None)
    for key in ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "PIP_CACHE_DIR", "npm_config_cache"):
        assert paths.is_sandbox_path(env[key], CONV), key
    assert env["DUCKY_SKIP_CLI_UPDATE"] == "1" and env["EXTRA"] == "1"
    assert "DUCKY_CONV_ID" not in env


@pytest.mark.parametrize("command", [
    "runas /user:admin cmd", "winget install git", "msiexec /i x.msi", "reg add HKLM\\Software\\X",
    "setx PATH c:\\x", "schtasks /create /tn x", "py release/publish_app.py --phase upload",
    "bash deploy/prod/deploy.sh", "npm publish", "gh release create v1", "git push origin main",
    "python scripts/release.py --publish",
])
def test_system_changes_releases_and_pushes_are_refused(command):
    assert policy.refusal(command)


@pytest.mark.parametrize("command", ["npm ci", "python -m http.server 8000", "git clone https://x/y.git",
                                     "uv pip install requests", "git commit -m test"])
def test_ordinary_work_is_allowed(command):
    assert policy.refusal(command) == ""


def test_the_card_is_skipped_when_the_chat_allows_sandbox_commands(monkeypatch):
    from backend.tools.panel import permission_prompt as prompts

    monkeypatch.setattr(prompts, "allows_sandbox", lambda conv: True)
    asked = []
    monkeypatch.setattr("backend.tools.panel.panel_ui.ducky_ask_user", lambda *a, **k: asked.append(1) or "{}")
    assert policy.ask_to_run(CONV, "Run", "npm ci") == "sandbox"
    assert asked == []


def test_allow_sandbox_commands_answer_is_remembered(monkeypatch):
    from backend.tools.panel import permission_prompt as prompts

    remembered = []
    monkeypatch.setattr(prompts, "allows_sandbox", lambda conv: False)
    monkeypatch.setattr(prompts, "allow_sandbox", lambda conv: remembered.append(conv))
    monkeypatch.setattr("backend.tools.panel.panel_ui.ducky_ask_user",
                        lambda *a, **k: '{"answers": {"sandbox_run": {"selected": ["sandbox"]}}}')
    assert policy.ask_to_run(CONV, "Run", "npm ci") == "sandbox"
    assert remembered == [CONV]
    monkeypatch.setattr("backend.tools.panel.panel_ui.ducky_ask_user",
                        lambda *a, **k: '{"answers": {"sandbox_run": {"selected": ["deny"]}}}')
    assert "chose not" in policy.ask_to_run(CONV, "Run", "npm ci")


def test_manifest_stays_small():
    box = paths.sandbox_dir(CONV, create=True)
    for i in range(manifest.MAX_EVENTS + 40):
        manifest.record(box, "run", command="x" * 2000, i=i)
    events = manifest.read(box)["events"]
    assert len(events) == manifest.MAX_EVENTS
    assert events[-1]["i"] == manifest.MAX_EVENTS + 39
    assert len(events[-1]["command"]) < 700


def _script(box: Path, name: str, body: str) -> Path:
    path = box / "work" / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


@needs_windows
def test_stopping_a_program_ends_everything_it_started():
    import psutil

    box = paths.sandbox_dir(CONV, create=True)
    child = _script(box, "child.py", """
        import subprocess, sys, time
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        print("started", flush=True)
        time.sleep(120)
    """)
    run = jobs.Launched([sys.executable, str(child)], cwd=str(box / "work"), env=dict(os.environ),
                        log=box / "logs" / "t.log")
    deadline = time.time() + 15
    log = box / "logs" / "t.log"
    while (len(run.tree()) < 2 or "started" not in jobs.read_tail(log)) and time.time() < deadline:
        time.sleep(0.2)
    tree = run.tree()
    assert len(tree) >= 2, tree
    run.stop()
    time.sleep(0.5)
    assert not any(psutil.pid_exists(p) and psutil.Process(p).status() != psutil.STATUS_ZOMBIE for p in tree)
    assert "started" in jobs.read_tail(box / "logs" / "t.log")
    assert run.job.handle == 0


@needs_windows
def test_a_chatty_program_keeps_only_a_capped_log(monkeypatch):
    monkeypatch.setattr(jobs, "LOG_CAP_BYTES", 20_000)
    box = paths.sandbox_dir(CONV, create=True)
    loud = _script(box, "loud.py", """
        import sys
        for i in range(4000):
            print("line", i, "x" * 40)
    """)
    run = jobs.Launched([sys.executable, str(loud)], cwd=str(box / "work"), env=dict(os.environ),
                        log=box / "logs" / "loud.log")
    run.proc.wait(timeout=30)
    run.stop()
    log = box / "logs" / "loud.log"
    assert log.stat().st_size <= 20_000
    assert log.with_suffix(".log.old").stat().st_size <= 20_000
    assert "line 3999" in jobs.read_tail(log)


@needs_windows
def test_a_web_server_gets_a_free_port_that_only_its_sandbox_can_call(monkeypatch):
    box = paths.sandbox_dir(CONV, create=True)
    (box / "work" / "index.html").write_text("<h1>hello sandbox</h1>", encoding="utf-8")
    server = _script(box, "serve.py", """
        import http.server, os, socketserver
        port = int(os.environ["PORT"])
        with socketserver.TCPServer(("127.0.0.1", port), http.server.SimpleHTTPRequestHandler) as s:
            print("serving", port, flush=True)
            s.serve_forever()
    """)
    out = service.launch(CONV, sys.executable, [str(server)], ports=["PORT"], name="web")
    assert out["ok"], out
    port = out["ports"]["PORT"]
    deadline = time.time() + 15
    got = {}
    while time.time() < deadline:
        got = service.http(CONV, port, "GET", "/index.html")
        if got.get("ok"):
            break
        time.sleep(0.3)
    assert got.get("ok") and "hello sandbox" in got.get("text", ""), got
    assert port in service.owned_ports(CONV)
    assert service.http("someone-else", port)["ok"] is False
    looked = service.look(CONV, out["pid"])
    assert looked["ok"] and "serving" in looked["output"] and port in looked["listening"]
    stopped = service.stop(CONV, out["pid"])
    assert stopped["stopped"][0]["pid"] == out["pid"]
    assert service.look(CONV)["programs"] == []


def test_launch_refuses_what_never_runs_and_folders_outside_the_sandbox(tmp_path):
    assert "never" in service.launch(CONV, "runas", ["/user:x", "cmd"])["error"].lower() or \
        service.launch(CONV, "runas", ["/user:x", "cmd"])["ok"] is False
    out = service.launch(CONV, sys.executable, ["-c", "print(1)"], cwd=str(tmp_path))
    assert out["ok"] is False and "inside this chat's sandbox" in out["error"]
    out = service.launch(CONV, "no-such-program-xyz")
    assert out["ok"] is False


def test_cwd_may_name_the_work_folder_or_not():
    # A live agent passed cwd="work/app" (relative to the sandbox) and got "work/work/app".
    box = paths.sandbox_dir(CONV, create=True)
    (box / "work" / "app").mkdir(parents=True, exist_ok=True)
    for cwd in ("app", "work/app"):
        out = service.launch(CONV, sys.executable, ["-c", "import time; time.sleep(30)"], cwd=cwd)
        assert out["ok"], (cwd, out)
        service.stop(CONV, out["pid"])
    out = service.launch(CONV, sys.executable, ["-c", "print(1)"], cwd="../../elsewhere")
    assert out["ok"] is False


@needs_windows
@pytest.mark.skipif(os.environ.get("DUCKY_WINDOW_TESTS") != "1",
                    reason="pops a real 'cap-test' window on the person's screen; DUCKY_WINDOW_TESTS=1 runs it")
def test_a_window_capture_is_only_the_window():
    """GetWindowRect counts the invisible resize borders: captures showed what was behind them."""
    pytest.importorskip("tkinter")
    from backend.sandbox import capture

    box = paths.sandbox_dir(CONV, create=True)
    out = service.launch(CONV, sys.executable, ["-c", "import tkinter as t; r = t.Tk(); r.title('cap-test'); "
                                                      "r.geometry('320x200'); r.mainloop()"])
    assert out["ok"], out
    try:
        wins = []
        deadline = time.time() + 15
        while not wins and time.time() < deadline:
            wins = [w for w in capture.windows_of(service._programs[out["pid"]].run.tree()) if w["title"] == "cap-test"]
            time.sleep(0.2)
        assert wins, "the Tk window never showed"
        rect = wins[0]["rect"]
        shot = capture.capture(wins[0], CONV)
        assert shot["ok"], shot
        assert shot["width"] <= rect["right"] - rect["left"] and shot["height"] <= rect["bottom"] - rect["top"]
        assert shot["width"] >= 200 and shot["height"] >= 150
    finally:
        service.stop(CONV, out["pid"])
    assert box.exists()


def test_files_are_written_inside_the_sandbox_only():
    out = service.launch(CONV, sys.executable, ["-c", "print(1)"], files={"../../escape.txt": "x"})
    assert out["ok"] is False and "inside the sandbox" in out["error"]


@needs_windows
def test_many_runs_leave_no_processes_handles_or_threads_behind():
    """Leak soak: 20 launch/stop cycles end with the test process back where it started."""
    import threading

    import psutil

    me = psutil.Process()
    box = paths.sandbox_dir(CONV, create=True)
    sleeper = _script(box, "sleeper.py", """
        import subprocess, sys, time
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        print("up", flush=True)
        time.sleep(60)
    """)
    # One warm-up run so lazily loaded modules (psutil, ctypes) are not counted as growth.
    warm = service.launch(CONV, sys.executable, [str(sleeper)])
    service.stop(CONV, warm["pid"])
    base_handles = me.num_handles()
    base_threads = threading.active_count()
    for _ in range(20):
        out = service.launch(CONV, sys.executable, [str(sleeper)])
        assert out["ok"], out
        service.look(CONV, out["pid"])
        service.stop(CONV, out["pid"])
    time.sleep(1.0)
    assert service.all_programs() == []
    assert not [c for c in me.children(recursive=True) if c.is_running() and c.status() != psutil.STATUS_ZOMBIE]
    assert me.num_handles() - base_handles < 60, (base_handles, me.num_handles())
    assert threading.active_count() - base_threads <= 2, (base_threads, threading.active_count())


@needs_windows
def test_programs_that_exit_on_their_own_leave_the_registry():
    out = service.launch(CONV, sys.executable, ["-c", "import time; time.sleep(1.5)"])
    assert out["ok"]
    # On a loaded PC the interpreter alone can take seconds to start: wait for the exit, don't guess.
    deadline = time.time() + 30
    while service.look(CONV)["programs"] and time.time() < deadline:
        time.sleep(0.5)
    assert service.look(CONV)["programs"] == []


def test_clear_deletes_the_sandbox_and_stale_ones_are_pruned():
    box = paths.sandbox_dir(CONV, create=True)
    (box / "work" / "f.txt").write_text("x", encoding="utf-8")
    assert service.clear(CONV)["ok"]
    assert not box.exists()
    old = paths.sandbox_dir("old-chat", create=True)
    (old / ".last_used").write_text(str(int(time.time() - paths.PRUNE_AFTER_S - 10)), encoding="utf-8")
    keep = paths.sandbox_dir("fresh-chat", create=True)
    assert service.prune() == ["old-chat"]
    assert not old.exists() and keep.exists()
    assert service.prune(chat_exists=lambda cid: cid != "fresh-chat") == ["fresh-chat"]
    built = paths.sandbox_dir("app-chat", create=True)
    manifest.record(built, "launch", name="web", command=["node"])
    (built / ".last_used").write_text(str(int(time.time() - paths.PRUNE_AFTER_S - 10)), encoding="utf-8")
    assert service.prune(chat_exists=lambda cid: False) == []  # an app it built stays until deleted
    assert built.exists()


@needs_windows
def test_a_port_named_in_args_is_filled_in():
    # Oct 31 2026: "python -m http.server %PORT%" got the text "%PORT%" and quit at once.
    code = "import sys, time; print('args', sys.argv[1:], flush=True); time.sleep(30)"
    out = service.launch(CONV, sys.executable, ["-c", code, "%PORT%", "--who=%WHO%"], ports=["PORT"],
                         env={"WHO": "duck"}, name="args")
    assert out["ok"], out
    want = f"args ['{out['ports']['PORT']}', '--who=duck']"
    deadline = time.time() + 10
    while time.time() < deadline and want not in service.look(CONV, out["pid"])["output"]:
        time.sleep(0.2)
    assert want in service.look(CONV, out["pid"])["output"]
    launched = [e for e in manifest.read(paths.sandbox_dir(CONV))["events"] if e.get("kind") == "launch"]
    assert launched[-1]["args"][2] == "%PORT%"  # a relaunch picks a new port


@needs_windows
def test_an_app_is_listed_after_it_stops_and_runs_again():
    box = paths.sandbox_dir(CONV, create=True)
    server = _script(box, "serve.py", """
        import http.server, os, socketserver
        with socketserver.TCPServer(("127.0.0.1", int(os.environ["PORT"])), http.server.SimpleHTTPRequestHandler) as s:
            s.serve_forever()
    """)
    first = service.launch(CONV, sys.executable, [str(server)], ports=["PORT"], name="web", env={"A": "1"})
    assert first["ok"], first
    listed = [a for a in service.apps()["apps"] if a["conv_id"] == CONV and a["name"] == "web"]
    assert listed and listed[0]["running"] and first["ports"]["PORT"] in listed[0]["ports"]
    assert service.relaunch(CONV, "web")["already_running"]
    service.stop(CONV, first["pid"])
    stopped = next(a for a in service.apps()["apps"] if a["conv_id"] == CONV and a["name"] == "web")
    assert stopped["running"] is False and stopped["pid"] == 0
    again = service.call("relaunch", {"conv_id": CONV, "name": "web"})
    assert again["ok"] and again["pid"] != first["pid"], again
    assert service._programs[again["pid"]].cwd == str(box / "work")
    assert service.relaunch(CONV, "nope")["ok"] is False


@needs_windows
def test_an_app_launched_before_the_full_record_still_runs_again():
    box = paths.sandbox_dir(CONV, create=True)
    manifest.record(box, "launch", name="old", command=[sys.executable, "-c", "import time; time.sleep(30)"], ports={})
    out = service.relaunch(CONV, "old")
    assert out["ok"], out


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.mark.skipif(not __import__("shutil").which("git"), reason="git not installed")
def test_a_worktree_is_one_commit_of_a_known_project_and_goes_with_the_sandbox(tmp_path, monkeypatch):
    from backend.sandbox import gitops

    repo = tmp_path / "proj"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "base", cwd=repo)
    (repo / "app.txt").write_text("v1", encoding="utf-8")
    _git("add", "app.txt", cwd=repo)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "v1", cwd=repo)
    (repo / "app.txt").write_text("uncommitted", encoding="utf-8")
    (repo / "secret_input.bin").write_text("ignored input", encoding="utf-8")
    monkeypatch.setattr("backend.bridge.client.workspace_roots", lambda: [str(repo)])
    monkeypatch.setattr("backend.bridge.client._recent_project_roots", lambda: [])
    out = gitops.worktree(CONV, str(repo), copy=["secret_input.bin", "../nope"])
    tree = Path(out["path"])
    assert (tree / "app.txt").read_text(encoding="utf-8") == "v1"  # the commit, not the dirty file
    assert (tree / "secret_input.bin").is_file()
    assert out["missing"] and "relative" in out["missing"][0]
    assert gitops.remove_worktrees(CONV) == [str(tree)]
    assert not tree.exists()
    with pytest.raises(gitops.GitError):
        gitops.worktree(CONV, str(tmp_path / "elsewhere"))


def test_clone_takes_public_https_only():
    from backend.sandbox import gitops

    with pytest.raises(gitops.GitError):
        gitops.clone(CONV, "file:///c:/secret")
    with pytest.raises(gitops.GitError):
        gitops.clone(CONV, "https://127.0.0.1/x.git")


def test_downloads_refuse_private_addresses(tmp_path):
    from backend.sandbox.fetch import FetchError, download

    with pytest.raises(FetchError):
        download("http://127.0.0.1:9/x.zip", tmp_path / "x.zip")
    assert not (tmp_path / "x.zip").exists()


def test_unknown_runtime_and_unknown_call_are_errors():
    from backend.sandbox import runtimes

    assert runtimes.setup("cobol")["ok"] is False
    assert service.call("explode", {"conv_id": CONV})["ok"] is False
    assert service.call("open", {"conv_id": ""})["ok"] is False


@needs_windows
def test_mcp_tools_of_a_server_the_sandbox_starts(tmp_path):
    box = paths.sandbox_dir(CONV, create=True)
    server = _script(box, "mcp_server.py", """
        from mcp.server.fastmcp import FastMCP
        mcp = FastMCP("t")

        @mcp.tool()
        def add(a: int, b: int) -> int:
            return a + b

        mcp.run()
    """)
    root = Path(__file__).resolve().parents[3]
    wrapper = box / "work" / "server.cmd"
    wrapper.write_text(f'@"{sys.executable}" "{server}"\r\n', encoding="utf-8")
    from backend.sandbox import mcp_client

    out = mcp_client.call(CONV, command=str(wrapper), list_tools=True,
                          calls=[{"tool": "add", "arguments": {"a": 2, "b": 3}}], timeout_s=60,
                          env={"PYTHONPATH": str(root / "ducky_app")})
    assert out.get("ok"), out
    assert "add" in out["tools"]
    first = out["results"][0]
    assert first.get("json") == {"result": 5} or "5" in str(first)
    refused = mcp_client.call(CONV, command=sys.executable)
    assert refused["ok"] is False and "inside this chat's sandbox" in refused["error"]


def test_sandbox_writes_never_ask_and_never_land_in_the_changes_tab():
    from backend.workspace.writer import _in_own_sandbox

    box = paths.sandbox_dir(CONV, create=True)
    assert _in_own_sandbox((str(box / "work" / "a.py"),), CONV)
    assert not _in_own_sandbox((str(box / "work" / "a.py"),), "other-chat")
    assert not _in_own_sandbox((str(Path.cwd() / "a.py"),), CONV)


def test_file_tools_reach_the_chats_own_sandbox():
    from backend.workspace.run_folders import granted

    assert granted(CONV) == [] or all(not paths.is_sandbox_path(f) for f in granted(CONV))
    box = paths.sandbox_dir(CONV, create=True)
    assert os.path.realpath(str(box)) in granted(CONV)
    assert os.path.realpath(str(box)) not in granted("other-chat")


def test_sandbox_and_self_care_panel_calls_are_this_pc_only():
    from frontend.duckyos_account import remote_denied

    for method in ("sandbox_call", "sandbox_status", "sandbox_stop_program", "sandbox_delete",
                   "sandbox_apps", "sandbox_relaunch",
                   "ducky_health_snapshot", "ducky_self_care_run"):
        assert remote_denied(method, on_this_pc=False)
        assert not remote_denied(method, on_this_pc=True)


def test_tools_refuse_before_asking_and_hand_off_in_the_window(monkeypatch):
    import json

    from backend.tools.core import sandbox_tools

    asked = []
    monkeypatch.setattr("backend.sandbox.policy.ask_to_run", lambda *a: asked.append(a) or "once")
    monkeypatch.setattr(sandbox_tools, "_chat", lambda: CONV)
    out = json.loads(sandbox_tools.sandbox_run("winget install x"))
    assert out["ok"] is False and asked == []
    monkeypatch.setattr("frontend.ui_web.agent_modes._in_bridge_process", lambda: False)
    called = []
    monkeypatch.setattr(service, "call", lambda name, args: called.append((name, args)) or {"ok": True})
    json.loads(sandbox_tools.sandbox_open())
    assert called[-1][0] == "open" and called[-1][1]["conv_id"] == CONV
    json.loads(sandbox_tools.sandbox_run("npm ci"))
    assert asked and called[-1][0] == "run"


@pytest.mark.parametrize("in_window", [True, False])
def test_every_tool_hands_off_all_its_arguments(monkeypatch, in_window):
    """A live Claude Code run: sandbox_clone, sandbox_fetch and sandbox_launch all failed with
    "_forward() got multiple values for argument 'name'" (their own ``name`` argument)."""
    import json
    import urllib.request

    from backend.tools.core import sandbox_tools

    monkeypatch.setattr("backend.sandbox.policy.ask_to_run", lambda *a: "once")
    monkeypatch.setattr(sandbox_tools, "_chat", lambda: CONV)
    monkeypatch.setattr("frontend.ui_web.agent_modes._in_bridge_process", lambda: not in_window)
    called: list[tuple[str, dict]] = []
    monkeypatch.setattr(service, "call", lambda call, args: called.append((call, args)) or {"ok": True})

    class _Answer:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self.body

    def fake_urlopen(req, timeout=0):
        sent = json.loads(req.data.decode("utf-8"))["args"]
        called.append((sent["name"], sent["args"]))
        return _Answer(b'{"ok": true, "result": {"ok": true}}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    calls = [
        ("fetch", lambda: sandbox_tools.sandbox_fetch("https://example.com/a.zip", name="a.zip", sha256="0" * 64)),
        ("clone", lambda: sandbox_tools.sandbox_clone("owner/repo", ref="main", name="repo")),
        ("worktree", lambda: sandbox_tools.sandbox_worktree("C:/p", commit="HEAD", name="wt", copy=["a.json"])),
        ("setup", lambda: sandbox_tools.sandbox_setup("node")),
        ("run", lambda: sandbox_tools.sandbox_run("npm ci", shell="powershell", timeout_s=60)),
        ("launch", lambda: sandbox_tools.sandbox_launch("node", args=["index.js"], cwd="app", name="web",
                                                       ports=["PORT"], env={"A": "1"}, files={"x.txt": "y"})),
        ("look", lambda: sandbox_tools.sandbox_look(pid=5, capture=False)),
        ("http", lambda: sandbox_tools.sandbox_http(8080, method="POST", path="/x", body={"a": 1})),
        ("mcp", lambda: sandbox_tools.sandbox_mcp(command="node", args=["s.js"], list_tools=True)),
        ("stop", lambda: sandbox_tools.sandbox_stop(pid=5)),
        ("clear", lambda: sandbox_tools.sandbox_clear()),
    ]
    for call, fn in calls:
        fn()
        assert called and called[-1][0] == call, (call, called[-1:])
        assert called[-1][1]["conv_id"] == CONV
    names = {c: a.get("name") for c, a in called}
    assert names["clone"] == "repo" and names["launch"] == "web" and names["fetch"] == "a.zip"


def test_workflow_template_verifies_in_a_sandbox():
    from backend.automations.pipeline_templates import BUILTIN_CATEGORIES
    from backend.automations.templates import BUILTIN_TEMPLATES

    tpl = next(t for t in BUILTIN_TEMPLATES if t["id"] == "builtin:sandbox-verify")
    agent = next(n for n in tpl["graph"]["nodes"] if n["type"] == "pipeline.agent")
    assert "sandbox" in agent["config"]["prompt"] and "deploy nothing" in agent["config"]["prompt"]
    assert BUILTIN_CATEGORIES["builtin:sandbox-verify"] == "Development"


def test_a_timed_out_run_has_what_it_left_running_stopped(monkeypatch):
    # Oct 31 2026: a headless Edge screenshot never returned; the terminal stayed busy and the
    # next sandbox_run failed until the agent found and closed it.
    import types

    from frontend.ui_web.terminal import session as session_module
    from frontend.ui_web.terminal.session import TerminalSession

    killed: list[int] = []
    monkeypatch.setattr(session_module, "kill_process_tree", lambda pid: killed.append(pid))
    shell = TerminalSession(shell="bash", cwd=".")
    shell._timed_out = {4242: "bash.exe", 4343: "msedge.exe"}
    shell._descendants = lambda snapshot=None: {4242: "bash.exe", 5555: "node.exe"}  # msedge already gone
    mgr = types.SimpleNamespace(get_session=lambda sid: shell if sid == "s1" else None)
    out = service._stop_leftovers(mgr, "s1", {"ok": False, "error": "command timed out", "output_tail": "FORM OK"})
    assert killed == [4242]
    assert out["stopped"] == "bash.exe" and out["output_tail"] == "FORM OK" and "timeout 60" in out["hint"]
    assert shell.left_running() == ""
    assert "stopped" not in service._stop_leftovers(mgr, "s1", {"ok": False, "error": "command timed out"})
