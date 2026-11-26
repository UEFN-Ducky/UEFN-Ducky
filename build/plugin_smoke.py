#!/usr/bin/env python3
"""Install Store plugins into a throwaway profile and run each one's register().

    python build/plugin_smoke.py vim kanban hacker [--out smoke.json]
    python build/plugin_smoke.py --self-test

Each id installs from the public Store with the app's own installer
(duckyos_account.store_download_and_install), then the plugin host's loader imports it and
calls register() with the real host api. A plugin whose Store record lists builds but none
for this OS and arch is skipped, not installed. Prints
{"<id>": "ok" | "skipped: no macOS build" | "<Error>: <message>"}, then a
"N ok · M skipped (no macOS build) · K failed" line, and exits 1 only when K > 0. The profile lives in a temp folder (under $RUNNER_TEMP on CI) and
is a test copy (DUCKY_SANDBOX), so nothing is counted or sent.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import platform
import sys
import tempfile
import threading
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTER_TIMEOUT_S = 60.0


def _isolate(profile: Path) -> None:
    """Point every app folder at ``profile`` before the app's modules are imported."""
    profile.mkdir(parents=True, exist_ok=True)
    os.environ["DUCKY_SANDBOX"] = "1"
    os.environ["DUCKY_TESTS_ISOLATED_APPDATA"] = "1"  # app_paths follows LOCALAPPDATA off Windows too
    os.environ["LOCALAPPDATA"] = str(profile)
    os.environ["APPDATA"] = str(profile)
    if sys.platform != "win32":
        os.environ["HOME"] = str(profile)
    sys.path.insert(0, str(ROOT / "ducky_app"))


_OS_LABELS = {"macos": "macOS", "windows": "Windows", "linux": "Linux"}


def host_platform() -> tuple[str, str]:
    """("macos", "aarch64") style, the Store's own `platforms` spelling."""
    os_name = {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux")
    arch = platform.machine().lower()
    arch = {"arm64": "aarch64", "amd64": "x86_64", "x64": "x86_64"}.get(arch, arch)
    return os_name, arch


def skip_reason(item: dict | None, os_name: str, arch: str) -> str | None:
    """Why this Store record can't install here, or None to install it.

    ``platforms`` None (or no record) is a source plugin that runs anywhere."""
    plats = (item or {}).get("platforms")
    if plats is None:
        return None
    label = _OS_LABELS.get(os_name, os_name)
    if not any(str(p).startswith(f"{os_name}-") for p in plats):
        return f"skipped: no {label} build"
    if f"{os_name}-{arch}" not in plats:
        return f"skipped: no {label} {arch} build"
    return None


def summary(results: dict[str, str]) -> tuple[str, int]:
    """The one-line tally and the failed count (skips never fail the run)."""
    ok = sum(v == "ok" for v in results.values())
    skipped = sum(v.startswith("skipped:") for v in results.values())
    failed = len(results) - ok - skipped
    label = _OS_LABELS.get(host_platform()[0], "this OS")
    return f"{ok} ok · {skipped} skipped (no {label} build) · {failed} failed", failed


def store_records() -> dict[str, dict]:
    from frontend.duckyos_account import store_catalog

    cat = store_catalog()
    if not cat.get("ok"):
        raise RuntimeError(f"Store catalog: {cat.get('error')}")
    return {str(i.get("slug")): i for i in cat.get("items") or []}


def _error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}".strip()


def _register(pid: str) -> None:
    """The host's own load of one installed plugin; raises what register() raised."""
    from backend.uefn_plugins.plugin_host import loader
    from backend.uefn_plugins.store import load_plugin_manifest, plugin_dir

    manifest = load_plugin_manifest(pid)
    if not manifest:
        raise RuntimeError("installed without a readable plugin.json")
    failure: list[BaseException] = []

    def run() -> None:
        try:
            loader._load_one(pid, plugin_dir(pid), manifest)
        except BaseException as exc:  # noqa: BLE001 — reported per plugin
            failure.append(exc)

    t = threading.Thread(target=run, daemon=True, name=f"smoke-{pid}")
    t.start()
    t.join(REGISTER_TIMEOUT_S)
    if t.is_alive():
        raise TimeoutError(f"register() still running after {REGISTER_TIMEOUT_S:.0f}s")
    if failure:
        raise failure[0]


