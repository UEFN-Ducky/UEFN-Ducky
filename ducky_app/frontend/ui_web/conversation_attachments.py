"""Persist and hydrate per-conversation message attachments on disk."""

from __future__ import annotations

import base64
import re
import shutil
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any

from backend.agent.message_attachment import MessageAttachment

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_CONV_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
_CHAT_FILE_RE = re.compile(r"^[A-Za-z0-9._-]+\.(?:png|jpe?g|webp|mp4|webm|mov|mkv)$", re.IGNORECASE)
NO_CHAT_CAPTURE_ERROR = "Screenshot was not saved: no active chat."


def _safe_attachment_filename(name: str) -> str:
    base = Path(name).name or "attachment"
    # Fold to ASCII so stored names always match the media-serving URL patterns.
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode("ascii")
    safe = _UNSAFE_CHARS.sub("_", base).strip("._")
    return (safe or "attachment")[:120]


def conversation_dir(conv_id: str, project_root: str | None, conversations_dir: Path) -> Path:
    return conversations_dir / conv_id


def conversation_attachments_dir(conv_id: str, project_root: str | None, conversations_dir: Path) -> Path:
    return conversation_dir(conv_id, project_root, conversations_dir) / "attachments"


def conversation_meta_path(conv_id: str, conversations_dir: Path) -> Path:
    return conversations_dir / conv_id / "conversation.json"


def persist_message_attachments(
    conv_id: str,
    message_ts: float,
    attachments: list[MessageAttachment],
    conversations_dir: Path,
    project_root: str | None = None,
) -> list[dict[str, Any]]:
    """Write attachment bytes/text under conversations/{conv_id}/attachments/."""
    del project_root  # reserved for future multi-root layouts
    if not attachments:
        return []
    conv_path = conversation_dir(conv_id, None, conversations_dir)
    att_dir = conversation_attachments_dir(conv_id, None, conversations_dir)
    from backend.workspace.ai_ignore import DENIED, current_policy

    policy = current_policy()
    policy.require(str(att_dir))
    att_dir.mkdir(parents=True, exist_ok=True)
    out: list[dict[str, Any]] = []
    ts_tag = int(message_ts * 1000)
    for i, att in enumerate(attachments):
        if policy.denied_hint(att.name):
            raise ValueError(DENIED)
        safe = _safe_attachment_filename(att.name)
        filename = f"{ts_tag}_{i}_{safe}"
        rel = f"attachments/{filename}"
        full = conv_path / rel
        policy.require(str(full))
        if att.kind == "image" and att.data_base64:
            try:
                full.write_bytes(base64.b64decode(att.data_base64, validate=True))
            except Exception:
                continue
            out.append({"kind": "image", "name": att.name, "mime": att.mime or "image/png", "path": rel})
        elif att.kind == "video" and att.file_path:
            from backend.agent.video.staging import VIDEO_MIME_EXT

            ext = VIDEO_MIME_EXT.get(att.mime, ".mp4")
            if Path(filename).suffix.lower() not in VIDEO_MIME_EXT.values():
                filename += ext
                rel = f"attachments/{filename}"
                full = conv_path / rel
            policy.require(att.file_path)
            policy.require(str(full))
            try:
                shutil.copyfile(att.file_path, full)
            except OSError as exc:
                raise ValueError(f"Could not save video {att.name!r}: {exc}") from exc
            video_row: dict[str, Any] = {
                "kind": "video",
                "name": att.name,
                "mime": att.mime,
                "path": rel,
                "size_bytes": att.size_bytes,
            }
            if att.transcript:
                video_row["transcript"] = att.transcript
            out.append(video_row)
        elif att.kind == "file":
            from backend.agent.attached_files import readable_copy_needed, readable_copy_path, readable_text

            text = att.text or ""
            full.write_text(text, encoding="utf-8")
            file_row: dict[str, Any] = {
                "kind": "file",
                "name": att.name,
                "mime": att.mime or "text/plain",
                "path": rel,
                "size_bytes": len(text.encode("utf-8")),
            }
            # Bundles and minified files: a copy without embedded data, lines wrapped,
            # that an agent can read in parts.
            if readable_copy_needed(text):
                readable = readable_copy_path(full)
                policy.require(str(readable))
                readable.write_text(readable_text(text), encoding="utf-8")
                file_row["readable_path"] = f"attachments/{readable.name}"
            out.append(file_row)
    return out


