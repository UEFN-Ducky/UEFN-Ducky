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


def test_register_starts_background_work_only_in_the_app(tmp_path: Path) -> None:
    from backend.uefn_plugins.plugin_lint import register_background_issues

    _write(tmp_path, "backend/__init__.py", (
        "import threading\n"
        "def register(api):\n"
        "    threading.Thread(target=lambda: None).start()\n"      # warns: every process
        "    if api.is_app():\n"
        "        threading.Timer(5, lambda: None).start()\n"       # fine: app only
        "    def later():\n"
        "        threading.Thread(target=lambda: None).start()\n"  # fine: runs when called\n"
        "    api.tool()(later)\n"
    ))
    _write(tmp_path, "backend/guarded.py", (
        "import socket\n"
        "def register(api):\n"
        "    if not api.is_app():\n"
        "        return\n"
        "    socket.socket().bind(('127.0.0.1', 0))\n"            # fine: after the early return
    ))
    issues = register_background_issues(tmp_path)
    assert issues == ["backend/__init__.py:3: register() starts Thread outside `if api.is_app()`; "
                      "it will run once per agent process."]


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


def test_code_and_styles_from_the_internet_are_rejected_vendored_files_pass(tmp_path: Path) -> None:
    _write(tmp_path, "ui/index.html", (
        '<!doctype html><script src="https://cdn.tailwindcss.com"></script>\n'
        '<script src="https://unpkg.com/lucide@0.460.0"></script>\n'
        '<link rel="stylesheet" href="https://fonts.example.com/inter.css">\n'
        '<link rel="preconnect" href="https://fonts.example.com">\n'
        '<script src="vendor/three.min.js"></script><script src="app.js"></script>\n'
        "<style>button:focus-visible { outline: 2px solid var(--border-focus); }</style>"
    ))
    _write(tmp_path, "ui/app.js", (
        'import { a } from "./local.js";\n'
        'import * as _ from "https://esm.sh/lodash";\n'
        'const lib = await import("https://cdn.jsdelivr.net/npm/x");\n'
        'const api = "https://api.example.com/data";\n'
    ))
    text = "\n".join(check_ui(tmp_path, PANEL))
    assert "ui/index.html:1: <script src> from cdn.tailwindcss.com." in text
    assert "ui/index.html:2: <script src> from unpkg.com." in text
    assert "ui/index.html:3: stylesheet from fonts.example.com." in text
    assert "ui/app.js:2: imports code from https://esm.sh/lodash." in text
    assert "ui/app.js:3: imports code from https://cdn.jsdelivr.net/npm/x." in text
    assert "ui/vendor/" in text  # the fix is named
    for fine in (":4:", ":5:", "local.js", "api.example.com"):
        assert fine not in text.replace("ui/app.js:4", ""), fine


def test_reloading_modules_is_rejected_because_compiled_builds_cannot(tmp_path: Path) -> None:
    _write(tmp_path, "backend/__init__.py", (
        "import importlib\nfrom . import defaults\n\n"
        "def register(api):\n    importlib.reload(defaults)\n"
    ))
    text = "\n".join(check_backend(tmp_path, {}, "demo"))
    assert "backend/__init__.py:5: importlib.reload() fails in the compiled build" in text


