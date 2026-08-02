"""Assert Ducky Source-Available License v1.0 is copied byte-identical everywhere.

Run from UEFN-Ducky-Release repo root (or any cwd; paths are absolute-sibling).
  py scripts/check_licenses.py
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

RELEASE = Path(__file__).resolve().parents[1]
DUCKY = RELEASE.parent / "UEFN-Ducky"
DUCKYOS = RELEASE.parent / "DuckyOS"
CANON = RELEASE / "LICENSE"
SPDX = "LicenseRef-Ducky-SAL-1.0"
LICENSE_NAME = "Ducky Source-Available License v1.0"

# First-party docs that must not claim MIT / "free and open source".
# Third-party paths (ponytail MIT, blender-mcp, lore-vcs-sdk, .venv, node_modules) are excluded.
# Docs that describe *this* project's license (not third-party notices, which
# correctly mention MIT for dependencies like Monaco / lore-vcs-sdk).
CLAIM_SCAN_ROOTS = [
    RELEASE / "README.md",
    RELEASE / "CONTRIBUTING.md",
    RELEASE / "release" / "portable" / "START_HERE.txt",
    RELEASE / "release" / "installer" / "UEFN-Ducky.iss",
    DUCKY / "README.md",
    DUCKYOS / "README.md",
    DUCKYOS / "duckyos" / "CONTRIBUTING.md",
]
# Project-level claims only — "MIT License" alone is fine in dependency lists.
FORBIDDEN_CLAIM = re.compile(
    r"(?i)\bfree and open source\b|\b\[MIT\]\(LICENSE\)|\blicensed under the MIT\b"
)


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _collect_license_paths() -> list[Path]:
    paths: list[Path] = [CANON]
    paths.append(DUCKY / "LICENSE")
    paths.extend(sorted(DUCKY.glob("plugins/uefn-plugin-*/LICENSE")))
    paths.extend(sorted(DUCKY.glob("plugins/_skill_packs/*/LICENSE.txt")))
    paths.append(RELEASE / "ducky_app" / "frontend" / "skill_packs" / "ducky" / "LICENSE.txt")
    paths.append(DUCKYOS / "LICENSE")
    paths.append(DUCKYOS / "duckyos" / "LICENSE")
    paths.extend(sorted(DUCKYOS.glob("plugins/*/LICENSE")))
    return paths


def _collect_cargo_tomls() -> list[Path]:
    paths = [
        DUCKYOS / "duckyos" / "Cargo.toml",
        DUCKYOS / "duckyos" / "deploy" / "prod" / "Cargo.docker.toml",
    ]
    paths.extend(sorted(DUCKYOS.glob("plugins/plugin-*/Cargo.toml")))
    return paths


def main() -> int:
    assert CANON.is_file(), f"canonical LICENSE missing: {CANON}"
    canon_hash = _md5(CANON)
    body = CANON.read_text(encoding="utf-8")
    assert LICENSE_NAME in body, "canonical LICENSE missing license name"
    assert "Mindful Path Company, LLC" in body, "canonical LICENSE missing copyright holder"
    assert SPDX in body, f"canonical LICENSE missing {SPDX}"

    license_paths = _collect_license_paths()
    missing = [p for p in license_paths if not p.is_file()]
    assert not missing, f"missing LICENSE copies:\n" + "\n".join(str(p) for p in missing)

    bad_hash = [p for p in license_paths if _md5(p) != canon_hash]
    assert not bad_hash, f"LICENSE copies differ from canonical:\n" + "\n".join(
        str(p) for p in bad_hash
    )

    uefn_plugins = list(DUCKY.glob("plugins/uefn-plugin-*/LICENSE"))
    assert len(uefn_plugins) >= 38, f"expected >=38 uefn plugin LICENSEs, got {len(uefn_plugins)}"
    duckyos_plugins = list(DUCKYOS.glob("plugins/*/LICENSE"))
    assert len(duckyos_plugins) >= 32, (
        f"expected >=32 duckyos plugin LICENSEs, got {len(duckyos_plugins)}"
    )

    cargo_paths = _collect_cargo_tomls()
    cargo_missing = [p for p in cargo_paths if not p.is_file()]
    assert not cargo_missing, f"missing Cargo.toml:\n" + "\n".join(str(p) for p in cargo_missing)
    cargo_bad = []
    for p in cargo_paths:
        text = p.read_text(encoding="utf-8")
        if f'license = "{SPDX}"' not in text:
            cargo_bad.append(p)
    assert not cargo_bad, f"Cargo.toml missing {SPDX}:\n" + "\n".join(str(p) for p in cargo_bad)

    claim_hits: list[str] = []
    for p in CLAIM_SCAN_ROOTS:
        if not p.is_file():
            claim_hits.append(f"missing claim-scan file: {p}")
            continue
        text = p.read_text(encoding="utf-8")
        if FORBIDDEN_CLAIM.search(text):
            claim_hits.append(str(p))
    assert not claim_hits, "first-party docs still claim MIT / open source:\n" + "\n".join(
        claim_hits
    )

    print(f"ok: {len(license_paths)} LICENSE copies match {canon_hash[:12]}…")
    print(f"ok: {len(cargo_paths)} Cargo.toml files use {SPDX}")
    print("ok: no first-party MIT / free-and-open-source claims")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
