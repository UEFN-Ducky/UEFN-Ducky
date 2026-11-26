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
