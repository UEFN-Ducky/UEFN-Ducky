"""A plugin-tree walk that started before an invalidate never pins the old tree.

linux-tests: a background thread stat'd the plugin tree, an install wrote its folder
and invalidated, then the thread stored its old fingerprint; for the next second
plugin_owner_for_skill() saw no owner for the freshly installed skill.
"""

from __future__ import annotations

from pathlib import Path

from backend.uefn_plugins import store


class _InstallDuringWalk:
    """A root whose directory walk finishes an 'install' (invalidate) mid-walk."""

    def __init__(self, real: Path) -> None:
        self._real = real

    def __str__(self) -> str:
        return str(self._real)

    def is_dir(self) -> bool:
        return True

    def iterdir(self):
        children = list(self._real.iterdir())
        store.invalidate_plugin_skill_caches()
        return iter(children)


def test_fingerprint_walked_before_invalidate_is_not_stored(tmp_path: Path) -> None:
    tmp_path = tmp_path / "plugins"
    (tmp_path / "old").mkdir(parents=True)
    store.invalidate_plugin_skill_caches()
    store._plugin_tree_fingerprint(_InstallDuringWalk(tmp_path))  # type: ignore[arg-type]
    assert store._FINGERPRINT is None

    (tmp_path / "new").mkdir()
    names = [name for name, _ in store._plugin_tree_fingerprint(tmp_path)]
    assert names == ["new", "old"]


def test_fingerprint_walked_without_invalidate_is_memoized(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    store.invalidate_plugin_skill_caches()
    first = store._plugin_tree_fingerprint(tmp_path)
    assert store._FINGERPRINT == first
