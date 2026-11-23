"""Linux terminal guards: the login shell's PATH, an agent terminal's limits, no focus lock."""

import os
import shutil
import subprocess
import sys

import pytest

if sys.platform == "win32":
    pytest.skip("Linux guards", allow_module_level=True)

from frontend.ui_web.terminal import focus_guard, path_env, resource_guard  # noqa: E402


@pytest.fixture
def fresh_path(monkeypatch):
    monkeypatch.setattr(path_env, "_login_path", None)


def _fake_login_shell(tmp_path, nvm_bin):
    calls = tmp_path / "calls"
    shell = tmp_path / "fakebash"
    # An rc file that prints a greeting, like many do; PATH gains nvm's bin.
    shell.write_text(
        "#!/bin/sh\n"
        f"echo x >> {calls}\n"
        "echo 'Welcome back!'\n"
        f'PATH="{nvm_bin}:$PATH"; export PATH\n'
        'shift; eval "$1"\n'
    )
    shell.chmod(0o755)
    return shell, calls


def test_path_comes_from_the_login_shell_once(tmp_path, fresh_path):
    nvm_bin = tmp_path / ".nvm" / "versions" / "node" / "v22" / "bin"
    nvm_bin.mkdir(parents=True)
    node = nvm_bin / "node"
    node.write_text("#!/bin/sh\n")
    node.chmod(0o755)
    shell, calls = _fake_login_shell(tmp_path, nvm_bin)
    value = path_env.posix_login_path(str(shell))
    assert value.split(os.pathsep)[0] == str(nvm_bin)
    assert path_env.posix_login_path(str(shell)) == value
    assert calls.read_text().count("x") == 1  # read once
    assert shutil.which("node", path=value) == str(node)


def test_merged_path_puts_the_login_shell_first_without_repeats(monkeypatch):
    monkeypatch.setattr(path_env, "posix_login_path", lambda: "/home/me/.nvm/bin:/usr/bin")
    monkeypatch.setenv("PATH", "/usr/bin:/snap/bin")
    assert path_env.windows_merged_path() == "/home/me/.nvm/bin:/usr/bin:/snap/bin"
    assert path_env.env_with_fresh_path({})["PATH"].startswith("/home/me/.nvm/bin:/usr/bin:/snap/bin")


def test_a_broken_login_shell_keeps_the_process_path(monkeypatch, fresh_path):
    assert path_env.posix_login_path("/nonexistent/shell") == ""
    monkeypatch.setenv("PATH", "/usr/bin")
    assert path_env.windows_merged_path() == "/usr/bin"


def test_an_agent_terminals_children_get_a_memory_cap_and_lower_priority():
    probe = "import os, resource; print(resource.getrlimit(resource.RLIMIT_DATA)[0], os.nice(0))"
    out = subprocess.run([sys.executable, "-c", probe], preexec_fn=resource_guard.posix_child_limits(512),
                         capture_output=True, text=True, check=True).stdout.split()
    assert int(out[0]) == 512 * 1024 * 1024
    assert int(out[1]) >= os.nice(0) + 10 or int(out[1]) == 19


def test_an_agent_terminal_shell_is_capped(tmp_path, monkeypatch):
    pytest.importorskip("ptyprocess")
    from frontend.ui_web.terminal.session import TerminalSession

    monkeypatch.setattr(resource_guard, "new_job_cap_mb", lambda: 2048)
    s = TerminalSession(shell="bash", cwd=str(tmp_path), agent=True)
    s.spawn()
    try:
        assert s.run_command("ulimit -d", timeout_s=20)["output_tail"] == str(2048 * 1024)
    finally:
        s.kill()


def test_the_focus_guard_does_nothing_on_linux():
    assert focus_guard._set_locked(True) is False
    with focus_guard.no_focus_steal():
        assert focus_guard._holders == 1
    assert focus_guard._holders == 0
