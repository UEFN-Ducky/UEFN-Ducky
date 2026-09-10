"""Local Daily Security Check: secrets + dep CVEs for the EXE and org plugins.

Mirrors .github/workflows/security.yml (gitleaks + pip-audit). Also npm-audits
any package.json. Not a GitHub workflow — run it on this machine.

  py -3 scripts/check_security.py
  py -3 scripts/check_security.py --app-only
  py -3 scripts/check_security.py --self-check
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

RELEASE = Path(__file__).resolve().parents[1]
PLUGINS_ROOT = RELEASE.parent / "uefn-plugins"
GITLEAKS_VERSION = "8.30.1"
TOOLS = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "UEFN-Ducky" / "tools"


def _req_pins(text: str) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("-r ") or "git+" in s:
            continue
        out.append(s)
    return list(dict.fromkeys(out))


def _plugin_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return [
        p
        for p in sorted(root.iterdir())
        if p.is_dir()
        and (p.name.startswith("uefn-plugin-") or p.name.startswith("uefn-skill-"))
    ]


def _targets(*, app_only: bool) -> list[tuple[str, Path]]:
    found = [("UEFN-Ducky", RELEASE)]
    if not app_only:
        found.extend((p.name, p) for p in _plugin_dirs(PLUGINS_ROOT))
    return found


def _ensure_gitleaks() -> Path:
    exe_name = "gitleaks.exe" if os.name == "nt" else "gitleaks"
    cached = TOOLS / exe_name
    on_path = shutil.which("gitleaks")
    if on_path:
        return Path(on_path)
    if cached.is_file():
        return cached
    TOOLS.mkdir(parents=True, exist_ok=True)
    archive = f"gitleaks_{GITLEAKS_VERSION}_windows_x64.zip"
    url = f"https://github.com/gitleaks/gitleaks/releases/download/v{GITLEAKS_VERSION}/{archive}"
    dest_zip = TOOLS / archive
    urllib.request.urlretrieve(url, dest_zip)
    with zipfile.ZipFile(dest_zip) as zf:
        zf.extract(exe_name, TOOLS)
    dest_zip.unlink(missing_ok=True)
    return cached


def _ensure_pip_audit() -> None:
    try:
        import pip_audit  # noqa: F401
    except ImportError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--upgrade", "pip", "pip-audit"]
        )


def _run(cmd: list[str], *, cwd: Path) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out.strip()


def _gitleaks(root: Path, exe: Path) -> tuple[str, bool]:
    cmd = [str(exe), "detect", "--source", ".", "--verbose", "--redact"]
    cfg = RELEASE / ".gitleaks.toml"
    if cfg.is_file():
        cmd.extend(["--config", str(cfg)])
    code, out = _run(cmd, cwd=root)
    if code == 0:
        return "ok", True
    return out or f"exit {code}", False


def _pip_audit(root: Path) -> tuple[str, bool]:
    srcs = [p for p in (root / "requirements.txt", root / "requirements-dev.txt") if p.is_file()]
    if not srcs:
        return "skip (no requirements)", True
    pins: list[str] = []
    for src in srcs:
        pins.extend(_req_pins(src.read_text(encoding="utf-8")))
    pins = list(dict.fromkeys(pins))
    if not pins:
        return "skip (no PyPI pins)", True
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".txt", delete=False
    ) as fh:
        fh.write("\n".join(pins) + "\n")
        req = Path(fh.name)
    try:
        code, out = _run(
            [sys.executable, "-m", "pip_audit", "-r", str(req)], cwd=root
        )
    finally:
        req.unlink(missing_ok=True)
    if code == 0:
        return "ok", True
    return out or f"exit {code}", False


def _npm_summary(raw: str) -> str:
    data = json.loads(raw)
    counts = data.get("metadata", {}).get("vulnerabilities") or {}
    roots: list[str] = []
    for name, info in (data.get("vulnerabilities") or {}).items():
        via = info.get("via") or []
        if any(isinstance(item, dict) for item in via):
            roots.append(f"{name} ({info.get('severity', '?')})")
    parts = [f"{k}={v}" for k, v in counts.items() if v]
    line = ", ".join(parts) or "issues"
    if roots:
        line += " — " + ", ".join(sorted(roots))
    return line


def _npm_audit(root: Path) -> tuple[str, bool]:
    npm = shutil.which("npm")
    if not npm:
        return "skip (npm not on PATH)", True
    pkgs = sorted(p for p in root.rglob("package.json") if "node_modules" not in p.parts)
    if not pkgs:
        return "skip (no package.json)", True
    failed: list[str] = []
    ok_n = 0
    for pkg in pkgs:
        code, out = _run([npm, "audit", "--omit=dev", "--json"], cwd=pkg.parent)
        if code == 0:
            ok_n += 1
            continue
        rel = pkg.parent.relative_to(root) if pkg.parent != root else Path(".")
        try:
            detail = _npm_summary(out)
        except (json.JSONDecodeError, TypeError, KeyError):
            detail = out.splitlines()[-1] if out else f"exit {code}"
        failed.append(f"{rel}: {detail}")
    if not failed:
        return f"ok ({ok_n})", True
    return "\n".join(failed), False


def _self_check() -> None:
    assert _req_pins("# a\nmcp>=1\ntoon @ git+https://x\n-r other.txt\n") == ["mcp>=1"]
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "uefn-plugin-x").mkdir()
        (root / "uefn-skill-y").mkdir()
        (root / "ignore-me").mkdir()
        names = [p.name for p in _plugin_dirs(root)]
        assert names == ["uefn-plugin-x", "uefn-skill-y"], names
    sample = json.dumps(
        {
            "metadata": {"vulnerabilities": {"high": 1, "moderate": 2, "total": 3}},
            "vulnerabilities": {
                "brace-expansion": {"severity": "high", "via": [{"source": 1}]},
                "monaco-languageclient": {"severity": "moderate", "via": ["dompurify"]},
            },
        }
    )
    assert "brace-expansion (high)" in _npm_summary(sample)
    assert "monaco-languageclient" not in _npm_summary(sample)
    print("self-check: ok")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-only", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        _self_check()
        return 0

    _self_check()
    exe = _ensure_gitleaks()
    _ensure_pip_audit()
    targets = _targets(app_only=args.app_only)
    if not args.app_only and len(targets) < 2:
        print(f"WARN: no plugin clones under {PLUGINS_ROOT}", file=sys.stderr)

    failed: list[str] = []
    for name, root in targets:
        print(f"== {name}")
        checks = (
            ("gitleaks", lambda: _gitleaks(root, exe)),
            ("pip-audit", lambda: _pip_audit(root)),
            ("npm-audit", lambda: _npm_audit(root)),
        )
        for label, fn in checks:
            msg, ok = fn()
            print(f"  {label}: {msg if ok else 'FAIL'}")
            if not ok:
                print(msg)
                failed.append(f"{name}/{label}")
        print()

    print(f"scanned {len(targets)} repos ({'app only' if args.app_only else 'app + org plugins'})")
    if failed:
        print("FAIL: " + ", ".join(failed))
        return 1
    print("ok: no secrets, no known dep CVEs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
