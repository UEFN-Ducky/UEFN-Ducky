"""The If / Expression language: JS-like, safe, null for unknown names."""

import math

import pytest

from backend.automations.expr import ExprError, check, evaluate


def test_validation_failure_only_runs_in_selected_branch():
    assert evaluate("ok ? value : fail('Upload did not complete')", {"ok": True, "value": 42}) == 42
    with pytest.raises(ExprError, match="Upload did not complete"):
        evaluate("ok ? value : fail('Upload did not complete')", {"ok": False})


@pytest.mark.parametrize("src, scope, expected", [
    ("score > 10", {"score": 12}, True),
    ("score > 10 && name.includes('duck')", {"score": 12, "name": "rubber duck"}, True),
    ("a || 'fallback'", {"a": ""}, "fallback"),
    ("items.length ? items[0] : 'none'", {"items": ["x", "y"]}, "x"),
    ("items.length ? items[0] : 'none'", {"items": []}, "none"),
    ("round(price * 1.2)", {"price": 10}, 12),
    ("round(2.345, 2)", {}, 2.35),
    ("'Hi ' + who + '!'", {"who": "Ducky"}, "Hi Ducky!"),
    ("1 + 2 * 3", {}, 7),
    ("(1 + 2) * 3", {}, 9),
    ("10 % 4", {}, 2),
    ("'5' == 5", {}, True),
    ("'5' === 5", {}, False),
    ("!done", {"done": False}, True),
    ("-x", {"x": 3}, -3),
    ("player.name", {"player": {"name": "Tas"}}, "Tas"),
    ("player['name']", {"player": {"name": "Tas"}}, "Tas"),
    ("missing.deep", {}, None),
    ("text.toUpperCase().trim()", {"text": " ok "}, "OK"),
    ("tags.join(', ')", {"tags": ["a", "b"]}, "a, b"),
    ("[1, 2, 3].includes(2)", {}, True),
    ("contains(list, 'b')", {"list": ["a", "b"]}, True),
    ("matches(code, '^E\\\\d+')", {"code": "E42"}, True),
    ("max(scores)", {"scores": [3, 9, 4]}, 9),
    ("len(name)", {"name": "duck"}, 4),
    ("number('3.5') + 1", {}, 4.5),
    ("'a' < 'b'", {}, True),
    ("null == undefined", {}, True),
    ("'line\\nbreak'", {}, "line\nbreak"),
    ("\"say \\\"hi\\\"\"", {}, 'say "hi"'),
])
def test_evaluates_like_javascript(src, scope, expected):
    assert evaluate(src, scope) == expected


def test_division_by_zero_gives_infinity_or_nan():
    assert evaluate("1 / 0") == math.inf
    assert math.isnan(evaluate("0 / 0"))


@pytest.mark.parametrize("src, message", [
    ("", "Write an expression"),
    ("score >", "ends too early"),
    ("a b", "Unexpected 'b'"),
    ("(1 + 2", "Expected ')'"),
    ("a @ b", "Unexpected '@'"),
])
def test_explains_syntax_errors(src, message):
    assert message in check(src)


def test_refuses_anything_that_is_not_an_expression():
    for src in ("__import__('os')", "open('x')", "exec('1')"):
        with pytest.raises(ExprError):
            evaluate(src)
    with pytest.raises(ExprError):
        evaluate("x.toUpperCase()", {"x": 5})  # no such method on a number
    assert check("a" * 2001) == "Keep expressions under 2000 characters"
