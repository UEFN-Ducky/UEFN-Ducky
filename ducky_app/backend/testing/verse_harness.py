"""Verse in-game test harness — scaffold, run, parse [DUCKY-TEST] results."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

TEST_TAG = "[DUCKY-TEST]"
HARNESS_REL = "Verse/DuckyTests/ducky_test_device.verse"
SCENARIOS_DIR = ".ducky/tests"

_RESULT_RE = re.compile(
    rf"{re.escape(TEST_TAG)}\s+(PASS|FAIL|SKIP)\s+(\S+)\s*:\s*(.*)$"
)


SCAFFOLD_TEMPLATE = '''using { /Fortnite.com/Devices }
using { /Verse.org/Simulation }
using { /UnrealEngine.com/Temporary/Diagnostics }

# Ducky Tester harness — auto-generated scaffold.
# Add cases in RunAllTests(). Results print as: [DUCKY-TEST] PASS|FAIL name: detail

ducky_test_device := class(creative_device):

    var PassCount : int = 0
    var FailCount : int = 0

    ExpectEqual<public>(Name : string, Actual : int, Expected : int) : void =
        if (Actual = Expected):
            set PassCount += 1
            Print("[DUCKY-TEST] PASS {Name}: got {Actual}")
        else:
            set FailCount += 1
            Print("[DUCKY-TEST] FAIL {Name}: expected {Expected} got {Actual}")

    ExpectTrue<public>(Name : string, Condition : logic) : void =
        if (Condition?):
            set PassCount += 1
            Print("[DUCKY-TEST] PASS {Name}: true")
        else:
            set FailCount += 1
            Print("[DUCKY-TEST] FAIL {Name}: expected true")

    ExpectInRange<public>(Name : string, Actual : float, Lo : float, Hi : float) : void =
        if (Actual >= Lo and Actual <= Hi):
            set PassCount += 1
            Print("[DUCKY-TEST] PASS {Name}: {Actual} in [{Lo},{Hi}]")
        else:
            set FailCount += 1
            Print("[DUCKY-TEST] FAIL {Name}: {Actual} not in [{Lo},{Hi}]")

    RunAllTests<public>() : void =
        # Example leveling math — replace with your real formulas
        XpForLevel := 100
        ExpectEqual("leveling.xp_per_level", XpForLevel, 100)
        ExpectTrue("leveling.xp_positive", XpForLevel > 0)
        # Example movement value check
        WalkSpeed := 600.0
        ExpectInRange("movement.walk_speed", WalkSpeed, 100.0, 2000.0)

    OnBegin<override>()<suspends> : void =
        RunAllTests()
        Print("[DUCKY-TEST] PASS summary: {PassCount} passed, {FailCount} failed")
'''


def scaffold_content() -> str:
    return SCAFFOLD_TEMPLATE


def parse_test_results(lines: list[str]) -> dict[str, Any]:
    """Parse editor-log lines for [DUCKY-TEST] PASS|FAIL|SKIP entries."""
    results: list[dict[str, Any]] = []
    for line in lines:
        text = line.rstrip()
        m = _RESULT_RE.search(text)
        if not m:
            if TEST_TAG in text and "summary" in text.lower():
                results.append(
                    {
                        "status": "INFO",
                        "name": "summary",
                        "detail": text.split(TEST_TAG, 1)[-1].strip(),
                        "raw": text,
                    }
                )
            continue
        results.append(
            {
                "status": m.group(1),
                "name": m.group(2),
                "detail": m.group(3).strip(),
                "raw": text,
            }
        )
    passed = sum(1 for r in results if r["status"] == "PASS" and r["name"] != "summary")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    skipped = sum(1 for r in results if r["status"] == "SKIP")
    return {
        "ok": failed == 0,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "results": results,
        "total": passed + failed + skipped,
    }


def list_harness_tests(project_root: str) -> list[dict[str, Any]]:
    """Discover test cases from Verse/DuckyTests/*.verse + .ducky/tests/*.json."""
    root = Path(project_root)
    tests: list[dict[str, Any]] = []

    # Parse Expect*("name", ...) from harness files
    expect_re = re.compile(
        r'Expect(?:Equal|True|InRange)\w*\s*\(\s*"([^"]+)"',
    )
    ducky_tests = root / "Verse" / "DuckyTests"
    if ducky_tests.is_dir():
        for path in sorted(ducky_tests.glob("*.verse")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = str(path.relative_to(root)).replace("\\", "/")
            names = expect_re.findall(text)
            if not names:
                tests.append(
                    {
                        "id": f"verse:{rel}",
                        "name": path.stem,
                        "kind": "verse_harness",
                        "path": rel,
                        "cases": [],
                    }
                )
            else:
                for name in names:
                    tests.append(
                        {
                            "id": f"verse:{rel}:{name}",
                            "name": name,
                            "kind": "verse_harness",
                            "path": rel,
                            "cases": [name],
                        }
                    )

    scenarios = root / SCENARIOS_DIR
    if scenarios.is_dir():
        for path in sorted(scenarios.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            rel = str(path.relative_to(root)).replace("\\", "/")
            tests.append(
                {
                    "id": f"sim:{path.stem}",
                    "name": data.get("name") or path.stem,
                    "kind": "simulation",
                    "path": rel,
                    "device": data.get("device"),
                    "event": data.get("event") or "InteractedWithEvent",
                    "cases": data.get("expect_effects") or [],
                }
            )
    return tests


def save_simulation_scenario(
    project_root: str,
    name: str,
    device: str,
    event: str = "InteractedWithEvent",
    expect_effects: list[str] | None = None,
) -> dict[str, Any]:
    root = Path(project_root)
    dest_dir = root / SCENARIOS_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w\-]+", "_", name.strip()) or "scenario"
    path = dest_dir / f"{safe}.json"
    payload = {
        "name": name,
        "device": device,
        "event": event,
        "expect_effects": list(expect_effects or []),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "path": str(path.relative_to(root)).replace("\\", "/"), "scenario": payload}


def load_simulation_scenario(project_root: str, name_or_path: str) -> dict[str, Any]:
    """Load a `.ducky/tests/*.json` scenario by stem, id (`sim:stem`), or relative path."""
    root = Path(project_root)
    raw = (name_or_path or "").strip()
    if raw.startswith("sim:"):
        raw = raw[4:]
    candidates = [
        root / SCENARIOS_DIR / f"{raw}.json",
        root / raw,
        root / SCENARIOS_DIR / Path(raw).name,
    ]
    if not raw.endswith(".json"):
        candidates.insert(0, root / SCENARIOS_DIR / f"{Path(raw).stem}.json")
    for path in candidates:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            return {
                "ok": True,
                "path": str(path.relative_to(root)).replace("\\", "/"),
                "scenario": data,
            }
    return {"ok": False, "error": f"scenario not found: {name_or_path!r}"}


def _format_expect_line(kind: str, name: str, args: dict[str, Any]) -> str:
    """Build one harness Expect* call. kind: equal | true | in_range."""
    k = (kind or "equal").strip().lower().replace("-", "_")
    safe_name = name.replace('"', "'")
    if k in ("equal", "eq"):
        actual = args.get("actual", "Actual")
        expected = args.get("expected", 0)
        return f'        ExpectEqual("{safe_name}", {actual}, {expected})'
    if k in ("true", "logic"):
        condition = args.get("condition", "true")
        return f'        ExpectTrue("{safe_name}", {condition})'
    if k in ("in_range", "range", "inrange"):
        actual = args.get("actual", "Actual")
        lo = args.get("lo", args.get("min", 0.0))
        hi = args.get("hi", args.get("max", 1.0))
        return f'        ExpectInRange("{safe_name}", {actual}, {lo}, {hi})'
    raise ValueError(f"unknown expect kind: {kind!r} (use equal|true|in_range)")


def add_verse_test_case(
    project_root: str,
    name: str,
    kind: str = "equal",
    *,
    actual: str = "",
    expected: str = "",
    condition: str = "",
    lo: str = "",
    hi: str = "",
    setup_line: str = "",
) -> dict[str, Any]:
    """Append an Expect* case into RunAllTests() in the harness file (creates scaffold if missing)."""
    root = Path(project_root)
    path = root / HARNESS_REL
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(scaffold_content(), encoding="utf-8", newline="\n")
        created = True
    else:
        created = False

    text = path.read_text(encoding="utf-8")
    # Avoid duplicate names
    if f'"{name}"' in text or f"'{name}'" in text:
        return {
            "ok": True,
            "path": HARNESS_REL,
            "added": False,
            "note": f"case {name!r} already present",
            "created_scaffold": created,
        }

    args: dict[str, Any] = {}
    if actual:
        args["actual"] = actual
    if expected != "":
        args["expected"] = expected
    if condition:
        args["condition"] = condition
    if lo != "":
        args["lo"] = lo
    if hi != "":
        args["hi"] = hi
    line = _format_expect_line(kind, name, args)
    block = (f"        {setup_line.rstrip()}\n" if setup_line.strip() else "") + line + "\n"

    marker = "    OnBegin<override>()"
    idx = text.find(marker)
    if idx < 0:
        # Fallback: append before end of file
        text = text.rstrip() + "\n" + block
    else:
        # Insert just before OnBegin — still inside RunAllTests
        run_idx = text.rfind("RunAllTests", 0, idx)
        if run_idx < 0:
            text = text[:idx] + block + text[idx:]
        else:
            text = text[:idx] + block + "\n" + text[idx:]

    path.write_text(text, encoding="utf-8", newline="\n")
    return {
        "ok": True,
        "path": HARNESS_REL,
        "added": True,
        "case": name,
        "kind": kind,
        "line": line.strip(),
        "created_scaffold": created,
    }


def compare_simulation_effects(
    sim_result: dict[str, Any],
    expect_effects: list[str],
) -> dict[str, Any]:
    """Check that each expected effect kind appears in a simulate_device_event result."""
    got = {str(e.get("kind") or "") for e in (sim_result.get("effects") or [])}
    missing = [e for e in expect_effects if e and e not in got]
    return {
        "ok": len(missing) == 0 and bool(sim_result.get("ok", True)),
        "expected": list(expect_effects),
        "got": sorted(got),
        "missing": missing,
        "effect_count": len(sim_result.get("effects") or []),
        "steps": sim_result.get("steps"),
    }
