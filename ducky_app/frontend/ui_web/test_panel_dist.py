"""panel_dist: the built page has every asset index.html names (moved out of panel_httpd)."""

from __future__ import annotations

import pytest

from frontend.ui_web import panel_dist, panel_httpd


def test_panel_httpd_still_offers_the_same_check() -> None:
    assert panel_httpd.verify_panel_dist is panel_dist.verify_panel_dist


def test_a_whole_build_passes_and_a_missing_asset_names_itself(tmp_path) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index.js").write_text("", encoding="utf-8")
    (tmp_path / "index.html").write_text('<script src="./assets/index.js"></script>', encoding="utf-8")
    panel_dist.verify_panel_dist(tmp_path)
    (tmp_path / "index.html").write_text('<link href="./assets/gone.css">', encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="gone.css"):
        panel_dist.verify_panel_dist(tmp_path)


def test_no_index_is_missing(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="Panel dist missing"):
        panel_dist.verify_panel_dist(tmp_path)
