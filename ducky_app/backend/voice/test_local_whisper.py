import hashlib
import io
import zipfile

from backend.voice import local_whisper as whisper


def test_interrupted_install_can_retry_without_partial_model(tmp_path, monkeypatch):
    monkeypatch.setattr(whisper.sys, "platform", "win32")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        for name in whisper._REQUIRED:
            package.writestr("Release/" + name, b"runtime")

    model = b"test model"
    runtime = archive.getvalue()
    responses = [runtime, b"truncated", runtime, model]
    monkeypatch.setattr(whisper, "_folder", lambda: tmp_path / "installed")
    monkeypatch.setattr(whisper, "_RUNTIME_BYTES", len(runtime))
    monkeypatch.setattr(whisper, "_RUNTIME_SHA", hashlib.sha256(runtime).hexdigest())
    monkeypatch.setattr(whisper, "_MODEL_BYTES", len(model))
    monkeypatch.setattr(whisper, "_MODEL_SHA", hashlib.sha256(model).hexdigest())
    monkeypatch.setattr(whisper.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(responses.pop(0)))
    assert not whisper.installed()
    assert not whisper.install()["ok"]
    assert not whisper.installed()
    assert whisper.install()["ok"]
    assert whisper.installed()
    assert whisper.install()["ok"]  # No network on repeat.
    assert not responses