def test_data_sharing_and_data_locks_must_say_something_valid() -> None:
    from backend.uefn_plugins.plugin_lint import check_manifest
    from backend.uefn_plugins.store import data_locks

    good = {"data_sharing": True, "data_locks": {"scene.": "required", "card.": "auto", "": "auto"}}
    assert check_manifest(good) == [] and check_manifest({}) == []
    assert data_locks(good) == {"scene.": "required", "card.": "auto", "": "auto"}
    text = "\n".join(check_manifest({"data_sharing": "yes", "data_locks": {
        "Scene.": "auto", "card.": "always", "art/": "required"}}))
    assert "\"data_sharing\" is 'yes'" in text
    assert "key 'Scene.' isn't the start of a doc key or file path" in text
    assert "'card.' is 'always': use \"auto\", \"required\" or \"none\"" in text
    assert "'art/'" not in text  # a file path prefix
    assert len(text.splitlines()) == 3
    assert check_manifest({"data_locks": ["scene."]}) == [
        'plugin.json "data_locks" must be an object: {"<doc key or file path prefix>": "auto", "required" or "none"}.']
    assert check_manifest({"data_locks": {"log.": "none"}}) == []
    both = check_manifest({"data_sharing": False, "data_locks": {"scene.": "auto"}})
    assert both == ['plugin.json has "data_locks" but "data_sharing": false: nothing is shared, so remove one.']
    assert data_locks({"data_sharing": False, "data_locks": {"scene.": "auto"}}) == {}
    assert data_locks({"data_locks": {"Scene.": "auto", "card.": "always", "pack.": "auto"}}) == {"pack.": "auto"}


def test_data_local_lists_prefixes_of_this_pcs_own_items() -> None:
    from backend.uefn_plugins.plugin_lint import check_manifest
    from backend.uefn_plugins.store import data_local

    assert check_manifest({"data_local": ["qa.", "device/"]}) == []
    assert data_local({"data_local": ["qa.", "device/", "qa."]}) == ("device/", "qa.")
    assert check_manifest({"data_local": "qa."}) == [
        'plugin.json "data_local" must be a list of doc key or file path prefixes: ["qa.", "device/"].']
    text = "\n".join(check_manifest({"data_local": ["QA.", "", 7, "ok."]}))
    assert "entry 'QA.' isn't the start" in text and "entry ''" in text and "entry 7" in text
    assert len(text.splitlines()) == 3
    assert data_local({"data_local": ["QA.", "", 7, "ok."]}) == ("ok.",)
    assert check_manifest({"data_sharing": False, "data_local": ["qa."]}) == [
        'plugin.json has "data_local" but "data_sharing": false: all of it is Local already, so remove one.']


def test_a_plugin_with_bad_data_locks_does_not_install() -> None:
    import io
    import zipfile

    from backend.uefn_plugins import store

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plugin.json", json.dumps({"id": "lockdemo", "data_locks": {"scene.": "always"}}))
    out = store.import_plugin_from_bytes(buf.getvalue(), source="local")
    assert out == {"ok": False, "error": 'plugin.json "data_locks" \'scene.\' is \'always\': use "auto", "required" or "none".'}
    assert not store.plugin_dir("lockdemo").exists()


FORGE_FOLDER = {"id": "scenes", "root": "{LOCALAPPDATA}/UEFN-Ducky/forge/scenes", "unit": "*", "lock": "unit",
                "last": ["scene.json"], "ignore": ["*/history.json", "*/history-index.json", "*/history-snapshots/**",
                                                   "**/*.tmp"], "after_pull": "reload"}


