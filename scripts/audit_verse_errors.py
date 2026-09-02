"""Mine Claude Code transcripts for UEFN Verse compile errors and mcp__uefn tool failures.

Dependency-free. Reads ``~/.claude/projects/*/*.jsonl``, pairs every
``tool_use`` with its ``tool_result`` and prints:

* ``Script error NNNN`` codes seen in workspace_compile_verse /
  workspace_list_verse_errors results, with a sample message each
* mcp__uefn tool failures (``is_error`` or ``"ok": false``) by tool

Usage::

    python scripts/audit_verse_errors.py [--days N] [--project SUBSTR] [--top N]
    python scripts/audit_verse_errors.py --stats      # summarize() from verse_stats
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

VERSE_RESULT_TOOLS = {
    "mcp__uefn__workspace_compile_verse",
    "mcp__uefn__workspace_list_verse_errors",
}
CODE_RE = re.compile(r"Script error (\d+)(?::?\s*([^\"\\\n]{0,140}))?")
OK_FALSE_RE = re.compile(r'\\?"ok\\?"\s*:\s*false')
VERSE_FILE_RE = re.compile(r"[\w\-./\\]+\.verse\b")


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    if content is None:
        return ""
    return json.dumps(content)


def _iter_blocks(record: dict):
    message = record.get("message")
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict):
            yield block


class Audit:
    def __init__(self) -> None:
        self.calls: Counter = Counter()
        self.failures: Counter = Counter()
        self.codes: Counter = Counter()
        self.samples: dict[str, str] = {}
        self.files: Counter = Counter()
        self.sessions = 0
        self.verse_results = 0

    def scan_file(self, path: Path) -> None:
        names: dict[str, str] = {}
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return
        self.sessions += 1
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            for block in _iter_blocks(record):
                kind = block.get("type")
                if kind == "tool_use":
                    name = str(block.get("name") or "")
                    names[str(block.get("id") or "")] = name
                    if name.startswith("mcp__uefn__"):
                        self.calls[name] += 1
                elif kind == "tool_result":
                    name = names.get(str(block.get("tool_use_id") or ""), "")
                    if not name.startswith("mcp__uefn__"):
                        continue
                    text = _text_of(block.get("content"))
                    if block.get("is_error") or OK_FALSE_RE.search(text):
                        self.failures[name] += 1
                    if name in VERSE_RESULT_TOOLS:
                        self._scan_verse_result(text)

    def _scan_verse_result(self, text: str) -> None:
        found = CODE_RE.findall(text)
        if not found:
            return
        self.verse_results += 1
        for code, msg in found:
            self.codes[code] += 1
            msg = (msg or "").strip()
            if msg and code not in self.samples:
                self.samples[code] = msg
        for f in set(VERSE_FILE_RE.findall(text)):
            if "digest" not in f.lower():
                self.files[f] += 1


def _table(rows: list[list[str]], headers: list[str]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    fmt = "  ".join("{:<" + str(w) + "}" for w in widths)
    out = [fmt.format(*headers), fmt.format(*["-" * w for w in widths])]
    out.extend(fmt.format(*row) for row in rows)
    return "\n".join(out)


def run_audit(days: int, project: str, top: int) -> int:
    root = Path.home() / ".claude" / "projects"
    since = time.time() - days * 86400.0 if days > 0 else 0.0
    audit = Audit()
    for path in sorted(root.glob("*/*.jsonl")):
        if project and project.lower() not in path.parent.name.lower():
            continue
        try:
            if path.stat().st_mtime < since:
                continue
        except OSError:
            continue
        audit.scan_file(path)

    print(f"Transcripts scanned: {audit.sessions} under {root}")
    print(f"Verse results with Script errors: {audit.verse_results}; codes total: {sum(audit.codes.values())}")
    print()
    print("Compile error codes")
    rows = [[code, str(n), audit.samples.get(code, "")[:90]] for code, n in audit.codes.most_common(top)]
    print(_table(rows, ["code", "count", "sample message"]) if rows else "  (none)")
    print()
    print("Files named in error output")
    rows = [[f[-70:], str(n)] for f, n in audit.files.most_common(top)]
    print(_table(rows, ["file", "count"]) if rows else "  (none)")
    print()
    print("mcp__uefn tool failures (is_error or ok:false)")
    rows = []
    for tool, n in audit.failures.most_common(top):
        calls = audit.calls.get(tool, 0)
        pct = f"{100.0 * n / calls:.0f}%" if calls else "?"
        rows.append([tool.replace("mcp__uefn__", ""), str(n), str(calls), pct])
    print(_table(rows, ["tool", "failures", "calls", "rate"]) if rows else "  (none)")
    return 0


def run_stats(days: int) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ducky_app"))
    from backend.tools.verse.verse_stats import summarize

    print(json.dumps(summarize(days=days if days > 0 else 30), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=0, help="only transcripts modified in the last N days (0 = all)")
    parser.add_argument("--project", default="", help="substring filter on the project folder name")
    parser.add_argument("--top", type=int, default=25, help="rows per table")
    parser.add_argument("--stats", action="store_true", help="print verse_stats.summarize() instead of mining transcripts")
    args = parser.parse_args(argv)
    if args.stats:
        return run_stats(args.days)
    return run_audit(args.days, args.project, args.top)


if __name__ == "__main__":
    raise SystemExit(main())