def smoke(ids: list[str], install, records: dict[str, dict] | None = None,
          register=None, where: tuple[str, str] | None = None) -> dict[str, str]:
    """``install(pid)`` puts the plugin in the profile; then its register() runs.

    ``records`` (slug -> Store item) skips plugins with no build for ``where``."""
    register = register or _register
    os_name, arch = where or host_platform()
    results: dict[str, str] = {}
    for pid in ids:
        reason = skip_reason((records or {}).get(pid), os_name, arch)
        if reason:
            results[pid] = reason
            print(f"{pid}: {reason}", file=sys.stderr, flush=True)
            continue
        try:
            install(pid)
            register(pid)
            results[pid] = "ok"
        except BaseException as exc:  # noqa: BLE001 — one plugin's failure never stops the rest
            if isinstance(exc, KeyboardInterrupt):
                raise
            results[pid] = _error(exc)
        print(f"{pid}: {results[pid]}", file=sys.stderr, flush=True)
    return results


def install_from_store(pid: str) -> None:
    from frontend.duckyos_account import store_download_and_install

    # is_update: a CI install never adds to the plugin's install_count on the Store.
    result = store_download_and_install(pid, replace=True, is_update=True)
    if not result.get("ok"):
        raise RuntimeError(f"install failed: {result.get('error')}")
    if result.get("kind") != "plugin":
        raise RuntimeError(f"{pid} is a {result.get('kind')}, not a plugin")


def _fixture_zip(pid: str, backend_src: str) -> bytes:
    manifest = {"id": pid, "kind": "plugin", "version": 1, "label": pid, "default_enabled": False,
                "backend": {"entry": "backend", "register": "register"}}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("plugin.json", json.dumps(manifest))
        zf.writestr("backend/__init__.py", backend_src)
    return buf.getvalue()


_FIXTURES = {
    "smoke-ok": "def register(api):\n    api.log('smoke ok')\n",
    # What a Windows-only import does on a Mac.
    "smoke-bad": "import ducky_no_such_module_on_this_os\n\ndef register(api):\n    pass\n",
}


def install_fixture(pid: str) -> None:
    from backend.uefn_plugins.store import import_plugin_from_bytes

    result = import_plugin_from_bytes(_fixture_zip(pid, _FIXTURES[pid]), source="local", replace=True)
    if not result.get("ok"):
        raise RuntimeError(f"install failed: {result.get('error')}")


def self_test() -> int:
    results = smoke(list(_FIXTURES), install_fixture)
    print(json.dumps(results, indent=1))
    assert results["smoke-ok"] == "ok", results
    assert results["smoke-bad"].startswith("ModuleNotFoundError"), results
    print("self-test ok")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ids", nargs="*", help="Store plugin ids")
    ap.add_argument("--out", type=Path, help="also write the JSON here")
    ap.add_argument("--self-test", action="store_true", help="run against two local fixture plugins")
    args = ap.parse_args(argv)
    if not args.self_test and not args.ids:
        ap.error("give plugin ids or --self-test")
    base = os.environ.get("RUNNER_TEMP") or None
    # The app keeps ducky.db open until exit: Windows can't delete it from inside this process.
    with tempfile.TemporaryDirectory(prefix="ducky-plugin-smoke-", dir=base, ignore_cleanup_errors=True) as tmp:
        _isolate(Path(tmp) / "profile")
        if args.self_test:
            return self_test()
        results = smoke(args.ids, install_from_store, store_records())
    text = json.dumps(results, indent=1)
    print(text)
    line, failed = summary(results)
    print(line)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