def hydrate_attachment_dict(
    raw: dict[str, Any],
    conv_id: str,
    conversations_dir: Path,
    project_root: str | None = None,
    *, display: bool = False,
) -> dict[str, Any]:
    del project_root
    if not isinstance(raw, dict):
        return raw
    kind = str(raw.get("kind") or "").strip().lower()
    if raw.get("generated"):
        # A picture an agent made: kept once in Ducky's generated images folder.
        from frontend.ui_web.generated_images import hydrate_generated

        return hydrate_generated(raw)
    path = str(raw.get("path") or "").strip()
    if not path:
        return raw
    full = conversation_dir(conv_id, None, conversations_dir) / path
    from backend.workspace.ai_ignore import current_policy

    policy = current_policy()
    if policy.denied(str(full)) or policy.denied_hint(str(raw.get("name") or full.name)):
        return {"kind": "ignored", "name": str(raw.get("name") or "")}
    if not full.is_file():
        return raw
    if display and kind == "image" and path == f"attachments/{full.name}" and _CHAT_FILE_RE.fullmatch(full.name):
        # The existing media route enforces account ownership, containment and
        # opening encrypted files. No bytes/base64 in the chat's JSON response.
        if not _signed_in_account_may_open(conv_id):
            return {"kind": "ignored", "name": str(raw.get("name") or "")}
        return {**{k: v for k, v in raw.items() if k != "data_base64"},
                "stored_conv_id": conv_id, "media_url": build_chat_attachment_url(conv_id, full.name)}
    if not _open_if_sealed(conversation_attachments_dir(conv_id, None, conversations_dir), full):
        # Still sealed (another thread is opening it, or the account's key isn't here yet):
        # its content in memory if this account can open it, never its ciphertext.
        plain = _sealed_content(full)
        if plain is None or kind == "video":
            return raw
        if kind == "image":
            return {**raw, "data_base64": base64.b64encode(plain).decode("ascii")}
        if kind == "file":
            return {**raw, "text": plain.decode("utf-8")}
        return raw
    if kind == "image":
        data = base64.b64encode(full.read_bytes()).decode("ascii")
        return {**raw, "data_base64": data}
    if kind == "file":
        return {**raw, "text": full.read_text(encoding="utf-8")}
    if kind == "video":
        conv_path = conversation_dir(conv_id, None, conversations_dir)
        frames = []
        for fr in raw.get("frames") or []:
            if not isinstance(fr, dict) or not fr.get("path"):
                continue
            fp = conv_path / str(fr["path"])
            if policy.denied(str(fp)):
                continue
            frames.append(
                {
                    **fr,
                    "abs_path": str(fp),
                    "media_url": build_chat_attachment_url(conv_id, fp.name),
                }
            )
        return {
            **raw,
            "abs_path": str(full),
            "media_url": build_chat_attachment_url(conv_id, full.name),
            "frames": frames,
        }
    return raw


def active_chat_id() -> str:
    """Conversation bound on this tool call, else DUCKY_CONV_ID on a dedicated bridge, or empty."""
    try:
        from backend.workspace.identity import resolve_context

        ctx = resolve_context()
    except Exception:
        return ""
    return str(getattr(ctx, "conv_id", "") or "").strip()


def chat_attachments_dir(conv_id: str, *, create: bool = False) -> Path | None:
    """`…/chats/projects/<project>/conversations/<chat>/attachments`, or None."""
    cid = (conv_id or "").strip()
    if not _CONV_ID_RE.fullmatch(cid):
        return None
    from frontend.ui_web.chats.store import get_conversations_dir

    path = get_conversations_dir(create=create) / cid / "attachments"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def _signed_in_account_may_open(conv_id: str) -> bool:
    """A chat of another Ducky account on this PC serves none of its files. A chat not
    saved yet (its first message is on its way) has no owner and is the current one's."""
    from backend.store.switch import use_db

    if not use_db("chats"):
        return True
    from backend.store import account_rows
    from backend.store.repos import chats

    me = account_rows.current()  # first: it claims rows from before accounts
    owner = chats.conv_account(conv_id)
    return owner is None or owner == me


def build_chat_attachment_url(conv_id: str, filename: str) -> str:
    from frontend.settings import PANEL_LISTENER_PORT

    name = Path(filename).name
    port = PANEL_LISTENER_PORT - 1
    return f"http://127.0.0.1:{port}/chat-attachments/{conv_id}/{name}"


