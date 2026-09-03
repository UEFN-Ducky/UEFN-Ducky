"""Heuristic Verse linter — one positive and one negative case per rule."""

from __future__ import annotations

import glob
import os

import pytest

from backend.tools.verse.compile_hints import hints_for
from backend.tools.verse.verse_lint import lint_verse, summarize_findings

TEMPLATE_DIR = r"C:\Users\tas13\Documents\GitHub\UEFN-Ducky\plugins\uefn-plugin-virtualpointer\templates"

DEVICE_USINGS = (
    "using { /Fortnite.com/Devices }\n"
    "using { /Fortnite.com/Playspaces }\n"
    "using { /Verse.org/Simulation }\n"
    "using { /Verse.org/SceneGraph }\n"
    "using { /Verse.org/SpatialMath }\n"
    "using { /Verse.org/Input }\n"
    "using { /Verse.org/Input/UI }\n"
    "using { /Verse.org/Colors }\n"
    "using { /Verse.org/Assets }\n"
    "using { /Fortnite.com/Characters }\n"
    "using { /Fortnite.com/UI }\n"
    "using { /UnrealEngine.com/Temporary/Diagnostics }\n\n"
)


def rules(src: str, **kw) -> list[str]:
    return [f["rule"] for f in lint_verse(DEVICE_USINGS + src, **kw)]


def errors(src: str, **kw) -> list[str]:
    return [f["rule"] for f in lint_verse(DEVICE_USINGS + src, **kw) if f["severity"] == "error"]


# L1 ------------------------------------------------------------------------


def test_no_rollback_in_if_condition_flagged():
    src = (
        "dev := class(creative_device):\n"
        "    Helper(X : int):int = X + 1\n"
        "    Go():void =\n"
        "        if (Helper(1) > 0):\n"
        '            Print("x")\n'
    )
    found = [f for f in lint_verse(DEVICE_USINGS + src) if f["rule"] == "no_rollback_in_failure_context"]
    assert len(found) == 1
    assert found[0]["severity"] == "error"
    assert "`Helper`" in found[0]["message"]


def test_no_rollback_in_brackets_and_if_head_flagged():
    src = (
        "dev := class(creative_device):\n"
        "    Idx():int = 0\n"
        "    Go(Arr : []int):void =\n"
        "        if (V := Arr[Idx()]):\n"
        '            Print("x")\n'
        "        if:\n"
        "            Idx() > 0\n"
        "        then:\n"
        '            Print("y")\n'
    )
    lines = [f["line"] for f in lint_verse(DEVICE_USINGS + src) if f["rule"] == "no_rollback_in_failure_context"]
    assert len(lines) == 2


def test_transacts_helper_and_local_binding_not_flagged():
    src = (
        "dev := class(creative_device):\n"
        "    Helper(X : int)<transacts>:int = X + 1\n"
        "    Plain(X : int):int = X\n"
        "    Go():void =\n"
        "        if (Helper(1) > 0):\n"
        '            Print("x")\n'
        "        V := Plain(1)\n"
        "        if (V > 0):\n"
        '            Print("y")\n'
    )
    assert "no_rollback_in_failure_context" not in rules(src)


# L2 ------------------------------------------------------------------------


def test_reserved_underscore_flagged():
    assert "reserved_underscore_binding" in errors("Go(Items : []int):void =\n    for (_ : Items):\n        Print(\"x\")\n")
    assert "reserved_underscore_binding" in errors("Go():void =\n    _ := 3\n")


def test_named_binding_not_flagged():
    assert "reserved_underscore_binding" not in rules("Go(Items : []int):void =\n    for (Item : Items):\n        Print(\"x\")\n")


# L3 ------------------------------------------------------------------------


def test_multiline_signature_flagged():
    src = "dev := class(creative_device):\n    Foo(\n        A : int,\n        B : int\n    ):void =\n        Print(\"x\")\n"
    assert "multiline_signature" in errors(src)


def test_one_line_signature_with_tuple_type_not_flagged():
    src = (
        "dev := class(creative_device):\n"
        "    OnZoomBegin(Arg : tuple(player, vector3)):void =\n"
        '        Print("x")\n'
        "    Go():void =\n"
        "        Result := Foo(\n"
        "            1)\n"
    )
    assert "multiline_signature" not in rules(src)


# L4 ------------------------------------------------------------------------


def test_shadowed_builtin_flagged():
    found = [f for f in lint_verse(DEVICE_USINGS + "Go(A : vector3, B : vector3):void =\n    Distance := 3.0\n") if f["rule"] == "shadowed_builtin"]
    assert found and found[0]["severity"] == "warning"
    assert "shadowed_builtin" in rules("dev := class(creative_device):\n    Go(Max : int):void =\n        Print(\"x\")\n")


