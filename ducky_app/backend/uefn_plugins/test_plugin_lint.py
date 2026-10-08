"""The AI plugin rules ``ducky_plugin_validate`` enforces, each with a fixable message."""

from __future__ import annotations

import json
from pathlib import Path

from backend.uefn_plugins.plugin_lint import check_backend, check_skill, check_ui, lint_draft

PANEL = {"contributes": {"ui.panels": [{"id": "main", "entry": "ui/index.html"}]}}


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_color_literals_are_rejected_in_css_html_and_js_but_not_where_they_mean_something_else(tmp_path: Path) -> None:
    _write(tmp_path, "ui/app.css", (
        "#main .red, a[href='#fff'] { color: #fff; background: url(img/#abc.png); }\n"
        ".x { border: 1px solid rgba(0, 0, 0, .5); grid-area: red; }\n"
        ".y { color: tomato; outline: 1px solid transparent; fill: currentColor; }\n"
        "/* color: #123456 in a comment */\n"
    ))
    _write(tmp_path, "ui/index.html", (
        "<!doctype html><style>button:focus-visible { outline: 2px solid var(--border-focus); }</style>"
        "<div style=\"color: hsl(0 0% 100%)\"></div><svg><rect fill=\"black\"/></svg>"
        "<script>el.style.color = 'white'; ctx.fillStyle = \"#00ff00\"; document.querySelector('#add');</script>"
    ))
    issues = check_ui(tmp_path, PANEL)
    text = "\n".join(issues)
    for found in ("'#fff'", "'rgba()'", "'tomato'", "'hsl()'", "'black'", "'white'", "'#00ff00'"):
        assert found in text, found
    for not_a_color in ("'#abc'", "'red'", "'transparent'", "'#123456'", "'#add'", "'#main'"):
        assert not_a_color not in text, not_a_color
    assert all("Appearance variable" in i for i in issues)
    assert "ui/app.css:1:" in text and "ui/app.css:3:" in text


def test_only_appearance_variables_or_the_plugins_own(tmp_path: Path) -> None:
    _write(tmp_path, "ui/app.css", (
        ":root { --gap: 8px; }\n"
        "body { color: var(--fg); padding: var(--gap); font-family: var(--font-family); }\n"
    ))
    issues = check_ui(tmp_path, {})
    assert len(issues) == 1
    assert "var(--font-family) isn't an Appearance variable" in issues[0]
    assert "Did you mean var(--font-" in issues[0]


def test_themes_and_vendored_files_are_exempt_and_panels_need_a_focus_style(tmp_path: Path) -> None:
    _write(tmp_path, "ui/theme.css", ":root { --accent: #0ff; }\n")
    _write(tmp_path, "ui/vendor/chart.js", "const c = 'rgb(1, 2, 3)';\n")
    _write(tmp_path, "ui/lib.min.js", "x.style.color = '#fff';\n")
    _write(tmp_path, "ui/index.html", "<!doctype html><button>Go</button>")
    manifest = {"contributes": {**PANEL["contributes"], "appearance.css": [{"entry": "ui/theme.css"}]}}
    issues = check_ui(tmp_path, manifest)
    assert len(issues) == 1 and "focus-visible" in issues[0] and "_kit/ducky.js" in issues[0]
    _write(tmp_path, "ui/index.html", '<!doctype html><script src="../../_kit/ducky.js"></script><button>Go</button>')
    assert check_ui(tmp_path, manifest) == []


def test_backend_rules_name_the_fix(tmp_path: Path) -> None:
    _write(tmp_path, "backend/__init__.py", (
        "import inspect, runpy\n"
        "from pathlib import Path\n"
        "def register(api):\n"
        "    @api.tool()\n"
        "    def demo_list(kind: str = '') -> dict:\n"
        "        return {'ok': True}\n"
        "    api.register_panel_rpc('list', lambda params=None: demo_list())\n"
        "    api.register_panel_rpc('delete', lambda params=None: {'ok': True})\n"
        "    src = Path(__file__).read_text()\n"
        "    data = (Path(__file__).parent / 'data.json').read_text()\n"
        "    helper = open('helpers.py').read()\n"
        "    runpy.run_path('x.py')\n"
        "    inspect.getsource(register)\n"
    ))
    manifest = {"contributes": {"automations": {"nodes": [{"id": "demo.run"}], "templates": []}}}
    issues = check_backend(tmp_path, manifest, "demo")
    text = "\n".join(issues)
    assert "Panel RPC 'delete' has no MCP tool. Add @api.tool() def demo_delete" in text
    assert "'list'" not in text
    assert "Workflow node 'demo.run' is declared but nothing handles it" in text
    assert "Add a workflow template that uses your node" in text
    assert "backend/__init__.py:9: reads a .py file (its own source)" in text
    assert "backend/__init__.py:10:" not in text  # data next to the module is fine
    assert "backend/__init__.py:11: reads a .py file." in text
    assert "backend/__init__.py:12: loads a .py file by path (run_path)" in text
    assert "backend/__init__.py:13: reads Python source (getsource)" in text
    assert check_skill(tmp_path, "demo") and "skills/demo/SKILL.md" in check_skill(tmp_path, "demo")[0]


def test_the_scaffold_shape_passes(tmp_path: Path) -> None:
    from backend.tools.panel.panel_ai_plugins import _minimal_manifest, _register_stub, _skill_stub

    manifest = _minimal_manifest("demo", "Demo", "")
    _write(tmp_path, "plugin.json", json.dumps(manifest))
    _write(tmp_path, "backend/__init__.py", _register_stub("demo"))
    _write(tmp_path, "skills/demo/SKILL.md", _skill_stub("demo", "Demo"))
    assert lint_draft(tmp_path, manifest, "demo") == []
