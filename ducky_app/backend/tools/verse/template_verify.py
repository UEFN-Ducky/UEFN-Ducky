"""verse_template_verify — build every installed plugin's Verse templates in the open project.

Ships every ``verse.templates`` pack/file into ``Content/Verse/<folder>/`` of the
open UEFN project, runs the real Verse build, reports errors per template file,
and removes what it copied. Nothing already in the project is overwritten.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from backend.tools.support.plugin_gate import plugin_mcp_tool
from backend.util.json_util import tool_json

SINGLES_FOLDER = "DuckyVerifySingles"
_ERR_RE = re.compile(r"^(?P<path>.+?\.verse)\((?P<line>\d+),\d+, \d+,\d+\) : Script (?P<kind>error|warning) (?P<code>\d+): (?P<msg>.*)$")


def _project_verse_root() -> Path:
    from frontend.ui_web.project_files import _project_root

    root = _project_root()
    verse = root / "Content" / "Verse"
    if not verse.is_dir():
        raise ValueError(f"No Content/Verse folder under {root} — open a UEFN project first.")
    return verse


def _templates(template_ids: set[str]) -> list[dict[str, Any]]:
    from backend.uefn_plugins.host import get_contributions

    rows = [r for r in (get_contributions().get("verse_templates") or []) if isinstance(r, dict)]
    if template_ids:
        rows = [r for r in rows if str(r.get("id") or "") in template_ids]
    return rows


def _files_for(row: dict[str, Any]) -> list[tuple[str, str]]:
    """(relative path under the pack folder, content) for one template row."""
    files = row.get("files") or []
    if isinstance(files, list) and files:
        return [
            (str(f.get("path") or ""), str(f.get("content") or ""))
            for f in files
            if isinstance(f, dict) and f.get("path")
        ]
    name = str(row.get("file") or f"{row.get('id')}.verse").replace("\\", "/").split("/")[-1]
    return [(name, str(row.get("content") or ""))]


def _stage(verse_root: Path, rows: list[dict[str, Any]]) -> tuple[dict[str, str], list[Path], list[Path], list[str]]:
    """Write template files. Returns (abs path -> 'template_id:rel', created files, created dirs, skipped)."""
    written: dict[str, str] = {}
    created_files: list[Path] = []
    created_dirs: list[Path] = []
    skipped: list[str] = []
    for row in rows:
        tid = str(row.get("id") or "")
        folder = str(row.get("folder") or "").strip().replace("\\", "/").strip("/") or SINGLES_FOLDER
        if ".." in folder.split("/"):
            skipped.append(f"{tid}: unsafe folder {folder!r}")
            continue
        pack_dir = verse_root / folder
        for rel, body in _files_for(row):
            rel = rel.replace("\\", "/").strip("/")
            if not rel or ".." in rel.split("/"):
                skipped.append(f"{tid}: unsafe path {rel!r}")
                continue
            target = pack_dir / rel
            if target.exists():
                skipped.append(f"{tid}: {folder}/{rel} already exists in the project — left untouched")
                continue
            # record dirs we create so cleanup removes only ours
            parent = target.parent
            chain: list[Path] = []
            while not parent.exists() and parent != verse_root:
                chain.append(parent)
                parent = parent.parent
            target.parent.mkdir(parents=True, exist_ok=True)
            created_dirs.extend(reversed(chain))
            target.write_text(body, encoding="utf-8")
            created_files.append(target)
            written[str(target.resolve()).lower().replace("\\", "/")] = f"{tid}:{folder}/{rel}"
    return written, created_files, created_dirs, skipped


def _cleanup(created_files: list[Path], created_dirs: list[Path]) -> None:
    for f in created_files:
        try:
            f.unlink()
        except OSError:
            pass
    for d in sorted(set(created_dirs), key=lambda p: len(str(p)), reverse=True):
        try:
            if d.is_dir() and not any(d.iterdir()):
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def parse_compile_message(message: str, written: dict[str, str]) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Map compiler lines onto the staged template files."""
    per_file: dict[str, list[dict[str, Any]]] = {}
    other: list[str] = []
    for line in (message or "").splitlines():
        m = _ERR_RE.match(line.strip())
        if not m:
            continue
        key = m.group("path").replace("\\", "/").lower()
        label = written.get(key)
        entry = {"line": int(m.group("line")), "kind": m.group("kind"), "code": m.group("code"), "message": m.group("msg")[:300]}
        if label:
            per_file.setdefault(label, []).append(entry)
        else:
            other.append(line.strip()[:300])
    return per_file, other


@plugin_mcp_tool("verse")
def verse_template_verify(template_ids: str = "", cleanup: bool = True, pretty: bool = False) -> str:
    """Build every installed plugin's Verse template pack in the open UEFN project and report per-file compile errors.

    Stages each ``verse.templates`` entry under ``Content/Verse/<pack folder>/`` (single files under
    ``Verse/DuckyVerifySingles/``), runs ``workspace_compile_verse`` (UEFN must be open), maps every
    ``Script error`` back to the template that caused it, then removes only the files it copied.
    Files that already exist in the project are never overwritten. ``template_ids`` = comma list
    to verify a subset. Use before Store-publishing a plugin that ships Verse.
    """
    ids = {s.strip() for s in (template_ids or "").split(",") if s.strip()}
    rows = _templates(ids)
    if not rows:
        return tool_json({"ok": False, "error": "no verse templates found for the given ids"}, pretty=pretty)
    verse_root = _project_verse_root()
    written, created_files, created_dirs, skipped = _stage(verse_root, rows)
    if not written:
        return tool_json({"ok": False, "error": "nothing staged", "skipped": skipped}, pretty=pretty)
    result: dict[str, Any] = {"staged": len(written), "skipped": skipped, "templates": sorted({r.get("id") for r in rows})}
    try:
        from backend.tools.verse.verse_diagnostics import workspace_compile_verse

        raw = workspace_compile_verse()
        payload = json.loads(raw) if isinstance(raw, str) else raw
        compile_info = payload.get("compile") if isinstance(payload, dict) else {}
        message = str((compile_info or {}).get("message") or "")
        per_file, other = parse_compile_message(message, written)
        num_errors = int((compile_info or {}).get("numErrors") or 0)
        failing = {k: v for k, v in per_file.items() if any(e["kind"] == "error" for e in v)}
        result.update(
            {
                "ok": num_errors == 0,
                "numErrors": num_errors,
                "numWarnings": int((compile_info or {}).get("numWarnings") or 0),
                "failing_templates": sorted({k.split(":", 1)[0] for k in failing}),
                "per_file": per_file,
                "errors_outside_templates": other,
            }
        )
        if failing:
            result["next"] = (
                "Fix the listed template files in the plugin source (skill_read_subskill('verse','compile_errors') "
                "explains each code), rebuild the plugin, re-run verse_template_verify."
            )
    except Exception as exc:  # noqa: BLE001 - surface any failure but always clean up
        result.update({"ok": False, "error": f"compile failed to run: {exc}"})
    finally:
        if cleanup:
            _cleanup(created_files, created_dirs)
            result["cleaned_up"] = len(created_files)
        else:
            result["left_in_project"] = sorted(written.values())
    return tool_json(result, pretty=pretty)