def test_prefixed_names_not_flagged():
    assert "shadowed_builtin" not in rules("dev := class(creative_device):\n    MinScale : float = 1.0\n    Go():void =\n        X := Max(1.0, Min(2.0, 3.0))\n")


# L5 ------------------------------------------------------------------------


def test_field_initialiser_call_flagged():
    src = "dev := class(creative_device):\n    Cached : float = Sqrt(2.0)\n"
    assert "field_initialiser_call" in errors(src)


def test_archetype_field_and_method_body_not_flagged():
    src = (
        "dev := class(creative_device):\n"
        "    Pos : vector3 = vector3{}\n"
        "    var Subs : [player][]cancelable = map{}\n"
        "    OnBegin<override>()<suspends>:void =\n"
        "        X : float = Sqrt(2.0)\n"
    )
    assert "field_initialiser_call" not in rules(src)


# L6 ------------------------------------------------------------------------


def test_missing_using_flagged():
    src = "using { /Fortnite.com/Devices }\n\ndev := class(creative_device):\n    OnBegin<override>()<suspends>:void =\n        P : fort_playspace = GetPlayspace()\n"
    found = [f for f in lint_verse(src) if f["rule"] == "missing_using"]
    assert found and "/Fortnite.com/Playspaces" in found[0]["message"]


def test_present_using_not_flagged():
    src = "dev := class(creative_device):\n    OnBegin<override>()<suspends>:void =\n        P := GetPlayspace()\n        V := vector3{}\n"
    assert "missing_using" not in rules(src)


def test_bare_getplayspace_needs_no_import():
    src = "using { /Fortnite.com/Devices }\n\ndev := class(creative_device):\n    OnBegin<override>()<suspends>:void =\n        P := GetPlayspace()\n"
    assert "missing_using" not in [f["rule"] for f in lint_verse(src)]


# L7 ------------------------------------------------------------------------


def test_array_block_with_commas_flagged():
    src = "Go():void =\n    Pairs := array:\n        1, 2\n        3, 4\n"
    assert "array_block_commas" in errors(src)


def test_array_block_of_archetypes_not_flagged():
    src = (
        "Go():void =\n"
        "    Slots := array:\n"
        "        canvas_slot:\n"
        "            Anchors := anchors{Minimum := vector2{X := 0.0, Y := 0.0}, Maximum := vector2{X := 1.0, Y := 1.0}}\n"
        "            Widget := Btn\n"
        "    Flat := array{1, 2, 3}\n"
    )
    assert "array_block_commas" not in rules(src)


# L8 ------------------------------------------------------------------------


def test_c_style_comment_flagged():
    assert "c_style_comment" in errors("Go():void =\n    // not a comment\n    Print(\"x\")\n")


def test_hash_comment_and_url_in_string_not_flagged():
    assert "c_style_comment" not in rules("Go():void =\n    # fine // really\n    Print(\"http://x/*y\")\n")


# L9 ------------------------------------------------------------------------


def test_decides_with_set_flagged():
    src = "dev := class(creative_device):\n    var N : int = 0\n    Bump()<decides>:void =\n        set N += 1\n"
    assert "decides_set_without_transacts" in errors(src)


def test_decides_transacts_with_set_not_flagged():
    src = "dev := class(creative_device):\n    var N : int = 0\n    Bump()<decides><transacts>:void =\n        set N += 1\n"
    assert "decides_set_without_transacts" not in rules(src)


# L10 -----------------------------------------------------------------------


def test_known_bad_apis_flagged():
    src = (
        "dev := class(creative_device):\n"
        "    @editable\n"
        "    MyTimer : timer_device = timer_device{}\n"
        "    Flag : logic = true\n"
        "    Go():void =\n"
        "        MyTimer.Reset()\n"
        "        S := ToString(Flag)\n"
        "        L := Log10(2.0)\n"
        "        Char.MoveToLocation(vector3{})\n"
    )
    found = [f["line"] for f in lint_verse(DEVICE_USINGS + src) if f["rule"] == "unknown_api"]
    assert len(found) == 4


def test_real_apis_not_flagged():
    src = (
        "dev := class(creative_device):\n"
        "    @editable\n"
        "    MyTimer : timer_device = timer_device{}\n"
        "    Go(A : [string]int, B : [string]int):void =\n"
        "        MyTimer.ResetForAll()\n"
        "        MyTimer.Reset(Agent)\n"
        "        X := Abs(-1.0)\n"
        "        M := ConcatenateMaps(A, B)\n"
        "        S := ToString(3)\n"
    )
    assert "unknown_api" not in rules(src)


# L11 -----------------------------------------------------------------------


def test_decides_called_with_parens_flagged():
    src = "dev := class(creative_device):\n    Check()<decides>:void = true\n    Go():void =\n        Check()\n"
    assert "decides_called_with_parens" in errors(src)


