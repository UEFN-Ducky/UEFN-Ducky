"""No product subprocess call decodes its output with the locale codec and no ``errors=``.

Nov 16 2026: py.unicodedecodeerror on 1.2.501 with no Ducky function attached. With
``text=True`` and no ``errors``, subprocess decodes in its own reader thread with the
Windows code page: git's UTF-8 output with a Cyrillic path (0x9D is undefined in cp1252)
raises there, outside every try in our code. 22 calls had it.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def test_every_text_subprocess_call_says_what_to_do_with_undecodable_bytes() -> None:
    bad: list[str] = []
    for path in sorted(ROOT.rglob("*.py")):
        if path.name.startswith("test_") or "node_modules" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            kw = {k.arg: k.value for k in node.keywords if k.arg}
            text = kw.get("text") or kw.get("universal_newlines")
            if isinstance(text, ast.Constant) and text.value is True and "errors" not in kw:
                bad.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
    assert not bad, 'add errors="replace" (and encoding="utf-8" for git): ' + ", ".join(bad)
