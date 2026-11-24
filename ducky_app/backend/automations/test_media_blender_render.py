"""The Blender render node renders without denoise on a Blender built without OpenImageDenoise."""

from __future__ import annotations

from pathlib import Path


def test_render_code_turns_denoise_off_without_oidn_and_reports_it(tmp_path, monkeypatch) -> None:
    """Nov 24 2026: Ubuntu's apt Blender has no OIDN; a denoised Cycles render failed and saved nothing."""
    from backend.automations import media

    mesh = tmp_path / "box.glb"
    mesh.write_bytes(b"glTF")
    sent: list[str] = []

    def fake_blender(code: str) -> dict:
        sent.append(code)
        compile(code, "render", "exec")  # the formatted script is valid Python
        Path(tmp_path / "out" / "render.png").write_bytes(b"png")
        return {"image": "x", "note": "This Blender has no OpenImageDenoise, so the render is not denoised."}

    monkeypatch.setattr(media, "_blender", fake_blender)
    step = media.blender_render({}, {"mesh": str(mesh)}, tmp_path / "out")
    assert step["ok"] is True and "OpenImageDenoise" in step["note"]
    assert "_cycles" in sent[0] and "use_denoising = False" in sent[0]