def test_decides_called_with_brackets_not_flagged():
    src = "dev := class(creative_device):\n    Check()<decides>:void = true\n    Go():void =\n        if (Check[]):\n            Print(\"x\")\n"
    assert "decides_called_with_parens" not in rules(src)


# L12 -----------------------------------------------------------------------


def test_int_division_flagged():
    src = "Go(A : int, B : int):void =\n    C := A / B\n    D := 7 / 2\n"
    found = [f for f in lint_verse(DEVICE_USINGS + src) if f["rule"] == "int_division"]
    assert len(found) == 2 and all(f["severity"] == "warning" for f in found)


def test_float_division_and_floor_not_flagged():
    src = "Go(A : float, B : int):void =\n    C := A / 2.0\n    if (D := Floor[B / 2]):\n        Print(\"x\")\n"
    assert "int_division" not in rules(src)


# L13 -----------------------------------------------------------------------


def test_lone_braces_under_if_flagged():
    src = "Go(M : [int]int):void =\n    if (set M[1] = 2):\n        {}\n    else:\n        {}\n"
    found = [f for f in lint_verse(DEVICE_USINGS + src) if f["rule"] == "lone_braces_block"]
    assert len(found) == 2 and found[0]["severity"] == "error"


def test_braces_on_head_line_not_flagged():
    src = "Go(M : [int]int):void =\n    if (set M[1] = 2) {}\n    if (M[1] > 0):\n        Print(\"x\")\n"
    assert "lone_braces_block" not in rules(src)


# L14 -----------------------------------------------------------------------


def test_module_folder_name_shadow_flagged():
    src = "dev := class(creative_device):\n    var Progression : ?xp_awarder = false\n"
    found = [f for f in lint_verse(DEVICE_USINGS + src, module_names={"Progression"}) if f["rule"] == "module_name_shadow"]
    assert found and found[0]["severity"] == "warning"


def test_module_declaration_and_no_modules_not_flagged():
    src = "Progression := module:\n    Go():void =\n        Print(\"x\")\n"
    assert "module_name_shadow" not in rules(src, module_names={"Progression"})
    assert "module_name_shadow" not in rules("dev := class(creative_device):\n    var Progression : int = 0\n")


# L15 -----------------------------------------------------------------------


def test_ui_widgets_without_fortnite_ui_using_flagged():
    src = "using { /UnrealEngine.com/Temporary/UI }\n\nBuild():canvas =\n    canvas{}\n"
    found = [f for f in lint_verse(src) if f["rule"] == "missing_fortnite_ui_using"]
    assert found and found[0]["severity"] == "error"


def test_ui_widgets_with_fortnite_ui_using_not_flagged():
    src = "Build():canvas =\n    Slot := canvas_slot{}\n    canvas{}\n"
    assert "missing_fortnite_ui_using" not in rules(src)


# Cross-cutting ---------------------------------------------------------------


def test_digest_paths_and_garbage_never_raise():
    assert lint_verse("anything", "Foo.digest.verse") == []
    assert isinstance(lint_verse("if (\n\"unterminated\n(((", "x.verse"), list)


def test_summarize_findings_counts():
    findings = lint_verse(DEVICE_USINGS + "Go():void =\n    // c\n    Distance := 1.0\n")
    summary = summarize_findings(findings)
    assert summary["errors"] == 1 and summary["warnings"] == 1
    assert summary["findings"] == findings


def test_hints_for_parses_codes():
    hints = hints_for("Script error 3512: x\nScript error 3512: y\nScript error 4242: z")
    assert [(h["code"], h["count"]) for h in hints] == [("3512", 2), ("4242", 1)]
    assert hints[0]["subskill"] == "compile_errors" and "transacts" in hints[0]["hint"]
    assert hints_for("") == []


@pytest.mark.skipif(not os.path.isdir(TEMPLATE_DIR), reason="virtualpointer templates not on this machine")
def test_shipped_templates_have_no_error_findings():
    paths = sorted(glob.glob(os.path.join(TEMPLATE_DIR, "*.verse")))
    assert paths
    for path in paths:
        with open(path, encoding="utf-8") as f:
            src = f.read()
        bad = [f for f in lint_verse(src, path) if f["severity"] == "error"]
        assert not bad, f"{os.path.basename(path)}: {bad}"


def test_l16_concrete_subtype_editable_warns() -> None:
    src = (
        "using { /Verse.org/SceneGraph }\n"
        "g := class(component):\n"
        "    @editable Items : []concrete_subtype(entity) = array{}\n"
    )
    rules = [f["rule"] for f in lint_verse(src)]
    assert "concrete_subtype_editable" in rules


def test_l16_concrete_subtype_not_editable_is_silent() -> None:
    src = (
        "using { /Verse.org/SceneGraph }\n"
        "g := class(component):\n"
        "    Items : []concrete_subtype(entity) = array{}\n"
    )
    assert not [f for f in lint_verse(src) if f["rule"] == "concrete_subtype_editable"]
