# One EXE: GUI + ``bridge`` MCP stdio. Run: py build/build_exes.py
import os
import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

# SPECPATH[0] is the directory containing this file (PyInstaller).
_spec_root = Path(SPECPATH[0]).resolve()  # type: ignore[name-defined]
ROOT = _spec_root if (_spec_root / "ducky_app" / "frontend" / "launcher.py").is_file() else _spec_root.parent
DUCKY_APP = ROOT / "ducky_app"
FRONTEND = DUCKY_APP / "frontend"

# collect_submodules runs at spec parse time — before Analysis pathex. Without
# ducky_app on sys.path it can miss `backend.*`, and Store gateway plugins that
# import host helpers (cli_shared / cli_pty / proc_exec) then fail to register.
import sys

for _p in (str(ROOT), str(DUCKY_APP)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

backend_hidden = collect_submodules("backend")
mcp_hidden = collect_submodules("mcp")

# Always ship coding-agent host helpers used only via Store plugins (no core import).
_PLUGIN_HOST_CODING = [
    "backend.agent.coding_agents.cli_shared",
    "backend.agent.coding_agents.cli_pty",
    "backend.agent.coding_agents.proc_exec",
    "backend.agent.coding_agents.mcp_inject",
    "backend.agent.coding_agents.settings_helpers",
]
for _mod in _PLUGIN_HOST_CODING:
    if _mod not in backend_hidden:
        backend_hidden.append(_mod)

# Keep the one-file EXE lean — block heavy optional deps PyInstaller may trace from the global env.
_HEAVY_EXCLUDES = [
    "torch",
    "torchvision",
    "torchaudio",
    "tensorflow",
    "keras",
    "transformers",
    "datasets",
    "sklearn",
    "scipy",
    "pandas",
    "matplotlib",
    "boto3",
    "botocore",
    "s3transfer",
    "pytest",
    "_pytest",
    "py",
    "imageio",
    "pyarrow",
    "fsspec",
    "numba",
    "cv2",
    "IPython",
    "notebook",
]

_listener = ROOT / "ducky_app" / "uefn_listener"  # plaintext listener source → bundle/uefn_listener

# PyInstaller ``datas`` tuples are (src, dest_dir): the file is placed *inside* dest_dir under its
# basename. Using ``bundle/uefn_listener/listener/foo.pyc`` as dest_dir wrongly creates
# ``listener/foo.pyc/foo.pyc`` in the bundle (Deploy then sees ``foo.pyc`` as a directory).
_datas = []
_seen_listener_leaf = set()
if _listener.is_dir():
    for path in _listener.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(_listener)
        # Folders named like *.py (e.g. listener/bootstrap.py/extra.pyc) become directories in the
        # frozen bundle and break Deploy / Unreal — refuse at freeze time.
        for _part in rel.parts[:-1]:
            if _part.endswith((".py", ".pyc")):
                raise RuntimeError(
                    f"unified.spec: invalid listener path (directory named like a module): {_listener / rel}"
                )
        if "__pycache__" in path.parts or path.suffix.lower() == ".pyc":
            continue
        _leaf = "bundle/uefn_listener/" + rel.as_posix()
        if _leaf in _seen_listener_leaf:
            raise RuntimeError(f"unified.spec: duplicate bundle/uefn_listener output: {_leaf}")
        _seen_listener_leaf.add(_leaf)
        _parent = rel.parent.as_posix()
        _dest_dir = "bundle/uefn_listener" if _parent == "." else f"bundle/uefn_listener/{_parent}"
        _datas.append((str(path), _dest_dir))
_frozen = FRONTEND / "frozen_init_unreal.py"
if _frozen.is_file():
    _datas.append((str(_frozen), "frontend"))
_skill = FRONTEND / "uefn_skill.md"
if _skill.is_file():
    _datas.append((str(_skill), "frontend"))
_skill_packs = FRONTEND / "skill_packs"
if _skill_packs.is_dir():
    for path in _skill_packs.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(_skill_packs)
        parent = rel.parent.as_posix()
        dest = (
            "frontend/skill_packs"
            if parent == "."
            else f"frontend/skill_packs/{parent.replace(chr(92), '/')}"
        )
        _datas.append((str(path), dest))
_mcp_plugins = FRONTEND / "mcp_plugins"
if _mcp_plugins.is_dir():
    for path in _mcp_plugins.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(_mcp_plugins)
        parent = rel.parent.as_posix()
        dest = (
            "frontend/mcp_plugins"
            if parent == "."
            else f"frontend/mcp_plugins/{parent.replace(chr(92), '/')}"
        )
        _datas.append((str(path), dest))
# Desktop plugins are Store-only — never pack frontend/uefn_plugins or
# plugins/uefn-plugin-* into the EXE. Users install via Settings → Store.
_bundled_duckies = FRONTEND / "bundled_duckies.json"
if _bundled_duckies.is_file():
    _datas.append((str(_bundled_duckies), "frontend"))
_appearance_builtin_profiles = FRONTEND / "appearance_builtin_profiles.json"
if _appearance_builtin_profiles.is_file():
    _datas.append((str(_appearance_builtin_profiles), "frontend"))
_bundled_agent_profiles = FRONTEND / "bundled_agent_profiles.json"
if _bundled_agent_profiles.is_file():
    _datas.append((str(_bundled_agent_profiles), "frontend"))
_web_dist = FRONTEND / "ui_web" / "web" / "dist"
if (_web_dist / "index.html").is_file():
    _index_text = (_web_dist / "index.html").read_text(encoding="utf-8")
    for _m in re.finditer(r"""(?:src|href)=["'](\./[^"']+)["']""", _index_text):
        _rel = _m.group(1).removeprefix("./")
        _asset = _web_dist / _rel
        if not _asset.is_file():
            raise RuntimeError(
                f"unified.spec: panel index.html references {_rel} but file is missing at {_asset}. "
                "Run: cd ducky_app/frontend/ui_web/web && npm run build"
            )
    for path in _web_dist.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(_web_dist)
        parent = rel.parent.as_posix()
        dest = (
            "frontend/ui_web/web/dist"
            if parent == "."
            else f"frontend/ui_web/web/dist/{parent.replace(chr(92), '/')}"
        )
        _datas.append((str(path), dest))
else:
    raise RuntimeError(
        "unified.spec: React UI dist missing. Run: cd ducky_app/frontend/ui_web/web && npm ci && npm run build"
    )
_app_icon = _spec_root / "app_icon.ico"
_staged_icon = os.environ.get("UEFN_DUCKY_BUILD_ICON", "").strip()
if _staged_icon and Path(_staged_icon).is_file():
    _app_icon = Path(_staged_icon)
if not _app_icon.is_file():
    raise RuntimeError(
        f"unified.spec: app icon missing ({_app_icon}). build_exes.py must write build/app_icon.ico first."
    )
_build_ico = _spec_root / "app_icon.ico"
if _build_ico.is_file():
    _datas.append((str(_build_ico), "frontend"))

for _src, _dest in list(_datas):
    _p = Path(_src)
    if not _p.is_file():
        raise RuntimeError(f"unified.spec: DATA source is not a file: {_src}")

_tz_datas, _tz_bins, _tz_hidden = [], [], []
try:
    from PyInstaller.utils.hooks import collect_all

    _tz_datas, _tz_bins, _tz_hidden = collect_all("tzdata")
except Exception:
    pass

_pty_datas, _pty_bins, _pty_hidden = [], [], []
try:
    from PyInstaller.utils.hooks import collect_all

    # The pip distribution is "pywinpty" but the importable package is "winpty".
    # collect_all("pywinpty") silently collects NOTHING, which drops conpty.dll /
    # OpenConsole.exe / winpty.dll / winpty-agent.exe from the frozen exe — shells
    # then die instantly with STATUS_CONTROL_C_EXIT (shown as exit 130).
    _pty_datas, _pty_bins, _pty_hidden = collect_all("winpty")
except Exception:
    pass
if not _pty_bins and not _pty_datas:
    raise RuntimeError(
        "unified.spec: collect_all('winpty') found no binaries — terminal shells "
        "would break in the frozen exe. Is pywinpty installed in the build env?"
    )

a = Analysis(
    [str(FRONTEND / "launcher.py")],
    pathex=[str(ROOT), str(DUCKY_APP)],
    binaries=_tz_bins + _pty_bins,
    datas=_datas + _tz_datas + _pty_datas,
    hiddenimports=list(backend_hidden)
    + list(mcp_hidden)
    + list(_tz_hidden)
    + list(_pty_hidden)
    + list(_PLUGIN_HOST_CODING)
    + [
        "frontend",
        "frontend.launcher",
        "frontend.ide_apply",
        "frontend.ui_web",
        "frontend.ui_web.panel_api",
        "frontend.appearance_builtin_profiles",
        "frontend.ui_web.agent_modes",
        "frontend.ui_web.webview_app",
        "frontend.ui_web.win_frameless",
        "frontend.ui_web.recent_projects",
        "frontend.ui_web.context_tokens",
        "frontend.ui_web.project_deploy",
        "frontend.ui_web.shutdown",
        "frontend.ui_web.terminal",
        "frontend.ui_web.terminal.manager",
        "frontend.ui_web.terminal.session",
        "frontend.ui_web.terminal.bridge",
        "winpty",
        "pywinpty",
        "frontend.chat_store",
        "frontend.tray_icon",
        "frontend.frozen_process",
        "webview",
        "webview.platforms.edgechromium",
        "anthropic",
        "openai",
        "google.genai",
        "pystray",
        "pystray._win32",
        "PIL",
        "PIL.Image",
        "PIL.ImageDraw",
        "toon_format",
        "toon_format.types",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_HEAVY_EXCLUDES,
    noarchive=False,
)

# Fail the freeze if Store plugin host modules were tree-shaken out (empty LLMs).
# TOC entries are (name, path, typecode).
_pure_names = set()
for _m in a.pure:
    if isinstance(_m, tuple) and _m:
        _pure_names.add(str(_m[0]))
    else:
        _pure_names.add(str(_m))
_missing_host = [m for m in _PLUGIN_HOST_CODING if m not in _pure_names]
if _missing_host:
    raise RuntimeError(
        "unified.spec: frozen build missing Store plugin host modules: "
        + ", ".join(_missing_host)
        + ". Gateways would show as Installed but LLMs would stay empty."
    )

pyz = PYZ(a.pure)

_exe_basename = os.environ.get("UEFN_DUCKY_EXE_BASENAME", "UEFN-Ducky").strip() or "UEFN-Ducky"

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=_exe_basename,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(_app_icon),
)
