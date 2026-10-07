"""Extract the built Inno payload and test frozen panel recovery before upload.

This does not run Setup, register an installation, or touch user data.
Use an innoextract version supporting the Inno version used by this build.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path


def run_check(exe: Path, flag: str, output: Path, *, succeeds: bool, recovered=None):
    result = subprocess.run([str(exe), flag, str(output)], timeout=120, check=False)
    report = json.loads(output.read_text(encoding="utf-8"))
    if (result.returncode == 0) != succeeds or report["ok"] != succeeds:
        raise RuntimeError(f"{flag}: exit={result.returncode}, report={report}")
    if recovered is not None and report.get("recovered") != recovered:
        raise RuntimeError(f"{flag}: expected recovered={recovered}, report={report}")
    return report


def verify(extractor: Path, engine: Path) -> None:
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
        run_check(exe, "--runtime-smoke", output, succeeds=True)
        print("PASS: extracted installer, strict asset check and frozen runtime", flush=True)

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
        run_check(exe, "--verify-panel", output, succeeds=True)
        print("PASS: restored installer payload", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extractor", type=Path, required=True)
    parser.add_argument("--engine", type=Path, default=Path("dist/Setup-engine.exe"))
    args = parser.parse_args()
    verify(args.extractor.resolve(), args.engine.resolve())


if __name__ == "__main__":
    main()
