"""One-shot AI draft for a new skill pack (SKILL.md + references/*.md)."""

from __future__ import annotations

import asyncio
import json
import re
import threading
from typing import Any

from backend.agent.model_pricing import infer_provider
from backend.agent.providers import make_provider
from backend.agent.providers.base import ProviderMessage, StreamEventKind
from backend.agent.secrets import get_key

_SYSTEM = """You design Agent Skills packs for UEFN-Ducky (Fortnite UEFN AI assistant).

Layout on disk:
- SKILL.md — core operator guidance (frontmatter: name, description, metadata.label, metadata.version)
- references/<id>.md — optional subskills (frontmatter: description, metadata.label, metadata.default_enabled)

Return ONLY valid JSON (no markdown fences, no commentary) matching:
{
  "label": "Human pack name",
  "description": "One-line summary for frontmatter",
  "core_markdown": "# Title\\n\\nMarkdown body for SKILL.md (no frontmatter in this field)",
  "files": [
    {
      "id": "snake_case_id",
      "label": "Human file label",
      "description": "One-line subskill description",
      "markdown": "# Title\\n\\nMarkdown body (no frontmatter)"
    }
  ]
}

Rules:
- 1–4 reference files; split topics that agents load on demand
- ids: lowercase snake_case, a-z0-9_, max 48 chars, start with a letter
- core_markdown: actionable rules the agent follows every turn when pack is enabled
- reference files: deeper guides (troubleshooting, workflows, examples)
- Write concise, operator-focused markdown — not essays
"""


def _strip_json_fences(text: str) -> str:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, count=1)
        raw = re.sub(r"\s*```\s*$", "", raw)
    return raw.strip()


def _normalize_file(entry: dict[str, Any]) -> dict[str, Any] | None:
    fid = re.sub(r"[^a-z0-9_]", "_", str(entry.get("id") or "").lower().strip())
    fid = re.sub(r"_+", "_", fid).strip("_")[:48]
    if not fid or not fid[0].isalpha():
        label = str(entry.get("label") or "").strip()
        fid = re.sub(r"[^a-z0-9_]", "_", label.lower())[:48] or "reference"
        if not fid[0].isalpha():
            fid = f"ref_{fid}"[:48]
    label = str(entry.get("label") or fid.replace("_", " ").title()).strip()
    desc = str(entry.get("description") or label).strip()
    md = str(entry.get("markdown") or f"# {label}\n").strip()
    if not md.startswith("#"):
        md = f"# {label}\n\n{md}"
    return {"id": fid, "label": label, "description": desc, "markdown": md}


def _parse_draft(text: str) -> dict[str, Any]:
    raw = _strip_json_fences(text)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Draft must be a JSON object")
    label = str(data.get("label") or "").strip()
    if not label:
        raise ValueError("Draft missing label")
    description = str(data.get("description") or label).strip()
    core = str(data.get("core_markdown") or "").strip()
    if not core:
        core = f"# {label}\n\n{description}\n"
    elif not core.startswith("#"):
        core = f"# {label}\n\n{core}"
    files_in = data.get("files")
    files: list[dict[str, Any]] = []
    if isinstance(files_in, list):
        for item in files_in:
            if isinstance(item, dict):
                norm = _normalize_file(item)
                if norm:
                    files.append(norm)
    return {
        "label": label,
        "description": description,
        "core_markdown": core,
        "files": files,
    }


async def _complete_text(*, provider_name: str, model: str, system: str, user: str) -> str:
    api_key = get_key(provider_name)
    if not api_key:
        raise ValueError(f"No API key for {provider_name}")
    provider = make_provider(provider_name, api_key, model)
    cancel = threading.Event()
    text = ""
    async for event in provider.stream_turn(
        system=system,
        messages=[ProviderMessage(role="user", content=user)],
        tools=[],
        cancel_event=cancel,
    ):
        if event.kind == StreamEventKind.TEXT_DELTA:
            text += event.text
        elif event.kind == StreamEventKind.ERROR:
            raise ValueError(event.error or "Generation failed")
    if not text.strip():
        raise ValueError("Model returned empty response")
    return text


