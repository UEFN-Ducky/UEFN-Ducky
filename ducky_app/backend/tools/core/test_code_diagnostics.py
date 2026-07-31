"""Assert-based parser self-check for host code diagnostics."""

from __future__ import annotations

from backend.tools.core.code_diagnostics_lib import (
    parse_cargo_json,
    parse_clang,
    parse_eslint_json,
    parse_php_l,
    parse_tsc,
)


def test_parsers() -> None:
    tsc = parse_tsc('src/app.ts(10,5): error TS2322: Type "string" is not assignable to type "number".')
    assert len(tsc) == 1 and tsc[0]["line"] == 10 and tsc[0]["source"] == "tsc"

    eslint = parse_eslint_json(
        '[{"filePath":"a.js","messages":[{"line":2,"column":3,"severity":2,"ruleId":"no-undef","message":"x is not defined"}]}]'
    )
    assert len(eslint) == 1 and eslint[0]["severity"] == "error"

    cargo_line = (
        '{"reason":"compiler-message","message":{"level":"error","message":"missing semicolon",'
        '"spans":[{"file_name":"src/main.rs","line_start":4,"column_start":8,"is_primary":true}]}}'
    )
    cargo = parse_cargo_json(cargo_line)
    assert len(cargo) == 1 and cargo[0]["path"] == "src/main.rs" and cargo[0]["line"] == 4

    php = parse_php_l("Parse error: syntax error, unexpected '}' in /tmp/x.php on line 12")
    assert len(php) == 1 and php[0]["line"] == 12

    clang = parse_clang("foo.cpp:3:1: error: expected ';' after expression")
    assert len(clang) == 1 and clang[0]["column"] == 1 and clang[0]["source"] == "clang"
