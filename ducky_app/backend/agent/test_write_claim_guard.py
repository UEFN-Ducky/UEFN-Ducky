"""Host catches invented file-write reports (Ollama Qwen-style inventory lies)."""

from types import SimpleNamespace

from backend.agent.prompt import EVIDENCE_RULE, _rules_body
from backend.agent.write_claim_guard import (
    fake_write_warning,
    record_write_from_tool,
    write_path_from_tool,
)


def test_write_path_unwraps_ducky_call_tool() -> None:
    assert (
        write_path_from_tool(
            "ducky_call_tool",
            {"name": "workspace_write_file", "arguments": {"relative_path": "Verse/Shop/a.verse"}},
        )
        == "Verse/Shop/a.verse"
    )


def test_warns_when_inventory_lists_unwritten_files() -> None:
    msg = {
        "content": (
            "The files were written!\n\n"
            "## Inventory — Content/Verse/VirtualPointer/\n"
            "- module_declarations.verse / `Content/Verse/module_declarations.verse`\n"
            "- virtual_pointer_device.verse / `Content/Verse/VirtualPointer/virtual_pointer_device.verse`\n"
        )
    }
    warning = fake_write_warning(msg, set())
    assert warning
    assert "module_declarations.verse" in warning
    assert "(nothing)" in warning


def test_silent_when_write_tool_landed() -> None:
    msg = {
        "content": (
            "Wrote the device.\n"
            "## Inventory\n"
            "- **Verse device** / `Verse/VirtualPointer/virtual_pointer_device.verse`"
        )
    }
    assert (
        fake_write_warning(msg, {"Verse/VirtualPointer/virtual_pointer_device.verse"}) is None
    )


def test_silent_when_no_write_claim() -> None:
    msg = {"content": "Listener is offline. I did not write anything."}
    assert fake_write_warning(msg, set()) is None


def test_record_write_success_and_failure() -> None:
    written: set[str] = set()
    failures: list[str] = []
    record_write_from_tool(
        SimpleNamespace(
            name="workspace_write_file",
            arguments={"relative_path": "Verse/A.verse"},
            status="success",
            result={"ok": True},
        ),
        written,
        failures,
    )
    record_write_from_tool(
        SimpleNamespace(
            name="workspace_write_file",
            arguments={"relative_path": "Verse/B.verse"},
            status="error",
            result={"ok": False},
        ),
        written,
        failures,
    )
    assert written == {"Verse/A.verse"}
    assert failures == ["Verse/B.verse"]


def test_local_slim_rules_lead_with_evidence_not_inventory_template() -> None:
    slim = _rules_body(4200, local_slim=True)
    fat = _rules_body(4200, local_slim=False)
    assert EVIDENCE_RULE.strip() in slim
    assert slim.index("Evidence (HARD)") < slim.index("Response formatting (local)")
    assert "## Inventory —" not in slim
    assert "## Inventory —" in fat