def test_team_folders_need_a_root_the_plugin_owns_and_a_known_unit() -> None:
    from backend.uefn_plugins.plugin_lint import check_manifest
    from backend.uefn_plugins.team_folders import declarations

    rogue = [{"id": "design", "root": "{PROJECT}/.ducky/roguelike-design", "unit": ".", "lock": "unit",
              "last": ["store.json"], "ignore": ["store.prev.json", "**/*.tmp"], "after_pull": "reload"},
             {"id": "noproject", "root": "{LOCALAPPDATA}/UEFN-Ducky/roguelike_design_data", "unit": "*",
              "lock": "unit", "last": ["store.json"], "ignore": ["*/store.prev.json", "**/*.tmp"],
              "after_pull": "reload"}]
    cards = {"id": "cards", "root": "{LOCALAPPDATA}/UEFN-Ducky/brainrot_tcg", "unit": "file", "lock": "unit",
             "ignore": ["backups/**", "**/*.tmp"], "after_pull": "reload", "on_locked": "quarantine",
             "merge": ["*.json"]}
    for good in ([FORGE_FOLDER], rogue, [cards]):
        assert check_manifest({"team_folders": good}) == []
    assert [f.id for f in declarations({"team_folders": rogue})] == ["design", "noproject"]

    def errors(**entry: object) -> str:
        return "\n".join(check_manifest({"team_folders": [{**FORGE_FOLDER, **entry}]}))

    assert 'must start with {LOCALAPPDATA}/UEFN-Ducky/ or {PROJECT}/' in errors(root="C:/Users/me/scenes")
    assert 'must start with' in errors(root="{PROJECT}")
    assert "must be inside {LOCALAPPDATA}/UEFN-Ducky/" in errors(root="{LOCALAPPDATA}/Epic/scenes")
    assert "must be inside {LOCALAPPDATA}/UEFN-Ducky/" in errors(root="{LOCALAPPDATA}/UEFN-Ducky")
    assert "one of the app's own folders" in errors(root="{LOCALAPPDATA}/UEFN-Ducky/scopes/x")
    assert "has a bad part '..'" in errors(root="{PROJECT}/../elsewhere")
    assert "has a bad part 'a:b'" in errors(root="{PROJECT}/a:b")
    assert '"unit" is \'dir\': use "*" (each top-level folder or file), "." (the whole folder) or "file"' in errors(
        unit="dir")
    assert '"lock" is \'always\': use "unit" or "none".' in errors(lock="always")
    assert '"after_pull" is \'restart\': use "notify" or "reload".' in errors(after_pull="restart")
    assert '"on_locked" is \'block\'' in errors(on_locked="block")
    assert "isn't a relative glob" in errors(ignore=["/abs/**"])
    assert "isn't a relative glob" in errors(last=["../scene.json"])
    assert 'must be a list of globs' in errors(merge="*.json")
    assert "has unknown 'mode'" in errors(mode="sync")
    assert '"id" must be 1-32' in errors(id="Scenes!")
    assert check_manifest({"team_folders": {"id": "x"}}) == [
        'plugin.json "team_folders" must be a list of folders: [{"id": "scenes", "root": '
        '"{LOCALAPPDATA}/UEFN-Ducky/<plugin>/scenes", "unit": "*"}].']
    two = check_manifest({"team_folders": [FORGE_FOLDER, {**FORGE_FOLDER, "root": "{LOCALAPPDATA}/UEFN-Ducky/x"}]})
    assert two == ['plugin.json "team_folders" has two folders with "id" \'scenes\': give each its own.']
    nested = check_manifest({"team_folders": [FORGE_FOLDER, {**FORGE_FOLDER, "id": "inner",
                                                              "root": "{LOCALAPPDATA}/UEFN-Ducky/forge/scenes/a"}]})
    assert nested == ['plugin.json "team_folders" \'inner\' and \'scenes\' overlap: one root is inside the other. '
                      'Declare each folder once.']
    off = check_manifest({"data_sharing": False, "team_folders": [FORGE_FOLDER]})
    assert off == ['plugin.json has "team_folders" but "data_sharing": false: nothing is shared, so remove one.']
    assert declarations({"data_sharing": False, "team_folders": [FORGE_FOLDER]}) == []
    assert declarations({"team_folders": [FORGE_FOLDER, {**FORGE_FOLDER, "id": "b", "unit": "dir"}]})[0].id == "scenes"


def test_a_plugin_with_bad_team_folders_does_not_install() -> None:
    import io
    import zipfile

    from backend.uefn_plugins import store

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plugin.json", json.dumps({"id": "folderdemo", "team_folders": [{**FORGE_FOLDER, "unit": "dir"}]}))
    out = store.import_plugin_from_bytes(buf.getvalue(), source="local")
    assert out["ok"] is False and '"unit" is \'dir\'' in out["error"]
    assert not store.plugin_dir("folderdemo").exists()


