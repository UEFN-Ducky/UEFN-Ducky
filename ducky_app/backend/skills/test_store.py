"""``backend.skills.store`` is the Store-plugin facade: ``appdata_dir`` and nothing else."""

from __future__ import annotations

from backend.skills import discovery, store


def test_facade_serves_store_plugins_appdata_dir() -> None:
    assert store.appdata_dir is discovery.appdata_dir


def test_facade_carries_no_other_names() -> None:
    public = {n for n in vars(store) if not n.startswith("_") and n != "annotations"}
    assert public == {"appdata_dir"}
