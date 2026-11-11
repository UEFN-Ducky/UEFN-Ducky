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


def test_the_persons_default_maker_comes_first_and_the_picker_lists_every_one(monkeypatch):
    _keys(monkeypatch, {"b_key": "sk-1"})
    services.register_media_generator("alpha", "video", lambda p, **o: {}, {"label": "Alpha"})
    services.register_media_generator("beta", "video", lambda p, **o: {}, {"label": "Beta", "secret_key": "b_key"})
    services.register_media_generator("gamma", "video", lambda p, **o: {}, {"secret_key": "g_key"})
    monkeypatch.setattr(services, "default_media_plugin", lambda kind: "")
    assert [r["plugin_id"] for r in services.media_generators("video")] == ["alpha", "beta"]
    assert services.media_makers("video") == [
        {"id": "alpha", "label": "Alpha", "ready": True},
        {"id": "beta", "label": "Beta", "ready": True},
        {"id": "gamma", "label": "gamma", "ready": False},
    ]
    # Settings > LLMs > Default models: callers take the first that answers.
    monkeypatch.setattr(services, "default_media_plugin", lambda kind: "beta" if kind == "video" else "")
    assert [r["plugin_id"] for r in services.media_generators("video")] == ["beta", "alpha"]
    # A default that is not set up never reaches a caller.
    monkeypatch.setattr(services, "default_media_plugin", lambda kind: "gamma")
    assert [r["plugin_id"] for r in services.media_generators("video")] == ["alpha", "beta"]
    assert services.media_makers("video")[0] == {"id": "gamma", "label": "gamma", "ready": False}


def test_the_default_maker_is_read_from_its_own_setting(monkeypatch):
    from types import SimpleNamespace

    from frontend.settings import PanelSettings

    saved = SimpleNamespace(default_image_provider="account", default_video_provider=" higgsfield ", default_music_provider="")
    monkeypatch.setattr(PanelSettings, "load", classmethod(lambda cls: saved))
    assert services.default_media_plugin("video") == "higgsfield"
    assert services.default_media_plugin("image") == services.default_media_plugin("image_edit") == "account"
    assert services.default_media_plugin("music") == ""
    assert services.default_media_plugin("mesh") == ""
