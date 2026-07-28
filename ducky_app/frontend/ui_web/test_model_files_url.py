"""Regression: model-files URLs must keep percent-encoded slashes in the dir token."""

from __future__ import annotations

from urllib.parse import quote, unquote, urlparse

from frontend.ui_web.project_media import (
    build_model_media_urls,
    model_files_re,
    resolve_model_files_path,
)
from frontend.ui_web.project_files import EXT_PATH_PREFIX


def _ext(path) -> str:
    return f"{EXT_PATH_PREFIX}{path}"


def test_model_files_regex_requires_encoded_dir_segment():
    """If the handler unquotes before matching, nested ext: dirs collapse to ext:C:."""
    encoded_dir = "ext:C:/Users/alice/Documents/assets/Bonfire/fbx/source"
    dir_token = quote(encoded_dir, safe="")
    raw_rel = f"model-files/{dir_token}/bonfire_01.fbx"
    decoded_rel = unquote(raw_rel)

    raw_match = model_files_re().match(raw_rel)
    assert raw_match is not None
    assert unquote(raw_match.group(1)) == encoded_dir
    assert unquote(raw_match.group(2)) == "bonfire_01.fbx"

    broken = model_files_re().match(decoded_rel)
    assert broken is not None
    # Premature unquote: first segment stops at the first real slash after ext:C:
    assert broken.group(1) == "ext:C:"
    assert "bonfire_01.fbx" in broken.group(2)
    assert "/" in broken.group(2)


def test_build_and_resolve_ext_model_nested_path(tmp_path):
    nested = tmp_path / "Bonfire-79447f90" / "fbx" / "source"
    nested.mkdir(parents=True)
    fbx = nested / "bonfire_01.fbx"
    fbx.write_bytes(b"Kaydara FBX Binary  \x00")

    encoded = _ext(fbx)
    urls = build_model_media_urls(encoded)
    parsed = urlparse(urls["media_url"])
    raw_rel = parsed.path.lstrip("/")
    match = model_files_re().match(raw_rel)
    assert match is not None

    resolved = resolve_model_files_path(match.group(1), match.group(2))
    assert resolved.resolve() == fbx.resolve()
    assert resolved.read_bytes().startswith(b"Kaydara")


def test_resolve_rejects_prematurely_unquoted_tokens(tmp_path):
    nested = tmp_path / "Bonfire" / "fbx" / "source"
    nested.mkdir(parents=True)
    fbx = nested / "bonfire_01.fbx"
    fbx.write_bytes(b"fbx")

    # Simulate the old httpd bug: dir token only "ext:C:" and filename contains slashes.
    import pytest

    with pytest.raises(ValueError, match="Invalid model file path"):
        resolve_model_files_path("ext:C:", f"Users/x/{fbx.name}")
