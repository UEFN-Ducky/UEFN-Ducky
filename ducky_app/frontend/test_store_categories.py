"""Browse categories inferred from plugin contributes.

Only real tags are emitted (``themes`` / ``gateways``) — the Browse UI hides the
redundant "plugins" bucket, so functional-only plugins get no tags.
"""

from frontend.duckyos_account import _plugin_browse_categories


def test_theme_only_appearance_plugin():
    cats = _plugin_browse_categories(
        {
            "contributes": {
                "appearance.profiles": [{"id": "x"}],
                "appearance.skin": [{"id": "x"}],
            }
        }
    )
    assert cats == ["themes"]


def test_functional_plugin_has_no_browse_tags():
    cats = _plugin_browse_categories(
        {"contributes": {"settings.tabs": [{"id": "Discord"}]}}
    )
    assert cats == []


def test_theme_and_plugin():
    cats = _plugin_browse_categories(
        {
            "contributes": {
                "appearance.profiles": [{"id": "x"}],
                "settings.tabs": [{"id": "Tools"}],
            }
        }
    )
    assert cats == ["themes"]


def test_gateway_plugin():
    cats = _plugin_browse_categories(
        {"contributes": {"llm.providers": [{"id": "ollama", "label": "Ollama"}]}}
    )
    assert cats == ["gateways"]
