"""Persisted patch notes, keyed by app/item version (cache_docs)."""

from __future__ import annotations

from typing import Any


def _doc_key(key: str) -> str:
    return f"patch_notes:{key}"


def get(key: str) -> dict[str, Any] | None:
    try:
        from backend.store.repos import kv
        from backend.store.switch import use_db

        if not use_db("cache_docs"):
            return None
        doc = kv.get_doc("cache_docs", _doc_key(key))
    except Exception:
        return None
    if not isinstance(doc, dict):
        return None
    versions = doc.get("versions")
    if not isinstance(versions, list):
        return None
    return {"version": str(doc.get("version") or ""), "versions": versions}


def put(key: str, version: str, versions: list[dict[str, Any]]) -> None:
    try:
        from backend.store.repos import kv
        from backend.store.switch import use_db

        if not use_db("cache_docs"):
            return
        doc = {"version": str(version or ""), "versions": versions}
        # The version check runs every 10 minutes per window and nearly always
        # gets the same notes back; only changed notes are worth a write.
        if kv.get_doc("cache_docs", _doc_key(key)) == doc:
            return
        kv.set_doc("cache_docs", _doc_key(key), doc)
    except Exception:
        pass
