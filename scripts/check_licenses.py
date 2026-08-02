"""Assert Ducky SAL + free-plugin MIT split is consistent across repos.

Run from UEFN-Ducky-Release repo root (or any cwd; paths are absolute-sibling).
  py scripts/check_licenses.py
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

RELEASE = Path(__file__).resolve().parents[1]
DUCKY = RELEASE.parent / "UEFN-Ducky"
DUCKYOS = RELEASE.parent / "DuckyOS"
CANON = RELEASE / "LICENSE"
SPDX = "LicenseRef-Ducky-SAL-1.0"
LICENSE_NAME = "Ducky Source-Available License v1.0"

# Short plugin ids whose plugin code is MIT; skills stay SAL.
FREE_PLUGIN_IDS = frozenset(
    {
        "light",
        "hacker",
        "vim",
        "openai",
        "anthropic",
        "google",
        "googledrive",
        "ollama",
        "kimi",
        "spacexai",
        "elevenlabs",
        "cursor",
        "discord",
        "warcraft",
        "galaxycraft",
        "meshy",
        "blender",
        "studio3d",
        "piper",
    }
)

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
SKILLS_CARVEOUT = re.compile(r"(?i)bundled skills")


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _plugin_id(plugin_dir: Path) -> str:
    pj = plugin_dir / "plugin.json"
    if pj.is_file():
        try:
            data = json.loads(pj.read_text(encoding="utf-8"))
            pid = str(data.get("id") or "").strip()
            if pid:
                return pid
        except json.JSONDecodeError:
            pass
    return plugin_dir.name.removeprefix("uefn-plugin-")


def _is_free_plugin(plugin_dir: Path) -> bool:
    pid = _plugin_id(plugin_dir)
    folder = plugin_dir.name.removeprefix("uefn-plugin-")
    return bool({pid, folder, folder.removeprefix("uefn-")} & FREE_PLUGIN_IDS)


def _collect_sal_paths() -> list[Path]:
    paths: list[Path] = [CANON, DUCKY / "LICENSE"]
    paths.extend(sorted(DUCKY.glob("plugins/_skill_packs/*/LICENSE.txt")))
    paths.append(RELEASE / "ducky_app" / "frontend" / "skill_packs" / "ducky" / "LICENSE.txt")
    paths.append(DUCKYOS / "LICENSE")
    paths.append(DUCKYOS / "duckyos" / "LICENSE")
    paths.extend(sorted(DUCKYOS.glob("plugins/*/LICENSE")))
    for plugin_dir in sorted(DUCKY.glob("plugins/uefn-plugin-*")):
        if plugin_dir.is_dir() and not _is_free_plugin(plugin_dir):
            paths.append(plugin_dir / "LICENSE")
    # Skill LICENSE.txt under free plugins that ship skills
    for plugin_dir in sorted(DUCKY.glob("plugins/uefn-plugin-*")):
        if not _is_free_plugin(plugin_dir):
            continue
        paths.extend(sorted(plugin_dir.glob("skills/*/LICENSE.txt")))
    return paths


def _collect_free_plugin_licenses() -> list[Path]:
    out: list[Path] = []
    for plugin_dir in sorted(DUCKY.glob("plugins/uefn-plugin-*")):
        if plugin_dir.is_dir() and _is_free_plugin(plugin_dir):
            out.append(plugin_dir / "LICENSE")
    return out


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
    assert "Official first-party skills" in body, "canonical LICENSE missing §5a skills section"

    sal_paths = _collect_sal_paths()
    missing = [p for p in sal_paths if not p.is_file()]
    assert not missing, "missing SAL LICENSE copies:\n" + "\n".join(str(p) for p in missing)
    bad_hash = [p for p in sal_paths if _md5(p) != canon_hash]
    assert not bad_hash, "SAL LICENSE copies differ from canonical:\n" + "\n".join(
        str(p) for p in bad_hash
    )

    free_paths = _collect_free_plugin_licenses()
    assert len(free_paths) == len(FREE_PLUGIN_IDS), (
        f"expected {len(FREE_PLUGIN_IDS)} free plugin LICENSEs, got {len(free_paths)}"
    )
    free_bad = []
    for p in free_paths:
        text = p.read_text(encoding="utf-8")
        if "MIT License" not in text:
            free_bad.append(f"{p}: missing MIT License")
        elif not SKILLS_CARVEOUT.search(text):
            free_bad.append(f"{p}: missing bundled-skills carve-out")
        elif LICENSE_NAME in text and "NOT covered" not in text:
            free_bad.append(f"{p}: looks like SAL body, not MIT+carve-out")
        elif _md5(p) == canon_hash:
            free_bad.append(f"{p}: identical to SAL canonical (should be MIT)")
    assert not free_bad, "free plugin LICENSE problems:\n" + "\n".join(free_bad)

    # Free plugins with skills must ship SAL LICENSE.txt beside SKILL.md
    for plugin_dir in sorted(DUCKY.glob("plugins/uefn-plugin-*")):
        if not _is_free_plugin(plugin_dir):
            continue
        for skill_md in plugin_dir.glob("skills/*/SKILL.md"):
            lic = skill_md.parent / "LICENSE.txt"
            assert lic.is_file(), f"missing skill LICENSE.txt: {lic}"
            assert _md5(lic) == canon_hash, f"skill LICENSE.txt not SAL: {lic}"
            fm = skill_md.read_text(encoding="utf-8")
            assert "license: Ducky Source-Available License v1.0" in fm, (
                f"SKILL.md license field not SAL: {skill_md}"
            )

    uefn_plugins = list(DUCKY.glob("plugins/uefn-plugin-*/LICENSE"))
    assert len(uefn_plugins) >= 38, f"expected >=38 uefn plugin LICENSEs, got {len(uefn_plugins)}"

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
    print(f"ok: {len(free_paths)} free plugins use MIT + skills carve-out")
    print(f"ok: {len(cargo_paths)} Cargo.toml files use {SPDX}")
    print("ok: no first-party MIT / free-and-open-source project claims")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
