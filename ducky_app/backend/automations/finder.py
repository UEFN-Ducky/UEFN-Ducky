"""Workflows first: before an agent does a task tool by tool, it looks here for a saved
workflow that already does it, or a template that does most of it.

Plain word matching over names, descriptions, folders, categories and (for
templates) the kinds of node in the graph, with a few synonyms so "3D model",
"mesh" and "prop" find each other."""

from __future__ import annotations

import math
import re
from typing import Any

_STOP = frozenset(("a an the and or to of for in on into with from by me my i you your it this that please make do can could would "
                   "want need fix put get use run build create add set help new some all just like using").split())
# Words that mean the same thing for this search.
_SYNONYMS = {
    "image": "picture", "img": "picture", "photo": "picture", "pic": "picture", "png": "picture", "sprite": "picture", "texture": "picture",
    "mesh": "3d", "model": "3d", "prop": "3d", "glb": "3d", "fbx": "3d", "asset": "3d",
    "rig": "character", "rigged": "character", "animate": "animation", "anim": "animation", "anims": "animation", "animations": "animation",
    "playtest": "test", "testing": "test", "tests": "test",
    "doc": "pdf", "document": "pdf",
    "bg": "background",
    "uefn": "uefn", "fortnite": "uefn", "island": "uefn",
}


def words(text: str) -> list[str]:
    out: list[str] = []
    for raw in re.findall(r"[a-z0-9]+", str(text or "").lower()):
        word = raw[:-1] if len(raw) > 3 and raw.endswith("s") and not raw.endswith("ss") else raw
        word = _SYNONYMS.get(raw, _SYNONYMS.get(word, word))
        if word not in _STOP and word not in out:
            out.append(word)
    return out


def _node_words(graph: Any) -> str:
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    return " ".join(f"{n.get('type', '')} {n.get('label', '')}".replace(".", " ").replace("_", " ") for n in nodes or [] if isinstance(n, dict))


def _template_words(row: dict[str, Any]) -> str:
    """Node words of a template: its graph, or every workflow of a folder template (and their names)."""
    bundle = row.get("bundle")
    if isinstance(bundle, dict):
        rows = [w for w in bundle.get("workflows") or [] if isinstance(w, dict)]
        return " ".join(f"{w.get('name', '')} {_node_words(w.get('graph'))}" for w in rows)
    return _node_words(row.get("graph"))


def _score(task: list[str], fields: list[tuple[str, int]], rarity: dict[str, float]) -> tuple[float, list[str]]:
    """Field weight × how rare the word is among the candidates (a word every template
    has, like "test", says less than "income")."""
    score, hits = 0.0, []
    for text, weight in fields:
        have = set(words(text))
        for word in task:
            if word in have:
                score += weight * rarity.get(word, 1.0)
                if word not in hits:
                    hits.append(word)
    return score, hits


def _rarity(task: list[str], texts: list[str]) -> dict[str, float]:
    total = max(1, len(texts))
    seen = [set(words(text)) for text in texts]
    return {word: 1.0 + math.log(total / max(1, sum(1 for have in seen if word in have))) for word in task}


def find(task: str, workflows: list[dict[str, Any]], templates: list[dict[str, Any]], limit: int = 6) -> dict[str, Any]:
    """Saved workflows and templates that fit ``task``, best first."""
    want = words(task)
    if not want:
        return {"workflows": [], "templates": [], "words": []}
    wf_fields = [[(str(row.get("name") or ""), 3), (str(row.get("description") or ""), 1),
                  (str(row.get("folder") or ""), 2), (str((row.get("trigger") or {}).get("label") or ""), 1)] for row in workflows]
    tpl_fields = [[(str(row.get("name") or ""), 3), (str(row.get("description") or ""), 1),
                   (str(row.get("category") or ""), 2), (_template_words(row), 1)] for row in templates]
    rarity = _rarity(want, [" ".join(text for text, _w in fields) for fields in wf_fields + tpl_fields])
    found_wf = []
    for row, fields in zip(workflows, wf_fields):
        score, hits = _score(want, fields, rarity)
        if score:
            found_wf.append((score, hits, row))
    found_tpl = []
    for row, fields in zip(templates, tpl_fields):
        score, hits = _score(want, fields, rarity)
        if score:
            found_tpl.append((score, hits, row))
    need = max(1, (len(want) + 1) // 2)  # at least half the task's words (one for short tasks)
    found_wf = [item for item in found_wf if len(item[1]) >= need]
    found_tpl = [item for item in found_tpl if len(item[1]) >= need]
    found_wf.sort(key=lambda item: -item[0])
    found_tpl.sort(key=lambda item: -item[0])
    return {
        "words": want,
        "workflows": [{"id": row.get("id"), "name": row.get("name"), "description": row.get("description") or "",
                       "folder": row.get("folder") or "", "owner": (row.get("owner") or {}).get("label") or "Local",
                       "enabled": row.get("enabled", True), "matched": hits} for _score_, hits, row in found_wf[:limit]],
        "templates": [{"id": row.get("id"), "name": row.get("name"), "category": row.get("category") or "",
                       "shape": row.get("shape") or "workflow", "workflow_count": row.get("workflow_count") or 1,
                       "description": row.get("description") or "", "ready": row.get("ready", True),
                       "missing_plugins": row.get("missing_plugins") or [], "matched": hits} for _score_, hits, row in found_tpl[:limit]],
    }