def draft_skill_pack(description: str, model: str, provider: str = "") -> dict[str, Any]:
    """Generate a full skill pack draft from a natural-language description."""
    desc = (description or "").strip()
    if not desc:
        return {"ok": False, "error": "Description is required"}
    model = (model or "").strip()
    if not model:
        return {"ok": False, "error": "Model is required"}

    provider_name = (provider or "").strip().lower() or infer_provider(model)
    if not provider_name:
        return {"ok": False, "error": "Could not determine provider for model"}

    try:
        raw = asyncio.run(
            _complete_text(
                provider_name=provider_name,
                model=model,
                system=_SYSTEM,
                user=f"Create a skill pack that teaches the agent:\n\n{desc}",
            )
        )
        draft = _parse_draft(raw)
        return {"ok": True, "draft": draft}
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"Invalid JSON from model: {e}"}
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": str(e) or "Generation failed"}


_SUBSKILL_SYSTEM = """You write a single reference subskill file for an Agent Skills pack (UEFN-Ducky).

Return ONLY valid JSON (no markdown fences, no commentary) matching:
{
  "id": "snake_case_id",
  "label": "Human file label",
  "description": "One-line subskill description",
  "markdown": "# Title\\n\\nMarkdown body (no frontmatter)"
}

Rules:
- id: lowercase snake_case, a-z0-9_, max 48 chars, start with a letter
- markdown: actionable operator guidance — not an essay
- Do not duplicate SKILL.md; go deeper on the requested topic
"""


def _parse_subskill_draft(text: str, fallback_label: str) -> dict[str, Any]:
    raw = _strip_json_fences(text)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Draft must be a JSON object")
    norm = _normalize_file({**data, "label": data.get("label") or fallback_label})
    if not norm:
        raise ValueError("Could not normalize subskill draft")
    return norm


def draft_subskill(
    pack_id: str,
    label: str,
    description: str,
    model: str,
    provider: str = "",
) -> dict[str, Any]:
    """Generate one reference/*.md draft for an existing pack."""
    from backend.skills.store import get_skill_pack_files

    pid = (pack_id or "").strip()
    file_label = (label or "").strip()
    topic = (description or "").strip()
    if not pid:
        return {"ok": False, "error": "Pack id is required"}
    if not file_label:
        return {"ok": False, "error": "File label is required"}
    if not topic:
        return {"ok": False, "error": "Description is required"}
    model = (model or "").strip()
    if not model:
        return {"ok": False, "error": "Model is required"}

    provider_name = (provider or "").strip().lower() or infer_provider(model)
    if not provider_name:
        return {"ok": False, "error": "Could not determine provider for model"}

    try:
        info = get_skill_pack_files(pid)
    except FileNotFoundError:
        return {"ok": False, "error": f"Pack not found: {pid}"}

    core = next((f.get("text") or "" for f in info.get("files", []) if f.get("id") == "core"), "")
    core_excerpt = (core or "")[:4000]
    existing = [
        str(f.get("label") or f.get("id") or "")
        for f in info.get("files", [])
        if f.get("id") != "core"
    ]

    user = (
        f"Pack: {info.get('label') or pid}\n"
        f"Pack description: {info.get('description') or ''}\n\n"
        f"Existing reference files: {', '.join(existing) or '(none)'}\n\n"
        f"SKILL.md excerpt:\n{core_excerpt or '(empty)'}\n\n"
        f"Create a new reference subskill:\n"
        f"- Human label: {file_label}\n"
        f"- Topic / what it should cover:\n{topic}\n"
    )

    try:
        raw = asyncio.run(
            _complete_text(
                provider_name=provider_name,
                model=model,
                system=_SUBSKILL_SYSTEM,
                user=user,
            )
        )
        draft = _parse_subskill_draft(raw, file_label)
        return {"ok": True, "draft": draft}
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"Invalid JSON from model: {e}"}
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": str(e) or "Generation failed"}
