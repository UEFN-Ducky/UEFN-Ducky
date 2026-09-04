"""Beta Access flag reader for Python + UEFN MCP Toolsets coexistence."""

from __future__ import annotations

import json
from pathlib import Path

from frontend.uefn_project_beta import read_uefn_beta_access


def test_read_beta_both_on(tmp_path: Path) -> None:
    root = tmp_path / "island"
    root.mkdir()
    (root / "demo.uefnproject").write_text(
        json.dumps(
            {
                "dataSets": {
                    "experimental": {
                        "pythonExperimental": {"bEnablePythonForProject": True},
                        "toolsets": {"bEnableToolsetsForProject": True},
                        "sceneGraph": {"bIsSceneGraphSystemAllowed": True},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    beta = read_uefn_beta_access(root)
    assert beta["ok"] is True
    assert beta["python_editor_scripting"] is True
    assert beta["uefn_mcp_toolsets"] is True
    assert beta["python_and_toolsets"] is True
    assert beta["listener_init_race"] is True
    assert "together" in beta["agent_note"].lower()
    assert "4200" in beta["agent_note"]


def test_read_beta_toolsets_only(tmp_path: Path) -> None:
    root = tmp_path / "island"
    root.mkdir()
    (root / "demo.uefnproject").write_text(
        json.dumps(
            {
                "dataSets": {
                    "experimental": {
                        "pythonExperimental": {"bEnablePythonForProject": False},
                        "toolsets": {"bEnableToolsetsForProject": True},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    beta = read_uefn_beta_access(root)
    assert beta["uefn_mcp_toolsets"] is True
    assert beta["python_and_toolsets"] is False
    assert beta["listener_init_race"] is False