def test_a_plugin_ready_for_teams_says_whether_it_shares_and_keeps_its_files_shareable(tmp_path: Path) -> None:
    from backend.uefn_plugins.plugin_lint import check_store_ready

    _write(tmp_path, "backend/__init__.py", (
        "import shutil, sqlite3\n"
        "from pathlib import Path\n"
        "def register(api):\n"
        "    open('a.json')\n"
        "    open('b.json', 'w').write('{}')\n"
        "    Path('c.json').open(mode='a')\n"
        "    Path('d.txt').write_text('x')\n"
        "    shutil.copy2('e', 'f')\n"
        "    sqlite3.connect('g.db')\n"
        "    import os; os.open('h', 1)\n"
    ))
    _write(tmp_path, "backend/test_x.py", "open('t', 'w')\n")  # tests never ship
    errors, _ = check_store_ready(tmp_path, {"data_sharing": False}, "demo")
    assert len(errors) == 1 and errors[0].startswith("No Store icon") and "ducky_plugin_set_icon" in errors[0]
    _write(tmp_path, "assets/icon.svg", "<svg xmlns='http://www.w3.org/2000/svg'/>")
    errors, warnings = check_store_ready(tmp_path, {}, "demo")
    assert len(errors) == 1 and 'no "data_sharing"' in errors[0] and "Store build refuses" in errors[0]
    # Reads, os.open and tests aren't the plugin keeping files of its own.
    assert len(warnings) == 1 and warnings[0].startswith(
        "backend/__init__.py:5, backend/__init__.py:6, backend/__init__.py:7, backend/__init__.py:8, "
        "backend/__init__.py:9: writes files of its own")
    # Not shared, or shared through a team folder on this PC: ready.
    assert check_store_ready(tmp_path, {"data_sharing": False}, "demo") == ([], [])
    assert check_store_ready(tmp_path, {"data_sharing": True, "team_folders": [FORGE_FOLDER]}, "demo") == ([], [])
    # A team folder inside the island never reaches a team.
    island = {**FORGE_FOLDER, "root": "{PROJECT}/.ducky/demo", "unit": "."}
    errors, _ = check_store_ready(tmp_path, {"data_sharing": True, "team_folders": [island]}, "demo")
    assert len(errors) == 1 and "inside the island" in errors[0] and "{LOCALAPPDATA}/UEFN-Ducky/demo/" in errors[0]


def test_the_store_icon_is_one_the_store_takes_and_set_icon_makes_one(tmp_path: Path, monkeypatch) -> None:
    from PIL import Image

    from backend.tools.panel import panel_ai_plugins
    from backend.uefn_plugins.plugin_lint import store_icon_problem

    _write(tmp_path, "assets/icon.png", "not a png")
    assert "isn't a real PNG" in store_icon_problem(tmp_path)
    (tmp_path / "assets/icon.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * (700 * 1024))
    assert "the Store takes an icon up to 700 KB" in store_icon_problem(tmp_path)

    draft = tmp_path / "drafts" / "demo"
    _write(draft, "plugin.json", "{}")
    monkeypatch.setattr(panel_ai_plugins, "_draft_root", lambda pid: draft)
    wide = tmp_path / "wide.jpg"
    Image.new("RGB", (1536, 1024), (200, 40, 40)).save(wide, "JPEG")
    out = panel_ai_plugins.set_ai_plugin_icon("demo", str(wide))
    assert out["ok"] and out["icon"] == "assets/icon.png" and out["store_ready"] is True
    with Image.open(draft / "assets/icon.png") as icon:
        assert icon.size == (512, 512) and icon.getpixel((0, 0))[3] == 0  # fitted, transparent around it
    assert not panel_ai_plugins.set_ai_plugin_icon("demo", str(tmp_path / "missing.png"))["ok"]
    # An SVG icon the Store picks first is never hidden behind a new PNG.
    _write(draft, "assets/icon.svg", "<svg xmlns='http://www.w3.org/2000/svg'/>")
    out = panel_ai_plugins.set_ai_plugin_icon("demo", str(wide))
    assert not out["ok"] and "assets/icon.svg is the Store icon" in out["error"]
