import pytest

from backend.uefn_plugins.plugin_host import services
from backend.uefn_plugins.plugin_host.testing import patch_host


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    patch_host(monkeypatch, "_MEDIA_GENERATORS", {})
    patch_host(monkeypatch, "is_plugin_enabled", lambda pid: True)


def _keys(monkeypatch, saved: dict):
    monkeypatch.setattr("backend.agent.secrets.get_key", lambda name: saved.get(name))


def test_fake_video_generator_listed_with_key_hidden_without(monkeypatch):
    fake = lambda prompt, **o: {"path": "x.mp4", "mime": "video/mp4"}  # noqa: E731
    services.register_media_generator("fakevid", "video", fake, {"label": "Fake", "secret_key": "fakevid_key"})

    _keys(monkeypatch, {})
    assert services.media_generators("video") == []

    _keys(monkeypatch, {"fakevid_key": "  "})
    assert services.media_generators("video") == []

    _keys(monkeypatch, {"fakevid_key": "sk-1"})
    rows = services.media_generators("video")
    assert [r["plugin_id"] for r in rows] == ["fakevid"]
    assert rows[0]["label"] == "Fake" and rows[0]["fn"] is fake
    assert services.media_generators("image") == []


def test_keyless_listed_and_bad_input_rejected(monkeypatch):
    _keys(monkeypatch, {})
    services.register_media_generator("freemusic", "music", lambda p, **o: {}, {"label": "Free"})
    assert len(services.media_generators("music")) == 1
    with pytest.raises(ValueError):
        services.register_media_generator("freemusic", "audio", lambda p, **o: {})
    with pytest.raises(TypeError):
        services.register_media_generator("freemusic", "video", "nope")


def test_disabled_plugin_hidden(monkeypatch):
    _keys(monkeypatch, {})
    services.register_media_generator("fakevid", "video", lambda p, **o: {})
    patch_host(monkeypatch, "is_plugin_enabled", lambda pid: False)
    assert services.media_generators("video") == []