def resolve_chat_attachment_path(conv_id: str, filename: str) -> Path:
    cid = Path(conv_id or "").name
    name = Path(filename or "").name
    if cid != (conv_id or "") or not _CONV_ID_RE.fullmatch(cid) or not _CHAT_FILE_RE.fullmatch(name):
        raise ValueError("Invalid attachment")
    if not _signed_in_account_may_open(cid):
        raise ValueError("Invalid attachment")
    root = chat_attachments_dir(cid)
    if root is None:
        raise ValueError("Invalid attachment")
    target = (root / name).resolve()
    root_resolved = root.resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("Path escapes chat attachments") from exc
    if not target.is_file():
        raise ValueError("Not a file")
    if not _open_if_sealed(root, target):
        raise ValueError("Attachment is locked")
    return target


def _open_if_sealed(folder: Path, path: Path) -> bool:
    """A chat's attachments are sealed while another account is signed in; its owner is
    back but the background pass hasn't reached this chat yet: open the chat's files now.
    False when ``path`` is still sealed afterwards."""
    from backend.store import sealed_files

    if not sealed_files.is_sealed_file(path):
        return True
    from backend.store import account_rows

    try:
        sealed_files.open_chat_folder(folder, account_rows.current())
    except Exception:  # noqa: BLE001 — locked for now: shown once the account's key is here
        pass
    return not sealed_files.is_sealed_file(path)


def _sealed_content(path: Path) -> bytes | None:
    """A sealed attachment's content for the signed-in account, or None if it can't open it."""
    from backend.store import account_rows, sealed_files

    try:
        return sealed_files.read_bytes(path, account_rows.current())
    except Exception:  # noqa: BLE001 — another account's, locked, or still being swapped
        return None


def save_chat_screenshot(raw: bytes, *, prefix: str = "capture", conv_id: str = "") -> dict[str, Any]:
    """Write one PNG under the active chat's attachments folder. Nowhere else."""
    if not raw:
        return {"ok": False, "error": "Screenshot was empty."}
    cid = (conv_id or active_chat_id()).strip()
    dest_dir = chat_attachments_dir(cid, create=True) if cid else None
    if dest_dir is None:
        return {"ok": False, "error": NO_CHAT_CAPTURE_ERROR}
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", (prefix or "capture").strip())[:40] or "capture"
    name = f"{safe}_{int(time.time())}_{uuid.uuid4().hex[:8]}.png"
    path = dest_dir / name
    path.write_bytes(raw)
    return {
        "ok": True,
        "path": str(path),
        "filename": name,
        "conv_id": cid,
        "media_url": build_chat_attachment_url(cid, name),
        "bytes": len(raw),
        "format": "png",
        "capture_path": str(path),
    }


def save_chat_file(conv_id: str, name: str, raw: bytes) -> dict[str, Any]:
    """A file the person hands an agent (an answer on its question card): saved in that
    chat's attachments folder, with a readable copy for bundles, so the agent reads it by
    path. The person never has to copy it anywhere."""
    from backend.agent.attached_files import readable_copy_needed, readable_copy_path, readable_text
    from backend.workspace.ai_ignore import DENIED, current_policy

    dest_dir = chat_attachments_dir((conv_id or "").strip(), create=True)
    if dest_dir is None:
        return {"ok": False, "error": "No chat to save the file in."}
    policy = current_policy()
    if policy.denied_hint(name or ""):
        return {"ok": False, "error": DENIED}
    path = dest_dir / f"{int(time.time() * 1000)}_{_safe_attachment_filename(name)}"
    policy.require(str(path))
    path.write_bytes(raw)
    out: dict[str, Any] = {"ok": True, "name": name, "path": str(path), "size_bytes": len(raw)}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return out
    if readable_copy_needed(text):
        readable = readable_copy_path(path)
        policy.require(str(readable))
        readable.write_text(readable_text(text), encoding="utf-8")
        out["readable_path"] = str(readable)
    return out


def hydrate_attachment_dicts(
    raw_list: list[Any] | None,
    conv_id: str,
    conversations_dir: Path,
    project_root: str | None = None,
    *, display: bool = False,
) -> list[dict[str, Any]]:
    if not raw_list:
        return []
    return [
        hydrate_attachment_dict(raw if isinstance(raw, dict) else {}, conv_id, conversations_dir, project_root, display=display)
        for raw in raw_list
    ]
