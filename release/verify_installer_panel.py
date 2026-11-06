"""Extract the built Inno payload and test frozen panel recovery before upload.

This does not run Setup, register an installation, or touch user data.
Use an innoextract version supporting the Inno version used by this build.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LAUNCHER = "Ducky-Launcher.exe"
LOADER_DLL = "WebView2Loader.dll"  # loaded at start: the launcher cannot run without it


def app_version(root: Path = REPO) -> str:
    init = (root / "ducky_app" / "frontend" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', init, re.M)
    if match is None:
        raise RuntimeError("no __version__ in ducky_app/frontend/__init__.py")
    return match.group(1)


def _signature_status(path: Path) -> str:
    sys.path.insert(0, str(REPO / "release"))
    from sign_windows import signature_info

    return str(signature_info(path).get("status") or "Unknown")


def check_launcher(app: Path, version: str, *, signed: bool,
                   run=subprocess.run, signature=_signature_status) -> None:
    """The native launcher and its dll beside UEFN-Ducky.exe, always (no build ships
    without them): signed when the release is, and ``--version`` prints the app version."""
    exe = app / LAUNCHER
    if not exe.is_file():
        raise RuntimeError(f"{LAUNCHER} missing from the installer payload")
    if not (app / LOADER_DLL).is_file():
        raise RuntimeError(f"{LAUNCHER} ships without {LOADER_DLL} beside it")
    if signed and (status := signature(exe)) != "Valid":
        raise RuntimeError(f"{LAUNCHER} is not signed (Authenticode status {status})")
    result = run([str(exe), "--version"], capture_output=True, text=True, timeout=30, check=False)
    printed = (result.stdout or "").strip()
    if result.returncode != 0 or printed != version:
        raise RuntimeError(f"{LAUNCHER} --version: exit={result.returncode}, printed {printed!r}, want {version!r}")
    print(f"PASS: {LAUNCHER} + {LOADER_DLL} present{', signed' if signed else ''}, --version {version}", flush=True)


def check_default_off(report: dict) -> None:
    """Fresh settings keep the native launcher off (the runtime-smoke report's value)."""
    mode = report.get("native_launcher")
    if mode is None:
        raise RuntimeError("runtime-smoke report has no native_launcher (an app older than this check?)")
    if mode != "off":
        raise RuntimeError(f"fresh settings have native_launcher={mode!r}, want 'off'")
    print("PASS: fresh settings have native_launcher off", flush=True)


def expect_signed(app: Path, signed: bool | None, signature=_signature_status) -> bool:
    """None means decide from the build: signed when the app EXE in the same install is."""
    if signed is None:
        signed = signature(app / "UEFN-Ducky.exe") == "Valid"
    return signed


def run_check(exe: Path, flag: str, output: Path, *, succeeds: bool, recovered=None):
    result = subprocess.run([str(exe), flag, str(output)], timeout=120, check=False)
    report = json.loads(output.read_text(encoding="utf-8"))
    if (result.returncode == 0) != succeeds or report["ok"] != succeeds:
        raise RuntimeError(f"{flag}: exit={result.returncode}, report={report}")
    if recovered is not None and report.get("recovered") != recovered:
        raise RuntimeError(f"{flag}: expected recovered={recovered}, report={report}")
    return report


def verify(extractor: Path, engine: Path, *, version: str = "", signed: bool | None = None) -> None:
    version = version or app_version()
    with tempfile.TemporaryDirectory(prefix="ducky-installer-panel-proof-") as scratch:
        root = Path(scratch)
        subprocess.run(
            [str(extractor), "--silent", "--extract", "--output-dir", str(root), str(engine)],
            timeout=300, check=True,
        )
        exe = root / "app" / "UEFN-Ducky.exe"
        panel = root / "app" / "_internal" / "frontend" / "ui_web" / "web" / "dist"
        output = root / "result.json"
        run_check(exe, "--verify-panel", output, succeeds=True)
        run_check(exe, "--panel-recovery-smoke", output, succeeds=True, recovered=False)
        check_default_off(run_check(exe, "--runtime-smoke", output, succeeds=True))
        print("PASS: extracted installer, strict asset check and frozen runtime", flush=True)
        check_launcher(root / "app", version, signed=expect_signed(root / "app", signed))

        index = panel / "index.html"
        match = re.search(r'(?:src|href)=["\'](?:\./|/)?(assets/[^"\']+\.js)["\']',
                          index.read_text(encoding="utf-8"))
        if match is None:
            raise RuntimeError("Installer panel has no JavaScript entrypoint")
        entry = panel / match.group(1)
        lazy = next(path for path in (panel / "assets").glob("*.js") if path != entry)
        for label, damaged, corrupt in (
            ("missing entrypoint", entry, False),
            ("missing lazy chunk", lazy, False),
            ("corrupted entrypoint", entry, True),
            ("missing index", index, False),
        ):
            original = damaged.read_bytes()
            try:
                if corrupt:
                    damaged.write_bytes(b"x" * len(original))
                else:
                    damaged.unlink()
                run_check(exe, "--verify-panel", output, succeeds=False)
                run_check(exe, "--panel-recovery-smoke", output,
                          succeeds=True, recovered=True)
                if corrupt:
                    assert damaged.read_bytes() != original
                else:
                    assert not damaged.exists()
                print(f"PASS: {label} rejected by installer and recovered by app", flush=True)
            finally:
                damaged.write_bytes(original)
        # A listener install failure must not disable the separate panel recovery.
        listener = root / "app" / "_internal" / "bundle" / "uefn_listener"
        held_listener = listener.with_name("uefn_listener-proof-held")
        listener.rename(held_listener)
        original = entry.read_bytes()
        try:
            entry.unlink()
            run_check(exe, "--panel-recovery-smoke", output,
                      succeeds=True, recovered=True)
            print("PASS: panel HTTP startup recovers with listener payload absent", flush=True)
        finally:
            entry.write_bytes(original)
            held_listener.rename(listener)
        run_check(exe, "--verify-panel", output, succeeds=True)
        print("PASS: restored installer payload", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extractor", type=Path, required=True)
    parser.add_argument("--engine", type=Path, default=Path("dist/Setup-engine.exe"))
    parser.add_argument("--version", default="", help="app version the launcher must print (default: the repo's)")
    parser.add_argument("--expect-signed", action=argparse.BooleanOptionalAction, default=None,
                        help="launcher must be signed (default: when UEFN-Ducky.exe is)")
    # No launcher opt-out flag on purpose: every install must carry Ducky-Launcher.exe.
    args = parser.parse_args()
    opts = {"version": args.version or None, "signed": args.expect_signed}
    # Unset options stay out, so verify() decides them from the build.
    verify(args.extractor.resolve(), args.engine.resolve(), **{k: v for k, v in opts.items() if v is not None})


if __name__ == "__main__":
    main()
