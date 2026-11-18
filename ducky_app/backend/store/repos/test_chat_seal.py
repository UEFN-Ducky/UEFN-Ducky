"""Sealing lives in ``chat_seal``; ``chats`` keeps the import plugins use.

The sealing behaviour itself is covered in ``backend/store/test_message_parts.py``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from backend.store.repos import chat_seal, chats


def test_chats_re_exports_seal_stored_messages() -> None:
    assert chats.seal_stored_messages is chat_seal.seal_stored_messages


def test_either_module_can_be_imported_first() -> None:
    root = Path(__file__).resolve().parents[3]
    for first in ("chat_seal", "chats"):
        code = (
            f"import backend.store.repos.{first}\n"
            "from backend.store.repos.chats import seal_stored_messages\n"
        )
        done = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=60)
        assert done.returncode == 0, done.stderr
