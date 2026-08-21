"""Read UEFN Beta Access flags from ``*.uefnproject`` (no editor required)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _uefnproject_path(project_root: Path) -> Path | None:
    matches = sorted(project_root.glob("*.uefnproject"))
    return matches[0] if matches else None


def read_uefn_beta_access(project_root: str | Path | None) -> dict[str, Any]:
    """Return Beta Access flags for the island.

    When both Python Editor Scripting and UEFN MCP Toolsets are on, UEFN often
    ForceEnablePythonAtRuntime *before* Content mounts — project
    ``Content/Python/init_unreal.py`` never runs (Ducky listener race).
    """
    out: dict[str, Any] = {
        "ok": False,
        "python_editor_scripting": False,
        "uefn_mcp_toolsets": False,
        "scene_graph": False,
        "python_and_toolsets": False,
        "listener_init_race": False,
        "agent_note": "",
        "project_file": "",
    }
    raw = str(project_root or "").strip()
    if not raw:
        out["agent_note"] = "No panel project selected — cannot read Beta Access flags."
        return out
    root = Path(raw)
    path = _uefnproject_path(root)
    if path is None or not path.is_file():
        out["agent_note"] = f"No .uefnproject under {root}"
        return out
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        out["agent_note"] = f"Could not parse {path.name}"
        return out
    if not isinstance(data, dict):
        return out
    exp = data.get("dataSets", {})
    if not isinstance(exp, dict):
        exp = {}
    experimental = exp.get("experimental", {})
    if not isinstance(experimental, dict):
        experimental = {}

    py = experimental.get("pythonExperimental", {})
    py_on = bool(isinstance(py, dict) and py.get("bEnablePythonForProject") is True)

    toolsets = experimental.get("toolsets", {})
    toolsets_on = bool(
        isinstance(toolsets, dict) and toolsets.get("bEnableToolsetsForProject") is True
    )

    sg = experimental.get("sceneGraph", {})
    sg_on = bool(isinstance(sg, dict) and sg.get("bIsSceneGraphSystemAllowed") is True)

    both = py_on and toolsets_on
    out.update(
        {
            "ok": True,
            "python_editor_scripting": py_on,
            "uefn_mcp_toolsets": toolsets_on,
            "scene_graph": sg_on,
            "python_and_toolsets": both,
            # Risk flag only — status.py clears this when the listener is online.
            "listener_init_race": both,
            "project_file": str(path),
        }
    )
    if both:
        out["agent_note"] = (
            "Restart UEFN once if the Ducky listener stays offline after an update. "
            "Ducky :4200 and UEFN MCP :8000 are meant to run together."
        )
    elif toolsets_on and not py_on:
        out["agent_note"] = (
            "UEFN MCP Toolsets on without Python — enable Python Editor Scripting for the Ducky listener."
        )
    elif py_on and not toolsets_on:
        out["agent_note"] = (
            "Python on without UEFN MCP Toolsets — enable Toolsets for nested unreal__* tools."
        )
    else:
        out["agent_note"] = ""
    return out
