"""Assert Ducky SAL on the app/OS + MIT on every UEFN desktop plugin.

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
MIT_MARKER = "MIT License"
MINDFUL = "Mindful Path Company, LLC"

CLAIM_SCAN_ROOTS = [
    RELEASE / "README.md",
    RELEASE / "CONTRIBUTING.md",
    RELEASE / "release" / "portable" / "START_HERE.txt",
    RELEASE / "release" / "installer" / "UEFN-Ducky.iss",
    DUCKY / "README.md",
    DUCKYOS / "README.md",
    DUCKYOS / "duckyos" / "CONTRIBUTING.md",
]
FORBIDDEN_CLAIM = re.compile(
    r"(?i)\bfree and open source\b|\b\[MIT\]\(LICENSE\)|\blicensed under the MIT\b"
)


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _collect_sal_paths() -> list[Path]:
    paths: list[Path] = [CANON, DUCKY / "LICENSE"]
    paths.append(RELEASE / "ducky_app" / "frontend" / "skill_packs" / "ducky" / "LICENSE.txt")
    paths.append(DUCKYOS / "LICENSE")
    paths.append(DUCKYOS / "duckyos" / "LICENSE")
    paths.extend(sorted(DUCKYOS.glob("plugins/plugin-*/LICENSE")))
    return paths


def _plugin_dirs() -> list[Path]:
    return [p for p in sorted(DUCKY.glob("plugins/uefn-plugin-*")) if p.is_dir()]


def _collect_plugin_mit_paths() -> list[Path]:
    out: list[Path] = []
    for plugin_dir in _plugin_dirs():
        out.append(plugin_dir / "LICENSE")
        out.extend(sorted(plugin_dir.glob("skills/*/LICENSE.txt")))
    return out


def _collect_cargo_tomls() -> list[Path]:
    paths = [
        DUCKYOS / "duckyos" / "Cargo.toml",
        DUCKYOS / "duckyos" / "deploy" / "prod" / "Cargo.docker.toml",
    ]
    paths.extend(sorted(DUCKYOS.glob("plugins/plugin-*/Cargo.toml")))
    return paths


def _assert_mit(path: Path) -> str | None:
    if not path.is_file():
        return f"missing MIT LICENSE: {path}"
    text = path.read_text(encoding="utf-8")
    if MIT_MARKER not in text:
        return f"{path}: missing MIT License"
    if MINDFUL not in text:
        return f"{path}: missing {MINDFUL}"
    if LICENSE_NAME in text or "Source-Available" in text:
        return f"{path}: still SAL / source-available"
    if "bundled skills" in text.lower():
        return f"{path}: leftover skills carve-out"
    if "SPDX-License-Identifier: LicenseRef-Ducky-SAL-1.0" in text:
        return f"{path}: leftover SAL SPDX"
    return None


def main() -> int:
    assert CANON.is_file(), f"canonical LICENSE missing: {CANON}"
    canon_hash = _md5(CANON)
    body = CANON.read_text(encoding="utf-8")
    assert LICENSE_NAME in body, "canonical LICENSE missing license name"
    assert MINDFUL in body, "canonical LICENSE missing copyright holder"
    assert SPDX in body, f"canonical LICENSE missing {SPDX}"
    assert "Official first-party skills" in body, "canonical LICENSE missing §5a skills section"

    sal_paths = _collect_sal_paths()
    missing = [p for p in sal_paths if not p.is_file()]
    assert not missing, "missing SAL LICENSE copies:\n" + "\n".join(str(p) for p in missing)
    bad_hash = [p for p in sal_paths if _md5(p) != canon_hash]
    assert not bad_hash, "SAL LICENSE copies differ from canonical:\n" + "\n".join(
        str(p) for p in bad_hash
    )

    plugin_dirs = _plugin_dirs()
    assert len(plugin_dirs) >= 38, f"expected >=38 uefn plugins, got {len(plugin_dirs)}"
    mit_paths = _collect_plugin_mit_paths()
    mit_bad = [msg for p in mit_paths if (msg := _assert_mit(p))]
    assert not mit_bad, "plugin MIT LICENSE problems:\n" + "\n".join(mit_bad)

    skill_bad: list[str] = []
    for plugin_dir in plugin_dirs:
        for skill_md in plugin_dir.glob("skills/*/SKILL.md"):
            fm = skill_md.read_text(encoding="utf-8")
            if "license: MIT" not in fm:
                skill_bad.append(f"SKILL.md license not MIT: {skill_md}")
            if "allow_redistribute: true" not in fm:
                skill_bad.append(f"SKILL.md allow_redistribute not true: {skill_md}")
            if MINDFUL not in fm:
                skill_bad.append(f"SKILL.md missing {MINDFUL}: {skill_md}")
            lic = skill_md.parent / "LICENSE.txt"
            if not lic.is_file():
                skill_bad.append(f"missing skill LICENSE.txt: {lic}")
    assert not skill_bad, "plugin skill license problems:\n" + "\n".join(skill_bad)

    cargo_paths = _collect_cargo_tomls()
    cargo_bad = [
        p
        for p in cargo_paths
        if f'license = "{SPDX}"' not in p.read_text(encoding="utf-8")
    ]
    assert not cargo_bad, f"Cargo.toml missing {SPDX}:\n" + "\n".join(str(p) for p in cargo_bad)

    claim_hits: list[str] = []
    for p in CLAIM_SCAN_ROOTS:
        if not p.is_file():
            claim_hits.append(f"missing claim-scan file: {p}")
            continue
        if FORBIDDEN_CLAIM.search(p.read_text(encoding="utf-8")):
            claim_hits.append(str(p))
    assert not claim_hits, "first-party docs still claim MIT / open source:\n" + "\n".join(
        claim_hits
    )

    print(f"ok: {len(sal_paths)} SAL LICENSE copies match {canon_hash[:12]}…")
    print(f"ok: {len(plugin_dirs)} plugins + {len(mit_paths)} MIT LICENSE files")
    print(f"ok: {len(cargo_paths)} Cargo.toml files use {SPDX}")
    print("ok: app/OS docs do not claim MIT / free-and-open-source")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
