"""Which Custom code may run on its own, on this PC.

Code an agent wrote doesn't run unattended until a person has run or tested it once,
or saved it themselves, or the chat that runs it said "Allow everything". Each approval
is for one exact version of one node's code (its sha256), so any later change asks
again. Approvals are per PC and never sync: ``workspace_state`` rows in the database,
else one JSON file next to the app's other local state.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from backend.store.switch import use_db

_TABLE = "workspace_state"
_FILE = "workflow_code_approvals.json"
_KEEP_PER_NODE = 8
_LOCK = threading.RLock()

TEAM_REFUSAL = "Custom code runs in Local workflows for now."
REVIEW = ("Review this code before it runs: it was changed by an agent. "
          "Open the node and press Review, or run it once yourself.")


def _key(workflow_id: str) -> str:
    return f"code_approval:{workflow_id}"


def _approvals_file() -> Path:
    from frontend.app_paths import resolve_app_data_dir

    return resolve_app_data_dir(for_write=True) / _FILE


def _read_file() -> dict[str, Any]:
    path = _approvals_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def _load(workflow_id: str) -> dict[str, Any]:
    """{node_id: {code_sha: {"by", "at"}}} for one workflow."""
    if use_db(_TABLE):
        from backend.store.repos import kv

        doc = kv.get_doc(_TABLE, _key(workflow_id))
    else:
        doc = _read_file().get(workflow_id)
    return doc if isinstance(doc, dict) else {}


def _store(workflow_id: str, doc: dict[str, Any]) -> None:
    if use_db(_TABLE):
        from backend.store.repos import kv

        if doc:
            kv.set_doc(_TABLE, _key(workflow_id), doc)
        else:
            kv.delete_doc(_TABLE, _key(workflow_id))
        return
    data = _read_file()
    if doc:
        data[workflow_id] = doc
    else:
        data.pop(workflow_id, None)
    path = _approvals_file()
    temporary = path.with_suffix(f".{uuid.uuid4()}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def approve(workflow_id: str, node_id: str, code_sha: str, by: str) -> None:
    """Remember that this exact code of this node may run on its own here."""
    wid, nid, sha = str(workflow_id or "").strip(), str(node_id or "").strip(), str(code_sha or "").strip()
    if not wid or not nid or not sha:
        return
    with _LOCK:
        doc = _load(wid)
        node = doc.get(nid) if isinstance(doc.get(nid), dict) else {}
        node[sha] = {"by": "person" if by == "person" else "chat", "at": time.time()}
        newest = sorted(node.items(), key=lambda kv: float((kv[1] or {}).get("at") or 0))[-_KEEP_PER_NODE:]
        doc[nid] = dict(newest)
        _store(wid, doc)


def is_approved(workflow_id: str, node_id: str, code_sha: str) -> bool:
    if not code_sha:
        return False
    with _LOCK:
        node = _load(str(workflow_id or "").strip()).get(str(node_id or "").strip())
    return isinstance(node, dict) and str(code_sha) in node


def approval(workflow_id: str, node_id: str, code_sha: str) -> dict[str, Any] | None:
    """Who approved this code and when, or None."""
    with _LOCK:
        node = _load(str(workflow_id or "").strip()).get(str(node_id or "").strip())
    row = node.get(str(code_sha)) if isinstance(node, dict) else None
    return dict(row) if isinstance(row, dict) else None


def forget(workflow_id: str) -> None:
    with _LOCK:
        _store(str(workflow_id or "").strip(), {})


def is_local(wf: dict[str, Any]) -> bool:
    return str((wf.get("owner") or {}).get("kind") or "local") == "local"


def node_sha(node: dict[str, Any]) -> str:
    """The sha of the code the node holds now (never trust a stale stored sha)."""
    from backend.automations.code_check import code_sha

    cfg = node.get("config") if isinstance(node.get("config"), dict) else {}
    code = cfg.get("code")
    return code_sha(code) if isinstance(code, str) else ""


def _caller_allows_everything(ctx: dict[str, Any]) -> bool:
    caller = str(ctx.get("caller_conv_id") or "").strip()
    if not caller:
        return False
    try:
        from backend.tools.panel.permission_prompt import allows_everything

        return bool(allows_everything(caller))
    except Exception:
        return False


def gate(wf: dict[str, Any], node: dict[str, Any], ctx: dict[str, Any]) -> str | None:
    """None when this code node may run now, else why not (the step fails with it)."""
    if not is_local(wf):
        return TEAM_REFUSAL
    wid, nid = str(wf.get("id") or ""), str(node.get("id") or "")
    sha = node_sha(node)
    if is_approved(wid, nid, sha):
        return None
    if ctx.get("_person_started"):
        approve(wid, nid, sha, "person")
        return None
    if _caller_allows_everything(ctx):
        approve(wid, nid, sha, "chat")
        return None
    return REVIEW


def approve_workflow(wf: dict[str, Any], by: str) -> list[str]:
    """Approve the code every Custom code node of a Local workflow holds now (a person
    saved it, or a chat that allows everything did). Returns the node ids."""
    if not wf or not is_local(wf):
        return []
    done: list[str] = []
    for node in (wf.get("graph") or {}).get("nodes") or []:
        if isinstance(node, dict) and node.get("type") == "code.js":
            sha = node_sha(node)
            if sha:
                approve(str(wf.get("id") or ""), str(node.get("id") or ""), sha, by)
                done.append(str(node.get("id") or ""))
    return done
