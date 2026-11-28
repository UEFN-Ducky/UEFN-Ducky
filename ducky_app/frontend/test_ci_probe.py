import json

from frontend import ci_probe


def test_osascript_refused_gets_the_settings_message():
    got = ci_probe.osascript_result(1, "", "execution error: Not authorized to send Apple events to System Events. (-1743)")
    assert got == {"ok": False, "message": ci_probe.AUTOMATION_NOTE}
    assert "Privacy & Security" in got["message"]


def test_osascript_ok_and_other_errors():
    assert ci_probe.osascript_result(0, "Finder\n", "") == {"ok": True, "result": "Finder"}
    assert ci_probe.osascript_result(1, "", "syntax error")["error"] == "syntax error"


def test_tcc_writes_every_probe_even_when_one_crashes(tmp_path, monkeypatch):
    monkeypatch.setattr(ci_probe, "_mic", lambda: {"ok": False, "message": ci_probe.MIC_NOTE})
    monkeypatch.setattr(ci_probe, "_snip", lambda: 1 / 0)
    monkeypatch.setattr(ci_probe, "_osascript", lambda: {"ok": True, "result": "Finder"})
    out = tmp_path / "tcc.json"
    assert ci_probe.tcc(str(out)) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["mic"]["message"] == ci_probe.MIC_NOTE
    assert report["snip"]["error"].startswith("ZeroDivisionError")
    assert report["osascript"]["ok"] is True


def _fake_updater(monkeypatch, applied):
    from frontend import updater

    status = {"local_version": "1.2.534", "remote_version": "99.0.0", "update_available": True,
              "feed_status": "update_available", "error": None, "last_failure": None, "asset": {"url": "x"}}
    monkeypatch.setattr(updater, "linux_update_status", lambda kind=None: status)
    monkeypatch.setattr(updater, "apply_update", lambda: applied.append(True) or {"ok": True, "error": None, "stage": "restarting"})


def test_update_probe_reports_status_without_applying(tmp_path, monkeypatch):
    applied = []
    _fake_updater(monkeypatch, applied)
    monkeypatch.delenv(ci_probe.UPDATE_APPLY_ENV, raising=False)
    out = tmp_path / "update.json"
    assert ci_probe.update(str(out)) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["local_version"] == "1.2.534" and report["remote_version"] == "99.0.0"
    assert "apply" not in report and "asset" not in report and applied == []


def test_update_probe_applies_only_on_a_ci_runner(tmp_path, monkeypatch):
    applied = []
    _fake_updater(monkeypatch, applied)
    monkeypatch.setenv(ci_probe.UPDATE_APPLY_ENV, "1")
    out = tmp_path / "update.json"
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert ci_probe.update(str(out)) == 1
    assert applied == [] and "CI runner" in json.loads(out.read_text(encoding="utf-8"))["apply"]["error"]
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("DUCKY_SANDBOX", "1")
    assert ci_probe.update(str(out)) == 0
    assert applied == [True] and json.loads(out.read_text(encoding="utf-8"))["apply"]["ok"] is True
